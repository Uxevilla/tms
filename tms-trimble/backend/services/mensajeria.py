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
import hmac
import io
import re
import subprocess
import uuid

import psycopg2
import redis

from models import *
from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD


def _save_mensaje(mid, trip_id, tipo, messagetype, originid, source, subject, body, mtime, needreply):
    with _db() as conn:
        conn.execute(
            "INSERT INTO mensajes (id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
            (mid, trip_id, tipo, messagetype, originid, source, subject, body, mtime, needreply,
             datetime.datetime.utcnow().isoformat()),
        )
        conn.commit()


def _store_mensaje(block, tipo):
    def f(tag):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
        return m.group(1).strip() if m else ""
    mid = f("id")
    if not mid:
        return
    originid = f("originid")
    trip_id = None
    if originid:
        # originid puede ser un LID (mapeado en la tabla lid_map) o el id de un mensaje enviado.
        conn = _db()
        row = conn.execute("SELECT trip_id FROM lid_map WHERE lid=?", (originid,)).fetchone()
        conn.close()
        trip_id = row["trip_id"] if row else None
    # Referencia directa del viaje si el macro la incluye (TRID / reference).
    if not trip_id:
        trip_id = f("reference") or f("trip") or f("trip_id") or f("trid")
    if not trip_id and originid:
        # Respuesta a un mensaje enviado por el TMS: originid = id SOAP del mensaje enviado
        conn = _db()
        row = conn.execute(
            "SELECT trip_id FROM mensajes WHERE id=? AND tipo='enviado'", (originid,)
        ).fetchone()
        conn.close()
        if row:
            trip_id = row["trip_id"]
    messagetype = f("messagetype")
    mtime = f("time")
    _save_mensaje(mid, trip_id, tipo, messagetype, originid, f("source"),
                  f("subject"), f("body"), mtime, f("needreply") == "true")
    # Estado gobernado por Trimble: los macros estructurados cambian el estado del viaje.
    if tipo == "estructurado" and trip_id and messagetype:
        # Automatización Inteligente: dietas (RRHH) + cuenta corriente de palés.
        _procesar_dieta(trip_id, messagetype, mtime)
        if re.search(r"descarga|descarreg|unload", messagetype or "", re.I):
            _procesar_pales(trip_id, f("body"), mtime)
        estado = _estado_desde_codigo(messagetype)
        if estado:
            _aplicar_estado_viaje(trip_id, estado, mtime)
            if estado == "Entregado":
                doc = _extraer_documento_ecmr(f("body"))
                if doc:
                    _guardar_documento_entrega(trip_id, doc[0], doc[1], doc[2], mtime)


def _extraer_pales(body):
    """Extrae palés entregados/recuperados de un question path de descarga.

    Patrones genéricos; AJUSTAR al CDATA real del question path de palés.
    """
    entregados = recuperados = 0
    for pat, key in (
        (r"palets?\s*(?:entregados?|cargados?|dejados?)\s*[=:>\s]+(\d+)", "entregados"),
        (r"palets?\s*(?:recuperados?|devueltos?|recogidos?)\s*[=:>\s]+(\d+)", "recuperados"),
        (r"(?:entregados?|cargados?)\s*[=:>\s]+(\d+)\s*palets?", "entregados"),
        (r"(?:recuperados?|devueltos?)\s*[=:>\s]+(\d+)\s*palets?", "recuperados"),
        (r"(\d+)\s*palets?\s*(?:entregados?|cargados?|dejados?)", "entregados"),
        (r"(\d+)\s*palets?\s*(?:recuperados?|devueltos?|recogidos?)", "recuperados"),
    ):
        m = re.search(pat, body or "", re.I)
        if m:
            v = int(m.group(1))
            if key == "entregados":
                entregados = max(entregados, v)
            else:
                recuperados = max(recuperados, v)
    return entregados, recuperados


def _procesar_pales(trip_id, body, mtime):
    """Actualiza la cuenta corriente de palés del cliente al finalizar la descarga."""
    entregados, recuperados = _extraer_pales(body)
    if not (entregados or recuperados):
        return None
    with _db() as conn:
        row = conn.execute("SELECT cliente_id FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not row or not row["cliente_id"]:
            conn.close()
            return None
        cliente_id = row["cliente_id"]
        prev = conn.execute(
            "SELECT balance FROM saldos_pales WHERE cliente_id=? ORDER BY id DESC LIMIT 1", (cliente_id,)
        ).fetchone()
        prev_balance = int(prev["balance"]) if prev else 0
        balance = prev_balance + entregados - recuperados
        conn.execute(
            "INSERT INTO saldos_pales (cliente_id, viaje_id, entregados, recuperados, balance, fecha, creado) "
            "VALUES (?,?,?,?,?,?,?)",
            (cliente_id, trip_id, entregados, recuperados, balance, (mtime or "")[:10],
             datetime.datetime.utcnow().isoformat() + "Z"),
        )
        conn.commit()
    return {"cliente_id": cliente_id, "balance": balance}


def _webhook_autenticado(authorization: str) -> bool:
    """Verifica la cabecera Authorization (Basic auth) del webhook de TransFollow.
    Fail closed: sin contraseña configurada se RECHAZA (nunca aceptar sin validar)."""
    if not config.TRANSFOLLOW_WEBHOOK_PASSWORD:
        return False  # sin credenciales: rechazar (fail closed)
    expected = "Basic " + base64.b64encode(
        f"{config.TRANSFOLLOW_WEBHOOK_USER}:{config.TRANSFOLLOW_WEBHOOK_PASSWORD}".encode()
    ).decode()
    return hmac.compare_digest(authorization, expected)


def _direccion_dict(r, tipo="direccion"):
    return {"id": r["id"], "tipo": tipo, "nombre": r["nombre"] or r["empresa"] or r["calle"],
            "empresa": r["empresa"], "calle": r["calle"], "numero": r["numero"], "ciudad": r["ciudad"],
            "cp": r["cp"], "pais": r["pais"], "lat": r["lat"], "lng": r["lng"]}


from services.rrhh import _procesar_dieta
from services.sync import _aplicar_estado_viaje, _extraer_documento_ecmr, _guardar_documento_entrega
