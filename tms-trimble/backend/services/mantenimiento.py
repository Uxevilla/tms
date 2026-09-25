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


def _insertar_alerta_publica(conn, vehiculo_id, codigo, severidad, mensaje):
    """Inserta una alerta en la tabla pública unificada (dedup por vehículo+código+abierta)."""
    dup = conn.execute(
        "SELECT id FROM alertas_mantenimiento WHERE vehiculo_id=? AND codigo=? AND estado='abierta' LIMIT 1",
        (vehiculo_id, codigo),
    ).fetchone()
    if dup:
        return False
    conn.execute(
        "INSERT INTO alertas_mantenimiento (vehiculo_id, codigo, severidad, mensaje, estado) "
        "VALUES (?,?,?,?, 'abierta')",
        (vehiculo_id, codigo, severidad, mensaje),
    )
    return True


def _revisar_caducidades(conn):
    """Genera alertas por caducidad de ITV/seguro (vence en <= 30 días o ya vencida)."""
    hoy = datetime.date.today()
    limite = hoy + datetime.timedelta(days=30)
    nuevas = []
    rows = conn.execute(
        "SELECT id, fecha_caducidad_itv, fecha_caducidad_seguro FROM vehiculos "
        "WHERE COALESCE(fecha_caducidad_itv,'') <> '' OR COALESCE(fecha_caducidad_seguro,'') <> ''"
    ).fetchall()
    for v in rows:
        for campo, tipo in (("fecha_caducidad_itv", "ITV"), ("fecha_caducidad_seguro", "Seguro")):
            fecha = (v[campo] or "").strip()
            if not fecha:
                continue
            try:
                d = datetime.date.fromisoformat(fecha[:10])
            except ValueError:
                continue
            if d <= limite:
                dias = (d - hoy).days
                severidad = "alta" if dias < 0 else "media"
                mensaje = f"{tipo} " + (f"vencida hace {-dias} días" if dias < 0 else f"caduca en {dias} días")
                if _insertar_alerta_publica(conn, v["id"], f"MANT-{tipo.upper()}", severidad, mensaje):
                    nuevas.append((v["id"], tipo))
    return nuevas


def _revisar_revision_fecha(conn):
    """Genera alertas por fecha próxima de revisión (semirremolques, ITV, termógrafo...)."""
    hoy = datetime.date.today()
    limite = hoy + datetime.timedelta(days=30)
    nuevas = []
    rows = conn.execute(
        "SELECT id, categoria, fecha_proxima_revision FROM vehiculos "
        "WHERE COALESCE(fecha_proxima_revision,'') <> ''"
    ).fetchall()
    for v in rows:
        fecha = (v["fecha_proxima_revision"] or "").strip()
        try:
            d = datetime.date.fromisoformat(fecha[:10])
        except ValueError:
            continue
        if d <= limite:
            dias = (d - hoy).days
            severidad = "alta" if dias < 0 else "media"
            mensaje = "Revisión " + (f"vencida hace {-dias} días" if dias < 0 else f"programada en {dias} días")
            if _insertar_alerta_publica(conn, v["id"], "MANT-REVISION", severidad, mensaje):
                nuevas.append((v["id"], "Revisión"))
    return nuevas


def _revisar_mantenimiento():
    """Cruza el odómetro con las reglas de mantenimiento y genera alertas pendientes."""
    conn = _db()
    nuevas = []
    try:
        odos = {r["vehiculo_id"]: r["odometer_km"] for r in conn.execute(
            "SELECT DISTINCT ON (vehiculo_id) vehiculo_id, odometer_km "
            "FROM telemetria.posiciones_gps "
            "WHERE odometer_km IS NOT NULL "
            "ORDER BY vehiculo_id, time DESC"
        ).fetchall()}
        # Actualiza km_actuales del vehículo con su último odómetro conocido (en km).
        for vid, odo in odos.items():
            conn.execute("UPDATE vehiculos SET km_actuales=? WHERE terminal_trimble=?", (odo / 1000.0, vid))
        # Siembra reglas por defecto (aceite cada 80.000 km) si no hay ninguna.
        if not conn.execute("SELECT id FROM flota.reglas_mantenimiento LIMIT 1").fetchone():
            for v in conn.execute("SELECT id FROM vehiculos").fetchall():
                conn.execute(
                    "INSERT INTO flota.reglas_mantenimiento (vehiculo_id, tipo_mantenimiento, intervalo_km, ultimo_km_realizado) "
                    "VALUES (?,?,?,?)",
                    (v["id"], "Aceite", 80000, 0),
                )
        reglas = conn.execute("SELECT * FROM flota.reglas_mantenimiento").fetchall()
        for reg in reglas:
            odo = odos.get(reg["vehiculo_id"])
            if odo is None:
                continue
            ultimo = float(reg["ultimo_km_realizado"] or 0)
            intervalo = float(reg["intervalo_km"] or 0)
            if (float(odo) - ultimo) < intervalo:
                continue
            exceso = float(odo) - ultimo - intervalo
            severidad = "alta" if exceso > 5000 else "media"
            tipo = reg["tipo_mantenimiento"]
            mensaje = f"{tipo}: {int(float(odo))} km (umbral {int(ultimo + intervalo)} km)"
            if _insertar_alerta_publica(conn, reg["vehiculo_id"], f"MANT-{tipo.upper()}", severidad, mensaje):
                nuevas.append((reg["vehiculo_id"], tipo))
        nuevas.extend(_revisar_caducidades(conn))
        nuevas.extend(_revisar_revision_fecha(conn))
        conn.commit()
    finally:
        conn.close()
    for veh, tipo in nuevas:
        try:
            _get_redis().publish("canal_alertas",
                                 json.dumps({"vehiculo_id": veh, "tipo_mantenimiento": tipo,
                                             "estado": "Pendiente"}))
        except Exception:
            pass
    return len(nuevas)


from services.telemetria import _get_redis
