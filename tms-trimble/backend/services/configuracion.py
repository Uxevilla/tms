"""Configuración de integraciones: proveedores, sus campos y tipos de actividad.

Los proveedores son un catálogo sembrado (Trimble, PTV, TransFollow, SMTP + los del
mercado inactivos). Cada proveedor define sus campos de configuración y sus tipos de
actividad (nombre -> referencia). Los valores por cuenta viven en integracion_valores.

Fase 1: solo siembra y migra la config actual (la lectura de credenciales sigue en `config`
hasta la Fase 2, que pasa a leer de `integracion_valores`).
"""
import psycopg2

import config

# --- Catálogo de proveedores (codigo, nombre, categoria, icono, activo, orden) ---
PROVEEDORES = [
    ("trimble", "Trimble FleetWorks", "telemetria", "", True, 1),
    ("ptv", "PTV (rutas y peajes)", "rutas", "", True, 2),
    ("transfollow", "TransFollow eCMR", "ecmr", "", True, 3),
    ("smtp", "Correo SMTP", "correo", "", True, 4),
    # Proveedores de telemetría del mercado, inactivos (listos para configurar).
    ("geotab", "Geotab", "telemetria", "", False, 10),
    ("webfleet", "Webfleet (Bridgestone)", "telemetria", "", False, 11),
    ("samsara", "Samsara", "telemetria", "", False, 12),
    ("verizon", "Verizon Connect", "telemetria", "", False, 13),
]

# --- Campos de configuración por proveedor (clave, etiqueta, tipo, requerido) ---
CAMPOS = {
    "trimble": [
        ("username", "Usuario SOAP", "texto", True),
        ("password", "Contraseña SOAP", "password", True),
        ("customer", "Customer", "texto", True),
        ("terminal", "Terminal por defecto", "texto", False),
    ],
    "ptv": [
        ("api_key", "API Key", "password", True),
    ],
    "transfollow": [
        ("api_key", "API Key", "password", True),
        ("base_url", "URL base", "url", False),
    ],
    "smtp": [
        ("host", "Host", "texto", True),
        ("port", "Puerto", "numero", True),
        ("user", "Usuario", "texto", False),
        ("password", "Contraseña", "password", False),
        ("from", "Remitente (From)", "texto", False),
    ],
}

# --- Tipos de actividad de Trimble (nombre -> referencia). Seed inicial editable por cuenta. ---
ACTIVIDADES_TRIMBLE = [
    ("CARGA", "040"),
    ("DESCARGA", "041"),
    ("REPOSTAJE", "019"),
    ("DOCUMENTOS", "030"),
    ("MANTENIMIENTO_ISO", "065"),
    ("ITV", "ITV"),
    ("DIETAS", "039"),
    ("FOTO-ESCANEO+", "048"),
    ("ADR", "090"),
    ("LAVADO", "094"),
    ("CAMBIO DE REMOLQUE", "EASOL01"),
    ("DIETAS HNOS INGLES", "DIETAS HNOS INGLES"),
    ("FIN DE JORNADA HNOS INGLES", "FIN DE JORNADA HNOS INGLES"),
    ("INICIO DE JORNADA HNOS INGLES", "INICIO DE JORNADA HNOS INGLES"),
    ("CHECK DIARIO TKN", "CHECK DIARIO TKN"),
]

# --- Migración: clave de `config` -> (proveedor, campo) ---
MIGRACION_CONFIG = {
    "trimble_username": ("trimble", "username"),
    "trimble_password": ("trimble", "password"),
    "trimble_customer": ("trimble", "customer"),
    "trimble_terminal": ("trimble", "terminal"),
    "ptv_api_key": ("ptv", "api_key"),
    "transfollow_api_key": ("transfollow", "api_key"),
    "transfollow_base_url": ("transfollow", "base_url"),
    "smtp_host": ("smtp", "host"),
    "smtp_port": ("smtp", "port"),
    "smtp_user": ("smtp", "user"),
    "smtp_password": ("smtp", "password"),
    "smtp_from": ("smtp", "from"),
}


def _conn(dbname):
    return psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=dbname,
                            user=config.DB_USER, password=config.DB_PASSWORD)


def _seed_integraciones(dbname: str) -> None:
    """Siembra proveedores, campos y actividades (idempotente)."""
    conn = _conn(dbname)
    try:
        cur = conn.cursor()
        for codigo, nombre, categoria, icono, activo, orden in PROVEEDORES:
            cur.execute(
                "INSERT INTO integracion_proveedores (codigo, nombre, categoria, icono, activo, orden) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (codigo) DO NOTHING",
                (codigo, nombre, categoria, icono, activo, orden),
            )
        for codigo, campos in CAMPOS.items():
            cur.execute("SELECT id FROM integracion_proveedores WHERE codigo=%s", (codigo,))
            prov = cur.fetchone()
            if not prov:
                continue
            for orden, (clave, etiqueta, tipo, requerido) in enumerate(campos, 1):
                cur.execute(
                    "INSERT INTO integracion_campos (proveedor_id, clave, etiqueta, tipo, requerido, orden) "
                    "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (proveedor_id, clave) DO NOTHING",
                    (prov[0], clave, etiqueta, tipo, requerido, orden),
                )
        cur.execute("SELECT id FROM integracion_proveedores WHERE codigo='trimble'")
        trimble = cur.fetchone()
        if trimble:
            for nombre, referencia in ACTIVIDADES_TRIMBLE:
                cur.execute(
                    "INSERT INTO actividades (proveedor_id, nombre, referencia) VALUES (%s,%s,%s) "
                    "ON CONFLICT (proveedor_id, nombre) DO NOTHING",
                    (trimble[0], nombre, referencia),
                )
        conn.commit()
    finally:
        conn.close()


def _migrar_config_integraciones(dbname: str) -> None:
    """Migra las claves de integración de `config` a `integracion_valores` (idempotente, no pisa)."""
    conn = _conn(dbname)
    try:
        cur = conn.cursor()
        for config_key, (codigo, campo_clave) in MIGRACION_CONFIG.items():
            cur.execute("SELECT value FROM config WHERE key=%s", (config_key,))
            row = cur.fetchone()
            if not row or not row[0]:
                continue
            cur.execute("SELECT id FROM integracion_proveedores WHERE codigo=%s", (codigo,))
            prov = cur.fetchone()
            if not prov:
                continue
            cur.execute("SELECT id FROM integracion_campos WHERE proveedor_id=%s AND clave=%s",
                        (prov[0], campo_clave))
            campo = cur.fetchone()
            if campo:
                cur.execute(
                    "INSERT INTO integracion_valores (campo_id, valor) VALUES (%s,%s) "
                    "ON CONFLICT (campo_id) DO NOTHING",
                    (campo[0], row[0]),
                )
        conn.commit()
    finally:
        conn.close()
