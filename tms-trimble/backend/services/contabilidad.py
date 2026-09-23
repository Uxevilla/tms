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
    _c = conn.execute("SELECT value FROM config WHERE key='cierre_fecha'").fetchone()
    cierre = (_c["value"] if _c and _c["value"] else "")
    if cierre and fecha and (fecha or "")[:10] <= cierre:
        if own:
            conn.close()
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
        conn.close()
    return asiento_id


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
    conn = _db()
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
    conn.close()
    return _crear_factura_borrador(dict(trip))
