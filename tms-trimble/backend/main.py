"""TMS Trimble - API FastAPI que convierte formularios de viaje en envíos SOAP."""
import asyncio
import base64
import contextvars
import csv
import datetime
import hashlib
import hmac
import io
import json
import jwt
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
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from passlib.context import CryptContext

import config
from soap_client import TrimbleClient
from transfollow_client import TransFollowClient, PROD_BASE_URL, build_waybill

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "history.db"

# Tipos de actividad: nombre -> referencia (la REFERENCIA va en activity.type)
ACTIVITY_TYPES = json.load(open(BASE_DIR / "activity_types.json"))

app = FastAPI(title="TMS Trimble", version="0.1.0")
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


# ---------------------------------------------------------------------- #
from core import *  # constantes y funciones puras (estados, plan contable, peajes, importes)
from models import *  # modelos Pydantic de la API
from db import *  # conexión, esquema y contexto multi-tenant

# Multi-tenant: contexto de cliente (empresa) + autenticación por token
# ---------------------------------------------------------------------- #
_client_cache = {}
_tf_cache = {}
_master_ready = False

# ---------------------------------------------------------------------- #
# Redis: productor (Stream telemetria:ingesta) + Pub/Sub de operaciones
# ---------------------------------------------------------------------- #
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_STREAM = os.environ.get("REDIS_STREAM", "telemetria:ingesta")
REDIS_CHANNEL = os.environ.get("REDIS_CHANNEL", "canal_operaciones")

_redis_sync = None  # cliente síncrono compartido (pool thread-safe)


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

FIRST_TENANT_SLUG = os.environ.get("FIRST_TENANT_SLUG", "eusebio")
FIRST_TENANT_NAME = os.environ.get("FIRST_TENANT_NAME", "Transportes Eusebio")

PUBLIC_PATHS = {"/manifest.webmanifest", "/sw.js", "/apple-touch-icon.png",
                "/favicon.ico", "/api/health",
                "/api/auth/login", "/api/auth/superadmin",
                # Endpoints del frontend React: protegidos por JWT dentro del endpoint
                # (require_jwt / require_role), no por el token HMAC del frontend antiguo.
                "/api/viajes", "/api/telemetria/activa", "/api/telemetria/trayectoria",
                # Webhook externo de TransFollow (sin token TMS): validar firma antes de producción.
                "/api/webhooks/transfollow"}


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")


# ----------------------------------------------------------------------
# JWT — autenticación del frontend React (nuevo)
# ----------------------------------------------------------------------

# Hash de contraseñas (bcrypt vía passlib).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_ADMIN_USER = os.environ.get("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "").strip()

if not config.SECRET_KEY or len(config.SECRET_KEY) < 32:
    raise RuntimeError("TMS_SECRET_KEY no definido o < 32 caracteres; rechazo el arranque para no emitir JWT falsificables.")
_JWT_KEY = config.SECRET_KEY


def _hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


def _make_jwt(user_id, usuario: str, rol: str, empresa: str = "", ttl: int = 12 * 3600) -> str:
    payload = {"sub": str(user_id), "rol": rol, "usuario": usuario, "exp": int(time.time()) + ttl}
    if empresa:
        payload["empresa"] = empresa
    return jwt.encode(payload, _JWT_KEY, algorithm="HS256")


# Rate limiting de login (anti fuerza bruta, en memoria por proceso).
_login_attempts = {}
_login_lock = threading.Lock()
_LOGIN_MAX_INTENTOS = 8
_LOGIN_VENTANA_S = 300


def _login_rate_ok(key: str) -> bool:
    now = time.time()
    with _login_lock:
        times = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_VENTANA_S]
        if len(times) >= _LOGIN_MAX_INTENTOS:
            return False
        times.append(now)
        _login_attempts[key] = times
        return True


def _verify_jwt(token: str):
    try:
        return jwt.decode(token, _JWT_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def require_jwt(authorization: str = Header(default="")):
    """Dependencia: valida el JWT de la cabecera Authorization: Bearer."""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    payload = _verify_jwt(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail={"error": "No autenticado"})
    return payload


def require_role(required_roles):
    """Factoría de dependencias: exige que el rol del JWT esté en `required_roles`.

    Uso: `@app.get("/ruta") def ruta(user: dict = Depends(require_role(["admin"]))): ...`
    Devuelve 403 si el rol no tiene permisos suficientes.
    """
    def _check(payload: dict = Depends(require_jwt)):
        if payload.get("rol") not in required_roles:
            raise HTTPException(status_code=403, detail={"error": "Permisos insuficientes"})
        return payload
    return _check


def _ensure_master():
    """Crea la BD maestra + tabla empresas + registra el primer cliente (idempotente)."""
    global _master_ready
    if _master_ready:
        return
    try:
        # 1) crear la BD maestra si no existe (autocommit, conexión a la BD por defecto)
        conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                                user=config.DB_USER, password=config.DB_PASSWORD)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (config.MASTER_DB_NAME,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{config.MASTER_DB_NAME}"')
        conn.close()
        # 2) tabla empresas + primer cliente (la BD actual pasa a ser el primer tenant)
        m = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.MASTER_DB_NAME,
                             user=config.DB_USER, password=config.DB_PASSWORD)
        cur = m.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS empresas (slug TEXT PRIMARY KEY, nombre TEXT, db_name TEXT UNIQUE, creado TEXT)")
        cur.execute(
            "INSERT INTO empresas (slug, nombre, db_name, creado) VALUES (%s,%s,%s,%s) ON CONFLICT (slug) DO NOTHING",
            (FIRST_TENANT_SLUG, FIRST_TENANT_NAME, config.DB_NAME,
             datetime.datetime.utcnow().isoformat() + "Z"),
        )
        m.commit()
        m.close()
        # 3) sembrar config del primer cliente desde .env
        _seed_tenant_config(config.DB_NAME, {
            "trimble_username": config.DEFAULT_TRIMBLE_USERNAME,
            "trimble_password": config.DEFAULT_TRIMBLE_PASSWORD,
            "trimble_customer": config.DEFAULT_TRIMBLE_CUSTOMER,
            "trimble_terminal": config.DEFAULT_TRIMBLE_TERMINAL,
            "ptv_api_key": config.DEFAULT_PTV_API_KEY,
            "transfollow_api_key": config.DEFAULT_TRANSFOLLOW_API_KEY,
            "transfollow_base_url": config.DEFAULT_TRANSFOLLOW_BASE_URL,
            "auth_users": ",".join(config.DEFAULT_AUTH_USERS),
            "auth_password": config.DEFAULT_AUTH_PASSWORD,
        })
        _seed_rbac(config.DB_NAME)
        _master_ready = True
    except Exception as e:
        print(f"[bootstrap] {e}")


def _bootstrap():
    """Inicializa el esquema del primer tenant + BD maestra + RBAC (idempotente).

    Debe ejecutarse en el arranque: `_ensure_master()` solo se disparaba al consultar
    la BD maestra, por lo que en una base limpia no se creaba `tms_master` ni se
    sembraba el admin. Aquí garantizamos el orden: esquema -> maestra -> RBAC.
    """
    try:
        c = _db()          # crea el esquema del tenant (config.roles/usuarios, ...)
        c.close()
        _ensure_master()   # tms_master + empresas + primer cliente + seed config/RBAC
    except Exception as e:
        print(f"[bootstrap] {e}")


def _seed_tenant_config(db_name, values=None):
    """Siembra claves en la tabla config de un cliente (solo las que aún no existen)."""
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=db_name,
                            user=config.DB_USER, password=config.DB_PASSWORD)
    cur = conn.cursor()
    for k, v in (values or {}).items():
        cur.execute("INSERT INTO config (key, value) VALUES (%s,%s) ON CONFLICT (key) DO NOTHING", (k, v or ""))
    conn.commit()
    conn.close()


def _seed_rbac(dbname: str) -> None:
    """Siembra roles y el usuario admin por defecto en la BD del cliente (idempotente).

    La contraseña del admin viene de DEFAULT_ADMIN_PASSWORD (.env). Si no está definida,
    se genera una aleatoria y se fuerza el cambio en el primer login. La rotación nunca
    pisa la clave de un admin que ya la cambió (debe_cambiar_clave=false)."""
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=dbname,
                            user=config.DB_USER, password=config.DB_PASSWORD)
    try:
        cur = conn.cursor()
        cur.execute("ALTER TABLE config.usuarios ADD COLUMN IF NOT EXISTS debe_cambiar_clave BOOLEAN DEFAULT false")
        for nombre, desc in (("admin", "Administrador"), ("dispatcher", "Dispatcher"), ("conductor", "Conductor")):
            cur.execute("INSERT INTO config.roles (nombre, descripcion) VALUES (%s,%s) ON CONFLICT (nombre) DO NOTHING", (nombre, desc))
        admin_pw = DEFAULT_ADMIN_PASSWORD
        if not admin_pw:
            admin_pw = secrets.token_urlsafe(16)
            print(f"[seguridad] DEFAULT_ADMIN_PASSWORD sin definir → contraseña temporal del admin "
                  f"'{DEFAULT_ADMIN_USER}': {admin_pw} (cámbiala en el primer login)")
        h = _hash_password(admin_pw)
        cur.execute(
            "INSERT INTO config.usuarios (usuario, password_hash, rol, nombre, activo, debe_cambiar_clave) "
            "VALUES (%s,%s,'admin','Administrador',true,true) ON CONFLICT (usuario) DO NOTHING",
            (DEFAULT_ADMIN_USER, h),
        )
        if DEFAULT_ADMIN_PASSWORD:
            # .env explícito = contraseña canónica del admin → rota SIEMPRE (break-glass reset).
            cur.execute(
                "UPDATE config.usuarios SET password_hash=%s, debe_cambiar_clave=true, activo=true "
                "WHERE usuario=%s",
                (h, DEFAULT_ADMIN_USER),
            )
        else:
            # Auto-generada → rota solo si aún debe cambiar clave (evita cambiarla en cada boot).
            cur.execute(
                "UPDATE config.usuarios SET password_hash=%s, debe_cambiar_clave=true, activo=true "
                "WHERE usuario=%s AND (debe_cambiar_clave IS NULL OR debe_cambiar_clave = true)",
                (h, DEFAULT_ADMIN_USER),
            )
        conn.commit()
    finally:
        conn.close()


def _provision_tenant(slug, nombre, seed_values=None):
    """Crea la BD del cliente + esquema + registro. Devuelve (db_name, error)."""
    db_name = f"tms_{slug}"
    # 1) crear la base de datos (si no existe)
    try:
        conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                                user=config.DB_USER, password=config.DB_PASSWORD)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{db_name}"')
        conn.close()
    except Exception as e:
        return db_name, str(e)
    # 2) inicializar esquema apuntando temporalmente el contexto al nuevo tenant
    token = _tenant_ctx.set({"db_name": db_name, "empresa": slug, "superadmin": False})
    try:
        c = _db()
        c.close()
    finally:
        _tenant_ctx.reset(token)
    seed = dict(seed_values or {})
    # Claves de integración vacías: un tenant nuevo NO debe heredar las del primer cliente.
    for k in (*config.TRIMBLE_KEYS, *config.PTV_KEYS, *config.SMTP_KEYS, *config.TRANSFOLLOW_KEYS):
        seed.setdefault(k, "")
    _seed_tenant_config(db_name, seed)
    _seed_rbac(db_name)
    # 3) registrar en la BD maestra
    m = _db_master()
    try:
        m.execute(
            "INSERT INTO empresas (slug, nombre, db_name, creado) VALUES (?,?,?,?) "
            "ON CONFLICT (slug) DO UPDATE SET nombre=EXCLUDED.nombre, db_name=EXCLUDED.db_name",
            (slug, nombre, db_name, datetime.datetime.utcnow().isoformat() + "Z"),
        )
        m.commit()
    finally:
        m.close()
    return db_name, None


def _db_master():
    """Conexión a la BD maestra (registro de empresas)."""
    _ensure_master()
    conn = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT,
                            dbname=config.MASTER_DB_NAME,
                            user=config.DB_USER, password=config.DB_PASSWORD)
    return _Conn(conn)


