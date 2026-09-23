"""Servicios de negocio (lógica compartida; sin FastAPI)."""

import datetime
import json
import os
import secrets
import urllib.parse
import urllib.request

import config
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
    conn = _db()
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
    conn.close()
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
    conn = _db()
    row = conn.execute("SELECT conductor_id FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
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
