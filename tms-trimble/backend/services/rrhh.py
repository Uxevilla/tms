"""Servicios de negocio (lógica compartida; sin FastAPI)."""

import datetime
import json
import os
import secrets
import urllib.parse
import urllib.request

import config
from fastapi import HTTPException
from db import *
from core import *
from security import *
from tenancy import *
from clients.trimble import get_client
from clients.transfollow import get_transfollow_client
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from clients.geocoding import _buscar_photon, reverse_geocode

import base64
import io
import re
import subprocess
import uuid

import psycopg2
import redis

from models import *
from services.empresa import _empresa
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD

from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES


def _imputar_dieta_nomina(conductor_id, fecha, tipo):
    """Inyecta el importe de una dieta/pernocta en la nómina abierta (borrador) del conductor.

    Devuelve {"ok": True, "nomina_id", "linea_id", "importe"} o {"ok": False, "error": ...}.
    """
    importe = _DIETA_IMPORTE.get((tipo or "").lower(), 0.0)
    if importe <= 0:
        return {"ok": False, "error": f"Tipo de dieta desconocido: {tipo}"}
    periodo = (fecha or "")[:7] if fecha else datetime.date.today().strftime("%Y-%m")
    with _db() as conn:
        emp = conn.execute(
            "SELECT e.id AS empleado_id FROM conductores c "
            "JOIN empleados e ON e.id = c.empleado_id "
            "WHERE c.id = ? AND c.empleado_id IS NOT NULL AND c.empleado_id != ''",
            (conductor_id,),
        ).fetchone()
        if not emp:
            conn.close()
            return {"ok": False, "error": f"Conductor {conductor_id} sin empleado asociado en RRHH"}
        nom = conn.execute(
            "SELECT id FROM nominas WHERE empleado_id=? AND periodo=? AND estado='borrador' LIMIT 1",
            (emp["empleado_id"], periodo),
        ).fetchone()
        if not nom:
            conn.close()
            return {"ok": False, "error": f"Sin nómina abierta para el periodo {periodo}"}
        cur = conn.execute(
            "INSERT INTO lineas_nomina (nomina_id, concepto, tipo, importe, creado) "
            "VALUES (?,?,?,?,?) RETURNING id",
            (nom["id"], f"Dieta {tipo}", "devengo", importe, datetime.datetime.utcnow().isoformat() + "Z"),
        )
        linea_id = cur.fetchone()["id"]
        # Sumar al bruto + recalcular SS/IRPF/neto/coste con el nuevo bruto.
        conn.execute("UPDATE nominas SET salario_bruto = salario_bruto + ? WHERE id=?", (importe, nom["id"]))
        n = conn.execute("SELECT * FROM nominas WHERE id=?", (nom["id"],)).fetchone()
        bruto = float(n["salario_bruto"] or 0)
        ss_t, ss_e, irpf_imp, neto, coste = _calc_nomina(bruto, float(n["irpf_pct"] or 15), 6.35, 30.0)
        conn.execute(
            "UPDATE nominas SET ss_trabajador=?, ss_empresa=?, irpf_importe=?, neto=?, coste_empresa=? WHERE id=?",
            (ss_t, ss_e, irpf_imp, neto, coste, nom["id"]),
        )
        conn.commit()
    return {"ok": True, "nomina_id": nom["id"], "linea_id": linea_id, "importe": importe}