def _empresa_por_slug(slug):
    conn = _db_master()
    try:
        row = conn.execute("SELECT slug, nombre, db_name FROM empresas WHERE slug=?", (slug,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_client():
    """Cliente SOAP del tenant actual (cacheado por credenciales Trimble)."""
    t = _tenant_ctx.get()
    if not t:
        u = config.DEFAULT_TRIMBLE_USERNAME
        p = config.DEFAULT_TRIMBLE_PASSWORD
        c = config.DEFAULT_TRIMBLE_CUSTOMER
        term = config.DEFAULT_TRIMBLE_TERMINAL
    else:
        conn = _db()
        try:
            rows = conn.execute(
                "SELECT key, value FROM config WHERE key IN (?,?,?,?)",
                ("trimble_username", "trimble_password", "trimble_customer", "trimble_terminal"),
            ).fetchall()
        finally:
            conn.close()
        cfg = {r["key"]: r["value"] for r in rows}
        u = cfg.get("trimble_username", "") or ""
        p = cfg.get("trimble_password", "") or ""
        c = cfg.get("trimble_customer", "") or ""
        term = cfg.get("trimble_terminal", "") or ""
        if not u or not c:
            raise HTTPException(status_code=503, detail={"error": "Trimble no configurado para este cliente"})
    key = (u, c)
    if key not in _client_cache:
        _client_cache[key] = TrimbleClient(u, p, c, term)
    return _client_cache[key]


def get_transfollow_client():
    """Cliente REST de TransFollow del tenant actual (cacheado)."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM config WHERE key IN (?,?)",
            ("transfollow_api_key", "transfollow_base_url"),
        ).fetchall()
    finally:
        conn.close()
    cfg = {r["key"]: r["value"] for r in rows}
    api_key = cfg.get("transfollow_api_key", "") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail={"error": "TransFollow no configurado para este cliente"})
    base_url = cfg.get("transfollow_base_url", "") or PROD_BASE_URL
    if api_key not in _tf_cache:
        _tf_cache[api_key] = TransFollowClient(api_key, base_url)
    return _tf_cache[api_key]


# ----------------------------------------------------------------------
# Frontend React (nuevo): snapshot de viajes + WebSocket de operaciones
# ----------------------------------------------------------------------

# Estados de viaje gobernados por la telemática Trimble (macros del FleetXPS).
# (estados del viaje + mapa de códigos Trimble movidos a core.py)


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
            "(SELECT string_agg(actividad, ' → ' ORDER BY orden) FROM paradas WHERE trip_id = t.id) AS itinerario, "
            "(SELECT COUNT(*) FROM files WHERE trip_id = t.id) AS n_documentos, "
            "(SELECT COUNT(*) FROM tramos WHERE trip_id = t.id) AS n_tramos, "
            "tl.speed_kmh AS velocidad, tl.heading, tl.odometer_km, tl.lat, tl.lng "
            "FROM trips t "
            "LEFT JOIN vehiculos v ON v.id = t.terminal "
            "LEFT JOIN LATERAL ("
            "  SELECT speed_kmh, heading, odometer_km, lat, lng FROM telemetria.posiciones_gps "
            "  WHERE vehiculo_id = t.terminal ORDER BY time DESC LIMIT 1"
            ") tl ON true "
            "ORDER BY t.creado DESC NULLS LAST LIMIT 500"
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
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
            "velocidad": r["velocidad"],
            "heading": r["heading"],
            "odometer_km": (r["odometer_km"] / 1000.0) if r["odometer_km"] is not None else None,
            "lat": r["lat"],
            "lng": r["lng"],
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


@app.post("/api/auth/login")
def auth_login(req: dict, request: Request):
    """Login del frontend React: valida contra config.usuarios (bcrypt) y devuelve JWT.

    Acepta `empresa` (slug) opcional para resolver el tenant; sin slug usa el tenant por defecto.
    """
    usuario = (req.get("usuario") or "").strip()
    contrasena = req.get("contrasena") or req.get("password") or ""
    empresa = (req.get("empresa") or "").strip().lower()
    ip = request.client.host if request.client else "?"
    if not _login_rate_ok(f"{ip}:{usuario}"):
        raise HTTPException(status_code=429, detail={"error": "Demasiados intentos de login. Espera unos minutos."})
    token_ctx = None
    if empresa:
        emp = _empresa_por_slug(empresa)
        if not emp:
            raise HTTPException(status_code=401, detail={"error": "Empresa no encontrada"})
        token_ctx = _tenant_ctx.set({"db_name": emp["db_name"], "empresa": emp["slug"],
                                     "nombre": emp["nombre"], "superadmin": False})
    try:
        conn = _db()
        try:
            row = conn.execute(
                "SELECT id, usuario, password_hash, rol, activo, COALESCE(debe_cambiar_clave, false) AS debe_cambiar_clave "
                "FROM config.usuarios WHERE usuario=?",
                (usuario,),
            ).fetchone()
        finally:
            conn.close()
    finally:
        if token_ctx is not None:
            _tenant_ctx.reset(token_ctx)
    if not row or not row["activo"] or not _verify_password(contrasena, row["password_hash"]):
        raise HTTPException(status_code=401, detail={"error": "Usuario o contraseña incorrectos"})
    return {"token": _make_jwt(row["id"], row["usuario"], row["rol"], empresa),
            "usuario": row["usuario"], "rol": row["rol"], "id": row["id"],
            "empresa": empresa,
            "debe_cambiar_clave": bool(row["debe_cambiar_clave"])}


@app.post("/api/auth/superadmin")
def auth_superadmin(req: dict, request: Request):
    """Login del superadmin (gestión de empresas) → JWT con rol 'superadmin'."""
    usuario = (req.get("usuario") or "").strip()
    contrasena = req.get("contrasena") or req.get("password") or ""
    ip = request.client.host if request.client else "?"
    if not _login_rate_ok(f"{ip}:sa:{usuario}"):
        raise HTTPException(status_code=429, detail={"error": "Demasiados intentos de login. Espera unos minutos."})
    if usuario != config.SUPERADMIN_USER or not secrets.compare_digest(config.SUPERADMIN_PASSWORD or "", contrasena):
        raise HTTPException(status_code=401, detail={"error": "Credenciales de administrador incorrectas"})
    return {"token": _make_jwt(0, usuario, "superadmin"),
            "superadmin": True, "usuario": usuario,
            "empresa": "admin", "nombre": "Administración"}


@app.post("/api/auth/change-password")
def change_password(req: dict, user: dict = Depends(require_jwt)):
    """Cambia la contraseña del usuario autenticado y limpia el flag de cambio forzado."""
    antigua = (req.get("old_password") or "").strip()
    nueva = (req.get("new_password") or "").strip()
    if len(nueva) < 12:
        raise HTTPException(status_code=400, detail={"error": "La nueva contraseña debe tener al menos 12 caracteres."})
    conn = _db()
    try:
        row = conn.execute("SELECT password_hash FROM config.usuarios WHERE usuario=?", (user["usuario"],)).fetchone()
        if not row or not _verify_password(antigua, row["password_hash"]):
            raise HTTPException(status_code=401, detail={"error": "Contraseña actual incorrecta."})
        conn.execute("UPDATE config.usuarios SET password_hash=?, debe_cambiar_clave=false WHERE usuario=?",
                     (_hash_password(nueva), user["usuario"]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.get("/api/viajes")
def api_viajes(user: dict = Depends(require_role(["admin", "dispatcher"]))):
    snap = _viajes_snapshot()
    return {"viajes": list(snap.values())}


@app.get("/api/telemetria/activa")
def api_telemetria_activa(user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Última coordenada de TODOS los vehículos + disponibilidad (Libre / En_Viaje)."""
    conn = _db()
    try:
        rows = conn.execute(
            f"WITH ultima AS ("
            f"  SELECT DISTINCT ON (vehiculo_id) vehiculo_id, lat, lng, speed_kmh, heading, odometer_km, time "
            f"  FROM telemetria.posiciones_gps ORDER BY vehiculo_id, time DESC"
            f"), activa AS ("
            f"  SELECT DISTINCT ON (terminal) terminal, id, estado, fecha_esperada_descarga, "
            f"         conductor, matricula "
            f"  FROM trips WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL} "
            f"  ORDER BY terminal, creado DESC"
            f"), dstat AS ("
            f"  SELECT DISTINCT ON (vehiculo_id) vehiculo_id, did "
            f"  FROM tacografo_dstat ORDER BY vehiculo_id, COALESCE(time, creado) DESC"
            f") "
            f"SELECT u.vehiculo_id, u.lat, u.lng, u.speed_kmh AS velocidad, "
            f"       u.heading, u.odometer_km, u.time, "
            f"       a.id AS viaje_id, COALESCE(a.estado,'') AS estado, "
            f"       a.fecha_esperada_descarga AS fecha_esperada_descarga, "
            f"       COALESCE(v.matricula, a.matricula, '') AS matricula, "
            f"       COALESCE(a.conductor,'') AS conductor, "
            f"       c.nombre AS conductor_taco "
            f"FROM ultima u "
            f"LEFT JOIN vehiculos v ON v.id = u.vehiculo_id "
            f"LEFT JOIN activa a ON a.terminal = u.vehiculo_id "
            f"LEFT JOIN dstat d ON d.vehiculo_id = u.vehiculo_id "
            f"LEFT JOIN conductores c ON c.did = d.did "
            f"ORDER BY u.time DESC"
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        tiene_viaje = bool(r["viaje_id"])
        out.append({
            "vehiculo_id": r["vehiculo_id"],
            "viaje_id": r["viaje_id"],
            "lat": r["lat"], "lng": r["lng"], "velocidad": r["velocidad"],
            "heading": r["heading"],
            "odometer_km": (r["odometer_km"] / 1000.0) if r["odometer_km"] is not None else None,
            "conductor_taco": r["conductor_taco"] or "",
            "ultima_telemetria": r["time"].isoformat() if r["time"] else None,
            "disponibilidad": "En_Viaje" if tiene_viaje else "Libre",
            "estado": _map_estado(r["estado"]) if tiene_viaje else "",
            "fecha_esperada_descarga": r["fecha_esperada_descarga"] or "",
            "matricula": r["matricula"] or "", "conductor": r["conductor"] or "",
        })
    return {"telemetria": out}


@app.get("/api/telemetria/trayectoria")
def api_telemetria_trayectoria(user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Últimos 20 puntos por vehículo (rastro) para pintar polilíneas en el mapa."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT vehiculo_id, lat, lng FROM ("
            "  SELECT vehiculo_id, lat, lng, "
            "         ROW_NUMBER() OVER (PARTITION BY vehiculo_id ORDER BY time DESC) AS rn "
            "  FROM telemetria.posiciones_gps"
            ") x WHERE rn <= 20 ORDER BY vehiculo_id, rn DESC"
        ).fetchall()
    finally:
        conn.close()
    out: dict = {}
    for r in rows:
        out.setdefault(r["vehiculo_id"], []).append([r["lat"], r["lng"]])
    return {"trayectorias": out}


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


async def _poll_ingesta():
    """Bucle de ingesta asíncrono: trazas/archivos y mensajería EN PARALELO.

    Los mensajes se sondean cada 2s en un bucle propio para que el chat no sufra
    el delay del procesado de trazas (que corre cada 5s en otro bucle).
    """

    async def _loop_files():
        while True:
            try:
                await asyncio.to_thread(_sync_files)
            except Exception as e:
                print(f"[ingesta] error _sync_files: {e}")
            await asyncio.sleep(5)

    async def _loop_mensajes():
        while True:
            try:
                await asyncio.to_thread(_sync_mensajes)
            except Exception as e:
                print(f"[ingesta] error _sync_mensajes: {e}")
            await asyncio.sleep(2)

    await asyncio.gather(_loop_files(), _loop_mensajes())


@app.on_event("startup")
async def _arrancar_ingesta():
    """Arranca el productor en segundo plano al levantar la API."""
    asyncio.create_task(_poll_ingesta())


@app.on_event("startup")
async def _arrancar_mantenimiento():
    """Arranca el revisor de mantenimiento predictivo en segundo plano."""
    asyncio.create_task(_poll_mantenimiento())


@app.on_event("startup")
async def _arrancar_facturacion():
    """Arranca el listener de facturación automática en segundo plano."""
    asyncio.create_task(_facturacion_listener())


@app.on_event("startup")
async def _arrancar_bootstrap():
    """Crea la BD maestra + siembra RBAC/config del primer cliente al arrancar."""
    if not TRANSFOLLOW_WEBHOOK_PASSWORD:
        print("[seguridad] AVISO: TRANSFOLLOW_WEBHOOK_PASSWORD sin configurar → "
              "el webhook de TransFollow rechaza todas las peticiones (fail closed).")
    await asyncio.to_thread(_bootstrap)


@app.middleware("http")
async def require_auth(request, call_next):
    """Autenticación por token de sesión (Bearer). Resuelve el tenant del request."""
    path = request.url.path
    # Público: frontend estático, recursos PWA y endpoints de login/health.
    # Solo se protegen los endpoints de datos (/api/*).
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

# Límite de tamaño para documentos DMS (Trimble: ~3000 KB en base64 ≈ 2 MB reales)
MAX_DOC_MB = 2
MAX_DOC_B64 = MAX_DOC_MB * 1024 * 1024 * 4 // 3

# --- Safe-Dispatching (tacógrafo predictivo) -------------------------------
# Límites legales de conducción (Reglamento UE 561/2006). Se comparan contra
# el DSTAT (traza 82) decodificado, no contra poll_driving_times.
_MAX_CONDUCCION_CONTINUA_MIN = 270.0   # 4,5 h de conducción continua
_MAX_DIA_CONDUCCION_MIN = 540.0        # 9 h diarias
_EXT_DIA_CONDUCCION_MIN = 600.0        # 10 h diarias (máx 2 días/semana)

# --- Automatización de dietas (RRHH) ---------------------------------------
# Importes por tipo de dieta (€). AJUSTAR a la política real de dietas.
_DIETA_IMPORTE = {
    "dieta": 26.67,           # dieta completa (manutención)
    "dieta_comida": 12.00,
    "dieta_cena": 14.67,
    "pernocta": 30.00,        # pernocta fuera de residencia
}


# ---------------------------------------------------------------------- #
# Persistencia (PostgreSQL / TimescaleDB)
# ---------------------------------------------------------------------- #



# (categorías + plan contable + mapeos de cuentas movidos a core.py)


def _categoria_cuenta(conn, categoria):
    """Cuenta contable (grupo 6) para una categoría de gasto: BD -> mapeo por defecto -> 629."""
    if categoria:
        row = conn.execute("SELECT cuenta FROM categorias_gasto WHERE nombre=?", (categoria,)).fetchone()
        if row and row["cuenta"]:
            return row["cuenta"]
    return _CATEGORIA_CUENTA.get(categoria, "629")


# (peaje categorías + categorías de vehículo + estados finales movidos a core.py)




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


def _save_trip(trip_id, nombre, matricula, conductor, tipo_carga,
               origen, destino, n_tareas, estado, error, terminal="",
               semirremolque_id="", remolque_id="",
               cliente="", precio=0.0, km_total=0.0, gastos=0.0,
               factura="", estado_pago="pendiente", iva=21.0,
               cliente_id=None, conductor_id=None,
               peaje_km=0.0, peaje_estimado=0.0, peaje_fuente="estimado",
               tiempo_min=0.0, trafico_min=0.0, pausas_min=0.0,
               fecha_esperada_carga="", fecha_esperada_descarga="",
               modo_tarifa="viaje", tarifa_id=None, precio_unitario=None,
               kilos=0.0, subcontratado=False, proveedor_id=None, coste=0.0):
    conn = _db()
    # referencia interna: conservar la existente o generar una nueva
    row = conn.execute("SELECT referencia FROM trips WHERE id=?", (trip_id,)).fetchone()
    referencia = (row["referencia"] if row and row["referencia"] else "") or ""
    if not referencia:
        referencia = _next_referencia(conn)
    conn.execute(
        "INSERT INTO trips "
        "(id, nombre, matricula, conductor, tipo_carga, origen, destino, "
        "tareas, estado, error, creado, terminal, semirremolque_id, remolque_id, "
        "cliente, cliente_id, conductor_id, "
        "precio, km_total, tiempo_min, pausas_min, trafico_min, peaje_km, peaje_estimado, peaje_fuente, "
        "gastos, factura, estado_pago, iva, referencia, fecha_esperada_carga, fecha_esperada_descarga, "
        "modo_tarifa, tarifa_id, precio_unitario, kilos, subcontratado, proveedor_id, coste) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET "
        "nombre=EXCLUDED.nombre, matricula=EXCLUDED.matricula, conductor=EXCLUDED.conductor, "
        "tipo_carga=EXCLUDED.tipo_carga, origen=EXCLUDED.origen, destino=EXCLUDED.destino, "
        "tareas=EXCLUDED.tareas, estado=EXCLUDED.estado, error=EXCLUDED.error, "
        "creado=EXCLUDED.creado, terminal=EXCLUDED.terminal, "
        "semirremolque_id=EXCLUDED.semirremolque_id, remolque_id=EXCLUDED.remolque_id, "
        "cliente=EXCLUDED.cliente, cliente_id=EXCLUDED.cliente_id, conductor_id=EXCLUDED.conductor_id, "
        "precio=EXCLUDED.precio, km_total=EXCLUDED.km_total, tiempo_min=EXCLUDED.tiempo_min, "
        "pausas_min=EXCLUDED.pausas_min, trafico_min=EXCLUDED.trafico_min, peaje_km=EXCLUDED.peaje_km, "
        "peaje_estimado=EXCLUDED.peaje_estimado, peaje_fuente=EXCLUDED.peaje_fuente, "
        "gastos=EXCLUDED.gastos, factura=EXCLUDED.factura, "
        "estado_pago=EXCLUDED.estado_pago, iva=EXCLUDED.iva, referencia=EXCLUDED.referencia, "
        "fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga, "
        "modo_tarifa=EXCLUDED.modo_tarifa, tarifa_id=EXCLUDED.tarifa_id, precio_unitario=EXCLUDED.precio_unitario, "
        "kilos=EXCLUDED.kilos, subcontratado=EXCLUDED.subcontratado, proveedor_id=EXCLUDED.proveedor_id, coste=EXCLUDED.coste",
        (trip_id, nombre, matricula, conductor, tipo_carga, origen,
         destino, n_tareas, estado, error,
         datetime.datetime.utcnow().isoformat() + "Z", terminal, semirremolque_id, remolque_id,
         cliente, cliente_id, conductor_id,
         precio, km_total, tiempo_min, pausas_min, trafico_min, peaje_km, peaje_estimado, peaje_fuente,
         gastos, factura, estado_pago, iva, referencia, fecha_esperada_carga, fecha_esperada_descarga,
         modo_tarifa, tarifa_id, precio_unitario, kilos, subcontratado, proveedor_id, coste),
    )
    conn.commit()
    conn.close()


def _save_tramos(conn, trip_id, tramos):
    """Persiste los tramos (segmentos) de un viaje; cada tramo con su vehículo/conductor."""
    conn.execute("DELETE FROM tramos WHERE trip_id=?", (trip_id,))
    for t in tramos:
        conn.execute(
            "INSERT INTO tramos (trip_id, orden, origen_nombre, origen_ciudad, origen_lat, origen_lng, "
            "destino_nombre, destino_ciudad, destino_lat, destino_lng, terminal, conductor, fecha_carga, fecha_descarga, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trip_id, t.orden, t.origen_nombre, t.origen_ciudad, t.origen_lat, t.origen_lng,
             t.destino_nombre, t.destino_ciudad, t.destino_lat, t.destino_lng,
             t.terminal, t.conductor, t.fecha_carga, t.fecha_descarga,
             datetime.datetime.utcnow().isoformat() + "Z"),
        )


def _save_paradas(trip_id, viaje):
    conn = _db()
    conn.execute("DELETE FROM paradas WHERE trip_id=?", (trip_id,))

    def insert(i, d, actividad):
        conn.execute(
            "INSERT INTO paradas (trip_id, orden, nombre, ciudad, lat, lng, actividad, comentario) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (trip_id, i, d.nombre or "", d.ciudad or "", d.lat, d.lng, actividad, d.comentario or ""),
        )

    insert(0, viaje.origen, viaje.origen.actividad or "CARGA")
    for i, p in enumerate(viaje.paradas, start=1):
        insert(i, p, p.actividad or "DESCARGA")
    insert(len(viaje.paradas) + 1, viaje.destino, viaje.destino.actividad or "DESCARGA")
    conn.commit()
    conn.close()


def _upsert_direccion(conn, d):
    """Guarda (o reutiliza) una dirección en el maestro y devuelve su id, o None si no hay datos."""
    ciudad = (d.ciudad or "").strip()
    calle = (d.calle or "").strip()
    numero = (d.numero or "").strip()
    nombre = (d.nombre or "").strip()
    empresa = (d.empresa or "").strip()
    cp = (d.cp or "").strip()
    if not (ciudad or calle or nombre or empresa):
        return None
    row = conn.execute(
        "SELECT id FROM direcciones WHERE ciudad=? AND calle=? AND numero=? AND nombre=? AND empresa=? AND cp=? LIMIT 1",
        (ciudad, calle, numero, nombre, empresa, cp),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO direcciones (nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (nombre, empresa, calle, numero, ciudad, cp, d.pais or "ES", d.lat, d.lng, d.comentario or "",
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    return cur.fetchone()["id"]


def _peaje_rate(categoria):
    if not categoria:
        return 0.0
    conn = _db()
    row = conn.execute("SELECT eur_km FROM tarifas_peaje WHERE categoria=?", (categoria,)).fetchone()
    conn.close()
    if row and row["eur_km"] is not None:
        return float(row["eur_km"])
    return float(_PEAJE_CATEGORIAS.get(categoria, {}).get("eur_km", 0.0))


def _vehiculo_peaje_categoria(terminal):
    if not terminal:
        return "pesado4"
    conn = _db()
    row = conn.execute("SELECT peaje_categoria FROM vehiculos WHERE id=?", (terminal,)).fetchone()
    conn.close()
    return (row["peaje_categoria"] if row and row["peaje_categoria"] else "pesado4")


def _vehiculos_en_curso(exclude_trip_id=None):
    """Ids de REMOLQUES (semirremolque/remolque) con un viaje activo (no finalizado).
    El terminal (tractora) NO se incluye: admite varios viajes en cola (se ejecutan uno tras otro)."""
    conn = _db()
    q = (f"SELECT semirremolque_id, remolque_id FROM trips "
         f"WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL}")
    params = []
    if exclude_trip_id:
        q += " AND id != ?"
        params.append(exclude_trip_id)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    ids = set()
    for r in rows:
        for v in (r["semirremolque_id"], r["remolque_id"]):
            if v:
                ids.add(v)
    return ids


def _haversine_km(lat1, lng1, lat2, lng2):
    """Distancia en línea recta (km) entre dos coordenadas."""
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return None
    R = 6371.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(R * 2 * math.asin(math.sqrt(a)), 1)


def _vehiculo_posicion(terminal):
    """Última posición conocida de un vehículo (lat, lng) o None."""
    conn = _db()
    row = conn.execute("SELECT last_lat, last_lng FROM vehiculos WHERE id=?", (terminal,)).fetchone()
    conn.close()
    if row and row["last_lat"] is not None and row["last_lng"] is not None:
        return (float(row["last_lat"]), float(row["last_lng"]))
    return None


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
    """Mapea el 'source' de una traza al id de vehículo.

    El source puede ser la matrícula (id del vehículo) o el serial del OBC (device).
    """
    if not source:
        return None
    # 1) match directo por id (referencia Trimble = matrícula)
    row = conn.execute("SELECT id FROM vehiculos WHERE id=? LIMIT 1", (source,)).fetchone()
    if row:
        return row["id"]
    # 2) match por device (serial OBC)
    row = conn.execute("SELECT id FROM vehiculos WHERE device=? LIMIT 1", (source,)).fetchone()
    if row:
        return row["id"]
    # 3) fallback por sufijo
    suffix = source.rsplit("-", 1)[-1].strip().lower()
    if suffix:
        row = conn.execute(
            "SELECT id FROM vehiculos WHERE LOWER(id) LIKE ? OR LOWER(COALESCE(matricula,'')) LIKE ? LIMIT 1",
            (f"%{suffix}%", f"%{suffix}%"),
        ).fetchone()
        if row:
            return row["id"]
    return None


def _terminal_app(conn, vehiculo_id):
    """Resuelve el terminal APP (Fleet XPS) para el despacho SOAP, desde un vehículo T4U.

    El T4U (id = matrícula) solo da telemetría/tacógrafo; los viajes y question paths
    se envían a la APP vinculada (id con sufijo 'APP'). Si no hay APP vinculada,
    cae al terminal APP por defecto (config.DEFAULT_TRIMBLE_TERMINAL, p. ej. 'demo').
    """
    row = conn.execute("SELECT app_terminal FROM vehiculos WHERE id=?", (vehiculo_id,)).fetchone()
    if row and row["app_terminal"]:
        return row["app_terminal"]
    return config.DEFAULT_TRIMBLE_TERMINAL or vehiculo_id


def _vehiculo_ptv(terminal):
    """Atributos PTV del vehículo para el cálculo de peaje exacto."""
    conn = _db()
    row = conn.execute(
        "SELECT ptv_profile, ejes, mma, clase_euro FROM vehiculos WHERE id=?", (terminal,)
    ).fetchone()
    conn.close()
    return {
        "ptv_profile": (row["ptv_profile"] if row and row["ptv_profile"] else "EUR_TRAILER_TRUCK"),
        "ejes": (row["ejes"] if row else None),
        "mma": (row["mma"] if row else None),
        "clase_euro": (row["clase_euro"] if row and row["clase_euro"] else ""),
    }


def _ptv_route(puntos, veh, conduccion_acumulada_min=0.0):
    """Ruta completa vía PTV: distancia, tiempo, tráfico, peaje, polyline y eventos de tráfico.

    conduccion_acumulada_min: minutos de conducción ya realizados desde la última pausa
    (del tacógrafo); alimenta el workLogbook para que PTV aplique la pausa restante correcta.

    Devuelve dict {'distance_km', 'travel_time_min', 'traffic_delay_min', 'toll',
    'currency', 'polyline', 'traffic_events', 'schedule'} o None si falla.
    """
    if not _get_config("ptv_api_key", config.DEFAULT_PTV_API_KEY) or len(puntos) < 2:
        return None
    waypoints = [{"onRoad": {"latitude": p["lat"], "longitude": p["lng"]}} for p in puntos]
    profile = veh.get("ptv_profile") or "EUR_TRAILER_TRUCK"
    url = f"{config.PTV_BASE_URL}/routes?profile={profile}&results=TOLL_COSTS,MONETARY_COSTS,POLYLINE,TRAFFIC_EVENTS,SCHEDULE_EVENTS,SCHEDULE_REPORT"
    url += "&options%5BtrafficMode%5D=REALISTIC&options%5BpolylineFormat%5D=GOOGLE_ENCODED_POLYLINE"
    extra = {}
    if veh.get("ejes"):
        extra["numberOfAxles"] = int(veh["ejes"])
    if veh.get("mma"):
        extra["totalPermittedWeight"] = int(veh["mma"])
    if veh.get("clase_euro"):
        extra["emissionStandard"] = veh["clase_euro"]
    if extra:
        url += "&" + "&".join(f"vehicle%5B{k}%5D={v}" for k, v in extra.items())
    body = {"waypoints": waypoints}
    driver = {"workingHoursPreset": "EU_DRIVING_TIME_REGULATION_FOR_MULTIPLE_DAYS"}
    if conduccion_acumulada_min and conduccion_acumulada_min > 0:
        driver["workLogbook"] = {
            "lastTimeTheDriverWorked": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "accumulatedDrivingTimeSinceLastBreak": int(conduccion_acumulada_min * 60),
        }
    body["driver"] = driver
    try:
        req = urllib.request.Request(
            url, method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "apiKey": _get_config("ptv_api_key", config.DEFAULT_PTV_API_KEY)},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode())
        prices = data.get("toll", {}).get("costs", {}).get("prices", [])
        toll = round(float(prices[0]["price"]), 2) if prices and prices[0].get("price") is not None else None
        traffic_events = []
        for ev in data.get("events", []) or []:
            t = ev.get("traffic")
            if not t:
                continue
            traffic_events.append({
                "lat": ev.get("latitude"),
                "lng": ev.get("longitude"),
                "delay": t.get("delay"),
                "accessType": t.get("accessType"),
                "description": t.get("description", ""),
            })
        sr = data.get("scheduleReport") or {}
        breaks = []
        for ev in data.get("events", []) or []:
            sch = ev.get("schedule") or {}
            types = sch.get("scheduleTypes") or []
            if "BREAK" in types or "DAILY_REST" in types:
                breaks.append({
                    "type": "BREAK" if "BREAK" in types else "DAILY_REST",
                    "duration_min": round(sch.get("duration", 0) / 60, 1),
                    "at": ev.get("startsAt"),
                })
        driving_min = round(sr.get("drivingTime", 0) / 60, 1)
        break_min = round(sr.get("breakTime", 0) / 60, 1)
        rest_min = round(sr.get("restTime", 0) / 60, 1)
        return {
            "distance_km": round(data.get("distance", 0) / 1000, 1),
            "travel_time_min": round(data.get("travelTime", 0) / 60, 1),
            "traffic_delay_min": round(data.get("trafficDelay", 0) / 60, 1),
            "toll": toll,
            "currency": (prices[0].get("currency", "EUR") if prices else "EUR"),
            "polyline": data.get("polyline", ""),
            "traffic_events": traffic_events,
            "schedule": {
                "driving_min": driving_min,
                "break_min": break_min,
                "rest_min": rest_min,
                "total_min": round(driving_min + break_min + rest_min, 1),
                "end_time": sr.get("endTime"),
                "breaks": breaks,
            },
        }
    except Exception:
        return None


@app.get("/api/categorias")
def list_categorias():
    conn = _db()
    rows = conn.execute("SELECT id, nombre, cuenta FROM categorias_gasto ORDER BY nombre").fetchall()
    conn.close()
    return {"categorias": [dict(r) for r in rows]}




@app.post("/api/categorias")
def add_categoria(c: CategoriaGasto):
    conn = _db()
    conn.execute(
        "INSERT INTO categorias_gasto (nombre, cuenta) VALUES (?,?) "
        "ON CONFLICT (nombre) DO UPDATE SET cuenta=EXCLUDED.cuenta",
        (c.nombre.strip(), (c.cuenta or "").strip()),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/categorias/{cat_id}")
def del_categoria(cat_id: int):
    conn = _db()
    conn.execute("DELETE FROM categorias_gasto WHERE id=?", (cat_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------- #
# Modelos
# ---------------------------------------------------------------------- #














# ---------------------------------------------------------------------- #
# Mapeo TMS -> estructura Trimble
# ---------------------------------------------------------------------- #
def _to_trimble_ts(iso_str: str) -> str:
    """Convierte un timestamp ISO a formato Trimble (UTC, sin 'Z', con ms)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except ValueError:
        return ""
    dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _haversine_km(lat1, lng1, lat2, lng2):
    """Distancia en línea recta (km) entre dos coordenadas."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# (regex de peaje + _is_toll_step movidos a core.py)


def _calc_ruta(puntos):
    """Distancia (km) entre puntos consecutivos por carretera (OSRM) con fallback línea recta.

    Devuelve (tramos, total_km, metodo, total_toll_km) donde metodo es
    'carretera' | 'linea_recta' | 'sin_ruta' y cada tramo lleva 'km' y 'toll_km'.
    """
    if len(puntos) < 2:
        return [], 0.0, "sin_ruta", 0.0
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in puntos)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=false&steps=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TMS-Trimble/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        if data.get("code") == "Ok" and data.get("routes"):
            legs = data["routes"][0].get("legs", [])
            tramos, total, total_toll = [], 0.0, 0.0
            for i, leg in enumerate(legs):
                km = round(leg["distance"] / 1000, 1)
                toll_m = sum(s.get("distance", 0) for s in leg.get("steps", []) if _is_toll_step(s))
                toll_km = round(toll_m / 1000, 1)
                tramos.append({
                    "de": puntos[i].get("nombre") or f"Punto {i + 1}",
                    "a": puntos[i + 1].get("nombre") or f"Punto {i + 2}",
                    "km": km,
                    "toll_km": toll_km,
                })
                total += km
                total_toll += toll_km
            return tramos, round(total, 1), "carretera", round(total_toll, 1)
    except Exception:
        pass
    tramos, total = [], 0.0
    for i in range(len(puntos) - 1):
        km = round(_haversine_km(puntos[i]["lat"], puntos[i]["lng"], puntos[i + 1]["lat"], puntos[i + 1]["lng"]), 1)
        tramos.append({
            "de": puntos[i].get("nombre") or f"Punto {i + 1}",
            "a": puntos[i + 1].get("nombre") or f"Punto {i + 2}",
            "km": km,
            "toll_km": 0.0,
        })
        total += km
    return tramos, round(total, 1), "linea_recta", 0.0


def _puntos_del_viaje(viaje):
    """Puntos ordenados (origen → paradas → destino) que tienen coordenadas."""
    puntos = []
    paradas = [p for p in viaje.paradas if (p.ciudad or p.nombre or p.calle or p.lat is not None)]
    for d, default in [(viaje.origen, "Origen")] + [(p, "Parada") for p in paradas] + [(viaje.destino, "Destino")]:
        if d.lat is not None and d.lng is not None:
            puntos.append({"lat": d.lat, "lng": d.lng, "nombre": d.ciudad or d.nombre or default})
    return puntos


def _build_trip(viaje: ViajeRequest, trip_id: str, documentos: list = None) -> dict:
    def contacto(d) -> dict:
        return {
            "nombre": d.nombre, "empresa": d.empresa, "calle": d.calle,
            "numero": d.numero, "ciudad": d.ciudad, "cp": d.cp, "pais": d.pais,
            "lat": d.lat, "lng": d.lng,
        }

    tasks = []

    def add(idx, label, actividad, d, comentario=""):
        ciudad = d.ciudad or d.nombre or "?"
        descripcion = f"{actividad} - {ciudad}"
        if comentario:
            descripcion += f" — {comentario}"
        task = {
            "id": f"{trip_id}_T{idx:02d}",
            "nombre": label,
            "descripcion": descripcion,
            "actividad": actividad,
            "tipo": ACTIVITY_TYPES.get(actividad, actividad),
            "contacto": contacto(d),
        }
        inicio = _to_trimble_ts(getattr(d, "fecha_inicio", "") or "")
        fin = _to_trimble_ts(getattr(d, "fecha_fin", "") or "")
        if inicio:
            task["timewindowstart"] = inicio
        if fin:
            task["timewindowend"] = fin
        tasks.append(task)

    add(1, f"Origen: {viaje.origen.ciudad or viaje.origen.nombre or '?'}",
        viaje.origen.actividad or "CARGA", viaje.origen, viaje.origen.comentario)
    i = 2
    for p in viaje.paradas:
        # omitir paradas vacías (sin ciudad, nombre, calle ni coordenadas)
        if not (p.ciudad or p.nombre or p.calle or p.lat is not None):
            continue
        act = p.actividad if p.actividad in ACTIVITY_TYPES else "DESCARGA"
        add(i, f"Parada: {p.ciudad or p.nombre or '?'}", act, p, p.comentario)
        i += 1
    add(i, f"Destino: {viaje.destino.ciudad or viaje.destino.nombre or '?'}",
        viaje.destino.actividad or "DESCARGA", viaje.destino, viaje.destino.comentario)

    extra = []
    if viaje.conductor:
        extra.append(f"Conductor: {viaje.conductor}")
    if viaje.tipo_carga:
        extra.append(f"Carga: {viaje.tipo_carga}")
    if viaje.cliente:
        extra.append(f"Cliente: {viaje.cliente}")

    # adjuntar documentos a la primera tarea (origen)
    if documentos and tasks:
        tasks[0]["documentos"] = documentos

    # navegación: propiedades de ruta (routing.*) en la primera tarea (origen de la ruta)
    if tasks:
        tasks[0]["routing"] = True
        # via points (puntos de paso de navegación, manual §5.3.6) en la primera tarea
        if viaje.waypoints:
            tasks[0]["waypoints"] = [
                {"nombre": w.nombre, "lat": w.lat, "lng": w.lng, "distance": w.distance}
                for w in viaje.waypoints
                if w.lat is not None and w.lng is not None
            ]

    # eCMR: adjuntar a la tarea de destino (última tarea = entrega)
    if viaje.ecmr_provider and viaje.ecmr_id and tasks:
        tasks[-1]["ecmr_provider"] = viaje.ecmr_provider
        tasks[-1]["ecmr_id"] = viaje.ecmr_id

    propiedades = {
        "conductor": viaje.conductor,
        "cargo.tipo": viaje.tipo_carga,
    }
    if viaje.cliente:
        propiedades["cliente"] = viaje.cliente
    if viaje.precio:
        propiedades["precio"] = f"{viaje.precio:.2f}"

    return {
        "id": trip_id,
        "nombre": f"Viaje {viaje.origen.ciudad or '?'} -> {viaje.destino.ciudad or '?'}",
        "descripcion": " | ".join(extra) or "Viaje TMS",
        "propiedades": propiedades,
        "tasks": tasks,
    }


# ---------------------------------------------------------------------- #
# Endpoints
# ---------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "terminal": _get_config("trimble_terminal", config.DEFAULT_TRIMBLE_TERMINAL),
        "customer": _get_config("trimble_customer", config.DEFAULT_TRIMBLE_CUSTOMER),
    }


def _es_superadmin():
    t = _tenant_ctx.get()
    return bool(t and t.get("superadmin"))


@app.get("/api/empresas")
def list_empresas():
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    conn = _db_master()
    try:
        rows = conn.execute("SELECT slug, nombre, db_name, creado FROM empresas ORDER BY slug").fetchall()
        return {"empresas": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/empresas")
def create_empresa(req: dict):
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    slug = (req.get("slug") or "").strip().lower()
    nombre = (req.get("nombre") or "").strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail={"error": "Identificador no válido (2-32 chars: minúsculas, números, guiones)"})
    if slug in ("admin", "_admin", "master", "postgres", "tms"):
        raise HTTPException(status_code=400, detail={"error": "Identificador reservado"})
    if not nombre:
        raise HTTPException(status_code=400, detail={"error": "Indica el nombre de la empresa"})
    auth_users = (req.get("auth_users") or "").strip()
    auth_password = req.get("auth_password") or req.get("contraseña") or ""
    seed = {"auth_users": auth_users, "auth_password": auth_password}
    db_name, err = _provision_tenant(slug, nombre, seed)
    if err:
        raise HTTPException(status_code=500, detail={"error": f"No se pudo crear la base de datos: {err}"})
    return {"ok": True, "slug": slug, "nombre": nombre, "db_name": db_name}


@app.delete("/api/empresas/{slug}")
def delete_empresa(slug: str):
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    conn = _db_master()
    db_name = None
    try:
        row = conn.execute("SELECT slug, db_name FROM empresas WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "Empresa no encontrada"})
        db_name = row["db_name"]
        if db_name == config.DB_NAME:
            raise HTTPException(status_code=400, detail={"error": "No se puede borrar la empresa principal"})
        conn.execute("DELETE FROM empresas WHERE slug=?", (slug,))
        conn.commit()
    finally:
        conn.close()
    # soltar la base de datos (fuera de transacción)
    if db_name:
        try:
            c = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                                 user=config.DB_USER, password=config.DB_PASSWORD)
            c.autocommit = True
            cur = c.cursor()
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            c.close()
        except Exception as e:
            return {"ok": True, "slug": slug, "aviso": f"Registro eliminado pero no se pudo soltar la BD: {e}"}
    return {"ok": True, "slug": slug, "db_name": db_name}


@app.post("/api/empresas/{slug}/entrar")
def entrar_empresa(slug: str):
    """Superadmin entra a una empresa: genera un token de tenant sin pedir contraseña."""
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    emp = _empresa_por_slug(slug)
    if not emp:
        raise HTTPException(status_code=404, detail={"error": "Empresa no encontrada"})
    return {"token": _make_jwt(0, "admin", "admin", emp["slug"]),
            "empresa": emp["slug"], "nombre": emp["nombre"],
            "usuario": "admin", "superadmin": False}


@app.get("/api/terminals")
def terminals():
    """Lista de terminales (unidades) habilitados del cliente."""
    units = get_client().list_units()
    enabled = sorted(
        (u for u in units if u.get("enabled") and u.get("id")),
        key=lambda u: u["id"],
    )
    return {"terminales": [{"id": u["id"], "name": u["name"]} for u in enabled]}


@app.get("/api/drivers")
def drivers():
    """Lista de conductores del cliente."""
    ds = get_client().list_drivers()
    result = []
    for d in ds:
        nombre = f"{d['firstName']} {d['lastName']}".strip()
        if not nombre:
            nombre = d.get("id") or "(sin nombre)"
        result.append({"id": d.get("id") or "", "nombre": nombre})
    result.sort(key=lambda d: d["nombre"].lower())
    return {"conductores": result}


@app.get("/api/activity-types")
def activity_types():
    return {"actividades": ACTIVITY_TYPES}


@app.get("/api/geocode")
def geocode(q: str = "", limit: int = 5):
    """Búsqueda de lugares vía Nominatim (OpenStreetMap). Gratis, sin API key."""
    if not q.strip():
        return {"resultados": []}
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({
            "q": q,
            "format": "json",
            "addressdetails": 1,
            "limit": min(max(limit, 1), 10),
            "accept-language": "es",
        })
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "tms-trimble/0.1 (contacto: uxevilla@gmail.com)",
        "Accept-Language": "es",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"resultados": [], "error": "No se pudo consultar el geocodificador."}

    resultados = []
    for item in data:
        a = item.get("address", {}) or {}
        resultados.append({
            "display_name": item.get("display_name", ""),
            "lat": float(item.get("lat", 0) or 0),
            "lng": float(item.get("lon", 0) or 0),
            "nombre": a.get("name") or a.get("amenity") or a.get("shop")
                      or a.get("tourism") or a.get("building") or "",
            "calle": a.get("road") or a.get("pedestrian") or "",
            "numero": a.get("house_number", ""),
            "ciudad": a.get("city") or a.get("town") or a.get("village")
                      or a.get("municipality") or a.get("county") or "",
            "cp": a.get("postcode", ""),
            "pais": (a.get("country_code") or "").upper(),
        })
    return {"resultados": resultados}


@app.get("/api/reverse-geocode")
def reverse_geocode(lat: float = 0, lng: float = 0):
    """Geocodificación inversa vía Nominatim: lat/lng -> dirección."""
    if not lat or not lng:
        return {"resultado": None}
    url = (
        "https://nominatim.openstreetmap.org/reverse?"
        + urllib.parse.urlencode({
            "lat": lat, "lon": lng, "format": "json", "addressdetails": 1,
            "accept-language": "es", "zoom": 18,
        })
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "tms-trimble/0.1 (contacto: uxevilla@gmail.com)",
        "Accept-Language": "es",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"resultado": None, "error": "No se pudo consultar el geocodificador inverso."}
    a = data.get("address", {}) or {}
    return {"resultado": {
        "display_name": data.get("display_name", ""),
        "lat": lat, "lng": lng,
        "nombre": a.get("name") or a.get("amenity") or a.get("shop") or a.get("tourism") or a.get("building") or "",
        "calle": a.get("road") or a.get("pedestrian") or "",
        "numero": a.get("house_number", ""),
        "ciudad": a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county") or "",
        "cp": a.get("postcode", ""),
        "pais": (a.get("country_code") or "").upper(),
    }}


@app.get("/api/trips")
def list_trips():
    conn = _db()
    rows = conn.execute(
        "SELECT * FROM trips ORDER BY creado DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return {"viajes": [dict(r) for r in rows]}


@app.patch("/api/trips/{trip_id}")
def update_trip(trip_id: str, upd: TripUpdate):
    """Actualiza campos de un viaje (contables + planificación)."""
    conn = _db()
    row = conn.execute("SELECT estado FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if (row["estado"] or "").lower() in _ESTADOS_FINALES:
        # en viajes finalizados solo se permite el cambio de estado de pago (cobro)
        otros = [f for f in ("factura", "cliente", "tipo_carga", "conductor", "terminal",
                             "semirremolque_id", "remolque_id", "precio", "gastos", "iva", "origen", "destino")
                 if getattr(upd, f) is not None]
        if otros:
            conn.close()
            raise HTTPException(status_code=409, detail={"error": "El viaje está finalizado y no se puede modificar."})
    sets, params = [], []
    for field in ("factura", "estado_pago", "cliente", "tipo_carga", "conductor", "terminal", "semirremolque_id", "remolque_id", "fecha_esperada_carga", "fecha_esperada_descarga", "origen", "destino"):
        val = getattr(upd, field)
        if val is not None:
            sets.append(f"{field}=?")
            params.append(val)
    for field in ("precio", "gastos", "iva"):
        val = getattr(upd, field)
        if val is not None:
            sets.append(f"{field}=?")
            params.append(float(val))
    if sets:
        params.append(trip_id)
        conn.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    conn.close()
    return {"ok": True, "trip_id": trip_id}


# ---- helpers de planificación (pedido → asignar) ----

def _calcular_ruta(viaje, terminal, conduccion_acumulada_min):
    km_total = 0.0
    peaje_km = 0.0
    peaje_estimado = 0.0
    peaje_fuente = "estimado"
    tiempo_min = 0.0
    trafico_min = 0.0
    pausas_min = 0.0
    puntos = _puntos_del_viaje(viaje)
    if len(puntos) >= 2:
        _, km_total, _, peaje_km = _calc_ruta(puntos)
        peaje_estimado = round(peaje_km * _peaje_rate(_vehiculo_peaje_categoria(terminal)), 2)
        ptv = _ptv_route(puntos, _vehiculo_ptv(terminal), conduccion_acumulada_min)
        if ptv:
            sch = ptv.get("schedule") or {}
            tiempo_min = sch.get("total_min") or ptv.get("travel_time_min") or 0.0
            pausas_min = (sch.get("break_min") or 0.0) + (sch.get("rest_min") or 0.0)
            trafico_min = ptv.get("traffic_delay_min") or 0.0
            if ptv.get("toll") is not None:
                peaje_estimado = float(ptv["toll"])
                peaje_fuente = "ptv"
    return km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min


# (_calcular_importes movido a core.py)


def _viaje_payload(viaje):
    try:
        return viaje.model_dump(exclude={"documentos"})
    except AttributeError:
        return json.loads(viaje.json(exclude={"documentos"}))




@app.post("/api/trips")
def create_trip(viaje: ViajeRequest):
    trip_id = "VIAJE-" + uuid.uuid4().hex[:10].upper()
    terminal = (viaje.terminal or "").strip()
    semirremolque = (viaje.semirremolque_id or "").strip()
    remolque = (viaje.remolque_id or "").strip()

    # Sin camión asignado → pedido (se planifica, aún no se envía a Trimble)
    if not terminal:
        return _crear_pedido(trip_id, viaje)

    return _enviar_viaje(trip_id, viaje, terminal, semirremolque, remolque)


@app.get("/api/tarifas")
def list_tarifas():
    conn = _db()
    rows = conn.execute(
        "SELECT t.id, t.nombre, t.tipo, t.precio, t.cliente_id, t.activo, t.creado_en, "
        "COALESCE(c.nombre, '') AS cliente_nombre "
        "FROM tarifas t LEFT JOIN clientes c ON c.id = t.cliente_id ORDER BY t.id"
    ).fetchall()
    conn.close()
    return {"tarifas": [dict(r) for r in rows]}


@app.post("/api/tarifas")
def create_tarifa(t: TarifaRequest):
    conn = _db()
    conn.execute(
        "INSERT INTO tarifas (nombre, tipo, precio, cliente_id, activo, creado_en) VALUES (?,?,?,?,?,?)",
        (t.nombre.strip(), t.tipo.strip().lower(), float(t.precio or 0), t.cliente_id, t.activo,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/tarifas/{tarifa_id}")
def update_tarifa(tarifa_id: int, t: TarifaRequest):
    conn = _db()
    conn.execute(
        "UPDATE tarifas SET nombre=?, tipo=?, precio=?, cliente_id=?, activo=? WHERE id=?",
        (t.nombre.strip(), t.tipo.strip().lower(), float(t.precio or 0), t.cliente_id, t.activo, tarifa_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/tarifas/{tarifa_id}")
def delete_tarifa(tarifa_id: int):
    conn = _db()
    conn.execute("DELETE FROM tarifas WHERE id=?", (tarifa_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str, user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Elimina un viaje. Los viajes finalizados solo puede eliminarlos un administrador."""
    conn = _db()
    row = conn.execute("SELECT estado FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    estado = (row["estado"] or "").lower()
    if estado in _ESTADOS_FINALES and user.get("rol") != "admin":
        conn.close()
        raise HTTPException(status_code=403, detail={"error": "Solo un administrador puede eliminar un viaje finalizado."})
    # Limpiar tablas hijas sin ON DELETE CASCADE.
    conn.execute("DELETE FROM files WHERE trip_id=?", (trip_id,))
    conn.execute("DELETE FROM mensajes WHERE trip_id=?", (trip_id,))
    conn.execute("DELETE FROM gastos WHERE trip_id=?", (trip_id,))
    conn.execute("UPDATE telemetria.posiciones_gps SET viaje_id=NULL WHERE viaje_id=?", (trip_id,))
    # paradas y tramos se borran por ON DELETE CASCADE.
    conn.execute("DELETE FROM trips WHERE id=?", (trip_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "trip_id": trip_id}


def _guardar_documentos_pedido(conn, trip_id, documentos):
    """Guarda los PDF del pedido en `files` (source='pedido'); se suben al DMS al asignar."""
    for d in documentos:
        contenido = (d.contenido or "").strip()
        if not contenido:
            continue
        nombre = ((d.nombre or "documento.pdf").rsplit("/", 1)[-1])[:120] or "documento.pdf"
        name = f"{uuid.uuid4().hex[:10]}__{nombre}"
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, content_b64) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (trip_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf", contenido),
        )


def _valorar_viaje(viaje, km_total):
    """Aplica la tarifa (km/viaje/kilos) al viaje. Devuelve (precio, precio_unitario).

    Si el viaje tiene tarifa_id, calcula el precio desde la tarifa; si no, usa el
    precio manual. `precio_unitario` es None si no hay tarifa.
    """
    modo = (viaje.modo_tarifa or "viaje").strip().lower()
    if viaje.tarifa_id:
        conn = _db()
        try:
            row = conn.execute("SELECT tipo, precio FROM tarifas WHERE id=?", (viaje.tarifa_id,)).fetchone()
        finally:
            conn.close()
        if row:
            unit = float(row["precio"] or 0)
            if modo == "km":
                return round(km_total * unit, 2), unit
            if modo == "kilos":
                return round(float(viaje.kilos or 0) * unit, 2), unit
            return round(unit, 2), unit
    return round(float(viaje.precio or 0), 2), None


def _crear_pedido(trip_id, viaje):
    km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min = \
        _calcular_ruta(viaje, "", 0.0)
    viaje.precio, precio_unitario = _valorar_viaje(viaje, km_total)
    precio, gastos, margen, iva_pct, base, cuota_iva = _calcular_importes(viaje)
    nombre = f"Viaje {viaje.origen.ciudad or viaje.origen.nombre or '?'} -> {viaje.destino.ciudad or viaje.destino.nombre or '?'}"
    n_tareas = 2 + len([p for p in viaje.paradas if (p.ciudad or p.nombre or p.calle or p.lat is not None)])
    _save_trip(
        trip_id, nombre, "", viaje.conductor, viaje.tipo_carga,
        viaje.origen.ciudad or viaje.origen.nombre,
        viaje.destino.ciudad or viaje.destino.nombre,
        n_tareas, "sin_asignar", None, "",
        cliente=viaje.cliente or "",
        precio=precio, km_total=km_total,
        peaje_km=peaje_km, peaje_estimado=peaje_estimado, peaje_fuente=peaje_fuente,
        tiempo_min=tiempo_min, trafico_min=trafico_min, pausas_min=pausas_min,
        gastos=gastos, factura=viaje.factura or "",
        estado_pago=viaje.estado_pago or "pendiente", iva=iva_pct,
        cliente_id=viaje.cliente_id, conductor_id=viaje.conductor_id,
        fecha_esperada_carga=viaje.fecha_esperada_carga,
        fecha_esperada_descarga=viaje.fecha_esperada_descarga,
        modo_tarifa=viaje.modo_tarifa, tarifa_id=viaje.tarifa_id,
        precio_unitario=precio_unitario, kilos=viaje.kilos,
        subcontratado=viaje.subcontratado, proveedor_id=viaje.proveedor_id, coste=viaje.coste,
    )
    _save_paradas(trip_id, viaje)
    conn = _db()
    origen_id = _upsert_direccion(conn, viaje.origen)
    destino_id = _upsert_direccion(conn, viaje.destino)
    _guardar_documentos_pedido(conn, trip_id, viaje.documentos)
    _save_tramos(conn, trip_id, viaje.tramos)
    conn.execute("UPDATE trips SET payload=?, origen_id=?, destino_id=? WHERE id=?",
                 (json.dumps(_viaje_payload(viaje), ensure_ascii=False), origen_id, destino_id, trip_id))
    conn.commit()
    conn.close()
    return {
        "ok": True, "trip_id": trip_id, "nombre": nombre, "estado": "sin_asignar",
        "km_total": km_total, "peaje_km": peaje_km, "peaje_estimado": peaje_estimado,
        "peaje_fuente": peaje_fuente, "tiempo_min": tiempo_min, "trafico_min": trafico_min,
        "pausas_min": pausas_min, "precio": precio, "gastos": gastos, "margen": margen,
    }


@app.put("/api/trips/{trip_id}")
def update_pedido(trip_id: str, viaje: ViajeRequest):
    """Actualiza un pedido sin asignar (direcciones, paradas y datos). Recalcula km/peaje."""
    conn = _db()
    row = conn.execute("SELECT estado FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if (row["estado"] or "") not in ("sin_asignar", ""):
        raise HTTPException(status_code=409, detail={"error": "Solo se puede editar un pedido sin asignar."})
    return _crear_pedido(trip_id, viaje)


@app.post("/api/trips/{trip_id}/documentos")
def add_trip_documentos(trip_id: str, req: dict):
    """Guarda los PDF del pedido (se suben al DMS al asignar el viaje)."""
    docs = req.get("documentos") or []
    if not docs:
        return {"ok": True, "guardados": 0}
    conn = _db()
    if not conn.execute("SELECT id FROM trips WHERE id=?", (trip_id,)).fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    guardados = 0
    for d in docs:
        contenido = d.get("contenido") or ""
        if not contenido:
            continue
        nombre = (d.get("nombre") or "documento.pdf").rsplit("/", 1)[-1][:120] or "documento.pdf"
        name = f"{uuid.uuid4().hex[:10]}__{nombre}"
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, content_b64) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (trip_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf", contenido),
        )
        guardados += 1
    conn.commit()
    conn.close()
    return {"ok": True, "guardados": guardados}


@app.get("/api/trips/{trip_id}/documentos")
def list_trip_documentos(trip_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, content_b64, source, formato FROM files WHERE trip_id=? ORDER BY id", (trip_id,)
    ).fetchall()
    conn.close()
    docs = []
    for r in rows:
        c = r["content_b64"] or ""
        name = r["name"]
        nombre = name.split("__", 1)[1] if "__" in name else name
        docs.append({"id": r["id"], "nombre": nombre, "contenido": c, "size": round(len(c) * 3 / 4), "source": r["source"] or "", "formato": r["formato"] or ""})
    return {"documentos": docs}


@app.delete("/api/trips/{trip_id}/documentos/{file_id}")
def del_trip_documento(trip_id: str, file_id: int):
    conn = _db()
    conn.execute("DELETE FROM files WHERE id=? AND trip_id=? AND source='pedido'", (file_id, trip_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/trips/{trip_id}/duplicar")
def duplicar_trip(trip_id: str):
    """Duplica un viaje: copia los datos pero lo crea SIN asignar (sin vehículo ni conductor)."""
    conn = _db()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    try:
        viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    except Exception:
        # Viajes sincronizados de Trimble sin payload estructurado → reconstruir desde columnas
        viaje = ViajeRequest(
            origen=Direccion(ciudad=row["origen"] or "", nombre=row["origen"] or ""),
            destino=Direccion(ciudad=row["destino"] or "", nombre=row["destino"] or ""),
            tipo_carga=row["tipo_carga"] or "General",
            cliente=row["cliente"] or "",
            cliente_id=row["cliente_id"],
            precio=float(row["precio"] or 0),
            iva=float(row["iva"] or 21),
        )
    # Forzar sin asignar (sin vehículo ni conductor)
    viaje.terminal = ""
    viaje.conductor = ""
    viaje.conductor_id = None
    viaje.semirremolque_id = ""
    viaje.remolque_id = ""
    viaje.factura = ""
    viaje.estado_pago = "pendiente"
    viaje.documentos = []
    new_id = "VIAJE-" + uuid.uuid4().hex[:10].upper()
    _crear_pedido(new_id, viaje)
    # Copiar los documentos PDF del pedido original
    conn = _db()
    docs = conn.execute(
        "SELECT name, content_b64 FROM files WHERE trip_id=? AND source='pedido'", (trip_id,)
    ).fetchall()
    for d in docs:
        base = d["name"].split("__", 1)[1] if "__" in d["name"] else d["name"]
        name = f"{uuid.uuid4().hex[:10]}__{base}"
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, content_b64) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (new_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf", d["content_b64"]),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "trip_id": new_id}


@app.post("/api/trips/{trip_id}/asignar")
def asignar_trip(trip_id: str, req: AsignarRequest):
    conn = _db()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if (row["estado"] or "") not in ("sin_asignar", ""):
        raise HTTPException(status_code=409, detail={"error": f"El viaje ya está asignado (estado: {row['estado']})"})

    viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    terminal = (req.terminal or "").strip()
    if not terminal:
        raise HTTPException(status_code=400, detail={"error": "Indica la tractora (terminal) para asignar."})

    # Fusionar ediciones en línea (columnas de trips) sobre el payload original
    viaje.cliente = row["cliente"] or viaje.cliente
    viaje.tipo_carga = row["tipo_carga"] or viaje.tipo_carga
    viaje.precio = float(row["precio"] or 0)
    viaje.gastos = float(row["gastos"] or 0)
    viaje.iva = float(row["iva"] or 0)
    viaje.factura = row["factura"] or viaje.factura
    viaje.conductor = req.conductor or row["conductor"] or viaje.conductor or ""
    viaje.conductor_id = req.conductor_id if req.conductor else row["conductor_id"]

    viaje.terminal = terminal
    viaje.semirremolque_id = (req.semirremolque_id or row["semirremolque_id"] or "").strip()
    viaje.remolque_id = (req.remolque_id or row["remolque_id"] or "").strip()
    viaje.conduccion_acumulada_min = req.conduccion_acumulada_min
    viaje.ecmr_provider = req.ecmr_provider
    viaje.ecmr_id = req.ecmr_id
    viaje.documentos = req.documentos

    # Safe-Dispatching: valida la conducción legal antes de despachar (saltable con force=true).
    if not req.force:
        _chequear_conduccion_legal(viaje, row)

    return _enviar_viaje(trip_id, viaje, terminal, viaje.semirremolque_id, viaje.remolque_id)


@app.post("/api/trips/{trip_id}/enviar")
def enviar_trip(trip_id: str, force: bool = False):
    """Envía (o reenvía) el viaje al terminal Trimble asignado.

    `force=true` (query) salta el chequeo de Safe-Dispatching.
    """
    conn = _db()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    terminal = (row["terminal"] or "").strip()
    if not terminal:
        raise HTTPException(status_code=400, detail={"error": "Asigna la tractora (terminal) antes de enviar el viaje a Trimble."})
    viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    # Fusionar el estado actual de la fila sobre el payload original.
    viaje.cliente = row["cliente"] or viaje.cliente
    viaje.precio = float(row["precio"] or 0)
    viaje.gastos = float(row["gastos"] or 0)
    viaje.iva = float(row["iva"] or 0)
    viaje.conductor = row["conductor"] or viaje.conductor or ""
    viaje.terminal = terminal
    viaje.semirremolque_id = (row["semirremolque_id"] or "").strip()
    viaje.remolque_id = (row["remolque_id"] or "").strip()

    # Safe-Dispatching: valida la conducción legal antes de despachar (saltable con force=true).
    if not force:
        _chequear_conduccion_legal(viaje, row)

    return _enviar_viaje(trip_id, viaje, terminal, viaje.semirremolque_id, viaje.remolque_id)


def _enviar_viaje(trip_id, viaje, terminal, semirremolque, remolque):
    # Resolver el terminal APP (Fleet XPS) para el despacho SOAP; el T4U (`terminal`) queda
    # para telemetría/PTV/posición. Sin APP vinculada cae al terminal APP por defecto.
    conn = _db()
    soap_terminal = _terminal_app(conn, terminal)
    conn.close()

    # Bloqueo: un semirremolque/remolque con viaje activo no puede asignarse de nuevo.
    # El terminal (tractora) SÍ admite varios viajes en cola: se ejecutan uno tras otro,
    # por eso NO se bloquea el terminal (solo los remolques).
    # (se excluye el propio viaje para que su remolque/semirremolque no se bloquee a sí mismo)
    activos = _vehiculos_en_curso(exclude_trip_id=trip_id)
    asignados = [v for v in (semirremolque, remolque) if v]
    bloqueados = [v for v in asignados if v in activos]
    if bloqueados:
        raise HTTPException(
            status_code=409,
            detail={"error": f"Remolque(s) ya asignados a un viaje en curso: {', '.join(bloqueados)}"},
        )

    # 1) subir documentos al DMS de Trimble (límite ~3 MB base64 por PDF)
    documentos = []
    for doc in viaje.documentos:
        if len(doc.contenido) > MAX_DOC_B64:
            mb = round(len(doc.contenido) * 3 / 4 / 1024 / 1024, 1)
            raise HTTPException(
                status_code=413,
                detail={"error": f"El documento «{doc.nombre}» pesa demasiado "
                                 f"(~{mb} MB). Trimble admite hasta ~{MAX_DOC_MB} MB por PDF. "
                                 "Comprímelo o redúcelo."},
            )
        res = get_client().create_document(doc.nombre, doc.contenido, "pdf", True)
        if res.get("ok") and res.get("uniqueDocId"):
            documentos.append({"fileKey": res["uniqueDocId"], "fileName": doc.nombre})
        else:
            raise HTTPException(
                status_code=502,
                detail={
                    "trip_id": trip_id,
                    "error": f"No se pudo subir el documento '{doc.nombre}': "
                             f"{res.get('error') or res.get('status') or 'error desconocido'}",
                },
            )

    # Asignar los documentos al terminal para que el dispositivo pueda descargarlos
    if documentos:
        try:
            get_client().assign_documents([d["fileKey"] for d in documentos], [soap_terminal])
        except Exception as e:
            # No bloquea el envío: el adjunto a la tarea (activity.file.N.fileKey) sigue vigente
            print(f"[DMS] assign_documents falló (no bloquea): {e}")

    trip = _build_trip(viaje, trip_id, documentos)
    nombre = trip["nombre"]
    n_tareas = len(trip["tasks"])

    pasos = get_client().send_trip(trip, soap_terminal)

    errores = [p["error"] for p in pasos if not p["ok"]]
    estado = "enviado" if pasos and all(p["ok"] for p in pasos) else "error"
    error = "; ".join(e for e in errores if e) or None
    # Sellar el puente: marcar el viaje activo en Redis para el worker de telemetría.
    if estado == "enviado":
        _set_viaje_activo(terminal, trip_id)

    km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min = \
        _calcular_ruta(viaje, terminal, viaje.conduccion_acumulada_min)
    viaje.precio, precio_unitario = _valorar_viaje(viaje, km_total)
    precio, gastos, margen, iva_pct, base, cuota_iva = _calcular_importes(viaje)

    # km en vacío: distancia desde la última posición conocida del vehículo hasta el origen
    km_vacio = 0.0
    pos = _vehiculo_posicion(terminal)
    if pos and viaje.origen.lat is not None and viaje.origen.lng is not None:
        km_vacio = _haversine_km(pos[0], pos[1], viaje.origen.lat, viaje.origen.lng) or 0.0

    _save_trip(
        trip_id, nombre, terminal, viaje.conductor, viaje.tipo_carga,
        viaje.origen.ciudad or viaje.origen.nombre,
        viaje.destino.ciudad or viaje.destino.nombre,
        n_tareas, estado, error, terminal,
        semirremolque_id=semirremolque,
        remolque_id=remolque,
        cliente=viaje.cliente or "",
        precio=precio,
        km_total=km_total,
        peaje_km=peaje_km,
        peaje_estimado=peaje_estimado,
        peaje_fuente=peaje_fuente,
        tiempo_min=tiempo_min,
        trafico_min=trafico_min,
        pausas_min=pausas_min,
        gastos=gastos,
        factura=viaje.factura or "",
        estado_pago=viaje.estado_pago or "pendiente",
        iva=iva_pct,
        cliente_id=viaje.cliente_id,
        conductor_id=viaje.conductor_id,
        fecha_esperada_carga=viaje.fecha_esperada_carga,
        fecha_esperada_descarga=viaje.fecha_esperada_descarga,
        modo_tarifa=viaje.modo_tarifa, tarifa_id=viaje.tarifa_id,
        precio_unitario=precio_unitario, kilos=viaje.kilos,
        subcontratado=viaje.subcontratado, proveedor_id=viaje.proveedor_id, coste=viaje.coste,
    )
    _save_paradas(trip_id, viaje)
    conn = _db()
    conn.execute("UPDATE trips SET km_vacio=? WHERE id=?", (km_vacio, trip_id))
    _save_tramos(conn, trip_id, viaje.tramos)
    conn.commit()
    conn.close()

    if estado == "error":
        raise HTTPException(
            status_code=502,
            detail={"trip_id": trip_id, "pasos": pasos, "error": error},
        )

    return {
        "ok": True,
        "trip_id": trip_id,
        "nombre": nombre,
        "terminal": terminal,
        "tareas": n_tareas,
        "km_total": km_total,
        "km_vacio": km_vacio,
        "peaje_km": peaje_km,
        "peaje_estimado": peaje_estimado,
        "peaje_fuente": peaje_fuente,
        "tiempo_min": tiempo_min,
        "trafico_min": trafico_min,
        "pausas_min": pausas_min,
        "precio": precio,
        "gastos": gastos,
        "margen": margen,
        "base": base,
        "iva": cuota_iva,
        "factura": viaje.factura or "",
        "estado_pago": viaje.estado_pago or "pendiente",
        "pasos": pasos,
    }


# ---------------------------------------------------------------------- #
# Sincronización de estados (en vivo) y archivos del conductor
# ---------------------------------------------------------------------- #
def _query_terminal_states(terminal=None) -> dict:
    r = get_client().query_terminal(terminal)
    states = {}
    if r.get("ok"):
        for m in re.finditer(r"<return>(.*?)</return>", r["body"], re.S):
            block = m.group(1)
            tid = re.search(r"<id>([^<]*)</id>", block)
            st = re.search(r"<lastKnownState>([^<]*)</lastKnownState>", block)
            if tid:
                states[tid.group(1)] = st.group(1) if st else "new"
    return states


def _sync_status():
    conn = _db()
    terminals = [r["terminal"] for r in conn.execute(
        "SELECT DISTINCT terminal FROM trips WHERE terminal IS NOT NULL AND terminal != ''"
    ).fetchall()]
    conn.close()
    terminals = list(set(terminals + [_get_config("trimble_terminal", config.DEFAULT_TRIMBLE_TERMINAL)]))
    states = {}
    for t in terminals:
        states.update(_query_terminal_states(t))
    conn = _db()
    rows = conn.execute(
        f"SELECT id, terminal, tareas, tareas_estado FROM trips "
        f"WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL} "
        f"AND COALESCE(estado,'') NOT IN ('sin_asignar', 'pedido') "
        f"AND terminal IS NOT NULL AND terminal != ''"
    ).fetchall()
    for row in rows:
        trip_id = row["id"]
        n = row["tareas"] or 0
        prev = json.loads(row["tareas_estado"] or "{}")
        task_states = {}
        for i in range(1, n + 1):
            tid = f"{trip_id}_T{i:02d}"
            if tid in states:
                task_states[tid] = states[tid]
            else:
                p = prev.get(tid)
                task_states[tid] = "finalizado" if (p and p != "finalizado") else (p or None)
        vals = [s for s in task_states.values() if s]
        if vals and all(s == "finalizado" for s in vals):
            estado = "finalizado"
        elif any(s == "busy" for s in vals):
            estado = "en curso"
        elif any(s == "accepted" for s in vals):
            estado = "aceptado"
        elif any(s == "received" for s in vals):
            estado = "recibido"
        else:
            estado = "enviado"
        conn.execute(
            "UPDATE trips SET estado=?, tareas_estado=? WHERE id=?",
            (estado, json.dumps(task_states), trip_id),
        )
        # Sellar el puente: liberar el viaje activo (DEL) al finalizar.
        if estado in _ESTADOS_FINALES and row["terminal"]:
            _del_viaje_activo(row["terminal"])
        # Al finalizar: la última posición conocida del vehículo pasa a ser el destino del viaje
        if estado == "finalizado" and row["terminal"]:
            dest = conn.execute(
                "SELECT lat, lng FROM paradas WHERE trip_id=? ORDER BY orden DESC LIMIT 1", (trip_id,)
            ).fetchone()
            if dest and dest["lat"] is not None and dest["lng"] is not None:
                conn.execute(
                    "UPDATE vehiculos SET last_lat=?, last_lng=? WHERE id=?",
                    (dest["lat"], dest["lng"], row["terminal"]),
                )
    conn.commit()
    conn.close()


def _get_sync_state(key):
    conn = _db()
    row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def _set_sync_state(key, value):
    conn = _db()
    conn.execute("INSERT INTO sync_state (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))
    conn.commit()
    conn.close()


def _parse_props(block):
    props = {}
    for m in re.finditer(
        r"<property>\s*<key>([^<]*)</key>\s*<value>([^<]*)</value>\s*</property>",
        block, re.S,
    ):
        props[m.group(1)] = m.group(2)
    return props


def _save_file(trip_id, name, ftype, ftime, source, driver, lid, content_b64):
    conn = _db()
    conn.execute(
        "INSERT INTO files (trip_id, name, ftype, ftime, source, driver, lid, content_b64) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
        (trip_id, name, ftype, ftime, source, driver, lid, content_b64),
    )
    conn.commit()
    conn.close()


def _extraer_reporte_xml(block):
    """Extrae el reporte XML (ARE o AFRE) de una traza de actividad (type 12 o 13).

    Devuelve (tipo_reporte, xml): tipo_reporte ∈ {'AFRE','ARE',''} y el XML sin envoltorio.
    AFRE = informe final (obligatorio); ARE = informe intermedio (opcional). El XML puede venir
    en CDATA o escapado (&lt;Report …). Devuelve ('', '') si no lo encuentra.
    """
    import html
    for key in ("AFRE", "ARE"):
        m = re.search(rf"<property>\s*<key>{key}</key>\s*<value>(.*?)</value>\s*</property>", block, re.S)
        if not m:
            continue
        val = m.group(1).strip()
        cdata = re.search(r"<!\[CDATA\[(.*?)\]\]>", val, re.S)
        if cdata:
            return key, cdata.group(1).strip()
        rep = re.search(r"(<Report\b.*?</Report>)", val, re.S)
        if rep:
            return key, rep.group(1)
        rep = re.search(r"(&lt;Report\b.*?&lt;/Report&gt;)", val, re.S)
        if rep:
            return key, html.unescape(rep.group(1))
        return key, val
    return "", ""


def _extraer_documento_ecmr(qp):
    """Extrae el documento/firma base64 del reporte AFRE (e-CMR).

    Busca en el XML del reporte: data URIs (imagen/pdf), nodos de firma/adjunto
    con contenido base64 y atributos base64. Devuelve (nombre, b64, formato) o None.
    AJUSTAR a la estructura real del CDATA de Trimble si es necesario.
    """
    if not qp:
        return None
    # 1) data URI embebida (imagen o pdf)
    m = re.search(r"data:(image/[a-z+]+|application/pdf);base64,([A-Za-z0-9+/=]+)", qp)
    if m:
        fmt = "png" if "png" in m.group(1) else ("jpg" if "jpeg" in m.group(1) else "pdf")
        return "firma_ecmr", m.group(2), fmt
    # 2) nodos con base64 en el cuerpo (firma/imagen/adjunto)
    for tag in ("Signature", "Image", "Photo", "Attachment", "File", "Document",
                "Firma", "Imagen", "Adjunto"):
        m = re.search(rf"<{tag}[^>]*>\s*([A-Za-z0-9+/=]{{20,}})\s*</{tag}>", qp, re.S)
        if m:
            return f"ecmr_{tag.lower()}", m.group(1).strip(), "png"
    # 3) atributo src/value/content con data URI
    m = re.search(r'(?:src|value|content)\s*=\s*["\']data:(image/[a-z+]+|application/pdf);base64,([A-Za-z0-9+/=]+)["\']', qp)
    if m:
        fmt = "png" if "png" in m.group(1) else ("jpg" if "jpeg" in m.group(1) else "pdf")
        return "firma_ecmr", m.group(2), fmt
    # 4) bloque CDATA con base64 puro (firma escaneada)
    m = re.search(r"<!\[CDATA\[\s*([A-Za-z0-9+/=]{100,})\s*\]\]>", qp)
    if m:
        return "firma_ecmr", m.group(1).strip(), "png"
    return None


def _guardar_documento_entrega(trip_id, nombre, contenido_b64, formato="png", ftime=None):
    """Guarda el documento del e-CMR (firma/escaneo base64) en la tabla files."""
    if not trip_id or not contenido_b64:
        return
    name = f"ecmr_{uuid.uuid4().hex[:8]}_{nombre}"
    conn = _db()
    conn.execute(
        "INSERT INTO files (trip_id, name, ftype, ftime, source, content_b64, formato) "
        "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
        (trip_id, name, 3, ftime or (datetime.datetime.utcnow().isoformat() + "Z"),
         "ecmr", contenido_b64, formato),
    )
    conn.commit()
    conn.close()


def _publicar_estado(trip_id, estado):
    """Publica un evento de estado en Redis Pub/Sub (best-effort)."""
    try:
        _get_redis().publish(REDIS_CHANNEL,
                             json.dumps({"tipo": "estado", "id": trip_id, "estado": estado}))
    except Exception:
        pass


def _odometro_vehiculo(conn, vehicle_id):
    """Último odómetro conocido de un vehículo (telemetría); None si no hay datos."""
    r = conn.execute(
        "SELECT odometer_km FROM telemetria.posiciones_gps "
        "WHERE vehiculo_id=? AND odometer_km IS NOT NULL ORDER BY time DESC LIMIT 1", (vehicle_id,)
    ).fetchone()
    return r["odometer_km"] if r else None


def _aplicar_estado_viaje(trip_id, nuevo_estado, ts=None):
    """Actualiza transaccionalmente el estado del viaje y lo publica en Redis.

    - UPDATE trips (estado + fecha_actualizacion + captura de km por odómetro).
    - Captura km_inicio al primer estado activo (Cargando/En_Transito) y km_fin/km_real
      al Entregado, con guard de sanidad (caída a planificado si el delta es inválido).
    - Publica {"tipo":"estado","id":trip_id,"estado":nuevo_estado} en canal_operaciones
      (es exactamente lo que _facturacion_listener escucha para facturar al Entregado).
    - Si el nuevo estado es 'Entregado', libera el viaje activo (DEL del SET en Redis).
    Devuelve True si el viaje existía, False si no se encontró.
    """
    conn = _db()
    row = conn.execute(
        "SELECT id, terminal, km_inicio, km_fin, km_total FROM trips WHERE id=?", (trip_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    terminal = row["terminal"]
    ts_iso = ts or (datetime.datetime.utcnow().isoformat() + "Z")

    # Captura de km por odómetro (km_real = km_fin - km_inicio, con guard de sanidad).
    km_updates, km_params = "", []
    if terminal:
        if nuevo_estado in ("Cargando", "En_Transito") and row["km_inicio"] is None:
            odo = _odometro_vehiculo(conn, terminal)
            if odo is not None:
                km_updates += ", km_inicio=?"
                km_params.append(odo)
        if nuevo_estado == "Entregado" and row["km_fin"] is None:
            odo = _odometro_vehiculo(conn, terminal)
            if odo is not None:
                km_updates += ", km_fin=?"
                km_params.append(odo)
                if row["km_inicio"] is not None and float(odo) >= float(row["km_inicio"]):
                    km_real = float(odo) - float(row["km_inicio"])
                    km_total = float(row["km_total"] or 0)
                    if km_real <= 0 or (km_total > 0 and km_real > km_total * 2.5):
                        km_updates += ", km_fuente='invalido'"
                    else:
                        km_updates += ", km_real=?, km_fuente='real'"
                        km_params.append(km_real)
                else:
                    km_updates += ", km_fuente='planificado'"

    conn.execute(
        f"UPDATE trips SET estado=?, fecha_actualizacion=?{km_updates} WHERE id=?",
        [nuevo_estado, ts_iso, *km_params, trip_id],
    )
    conn.commit()
    conn.close()
    _publicar_estado(trip_id, nuevo_estado)
    if nuevo_estado == "Entregado" and terminal:
        _del_viaje_activo(terminal)
    return True


def _cerrar_viaje(trip_id):
    """Marca el viaje como Entregado (usa la vía transaccional + Redis)."""
    _aplicar_estado_viaje(trip_id, "Entregado")
    return trip_id


def _cerrar_viaje_por_ecmr(fd_id):
    """Cierra el viaje cuyo ecmr_id coincide con el freightDocumentId recibido."""
    conn = _db()
    try:
        row = conn.execute("SELECT id, terminal FROM trips WHERE ecmr_id=? LIMIT 1", (fd_id,)).fetchone()
        if not row:
            return None
        trip_id = row["id"]
        conn.execute("UPDATE trips SET estado='Entregado' WHERE id=?", (trip_id,))
        conn.commit()
    finally:
        conn.close()
    _publicar_estado(trip_id, "Entregado")
    if row["terminal"]:
        _del_viaje_activo(row["terminal"])
    return trip_id


def _entrega_confirmada(qp):
    """Detecta si el question path (ARE/AFRE) confirma entrega o firma del e-CMR.

    Heurística sobre el texto de las respuestas <Answer>…</Answer> y del reporte
    completo. AJUSTAR a la estructura real del CDATA de Trimble si es necesario.
    """
    if not qp:
        return False
    respuestas = " ".join(re.findall(r"<Answer[^>]*>(.*?)</Answer>", qp, re.S))
    texto = (respuestas + " " + qp).lower()
    firmado = any(k in texto for k in ("signed", "firmado", "signature", "firma", "signed by"))
    sin_incidencias = any(k in texto for k in (
        "sin incidencia", "sin incidencias", "sin anomal", "no incident", "no incidents",
        "without incident", "sin daños", "sin faltas", "no damage"))
    return firmado or sin_incidencias


MANTENIMIENTO_INTERVALO = int(os.environ.get("MANTENIMIENTO_INTERVALO", "3600"))  # segundos


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
            conn.execute("UPDATE vehiculos SET km_actuales=? WHERE id=?", (odo / 1000.0, vid))
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


async def _poll_mantenimiento():
    """Bucle de revisión de mantenimiento (cada MANTENIMIENTO_INTERVALO segundos)."""
    while True:
        try:
            await asyncio.to_thread(_revisar_mantenimiento)
        except Exception as e:
            print(f"[mantenimiento] error: {e}")
        await asyncio.sleep(MANTENIMIENTO_INTERVALO)


async def _facturacion_listener():
    """Escucha canal_operaciones y factura automáticamente los viajes entregados."""
    while True:
        pubsub = None
        r = None
        try:
            r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if not msg or msg.get("type") != "message":
                    continue
                try:
                    ev = json.loads(msg.get("data") or "{}")
                except (ValueError, TypeError):
                    continue
                if ev.get("tipo") == "estado" and ev.get("estado") == "Entregado" and ev.get("id"):
                    await asyncio.to_thread(_facturar_viaje, ev["id"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[facturacion] error de suscripción: {e}")
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(REDIS_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass
        await asyncio.sleep(5)  # reintentar la suscripción si Redis cayó


def _sync_files():
    # 1) trazas tipo 10 (activity started) -> mapa LID -> trip_id
    lid_map = json.loads(_get_sync_state("lid_map") or "{}")
    mark = _get_sync_state("traces_mark")
    pos_conn = _db()
    pos_conn.set_autocommit(True)  # no mantener transacción abierta durante los poll SOAP (evita lock en cascada)
    posiciones = {}
    eventos = []  # cambios de actividad a publicar en Redis Pub/Sub
    for _ in range(30):
        r = get_client().poll_traces(mark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<traces>(.*?)</traces>", r["body"], re.S):
            ttype = re.search(r"<type>(\d+)</type>", block)
            props = _parse_props(block)
            t = ttype.group(1) if ttype else ""
            if t == "10" and props.get("LID") and props.get("TRID"):
                lid_map[props["LID"]] = props["TRID"]
                eventos.append({"tipo": "actividad", "traza": "10",
                                "trip_id": props["TRID"], "reporte": ""})
            elif t in ("12", "13") and props.get("LID"):
                # activity report (12) o activity end (13) -> question path (ARE/AFRE)
                trip_id = props.get("TRID") or lid_map.get(props["LID"])
                src = re.search(r"<source>([^<]*)</source>", block)
                tm = re.search(r"<time>([^<]*)</time>", block)
                rt, qp = _extraer_reporte_xml(block)
                if not qp:
                    continue  # sin reporte (ARE/AFRE), no es question path
                eseq = props.get("ESEQ", "")
                suf = f"_{rt.lower()}" + (f"_{eseq}" if eseq else "")
                _save_mensaje(
                    f"qp_{props['LID']}{suf}", trip_id, "cuestionario", props.get("ATY", ""),
                    props["LID"], src.group(1) if src else "", rt,
                    qp, tm.group(1) if tm else "", False,
                )
                # Regla de negocio: CMR/DESCARGA firmada o sin incidencias => cerrar viaje.
                aty = (props.get("ATY") or "").upper()
                if trip_id and aty in ("CMR", "DESCARGA") and _entrega_confirmada(qp):
                    _cerrar_viaje(trip_id)
                    doc = _extraer_documento_ecmr(qp)
                    if doc:
                        _guardar_documento_entrega(trip_id, doc[0], doc[1], doc[2],
                                                   tm.group(1) if tm else None)
                # AFRE (informe final, traza 13) puede llevar el e-CMR embebido.
                eventos.append({"tipo": "actividad", "traza": t,
                                "trip_id": trip_id, "reporte": rt})
            elif t == "82":
                # Traza 82: cambio de estado del tacógrafo con DSTAT (estadísticas del conductor).
                did = props.get("DID", "")
                dstat_b64 = props.get("DSTAT", "")
                if did and dstat_b64:
                    src = re.search(r"<source>([^<]*)</source>", block)
                    tm = re.search(r"<time>([^<]*)</time>", block)
                    source = src.group(1) if src else ""
                    veh = _source_a_vehiculo(pos_conn, source) if source else None
                    _ingestar_dstat(did, source, veh, dstat_b64,
                                    _decode_dstat(dstat_b64), tm.group(1) if tm else "")
            pos = _extraer_posicion(block)
            if pos:
                veh = _source_a_vehiculo(pos_conn, pos["source"])
                if veh:
                    posiciones[veh] = (pos["lat"], pos["lng"], pos["time"])
                    # Solo el flujo GPS periódico (tipo 0) va a la telemetría histórica.
                    if t == "0":
                        _guardar_telemetria(pos, veh)
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            mark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    for veh, (lat, lng, ttime) in posiciones.items():
        pos_conn.execute(
            "UPDATE vehiculos SET last_lat=?, last_lng=?, last_position_time=? WHERE id=?",
            (lat, lng, ttime, veh),
        )
    pos_conn.commit()
    pos_conn.close()
    _set_sync_state("traces_mark", mark or "")
    _set_sync_state("lid_map", json.dumps(lid_map))

    # Publicar cambios de actividad en Redis Pub/Sub (tiempo real).
    for ev in eventos:
        try:
            _get_redis().publish(REDIS_CHANNEL, json.dumps(ev))
        except Exception:
            pass

    # re-asignar archivos ya descargados cuyo LID ya tiene mapeo
    if lid_map:
        conn = _db()
        for lid, trip in lid_map.items():
            conn.execute(
                "UPDATE files SET trip_id=? WHERE lid=? AND trip_id IS NULL",
                (trip, lid),
            )
        conn.commit()
        conn.close()

    # 2) poll files + descarga (solo tipo 3 = documentos/escaneos del conductor)
    fmark = _get_sync_state("files_mark")
    for _ in range(10):
        r = get_client().poll_files(fmark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<files>(.*?)</files>", r["body"], re.S):
            name = re.search(r"<name>([^<]*)</name>", block)
            ftype = re.search(r"<type>([^<]*)</type>", block)
            if not name or (ftype and ftype.group(1) != "3"):
                continue
            ftime = re.search(r"<time>([^<]*)</time>", block)
            source = re.search(r"<source>([^<]*)</source>", block)
            driver = re.search(r"<driver>([^<]*)</driver>", block)
            props = _parse_props(block)
            lid = props.get("LID", "")
            trip_id = lid_map.get(lid) if lid else None
            dl = get_client().download_file(name.group(1))
            content = re.search(r"<return>([^<]*)</return>", dl.get("body", ""))
            _save_file(
                trip_id, name.group(1),
                int(ftype.group(1)) if ftype else 0,
                ftime.group(1) if ftime else "",
                source.group(1) if source else "",
                driver.group(1) if driver else "",
                lid, content.group(1) if content else "",
            )
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            fmark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("files_mark", fmark or "")


def _save_mensaje(mid, trip_id, tipo, messagetype, originid, source, subject, body, mtime, needreply):
    conn = _db()
    conn.execute(
        "INSERT INTO mensajes (id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
        (mid, trip_id, tipo, messagetype, originid, source, subject, body, mtime, needreply,
         datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def _store_mensaje(block, tipo, lid_map):
    def f(tag):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
        return m.group(1).strip() if m else ""
    mid = f("id")
    if not mid:
        return
    originid = f("originid")
    trip_id = lid_map.get(originid) if originid else None
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


def _sync_mensajes():
    """Polls mensajes estructurados y libres del conductor (Messaging), anexándolos al viaje vía originid→LID."""
    lid_map = json.loads(_get_sync_state("lid_map") or "{}")

    # 1) mensajes estructurados (candidato del question path)
    smark = _get_sync_state("mensajes_mark")
    for _ in range(10):
        r = get_client().poll_structured_messages(smark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<messages>(.*?)</messages>", r["body"], re.S):
            _store_mensaje(block, "estructurado", lid_map)
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            smark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("mensajes_mark", smark or "")

    # 2) mensajes libres
    fmark = _get_sync_state("mensajes_free_mark")
    for _ in range(10):
        r = get_client().poll_messages(fmark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<messages>(.*?)</messages>", r["body"], re.S):
            _store_mensaje(block, "libre", lid_map)
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            fmark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("mensajes_free_mark", fmark or "")


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
    """Persiste la foto DSTAT más reciente de un conductor (clave DID) con su terminal."""
    if not decoded:
        return
    conn = _db()
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
    conn.close()


def _dstat_terminal(terminal):
    """Devuelve el DSTAT más reciente del conductor logueado en el terminal (o None)."""
    conn = _db()
    row = conn.execute(
        "SELECT * FROM tacografo_dstat WHERE vehiculo_id=? ORDER BY COALESCE(time, creado) DESC LIMIT 1",
        (terminal,),
    ).fetchone()
    conn.close()
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


@app.get("/api/tacografo/{terminal}/dstat")
def tacografo_dstat(terminal: str):
    """Estadísticas de conducción (DSTAT, traza 82) del conductor logueado en el terminal."""
    stats = _dstat_terminal(terminal.strip())
    if not stats:
        return {"ok": False, "terminal": terminal, "error": "Sin datos de tacógrafo para este terminal."}
    limite_diario = _EXT_DIA_CONDUCCION_MIN if stats["week_long_driving_count"] < 2 else _MAX_DIA_CONDUCCION_MIN
    return {
        "ok": True,
        "terminal": terminal,
        "did": stats["did"],
        "driving_coupure_min": stats["driving_coupure"],
        "day_driving_min": stats["day_driving"],
        "week_driving_min": stats["week_driving"],
        "remaining_week_available_min": stats["remaining_week_available"],
        "week_long_driving_count": stats["week_long_driving_count"],
        "next_rest_due_ts": stats["next_rest_due_ts"],
        "next_rest_due": datetime.datetime.fromtimestamp(
            stats["next_rest_due_ts"], tz=datetime.timezone.utc
        ).strftime("%d/%m %H:%M") if stats["next_rest_due_ts"] else "",
        "conduccion_continua_restante_min": round(max(0.0, _MAX_CONDUCCION_CONTINUA_MIN - stats["driving_coupure"]), 1),
        "dia_restante_min": round(max(0.0, limite_diario - stats["day_driving"]), 1),
        "limite_diario_min": limite_diario,
    }


@app.get("/api/clientes/{cliente_id}/pales")
def cliente_pales(cliente_id: int):
    """Cuenta corriente de palés de un cliente: saldo actual + histórico de movimientos."""
    conn = _db()
    movs = conn.execute(
        "SELECT * FROM saldos_pales WHERE cliente_id=? ORDER BY id DESC LIMIT 200",
        (cliente_id,),
    ).fetchall()
    conn.close()
    saldo = int(movs[0]["balance"]) if movs else 0
    return {
        "ok": True,
        "cliente_id": cliente_id,
        "saldo": saldo,
        "movimientos": [dict(m) for m in movs],
    }


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
    conn = _db()
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
    conn.close()
    return {"cliente_id": cliente_id, "balance": balance}


@app.get("/api/trips/status")
def trips_status():
    _sync_status()
    conn = _db()
    rows = conn.execute("SELECT * FROM trips ORDER BY creado DESC LIMIT 100").fetchall()
    conn.close()
    return {"viajes": [dict(r) for r in rows]}


@app.get("/api/trips/{trip_id}")
def get_trip(trip_id: str):
    conn = _db()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    paradas = conn.execute("SELECT * FROM paradas WHERE trip_id=? ORDER BY orden", (trip_id,)).fetchall()
    conn.close()
    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return {"trip": dict(row), "payload": payload, "paradas": [dict(p) for p in paradas]}


def _get_config(key, default=""):
    conn = _db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


@app.get("/api/config")
def get_config():
    conn = _db()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conn.close()
    out = {}
    for r in rows:
        k, v = r["key"], r["value"]
        kl = k.lower()
        if any(s in kl for s in ("password", "api_key", "token", "secret", "clave", "contraseña")):
            v = "••••••••" if v else ""
        out[k] = v
    return {"config": out}


@app.post("/api/config")
def set_config(req: dict):
    conn = _db()
    for k, v in req.items():
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (k, str(v)),
        )
    conn.commit()
    conn.close()
    # Si cambian credenciales Trimble, invalidar el cliente SOAP cacheado
    if any(k.startswith("trimble_") for k in req.keys()):
        _client_cache.clear()
    return {"ok": True}


@app.post("/api/ecmr/crear")
def crear_ecmr(req: dict):
    """Crea un e-CMR (Freight Document) en TransFollow y devuelve su freightDocumentId."""
    carrier_email = (req.get("carrier_email") or "").strip()
    if not carrier_email:
        raise HTTPException(status_code=400, detail={"error": "Indica el email del transportista (carrier)"})
    payload = build_waybill(
        req.get("trip") or {},
        carrier_email=carrier_email,
        consignor=req.get("consignor") or {},
        consignee=req.get("consignee") or {},
        goods=req.get("goods") or [],
    )
    r = get_transfollow_client().create_freight_document(payload)
    if not r.get("ok"):
        detail = r.get("description") or r.get("error") or "error desconocido"
        raise HTTPException(status_code=502, detail={"error": f"TransFollow: {detail}"})
    fd_id = r.get("freightDocumentId")
    # Persistir el vínculo freightDocumentId -> viaje para el webhook de cierre.
    trip_id = (req.get("trip") or {}).get("id")
    if trip_id and fd_id:
        conn = _db()
        conn.execute("UPDATE trips SET ecmr_id=? WHERE id=?", (fd_id, trip_id))
        conn.commit()
        conn.close()
    return {"ok": True, "freightDocumentId": fd_id}


# Credenciales del webhook de TransFollow (Basic auth). Sin contraseña configurada
# el webhook NO valida autenticación (solo desarrollo); configúrala en producción.
TRANSFOLLOW_WEBHOOK_USER = os.environ.get("TRANSFOLLOW_WEBHOOK_USER", "transfollow")
TRANSFOLLOW_WEBHOOK_PASSWORD = os.environ.get("TRANSFOLLOW_WEBHOOK_PASSWORD", "")


def _webhook_autenticado(authorization: str) -> bool:
    """Verifica la cabecera Authorization (Basic auth) del webhook de TransFollow.
    Fail closed: sin contraseña configurada se RECHAZA (nunca aceptar sin validar)."""
    if not TRANSFOLLOW_WEBHOOK_PASSWORD:
        return False  # sin credenciales: rechazar (fail closed)
    expected = "Basic " + base64.b64encode(
        f"{TRANSFOLLOW_WEBHOOK_USER}:{TRANSFOLLOW_WEBHOOK_PASSWORD}".encode()
    ).decode()
    return hmac.compare_digest(authorization, expected)


@app.post("/api/webhooks/transfollow")
async def webhook_transfollow(req: dict, authorization: str = Header(default="")):
    """Webhook de TransFollow: cierra el viaje cuando el e-CMR se entrega.

    Seguridad: TransFollow autentica sus webhooks por Basic auth (o mTLS / path
    aleatorio); aquí validamos la cabecera Authorization de forma constante en el
    tiempo. Estados TransFollow: DRAFT→ISSUED→TRANSIT→DELIVERED / CANCELLED /
    DELIVERED FOR FURTHER INSPECTION. Solo DELIVERED limpio cierra el viaje.
    """
    if not _webhook_autenticado(authorization):
        raise HTTPException(status_code=401, detail={"error": "No autorizado"})

    data = req.get("data") if isinstance(req.get("data"), dict) else req
    fd_id = (data.get("freightDocumentId") or data.get("documentId")
             or data.get("id") or req.get("freightDocumentId"))
    estado = str(data.get("status") or data.get("state")
                 or data.get("event") or data.get("eventType") or "").lower()
    # DELIVERED FOR FURTHER INSPECTION NO es entrega limpia: no cerrar.
    if "delivered for further inspection" in estado:
        return {"ok": True, "ignored": True, "estado": estado}
    entregado = any(k in estado for k in ("delivered", "entregado", "completed",
                                          "finalizado", "signed", "firmado"))
    if not entregado:
        return {"ok": True, "ignored": True, "estado": estado}
    if not fd_id:
        raise HTTPException(status_code=400, detail={"error": "freightDocumentId ausente"})
    trip_id = await asyncio.to_thread(_cerrar_viaje_por_ecmr, str(fd_id))
    if not trip_id:
        return {"ok": True, "ignored": True, "motivo": "sin viaje asociado"}
    return {"ok": True, "trip_id": trip_id, "estado": "Entregado"}


@app.get("/api/mantenimiento/alertas")
def mantenimiento_alertas(user: dict = Depends(require_role(["admin", "dispatcher"])),
                          estado: str = "abierta"):
    """Consulta las alertas (tabla pública unificada: inspecciones + predictivas) por estado."""
    conn = _db()
    rows = conn.execute(
        "SELECT a.id, a.vehiculo_id, a.codigo, a.severidad, a.mensaje, a.estado, a.creado_en, "
        "v.matricula, v.marca, v.modelo, v.categoria "
        "FROM alertas_mantenimiento a "
        "LEFT JOIN vehiculos v ON v.id = a.vehiculo_id "
        "WHERE a.estado = ? ORDER BY a.creado_en DESC",
        (estado,),
    ).fetchall()
    conn.close()
    return {"alertas": [dict(r) for r in rows]}


@app.post("/api/mantenimiento/resolver/{alerta_id}")
def mantenimiento_resolver(alerta_id: int,
                           user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Marca la alerta (tabla pública) como Resuelta y actualiza ultimo_km_realizado de su regla."""
    conn = _db()
    row = conn.execute("SELECT * FROM alertas_mantenimiento WHERE id=?", (alerta_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Alerta no encontrada"})
    conn.execute("UPDATE alertas_mantenimiento SET estado='resuelta' WHERE id=?", (alerta_id,))
    tipo = (row["codigo"] or "").replace("MANT-", "")
    reg = conn.execute(
        "SELECT tipo_mantenimiento FROM flota.reglas_mantenimiento WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?) LIMIT 1",
        (row["vehiculo_id"], tipo),
    ).fetchone()
    if reg:
        tipo = reg["tipo_mantenimiento"]
    km = conn.execute("SELECT km_actuales FROM vehiculos WHERE id=?", (row["vehiculo_id"],)).fetchone()
    conn.execute(
        "UPDATE flota.reglas_mantenimiento SET ultimo_km_realizado=? WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?)",
        ((km["km_actuales"] if km and km["km_actuales"] else 0), row["vehiculo_id"], tipo),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "alerta_id": alerta_id}


@app.post("/api/mantenimiento/convertir/{alerta_id}")
def mantenimiento_convertir(alerta_id: int,
                            user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Convierte una alerta (tabla pública) en orden de taller y la resuelve."""
    conn = _db()
    row = conn.execute("SELECT * FROM alertas_mantenimiento WHERE id=?", (alerta_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Alerta no encontrada"})
    tipo = (row["codigo"] or "").replace("MANT-", "") or "Mantenimiento"
    reg = conn.execute(
        "SELECT tipo_mantenimiento FROM flota.reglas_mantenimiento WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?) LIMIT 1",
        (row["vehiculo_id"], tipo),
    ).fetchone()
    if reg:
        tipo = reg["tipo_mantenimiento"]
    km = conn.execute("SELECT km_actuales FROM vehiculos WHERE id=?", (row["vehiculo_id"],)).fetchone()
    km_val = km["km_actuales"] if km and km["km_actuales"] else 0
    hoy = datetime.date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO mantenimientos (vehiculo_id, tipo, fecha, km, coste, notas, hecho, creado) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        (row["vehiculo_id"], tipo, hoy, km_val, 0,
         "Convertida desde alerta preventiva", False,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    mid = cur.fetchone()["id"]
    conn.execute("UPDATE alertas_mantenimiento SET estado='resuelta' WHERE id=?", (alerta_id,))
    conn.execute(
        "UPDATE flota.reglas_mantenimiento SET ultimo_km_realizado=? WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?)",
        (km_val, row["vehiculo_id"], tipo),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "mantenimiento_id": mid}




@app.get("/api/empresa")
def get_empresa():
    conn = _db()
    row = conn.execute("SELECT * FROM empresa WHERE id=1").fetchone()
    conn.close()
    return {"empresa": dict(row) if row else {}}


@app.post("/api/empresa")
def set_empresa(e: Empresa):
    conn = _db()
    conn.execute(
        "INSERT INTO empresa (id, nombre, cif, direccion, poblacion, cp, pais, telefono, email, web, iva, iban) "
        "VALUES (1,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET nombre=EXCLUDED.nombre, cif=EXCLUDED.cif, direccion=EXCLUDED.direccion, "
        "poblacion=EXCLUDED.poblacion, cp=EXCLUDED.cp, pais=EXCLUDED.pais, telefono=EXCLUDED.telefono, "
        "email=EXCLUDED.email, web=EXCLUDED.web, iva=EXCLUDED.iva, iban=EXCLUDED.iban",
        (e.nombre, e.cif, e.direccion, e.poblacion, e.cp, e.pais, e.telefono, e.email, e.web, e.iva, e.iban),
    )
    conn.commit()
    conn.close()
    return {"ok": True}




@app.get("/api/mantenimientos")
def list_mantenimientos(vehiculo_id: str = ""):
    conn = _db()
    if vehiculo_id:
        rows = conn.execute(
            "SELECT m.*, v.matricula, v.categoria FROM mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id "
            "WHERE m.vehiculo_id=? ORDER BY m.fecha", (vehiculo_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.*, v.matricula, v.categoria FROM mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id ORDER BY m.fecha"
        ).fetchall()
    conn.close()
    return {"mantenimientos": [dict(r) for r in rows]}


@app.post("/api/mantenimientos")
def add_mantenimiento(m: Mantenimiento):
    conn = _db()
    cur = conn.execute(
        "INSERT INTO mantenimientos (vehiculo_id, tipo, fecha, fecha_fin, km, coste, notas, hecho, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
        (m.vehiculo_id, m.tipo, m.fecha, m.fecha_fin, m.km, m.coste, m.notas, m.hecho,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    mid = cur.fetchone()["id"]
    # Integración contable: si se marca Completado y se pide generar gasto → gastos_vehiculos + asiento 622/472/400.
    if m.hecho and m.generar_gasto and m.base_imponible > 0:
        iva_pct = round(float(m.iva or 21), 2)
        importe = round(float(m.base_imponible) * (1 + iva_pct / 100.0), 2)
        cuota = round(importe - float(m.base_imponible), 2)
        concepto = (m.tipo or "Reparación").strip()
        gcur = conn.execute(
            "INSERT INTO gastos_vehiculos (vehiculo_id, proveedor_id, fecha, tipo, litros, base_imponible, iva, "
            "importe_total, factura_ref, cuenta_contable_gasto, estado_pago, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (m.vehiculo_id, m.proveedor_id, m.fecha, "reparaciones", 0, m.base_imponible, iva_pct, importe,
             "", "622", "Pendiente", datetime.datetime.utcnow().isoformat() + "Z"),
        )
        gid = gcur.fetchone()["id"]
        lineas = [("622", float(m.base_imponible), 0, concepto)]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("400", 0, importe, "Proveedor"))
        try:
            _registrar_asiento((m.fecha or "")[:10], f"Gasto taller: {concepto}", lineas,
                               origen="Gasto_Vehiculo", origen_id=str(gid), conn=conn)
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    conn.close()
    return {"ok": True, "id": mid}


@app.patch("/api/mantenimientos/{mid}")
def upd_mantenimiento(mid: int, m: Optional[Mantenimiento] = None):
    conn = _db()
    if m is None:
        conn.execute("UPDATE mantenimientos SET hecho = NOT hecho WHERE id=?", (mid,))
    else:
        conn.execute(
            "UPDATE mantenimientos SET vehiculo_id=?, tipo=?, fecha=?, km=?, coste=?, notas=?, hecho=? WHERE id=?",
            (m.vehiculo_id, m.tipo, m.fecha, m.km, m.coste, m.notas, m.hecho, mid),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/mantenimientos/{mid}/campos")
def upd_mantenimiento_campos(mid: int, body: dict):
    """Edición en línea parcial: estado (hecho), coste, km, fechas, tipo o notas."""
    allow = ("hecho", "coste", "km", "fecha", "fecha_fin", "notas", "tipo")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE mantenimientos SET {sets} WHERE id=?", (*fields.values(), mid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/mantenimientos/{mid}")
def delete_mantenimiento(mid: int):
    conn = _db()
    conn.execute("DELETE FROM mantenimientos WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return {"ok": True}




@app.get("/api/alertas")
def list_alertas(estado: str = ""):
    conn = _db()
    base = (
        "SELECT a.id, a.vehiculo_id, a.codigo, a.severidad, a.mensaje, a.estado, a.creado_en, "
        "i.reporte_id, v.matricula, v.marca, v.modelo "
        "FROM alertas_mantenimiento a "
        "LEFT JOIN inspecciones i ON i.id = a.inspeccion_id "
        "LEFT JOIN vehiculos v ON v.id = a.vehiculo_id "
    )
    if estado:
        rows = conn.execute(base + " WHERE a.estado=? ORDER BY a.creado_en DESC", (estado,)).fetchall()
    else:
        rows = conn.execute(base + " ORDER BY a.creado_en DESC").fetchall()
    conn.close()
    return {"alertas": [dict(r) for r in rows]}


@app.patch("/api/alertas/{aid}")
def upd_alerta(aid: int, body: AlertaUpdate):
    conn = _db()
    if body.estado in ("resuelta", "descartada", "abierta"):
        conn.execute("UPDATE alertas_mantenimiento SET estado=? WHERE id=?", (body.estado, aid))
    conn.commit()
    conn.close()
    return {"ok": True}






@app.get("/api/transportistas")
def list_transportistas():
    conn = _db()
    rows = conn.execute("SELECT * FROM transportistas ORDER BY nombre").fetchall()
    conn.close()
    return {"transportistas": [dict(r) for r in rows]}


@app.post("/api/transportistas")
def add_transportista(t: Transportista):
    conn = _db()
    conn.execute(
        "INSERT INTO transportistas (nombre, cif, telefono, email, tarifa) VALUES (?,?,?,?,?)",
        (t.nombre, t.cif, t.telefono, t.email, t.tarifa),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/transportistas/{tid}")
def del_transportista(tid: int):
    conn = _db()
    conn.execute("DELETE FROM liquidaciones WHERE transportista_id=?", (tid,))
    conn.execute("DELETE FROM transportistas WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/transportistas/{tid}")
def upd_transportista(tid: int, t: Transportista):
    conn = _db()
    conn.execute("UPDATE transportistas SET nombre=?, cif=?, telefono=?, email=?, tarifa=? WHERE id=?",
                 (t.nombre, t.cif, t.telefono, t.email, t.tarifa, tid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/liquidaciones")
def list_liquidaciones():
    conn = _db()
    liq = conn.execute(
        "SELECT l.*, t.nombre AS transportista FROM liquidaciones l "
        "LEFT JOIN transportistas t ON l.transportista_id = t.id ORDER BY l.fecha"
    ).fetchall()
    acum = conn.execute(
        "SELECT t.id, t.nombre, t.cif, t.tarifa, "
        "COALESCE(SUM(CASE WHEN l.pagado THEN 0 ELSE l.importe END), 0) AS pendiente, "
        "COALESCE(SUM(l.importe), 0) AS total "
        "FROM transportistas t LEFT JOIN liquidaciones l ON l.transportista_id = t.id "
        "GROUP BY t.id, t.nombre, t.cif, t.tarifa ORDER BY t.nombre"
    ).fetchall()
    conn.close()
    return {"liquidaciones": [dict(r) for r in liq], "por_transportista": [dict(r) for r in acum]}


@app.post("/api/liquidaciones")
def add_liquidacion(l: Liquidacion):
    conn = _db()
    conn.execute(
        "INSERT INTO liquidaciones (transportista_id, fecha, importe, concepto, pagado, creado) "
        "VALUES (?,?,?,?,?,?)",
        (l.transportista_id, l.fecha, l.importe, l.concepto, l.pagado,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/liquidaciones/{lid}")
def upd_liquidacion(lid: int, l: Optional[Liquidacion] = None):
    conn = _db()
    if l is None:
        conn.execute("UPDATE liquidaciones SET pagado = NOT pagado WHERE id=?", (lid,))
    else:
        conn.execute(
            "UPDATE liquidaciones SET transportista_id=?, fecha=?, importe=?, concepto=?, pagado=? WHERE id=?",
            (l.transportista_id, l.fecha, l.importe, l.concepto, l.pagado, lid),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/liquidaciones/{lid}")
def del_liquidacion(lid: int):
    conn = _db()
    conn.execute("DELETE FROM liquidaciones WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/ingresos")
def ingresos(desde: str = "", hasta: str = "", estado: str = ""):
    """Agrega los ingresos por vehículo y por cliente (precio de los viajes).

    Filtros opcionales: desde/hasta (fecha YYYY-MM-DD) y estado.
    """
    conn = _db()
    query = ("SELECT id, nombre, terminal, cliente, conductor, tipo_carga, origen, destino, "
             "precio, gastos, km_total, estado_pago, factura, iva, creado FROM trips")
    conds, params = [], []
    if desde:
        conds.append("substr(creado, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(creado, 1, 10) <= ?")
        params.append(hasta)
    if estado:
        conds.append("estado = ?")
        params.append(estado)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    rows = conn.execute(query, params).fetchall()

    cf_map = {}
    for r in conn.execute("SELECT terminal, SUM(importe) AS t FROM costes_fijos GROUP BY terminal").fetchall():
        cf_map[(r["terminal"] or "").strip()] = round(r["t"] or 0, 2)
    gv_map = {}
    gv_conds, gv_params = [], []
    if desde:
        gv_conds.append("substr(fecha, 1, 10) >= ?")
        gv_params.append(desde)
    if hasta:
        gv_conds.append("substr(fecha, 1, 10) <= ?")
        gv_params.append(hasta)
    gv_where = (" WHERE " + " AND ".join(gv_conds)) if gv_conds else ""
    for r in conn.execute(f"SELECT terminal, SUM(importe) AS t FROM gastos{gv_where} GROUP BY terminal", gv_params).fetchall():
        gv_map[(r["terminal"] or "").strip()] = round(r["t"] or 0, 2)
    conn.close()

    # Costes de estructura: % sobre ingresos (configurable en /api/config)
    try:
        pct_estructura = float(_get_config("costes_estructura_pct", "0") or 0)
    except Exception:
        pct_estructura = 0.0

    total = 0.0
    total_km = 0.0
    total_gastos = 0.0
    cobrado = 0.0
    pendiente = 0.0
    por_vehiculo = {}
    por_cliente = {}
    por_mes = {}
    detalle = []
    for r in rows:
        p = float(r["precio"] or 0) or 0.0
        g = float(r["gastos"] or 0) or 0.0
        km = float(r["km_total"] or 0) or 0.0
        detalle.append({
            "id": r["id"], "nombre": r["nombre"], "terminal": r["terminal"] or "",
            "cliente": r["cliente"] or "", "conductor": r["conductor"] or "",
            "tipo_carga": r["tipo_carga"] or "", "origen": r["origen"] or "",
            "destino": r["destino"] or "", "precio": round(p, 2), "gastos": round(g, 2),
            "margen": round(p - g, 2), "km_total": round(km, 1),
            "estado_pago": r["estado_pago"] or "", "factura": r["factura"] or "",
            "creado": r["creado"] or "",
        })
        if p <= 0:
            continue
        ep = (r["estado_pago"] or "").strip().lower()
        mes = (r["creado"] or "")[:7] or "—"
        total += p
        total_gastos += g
        total_km += km
        if ep == "cobrada":
            cobrado += p
        else:
            pendiente += p
        term = (r["terminal"] or "").strip() or "Sin vehículo"
        cli = (r["cliente"] or "").strip() or "Sin cliente"
        v = por_vehiculo.setdefault(term, {"total": 0.0, "gastos": 0.0, "km": 0.0, "viajes": 0})
        v["total"] += p
        v["gastos"] += g
        v["km"] += km
        v["viajes"] += 1
        c = por_cliente.setdefault(cli, {"total": 0.0, "gastos": 0.0, "km": 0.0, "viajes": 0})
        c["total"] += p
        c["gastos"] += g
        c["km"] += km
        c["viajes"] += 1
        m = por_mes.setdefault(mes, {"total": 0.0, "gastos": 0.0, "cobrado": 0.0, "viajes": 0})
        m["total"] += p
        m["gastos"] += g
        if ep == "cobrada":
            m["cobrado"] += p
        m["viajes"] += 1

    def _eur_km(t, k):
        return round(t / k, 2) if k > 0 else None

    def _fmt(d):
        return sorted(
            [{"nombre": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
              "margen": round(v["total"] - v["gastos"], 2), "km": round(v["km"], 1),
              "eur_km": _eur_km(v["total"], v["km"]), "viajes": v["viajes"]}
             for k, v in d.items()],
            key=lambda x: -x["total"],
        )

    def _fmt_vehiculo(d):
        out = []
        for k, v in d.items():
            cf = cf_map.get(k, 0.0)
            gv = gv_map.get(k, 0.0)
            ce = round(v["total"] * pct_estructura / 100, 2)
            out.append({"nombre": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
                        "margen": round(v["total"] - v["gastos"], 2), "costes_fijos": cf,
                        "gastos_vehiculo": gv, "costes_estructura": ce,
                        "margen_real": round(v["total"] - v["gastos"] - cf - gv - ce, 2),
                        "km": round(v["km"], 1), "eur_km": _eur_km(v["total"], v["km"]),
                        "viajes": v["viajes"]})
        return sorted(out, key=lambda x: -x["total"])

    def _fmt_mes():
        return sorted(
            [{"mes": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
              "margen": round(v["total"] - v["gastos"], 2),
              "cobrado": round(v["cobrado"], 2),
              "pendiente": round(v["total"] - v["cobrado"], 2), "viajes": v["viajes"]}
             for k, v in por_mes.items()],
            key=lambda x: x["mes"],
        )

    total_cf = round(sum(cf_map.values()), 2)
    total_gv = round(sum(gv_map.values()), 2)
    costes_estructura = round(total * pct_estructura / 100, 2)
    return {
        "total": round(total, 2),
        "total_gastos": round(total_gastos, 2),
        "margen": round(total - total_gastos, 2),
        "cobrado": round(cobrado, 2),
        "pendiente": round(pendiente, 2),
        "total_km": round(total_km, 1),
        "eur_km": _eur_km(total, total_km),
        "viajes": sum(v["viajes"] for v in por_vehiculo.values()),
        "costes_fijos": total_cf,
        "gastos_vehiculo": total_gv,
        "costes_estructura": costes_estructura,
        "costes_estructura_pct": round(pct_estructura, 2),
        "margen_real": round(total - total_gastos - total_cf - total_gv - costes_estructura, 2),
        "por_vehiculo": _fmt_vehiculo(por_vehiculo),
        "por_cliente": _fmt(por_cliente),
        "por_mes": _fmt_mes(),
        "detalle": detalle,
    }


@app.post("/api/ruta")
def calcular_ruta(req: RutaRequest):
    """Km estimados (OSRM) + ruta completa PTV (distancia, tiempo, tráfico, peaje, polyline)."""
    puntos = [p.dict() for p in req.puntos]
    tramos, total, metodo, toll_km = _calc_ruta(puntos)
    resp = {"tramos": tramos, "total_km": total, "metodo": metodo, "total_toll_km": toll_km}
    if len(puntos) >= 2:
        ptv = _ptv_route(puntos, _vehiculo_ptv(req.terminal), req.conduccion_acumulada_min)
        if ptv:
            resp["ptv"] = ptv
    return resp




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


def _parse_ticket(texto):
    proveedor, fecha, total = "", None, None
    lines = [l.strip() for l in texto.splitlines() if l.strip()]
    if lines:
        proveedor = lines[0][:60]
    m = re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", texto)
    if m:
        fecha = _norm_fecha(m.group(0))
    m = re.search(r"(?i)(total|importe|a pagar|t\.?\s?p\.?)[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        total = _norm_total(m.group(2))
    if total is None:
        amts = re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{2}", texto)
        if amts:
            total = _norm_total(amts[-1])
    return proveedor, fecha, total


def _parse_documento(texto):
    """Extrae campos de factura/albarán: nº, CIF, base imponible, IVA."""
    num = ""
    m = re.search(r"(?i)(?:factura|albar[aá]n|fra\.?)\s*(?:n[ºo°]?\.?)?\s*[:#]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,30})", texto)
    if m:
        num = m.group(1).strip(" .:,-")
    cif = ""
    m = re.search(r"\b[A-Z]\d{7}[A-Z0-9]\b", texto)
    if not m:
        m = re.search(r"\b\d{8}[A-Z]\b", texto)
    if m:
        cif = m.group(0)
    base = None
    m = re.search(r"(?i)base\s*(?:imponible)?[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        base = _norm_total(m.group(1))
    iva = None
    m = re.search(r"(?i)\biva\b[^\d]{0,20}(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", texto)
    if m:
        iva = _norm_total(m.group(1))
    return num, cif, base, iva


@app.post("/api/ocr")
def ocr_ticket(req: OcrRequest):
    """Extrae proveedor/fecha/total de una foto de ticket o factura (Tesseract)."""
    data = req.imagen
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        img = base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "Imagen base64 inválida"})
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img)
        tmp = f.name
    texto = ""
    try:
        proc = subprocess.run(["tesseract", tmp, "stdout", "-l", "spa+eng"],
                              capture_output=True, text=True, timeout=60)
        texto = (proc.stdout or "").strip()
    except Exception:
        texto = ""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    proveedor, fecha, total = _parse_ticket(texto)
    num_factura, cif, base, iva = _parse_documento(texto)
    return {"texto": texto, "proveedor": proveedor, "fecha": fecha, "total": total,
            "numero": num_factura, "cif": cif, "base": base, "iva": iva}


@app.get("/api/export")
def export_xlsx(tipo: str = "trips"):
    """Descarga Excel (.xlsx) del histórico (trips), gastos o ingresos."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    conn = _db()
    wb = Workbook()
    ws = wb.active
    ws.title = (tipo or "hoja")[:28]

    HEADER_FONT = Font(bold=True, color="FFFFFF")
    HEADER_FILL = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")

    def _write(headers, data):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
        for r_idx, row in enumerate(data, 2):
            for c_idx, val in enumerate(row, 1):
                ws.cell(row=r_idx, column=c_idx, value=val)
        ws.freeze_panes = "A2"

    if tipo == "ingresos":
        rows = conn.execute(
            "SELECT terminal, cliente, precio, gastos, km_total, estado_pago, factura, creado "
            "FROM trips ORDER BY creado DESC"
        ).fetchall()
        data = []
        for r in rows:
            p = float(r["precio"] or 0)
            g = float(r["gastos"] or 0)
            data.append([r["terminal"], r["cliente"], p, g, round(p - g, 2),
                         r["km_total"], r["estado_pago"], r["factura"], r["creado"]])
        _write(["Vehículo", "Cliente", "Precio (€)", "Gastos (€)", "Margen (€)", "Km", "Cobro", "Factura", "Creado"], data)
    elif tipo == "gastos":
        rows = conn.execute(
            "SELECT g.fecha, g.terminal, g.categoria, p.nombre AS proveedor, p.cif AS proveedor_cif, "
            "g.concepto, g.importe, g.pagado FROM gastos g "
            "LEFT JOIN proveedores p ON p.id = g.proveedor_id ORDER BY g.fecha DESC"
        ).fetchall()
        data = [[r["fecha"], r["terminal"], r["categoria"],
                 (r["proveedor"] or "") + ((" (" + r["proveedor_cif"] + ")") if r["proveedor_cif"] else ""),
                 r["concepto"], float(r["importe"] or 0), "Sí" if r["pagado"] else "No"] for r in rows]
        _write(["Fecha", "Vehículo", "Categoría", "Proveedor", "Concepto", "Importe (€)", "Pagado"], data)
    elif tipo == "clientes":
        rows = conn.execute("SELECT nombre, cif, direccion, poblacion, cp, telefono, email FROM clientes ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Dirección", "Población", "CP", "Teléfono", "Email"],
               [[r["nombre"], r["cif"], r["direccion"], r["poblacion"], r["cp"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "conductores":
        rows = conn.execute("SELECT nombre, dni, telefono, email FROM conductores ORDER BY nombre").fetchall()
        _write(["Nombre", "DNI", "Teléfono", "Email"],
               [[r["nombre"], r["dni"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "vehiculos":
        rows = conn.execute(
            "SELECT categoria, id, matricula, marca, modelo, anno, itv, seguro, peaje_categoria, ejes, mma, "
            "clase_euro, capacidad_peso, capacidad_palets, coste_adquisicion, fecha_adquisicion, vida_util, valor_residual "
            "FROM vehiculos ORDER BY categoria, id"
        ).fetchall()
        _write(["Categoría", "ID", "Matrícula", "Marca", "Modelo", "Año", "ITV", "Seguro", "Peaje", "Ejes", "MMA", "Euro",
                "Cap. peso", "Cap. palets", "Coste", "Fecha adq.", "Vida útil", "Residual"],
               [[r["categoria"], r["id"], r["matricula"], r["marca"], r["modelo"], r["anno"], r["itv"], r["seguro"],
                 r["peaje_categoria"], r["ejes"], r["mma"], r["clase_euro"], r["capacidad_peso"], r["capacidad_palets"],
                 r["coste_adquisicion"], r["fecha_adquisicion"], r["vida_util"], r["valor_residual"]] for r in rows])
    elif tipo == "proveedores":
        rows = conn.execute("SELECT nombre, cif, direccion, poblacion, cp, telefono, email FROM proveedores ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Dirección", "Población", "CP", "Teléfono", "Email"],
               [[r["nombre"], r["cif"], r["direccion"], r["poblacion"], r["cp"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "transportistas":
        rows = conn.execute("SELECT nombre, cif, telefono, email, tarifa FROM transportistas ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Teléfono", "Email", "Tarifa €/km"],
               [[r["nombre"], r["cif"], r["telefono"], r["email"], float(r["tarifa"] or 0)] for r in rows])
    elif tipo == "categorias_gasto":
        rows = conn.execute("SELECT nombre FROM categorias_gasto ORDER BY nombre").fetchall()
        _write(["Categoría"], [[r["nombre"]] for r in rows])
    elif tipo == "tarifas_peaje":
        rows = conn.execute("SELECT categoria, eur_km FROM tarifas_peaje ORDER BY categoria").fetchall()
        _write(["Categoría", "€/km"], [[r["categoria"], float(r["eur_km"] or 0)] for r in rows])
    elif tipo == "costes_fijos":
        rows = conn.execute("SELECT terminal, concepto, importe FROM costes_fijos ORDER BY terminal").fetchall()
        _write(["Vehículo", "Concepto", "€/mes"], [[r["terminal"], r["concepto"], float(r["importe"] or 0)] for r in rows])
    elif tipo == "mantenimientos":
        rows = conn.execute(
            "SELECT m.fecha, v.matricula, m.tipo, m.km, m.coste, m.notas, m.hecho "
            "FROM mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id ORDER BY m.fecha DESC"
        ).fetchall()
        _write(["Fecha", "Vehículo", "Tipo", "Km", "Coste (€)", "Notas", "Hecho"],
               [[r["fecha"], r["matricula"], r["tipo"], r["km"], float(r["coste"] or 0), r["notas"],
                 "Sí" if r["hecho"] else "No"] for r in rows])
    elif tipo == "direcciones":
        rows = conn.execute(
            "SELECT nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario "
            "FROM direcciones ORDER BY ciudad, nombre, calle"
        ).fetchall()
        _write(["Nombre", "Empresa", "Calle", "Número", "Ciudad", "CP", "País", "Lat", "Lng", "Comentario"],
               [[r["nombre"], r["empresa"], r["calle"], r["numero"], r["ciudad"], r["cp"], r["pais"],
                 r["lat"], r["lng"], r["comentario"]] for r in rows])
    elif tipo == "liquidaciones":
        rows = conn.execute(
            "SELECT l.fecha, t.nombre AS transportista, l.concepto, l.importe, l.pagado "
            "FROM liquidaciones l LEFT JOIN transportistas t ON t.id=l.transportista_id ORDER BY l.fecha DESC"
        ).fetchall()
        _write(["Fecha", "Transportista", "Concepto", "Importe (€)", "Pagado"],
               [[r["fecha"], r["transportista"], r["concepto"], float(r["importe"] or 0),
                 "Sí" if r["pagado"] else "No"] for r in rows])
    elif tipo == "facturas":
        rows = conn.execute("SELECT numero, fecha, cliente_nombre, base, iva, cuota_iva, total, estado FROM facturas ORDER BY fecha DESC").fetchall()
        _write(["Nº", "Fecha", "Cliente", "Base", "IVA %", "Cuota IVA", "Total", "Estado"],
               [[r["numero"], r["fecha"], r["cliente_nombre"], float(r["base"] or 0), float(r["iva"] or 0),
                 float(r["cuota_iva"] or 0), float(r["total"] or 0), r["estado"]] for r in rows])
    elif tipo == "asientos":
        rows = conn.execute(
            "SELECT a.numero, a.fecha, a.concepto, a.origen, a.documento, p.cuenta, c.nombre AS cuenta_nombre, p.debe, p.haber "
            "FROM asientos a JOIN apuntes p ON p.asiento_id=a.id JOIN cuentas c ON c.codigo=p.cuenta "
            "ORDER BY a.fecha DESC, a.numero DESC, p.id"
        ).fetchall()
        _write(["Nº", "Fecha", "Concepto", "Origen", "Documento", "Cuenta", "Descripción", "Debe", "Haber"],
               [[r["numero"], r["fecha"], r["concepto"], r["origen"], r["documento"], r["cuenta"],
                 r["cuenta_nombre"], float(r["debe"] or 0), float(r["haber"] or 0)] for r in rows])
    elif tipo == "balance":
        rows = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
            "FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
            "GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden, p.cuenta"
        ).fetchall()
        _write(["Cuenta", "Nombre", "Debe", "Haber", "Saldo"],
               [[r["cuenta"], r["nombre"], round(float(r["debe"] or 0), 2), round(float(r["haber"] or 0), 2),
                 round(float(r["debe"] or 0) - float(r["haber"] or 0), 2)] for r in rows])
    elif tipo == "pyg":
        gastos = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.debe)-SUM(p.haber) AS importe "
            "FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
            "WHERE c.tipo='gasto' GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden"
        ).fetchall()
        ingresos = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.haber)-SUM(p.debe) AS importe "
            "FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
            "WHERE c.tipo='ingreso' GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden"
        ).fetchall()
        data = [["GASTOS", "", ""]]
        data += [[r["cuenta"], r["nombre"], round(float(r["importe"] or 0), 2)] for r in gastos]
        data += [["INGRESOS", "", ""]]
        data += [[r["cuenta"], r["nombre"], round(float(r["importe"] or 0), 2)] for r in ingresos]
        _write(["Cuenta", "Concepto", "Importe"], data)
    elif tipo == "empleados":
        rows = conn.execute(
            "SELECT nombre, apellidos, dni, categoria, puesto, tipo_contrato, jornada, fecha_alta, fecha_baja, "
            "salario_bruto, irpf, disponibilidad, banco, iban, telefono, email FROM empleados ORDER BY nombre"
        ).fetchall()
        _write(["Nombre", "Apellidos", "DNI/NIF", "Categoría", "Puesto", "Contrato", "Jornada", "Alta", "Baja",
                "Salario bruto (€)", "IRPF %", "Disponibilidad", "Banco", "IBAN", "Teléfono", "Email"],
               [[r["nombre"], r["apellidos"], r["dni"], r["categoria"], r["puesto"], r["tipo_contrato"], r["jornada"],
                 r["fecha_alta"], r["fecha_baja"], float(r["salario_bruto"] or 0), float(r["irpf"] or 0),
                 r["disponibilidad"], r["banco"], r["iban"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "nominas":
        rows = conn.execute(
            "SELECT n.periodo, e.nombre, e.apellidos, n.salario_bruto, n.irpf_pct, n.ss_trabajador, n.irpf_importe, "
            "n.neto, n.ss_empresa, n.coste_empresa, n.estado, n.pagado FROM nominas n "
            "JOIN empleados e ON e.id=n.empleado_id ORDER BY n.periodo DESC, n.id DESC"
        ).fetchall()
        _write(["Periodo", "Nombre", "Apellidos", "Salario bruto", "IRPF %", "SS trabajador", "IRPF", "Neto",
                "SS empresa", "Coste empresa", "Estado", "Pagado"],
               [[r["periodo"], r["nombre"], r["apellidos"], float(r["salario_bruto"] or 0), float(r["irpf_pct"] or 0),
                 float(r["ss_trabajador"] or 0), float(r["irpf_importe"] or 0), float(r["neto"] or 0),
                 float(r["ss_empresa"] or 0), float(r["coste_empresa"] or 0), r["estado"],
                 "Sí" if r["pagado"] else "No"] for r in rows])
    elif tipo == "ausencias":
        rows = conn.execute(
            "SELECT e.nombre, e.apellidos, a.tipo, a.fecha_inicio, a.fecha_fin, a.dias, a.estado, a.nota "
            "FROM ausencias a JOIN empleados e ON e.id=a.empleado_id ORDER BY a.fecha_inicio DESC"
        ).fetchall()
        _write(["Nombre", "Apellidos", "Tipo", "Inicio", "Fin", "Días", "Estado", "Nota"],
               [[r["nombre"], r["apellidos"], r["tipo"], r["fecha_inicio"], r["fecha_fin"], float(r["dias"] or 0),
                 r["estado"], r["nota"]] for r in rows])
    else:  # trips
        rows = conn.execute("SELECT * FROM trips ORDER BY creado DESC").fetchall()
        cols = ["referencia", "id", "nombre", "cliente", "terminal", "conductor", "tipo_carga", "origen", "destino",
                "tareas", "estado", "estado_pago", "factura", "precio", "gastos", "km_total", "iva", "creado"]
        data = [[r[c] if c in r.keys() else "" for c in cols] for r in rows]
        _write(cols, data)

    conn.close()

    # Ajustar ancho de columnas al contenido
    for col_cells in ws.columns:
        max_len = 0
        letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[letter].width = min(max_len + 2, 60)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="tms_{tipo}.xlsx"'},
    )


@app.post("/api/sync/files")
def sync_files():
    try:
        _sync_files()
        _sync_mensajes()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e)})


@app.post("/api/sync/mensajes")
def sync_mensajes():
    """Sync ligero: solo mensajería (estructurados + libres), para refrescar el chat con poco delay."""
    try:
        _sync_mensajes()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e)})






@app.get("/api/proveedores")
def list_proveedores():
    conn = _db()
    rows = conn.execute("SELECT * FROM proveedores WHERE COALESCE(borrado, false) = false ORDER BY nombre").fetchall()
    conn.close()
    return {"proveedores": [dict(r) for r in rows]}


@app.post("/api/proveedores")
def add_proveedor(p: Proveedor):
    conn = _db()
    conn.execute(
        "INSERT INTO proveedores (nombre, cif, direccion, poblacion, cp, telefono, email, cuenta_contable_defecto) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (p.nombre, p.cif, p.direccion, p.poblacion, p.cp, p.telefono, p.email, p.cuenta_contable_defecto),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/proveedores/{prov_id}")
def del_proveedor(prov_id: int, user: dict = Depends(require_role(["admin", "dispatcher"]))):
    conn = _db()
    row = conn.execute("SELECT * FROM proveedores WHERE id=?", (prov_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    quien = user.get("usuario") or user.get("rol") or "sistema"
    conn.execute("UPDATE proveedores SET borrado=true, borrado_por=?, borrado_en=? WHERE id=?",
                 (quien, datetime.datetime.utcnow().isoformat() + "Z", prov_id))
    _auditar(conn, "proveedores", prov_id, "eliminar", quien, antes=dict(row))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/proveedores/{prov_id}")
def upd_proveedor(prov_id: int, body: dict):
    allow = ("nombre", "cif", "direccion", "poblacion", "cp", "telefono", "email", "cuenta_contable_defecto")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE proveedores SET {sets} WHERE id=?", (*fields.values(), prov_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/gastos")
def list_gastos(terminal: str = "", categoria: str = "", desde: str = "", hasta: str = ""):
    conn = _db()
    query = ("SELECT g.*, p.nombre AS proveedor, p.cif AS proveedor_cif "
             "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id = p.id")
    conds, params = [], []
    if terminal:
        conds.append("g.terminal = ?")
        params.append(terminal)
    if categoria:
        conds.append("g.categoria = ?")
        params.append(categoria)
    if desde:
        conds.append("substr(g.fecha, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(g.fecha, 1, 10) <= ?")
        params.append(hasta)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY g.fecha DESC, g.id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"gastos": [dict(r) for r in rows]}


@app.post("/api/gastos")
def add_gasto(g: Gasto):
    conn = _db()
    # importe = total (IVA incluido); se deriva base y cuota de IVA soportado
    total = round(float(g.importe or 0), 2)
    iva_pct = round(float(g.iva or 21), 2)
    ret_pct = round(float(g.retencion or 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    cuota = round(total - base, 2)
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    cuenta = (g.cuenta or "").strip() or _categoria_cuenta(conn, g.categoria)
    cur = conn.execute(
        "INSERT INTO gastos (terminal, trip_id, categoria, fecha, importe, concepto, foto, creado, proveedor_id, iva, retencion, cuenta) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (g.terminal, g.trip_id, g.categoria, g.fecha, g.importe, g.concepto, g.foto,
         datetime.datetime.utcnow().isoformat() + "Z", g.proveedor_id, iva_pct, ret_pct, cuenta),
    )
    gasto_id = cur.fetchone()["id"]
    if total != 0:
        lineas = [(cuenta, base, 0, g.concepto or g.categoria or "Gasto")]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("410", 0, a_pagar, g.concepto or g.categoria or "Gasto"))
        if retencion > 0:
            lineas.append(("4751", 0, retencion, "Retención IRPF"))
        try:
            _post_asiento((g.fecha or "")[:10], f"Gasto {g.categoria or 'Otros'}: {g.concepto or ''}",
                          lineas, origen="gasto", gasto_id=gasto_id, conn=conn)
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    conn.close()
    return {"ok": True, "gasto_id": gasto_id}


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


# ======================================================================
# Gastos operativos de vehículo (combustible/peajes) + OCR de facturas
# ======================================================================



def _pdf_a_texto(raw: bytes) -> str:
    """Extrae el texto de la capa de texto de un PDF (pdfplumber)."""
    try:
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join((page.extract_text() or "") for page in pdf.pages)
    except Exception:
        return ""


def _regex_matricula(t):
    # Formato moderno: 1234 ABC ; fallback formato antiguo: A 1234 AB
    m = re.search(r"\b(\d{4})\s?([A-Z]{3})\b", t)
    if not m:
        m = re.search(r"\b([A-Z]{1,2})\s?(\d{4})\s?([A-Z]{2})\b", t)
    return m.group(0).replace(" ", "") if m else ""


def _regex_litros(t):
    m = re.search(r"(\d+[.,]?\d*)\s*(?:L|Lts?|Litros|litros)\b", t)
    if not m:
        return None
    return round(float(m.group(1).replace(",", ".")), 2)


def _regex_importe(t):
    m = re.search(r"(?:total|importe total|€|eur)\s*:?\s*([0-9][0-9.,]*\d)", t, re.I)
    if not m:
        return None
    s = m.group(1).replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _regex_fecha(t):
    m = re.search(r"\b(\d{2})[/.-](\d{2})[/.-](\d{4})\b", t)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


@app.post("/api/gastos/ocr")
def gastos_ocr(req: dict):
    b64 = (req.get("archivo_base64") or req.get("file_base64") or "").strip()
    if not b64:
        raise HTTPException(status_code=400, detail={"error": "Sin archivo (archivo_base64)."})
    if b64.startswith("data:") and "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "Base64 inválido."})
    texto = _pdf_a_texto(raw)
    return {
        "ok": True,
        "borrador": {
            "matricula": _regex_matricula(texto),
            "litros": _regex_litros(texto),
            "importe_total": _regex_importe(texto),
            "fecha": _regex_fecha(texto),
        },
        "archivo_base64": b64,
        "texto_extraido": texto[:2000],
    }


@app.post("/api/gastos/vehiculos")
def add_gasto_vehiculo(g: GastoVehiculo):
    tipo = (g.tipo or "combustible").lower()
    if tipo not in _TIPO_GASTO_CUENTA:
        raise HTTPException(status_code=400, detail={"error": "Tipo inválido."})
    iva_pct = round(float(g.iva or 21), 2)
    importe = round(float(g.importe_total or 0), 2)
    base = round(float(g.base_imponible or 0), 2)
    # Si solo viene el total (p. ej. flujo OCR), se deriva la base.
    if base <= 0 and importe > 0:
        base = round(importe / (1 + iva_pct / 100.0), 2) if iva_pct > 0 else importe
    if importe <= 0 and base > 0:
        importe = round(base * (1 + iva_pct / 100.0), 2)
    cuota = round(importe - base, 2)
    litros = round(float(g.litros or 0), 2)
    cuenta = (g.cuenta_contable_gasto or "").strip() or _TIPO_GASTO_CUENTA[tipo]
    estado = (g.estado_pago or "Pendiente").strip() or "Pendiente"
    conn = _db()
    cur = conn.execute(
        "INSERT INTO gastos_vehiculos (vehiculo_id, proveedor_id, fecha, tipo, litros, base_imponible, iva, "
        "importe_total, factura_ref, cuenta_contable_gasto, estado_pago, archivo_base64, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (g.vehiculo_id, g.proveedor_id, g.fecha, tipo, litros, base, iva_pct, importe,
         g.factura_ref, cuenta, estado, g.archivo_base64, datetime.datetime.utcnow().isoformat() + "Z"),
    )
    gasto_id = cur.fetchone()["id"]
    if importe > 0:
        # Debe: cuenta de gasto (base) + 472 IVA soportado (cuota) · Haber: 400 Proveedores (total)
        label = tipo.capitalize()
        concepto = f"{label} {g.factura_ref or ''}".strip()
        lineas = [(cuenta, base, 0, concepto)]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("400", 0, importe, "Proveedor"))
        try:
            _registrar_asiento((g.fecha or "")[:10], f"Gasto {label}: {concepto}", lineas,
                               origen="Gasto_Vehiculo", origen_id=str(gasto_id), conn=conn)
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    conn.close()
    return {"ok": True, "gasto_id": gasto_id}


@app.get("/api/gastos/vehiculos")
def list_gastos_vehiculos():
    conn = _db()
    rows = conn.execute(
        "SELECT g.id, g.vehiculo_id, g.proveedor_id, g.fecha, g.tipo, g.litros, "
        "g.importe_total, g.factura_ref, g.creado, "
        "COALESCE(v.matricula,'') AS matricula, COALESCE(p.nombre,'') AS proveedor "
        "FROM gastos_vehiculos g "
        "LEFT JOIN vehiculos v ON v.id = g.vehiculo_id "
        "LEFT JOIN proveedores p ON p.id = g.proveedor_id "
        "ORDER BY g.fecha DESC, g.id DESC LIMIT 500"
    ).fetchall()
    conn.close()
    return {"gastos": [dict(r) for r in rows]}


@app.get("/api/gastos/vehiculos/{gasto_id}")
def get_gasto_vehiculo(gasto_id: int):
    conn = _db()
    row = conn.execute("SELECT * FROM gastos_vehiculos WHERE id=?", (gasto_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    return dict(row)


@app.patch("/api/gastos/vehiculos/{gasto_id}")
def upd_gasto_vehiculo(gasto_id: int, body: dict):
    """Edición en línea segura: solo estado de pago y referencia (no toca importes/contabilidad)."""
    allow = ("estado_pago", "factura_ref")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE gastos_vehiculos SET {sets} WHERE id=?", (*fields.values(), gasto_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/kpis/rentabilidad-flota")
def rentabilidad_flota(desde: str = "", hasta: str = ""):
    """Rentabilidad por vehículo: cruza ingresos (trips Entregado) con gastos (gastos_vehiculos).

    Por defecto filtra el mes en curso. Devuelve por tractora: vehiculo_id, matricula,
    total_ingresos, total_gastos, margen_neto y margen_porcentaje.
    """
    hoy = datetime.date.today()
    if not desde:
        desde = f"{hoy.year:04d}-{hoy.month:02d}-01"
    if not hasta:
        nxt = hoy.replace(day=28) + datetime.timedelta(days=4)
        hasta = (nxt - datetime.timedelta(days=nxt.day)).isoformat()
    conn = _db()
    rows = conn.execute(
        "WITH ingresos AS ("
        "  SELECT terminal AS vehiculo_id, COALESCE(SUM(precio),0) AS ing "
        "  FROM trips "
        "  WHERE LOWER(COALESCE(estado,'')) = 'entregado' "
        "    AND substr(COALESCE(fecha_actualizacion, creado),1,10) >= ? "
        "    AND substr(COALESCE(fecha_actualizacion, creado),1,10) <= ? "
        "  GROUP BY terminal"
        "), gastos AS ("
        "  SELECT vehiculo_id, COALESCE(SUM(importe_total),0) AS gas "
        "  FROM gastos_vehiculos "
        "  WHERE substr(COALESCE(fecha,''),1,10) >= ? AND substr(COALESCE(fecha,''),1,10) <= ? "
        "  GROUP BY vehiculo_id"
        ") "
        "SELECT v.id AS vehiculo_id, COALESCE(v.matricula,'') AS matricula, "
        "       COALESCE(i.ing,0) AS total_ingresos, COALESCE(g.gas,0) AS total_gastos "
        "FROM vehiculos v "
        "LEFT JOIN ingresos i ON i.vehiculo_id = v.id "
        "LEFT JOIN gastos g ON g.vehiculo_id = v.id "
        "WHERE COALESCE(i.ing,0) <> 0 OR COALESCE(g.gas,0) <> 0 "
        "ORDER BY (COALESCE(i.ing,0) - COALESCE(g.gas,0)) DESC",
        (desde, hasta, desde, hasta),
    ).fetchall()
    conn.close()
    flota = []
    for r in rows:
        ing = float(r["total_ingresos"] or 0)
        gas = float(r["total_gastos"] or 0)
        margen = round(ing - gas, 2)
        pct = round((margen / ing) * 100, 2) if ing > 0 else None
        flota.append({
            "vehiculo_id": r["vehiculo_id"],
            "matricula": r["matricula"] or "",
            "total_ingresos": round(ing, 2),
            "total_gastos": round(gas, 2),
            "margen_neto": margen,
            "margen_porcentaje": pct,
        })
    return {"desde": desde, "hasta": hasta, "flota": flota}


@app.post("/api/gastos/{gasto_id}/pagar")
def pagar_gasto(gasto_id: int):
    """Marca un gasto como pagado: Debe 410 / Haber 572 por el importe a pagar."""
    conn = _db()
    g = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    if not g:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    if g["pagado"]:
        conn.close()
        return {"ok": True, "ya_pagado": True}
    total = round(float(g["importe"] or 0), 2)
    iva_pct = round(float(g["iva"] or 21), 2)
    ret_pct = round(float(g["retencion"] or 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    fecha = (g["fecha"] or "")[:10] or datetime.date.today().isoformat()
    if a_pagar != 0:
        _post_asiento(
            fecha, f"Pago gasto {g['concepto'] or g['categoria'] or gasto_id}",
            [("410", a_pagar, 0, "Pago acreedor"),
             ("572", 0, a_pagar, "Pago gasto")],
            origen="pago", gasto_id=gasto_id, conn=conn,
        )
    conn.execute("UPDATE gastos SET pagado=true WHERE id=?", (gasto_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "a_pagar": a_pagar}


@app.delete("/api/gastos/{gasto_id}")
def del_gasto(gasto_id: int):
    conn = _db()
    conn.execute("DELETE FROM asientos WHERE gasto_id=?", (gasto_id,))
    conn.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/gastos/{gasto_id}")
def upd_gasto(gasto_id: int, g: Gasto):
    conn = _db()
    existing = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    total = round(float(g.importe or 0), 2)
    iva_pct = round(float(existing["iva"] if existing["iva"] is not None else 21), 2)
    ret_pct = round(float(existing["retencion"] if existing["retencion"] is not None else 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    cuota = round(total - base, 2)
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    cuenta = (g.cuenta or "").strip() or _categoria_cuenta(conn, g.categoria)
    conn.execute(
        "UPDATE gastos SET terminal=?, categoria=?, fecha=?, importe=?, concepto=?, proveedor_id=?, cuenta=? WHERE id=?",
        (g.terminal, g.categoria, g.fecha, g.importe, g.concepto, g.proveedor_id, cuenta, gasto_id),
    )
    conn.execute("DELETE FROM asientos WHERE origen='gasto' AND gasto_id=?", (gasto_id,))
    if total != 0:
        lineas = [(cuenta, base, 0, g.concepto or g.categoria or "Gasto")]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("410", 0, a_pagar, g.concepto or g.categoria or "Gasto"))
        if retencion > 0:
            lineas.append(("4751", 0, retencion, "Retención IRPF"))
        try:
            _post_asiento((g.fecha or "")[:10], f"Gasto {g.categoria or 'Otros'}: {g.concepto or ''}",
                          lineas, origen="gasto", gasto_id=gasto_id, conn=conn)
        except ValueError as e:
            conn.rollback()
            conn.close()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/gastos/resumen")
def resumen_gastos(desde: str = "", hasta: str = ""):
    conn = _db()
    where, conds, params = "", [], []
    if desde:
        conds.append("substr(fecha, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(fecha, 1, 10) <= ?")
        params.append(hasta)
    if conds:
        where = " WHERE " + " AND ".join(conds)
    cat_rows = conn.execute(
        f"SELECT categoria, SUM(importe) AS total, COUNT(*) AS n FROM gastos{where} GROUP BY categoria ORDER BY total DESC",
        params,
    ).fetchall()
    prov_rows = conn.execute(
        f"SELECT p.id, p.nombre, p.cif, SUM(g.importe) AS total, COUNT(*) AS n "
        f"FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id = p.id{where} "
        f"GROUP BY p.id ORDER BY total DESC",
        params,
    ).fetchall()
    conn.close()
    return {
        "resumen": [
            {"categoria": r["categoria"] or "Sin categoría", "total": round(r["total"] or 0, 2), "n": r["n"]}
            for r in cat_rows
        ],
        "por_proveedor": [
            {"id": r["id"], "nombre": r["nombre"] or "Sin proveedor", "cif": r["cif"] or "",
             "total": round(r["total"] or 0, 2), "n": r["n"]}
            for r in prov_rows
        ],
    }


@app.get("/api/costes-fijos")
def list_costes():
    conn = _db()
    rows = conn.execute("SELECT * FROM costes_fijos ORDER BY terminal, concepto").fetchall()
    conn.close()
    return {"costes": [dict(r) for r in rows]}




@app.post("/api/costes-fijos")
def add_coste(c: CosteFijo):
    conn = _db()
    conn.execute("INSERT INTO costes_fijos (terminal, concepto, importe) VALUES (?,?,?)",
                 (c.terminal, c.concepto, c.importe))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/costes-fijos/{coste_id}")
def del_coste(coste_id: int):
    conn = _db()
    conn.execute("DELETE FROM costes_fijos WHERE id=?", (coste_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/trips/{trip_id}/files")
def trip_files(trip_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, ftype, ftime, source, driver, lid, content_b64 "
        "FROM files WHERE trip_id=? ORDER BY ftime DESC", (trip_id,)
    ).fetchall()
    conn.close()
    return {"archivos": [dict(r) for r in rows]}


@app.get("/api/trips/{trip_id}/tramos")
def trip_tramos(trip_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT id, orden, origen_nombre, origen_ciudad, origen_lat, origen_lng, "
        "destino_nombre, destino_ciudad, destino_lat, destino_lng, terminal, conductor, "
        "km_total, km_real, km_fuente, estado, fecha_carga, fecha_descarga "
        "FROM tramos WHERE trip_id=? ORDER BY orden", (trip_id,)
    ).fetchall()
    conn.close()
    return {"tramos": [dict(r) for r in rows]}


@app.get("/api/trips/{trip_id}/mensajes")
def trip_mensajes(trip_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT id, tipo, messagetype, originid, source, subject, body, time, needreply "
        "FROM mensajes WHERE trip_id=? ORDER BY time DESC", (trip_id,)
    ).fetchall()
    conn.close()
    return {"mensajes": [dict(r) for r in rows]}




@app.post("/api/trips/{trip_id}/mensajes")
def send_trip_mensaje(trip_id: str, req: SendMensajeRequest):
    conn = _db()
    row = conn.execute("SELECT terminal FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    terminal = (row["terminal"] if row else "") or ""
    if not terminal:
        return {"ok": False, "error": "El viaje no tiene terminal asignado."}
    subject = (req.subject or "").strip()
    body = (req.body or "").strip()
    if not subject and not body:
        return {"ok": False, "error": "El mensaje está vacío."}

    # hilo: responder al último mensaje del chat de este viaje (para no abrir un hilo nuevo)
    conn = _db()
    row = conn.execute(
        "SELECT id FROM mensajes WHERE trip_id=? AND tipo IN ('enviado','estructurado','libre') "
        "ORDER BY creado DESC LIMIT 1", (trip_id,)
    ).fetchone()
    conn.close()
    parent_id = row["id"] if row else None

    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_message(terminal, subject, body, req.needreply, message_id=msg_id, originid=parent_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    _save_mensaje(msg_id, trip_id, "enviado", "", parent_id, "TMS", subject, body,
                  datetime.datetime.utcnow().isoformat(), req.needreply)
    return {"ok": True, "id": msg_id, "terminal": terminal}


# ------------------------------------------------------------------ #
# Mensajería independiente (sección del Ribbon, sin viaje asociado)   #
# ------------------------------------------------------------------ #
@app.get("/api/mensajeria/terminales")
def mensajeria_terminales():
    """Lista los terminales APP (Fleet XPS) disponibles para mensajería."""
    conn = _db()
    rows = conn.execute(
        "SELECT DISTINCT app_terminal AS id FROM vehiculos "
        "WHERE app_terminal IS NOT NULL AND app_terminal<>'' ORDER BY app_terminal"
    ).fetchall()
    conn.close()
    terminales = [dict(r) for r in rows]
    default = config.DEFAULT_TRIMBLE_TERMINAL
    if default and not any(t["id"] == default for t in terminales):
        terminales.insert(0, {"id": default})
    return {"ok": True, "terminales": terminales}


@app.get("/api/mensajeria/{terminal}/mensajes")
def mensajeria_mensajes(terminal: str):
    """Mensajes de un terminal (recibidos: source=terminal; enviados: terminal=terminal)."""
    conn = _db()
    rows = conn.execute(
        "SELECT id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, terminal "
        "FROM mensajes WHERE source=? OR terminal=? ORDER BY time LIMIT 300",
        (terminal, terminal),
    ).fetchall()
    conn.close()
    return {"ok": True, "terminal": terminal, "mensajes": [dict(r) for r in rows]}


@app.post("/api/mensajeria/{terminal}/mensajes")
def mensajeria_enviar(terminal: str, req: SendMensajeRequest):
    """Envía un mensaje libre a un terminal APP (sin viaje asociado)."""
    subject = (req.subject or "").strip()
    body = (req.body or "").strip()
    if not subject and not body:
        return {"ok": False, "error": "El mensaje está vacío."}
    conn = _db()
    row = conn.execute(
        "SELECT id FROM mensajes WHERE source=? OR terminal=? ORDER BY creado DESC LIMIT 1",
        (terminal, terminal),
    ).fetchone()
    conn.close()
    parent_id = row["id"] if row else None
    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_message(terminal, subject, body, req.needreply,
                                     message_id=msg_id, originid=parent_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    conn = _db()
    conn.execute(
        "INSERT INTO mensajes (id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, terminal, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
        (msg_id, None, "enviado", "", parent_id, "TMS", subject, body,
         datetime.datetime.utcnow().isoformat() + "Z", req.needreply, terminal,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": msg_id, "terminal": terminal}


@app.post("/api/mensajeria/{terminal}/adjuntos")
def mensajeria_adjunto(terminal: str, req: dict):
    """Envía un archivo (base64) al terminal APP vía DMS."""
    nombre = (req.get("nombre") or "").strip()
    contenido = (req.get("contenido") or "").strip()
    if not nombre or not contenido:
        return {"ok": False, "error": "Falta nombre o contenido del archivo."}
    if len(contenido) > MAX_DOC_B64:
        return {"ok": False, "error": "El archivo supera ~2 MB."}
    res = get_client().create_document(nombre, contenido, "pdf", True)
    if not res.get("ok") or not res.get("uniqueDocId"):
        return {"ok": False, "error": res.get("error") or "No se pudo subir el documento."}
    get_client().assign_documents([res["uniqueDocId"]], [terminal])
    return {"ok": True, "fileKey": res["uniqueDocId"], "nombre": nombre}


@app.post("/api/trips/{trip_id}/questionpath")
def send_trip_questionpath(trip_id: str, req: dict):
    """Envía un question path (mensaje estructurado) al terminal del viaje."""
    conn = _db()
    row = conn.execute("SELECT terminal FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    terminal = (row["terminal"] if row else "") or ""
    if not terminal:
        return {"ok": False, "error": "El viaje no tiene terminal asignado."}
    subject = (req.get("subject") or "").strip()
    body = (req.get("body") or "").strip()
    messagetype = (req.get("messagetype") or "").strip()
    if not body:
        return {"ok": False, "error": "El question path está vacío."}
    if not messagetype:
        return {"ok": False, "error": "Selecciona el messagetype del question path."}
    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_structured_message(terminal, subject, body, messagetype, needreply=False, message_id=msg_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    _save_mensaje(msg_id, trip_id, "enviado", messagetype, None, "TMS", subject, body,
                  datetime.datetime.utcnow().isoformat(), False)
    return {"ok": True, "id": msg_id, "terminal": terminal}


@app.get("/api/messagetypes")
def list_messagetypes():
    """messagetype válidos configurados en FleetWorks (descubiertos de los mensajes recibidos)."""
    conn = _db()
    rows = conn.execute(
        "SELECT DISTINCT messagetype FROM mensajes WHERE COALESCE(messagetype,'') <> '' ORDER BY messagetype"
    ).fetchall()
    conn.close()
    return {"messagetypes": [r["messagetype"] for r in rows]}


@app.get("/api/trips/{trip_id}/paradas")
def trip_paradas(trip_id: str):
    conn = _db()
    rows = conn.execute("SELECT * FROM paradas WHERE trip_id=? ORDER BY orden", (trip_id,)).fetchall()
    conn.close()
    return {"paradas": [dict(r) for r in rows]}








@app.get("/api/clientes")
def list_clientes():
    conn = _db()
    rows = conn.execute("SELECT * FROM clientes WHERE COALESCE(borrado, false) = false ORDER BY nombre").fetchall()
    conn.close()
    return {"clientes": [dict(r) for r in rows]}


@app.post("/api/clientes")
def add_cliente(c: Cliente):
    conn = _db()
    conn.execute(
        "INSERT INTO clientes (nombre, cif, direccion, poblacion, cp, telefono, email, cuenta_contable_defecto) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (c.nombre, c.cif, c.direccion, c.poblacion, c.cp, c.telefono, c.email, c.cuenta_contable_defecto),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/clientes/{cli_id}")
def del_cliente(cli_id: int, user: dict = Depends(require_role(["admin", "dispatcher"]))):
    conn = _db()
    row = conn.execute("SELECT * FROM clientes WHERE id=?", (cli_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Cliente no encontrado"})
    quien = user.get("usuario") or user.get("rol") or "sistema"
    conn.execute("UPDATE clientes SET borrado=true, borrado_por=?, borrado_en=? WHERE id=?",
                 (quien, datetime.datetime.utcnow().isoformat() + "Z", cli_id))
    _auditar(conn, "clientes", cli_id, "eliminar", quien, antes=dict(row))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/clientes/{cli_id}")
def upd_cliente(cli_id: int, body: dict):
    allow = ("nombre", "cif", "direccion", "poblacion", "cp", "telefono", "email", "cuenta_contable_defecto")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE clientes SET {sets} WHERE id=?", (*fields.values(), cli_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/conductores")
def list_conductores(fecha_esperada_carga: str = ""):
    """Conductores con disponibilidad según ausencias_empleados para la fecha de carga indicada."""
    conn = _db()
    fecha = (fecha_esperada_carga or "")[:10]
    if fecha:
        rows = conn.execute(
            """
            WITH aus AS (
                SELECT DISTINCT ON (empleado_id) empleado_id, tipo
                FROM ausencias_empleados
                WHERE ? >= fecha_inicio AND ? <= fecha_fin
                ORDER BY empleado_id, fecha_inicio
            )
            SELECT c.*, (aus.empleado_id IS NULL) AS disponible, aus.tipo AS motivo_ausencia
            FROM conductores c
            LEFT JOIN aus ON aus.empleado_id = c.empleado_id
            ORDER BY c.nombre
            """,
            (fecha, fecha),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, true AS disponible, NULL AS motivo_ausencia FROM conductores c ORDER BY c.nombre"
        ).fetchall()
    conn.close()
    return {"conductores": [dict(r) for r in rows]}


@app.post("/api/conductores")
def add_conductor(c: Conductor):
    conn = _db()
    conn.execute(
        "INSERT INTO conductores (nombre, dni, telefono, email) VALUES (?,?,?,?)",
        (c.nombre, c.dni, c.telefono, c.email),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/conductores/{con_id}")
def del_conductor(con_id: int):
    conn = _db()
    conn.execute("DELETE FROM conductores WHERE id=?", (con_id,))
    conn.commit()
    conn.close()
    return {"ok": True}




@app.get("/api/direcciones")
def list_direcciones():
    conn = _db()
    rows = conn.execute("SELECT * FROM direcciones ORDER BY ciudad, nombre, calle").fetchall()
    conn.close()
    return {"direcciones": [dict(r) for r in rows]}


def _direccion_dict(r, tipo="direccion"):
    return {"id": r["id"], "tipo": tipo, "nombre": r["nombre"] or r["empresa"] or r["calle"],
            "empresa": r["empresa"], "calle": r["calle"], "numero": r["numero"], "ciudad": r["ciudad"],
            "cp": r["cp"], "pais": r["pais"], "lat": r["lat"], "lng": r["lng"]}


def _buscar_photon(q, limit=5):
    """Geocodifica con Photon (OpenStreetMap) — gratis, sin API key. Devuelve sugerencias externas."""
    import urllib.parse
    import urllib.request
    import json
    url = "https://photon.komoot.io/api/?q=" + urllib.parse.quote(q) + f"&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {}) or {}
        coords = (f.get("geometry", {}) or {}).get("coordinates") or [None, None]  # [lng, lat]
        out.append({
            "id": None, "tipo": "externo",
            "nombre": p.get("name") or "",
            "empresa": p.get("name") or "",
            "calle": " ".join(x for x in [p.get("street"), p.get("housenumber")] if x),
            "numero": p.get("housenumber") or "",
            "ciudad": p.get("city") or "",
            "cp": p.get("postcode") or "",
            "pais": (p.get("countrycode") or "ES").upper(),
            "lat": coords[1], "lng": coords[0],
        })
    return out


@app.get("/api/direcciones/buscar")
def buscar_direcciones(q: str = ""):
    """Buscador de origen/destino: primero maestros (direcciones + clientes), luego Photon (externo)."""
    q = (q or "").strip()
    if not q:
        conn = _db()
        rows = conn.execute("SELECT * FROM direcciones ORDER BY ciudad, nombre LIMIT 20").fetchall()
        conn.close()
        return {"sugerencias": [_direccion_dict(r) for r in rows]}

    like = f"%{q}%"
    conn = _db()
    out = []
    rows = conn.execute(
        "SELECT * FROM direcciones WHERE nombre ILIKE ? OR empresa ILIKE ? OR calle ILIKE ? OR ciudad ILIKE ? OR cp ILIKE ? "
        "ORDER BY ciudad, nombre LIMIT 8",
        (like, like, like, like, like),
    ).fetchall()
    for r in rows:
        out.append(_direccion_dict(r))
    cli = conn.execute(
        "SELECT id, nombre, direccion, poblacion, cp FROM clientes WHERE nombre ILIKE ? OR cif ILIKE ? ORDER BY nombre LIMIT 5",
        (like, like),
    ).fetchall()
    for c in cli:
        out.append({"id": None, "tipo": "cliente", "nombre": c["nombre"], "empresa": c["nombre"],
                    "calle": c["direccion"] or "", "numero": "", "ciudad": c["poblacion"] or "",
                    "cp": c["cp"] or "", "pais": "ES", "lat": None, "lng": None})
    conn.close()
    out.extend(_buscar_photon(q))
    return {"sugerencias": out}


@app.post("/api/direcciones")
def add_direccion(d: DireccionMaestro):
    conn = _db()
    cur = conn.execute(
        "INSERT INTO direcciones (nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (d.nombre, d.empresa, d.calle, d.numero, d.ciudad, d.cp, d.pais, d.lat, d.lng, d.comentario,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    nuevo_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"ok": True, "id": nuevo_id}


@app.patch("/api/direcciones/{did}")
def upd_direccion(did: int, d: DireccionMaestro):
    conn = _db()
    conn.execute(
        "UPDATE direcciones SET nombre=?, empresa=?, calle=?, numero=?, ciudad=?, cp=?, pais=?, lat=?, lng=?, comentario=? WHERE id=?",
        (d.nombre, d.empresa, d.calle, d.numero, d.ciudad, d.cp, d.pais, d.lat, d.lng, d.comentario, did),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/direcciones/{did}")
def del_direccion(did: int):
    conn = _db()
    conn.execute("DELETE FROM direcciones WHERE id=?", (did,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/conductores/{con_id}")
def upd_conductor(con_id: int, c: Conductor):
    conn = _db()
    conn.execute("UPDATE conductores SET nombre=?, dni=?, telefono=?, email=? WHERE id=?",
                 (c.nombre, c.dni, c.telefono, c.email, con_id))
    conn.commit()
    conn.close()
    return {"ok": True}


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


@app.post("/api/conductores/sincronizar-rrhh")
def sincronizar_conductores_rrhh():
    """Nutre el maestro de conductores desde los empleados (categoría Conductor)."""
    conn = _db()
    emps = conn.execute(
        "SELECT * FROM empleados WHERE categoria='Conductor' ORDER BY nombre, apellidos"
    ).fetchall()
    creados, actualizados = 0, 0
    for e in emps:
        exist = conn.execute("SELECT id FROM conductores WHERE empleado_id=?", (e["id"],)).fetchone()
        if exist:
            actualizados += 1
        else:
            creados += 1
        _sync_conductor(conn, e)
    conn.commit()
    conn.close()
    return {"ok": True, "creados": creados, "actualizados": actualizados, "empleados_conductor": len(emps)}


@app.get("/api/vehiculos")
def list_vehiculos(categoria: str = ""):
    conn = _db()
    if categoria:
        rows = conn.execute("SELECT * FROM vehiculos WHERE categoria=? ORDER BY id", (categoria,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vehiculos ORDER BY id").fetchall()
    conn.close()
    activos = _vehiculos_en_curso()
    return {"vehiculos": [{**dict(r), "disponible": r["id"] not in activos} for r in rows]}


@app.get("/api/vehiculos/disponibles")
def list_vehiculos_disponibles(fecha_esperada_carga: str = "", categoria: str = ""):
    """Vehículos con disponibilidad para una fecha de carga: bloquea si tiene mantenimiento solapado o viaje en curso."""
    conn = _db()
    fecha = (fecha_esperada_carga or "")[:10]
    en_curso = _vehiculos_en_curso()
    conds, params = [], []
    if categoria:
        conds.append("v.categoria=?")
        params.append(categoria)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    if fecha:
        rows = conn.execute(
            f"""
            WITH man AS (
                SELECT DISTINCT ON (vehiculo_id) vehiculo_id, tipo AS tipo_man
                FROM mantenimientos
                WHERE ? >= fecha AND ? <= COALESCE(NULLIF(fecha_fin,''), fecha)
                  AND COALESCE(hecho, false) = false
                ORDER BY vehiculo_id, fecha
            )
            SELECT v.*, man.tipo_man
            FROM vehiculos v
            LEFT JOIN man ON man.vehiculo_id = v.id
            {where}
            ORDER BY v.id
            """,
            (fecha, fecha, *params),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            tipo_man = d.pop("tipo_man", None)
            ocupado = r["id"] in en_curso
            d["disponible"] = (tipo_man is None) and (not ocupado)
            d["motivo_bloqueo"] = tipo_man if tipo_man else ("En viaje" if ocupado else None)
            out.append(d)
    else:
        rows = conn.execute(
            f"SELECT v.* FROM vehiculos v {where} ORDER BY v.id", params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            ocupado = r["id"] in en_curso
            d["disponible"] = not ocupado
            d["motivo_bloqueo"] = "En viaje" if ocupado else None
            out.append(d)
    conn.close()
    return {"vehiculos": out}


@app.get("/api/vehiculos/en-curso")
def vehiculos_en_curso():
    return {"en_curso": sorted(_vehiculos_en_curso())}


@app.get("/api/vehiculos/posiciones")
def vehiculos_posiciones():
    """Última posición conocida de cada vehículo (de las trazas de Trimble)."""
    conn = _db()
    rows = conn.execute(
        "SELECT id, matricula, categoria, marca, modelo, last_lat, last_lng, last_position_time "
        "FROM vehiculos WHERE last_lat IS NOT NULL AND last_lng IS NOT NULL"
    ).fetchall()
    conn.close()
    return {"vehiculos": [dict(r) for r in rows]}


@app.get("/api/telemetria")
def telemetria(vehiculo: str = "", desde: str = "", hasta: str = "", limit: int = 500):
    """Historial de telemetría (posiciones/velocidad/rumbo) de un vehículo (hypertable)."""
    conn = _db()
    q = ("SELECT time, source, vehiculo_id, lat, lng, speed, heading, mileage "
         "FROM telemetria WHERE 1=1")
    params = []
    if vehiculo:
        q += " AND vehiculo_id=?"
        params.append(vehiculo)
    if desde:
        q += " AND time >= ?"
        params.append(desde)
    if hasta:
        q += " AND time <= ?"
        params.append(hasta)
    q += " ORDER BY time DESC LIMIT ?"
    params.append(min(int(limit), 5000))
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return {"puntos": [dict(r) for r in rows]}


@app.get("/api/vehiculos/cercano")
def vehiculo_cercano(lat: float, lng: float):
    """Devuelve la tractora libre más cercana al punto dado (por última posición conocida)."""
    activos = _vehiculos_en_curso()
    conn = _db()
    rows = conn.execute("SELECT id, last_lat, last_lng, matricula FROM vehiculos WHERE categoria='tractora'").fetchall()
    conn.close()
    best = None
    for r in rows:
        if r["id"] in activos:
            continue
        if r["last_lat"] is None or r["last_lng"] is None:
            continue
        d = _haversine_km(lat, lng, r["last_lat"], r["last_lng"])
        if d is not None and (best is None or d < best["dist"]):
            best = {"id": r["id"], "matricula": r["matricula"], "dist": d}
    return {"cercano": best}


@app.post("/api/vehiculos")
def add_vehiculo(v: Vehiculo):
    conn = _db()
    coste = float(v.coste_adquisicion or 0)
    # Compra (coste>0) o renting/leasing exigen proveedor vinculado.
    if (v.tipo_tenencia in ("Renting", "Leasing") or coste > 0) and not v.proveedor_id:
        conn.close()
        raise HTTPException(status_code=400, detail={"error": "Indica el proveedor (proveedor_id) para este vehículo."})
    conn.execute(
        "INSERT INTO vehiculos (id, categoria, matricula, marca, modelo, anno, itv, seguro, peaje_categoria, "
        "ptv_profile, ejes, mma, clase_euro, capacidad_peso, capacidad_palets, "
        "coste_adquisicion, fecha_adquisicion, vida_util, valor_residual, "
        "fecha_caducidad_itv, seguro_compania, fecha_caducidad_seguro, tipo_tenencia, proveedor_id, fecha_alta, cuota_mensual) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET categoria=EXCLUDED.categoria, matricula=EXCLUDED.matricula, marca=EXCLUDED.marca, "
        "modelo=EXCLUDED.modelo, anno=EXCLUDED.anno, itv=EXCLUDED.itv, seguro=EXCLUDED.seguro, "
        "peaje_categoria=EXCLUDED.peaje_categoria, ptv_profile=EXCLUDED.ptv_profile, "
        "ejes=EXCLUDED.ejes, mma=EXCLUDED.mma, clase_euro=EXCLUDED.clase_euro, "
        "capacidad_peso=EXCLUDED.capacidad_peso, capacidad_palets=EXCLUDED.capacidad_palets, "
        "coste_adquisicion=EXCLUDED.coste_adquisicion, fecha_adquisicion=EXCLUDED.fecha_adquisicion, "
        "vida_util=EXCLUDED.vida_util, valor_residual=EXCLUDED.valor_residual, "
        "fecha_caducidad_itv=EXCLUDED.fecha_caducidad_itv, seguro_compania=EXCLUDED.seguro_compania, "
        "fecha_caducidad_seguro=EXCLUDED.fecha_caducidad_seguro, tipo_tenencia=EXCLUDED.tipo_tenencia, "
        "proveedor_id=EXCLUDED.proveedor_id, fecha_alta=EXCLUDED.fecha_alta, cuota_mensual=EXCLUDED.cuota_mensual",
        (v.id, v.categoria, v.matricula, v.marca, v.modelo, v.anno, v.itv, v.seguro, v.peaje_categoria,
         v.ptv_profile, v.ejes, v.mma, v.clase_euro, v.capacidad_peso, v.capacidad_palets,
         v.coste_adquisicion, v.fecha_adquisicion, v.vida_util, v.valor_residual,
         v.fecha_caducidad_itv, v.seguro_compania, v.fecha_caducidad_seguro, v.tipo_tenencia, v.proveedor_id, v.fecha_alta, v.cuota_mensual),
    )
    # Asiento de adquisición (solo compra en Propiedad): Debe 218 / Haber 400, una sola vez.
    if coste > 0 and v.proveedor_id:
        ya = conn.execute("SELECT id FROM asientos WHERE origen='Compra_Vehiculo' AND origen_id=?", (v.id,)).fetchone()
        if not ya:
            fecha = v.fecha_adquisicion or datetime.date.today().isoformat()
            try:
                _registrar_asiento(
                    fecha, f"Adquisición vehículo {v.id}",
                    [("218", round(coste, 2), 0, "Elementos de transporte"),
                     ("400", 0, round(coste, 2), "Proveedor de inmovilizado")],
                    origen="Compra_Vehiculo", origen_id=v.id, conn=conn,
                )
            except ValueError as exc:
                conn.rollback()
                conn.close()
                raise HTTPException(status_code=400, detail={"error": str(exc)})
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/vehiculos/{veh_id}")
def del_vehiculo(veh_id: str):
    conn = _db()
    conn.execute("DELETE FROM vehiculos WHERE id=?", (veh_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.patch("/api/vehiculos/{veh_id}")
def upd_vehiculo(veh_id: str, body: dict):
    """Edita datos técnicos y costes fijos de un vehículo (ITV, seguro, costes...)."""
    allow = ("itv", "seguro", "coste_adquisicion", "valor_residual", "vida_util",
             "clase_euro", "capacidad_peso", "capacidad_palets", "mma", "ejes",
             "fecha_caducidad_itv", "seguro_compania", "fecha_caducidad_seguro",
             "tipo_tenencia", "proveedor_id", "fecha_alta", "cuota_mensual", "fecha_proxima_revision")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE vehiculos SET {sets} WHERE id=?", (*fields.values(), veh_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/vehiculos/{veh_id}/documentos")
def add_vehiculo_documentos(veh_id: str, req: dict):
    """Guarda los PDF del vehículo (base64), con source='vehiculo'."""
    docs = req.get("documentos") or []
    if not docs:
        return {"ok": True, "guardados": 0}
    conn = _db()
    if not conn.execute("SELECT id FROM vehiculos WHERE id=?", (veh_id,)).fetchone():
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Vehículo no encontrado."})
    guardados = 0
    for d in docs:
        contenido = d.get("contenido") or ""
        if not contenido:
            continue
        nombre = (d.get("nombre") or "documento.pdf").rsplit("/", 1)[-1][:120] or "documento.pdf"
        name = f"{uuid.uuid4().hex[:10]}__{nombre}"
        conn.execute(
            "INSERT INTO files (vehiculo_id, name, ftype, ftime, source, formato, content_b64) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (veh_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "vehiculo", "pdf", contenido),
        )
        guardados += 1
    conn.commit()
    conn.close()
    return {"ok": True, "guardados": guardados}


@app.get("/api/vehiculos/{veh_id}/documentos")
def list_vehiculo_documentos(veh_id: str):
    conn = _db()
    rows = conn.execute(
        "SELECT id, name, content_b64 FROM files WHERE vehiculo_id=? AND source='vehiculo' ORDER BY id", (veh_id,)
    ).fetchall()
    conn.close()
    docs = []
    for r in rows:
        c = r["content_b64"] or ""
        nombre = r["name"].split("__", 1)[1] if "__" in r["name"] else r["name"]
        docs.append({"id": r["id"], "nombre": nombre, "contenido": c, "size": round(len(c) * 3 / 4)})
    return {"documentos": docs}


@app.delete("/api/vehiculos/{veh_id}/documentos/{file_id}")
def del_vehiculo_documento(veh_id: str, file_id: int):
    conn = _db()
    conn.execute("DELETE FROM files WHERE id=? AND vehiculo_id=? AND source='vehiculo'", (file_id, veh_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/documentos")
def list_documentos():
    """Gestor documental global: unifica files (e-CMR, pedidos, vehículos) y facturas OCR de gastos."""
    conn = _db()
    rows = conn.execute(
        """
        SELECT f.id, 'files' AS origen, f.name AS nombre, f.source, f.formato, f.ftime AS fecha,
               f.content_b64, f.trip_id, f.vehiculo_id, v.matricula, t.cliente
        FROM files f
        LEFT JOIN vehiculos v ON v.id = f.vehiculo_id
        LEFT JOIN trips t ON t.id = f.trip_id
        UNION ALL
        SELECT g.id, 'gastos', COALESCE(g.factura_ref,''), 'gasto', 'pdf', g.fecha,
               g.archivo_base64, NULL, g.vehiculo_id, v.matricula, NULL
        FROM gastos_vehiculos g
        LEFT JOIN vehiculos v ON v.id = g.vehiculo_id
        WHERE COALESCE(g.archivo_base64,'') <> ''
        ORDER BY fecha DESC
        """,
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        nombre = r["nombre"] or "documento.pdf"
        if "__" in nombre:
            nombre = nombre.split("__", 1)[1]
        src = r["source"] or ""
        # Clasificación real: la columna `source` de files guarda el ID de terminal/device en
        # los docs del DMS de Trimble (POD/signoff/DOC_CARGA), no el tipo. Se clasifica por contexto.
        if r["origen"] == "gastos":
            modulo = "Gastos"
        elif src == "vehiculo":
            modulo = "Vehículos"
        else:
            modulo = "Operaciones"
        ref = r["matricula"] or r["trip_id"] or ""
        formato = (r["formato"] or "").upper()
        if not formato:
            ext = nombre.rsplit(".", 1)[-1].upper() if "." in nombre else ""
            formato = ext or "PDF"
        out.append({
            "id": f"{r['origen']}:{r['id']}",
            "nombre": nombre,
            "modulo_origen": modulo,
            "referencia": ref or "—",
            "formato": formato,
            "fecha": (r["fecha"] or "")[:10],
            "content_b64": r["content_b64"] or "",
            "source": src,
        })
    return {"documentos": out}


@app.patch("/api/documentos/{doc_id}/renombrar")
def renombrar_documento(doc_id: str, body: dict):
    """Renombra un documento (files.name o gastos_vehiculos.factura_ref)."""
    nuevo = (body.get("nombre") or "").strip()[:120]
    if not nuevo:
        return {"ok": False, "error": "Nombre vacío"}
    conn = _db()
    if doc_id.startswith("files:"):
        fid = int(doc_id.split(":", 1)[1])
        row = conn.execute("SELECT name FROM files WHERE id=?", (fid,)).fetchone()
        if not row:
            conn.close()
            raise HTTPException(status_code=404, detail={"error": "No encontrado"})
        prefijo = row["name"].split("__", 1)[0] + "__" if "__" in row["name"] else ""
        conn.execute("UPDATE files SET name=? WHERE id=?", (prefijo + nuevo, fid))
    elif doc_id.startswith("gastos:"):
        gid = int(doc_id.split(":", 1)[1])
        conn.execute("UPDATE gastos_vehiculos SET factura_ref=? WHERE id=?", (nuevo, gid))
    else:
        conn.close()
        raise HTTPException(status_code=400, detail={"error": "ID inválido"})
    conn.commit()
    conn.close()
    return {"ok": True}




@app.get("/api/tarifas-peaje")
def list_tarifas_peaje():
    conn = _db()
    rows = conn.execute("SELECT categoria, eur_km FROM tarifas_peaje ORDER BY eur_km").fetchall()
    conn.close()
    labels = {k: v["label"] for k, v in _PEAJE_CATEGORIAS.items()}
    return {
        "tarifas": [
            {"categoria": r["categoria"], "label": labels.get(r["categoria"], r["categoria"]),
             "eur_km": float(r["eur_km"] or 0)}
            for r in rows
        ]
    }


@app.post("/api/tarifas-peaje")
def set_tarifa_peaje(t: TarifaPeaje):
    conn = _db()
    conn.execute(
        "INSERT INTO tarifas_peaje (categoria, eur_km) VALUES (?,?) "
        "ON CONFLICT (categoria) DO UPDATE SET eur_km=EXCLUDED.eur_km",
        (t.categoria, t.eur_km),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------- #
# Contabilidad: doble partida (plan contable, asientos, informes, facturas)
# ---------------------------------------------------------------------- #






def _sumas_por_tipo(conn, desde="", hasta=""):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY c.tipo", params,
    ).fetchall()
    sums = {}
    for r in rows:
        sums[r["tipo"]] = [float(r["debe"] or 0), float(r["haber"] or 0)]
    return sums


def _suma_cuenta(conn, cuenta, desde="", hasta=""):
    conds, params = ["p.cuenta = ?"], [cuenta]
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    row = conn.execute(
        f"SELECT SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id WHERE {' AND '.join(conds)}", params,
    ).fetchone()
    return float(row["debe"] or 0), float(row["haber"] or 0)


@app.get("/api/contabilidad/cuentas")
def contabilidad_cuentas():
    conn = _db()
    rows = conn.execute("SELECT * FROM cuentas ORDER BY orden, codigo").fetchall()
    conn.close()
    return {"cuentas": [dict(r) for r in rows]}


@app.post("/api/contabilidad/cuentas")
def upsert_cuenta(c: CuentaContable):
    conn = _db()
    conn.execute(
        "INSERT INTO cuentas (codigo, nombre, grupo, tipo, orden) VALUES (?,?,?,?,?) "
        "ON CONFLICT (codigo) DO UPDATE SET nombre=EXCLUDED.nombre, grupo=EXCLUDED.grupo, "
        "tipo=EXCLUDED.tipo, orden=EXCLUDED.orden",
        (c.codigo.strip(), c.nombre.strip(), c.grupo.strip(), c.tipo.strip(), c.orden),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/contabilidad/cuentas/{codigo}")
def del_cuenta(codigo: str):
    conn = _db()
    n = conn.execute("SELECT COUNT(*) AS n FROM apuntes WHERE cuenta=?", (codigo,)).fetchone()["n"]
    if n:
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "La cuenta tiene apuntes; no se puede borrar."})
    conn.execute("DELETE FROM cuentas WHERE codigo=?", (codigo,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/contabilidad/asientos")
def contabilidad_asientos(desde: str = "", hasta: str = ""):
    conn = _db()
    conds, params = [], []
    if desde:
        conds.append("fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM asientos{where} ORDER BY fecha DESC, numero DESC, id DESC LIMIT 500", params,
    ).fetchall()
    apuntes = {}
    if rows:
        ids = [r["id"] for r in rows]
        ph = ",".join("?" for _ in ids)
        arows = conn.execute(
            f"SELECT ap.*, c.nombre AS cuenta_nombre FROM apuntes ap JOIN cuentas c ON c.codigo=ap.cuenta "
            f"WHERE ap.asiento_id IN ({ph}) ORDER BY ap.id", ids,
        ).fetchall()
        for r in arows:
            apuntes.setdefault(r["asiento_id"], []).append(dict(r))
    conn.close()
    return {"asientos": [dict(r) for r in rows], "apuntes": apuntes}


@app.post("/api/contabilidad/asientos")
def contabilidad_crear_asiento(a: AsientoManual):
    lineas = [(l.cuenta.strip(), l.debe, l.haber, l.concepto) for l in a.lineas if l.cuenta and l.cuenta.strip()]
    if not lineas:
        raise HTTPException(status_code=400, detail={"error": "El asiento no tiene líneas."})
    try:
        aid = _post_asiento(a.fecha, a.concepto, lineas, origen="manual", documento=a.documento)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    return {"ok": True, "asiento_id": aid}


@app.delete("/api/contabilidad/asientos/{asiento_id}")
def contabilidad_del_asiento(asiento_id: int):
    conn = _db()
    row = conn.execute("SELECT origen FROM asientos WHERE id=?", (asiento_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Asiento no encontrado."})
    if row["origen"] != "manual":
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "Es un asiento automático; borra la factura o el gasto asociado."})
    conn.execute("DELETE FROM asientos WHERE id=?", (asiento_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/contabilidad/balance")
def contabilidad_balance(desde: str = "", hasta: str = ""):
    conn = _db()
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT p.cuenta, c.nombre, c.grupo, c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY p.cuenta, c.nombre, c.grupo, c.tipo, c.orden ORDER BY c.orden, p.cuenta", params,
    ).fetchall()
    conn.close()
    total_debe = total_haber = 0.0
    cuentas = []
    for r in rows:
        d = float(r["debe"] or 0); h = float(r["haber"] or 0)
        total_debe += d; total_haber += h
        cuentas.append({"cuenta": r["cuenta"], "nombre": r["nombre"], "grupo": r["grupo"],
                        "tipo": r["tipo"], "debe": round(d, 2), "haber": round(h, 2),
                        "saldo": round(d - h, 2)})
    return {"cuentas": cuentas, "total_debe": round(total_debe, 2), "total_haber": round(total_haber, 2)}


@app.get("/api/contabilidad/pyg")
def contabilidad_pyg(desde: str = "", hasta: str = ""):
    conn = _db()
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    base = ("FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta ")
    gastos = conn.execute(
        f"SELECT p.cuenta, c.nombre, SUM(p.debe)-SUM(p.haber) AS importe {base} "
        f"WHERE {' AND '.join(conds + ['c.tipo=?'])} GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden",
        params + ["gasto"],
    ).fetchall()
    ingresos = conn.execute(
        f"SELECT p.cuenta, c.nombre, SUM(p.haber)-SUM(p.debe) AS importe {base} "
        f"WHERE {' AND '.join(conds + ['c.tipo=?'])} GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden",
        params + ["ingreso"],
    ).fetchall()
    conn.close()
    tg = sum(float(r["importe"] or 0) for r in gastos)
    ti = sum(float(r["importe"] or 0) for r in ingresos)
    return {
        "gastos": [{"cuenta": r["cuenta"], "nombre": r["nombre"], "importe": round(float(r["importe"] or 0), 2)} for r in gastos],
        "ingresos": [{"cuenta": r["cuenta"], "nombre": r["nombre"], "importe": round(float(r["importe"] or 0), 2)} for r in ingresos],
        "total_gastos": round(tg, 2),
        "total_ingresos": round(ti, 2),
        "resultado": round(ti - tg, 2),
    }


@app.get("/api/contabilidad/situacion")
def contabilidad_situacion(hasta: str = ""):
    conn = _db()
    sums = _sumas_por_tipo(conn, "", hasta)
    conn.close()
    def saldo(tipo):
        d, h = sums.get(tipo, [0, 0])
        return d - h
    activo = saldo("activo")
    pasivo = -(saldo("pasivo"))
    patrimonio = -(saldo("patrimonio"))
    resultado = -(saldo("ingreso")) - saldo("gasto")
    neto = patrimonio + resultado
    return {
        "activo": round(activo, 2),
        "pasivo": round(pasivo, 2),
        "patrimonio": round(patrimonio, 2),
        "resultado": round(resultado, 2),
        "patrimonio_neto": round(neto, 2),
        "cuadra": abs(activo - (pasivo + neto)) < 0.01,
    }


@app.get("/api/contabilidad/dashboard")
def contabilidad_dashboard(desde: str = "", hasta: str = ""):
    conn = _db()
    sums = _sumas_por_tipo(conn, desde, hasta)
    def saldo(tipo):
        d, h = sums.get(tipo, [0, 0])
        return d - h
    ingresos = -saldo("ingreso")
    gastos = saldo("gasto")
    iva_rep = conn.execute(
        "SELECT SUM(haber)-SUM(debe) AS v FROM apuntes WHERE cuenta='477'"
    ).fetchone()["v"] or 0
    iva_sop = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta='472'"
    ).fetchone()["v"] or 0
    bancos = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')"
    ).fetchone()["v"] or 0
    frows = conn.execute(
        "SELECT estado, COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM facturas GROUP BY estado"
    ).fetchall()
    conn.close()
    fact = {"emitida": 0, "cobrada": 0}
    for r in frows:
        fact[r["estado"]] = round(float(r["total"] or 0), 2)
    return {
        "ingresos": round(ingresos, 2),
        "gastos": round(gastos, 2),
        "margen": round(ingresos - gastos, 2),
        "iva_repercutido": round(float(iva_rep), 2),
        "iva_soportado": round(float(iva_sop), 2),
        "iva_neto": round(float(iva_rep) - float(iva_sop), 2),
        "tesoreria": round(float(bancos), 2),
        "facturas": fact,
    }


@app.get("/api/contabilidad/cierre")
def contabilidad_cierre_get():
    return {"cierre_fecha": _get_config("cierre_fecha", "")}


@app.post("/api/contabilidad/cierre")
def contabilidad_cierre(req: dict):
    fecha = (req.get("fecha") or "").strip()
    if not fecha:
        raise HTTPException(status_code=400, detail={"error": "Indica la fecha de cierre."})
    conn = _db()
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        ("cierre_fecha", fecha),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "cierre_fecha": fecha}


@app.get("/api/contabilidad/tesoreria-prevision")
def contabilidad_tesoreria_prevision():
    conn = _db()
    saldo = conn.execute("SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')").fetchone()["v"] or 0
    fact = conn.execute("SELECT fecha, COALESCE(SUM(total),0) AS t FROM facturas WHERE estado != 'cobrada' GROUP BY fecha").fetchall()
    gast = conn.execute("SELECT fecha, COALESCE(SUM(importe),0) AS t FROM gastos WHERE COALESCE(pagado,false)=false AND COALESCE(importe,0)>0 GROUP BY fecha").fetchall()
    conn.close()
    hoy = datetime.date.today()
    months = []
    for i in range(3):
        y = hoy.year + (hoy.month + i - 1) // 12
        m = (hoy.month + i - 1) % 12 + 1
        months.append(f"{y:04d}-{m:02d}")
    por_mes = {m: {"entradas": 0.0, "salidas": 0.0} for m in months}
    sin_fecha = {"entradas": 0.0, "salidas": 0.0}
    for r in fact:
        mes = (r["fecha"] or "")[:7]
        if mes in por_mes: por_mes[mes]["entradas"] += float(r["t"] or 0)
        else: sin_fecha["entradas"] += float(r["t"] or 0)
    for r in gast:
        mes = (r["fecha"] or "")[:7]
        if mes in por_mes: por_mes[mes]["salidas"] += float(r["t"] or 0)
        else: sin_fecha["salidas"] += float(r["t"] or 0)
    saldo_proy = float(saldo)
    prevision = []
    for m in months:
        e = round(por_mes[m]["entradas"], 2); s = round(por_mes[m]["salidas"], 2)
        saldo_proy += e - s
        prevision.append({"mes": m, "entradas": e, "salidas": s, "saldo_proyectado": round(saldo_proy, 2)})
    return {
        "saldo": round(float(saldo), 2),
        "entradas_pendientes": round(sum(por_mes[m]["entradas"] for m in months) + sin_fecha["entradas"], 2),
        "salidas_pendientes": round(sum(por_mes[m]["salidas"] for m in months) + sin_fecha["salidas"], 2),
        "sin_fecha": {"entradas": round(sin_fecha["entradas"], 2), "salidas": round(sin_fecha["salidas"], 2)},
        "prevision": prevision,
    }


@app.get("/api/contabilidad/modelo347")
def contabilidad_modelo347(anio: str = ""):
    if not anio:
        anio = str(datetime.date.today().year)
    conn = _db()
    cli = conn.execute(
        "SELECT COALESCE(NULLIF(cliente_nombre,''),'Sin nombre') AS tercero, SUM(total) AS total "
        "FROM facturas WHERE substr(fecha,1,4)=? GROUP BY 1 ORDER BY total DESC", (anio,)
    ).fetchall()
    prov = conn.execute(
        "SELECT COALESCE(NULLIF(p.nombre,''),'Sin nombre') AS tercero, SUM(g.importe) AS total "
        "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id=p.id "
        "WHERE substr(g.fecha,1,4)=? AND COALESCE(g.proveedor_id,0) > 0 GROUP BY 1 ORDER BY total DESC", (anio,)
    ).fetchall()
    conn.close()
    LIMITE = 3005.06
    clientes = [{"tercero": r["tercero"], "total": round(float(r["total"] or 0), 2)} for r in cli if float(r["total"] or 0) > LIMITE]
    proveedores = [{"tercero": r["tercero"], "total": round(float(r["total"] or 0), 2)} for r in prov if float(r["total"] or 0) > LIMITE]
    return {"anio": anio, "limite": LIMITE, "clientes": clientes, "proveedores": proveedores,
            "total_clientes": round(sum(x["total"] for x in clientes), 2),
            "total_proveedores": round(sum(x["total"] for x in proveedores), 2)}


@app.get("/api/contabilidad/tesoreria")
def contabilidad_tesoreria():
    conn = _db()
    saldo = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')"
    ).fetchone()["v"] or 0
    gpend = conn.execute(
        "SELECT g.id, g.fecha, g.categoria, g.concepto, g.importe, g.iva, g.retencion, p.nombre AS proveedor "
        "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id=p.id "
        "WHERE COALESCE(g.pagado, false) = false AND COALESCE(g.importe,0) > 0 "
        "ORDER BY g.fecha DESC LIMIT 100"
    ).fetchall()
    fpend = conn.execute(
        "SELECT id, numero, fecha, cliente_nombre, total FROM facturas WHERE estado != 'cobrada' ORDER BY fecha DESC"
    ).fetchall()
    movs = conn.execute(
        "SELECT a.id, a.fecha, a.concepto, a.origen, "
        "COALESCE(SUM(CASE WHEN p.cuenta IN ('570','572') THEN p.debe - p.haber ELSE 0 END),0) AS importe "
        "FROM asientos a JOIN apuntes p ON p.asiento_id=a.id "
        "WHERE a.origen IN ('cobro','pago') "
        "GROUP BY a.id, a.fecha, a.concepto, a.origen "
        "ORDER BY a.fecha DESC, a.id DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return {
        "saldo": round(float(saldo), 2),
        "gastos_pendientes": [dict(r) for r in gpend],
        "facturas_pendientes": [dict(r) for r in fpend],
        "movimientos": [dict(r) for r in movs],
    }


@app.get("/api/contabilidad/impuestos")
def contabilidad_impuestos(desde: str = "", hasta: str = ""):
    conn = _db()
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    conds.append("p.cuenta IN ('477','472')")
    where = " WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"SELECT substr(a.fecha,1,7) AS mes, p.cuenta, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id {where} "
        f"GROUP BY substr(a.fecha,1,7), p.cuenta ORDER BY mes",
        params,
    ).fetchall()
    trim = {}
    for r in rows:
        mes = r["mes"]
        y, m = int(mes[:4]), int(mes[5:7])
        q = (m - 1) // 3 + 1
        key = f"{y}-T{q}"
        d = trim.setdefault(key, {"repercutido": 0.0, "soportado": 0.0})
        if r["cuenta"] == "477":
            d["repercutido"] += float(r["haber"] or 0) - float(r["debe"] or 0)
        else:
            d["soportado"] += float(r["debe"] or 0) - float(r["haber"] or 0)
    trimestres = []
    for k in sorted(trim.keys()):
        d = trim[k]
        trimestres.append({
            "trimestre": k, "repercutido": round(d["repercutido"], 2),
            "soportado": round(d["soportado"], 2),
            "a_ingresar": round(d["repercutido"] - d["soportado"], 2),
        })
    ret = conn.execute(
        "SELECT SUM(haber)-SUM(debe) AS v FROM apuntes WHERE cuenta='4751'"
    ).fetchone()["v"] or 0
    conn.close()
    tot_rep = sum(t["repercutido"] for t in trimestres)
    tot_sop = sum(t["soportado"] for t in trimestres)
    return {
        "trimestres": trimestres,
        "total_repercutido": round(tot_rep, 2),
        "total_soportado": round(tot_sop, 2),
        "a_ingresar_total": round(tot_rep - tot_sop, 2),
        "retenciones": round(float(ret), 2),
    }




@app.get("/api/contabilidad/inmovilizado")
def contabilidad_inmovilizado():
    conn = _db()
    rows = conn.execute(
        "SELECT id, categoria, matricula, marca, modelo, coste_adquisicion, fecha_adquisicion, vida_util, valor_residual "
        "FROM vehiculos WHERE COALESCE(coste_adquisicion,0) > 0 ORDER BY matricula"
    ).fetchall()
    acumulado = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta='281'"
    ).fetchone()["v"] or 0
    adq_docs = {r["documento"] for r in conn.execute(
        "SELECT documento FROM asientos WHERE origen='adquisicion'"
    ).fetchall()}
    conn.close()
    hoy = datetime.date.today()
    items = []
    for r in rows:
        coste = float(r["coste_adquisicion"] or 0)
        residual = float(r["valor_residual"] or 0)
        vida = int(r["vida_util"] or 5)
        anual = round((coste - residual) / vida, 2) if vida > 0 else 0.0
        fecha = r["fecha_adquisicion"] or ""
        am_acum = 0.0
        if fecha and anual > 0:
            try:
                d0 = datetime.date.fromisoformat(fecha[:10])
                meses = max(0, (hoy.year - d0.year) * 12 + (hoy.month - d0.month))
                am_acum = round(min(anual * meses / 12.0, coste - residual), 2)
            except Exception:
                am_acum = 0.0
        items.append({
            "id": r["id"], "matricula": r["matricula"] or r["id"], "categoria": r["categoria"],
            "marca": r["marca"] or "", "modelo": r["modelo"] or "",
            "coste": coste, "residual": residual, "vida_util": vida,
            "fecha": fecha, "anual": anual, "acumulado": am_acum, "vnc": round(coste - am_acum, 2),
            "tiene_adquisicion": f"adquisicion-{r['id']}" in adq_docs,
        })
    return {"vehiculos": items, "amort_acumulada_contable": round(float(acumulado), 2), "anio": str(hoy.year)}


@app.post("/api/contabilidad/inmovilizado/{veh_id}")
def set_amortizacion(veh_id: str, a: AmortizacionRequest):
    conn = _db()
    conn.execute(
        "UPDATE vehiculos SET coste_adquisicion=?, fecha_adquisicion=?, vida_util=?, valor_residual=? WHERE id=?",
        (a.coste_adquisicion, a.fecha_adquisicion, a.vida_util, a.valor_residual, veh_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/contabilidad/inmovilizado/{veh_id}/adquisicion")
def registrar_adquisicion(veh_id: str):
    conn = _db()
    v = conn.execute("SELECT * FROM vehiculos WHERE id=?", (veh_id,)).fetchone()
    if not v:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Vehículo no encontrado."})
    coste = float(v["coste_adquisicion"] or 0)
    if coste <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail={"error": "El vehículo no tiene coste de adquisición."})
    exist = conn.execute(
        "SELECT id FROM asientos WHERE origen='adquisicion' AND documento=?", (f"adquisicion-{veh_id}",)
    ).fetchone()
    if exist:
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "Este vehículo ya tiene asiento de adquisición."})
    fecha = (v["fecha_adquisicion"] or "")[:10] or datetime.date.today().isoformat()
    nombre = v["matricula"] or veh_id
    aid = _post_asiento(
        fecha, f"Adquisición {nombre}",
        [("218", coste, 0, f"Adquisición {nombre}"),
         ("572", 0, coste, f"Pago adquisición {nombre}")],
        origen="adquisicion", documento=f"adquisicion-{veh_id}", conn=conn,
    )
    conn.commit()
    conn.close()
    return {"ok": True, "asiento_id": aid, "coste": coste}


@app.post("/api/contabilidad/amortizar")
def contabilidad_amortizar(periodo: str = ""):
    hoy = datetime.date.today()
    if not periodo:
        periodo = f"{hoy.year:04d}-{hoy.month:02d}"  # mes actual por defecto
    es_anual = len(periodo) == 4
    if es_anual:
        fecha = f"{periodo}-12-31"
    else:
        y, m = int(periodo[:4]), int(periodo[5:7])
        _dias = {1:31,2:28,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}
        last = _dias[m]
        if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
            last = 29
        fecha = f"{y:04d}-{m:02d}-{last:02d}"
    conn = _db()
    exist = conn.execute(
        "SELECT id FROM asientos WHERE origen='amortizacion' AND documento=?", (f"amortizacion-{periodo}",)
    ).fetchone()
    if exist:
        conn.close()
        raise HTTPException(status_code=409, detail={"error": f"Ya hay amortización para {periodo}."})
    rows = conn.execute(
        "SELECT id, matricula, coste_adquisicion, valor_residual, vida_util FROM vehiculos WHERE COALESCE(coste_adquisicion,0) > 0"
    ).fetchall()
    lineas = []
    total = 0.0
    for r in rows:
        coste = float(r["coste_adquisicion"] or 0)
        residual = float(r["valor_residual"] or 0)
        vida = int(r["vida_util"] or 5)
        anual = round((coste - residual) / vida, 2) if vida > 0 else 0.0
        cuota = anual if es_anual else round(anual / 12.0, 2)
        if cuota > 0:
            nombre = r["matricula"] or r["id"]
            lineas.append(("681", cuota, 0, f"Amortización {nombre}"))
            lineas.append(("281", 0, cuota, f"Amortización {nombre}"))
            total += cuota
    if not lineas:
        conn.close()
        raise HTTPException(status_code=400, detail={"error": "No hay vehículos con datos de amortización."})
    aid = _post_asiento(fecha, f"Amortización {'anual' if es_anual else 'mensual'} {periodo}", lineas,
                        origen="amortizacion", documento=f"amortizacion-{periodo}", conn=conn)
    conn.commit()
    conn.close()
    return {"ok": True, "asiento_id": aid, "total": round(total, 2), "periodo": periodo, "mensual": not es_anual}


@app.get("/api/contabilidad/explotacion")
def contabilidad_explotacion(desde: str = "", hasta: str = ""):
    conn = _db()
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    conds.append("c.tipo IN ('gasto','ingreso')")
    where = " WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"SELECT substr(a.fecha,1,7) AS mes, c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY substr(a.fecha,1,7), c.tipo ORDER BY mes", params,
    ).fetchall()
    conn.close()
    by_mes = {}
    for r in rows:
        d = by_mes.setdefault(r["mes"], {"ingresos": 0.0, "gastos": 0.0})
        if r["tipo"] == "ingreso":
            d["ingresos"] += float(r["haber"] or 0) - float(r["debe"] or 0)
        else:
            d["gastos"] += float(r["debe"] or 0) - float(r["haber"] or 0)
    meses = []
    for m in sorted(by_mes):
        d = by_mes[m]
        meses.append({"mes": m, "ingresos": round(d["ingresos"], 2), "gastos": round(d["gastos"], 2),
                      "resultado": round(d["ingresos"] - d["gastos"], 2)})
    return {"meses": meses, "total_ingresos": round(sum(x["ingresos"] for x in meses), 2),
            "total_gastos": round(sum(x["gastos"] for x in meses), 2),
            "total_resultado": round(sum(x["resultado"] for x in meses), 2)}


@app.get("/api/contabilidad/explotacion-vehiculos")
def contabilidad_explotacion_vehiculos():
    """Explotación por vehículo: ingresos y gastos directos por tractora + gastos generales repartibles."""
    conn = _db()
    trips = conn.execute(
        "SELECT COALESCE(NULLIF(terminal,''),'General') AS veh, COALESCE(SUM(precio),0) AS ingresos, "
        "COALESCE(SUM(COALESCE(km_real, km_total)),0) AS km, COALESCE(SUM(km_vacio),0) AS km_vacio FROM trips GROUP BY 1"
    ).fetchall()
    gastos = conn.execute(
        "SELECT COALESCE(NULLIF(terminal,''),'General') AS veh, COALESCE(SUM(importe),0) AS gastos FROM gastos GROUP BY 1"
    ).fetchall()
    conn.close()
    by = {}
    for r in trips:
        by[r["veh"]] = {"vehiculo": r["veh"], "ingresos": float(r["ingresos"] or 0),
                        "km": float(r["km"] or 0), "km_vacio": float(r["km_vacio"] or 0), "gastos": 0.0}
    for r in gastos:
        v = by.setdefault(r["veh"], {"vehiculo": r["veh"], "ingresos": 0.0, "km": 0.0, "km_vacio": 0.0, "gastos": 0.0})
        v["gastos"] = float(r["gastos"] or 0)
    generales = by.pop("General", {"ingresos": 0.0, "km": 0.0, "km_vacio": 0.0, "gastos": 0.0})
    vehiculos = []
    for v in by.values():
        vehiculos.append({
            "vehiculo": v["vehiculo"], "ingresos": round(v["ingresos"], 2),
            "gastos": round(v["gastos"], 2), "km": round(v["km"], 1),
            "resultado": round(v["ingresos"] - v["gastos"], 2),
        })
    vehiculos.sort(key=lambda x: -x["ingresos"])
    total_km = round(sum(v["km"] for v in vehiculos), 1)
    return {"vehiculos": vehiculos, "gastos_generales": round(float(generales["gastos"] or 0), 2), "total_km": total_km}


@app.get("/api/contabilidad/reconciliacion-km")
def contabilidad_reconciliacion_km(desde: str = "", hasta: str = ""):
    """Reconciliación de km por vehículo: Δ odómetro (telemetría) vs Σ km de viajes.

    km_odometro = odómetro_fin - odómetro_inicio en el periodo (MIN/MAX odometer_km).
    km_viajes   = Σ km_efectivo (COALESCE(km_real, km_total)) de los viajes del periodo.
    km_vacio    = km_odometro - km_viajes (km huérfano: maniobras/reposicionamiento/viajes sin registrar).
    """
    conn = _db()

    conds_t = ["COALESCE(NULLIF(t.terminal,''),'') <> ''"]
    params_t = []
    if desde:
        conds_t.append("substr(COALESCE(t.fecha_actualizacion, t.creado),1,10) >= ?"); params_t.append(desde)
    if hasta:
        conds_t.append("substr(COALESCE(t.fecha_actualizacion, t.creado),1,10) <= ?"); params_t.append(hasta)
    where_t = " WHERE " + " AND ".join(conds_t)

    conds_o = ["odometer_km IS NOT NULL"]
    params_o = []
    if desde:
        conds_o.append("time >= ?::timestamptz"); params_o.append(desde + "T00:00:00Z")
    if hasta:
        conds_o.append("time <= ?::timestamptz"); params_o.append(hasta + "T23:59:59Z")
    where_o = " WHERE " + " AND ".join(conds_o)

    viajes = conn.execute(
        f"SELECT t.terminal AS veh, COALESCE(SUM(COALESCE(t.km_real, t.km_total)),0) AS km "
        f"FROM trips t{where_t} GROUP BY t.terminal", params_t,
    ).fetchall()

    odos = conn.execute(
        f"SELECT vehiculo_id AS veh, MIN(odometer_km) AS odo_inicio, MAX(odometer_km) AS odo_fin "
        f"FROM telemetria.posiciones_gps{where_o} GROUP BY vehiculo_id", params_o,
    ).fetchall()
    conn.close()

    by = {}
    for r in odos:
        by[r["veh"]] = {
            "vehiculo": r["veh"],
            "odo_inicio": float(r["odo_inicio"] or 0),
            "odo_fin": float(r["odo_fin"] or 0),
            "km_odometro": round(float(r["odo_fin"] or 0) - float(r["odo_inicio"] or 0), 1),
            "km_viajes": 0.0,
        }
    for r in viajes:
        v = by.setdefault(r["veh"], {"vehiculo": r["veh"], "odo_inicio": None, "odo_fin": None, "km_odometro": 0.0})
        v["km_viajes"] = round(float(r["km"] or 0), 1)

    vehiculos = []
    for v in by.values():
        km_odo = round(v["km_odometro"] or 0, 1)
        km_via = round(v["km_viajes"] or 0, 1)
        km_vacio = round(km_odo - km_via, 1)
        pct_vacio = round((km_vacio / km_odo * 100), 1) if km_odo > 0 else 0.0
        vehiculos.append({
            "vehiculo": v["vehiculo"],
            "odo_inicio": v["odo_inicio"],
            "odo_fin": v["odo_fin"],
            "km_odometro": km_odo,
            "km_viajes": km_via,
            "km_vacio": km_vacio,
            "pct_vacio": pct_vacio,
        })
    vehiculos.sort(key=lambda x: -(x["km_odometro"]))
    total = {
        "km_odometro": round(sum(v["km_odometro"] for v in vehiculos), 1),
        "km_viajes": round(sum(v["km_viajes"] for v in vehiculos), 1),
        "km_vacio": round(sum(v["km_vacio"] for v in vehiculos), 1),
    }
    return {"vehiculos": vehiculos, "total": total, "desde": desde, "hasta": hasta}


@app.get("/api/contabilidad/auditoria")
def contabilidad_auditoria(limite: int = 200, user: dict = Depends(require_role(["admin"]))):
    """Pista de auditoría contable (solo administradores): quién hizo qué y cuándo."""
    limite = max(1, min(int(limite), 1000))
    conn = _db()
    rows = conn.execute(
        "SELECT id, tabla, registro_id, accion, usuario, antes, despues, ts "
        "FROM audit_log ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
    conn.close()
    return {"auditoria": [dict(r) for r in rows]}


@app.get("/api/contabilidad/facturas")
def contabilidad_facturas():
    conn = _db()
    rows = conn.execute("SELECT * FROM facturas ORDER BY fecha DESC, id DESC").fetchall()
    conn.close()
    return {"facturas": [dict(r) for r in rows]}


@app.get("/api/contabilidad/facturables")
def contabilidad_facturables():
    conn = _db()
    rows = conn.execute(
        "SELECT t.id, t.referencia, t.cliente, t.precio, t.iva, t.origen, t.destino, t.creado, t.estado "
        "FROM trips t "
        "WHERE NOT EXISTS (SELECT 1 FROM facturas f WHERE f.trip_id=t.id) "
        "AND NOT EXISTS (SELECT 1 FROM factura_lineas fl WHERE fl.trip_id=t.id) "
        "AND COALESCE(t.precio,0) > 0 "
        "AND LOWER(COALESCE(t.estado,'')) NOT IN ('sin_asignar','cancelado','canceled','rechazado','refused','error') "
        "ORDER BY t.creado DESC LIMIT 200"
    ).fetchall()
    conn.close()
    return {"viajes": [dict(r) for r in rows]}


def _costes_reales_viaje(trip):
    """Costes reales del viaje: peajes estimados + gastos vinculados exactamente al viaje."""
    peaje = float(trip["peaje_estimado"] or 0)
    conn = _db()
    row = conn.execute(
        "SELECT COALESCE(SUM(importe), 0) AS total FROM gastos WHERE trip_id=?",
        (trip["id"],),
    ).fetchone()
    conn.close()
    gastos = float(row["total"] or 0) if row else 0.0
    coste = round(peaje + gastos, 2)
    margen = round(float(trip["precio"] or 0) - coste, 2)
    return coste, margen


def _desglose_costes(conn, trip_id):
    """Desglose exacto de costes de un viaje: peajes PTV + gastos por categoría (trip_id)."""
    desglose = []
    peaje = 0.0
    if trip_id:
        t = conn.execute("SELECT peaje_estimado FROM trips WHERE id=?", (trip_id,)).fetchone()
        if t:
            peaje = round(float(t["peaje_estimado"] or 0), 2)
    if peaje > 0:
        desglose.append({"concepto": "Peajes PTV", "importe": peaje})
    if trip_id:
        for g in conn.execute(
            "SELECT categoria, COALESCE(SUM(importe), 0) AS total FROM gastos "
            "WHERE trip_id=? GROUP BY categoria ORDER BY total DESC",
            (trip_id,),
        ).fetchall():
            imp = round(float(g["total"] or 0), 2)
            if imp > 0:
                etiqueta = (g["categoria"] or "Otros").strip().title() or "Otros"
                desglose.append({"concepto": etiqueta, "importe": imp})
    return desglose


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


def _crear_factura_borrador(trip):
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
    conn = _db()
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
    conn.close()
    return factura_id


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


def _factura_numero(conn, fecha):
    fr = conn.execute("SELECT numero FROM facturas WHERE substr(fecha,1,4)=?", (fecha[:4],)).fetchall()
    max_n = 0
    for r in fr:
        m = re.match(r"^F-\d{4}-(\d+)$", r["numero"] or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"F-{fecha[:4]}-{max_n + 1:04d}"


def _empresa():
    conn = _db()
    row = conn.execute("SELECT * FROM empresa WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}


def _crear_factura(conn, trips, cliente_id, cliente_nombre, fecha):
    """Crea una factura (y sus líneas) para una lista de viajes del mismo cliente.
    Devuelve (factura_id, numero, base, cuota, total)."""
    base = round(sum(float(t["precio"] or 0) for t in trips), 2)
    iva = round(float(trips[0]["iva"] or 21), 2)
    cuota = round(base * iva / 100.0, 2)
    total = round(base + cuota, 2)
    numero = _factura_numero(conn, fecha)
    asiento_id = _post_asiento(
        fecha, f"Factura {numero} - {cliente_nombre or 'varios'}",
        [("430", total, 0, f"Factura {numero}"),
         ("705", 0, base, f"Ventas {numero}"),
         ("477", 0, cuota, f"IVA repercutido {numero}")],
        origen="viaje", documento=numero, conn=conn,
    )
    cur = conn.execute(
        "INSERT INTO facturas (numero, fecha, trip_id, cliente_id, cliente_nombre, base, iva, cuota_iva, total, estado, asiento_id, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (numero, fecha, trips[0]["id"] if len(trips) == 1 else None, cliente_id, cliente_nombre,
         base, iva, cuota, total, "emitida", asiento_id, datetime.datetime.utcnow().isoformat() + "Z"),
    )
    factura_id = cur.fetchone()["id"]
    for t in trips:
        tbase = round(float(t["precio"] or 0), 2)
        tcuota = round(tbase * iva / 100.0, 2)
        ttotal = round(tbase + tcuota, 2)
        concepto = f"{t['origen'] or ''} → {t['destino'] or ''}".strip().strip("→").strip() or t["id"]
        conn.execute(
            "INSERT INTO factura_lineas (factura_id, trip_id, concepto, base, iva, cuota_iva, total) VALUES (?,?,?,?,?,?,?)",
            (factura_id, t["id"], concepto, tbase, iva, tcuota, ttotal),
        )
        conn.execute("UPDATE trips SET factura=? WHERE id=?", (numero, t["id"]))
    return factura_id, numero, base, cuota, total


@app.post("/api/contabilidad/facturas/agrupada")
def contabilidad_factura_agrupada(req: dict):
    trip_ids = [t for t in (req.get("trip_ids") or []) if t]
    if not trip_ids:
        raise HTTPException(status_code=400, detail={"error": "Selecciona al menos un viaje para facturar."})
    conn = _db()
    ph = ",".join("?" for _ in trip_ids)
    trips = conn.execute(f"SELECT * FROM trips WHERE id IN ({ph}) ORDER BY creado", trip_ids).fetchall()
    if len(trips) != len(set(trip_ids)):
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Algún viaje seleccionado no existe."})
    clientes = {t["cliente_id"] for t in trips}
    if len(clientes) > 1:
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "Todos los viajes deben ser del mismo cliente para agruparlos."})
    for t in trips:
        if conn.execute("SELECT 1 FROM facturas WHERE trip_id=?", (t["id"],)).fetchone() or \
           conn.execute("SELECT 1 FROM factura_lineas WHERE trip_id=?", (t["id"],)).fetchone():
            conn.close()
            raise HTTPException(status_code=409, detail={"error": f"El viaje {t['id']} ya tiene factura."})
        if float(t["precio"] or 0) <= 0:
            conn.close()
            raise HTTPException(status_code=400, detail={"error": f"El viaje {t['id']} no tiene precio."})
    cliente_nombre = trips[0]["cliente"] or ""
    cliente_id = trips[0]["cliente_id"]
    fecha = (trips[0]["creado"] or "")[:10] or datetime.date.today().isoformat()
    try:
        factura_id, numero, base, cuota, total = _crear_factura(conn, trips, cliente_id, cliente_nombre, fecha)
    except ValueError as e:
        conn.rollback(); conn.close()
        raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit(); conn.close()
    return {"ok": True, "factura": numero, "total": total, "factura_id": factura_id, "viajes": len(trips)}


@app.post("/api/contabilidad/facturas/{trip_id}")
def contabilidad_generar_factura(trip_id: str):
    conn = _db()
    trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not trip:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    if conn.execute("SELECT 1 FROM facturas WHERE trip_id=?", (trip_id,)).fetchone() or \
       conn.execute("SELECT 1 FROM factura_lineas WHERE trip_id=?", (trip_id,)).fetchone():
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "Este viaje ya tiene factura."})
    if float(trip["precio"] or 0) <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail={"error": "El viaje no tiene precio."})
    fecha = (trip["creado"] or "")[:10] or datetime.date.today().isoformat()
    try:
        # Subcontratación: generar el gasto (624/410) antes de calcular costes/margen.
        if trip["subcontratado"]:
            _gasto_subcontrata(conn, trip, fecha)
        factura_id, numero, base, cuota, total = _crear_factura(
            conn, [trip], trip["cliente_id"], trip["cliente"] or "", fecha)
    except ValueError as e:
        conn.rollback(); conn.close()
        raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit(); conn.close()
    return {"ok": True, "factura": numero, "total": total, "factura_id": factura_id}


@app.get("/api/contabilidad/borradores")
def contabilidad_borradores(user: dict = Depends(require_role(["admin"]))):
    """Facturas autogeneradas en estado Borrador, listas para validar y emitir."""
    conn = _db()
    rows = conn.execute(
        "SELECT f.id, f.numero, f.fecha, f.trip_id, f.cliente_nombre, f.base, f.iva, "
        "f.cuota_iva, f.total, f.coste, f.margen, f.creado, t.origen, t.destino "
        "FROM facturas f LEFT JOIN trips t ON t.id = f.trip_id "
        "WHERE f.estado='Borrador' ORDER BY f.creado DESC"
    ).fetchall()
    salida = []
    for r in rows:
        d = dict(r)
        d["desglose"] = _desglose_costes(conn, r["trip_id"])
        salida.append(d)
    conn.close()
    return {"borradores": salida}


@app.get("/api/contabilidad/liquidaciones")
def contabilidad_liquidaciones(user: dict = Depends(require_role(["admin"]))):
    """Liquidaciones de conductores autogeneradas (modelo variable por km)."""
    conn = _db()
    rows = conn.execute(
        "SELECT l.id, c.nombre AS conductor, l.viaje_id, l.fecha, "
        "COALESCE(t.km_total, 0) AS km_total, COALESCE(c.tarifa_km, 0) AS tarifa, "
        "l.importe, (CASE WHEN l.pagado THEN 'Pagada' ELSE 'Pendiente' END) AS estado "
        "FROM liquidaciones l "
        "LEFT JOIN conductores c ON l.conductor_id = c.id "
        "LEFT JOIN trips t ON l.viaje_id = t.id "
        "WHERE l.conductor_id IS NOT NULL "
        "ORDER BY l.fecha DESC, l.id DESC"
    ).fetchall()
    conn.close()
    return {"liquidaciones": [dict(r) for r in rows]}


@app.post("/api/contabilidad/borradores/{factura_id}/emitir")
def contabilidad_emitir_borrador(factura_id: int,
                                 user: dict = Depends(require_role(["admin"]))):
    """Valida y emite un borrador: asigna número, publica el asiento y marca 'emitida'."""
    conn = _db()
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Borrador no encontrado"})
    if f["estado"] != "Borrador":
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "La factura ya no está en borrador"})
    fecha = (f["fecha"] or "")[:10] or datetime.date.today().isoformat()
    numero = _factura_numero(conn, fecha)
    total = float(f["total"] or 0)
    base = float(f["base"] or 0)
    cuota = float(f["cuota_iva"] or 0)
    asiento_id = _post_asiento(
        fecha, f"Factura {numero} - {f['cliente_nombre'] or 'varios'}",
        [("430", total, 0, f"Factura {numero}"),
         ("705", 0, base, f"Ventas {numero}"),
         ("477", 0, cuota, f"IVA repercutido {numero}")],
        origen="viaje", documento=numero, conn=conn,
    )
    conn.execute(
        "UPDATE facturas SET estado='emitida', numero=?, asiento_id=? WHERE id=?",
        (numero, asiento_id, factura_id),
    )
    if f["trip_id"]:
        conn.execute("UPDATE trips SET factura=? WHERE id=?", (numero, f["trip_id"]))
    conn.commit()
    conn.close()
    return {"ok": True, "factura": numero, "factura_id": factura_id}


def _generar_factura_pdf(factura_id):
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    conn = _db()
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    lineas = conn.execute("SELECT * FROM factura_lineas WHERE factura_id=? ORDER BY id", (factura_id,)).fetchall()
    cliente = conn.execute("SELECT * FROM clientes WHERE id=?", (f["cliente_id"],)).fetchone() if f["cliente_id"] else None
    emp = _empresa()
    conn.close()

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


def _enviar_email(para, asunto, cuerpo, adjuntos=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    host = _get_config("smtp_host", "")
    port = int(_get_config("smtp_port", "587") or 587)
    user = _get_config("smtp_user", "")
    pwd = _get_config("smtp_password", "")
    from_addr = _get_config("smtp_from", "") or user
    if not host or not user:
        raise HTTPException(status_code=400, detail={"error": "Configura la cuenta de correo saliente (SMTP) en Configuración."})

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = para
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    for nombre, contenido in (adjuntos or []):
        part = MIMEApplication(contenido, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=nombre)
        msg.attach(part)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo(); s.starttls(); s.ehlo()
        s.login(user, pwd)
        s.sendmail(from_addr, [para], msg.as_string())


@app.get("/api/contabilidad/facturas/{factura_id}/pdf")
def factura_pdf(factura_id: int):
    pdf = _generar_factura_pdf(factura_id)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=factura_{factura_id}.pdf"})


@app.post("/api/contabilidad/facturas/{factura_id}/enviar")
def factura_enviar(factura_id: int, req: dict):
    email_to = (req.get("email") or "").strip()
    if not email_to:
        raise HTTPException(status_code=400, detail={"error": "Indica el email del destinatario."})
    conn = _db()
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    conn.close()
    if not f:
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    pdf = _generar_factura_pdf(factura_id)
    asunto = req.get("asunto") or f"Factura {f['numero']}"
    cuerpo = req.get("cuerpo") or f"Adjuntamos la factura {f['numero']}."
    try:
        _enviar_email(email_to, asunto, cuerpo, [(f"factura_{f['numero']}.pdf", pdf)])
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"No se pudo enviar el email: {e}"})
    return {"ok": True}


@app.post("/api/contabilidad/facturas/{factura_id}/cobrar")
def contabilidad_cobrar_factura(factura_id: int):
    conn = _db()
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    if f["estado"] == "cobrada":
        conn.close()
        return {"ok": True, "ya_cobrada": True}
    total = round(float(f["total"] or 0), 2)
    fecha = (f["fecha"] or "")[:10] or datetime.date.today().isoformat()
    asiento_id = _post_asiento(
        fecha, f"Cobro factura {f['numero']}",
        [("572", total, 0, f"Cobro {f['numero']}"),
         ("430", 0, total, f"Cobro {f['numero']}")],
        origen="cobro", documento=f["numero"], conn=conn,
    )
    conn.execute("UPDATE facturas SET estado='cobrada' WHERE id=?", (factura_id,))
    conn.commit()
    conn.close()
    return {"ok": True, "asiento_id": asiento_id}


# ---------------------------------------------------------------------- #
# Recursos Humanos (RRHH): empleados, nóminas, ausencias  →  routers/rrhh.py
# ---------------------------------------------------------------------- #
from routers.rrhh import router as rrhh_router
app.include_router(rrhh_router)
# ---------------------------------------------------------------------- #
# Frontend vanilla retirado: el frontend React se sirve vía nginx (tms-stack).
# ---------------------------------------------------------------------- #
