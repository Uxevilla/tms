"""Capa de datos: conexión PostgreSQL (wrapper ? -> %s), esquema y contexto multi-tenant."""
import contextvars
import re
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool

import config
from core import *

_tenant_ctx = contextvars.ContextVar("tenant", default=None)
_usuario_ctx = contextvars.ContextVar("usuario", default="sistema")
_schema_done = set()


class _Conn:
    """Envuelve psycopg2 con interfaz tipo sqlite3; execute() traduce ? -> %s."""

    def __init__(self, conn, pool=None):
        self._conn = conn
        self._pool = pool

    def execute(self, sql, params=()):
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._pool is not None:
            try:
                self._conn.rollback()  # descartar transacción abierta antes de devolverla al pool
            except Exception:
                pass
            self._pool.putconn(self._conn)
        else:
            self._conn.close()

    def set_autocommit(self, enabled):
        self._conn.autocommit = enabled


_SCHEMA = """
CREATE TABLE IF NOT EXISTS clientes (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, cif TEXT, direccion TEXT,
    poblacion TEXT, cp TEXT, telefono TEXT, email TEXT, activo BOOLEAN DEFAULT true,
    cuenta_contable_defecto TEXT DEFAULT '430'
);
CREATE TABLE IF NOT EXISTS conductores (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, dni TEXT, telefono TEXT, email TEXT,
    activo BOOLEAN DEFAULT true, empleado_id TEXT, did TEXT
);
CREATE TABLE IF NOT EXISTS vehiculos (
    id TEXT PRIMARY KEY, categoria TEXT DEFAULT 'tractora', matricula TEXT, marca TEXT, modelo TEXT, anno INTEGER,
    itv TEXT, seguro TEXT, peaje_categoria TEXT, ptv_profile TEXT DEFAULT 'EUR_TRAILER_TRUCK',
    ejes INTEGER, mma INTEGER, clase_euro TEXT, activo BOOLEAN DEFAULT true,
    last_lat NUMERIC(10,7), last_lng NUMERIC(10,7),
    capacidad_peso NUMERIC(10,1) DEFAULT 0, capacidad_palets INTEGER DEFAULT 0,
    fecha_caducidad_itv TEXT, seguro_compania TEXT, fecha_caducidad_seguro TEXT,
    tipo_tenencia TEXT DEFAULT 'Propiedad', proveedor_id INTEGER,
    fecha_alta TEXT, cuota_mensual NUMERIC(12,2) DEFAULT 0, km_actuales NUMERIC(12,1) DEFAULT 0,
    fecha_proxima_revision TEXT, app_terminal TEXT
);
CREATE TABLE IF NOT EXISTS proveedores (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, cif TEXT, direccion TEXT,
    poblacion TEXT, cp TEXT, telefono TEXT, email TEXT,
    cuenta_contable_defecto TEXT DEFAULT '400'
);
CREATE TABLE IF NOT EXISTS categorias_gasto (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY, nombre TEXT, matricula TEXT, conductor TEXT, tipo_carga TEXT,
    origen TEXT, destino TEXT, tareas INTEGER, estado TEXT, error TEXT, creado TEXT,
    terminal TEXT, ecmr_id TEXT, semirremolque_id TEXT, remolque_id TEXT, tareas_estado TEXT, cliente TEXT, precio NUMERIC(12,2) DEFAULT 0,
    km_total NUMERIC(10,1) DEFAULT 0, km_vacio NUMERIC(10,1) DEFAULT 0, tiempo_min NUMERIC(8,1) DEFAULT 0,
    km_inicio NUMERIC(12,1), km_fin NUMERIC(12,1), km_real NUMERIC(10,1), km_fuente TEXT DEFAULT 'planificado',
    pausas_min NUMERIC(8,1) DEFAULT 0, trafico_min NUMERIC(8,1) DEFAULT 0,
    peaje_km NUMERIC(10,1) DEFAULT 0,
    peaje_estimado NUMERIC(10,2) DEFAULT 0, peaje_fuente TEXT,
    gastos NUMERIC(12,2) DEFAULT 0, factura TEXT,
    estado_pago TEXT DEFAULT 'pendiente', iva NUMERIC(5,2) DEFAULT 21,
    cliente_id INTEGER REFERENCES clientes(id), conductor_id INTEGER REFERENCES conductores(id),
    payload TEXT, fecha_actualizacion TEXT,
    fecha_esperada_carga TEXT, fecha_esperada_descarga TEXT
);
CREATE TABLE IF NOT EXISTS paradas (
    id SERIAL PRIMARY KEY, trip_id TEXT REFERENCES trips(id) ON DELETE CASCADE,
    orden INTEGER, nombre TEXT, ciudad TEXT, lat NUMERIC(10,7), lng NUMERIC(10,7),
    actividad TEXT, comentario TEXT
);
CREATE TABLE IF NOT EXISTS tramos (
    id SERIAL PRIMARY KEY, trip_id TEXT REFERENCES trips(id) ON DELETE CASCADE,
    orden INTEGER DEFAULT 1,
    origen_nombre TEXT DEFAULT '', origen_ciudad TEXT DEFAULT '', origen_lat NUMERIC(10,7), origen_lng NUMERIC(10,7),
    destino_nombre TEXT DEFAULT '', destino_ciudad TEXT DEFAULT '', destino_lat NUMERIC(10,7), destino_lng NUMERIC(10,7),
    terminal TEXT DEFAULT '', conductor TEXT DEFAULT '',
    km_total NUMERIC(10,1) DEFAULT 0, km_real NUMERIC(10,1), km_fuente TEXT DEFAULT 'planificado',
    estado TEXT DEFAULT 'planificado',
    fecha_carga TEXT, fecha_descarga TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS direcciones (
    id SERIAL PRIMARY KEY, nombre TEXT, empresa TEXT, calle TEXT, numero TEXT,
    ciudad TEXT, cp TEXT, pais TEXT DEFAULT 'ES',
    lat NUMERIC(10,7), lng NUMERIC(10,7), comentario TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS gastos (
    id SERIAL PRIMARY KEY, terminal TEXT, trip_id TEXT, categoria TEXT, fecha TEXT,
    importe NUMERIC(12,2) DEFAULT 0, concepto TEXT, foto TEXT, creado TEXT,
    proveedor_id INTEGER REFERENCES proveedores(id),
    categoria_id INTEGER REFERENCES categorias_gasto(id)
);
CREATE TABLE IF NOT EXISTS gastos_vehiculos (
    id SERIAL PRIMARY KEY, vehiculo_id TEXT, proveedor_id INTEGER REFERENCES proveedores(id),
    fecha TEXT, tipo TEXT, litros NUMERIC(10,2) DEFAULT 0,
    base_imponible NUMERIC(12,2) DEFAULT 0, iva NUMERIC(6,2) DEFAULT 21,
    importe_total NUMERIC(12,2) DEFAULT 0, factura_ref TEXT,
    cuenta_contable_gasto TEXT, estado_pago TEXT DEFAULT 'Pendiente',
    archivo_base64 TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS costes_fijos (
    id SERIAL PRIMARY KEY, terminal TEXT, concepto TEXT, importe NUMERIC(12,2) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tarifas_peaje (
    categoria TEXT PRIMARY KEY, eur_km NUMERIC(8,4) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY, trip_id TEXT, vehiculo_id TEXT, name TEXT UNIQUE, ftype INTEGER, ftime TEXT,
    source TEXT, driver TEXT, lid TEXT, content_b64 TEXT, formato TEXT
);
CREATE TABLE IF NOT EXISTS mensajes (
    id TEXT PRIMARY KEY, trip_id TEXT, tipo TEXT, messagetype TEXT,
    originid TEXT, source TEXT, terminal TEXT, subject TEXT, body TEXT,
    time TEXT, needreply BOOLEAN DEFAULT false, creado TEXT
);
CREATE TABLE IF NOT EXISTS sync_state (
    key TEXT PRIMARY KEY, value TEXT
);
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY, value TEXT
);
-- RBAC: esquema `config` (roles/usuarios). Distinto de la tabla public.config de arriba.
CREATE SCHEMA IF NOT EXISTS config;
CREATE TABLE IF NOT EXISTS config.roles (
    id SERIAL PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, descripcion TEXT
);
CREATE TABLE IF NOT EXISTS config.usuarios (
    id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    rol TEXT NOT NULL REFERENCES config.roles(nombre), nombre TEXT,
    activo BOOLEAN NOT NULL DEFAULT true, creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS telemetria (
    time TIMESTAMPTZ NOT NULL,
    source TEXT,
    vehiculo_id TEXT,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION,
    speed DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    mileage DOUBLE PRECISION
);
CREATE TABLE IF NOT EXISTS mantenimientos (
    id SERIAL PRIMARY KEY, vehiculo_id TEXT, tipo TEXT, fecha TEXT, km INTEGER,
    coste NUMERIC(10,2) DEFAULT 0, notas TEXT, hecho BOOLEAN DEFAULT false, fecha_fin TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS ecmr (
    id BIGSERIAL PRIMARY KEY, viaje_id TEXT, viaje_valido BOOLEAN DEFAULT false,
    vehiculo_id TEXT, matricula TEXT, origen TEXT, destino TEXT, mercancia TEXT,
    payload_hash TEXT UNIQUE, recibido_en TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS inspecciones (
    id BIGSERIAL PRIMARY KEY, reporte_id TEXT, vehiculo_id TEXT, matricula TEXT, vin TEXT,
    payload_hash TEXT UNIQUE, recibido_en TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS alertas_mantenimiento (
    id BIGSERIAL PRIMARY KEY, inspeccion_id BIGINT REFERENCES inspecciones(id) ON DELETE CASCADE,
    vehiculo_id TEXT, codigo TEXT, severidad TEXT, mensaje TEXT,
    estado TEXT DEFAULT 'abierta', creado_en TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alertas_vehiculo_estado ON alertas_mantenimiento (vehiculo_id, estado);
-- Telemetría (nueva arquitectura): posiciones con odómetro (la escribe ingest_worker).
CREATE SCHEMA IF NOT EXISTS telemetria;
CREATE TABLE IF NOT EXISTS telemetria.posiciones_gps (
    time TIMESTAMPTZ NOT NULL,
    vehiculo_id TEXT NOT NULL,
    viaje_id TEXT,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    speed_kmh DOUBLE PRECISION,
    heading DOUBLE PRECISION,
    odometer_km DOUBLE PRECISION,
    ignicion BOOLEAN,
    fuente TEXT
);
-- Mantenimiento predictivo (reglas + alertas).
CREATE SCHEMA IF NOT EXISTS flota;
CREATE TABLE IF NOT EXISTS flota.reglas_mantenimiento (
    id SERIAL PRIMARY KEY,
    vehiculo_id TEXT NOT NULL,
    tipo_mantenimiento TEXT NOT NULL,
    intervalo_km NUMERIC(12,1) NOT NULL DEFAULT 0,
    ultimo_km_realizado NUMERIC(12,1) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS flota.alertas_mantenimiento (
    id SERIAL PRIMARY KEY,
    vehiculo_id TEXT NOT NULL,
    tipo_mantenimiento TEXT NOT NULL,
    km_actual NUMERIC(12,1),
    estado TEXT DEFAULT 'Pendiente',
    creado_en TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS transportistas (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL, cif TEXT, telefono TEXT, email TEXT, tarifa NUMERIC(10,3) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS liquidaciones (
    id SERIAL PRIMARY KEY, transportista_id INTEGER REFERENCES transportistas(id),
    fecha TEXT, importe NUMERIC(12,2) DEFAULT 0, concepto TEXT, pagado BOOLEAN DEFAULT false, creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_trips_creado ON trips(creado);
CREATE INDEX IF NOT EXISTS idx_trips_terminal ON trips(terminal);
CREATE INDEX IF NOT EXISTS idx_trips_cliente ON trips(cliente_id);
CREATE INDEX IF NOT EXISTS idx_gastos_terminal ON gastos(terminal);
CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos(fecha);
CREATE INDEX IF NOT EXISTS idx_gastos_proveedor ON gastos(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_paradas_trip ON paradas(trip_id);
CREATE TABLE IF NOT EXISTS cuentas (
    codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL,
    grupo TEXT NOT NULL, tipo TEXT NOT NULL, orden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS asientos (
    id SERIAL PRIMARY KEY, numero INTEGER NOT NULL, fecha TEXT NOT NULL,
    concepto TEXT NOT NULL, documento TEXT, origen TEXT NOT NULL DEFAULT 'manual',
    origen_id TEXT, trip_id TEXT, gasto_id INTEGER, creado TEXT
);
CREATE TABLE IF NOT EXISTS apuntes (
    id SERIAL PRIMARY KEY, asiento_id INTEGER NOT NULL REFERENCES asientos(id) ON DELETE CASCADE,
    cuenta TEXT NOT NULL REFERENCES cuentas(codigo),
    debe NUMERIC(12,2) DEFAULT 0, haber NUMERIC(12,2) DEFAULT 0, concepto TEXT
);
CREATE INDEX IF NOT EXISTS idx_asientos_fecha ON asientos(fecha);
CREATE INDEX IF NOT EXISTS idx_asientos_numero ON asientos(numero);
CREATE INDEX IF NOT EXISTS idx_apuntes_asiento ON apuntes(asiento_id);
CREATE TABLE IF NOT EXISTS facturas (
    id SERIAL PRIMARY KEY, numero TEXT NOT NULL, fecha TEXT NOT NULL,
    trip_id TEXT, cliente_id INTEGER, cliente_nombre TEXT,
    base NUMERIC(12,2) DEFAULT 0, iva NUMERIC(5,2) DEFAULT 21,
    cuota_iva NUMERIC(12,2) DEFAULT 0, total NUMERIC(12,2) DEFAULT 0,
    estado TEXT DEFAULT 'emitida', asiento_id INTEGER, creado TEXT
);
CREATE TABLE IF NOT EXISTS factura_lineas (
    id SERIAL PRIMARY KEY, factura_id INTEGER NOT NULL REFERENCES facturas(id) ON DELETE CASCADE,
    trip_id TEXT, concepto TEXT, base NUMERIC(12,2) DEFAULT 0,
    iva NUMERIC(5,2) DEFAULT 21, cuota_iva NUMERIC(12,2) DEFAULT 0, total NUMERIC(12,2) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS empresa (
    id INTEGER PRIMARY KEY, nombre TEXT, cif TEXT, direccion TEXT, poblacion TEXT,
    cp TEXT, pais TEXT DEFAULT 'ES', telefono TEXT, email TEXT, web TEXT,
    iva NUMERIC(5,2) DEFAULT 21, iban TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS empleados (
    id TEXT PRIMARY KEY, nombre TEXT NOT NULL, apellidos TEXT, dni TEXT, nss TEXT,
    email TEXT, telefono TEXT, direccion TEXT, ciudad TEXT, cp TEXT,
    fecha_alta TEXT, fecha_baja TEXT, categoria TEXT DEFAULT 'Conductor', puesto TEXT,
    tipo_contrato TEXT DEFAULT 'Indefinido', jornada TEXT DEFAULT 'Completa',
    banco TEXT, iban TEXT, titular TEXT,
    caducidad_carnet TEXT, caducidad_cap TEXT, caducidad_medica TEXT,
    salario_bruto NUMERIC(12,2) DEFAULT 0, irpf NUMERIC(5,2) DEFAULT 15,
    disponibilidad TEXT DEFAULT 'disponible', motivo_no_dispo TEXT,
    convenio TEXT, observaciones TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS nominas (
    id SERIAL PRIMARY KEY, empleado_id TEXT REFERENCES empleados(id) ON DELETE CASCADE,
    periodo TEXT, salario_bruto NUMERIC(12,2) DEFAULT 0, irpf_pct NUMERIC(5,2) DEFAULT 15,
    ss_trabajador_pct NUMERIC(5,2) DEFAULT 6.35, ss_empresa_pct NUMERIC(5,2) DEFAULT 30.0,
    ss_trabajador NUMERIC(12,2) DEFAULT 0, ss_empresa NUMERIC(12,2) DEFAULT 0,
    irpf_importe NUMERIC(12,2) DEFAULT 0, neto NUMERIC(12,2) DEFAULT 0,
    coste_empresa NUMERIC(12,2) DEFAULT 0,
    estado TEXT DEFAULT 'borrador', pagado BOOLEAN DEFAULT false, fecha_pago TEXT,
    contabilizado BOOLEAN DEFAULT false, asiento_id INTEGER, notas TEXT, creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_nominas_empleado ON nominas(empleado_id);
CREATE INDEX IF NOT EXISTS idx_nominas_periodo ON nominas(periodo);
CREATE TABLE IF NOT EXISTS ausencias (
    id SERIAL PRIMARY KEY, empleado_id TEXT REFERENCES empleados(id) ON DELETE CASCADE,
    tipo TEXT DEFAULT 'Vacaciones', fecha_inicio TEXT, fecha_fin TEXT, dias NUMERIC(5,1) DEFAULT 0,
    estado TEXT DEFAULT 'Pendiente', nota TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS ausencias_empleados (
    id SERIAL PRIMARY KEY, empleado_id TEXT REFERENCES empleados(id) ON DELETE CASCADE,
    fecha_inicio TEXT NOT NULL, fecha_fin TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'vacaciones', observaciones TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS saldos_pales (
    id SERIAL PRIMARY KEY,
    cliente_id INTEGER NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
    viaje_id TEXT,
    entregados INTEGER NOT NULL DEFAULT 0,
    recuperados INTEGER NOT NULL DEFAULT 0,
    balance INTEGER NOT NULL DEFAULT 0,
    fecha TEXT, creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_saldos_pales_cliente ON saldos_pales(cliente_id);
CREATE TABLE IF NOT EXISTS tacografo_estados (
    id SERIAL PRIMARY KEY,
    driver_id TEXT NOT NULL,
    tipo TEXT,
    start_time TEXT, end_time TEXT,
    duration_min NUMERIC(10,1) DEFAULT 0,
    creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_tacografo_driver ON tacografo_estados(driver_id, start_time);
CREATE TABLE IF NOT EXISTS tacografo_dstat (
    id SERIAL PRIMARY KEY,
    did TEXT NOT NULL,
    source TEXT,
    vehiculo_id TEXT,
    dstat_raw TEXT,
    driving_coupure_min NUMERIC(10,1) DEFAULT 0,
    day_driving_min NUMERIC(10,1) DEFAULT 0,
    day_working_min NUMERIC(10,1) DEFAULT 0,
    day_resting_min NUMERIC(10,1) DEFAULT 0,
    week_driving_min NUMERIC(10,1) DEFAULT 0,
    remaining_week_available_min NUMERIC(10,1) DEFAULT 0,
    week_long_driving_count INTEGER DEFAULT 0,
    next_rest_due_ts BIGINT DEFAULT 0,
    time TEXT,
    creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_tacografo_dstat_veh ON tacografo_dstat(vehiculo_id, time);
CREATE INDEX IF NOT EXISTS idx_tacografo_dstat_did ON tacografo_dstat(did, time);
CREATE TABLE IF NOT EXISTS lineas_nomina (
    id SERIAL PRIMARY KEY,
    nomina_id INTEGER NOT NULL REFERENCES nominas(id) ON DELETE CASCADE,
    concepto TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'devengo',
    importe NUMERIC(12,2) NOT NULL DEFAULT 0,
    creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_lineas_nomina_nomina ON lineas_nomina(nomina_id);
CREATE INDEX IF NOT EXISTS idx_ausencias_empleado ON ausencias(empleado_id);
CREATE TABLE IF NOT EXISTS audit_log (
    id SERIAL PRIMARY KEY,
    tabla TEXT NOT NULL,
    registro_id TEXT,
    accion TEXT NOT NULL,
    usuario TEXT DEFAULT 'sistema',
    antes TEXT,
    despues TEXT,
    ts TEXT
);
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE clientes ADD COLUMN IF NOT EXISTS borrado_en TEXT;
ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS borrado_en TEXT;
ALTER TABLE asientos ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE asientos ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE asientos ADD COLUMN IF NOT EXISTS borrado_en TEXT;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE facturas ADD COLUMN IF NOT EXISTS borrado_en TEXT;
"""


