"""Tenancy: BD maestra, provisionado de clientes, RBAC y resolución de tenant.

Depende de `config`, `db` y `security`; no depende de main ni de services.
"""
import datetime
import os
import secrets

import psycopg2

import config
from db import _db, _Conn, _tenant_ctx
from security import DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD, _hash_password

_master_ready = False

FIRST_TENANT_SLUG = os.environ.get("FIRST_TENANT_SLUG", "eusebio")
FIRST_TENANT_NAME = os.environ.get("FIRST_TENANT_NAME", "Transportes Eusebio")


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
        _seed_integraciones_tenant(config.DB_NAME)
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


def _seed_integraciones_tenant(dbname: str) -> None:
    """Siembra proveedores/campos/actividades y migra la config de integración (idempotente)."""
    from services.configuracion import _seed_integraciones, _migrar_config_integraciones
    _seed_integraciones(dbname)
    _migrar_config_integraciones(dbname)


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
    _seed_integraciones_tenant(db_name)
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


def _es_superadmin():
    t = _tenant_ctx.get()
    return bool(t and t.get("superadmin"))

__all__ = ["_master_ready", "FIRST_TENANT_SLUG", "FIRST_TENANT_NAME",
           "_ensure_master", "_bootstrap", "_seed_tenant_config", "_seed_rbac",
           "_provision_tenant", "_db_master", "_empresa_por_slug", "_es_superadmin"]
