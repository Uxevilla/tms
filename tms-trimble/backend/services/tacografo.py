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
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD

from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES


def _decode_dstat(b64):
    """Decodifica el DSTAT (base64 de la traza 82) en un dict con las estadísticas del conductor.

    Estructura big-endian (manual 'Trace82 / DSTAT decode'): byte + ints de 4 bytes +
    string UTF (2 bytes de longitud) + campos condicionales (service_coupure si != -1,
    compensaciones de descanso repetidas, campos v8+ opcionales).
    """
    import base64 as b64m
    import io, struct
    try:
        raw = b64m.b64decode((b64 or "").strip())
    except Exception:
        return {}
    s = io.BytesIO(raw)
    n = len(raw)

    def u8():
        return struct.unpack(">B", s.read(1))[0]

    def i32():
        return struct.unpack(">i", s.read(4))[0]

    def boolean():
        return struct.unpack(">?", s.read(1))[0]

    def utf():
        ln = struct.unpack(">H", s.read(2))[0]
        return s.read(ln).decode("utf-8", "replace") if ln > 0 else ""

    try:
        d = {}
        d["version"] = u8()                        # 1
        d["driving_coupure"] = i32()               # 2
        d["resting_coupure"] = u8()                # 3 (0 o 15)
        d["day_driving"] = i32()                   # 4
        d["day_working"] = i32()                   # 5
        d["day_waiting"] = i32()                   # 6
        d["day_resting"] = u8()                    # 7 (0 o 180)
        d["week_driving"] = i32()                  # 8
        d["week_day_count"] = u8()                 # 9 (deprecated)
        d["week_long_driving_count"] = u8()        # 10
        d["week_short_resting_count"] = u8()       # 11
        d["prev_week_driving"] = i32()             # 12
        d["prev_week_resting"] = i32()             # 13
        d["next_rest_due_ts"] = i32() * 60         # 14 (min -> s)
        d["max_day_amplitude"] = i32()             # 15
        d["remaining_week_available"] = i32()      # 16
        d["deprecated"] = i32()                    # 17
        d["monthly_service_time"] = i32()          # 18
        d["service_coupure_activation_duration"] = i32()  # 19
        if d["service_coupure_activation_duration"] != -1:
            d["service_coupure"] = i32()           # 20
            d["min_service_coupure"] = i32()       # 21
        d["calendar_day_driving"] = i32()          # 22
        d["calendar_day_working"] = i32()          # 23
        d["calendar_day_waiting"] = i32()          # 24
        d["calendar_day_distance"] = i32()         # 25
        d["codriver_session_id"] = utf()           # 26
        d["codriver_start_time"] = i32()           # 27
        d["maximum_shift_time"] = i32()            # 28
        rc_count = i32()                           # 29
        d["rest_compensations"] = []
        for _ in range(max(0, min(rc_count, 16))):
            d["rest_compensations"].append({"amount": i32(), "due_ts": i32()})  # 30/31
        d["working_time"] = i32()                  # 32
        d["60h_working_time_enable"] = boolean()   # 33
        if s.tell() < n:
            d["min_weekly_resting"] = i32()        # 34 (24 o 45)
        if s.tell() < n:
            d["min_resting"] = i32()               # 35
        if s.tell() < n:
            d["start_of_resting_ts"] = i32() * 60  # 36
        return d
    except Exception as e:
        print(f"[dstat] error decodificando DSTAT: {e}")
        return {}


def _ingestar_dstat(did, source, vehiculo_id, raw, decoded, time):
    """Persiste la foto DSTAT más reciente de un conductor (clave DID) con su terminal.

    Si el decode falla (decoded == {}), persiste igual con valores a 0 conservando
    `dstat_raw`, para poder depurar el formato real de producción de la traza 82.
    """
    with _db() as conn:
        conn.execute(
            "INSERT INTO tacografo_dstat (did, source, vehiculo_id, dstat_raw, "
            "driving_coupure_min, day_driving_min, day_working_min, day_resting_min, "
            "week_driving_min, remaining_week_available_min, week_long_driving_count, "
            "next_rest_due_ts, time, creado) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                did, source, vehiculo_id or "", raw,
                float(decoded.get("driving_coupure") or 0),
                float(decoded.get("day_driving") or 0),
                float(decoded.get("day_working") or 0),
                float(decoded.get("day_resting") or 0),
                float(decoded.get("week_driving") or 0),
                float(decoded.get("remaining_week_available") or 0),
                int(decoded.get("week_long_driving_count") or 0),
                int(decoded.get("next_rest_due_ts") or 0),
                time or "",
                datetime.datetime.utcnow().isoformat() + "Z",
            ),
        )
        conn.commit()


def _dstat_terminal(terminal):
    """Devuelve el DSTAT más reciente del conductor logueado en el terminal (o None)."""
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM tacografo_dstat WHERE vehiculo_id=? ORDER BY COALESCE(time, creado) DESC LIMIT 1",
            (terminal,),
        ).fetchone()
    if not row:
        return None
    return {
        "did": row["did"],
        "driving_coupure": float(row["driving_coupure_min"] or 0),
        "day_driving": float(row["day_driving_min"] or 0),
        "week_driving": float(row["week_driving_min"] or 0),
        "remaining_week_available": float(row["remaining_week_available_min"] or 0),
        "week_long_driving_count": int(row["week_long_driving_count"] or 0),
        "next_rest_due_ts": int(row["next_rest_due_ts"] or 0),
    }


def _chequear_conduccion_legal(viaje, row):
    """Safe-Dispatching: rechaza el despacho si el conductor del terminal no tiene tiempo legal.

    Usa el DSTAT (traza 82) del conductor logueado en el terminal asignado.
    """
    terminal = (viaje.terminal or "").strip()
    if not terminal:
        return  # sin terminal asignado, no aplica
    stats = _dstat_terminal(terminal)
    if not stats:
        return  # sin datos de tacógrafo (DSTAT), no se puede verificar (fail-open)
    tiempo_min = float(row["tiempo_min"] or 0) if row is not None and row.get("tiempo_min") else 0.0
    if tiempo_min <= 0:
        return  # sin estimación PTV, no se puede verificar

    coupure = stats["driving_coupure"]
    day = stats["day_driving"]
    long_count = stats["week_long_driving_count"]
    restante_semana = stats["remaining_week_available"]

    # (1) Conducción continua agotada (4,5 h): debe hacer pausa antes de conducir.
    if coupure >= _MAX_CONDUCCION_CONTINUA_MIN:
        raise HTTPException(status_code=400, detail={
            "error": "Conducción continua agotada (4,5 h): el conductor debe hacer una pausa de 45 min.",
            "did": stats["did"], "forzar": True,
        })

    # (2) Presupuesto diario/semanal: el viaje no debe agotar el tiempo legal restante.
    limite_diario = _EXT_DIA_CONDUCCION_MIN if long_count < 2 else _MAX_DIA_CONDUCCION_MIN
    restante = min(limite_diario - day, restante_semana)

    if tiempo_min > restante:
        raise HTTPException(status_code=400, detail={
            "error": "Conducción legal insuficiente para este viaje (Safe-Dispatching).",
            "did": stats["did"],
            "conduccion_restante_min": round(max(0.0, restante), 1),
            "duracion_viaje_min": round(tiempo_min, 1),
            "forzar": True,  # repetir con force=true para saltar el chequeo
        })
