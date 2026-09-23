"""TMS Trimble - API FastAPI que convierte formularios de viaje en envíos SOAP.

Punto de entrada delgado: crea la app, monta middleware, registra routers y
arranca los workers vía lifespan. La lógica de negocio vive en services/, los
clientes externos en clients/, la autenticación en security.py y la tenancy en
tenancy.py. Los workers corren en workers/ sin tocar FastAPI.
"""
import asyncio
import base64
import contextvars
import csv
import datetime
import hashlib
import hmac
import io
import json
import math
import os
import re
import secrets
import subprocess
import psycopg2
import psycopg2.extras
import redis
import redis.asyncio as redis_asyncio
import tempfile
import time
import threading
import urllib.parse
import urllib.request
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

import config
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "history.db"

# ----------------------------------------------------------------------
# Capas: core, modelos, db, seguridad, tenancy, clientes y servicios.
# Los servicios se reexportan desde aquí para compatibilidad con los tests
# (que usan main._auditar, main._registrar_asiento, etc.).
# ----------------------------------------------------------------------
from core import *
from models import *
from db import *

from security import (DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD, _JWT_KEY,
                      _hash_password, _verify_password, _make_jwt, _login_rate_ok,
                      _verify_jwt, require_jwt, require_role)
from tenancy import (_ensure_master, _bootstrap, _seed_tenant_config, _seed_rbac,
                      _provision_tenant, _db_master, _empresa_por_slug, _es_superadmin,
                      FIRST_TENANT_SLUG, FIRST_TENANT_NAME)
from clients.trimble import get_client, _client_cache
from clients.transfollow import get_transfollow_client, _tf_cache
from clients.geocoding import _buscar_photon, reverse_geocode
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from services.viajes import _save_trip, _save_tramos, _save_paradas, _upsert_direccion, _peaje_rate, _vehiculo_peaje_categoria, _vehiculos_en_curso, _puntos_del_viaje, _build_trip, _calcular_ruta, _viaje_payload, _guardar_documentos_pedido, _valorar_viaje, _crear_pedido, _enviar_viaje, _terminal_app, _vehiculo_ptv
from services.telemetria import _get_redis, _set_viaje_activo, _del_viaje_activo, _json_safe, _viajes_snapshot, _extraer_posicion, _parse_trimble_ts, _guardar_telemetria, _source_a_vehiculo
from services.tacografo import _decode_dstat, _ingestar_dstat, _dstat_terminal, _chequear_conduccion_legal
from services.sync import _query_terminal_states, _sync_status, _get_sync_state, _set_sync_state, _parse_props, _save_file, _extraer_reporte_xml, _extraer_documento_ecmr, _guardar_documento_entrega, _publicar_estado, _odometro_vehiculo, _aplicar_estado_viaje, _cerrar_viaje, _cerrar_viaje_por_ecmr, _entrega_confirmada, _sync_files, _sync_mensajes
from services.rrhh import _imputar_dieta_nomina, _procesar_dieta, _sync_conductor
from services.ocr import _parse_ticket, _parse_documento, _pdf_a_texto, _regex_matricula, _regex_litros, _regex_importe, _regex_fecha
from services.mensajeria import _save_mensaje, _store_mensaje, _extraer_pales, _procesar_pales, _webhook_autenticado, _direccion_dict
from services.mantenimiento import _insertar_alerta_publica, _revisar_caducidades, _revisar_revision_fecha, _revisar_mantenimiento
from services.empresa import _empresa
from services.contabilidad import _categoria_cuenta, _next_referencia, _auditar, _post_asiento, _registrar_asiento, _norm_fecha, _norm_total, _gasto_subcontrata, _facturar_viaje

from workers.ingesta import run as ingesta_run
from workers.mantenimiento import run as mantenimiento_run
from workers.facturacion import run as facturacion_run

# ----------------------------------------------------------------------
# Constantes de entorno y de negocio
# ----------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_STREAM = os.environ.get("REDIS_STREAM", "telemetria:ingesta")
REDIS_CHANNEL = os.environ.get("REDIS_CHANNEL", "canal_operaciones")

_redis_sync = None  # cliente síncrono compartido (pool thread-safe)

