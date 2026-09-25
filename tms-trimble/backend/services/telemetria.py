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


_redis_sync = None


def _get_redis():
    """Cliente Redis síncrono para el productor (xadd/publish)."""
    global _redis_sync
    if _redis_sync is None:
        _redis_sync = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_sync


def _set_viaje_activo(vehiculo_id, trip_id):
    """Marca el viaje activo de un vehículo en Redis (lo lee ingest_worker)."""
    try:
        _get_redis().set(f"vehiculo:{vehiculo_id}:viaje_activo", trip_id)
    except Exception:
        pass


def _del_viaje_activo(vehiculo_id):
    """Limpia el viaje activo del vehículo (viaje finalizado/cancelado)."""
    try:
        _get_redis().delete(f"vehiculo:{vehiculo_id}:viaje_activo")
    except Exception:
        pass


def _json_safe(obj):
    """Convierte Decimal (psycopg2 NUMERIC) a float para que json.dumps/send_json no fallen."""
    from decimal import Decimal
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    return obj


def _viajes_snapshot() -> dict:
    """Mapa {id: viaje} con la forma que espera el frontend React (incl. lat/lng)."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT t.id, COALESCE(t.referencia, '') AS referencia, COALESCE(v.matricula, t.matricula, '') AS matricula, "
            "COALESCE(t.conductor, '') AS conductor, "
            "COALESCE(t.origen, '') AS origen, COALESCE(t.destino, '') AS destino, "
            "COALESCE(t.estado, 'planificado') AS estado, t.creado, "
            "t.fecha_esperada_carga, t.fecha_esperada_descarga, "
            "COALESCE(t.cliente, '') AS cliente, t.precio, "
            "COALESCE(t.estado_pago, 'pendiente') AS estado_pago, "
            "t.km_total, t.km_real, t.km_fuente, t.peaje_km, t.peaje_estimado, t.tiempo_min, "
            "t.modo_tarifa, t.tarifa_id, t.precio_unitario, t.kilos, t.subcontratado, t.proveedor_id, t.coste, "
            "v.terminal_trimble AS terminal_trimble, "
            "(SELECT string_agg(actividad, ' → ' ORDER BY orden) FROM operaciones.paradas WHERE trip_id = t.id) AS itinerario, "
            "(SELECT COUNT(*) FROM files WHERE trip_id = t.id) AS n_documentos, "
            "(SELECT COUNT(*) FROM operaciones.tramos WHERE trip_id = t.id) AS n_tramos "
            "FROM trips t "
            "LEFT JOIN vehiculos v ON v.codigo = t.terminal "
            "ORDER BY t.creado DESC NULLS LAST LIMIT 500"
        ).fetchall()
        # Posiciones (última por vehículo) en consulta aparte: si el hypertable de
        # telemetría se cuelga (p. ej. en CI), el listado de viajes no se bloquea.
        pos = {}
        try:
            conn.execute("SET LOCAL statement_timeout = '3000'")
            for pr in conn.execute(
                "SELECT DISTINCT ON (vehiculo_id) vehiculo_id, speed_kmh, heading, odometer_km, lat, lng "
                "FROM telemetria.posiciones_gps ORDER BY vehiculo_id, time DESC"
            ).fetchall():
                pos[pr["vehiculo_id"]] = pr
        except Exception:
            pos = {}
        finally:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        conn.close()
    out = {}
    for r in rows:
        p = pos.get(r["terminal_trimble"])
        estado = _map_estado(r["estado"])
        out[r["id"]] = {
            "id": r["id"],
            "referencia": r["referencia"] or "",
            "matricula": r["matricula"] or "",
            "conductor": r["conductor"] or "",
            "origen": r["origen"] or "",
            "destino": r["destino"] or "",
            "estado": estado,
            "progreso": _progreso(estado),
            "velocidad": p["speed_kmh"] if p else None,
            "heading": p["heading"] if p else None,
            "odometer_km": (p["odometer_km"] / 1000.0) if p and p["odometer_km"] is not None else None,
            "lat": p["lat"] if p else None,
            "lng": p["lng"] if p else None,
            "eta": None,
            "ultima_actualizacion": r["creado"] or "",
            "fecha_esperada_carga": r["fecha_esperada_carga"] or "",
            "fecha_esperada_descarga": r["fecha_esperada_descarga"] or "",
            "cliente": r["cliente"] or "",
            "precio": r["precio"],
            "estado_pago": r["estado_pago"] or "",
            "km_total": r["km_total"],
            "km_real": r["km_real"],
            "km_fuente": r["km_fuente"] or "planificado",
            "peaje_km": r["peaje_km"],
            "peaje_estimado": r["peaje_estimado"],
            "tiempo_min": r["tiempo_min"],
            "itinerario": r["itinerario"] or "",
            "n_documentos": r["n_documentos"] or 0,
            "n_tramos": r["n_tramos"] or 0,
            "disponibilidad": "En_Viaje" if r["id"] else "Libre",
            "modo_tarifa": r["modo_tarifa"] or "viaje",
            "tarifa_id": r["tarifa_id"],
            "precio_unitario": r["precio_unitario"],
            "kilos": r["kilos"] or 0,
            "subcontratado": bool(r["subcontratado"]),
            "proveedor_id": r["proveedor_id"],
            "coste": r["coste"] or 0,
        }
    return _json_safe(out)


def _extraer_posicion(block):
    """Extrae la posición de un bloque <traces> con coordenada, o None.
    Devuelve dict {source, lat, lng, time, speed, heading, mileage}."""
    lat = re.search(r"<latitude>([^<]*)</latitude>", block)
    lng = re.search(r"<longitude>([^<]*)</longitude>", block)
    src = re.search(r"<source>([^<]*)</source>", block)
    if not (lat and lng and src):
        return None
    try:
        def _f(pattern):
            m = re.search(pattern, block)
            return float(m.group(1)) if m else None
        tm = re.search(r"<time>([^<]*)</time>", block)
        return {
            "source": src.group(1).strip(),
            "lat": float(lat.group(1)),
            "lng": float(lng.group(1)),
            "time": tm.group(1) if tm else "",
            "speed": _f(r"<speed>([^<]*)</speed>"),
            "heading": _f(r"<heading>([^<]*)</heading>"),
            "mileage": _f(r"<mileage>([^<]*)</mileage>"),
        }
    except ValueError:
        return None


def _parse_trimble_ts(s):
    """Convierte una fecha ISO de Trimble (p.ej. '2026-09-21T11:43:34.739Z') a datetime, o None."""
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(s)
    except ValueError:
        return None


def _guardar_telemetria(pos, vehiculo_id):
    """Publica la posición en el Redis Stream `telemetria:ingesta` (xadd).

    ingest_worker.py consume el stream y hace la escritura masiva en TimescaleDB.
    Los nombres de campo se mapean al contrato del worker
    (speed->speed_kmh, mileage->odometer_km, source->fuente).
    """
    try:
        msg = {
            "vehiculo_id": vehiculo_id,
            "time": pos.get("time", ""),
            "lat": pos.get("lat"),
            "lng": pos.get("lng"),
            "speed_kmh": pos.get("speed"),
            "heading": pos.get("heading"),
            "odometer_km": pos.get("mileage"),
            "fuente": pos.get("source"),
        }
        _get_redis().xadd(REDIS_STREAM, {"data": json.dumps(msg)})
    except Exception:
        pass  # Redis caído no debe interrumpir la sync SOAP


def _source_a_vehiculo(conn, source):
    """Mapea el 'source' de una traza al terminal_trimble del vehículo.

    El source es el ID del proveedor de telemetría (hoy Trimble: terminal_trimble)
    o el serial del OBC (device). Devuelve el terminal_trimble (clave de telemetría),
    no el código interno del vehículo.
    """
    if not source:
        return None
    # 1) match directo por terminal_trimble (ID del proveedor de telemetría)
    row = conn.execute("SELECT terminal_trimble FROM vehiculos WHERE terminal_trimble=? LIMIT 1", (source,)).fetchone()
    if row:
        return row["terminal_trimble"]
    # 2) match por device (serial OBC)
    row = conn.execute("SELECT terminal_trimble FROM vehiculos WHERE device=? LIMIT 1", (source,)).fetchone()
    if row:
        return row["terminal_trimble"]
    # 3) fallback por sufijo (terminal_trimble o matrícula)
    suffix = source.rsplit("-", 1)[-1].strip().lower()
    if suffix:
        row = conn.execute(
            "SELECT terminal_trimble FROM vehiculos WHERE LOWER(terminal_trimble) LIKE ? OR LOWER(COALESCE(matricula,'')) LIKE ? LIMIT 1",
            (f"%{suffix}%", f"%{suffix}%"),
        ).fetchone()
        if row:
            return row["terminal_trimble"]
    return None

