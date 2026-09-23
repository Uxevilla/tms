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


def _categoria_cuenta(conn, categoria):
    """Cuenta contable (grupo 6) para una categoría de gasto: BD -> mapeo por defecto -> 629."""
    if categoria:
        row = conn.execute("SELECT cuenta FROM categorias_gasto WHERE nombre=?", (categoria,)).fetchone()
        if row and row["cuenta"]:
            return row["cuenta"]
    return _CATEGORIA_CUENTA.get(categoria, "629")


def _next_referencia(conn):
    """Siguiente numeración interna de viaje (V-0001, V-0002, …)."""
    rows = conn.execute(
        "SELECT referencia FROM trips WHERE referencia IS NOT NULL AND referencia != ''"
    ).fetchall()
    max_n = 0
    for r in rows:
        m = re.match(r"^V-(\d+)$", (r["referencia"] or "").strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"V-{max_n + 1:04d}"


def _auditar(conn, tabla, registro_id, accion, usuario=None, antes=None, despues=None):
    """Registra una acción en el audit log (quién, qué, sobre qué registro, cuándo)."""
    if usuario is None:
        usuario = _usuario_ctx.get() or "sistema"
    conn.execute(
        "INSERT INTO audit_log (tabla, registro_id, accion, usuario, antes, despues, ts) "
        "VALUES (?,?,?,?,?,?,?)",
        (tabla, str(registro_id) if registro_id is not None else None, accion, usuario,
         json.dumps(antes, ensure_ascii=False, default=str) if antes is not None else None,
         json.dumps(despues, ensure_ascii=False, default=str) if despues is not None else None,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )


def _post_asiento(fecha, concepto, lineas, origen="manual", trip_id=None, gasto_id=None, documento=None, origen_id=None, conn=None, usuario=None):
    """Crea un asiento de doble partida. lineas = [(cuenta, debe, haber, concepto), ...].
    Lanza ValueError si no cuadra. Devuelve el id del asiento."""
    own = conn is None
    if own:
        conn = _db()
    try:
        _c = conn.execute("SELECT value FROM config WHERE key='cierre_fecha'").fetchone()
        cierre = (_c["value"] if _c and _c["value"] else "")
        if cierre and fecha and (fecha or "")[:10] <= cierre:
            raise ValueError(f"Periodo cerrado (cierre {cierre}).")
        debe_total = round(sum(l[1] or 0 for l in lineas), 2)
        haber_total = round(sum(l[2] or 0 for l in lineas), 2)
        if abs(debe_total - haber_total) > 0.005:
            raise ValueError(f"Asiento descuadrado: debe {debe_total:.2f} ≠ haber {haber_total:.2f}")
        year = (fecha or "")[:4]
        row = conn.execute(
            "SELECT COALESCE(MAX(numero), 0) AS m FROM asientos WHERE substr(fecha, 1, 4)=?",
            (year,),
        ).fetchone()
        numero = (row["m"] or 0) + 1
        cur = conn.execute(
            "INSERT INTO asientos (numero, fecha, concepto, documento, origen, origen_id, trip_id, gasto_id, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
            (numero, fecha, concepto, documento, origen, origen_id, trip_id, gasto_id,
             datetime.datetime.utcnow().isoformat() + "Z"),
        )
        asiento_id = cur.fetchone()["id"]
        for cuenta, debe, haber, cline in lineas:
            conn.execute(
                "INSERT INTO apuntes (asiento_id, cuenta, debe, haber, concepto) VALUES (?,?,?,?,?)",
                (asiento_id, cuenta, round(debe or 0, 2), round(haber or 0, 2), cline or ""),
            )
        _auditar(conn, "asientos", asiento_id, "crear", usuario,
                 despues={"numero": numero, "fecha": fecha, "concepto": concepto, "origen": origen, "lineas": lineas})
        if own:
            conn.commit()
        return asiento_id
    finally:
        if own:
            conn.close()


def _registrar_asiento(fecha, concepto, lineas_apuntes, origen, origen_id=None, conn=None, usuario=None):
    """Registra un asiento de partida doble de forma transaccional (PGC).
    lineas_apuntes = [(cuenta, debe, haber, concepto), ...].
    Valida estrictamente que SUM(debe) == SUM(haber); si descuadra hace rollback() y lanza ValueError."""
    own = conn is None
    if own:
        conn = _db()
    debe_total = round(sum(l[1] or 0 for l in lineas_apuntes), 2)
    haber_total = round(sum(l[2] or 0 for l in lineas_apuntes), 2)
    if abs(debe_total - haber_total) > 0.005:
        if own:
            conn.rollback()
            conn.close()
        raise ValueError(f"Asiento descuadrado: debe {debe_total:.2f} ≠ haber {haber_total:.2f}")
    try:
        aid = _post_asiento(fecha, concepto, lineas_apuntes, origen=origen, origen_id=origen_id, conn=conn, usuario=usuario)
        if own:
            conn.commit()
            conn.close()
        return aid
    except Exception:
        if own:
            conn.rollback()
            conn.close()
        raise


def _norm_fecha(s):
    if not s:
        return None
    s = s.replace(".", "/").replace("-", "/")
    parts = s.split("/")
    if len(parts) == 3:
        try:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            return f"{y:04d}-{m:02d}-{d:02d}"
        except ValueError:
            return None
    return None


def _norm_total(s):
    if not s:
        return None
    s = s.strip().replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _gasto_subcontrata(conn, trip, fecha):
    """Genera el gasto de subcontratación (624/410) para un viaje vendido a un tercero.

    Se llama dentro de una transacción ya abierta (`conn`). Devuelve el gasto_id o
    None si no procede (sin coste o ya creado).
    """
    coste = float(trip["coste"] or 0)
    if coste <= 0:
        return None
    # Evitar duplicados si ya se generó la subcontrata de este viaje.
    if conn.execute(
        "SELECT 1 FROM gastos WHERE trip_id=? AND categoria='Transportes' AND concepto LIKE 'Subcontrata%%'",
        (trip["id"],),
    ).fetchone():
        return None
    iva_pct = float(trip["iva"] or 21)
    base = round(coste / (1 + iva_pct / 100.0), 2) if iva_pct > 0 else coste
    cuota = round(coste - base, 2)
    concepto = f"Subcontrata viaje {trip['id']}"
    cur = conn.execute(
        "INSERT INTO gastos (terminal, trip_id, categoria, fecha, importe, concepto, foto, creado, proveedor_id, iva, retencion, cuenta) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (trip["terminal"] or "", trip["id"], "Transportes", fecha, coste, concepto, "",
         datetime.datetime.utcnow().isoformat() + "Z", trip["proveedor_id"], iva_pct, 0, "624"),
    )
    gasto_id = cur.fetchone()["id"]
    lineas = [("624", base, 0, concepto)]
    if cuota > 0:
        lineas.append(("472", cuota, 0, "IVA soportado"))
    lineas.append(("410", 0, coste, concepto))
    _post_asiento(fecha[:10], concepto, lineas, origen="gasto", gasto_id=gasto_id, conn=conn)
    return gasto_id


def _facturar_viaje(trip_id):
    """Dispara la facturación automática de un viaje entregado (idempotente)."""
    with _db() as conn:
        trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not trip:
            conn.close()
            return None
        if _map_estado(trip["estado"]) != "Entregado":
            conn.close()
            return None
        if conn.execute("SELECT 1 FROM facturas WHERE trip_id=?", (trip_id,)).fetchone():
            conn.close()
            return None  # ya facturado (borrador o emitida)
    return _crear_factura_borrador(dict(trip))


def _costes_reales_viaje(trip, conn=None):
    if conn is None:
        with _db() as conn:
            return _costes_reales_viaje(trip, conn)
    """Costes reales del viaje: peajes estimados + gastos vinculados exactamente al viaje."""
    peaje = float(trip["peaje_estimado"] or 0)
    row = conn.execute(
        "SELECT COALESCE(SUM(importe), 0) AS total FROM gastos WHERE trip_id=?",
        (trip["id"],),
    ).fetchone()
    gastos = float(row["total"] or 0) if row else 0.0
    coste = round(peaje + gastos, 2)
    margen = round(float(trip["precio"] or 0) - coste, 2)
    return coste, margen


def _crear_factura_borrador(trip, conn=None):
    if conn is None:
        with _db() as conn:
            return _crear_factura_borrador(trip, conn)
    """Crea una factura en estado Borrador (sin asiento) para un viaje entregado."""
    # Subcontratación: registrar el gasto (624/410) antes de calcular costes/margen.
    if trip["subcontratado"]:
        gconn = _db()
        _gasto_subcontrata(gconn, trip, (trip["creado"] or "")[:10] or datetime.date.today().isoformat())
        gconn.commit()
        gconn.close()
    coste, margen = _costes_reales_viaje(trip)
    base = round(float(trip["precio"] or 0), 2)
    iva = round(float(trip["iva"] or 21), 2)
    cuota = round(base * iva / 100.0, 2)
    total = round(base + cuota, 2)
    cur = conn.execute(
        "INSERT INTO facturas (numero, fecha, trip_id, cliente_id, cliente_nombre, base, iva, cuota_iva, total, estado, coste, margen, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        ("", (trip["creado"] or "")[:10] or datetime.date.today().isoformat(),
         trip["id"], trip["cliente_id"], trip["cliente"] or "",
         base, iva, cuota, total, "Borrador", coste, margen,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    factura_id = cur.fetchone()["id"]
    concepto = f"{trip['origen'] or ''} → {trip['destino'] or ''}".strip().strip("→").strip() or trip["id"]
    conn.execute(
        "INSERT INTO factura_lineas (factura_id, trip_id, concepto, base, iva, cuota_iva, total) VALUES (?,?,?,?,?,?,?)",
        (factura_id, trip["id"], concepto, base, iva, cuota, total),
    )
    _liquidar_conductor(trip, conn)
    conn.commit()
    return factura_id


def _generar_factura_pdf(factura_id, conn=None):
    if conn is None:
        with _db() as conn:
            return _generar_factura_pdf(factura_id, conn)
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    lineas = conn.execute("SELECT * FROM factura_lineas WHERE factura_id=? ORDER BY id", (factura_id,)).fetchall()
    cliente = conn.execute("SELECT * FROM clientes WHERE id=?", (f["cliente_id"],)).fetchone() if f["cliente_id"] else None
    emp = _empresa()

    def eur(n):
        v = f"{float(n or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{v} €".replace("€", "€")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=14)
    bold = ParagraphStyle("bold", parent=styles["Normal"], fontSize=10, leading=14, fontName="Helvetica-Bold")
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=22, spaceAfter=0)

    story = []
    emp_nombre = emp.get("nombre") or "Mi empresa"
    emp_txt = [emp_nombre]
    if emp.get("cif"): emp_txt.append(f"CIF: {emp['cif']}")
    if emp.get("direccion"): emp_txt.append(emp["direccion"])
    if emp.get("cp") or emp.get("poblacion"): emp_txt.append(f"{emp.get('cp','')} {emp.get('poblacion','')}".strip())
    if emp.get("telefono"): emp_txt.append(f"Tel: {emp['telefono']}")
    if emp.get("email"): emp_txt.append(emp["email"])
    emp_block = [Paragraph(x, normal) for x in emp_txt]
    fact_block = [Paragraph(f"<b>FACTURA</b> {f['numero']}", title),
                  Paragraph(f"Fecha: {f['fecha']}", normal)]

    header = Table([[emp_block, fact_block]], colWidths=[doc.width*0.55, doc.width*0.45])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0)]))
    story.append(header)
    story.append(Spacer(1, 10*mm))

    cli_nombre = (cliente["nombre"] if cliente else "") or f["cliente_nombre"] or "Cliente"
    story.append(Paragraph(f"<b>Facturar a:</b> {cli_nombre}", normal))
    if cliente:
        if cliente.get("cif"): story.append(Paragraph(f"CIF: {cliente['cif']}", normal))
        if cliente.get("direccion"): story.append(Paragraph(cliente["direccion"], normal))
        if cliente.get("cp") or cliente.get("poblacion"):
            story.append(Paragraph(f"{cliente.get('cp','')} {cliente.get('poblacion','')}".strip(), normal))
    story.append(Spacer(1, 10*mm))

    data = [["Concepto", "Base", "IVA %", "Total"]]
    for l in lineas:
        data.append([l["concepto"] or "—", eur(l["base"]), f"{float(l['iva'] or 0):.0f}%", eur(l["total"])])
    data.append(["", "Base imponible", "", eur(f["base"])])
    data.append(["", f"IVA ({float(f['iva'] or 0):.0f}%)", "", eur(f["cuota_iva"])])
    data.append(["", "TOTAL", "", eur(f["total"])])
    t = Table(data, colWidths=[doc.width*0.46, doc.width*0.18, doc.width*0.12, doc.width*0.24])
    t.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("GRID", (0,0), (-1,-4), 0.5, colors.grey),
        ("LINEABOVE", (1,-1), (-1,-1), 1, colors.black),
        ("ALIGN", (1,1), (-1,-1), "RIGHT"),
        ("FONTNAME", (1,-1), (-1,-1), "Helvetica-Bold"),
    ]))
    story.append(t)
    story.append(Spacer(1, 12*mm))
    if emp.get("iban"):
        story.append(Paragraph(f"<b>IBAN:</b> {emp['iban']}", normal))
    if emp.get("web"):
        story.append(Paragraph(emp["web"], normal))

    doc.build(story)
    return buf.getvalue()


def _liquidar_conductor(trip, conn):
    """Liquidación variable del conductor (si tiene tarifa por km). Devuelve importe o None."""
    if not trip["conductor_id"]:
        return None
    c = conn.execute("SELECT tarifa_km FROM conductores WHERE id=?", (trip["conductor_id"],)).fetchone()
    if not c or float(c["tarifa_km"] or 0) <= 0:
        return None  # sin modelo variable: no aplica
    importe = round(float(trip["km_total"] or 0) * float(c["tarifa_km"]), 2)
    if importe <= 0:
        return None
    conn.execute(
        "INSERT INTO liquidaciones (conductor_id, viaje_id, fecha, importe, concepto, pagado, creado) "
        "VALUES (?,?,?,?,?,?,?)",
        (trip["conductor_id"], trip["id"], datetime.date.today().isoformat(), importe,
         f"Liquidación viaje {trip['id']} - {trip['conductor'] or ''}", False,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    return importe