PUBLIC_PATHS = {"/manifest.webmanifest", "/sw.js", "/apple-touch-icon.png",
                "/favicon.ico", "/api/health",
                "/api/auth/login", "/api/auth/superadmin",
                # Endpoints del frontend React: protegidos por JWT dentro del endpoint
                # (require_jwt / require_role), no por el token HMAC del frontend antiguo.
                "/api/viajes", "/api/telemetria/activa", "/api/telemetria/trayectoria",
                # Webhook externo de TransFollow (sin token TMS): validar firma antes de producción.
                "/api/webhooks/transfollow"}

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")

# Límite de tamaño para documentos DMS (Trimble: ~3000 KB en base64 ≈ 2 MB reales)
MAX_DOC_MB = 2
MAX_DOC_B64 = MAX_DOC_MB * 1024 * 1024 * 4 // 3

# --- Safe-Dispatching (tacógrafo predictivo) -------------------------------
# Límites legales de conducción (Reglamento UE 561/2006).
_MAX_CONDUCCION_CONTINUA_MIN = 270.0   # 4,5 h de conducción continua
_MAX_DIA_CONDUCCION_MIN = 540.0        # 9 h diarias
_EXT_DIA_CONDUCCION_MIN = 600.0        # 10 h diarias (máx 2 días/semana)

# --- Automatización de dietas (RRHH) ---------------------------------------
_DIETA_IMPORTE = {
    "dieta": 26.67,           # dieta completa (manutención)
    "dieta_comida": 12.00,
    "dieta_cena": 14.67,
    "pernocta": 30.00,        # pernocta fuera de residencia
}