_pools = {}
_pools_lock = threading.Lock()


def _pool_for(dbname):
    with _pools_lock:
        p = _pools.get(dbname)
        if p is None:
            p = psycopg2.pool.ThreadedConnectionPool(
                2, 20,
                host=config.DB_HOST, port=config.DB_PORT, dbname=dbname,
                user=config.DB_USER, password=config.DB_PASSWORD,
            )
            _pools[dbname] = p
        return p


def _db():
    t = _tenant_ctx.get()
    dbname = t["db_name"] if t else config.DB_NAME
    pool = _pool_for(dbname)
    conn = pool.getconn()
    if t and t.get("superadmin"):
        # El superadmin no inicializa el esquema de tenant (la BD maestra solo tiene 'empresas')
        return _Conn(conn, pool)
    if dbname not in _schema_done:
        try:
            cur = conn.cursor()
            # No bloquear la BD si otra conexión retiene locks: la migración falla
            # rápido (se reintenta en la próxima _db()) en vez de encadenar locks.
            cur.execute("SET LOCAL lock_timeout = '15000'")
            cur.execute(_SCHEMA)
            # TimescaleDB: convertir telemetria en hypertable (particionado por tiempo).
            # Si la extensión no está disponible, queda como tabla normal (degradación limpia).
            try:
                cur.execute("SELECT create_hypertable('telemetria', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
            except Exception:
                pass
            cur.execute("CREATE INDEX IF NOT EXISTS idx_telemetria_veh_time ON telemetria (vehiculo_id, time DESC)")
            try:
                cur.execute("SELECT create_hypertable('telemetria.posiciones_gps', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
            except Exception:
                pass
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_posiciones_vehiculo_time ON telemetria.posiciones_gps (vehiculo_id, time)")
            cur.execute("INSERT INTO empresa (id, nombre, pais, iva) VALUES (1, '', 'ES', 21) ON CONFLICT (id) DO NOTHING")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS itv TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS seguro TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS peaje_categoria TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS ptv_profile TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS ejes INTEGER")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS mma INTEGER")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS clase_euro TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS categoria TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS last_lat NUMERIC(10,7)")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS last_lng NUMERIC(10,7)")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS last_position_time TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS device TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS app_terminal TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS capacidad_peso NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS capacidad_palets INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS coste_adquisicion NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS fecha_adquisicion TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS vida_util INTEGER DEFAULT 5")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS valor_residual NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS fecha_caducidad_itv TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS seguro_compania TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS fecha_caducidad_seguro TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS tipo_tenencia TEXT DEFAULT 'Propiedad'")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS proveedor_id INTEGER")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS fecha_alta TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS cuota_mensual NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS vehiculo_id TEXT")
            cur.execute("ALTER TABLE asientos ADD COLUMN IF NOT EXISTS origen_id TEXT")
            cur.execute("ALTER TABLE conductores ADD COLUMN IF NOT EXISTS did TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS terminal TEXT")
            cur.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS cuenta_contable_defecto TEXT DEFAULT '430'")
            cur.execute("ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS cuenta_contable_defecto TEXT DEFAULT '400'")
            cur.execute("ALTER TABLE gastos_vehiculos ADD COLUMN IF NOT EXISTS base_imponible NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE gastos_vehiculos ADD COLUMN IF NOT EXISTS iva NUMERIC(6,2) DEFAULT 21")
            cur.execute("ALTER TABLE gastos_vehiculos ADD COLUMN IF NOT EXISTS cuenta_contable_gasto TEXT")
            cur.execute("ALTER TABLE gastos_vehiculos ADD COLUMN IF NOT EXISTS estado_pago TEXT DEFAULT 'Pendiente'")
            cur.execute("ALTER TABLE mantenimientos ADD COLUMN IF NOT EXISTS fecha_fin TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_carnet TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_cap TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_medica TEXT")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS km_actuales NUMERIC(12,1) DEFAULT 0")
            cur.execute("ALTER TABLE vehiculos ADD COLUMN IF NOT EXISTS fecha_proxima_revision TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS fecha_actualizacion TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS fecha_esperada_carga TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS fecha_esperada_descarga TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS km_vacio NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS semirremolque_id TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS remolque_id TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS payload TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS referencia TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS ecmr_id TEXT")
            cur.execute("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS coste NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE facturas ADD COLUMN IF NOT EXISTS margen NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE conductores ADD COLUMN IF NOT EXISTS tarifa_km NUMERIC(10,3) DEFAULT 0")
            cur.execute("ALTER TABLE liquidaciones ADD COLUMN IF NOT EXISTS conductor_id INTEGER")
            cur.execute("ALTER TABLE liquidaciones ADD COLUMN IF NOT EXISTS viaje_id TEXT")
            cur.execute("ALTER TABLE gastos ADD COLUMN IF NOT EXISTS trip_id TEXT")
            cur.execute("ALTER TABLE gastos ADD COLUMN IF NOT EXISTS iva NUMERIC(5,2) DEFAULT 21")
            cur.execute("ALTER TABLE gastos ADD COLUMN IF NOT EXISTS retencion NUMERIC(5,2) DEFAULT 0")
            cur.execute("ALTER TABLE gastos ADD COLUMN IF NOT EXISTS pagado BOOLEAN DEFAULT false")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS peaje_km NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS peaje_estimado NUMERIC(10,2) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS peaje_fuente TEXT")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS tiempo_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS pausas_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS trafico_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS origen_id INTEGER")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS destino_id INTEGER")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS km_inicio NUMERIC(12,1)")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS km_fin NUMERIC(12,1)")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS km_real NUMERIC(10,1)")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS km_fuente TEXT DEFAULT 'planificado'")
            # Tarifas + valoración del viaje (km/viaje/kilos) y subcontratación.
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS modo_tarifa TEXT DEFAULT 'viaje'")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS tarifa_id INTEGER")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS precio_unitario NUMERIC(12,3)")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS kilos NUMERIC(12,1) DEFAULT 0")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS subcontratado BOOLEAN DEFAULT false")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS proveedor_id INTEGER")
            cur.execute("ALTER TABLE trips ADD COLUMN IF NOT EXISTS coste NUMERIC(12,2) DEFAULT 0")
            cur.execute("""CREATE TABLE IF NOT EXISTS tarifas (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                tipo TEXT NOT NULL DEFAULT 'viaje' CHECK (tipo IN ('km','viaje','kilos')),
                precio NUMERIC(12,3) NOT NULL DEFAULT 0,
                cliente_id INTEGER,
                activo BOOLEAN DEFAULT true,
                creado_en TEXT
            )""")
            # Auditoría contable + soft delete (nada se borra físicamente en contabilidad).
            cur.execute("""CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                tabla TEXT NOT NULL,
                registro_id TEXT,
                accion TEXT NOT NULL,
                usuario TEXT DEFAULT 'sistema',
                antes TEXT,
                despues TEXT,
                ts TEXT
            )""")
            for _t in ("clientes", "proveedores", "asientos", "facturas"):
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false")
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado_por TEXT")
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado_en TEXT")
            # Backfill: asignar referencia interna a viajes existentes (por orden de creación)
            cur.execute("SELECT id FROM trips WHERE referencia IS NULL OR referencia = '' ORDER BY creado ASC")
            _pend = [r[0] for r in cur.fetchall()]
            if _pend:
                _n = 0
                cur.execute("SELECT referencia FROM trips WHERE referencia IS NOT NULL AND referencia != ''")
                for _r in cur.fetchall():
                    _m = re.match(r"^V-(\d+)$", (_r[0] or "").strip())
                    if _m:
                        _n = max(_n, int(_m.group(1)))
                for _tid in _pend:
                    _n += 1
                    cur.execute("UPDATE trips SET referencia=%s WHERE id=%s", (f"V-{_n:04d}", _tid))
            for c in _CATEGORIAS:
                cur.execute(
                    "INSERT INTO categorias_gasto (nombre) VALUES (%s) "
                    "ON CONFLICT (nombre) DO NOTHING",
                    (c,),
                )
            cur.execute("ALTER TABLE gastos ADD COLUMN IF NOT EXISTS cuenta TEXT")
            cur.execute("ALTER TABLE categorias_gasto ADD COLUMN IF NOT EXISTS cuenta TEXT")
            cur.execute("ALTER TABLE conductores ADD COLUMN IF NOT EXISTS empleado_id TEXT")
            for cat, cuenta in _CATEGORIA_CUENTA.items():
                cur.execute("UPDATE categorias_gasto SET cuenta=%s WHERE nombre=%s AND cuenta IS NULL", (cuenta, cat))
            for cat, info in _PEAJE_CATEGORIAS.items():
                cur.execute(
                    "INSERT INTO tarifas_peaje (categoria, eur_km) VALUES (%s, %s) "
                    "ON CONFLICT (categoria) DO NOTHING",
                    (cat, info["eur_km"]),
                )
            for cod, nom, grupo, tipo, orden in _PLAN_CONTABLE:
                cur.execute(
                    "INSERT INTO cuentas (codigo, nombre, grupo, tipo, orden) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (codigo) DO NOTHING",
                    (cod, nom, grupo, tipo, orden),
                )
            conn.commit()
            cur.close()
            _schema_done.add(dbname)
        except Exception:
            conn.rollback()
    return _Conn(conn, pool)

def _get_config(key, default=""):
    conn = _db()
    row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def get_conn():
    """Dependencia FastAPI: una conexión por request, cerrada SIEMPRE (try/finally).

    Uso: `def list_clientes(conn = Depends(get_conn)): ...` — elimina las fugas de
    conexión del pool (el `finally` devuelve la conexión aunque el endpoint lance).
    """
    conn = _db()
    try:
        yield conn
    finally:
        conn.close()


__all__ = ["_tenant_ctx", "_usuario_ctx", "_schema_done", "_Conn", "_SCHEMA", "_db", "_get_config", "get_conn"]