def _procesar_dieta(trip_id, messagetype, mtime):
    """Detecta una dieta/pernocta en un mensaje estructurado y la imputa a nómina."""
    mt = (messagetype or "").lower()
    tipo = None
    if "pernocta" in mt:
        tipo = "pernocta"
    elif "dieta" in mt:
        tipo = "dieta_comida" if "comida" in mt else ("dieta_cena" if "cena" in mt else "dieta")
    if not tipo:
        return None
    with _db() as conn:
        row = conn.execute("SELECT conductor_id FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row or not row["conductor_id"]:
        return None
    fecha = (mtime or "")[:10] if mtime else datetime.date.today().isoformat()
    return _imputar_dieta_nomina(row["conductor_id"], fecha, tipo)


def _sync_conductor(conn, emp):
    """Crea/actualiza el conductor local a partir de un empleado (categoría Conductor)."""
    nombre = f"{emp['nombre']} {emp['apellidos'] or ''}".strip()
    dni = emp["dni"] or ""
    activo = not (emp["fecha_baja"] or "")
    exist = conn.execute("SELECT id FROM conductores WHERE empleado_id=?", (emp["id"],)).fetchone()
    if not exist and dni:
        exist = conn.execute(
            "SELECT id FROM conductores WHERE dni=? AND (empleado_id IS NULL OR empleado_id='')", (dni,)
        ).fetchone()
    if exist:
        conn.execute(
            "UPDATE conductores SET nombre=?, dni=?, telefono=?, email=?, empleado_id=?, activo=? WHERE id=?",
            (nombre, dni, emp["telefono"], emp["email"], emp["id"], activo, exist["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO conductores (nombre, dni, telefono, email, empleado_id, activo) VALUES (?,?,?,?,?,?)",
            (nombre, dni, emp["telefono"], emp["email"], emp["id"], activo),
        )


def _calc_nomina(bruto, irpf_pct, ss_t_pct, ss_e_pct):
    ss_trabajador = round(bruto * ss_t_pct / 100.0, 2)
    ss_empresa = round(bruto * ss_e_pct / 100.0, 2)
    irpf = round(bruto * irpf_pct / 100.0, 2)
    neto = round(bruto - ss_trabajador - irpf, 2)
    coste = round(bruto + ss_empresa, 2)
    return ss_trabajador, ss_empresa, irpf, neto, coste


def _generar_nomina_pdf(nomina_id, conn=None):
    if conn is None:
        with _db() as conn:
            return _generar_nomina_pdf(nomina_id, conn)
        import io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

        n = conn.execute(
            "SELECT n.*, e.nombre, e.apellidos, e.dni, e.nss, e.categoria, e.puesto, e.iban "
            "FROM nominas n JOIN empleados e ON e.id=n.empleado_id WHERE n.id=?",
            (nomina_id,),
        ).fetchone()
        if not n:
            raise HTTPException(status_code=404, detail={"error": "Nómina no encontrada."})
        emp = _empresa()
        # Líneas de devengo extra (dietas/pernocta) desde lineas_nomina.
        dietas = conn.execute(
            "SELECT concepto, importe FROM lineas_nomina WHERE nomina_id=? AND tipo='devengo' ORDER BY id",
            (nomina_id,),
        ).fetchall()

        def eur(v):
            n = f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            return f"{n} €"

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=14)
        title = ParagraphStyle("title", parent=styles["Title"], fontSize=22, spaceAfter=0)

        story = []
        emp_nombre = emp.get("nombre") or "Mi empresa"
        emp_txt = [emp_nombre]
        if emp.get("cif"): emp_txt.append(f"CIF: {emp['cif']}")
        if emp.get("direccion"): emp_txt.append(emp["direccion"])
        if emp.get("cp") or emp.get("poblacion"): emp_txt.append(f"{emp.get('cp','')} {emp.get('poblacion','')}".strip())
        if emp.get("telefono"): emp_txt.append(f"Tel: {emp['telefono']}")
        emp_block = [Paragraph(x, normal) for x in emp_txt]
        nom_block = [Paragraph("NÓMINA", title), Paragraph(f"Periodo: {n['periodo']}", normal)]
        header = Table([[emp_block, nom_block]], colWidths=[doc.width*0.55, doc.width*0.45])
        header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                    ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0)]))
        story.append(header)
        story.append(Spacer(1, 10*mm))

        nombre_completo = f"{n['nombre']} {n['apellidos'] or ''}".strip()
        story.append(Paragraph(f"<b>Empleado:</b> {nombre_completo}", normal))
        if n.get("dni"): story.append(Paragraph(f"DNI: {n['dni']}", normal))
        if n.get("nss"): story.append(Paragraph(f"Afiliación SS: {n['nss']}", normal))
        if n.get("categoria") or n.get("puesto"): story.append(Paragraph(f"Categoría: {n['categoria']} — {n.get('puesto') or ''}", normal))
        story.append(Spacer(1, 8*mm))

        bruto = float(n["salario_bruto"] or 0)
        irpf_imp = float(n["irpf_importe"] or 0)
        ss_t = float(n["ss_trabajador"] or 0)
        ss_e = float(n["ss_empresa"] or 0)
        neto = float(n["neto"] or 0)
        coste = float(n["coste_empresa"] or 0)

        dietas_total = sum(float(d["importe"] or 0) for d in dietas)
        base = max(0.0, bruto - dietas_total)

        data = [["Concepto", "Devengos", "Deducciones"]]
        if dietas:
            data.append(["Salario base", eur(base), ""])
            for d in dietas:
                data.append([d["concepto"], eur(float(d["importe"] or 0)), ""])
        data.append(["Salario bruto", eur(bruto), ""])
        data.append([f"IRPF ({float(n['irpf_pct'] or 0):.1f}%)", "", eur(irpf_imp)])
        data.append([f"Seg. Social trabajador ({float(n['ss_trabajador_pct'] or 0):.2f}%)", "", eur(ss_t)])
        data.append(["LÍQUIDO A PERCIBIR", "", eur(neto)])
        t = Table(data, colWidths=[doc.width*0.46, doc.width*0.27, doc.width*0.27])
        t.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("ALIGN", (1,1), (-1,-1), "RIGHT"),
            ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f1f5f9")),
            ("LINEABOVE", (0,-1), (-1,-1), 1, colors.black),
        ]))
        story.append(t)
        story.append(Spacer(1, 8*mm))

        coste_data = [
            ["Coste para la empresa", ""],
            ["Seguridad Social a cargo de la empresa", eur(ss_e)],
            ["COSTE TOTAL EMPRESA", eur(coste)],
        ]
        ct = Table(coste_data, colWidths=[doc.width*0.6, doc.width*0.4])
        ct.setStyle(TableStyle([
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("ALIGN", (1,0), (-1,-1), "RIGHT"),
            ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
            ("LINEABOVE", (0,-1), (-1,-1), 1, colors.black),
        ]))
        story.append(ct)
        story.append(Spacer(1, 12*mm))

        if n.get("iban"):
            story.append(Paragraph(f"<b>IBAN:</b> {n['iban']}", normal))
        estado = f"{n.get('estado') or 'borrador'}" + (" · PAGADA" if n.get("pagado") else "")
        story.append(Paragraph(f"Estado: {estado}", normal))

        doc.build(story)
        return buf.getvalue()