# ----------------------------------------------------------------------
# Lifespan: bootstrap de tenancy + arranque/cancelación de workers.
# Sustituye a los 4 @app.on_event("startup") (obsoletos en Starlette).
# ----------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):
    if not TRANSFOLLOW_WEBHOOK_PASSWORD:
        print("[seguridad] AVISO: TRANSFOLLOW_WEBHOOK_PASSWORD sin configurar → "
              "el webhook de TransFollow rechaza todas las peticiones (fail closed).")
    await asyncio.to_thread(_bootstrap)
    tasks = [asyncio.create_task(w()) for w in (ingesta_run, mantenimiento_run, facturacion_run)]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="TMS Trimble", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # auth por Bearer token (sin cookies) + WS necesita el Origin; bajo riesgo
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.middleware("http")
async def no_cache_static(request, call_next):
    """Evita que el navegador cachee el frontend (app.js/css/html)."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html")):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.websocket("/ws/operaciones")
async def ws_operaciones(websocket: WebSocket):
    """Emite cambios de viajes (estado/telemetría) en tiempo real.

    - Requiere JWT válido vía query param `?token=...` (los navegadores no
      pueden mandar cabeceras en WebSocket).
    - Se suscribe al canal Redis Pub/Sub `canal_operaciones`: cuando la ingesta
      detecta un cambio de actividad, publica un evento y aquí se retransmite al
      instante (diff inmediato). Cada 3s hace un diff completo como fallback.
    """
    token = websocket.query_params.get("token", "")
    payload = _verify_jwt(token)
    if not payload or payload.get("rol") not in ("admin", "dispatcher"):
        await websocket.close(code=1008)
        return
    await websocket.accept()

    # Suscripción Redis Pub/Sub (best-effort: sin Redis seguimos solo con polling).
    pubsub = None
    redis_async = None
    try:
        redis_async = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
        pubsub = redis_async.pubsub()
        await pubsub.subscribe(REDIS_CHANNEL)
    except Exception:
        pubsub = None

    last: dict = {}
    try:
        while True:
            # Espera o bien un evento Pub/Sub (instantáneo) o bien 3s (tick).
            if pubsub is not None:
                try:
                    await pubsub.get_message(ignore_subscribe_messages=True, timeout=3.0)
                except Exception:
                    pass
            else:
                await asyncio.sleep(3)

            snapshot = await asyncio.to_thread(_viajes_snapshot)
            ids_actuales = set(snapshot)
            for vid, v in snapshot.items():
                if vid not in last:
                    await websocket.send_json({"tipo": "creado", "viaje": v})
                    continue
                prev = last[vid]
                if prev["estado"] != v["estado"]:
                    await websocket.send_json({"tipo": "estado", "id": vid, "estado": v["estado"]})
                if (prev["velocidad"] != v["velocidad"] or prev["progreso"] != v["progreso"]
                        or prev["lat"] != v["lat"] or prev["lng"] != v["lng"]
                        or prev.get("heading") != v.get("heading")
                        or prev.get("odometer_km") != v.get("odometer_km")):
                    await websocket.send_json({
                        "tipo": "telemetria", "id": vid,
                        "velocidad": v["velocidad"], "progreso": v["progreso"],
                        "lat": v["lat"], "lng": v["lng"],
                        "heading": v.get("heading"),
                        "odometer_km": v.get("odometer_km"),
                        "fecha_esperada_descarga": v.get("fecha_esperada_descarga", ""),
                        "disponibilidad": v.get("disponibilidad", "En_Viaje"),
                    })
            for vid in set(last) - ids_actuales:
                await websocket.send_json({"tipo": "eliminado", "id": vid})
            last = snapshot
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(REDIS_CHANNEL)
                await pubsub.aclose()
            except Exception:
                pass
        if redis_async is not None:
            try:
                await redis_async.aclose()
            except Exception:
                pass


@app.middleware("http")
async def require_auth(request, call_next):
    """Autenticación por token de sesión (Bearer). Resuelve el tenant del request."""
    path = request.url.path
    # Público: frontend estático, recursos PWA y endpoints de login/health.
    if not path.startswith("/api/") or path in PUBLIC_PATHS or path.startswith("/icon-") \
            or path.startswith("/api/mantenimiento/") or path.startswith("/api/contabilidad/borradores") \
            or path.startswith("/api/contabilidad/liquidaciones"):
        return await call_next(request)
    auth = request.headers.get("Authorization", "")
    payload = None
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        payload = _verify_jwt(token)
    if not payload:
        return JSONResponse(status_code=401, content={"detail": "Acceso no autorizado"})
    rol = payload.get("rol")
    # Superadmin (JWT rol=superadmin) → BD maestra.
    if rol == "superadmin":
        _tenant_ctx.set({"db_name": config.MASTER_DB_NAME, "empresa": "", "superadmin": True})
    elif rol is not None:
        # JWT admin/dispatcher: control de rol + resolución de tenant.
        if rol not in ("admin", "dispatcher"):
            return JSONResponse(status_code=403, content={"detail": "Permisos insuficientes"})
        if rol != "admin" and path.startswith(("/api/config", "/api/contabilidad", "/api/empleados",
                                               "/api/nominas", "/api/ausencias", "/api/empresas")):
            return JSONResponse(status_code=403, content={"detail": "Solo administrador"})
        if payload.get("empresa"):
            emp = _empresa_por_slug(payload.get("empresa", ""))
            if not emp:
                return JSONResponse(status_code=401, content={"detail": "Empresa no encontrada"})
            _tenant_ctx.set({"db_name": emp["db_name"], "empresa": emp["slug"],
                             "nombre": emp["nombre"], "superadmin": False})
    # JWT sin empresa: el tenant queda en None -> _db() usa config.DB_NAME.
    request.state.usuario = payload.get("usuario") or payload.get("rol") or ""
    request.state.empresa = payload.get("empresa") or ""
    _usuario_ctx.set(request.state.usuario or "sistema")
    return await call_next(request)


# ----------------------------------------------------------------------
# Routers por dominio
# ----------------------------------------------------------------------
from routers.auth import router as auth_router
app.include_router(auth_router)
from routers.finanzas import router as finanzas_router
app.include_router(finanzas_router)
from routers.empresas import router as empresas_router
app.include_router(empresas_router)
from routers.flota import router as flota_router
app.include_router(flota_router)
from routers.gastos import router as gastos_router
app.include_router(gastos_router)
from routers.integraciones import router as integraciones_router
app.include_router(integraciones_router)
from routers.maestros import router as maestros_router
app.include_router(maestros_router)
from routers.mensajeria import router as mensajeria_router
app.include_router(mensajeria_router)
from routers.viajes import router as viajes_router
app.include_router(viajes_router)
from routers.contabilidad import router as contabilidad_router
app.include_router(contabilidad_router)
from routers.rrhh import router as rrhh_router
app.include_router(rrhh_router)
