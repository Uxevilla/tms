"""Capa de datos: conexión PostgreSQL (wrapper ? -> %s), esquema y contexto multi-tenant."""
import contextvars
import re
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool

import config
from core import *
from crypto import _decrypt_valor

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
        if self._pool is None:
            self._conn.close()
            return
        try:
            if self._conn.autocommit:
                self._conn.autocommit = False  # restaurar: si no, la próxima petición hace rollback() y no deshace nada
            else:
                self._conn.rollback()  # descartar transacción abierta antes de devolverla al pool
            self._pool.putconn(self._conn)
        except Exception:
            self._pool.putconn(self._conn, close=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def set_autocommit(self, enabled):
        self._conn.autocommit = enabled


_SCHEMA = """
CREATE SCHEMA IF NOT EXISTS sistema;
CREATE SCHEMA IF NOT EXISTS config;
CREATE SCHEMA IF NOT EXISTS maestros;
CREATE SCHEMA IF NOT EXISTS rrhh;
CREATE SCHEMA IF NOT EXISTS flota;
CREATE SCHEMA IF NOT EXISTS operaciones;
CREATE SCHEMA IF NOT EXISTS telemetria;
CREATE SCHEMA IF NOT EXISTS finanzas;
CREATE SCHEMA IF NOT EXISTS docs;
CREATE TABLE IF NOT EXISTS flota.vehiculos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo TEXT UNIQUE, terminal_trimble TEXT UNIQUE,
    categoria TEXT DEFAULT 'tractora', matricula TEXT, marca TEXT, modelo TEXT, anno INTEGER,
    itv TEXT, seguro TEXT, peaje_categoria TEXT, ptv_profile TEXT DEFAULT 'EUR_TRAILER_TRUCK',
    ejes INTEGER, mma INTEGER, clase_euro TEXT, activo BOOLEAN DEFAULT true,
    last_lat NUMERIC(10,7), last_lng NUMERIC(10,7),
    capacidad_peso NUMERIC(10,1) DEFAULT 0, capacidad_palets INTEGER DEFAULT 0,
    fecha_caducidad_itv TEXT, seguro_compania TEXT, fecha_caducidad_seguro TEXT,
    tipo_tenencia TEXT DEFAULT 'Propiedad', proveedor_id INTEGER,
    fecha_alta TEXT, cuota_mensual NUMERIC(12,2) DEFAULT 0, km_actuales NUMERIC(12,1) DEFAULT 0,
    fecha_proxima_revision TEXT, app_terminal TEXT
);
CREATE TABLE IF NOT EXISTS categorias_gasto (
    id SERIAL PRIMARY KEY, nombre TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS operaciones.trips (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo TEXT UNIQUE, nombre TEXT, matricula TEXT, conductor TEXT, tipo_carga TEXT,
    origen TEXT, destino TEXT, tareas INTEGER, estado TEXT, error TEXT, creado TEXT,
    terminal TEXT, ecmr_id TEXT, semirremolque_id TEXT, remolque_id TEXT, tareas_estado TEXT, cliente TEXT, precio NUMERIC(12,2) DEFAULT 0,
    km_total NUMERIC(10,1) DEFAULT 0, km_vacio NUMERIC(10,1) DEFAULT 0, tiempo_min NUMERIC(8,1) DEFAULT 0,
    km_inicio NUMERIC(12,1), km_fin NUMERIC(12,1), km_real NUMERIC(10,1), km_fuente TEXT DEFAULT 'planificado',
    pausas_min NUMERIC(8,1) DEFAULT 0, trafico_min NUMERIC(8,1) DEFAULT 0,
    peaje_km NUMERIC(10,1) DEFAULT 0,
    peaje_estimado NUMERIC(10,2) DEFAULT 0, peaje_fuente TEXT,
    gastos NUMERIC(12,2) DEFAULT 0, factura TEXT,
    estado_pago TEXT DEFAULT 'pendiente', iva NUMERIC(5,2) DEFAULT 21,
    cliente_id INTEGER, conductor_id INTEGER,
    payload TEXT, fecha_actualizacion TEXT,
    fecha_esperada_carga TEXT, fecha_esperada_descarga TEXT
);
CREATE TABLE IF NOT EXISTS operaciones.paradas (
    id SERIAL PRIMARY KEY, trip_id TEXT REFERENCES operaciones.trips(codigo) ON DELETE CASCADE,
    orden INTEGER, nombre TEXT, ciudad TEXT, lat NUMERIC(10,7), lng NUMERIC(10,7),
    actividad TEXT, comentario TEXT
);
CREATE TABLE IF NOT EXISTS operaciones.tramos (
    id SERIAL PRIMARY KEY, trip_id TEXT REFERENCES operaciones.trips(codigo) ON DELETE CASCADE,
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
CREATE TABLE IF NOT EXISTS finanzas.gastos (
    id SERIAL PRIMARY KEY, terminal TEXT, trip_id TEXT, categoria TEXT, fecha TEXT,
    importe NUMERIC(12,2) DEFAULT 0, concepto TEXT, foto TEXT, creado TEXT,
    proveedor_id INTEGER,
    categoria_id INTEGER REFERENCES categorias_gasto(id)
);
CREATE TABLE IF NOT EXISTS finanzas.gastos_vehiculos (
    id SERIAL PRIMARY KEY, vehiculo_id TEXT, proveedor_id INTEGER,
    fecha TEXT, tipo TEXT, litros NUMERIC(10,2) DEFAULT 0,
    base_imponible NUMERIC(12,2) DEFAULT 0, iva NUMERIC(6,2) DEFAULT 21,
    importe_total NUMERIC(12,2) DEFAULT 0, factura_ref TEXT,
    cuenta_contable_gasto TEXT, estado_pago TEXT DEFAULT 'Pendiente',
    archivo_base64 TEXT, creado TEXT
);
CREATE TABLE IF NOT EXISTS finanzas.costes_fijos (
    id SERIAL PRIMARY KEY, terminal TEXT, concepto TEXT, importe NUMERIC(12,2) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tarifas_peaje (
    categoria TEXT PRIMARY KEY, eur_km NUMERIC(8,4) DEFAULT 0
);
CREATE TABLE IF NOT EXISTS files (
    id SERIAL PRIMARY KEY, trip_id TEXT, vehiculo_id TEXT, name TEXT UNIQUE, ftype INTEGER, ftime TEXT,
    source TEXT, driver TEXT, lid TEXT, content_b64 TEXT, formato TEXT
);
CREATE TABLE IF NOT EXISTS documentos_auditoria (
    id BIGSERIAL PRIMARY KEY, file_id INT, accion TEXT, usuario TEXT, detalle TEXT, creado TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS lid_map (
    lid TEXT PRIMARY KEY, trip_id TEXT, task_id TEXT, terminal TEXT, desde TEXT
);
CREATE TABLE IF NOT EXISTS qp_definiciones (
    id BIGSERIAL PRIMARY KEY, report_id TEXT NOT NULL, version TEXT NOT NULL DEFAULT '',
    nombre TEXT, firstquestion TEXT, activa BOOLEAN NOT NULL DEFAULT true,
    xml_original TEXT, importado_por TEXT, importado_en TIMESTAMPTZ DEFAULT now(),
    UNIQUE (report_id, version)
);
CREATE TABLE IF NOT EXISTS qp_preguntas (
    id BIGSERIAL PRIMARY KEY, definicion_id BIGINT NOT NULL REFERENCES qp_definiciones(id) ON DELETE CASCADE,
    question_id TEXT NOT NULL, texto TEXT, selectionmodel TEXT DEFAULT 'single',
    hide BOOLEAN DEFAULT false, orden INT,
    UNIQUE (definicion_id, question_id)
);
CREATE TABLE IF NOT EXISTS qp_opciones (
    id BIGSERIAL PRIMARY KEY, pregunta_id BIGINT NOT NULL REFERENCES qp_preguntas(id) ON DELETE CASCADE,
    option_id TEXT NOT NULL, texto TEXT, valuetype TEXT DEFAULT 'text', inputmask TEXT DEFAULT '',
    readonly BOOLEAN DEFAULT false, nextquestion TEXT DEFAULT 'END', orden INT,
    UNIQUE (pregunta_id, option_id)
);
CREATE TABLE IF NOT EXISTS cfg_reglas (
    id BIGSERIAL PRIMARY KEY, report_id TEXT NOT NULL, question_id TEXT, option_id TEXT,
    estado TEXT NOT NULL, activa BOOLEAN NOT NULL DEFAULT true, nota TEXT
);
CREATE TABLE IF NOT EXISTS cfg_docs_requeridos (
    id BIGSERIAL PRIMARY KEY, cliente_id INTEGER, tipo_documento TEXT NOT NULL,
    requerido BOOLEAN NOT NULL DEFAULT true, orden INT DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mensajes (
    id TEXT PRIMARY KEY, trip_id TEXT, tipo TEXT, messagetype TEXT,
    originid TEXT, source TEXT, terminal TEXT, subject TEXT, body TEXT,
    time TEXT, needreply BOOLEAN DEFAULT false, creado TEXT
);
CREATE TABLE IF NOT EXISTS flota.mantenimientos (
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
CREATE TABLE IF NOT EXISTS finanzas.liquidaciones (
    id SERIAL PRIMARY KEY, transportista_id INTEGER,
    fecha TEXT, importe NUMERIC(12,2) DEFAULT 0, concepto TEXT, pagado BOOLEAN DEFAULT false, creado TEXT
);
CREATE INDEX IF NOT EXISTS idx_trips_creado ON operaciones.trips(creado);
CREATE INDEX IF NOT EXISTS idx_trips_terminal ON operaciones.trips(terminal);
CREATE INDEX IF NOT EXISTS idx_trips_cliente ON operaciones.trips(cliente_id);
CREATE INDEX IF NOT EXISTS idx_gastos_terminal ON finanzas.gastos(terminal);
CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON finanzas.gastos(fecha);
CREATE INDEX IF NOT EXISTS idx_gastos_proveedor ON finanzas.gastos(proveedor_id);
CREATE INDEX IF NOT EXISTS idx_paradas_trip ON operaciones.paradas(trip_id);
CREATE TABLE IF NOT EXISTS finanzas.cuentas (
    codigo TEXT PRIMARY KEY, nombre TEXT NOT NULL,
    grupo TEXT NOT NULL, tipo TEXT NOT NULL, orden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finanzas.asientos (
    id SERIAL PRIMARY KEY, numero INTEGER NOT NULL, fecha TEXT NOT NULL,
    concepto TEXT NOT NULL, documento TEXT, origen TEXT NOT NULL DEFAULT 'manual',
    origen_id TEXT, trip_id TEXT, gasto_id INTEGER, creado TEXT
);
CREATE TABLE IF NOT EXISTS finanzas.apuntes (
    id SERIAL PRIMARY KEY, asiento_id INTEGER NOT NULL REFERENCES finanzas.asientos(id) ON DELETE CASCADE,
    cuenta TEXT NOT NULL REFERENCES finanzas.cuentas(codigo),
    debe NUMERIC(12,2) DEFAULT 0, haber NUMERIC(12,2) DEFAULT 0, concepto TEXT
);
CREATE INDEX IF NOT EXISTS idx_asientos_fecha ON finanzas.asientos(fecha);
CREATE INDEX IF NOT EXISTS idx_asientos_numero ON finanzas.asientos(numero);
CREATE INDEX IF NOT EXISTS idx_apuntes_asiento ON finanzas.apuntes(asiento_id);
CREATE TABLE IF NOT EXISTS finanzas.facturas (
    id SERIAL PRIMARY KEY, numero TEXT NOT NULL, fecha TEXT NOT NULL,
    trip_id TEXT, cliente_id INTEGER, cliente_nombre TEXT,
    base NUMERIC(12,2) DEFAULT 0, iva NUMERIC(5,2) DEFAULT 21,
    cuota_iva NUMERIC(12,2) DEFAULT 0, total NUMERIC(12,2) DEFAULT 0,
    estado TEXT DEFAULT 'emitida', asiento_id INTEGER, creado TEXT
);
CREATE TABLE IF NOT EXISTS finanzas.factura_lineas (
    id SERIAL PRIMARY KEY, factura_id INTEGER NOT NULL REFERENCES finanzas.facturas(id) ON DELETE CASCADE,
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
    cliente_id INTEGER NOT NULL,
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
ALTER TABLE finanzas.asientos ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE finanzas.asientos ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE finanzas.asientos ADD COLUMN IF NOT EXISTS borrado_en TEXT;
ALTER TABLE finanzas.facturas ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false;
ALTER TABLE finanzas.facturas ADD COLUMN IF NOT EXISTS borrado_por TEXT;
ALTER TABLE finanzas.facturas ADD COLUMN IF NOT EXISTS borrado_en TEXT;
-- Docs: el binario sale de la BD a disco. storage_key + sha256 en la tabla, content_b64 en desuso.
ALTER TABLE files ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS sha256 TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS bytes INTEGER;
ALTER TABLE finanzas.gastos_vehiculos ADD COLUMN IF NOT EXISTS storage_key TEXT;
CREATE TABLE IF NOT EXISTS maestros.terceros (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    razon_social TEXT NOT NULL,
    nombre_comercial TEXT,
    nif TEXT UNIQUE,
    email TEXT, telefono TEXT,
    direccion TEXT, poblacion TEXT, cp TEXT,
    es_cliente BOOLEAN NOT NULL DEFAULT false,
    es_proveedor BOOLEAN NOT NULL DEFAULT false,
    es_transportista BOOLEAN NOT NULL DEFAULT false,
    forma_pago TEXT, dias_pago SMALLINT DEFAULT 0,
    cuenta_cliente TEXT DEFAULT '430',
    cuenta_proveedor TEXT DEFAULT '400',
    tarifa_km NUMERIC(10,3) DEFAULT 0,
    activo BOOLEAN NOT NULL DEFAULT true,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    borrado_en TIMESTAMPTZ
);
-- Vistas de compatibilidad: clientes/proveedores/transportistas -> maestros.terceros
CREATE OR REPLACE VIEW clientes AS
SELECT id, razon_social AS nombre, nif AS cif, direccion, poblacion, cp, telefono, email,
       activo, cuenta_cliente AS cuenta_contable_defecto,
       (borrado_en IS NOT NULL) AS borrado, NULL::text AS borrado_por, borrado_en::text AS borrado_en
FROM maestros.terceros WHERE es_cliente;
CREATE OR REPLACE VIEW proveedores AS
SELECT id, razon_social AS nombre, nif AS cif, direccion, poblacion, cp, telefono, email,
       cuenta_proveedor AS cuenta_contable_defecto,
       (borrado_en IS NOT NULL) AS borrado, NULL::text AS borrado_por, borrado_en::text AS borrado_en
FROM maestros.terceros WHERE es_proveedor;
CREATE OR REPLACE VIEW transportistas AS
SELECT id, razon_social AS nombre, nif AS cif, telefono, email, tarifa_km AS tarifa
FROM maestros.terceros WHERE es_transportista;

CREATE OR REPLACE FUNCTION maestros.clientes_ins() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_nif TEXT := NULLIF(upper(regexp_replace(COALESCE(NEW.cif, ''), '[^A-Za-z0-9]', '', 'g')), '');
BEGIN
  IF v_nif IS NOT NULL THEN
    UPDATE maestros.terceros SET es_cliente = true, borrado_en = NULL
     WHERE nif = v_nif RETURNING id INTO NEW.id;
    IF FOUND THEN RETURN NEW; END IF;
  END IF;
  INSERT INTO maestros.terceros (razon_social, nif, direccion, poblacion, cp, telefono, email, cuenta_cliente, es_cliente, activo)
  VALUES (NEW.nombre, v_nif, NEW.direccion, NEW.poblacion, NEW.cp, NEW.telefono, NEW.email, COALESCE(NEW.cuenta_contable_defecto,'430'), true, COALESCE(NEW.activo, true))
  RETURNING id INTO NEW.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.clientes_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE maestros.terceros SET razon_social=NEW.nombre, nif=NEW.cif, direccion=NEW.direccion,
    poblacion=NEW.poblacion, cp=NEW.cp, telefono=NEW.telefono, email=NEW.email,
    cuenta_cliente=COALESCE(NEW.cuenta_contable_defecto,'430'),
    borrado_en = CASE WHEN NEW.borrado THEN COALESCE(NEW.borrado_en::timestamptz, now()) ELSE NULL END,
    actualizado_en = now()
  WHERE id = OLD.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.proveedores_ins() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_nif TEXT := NULLIF(upper(regexp_replace(COALESCE(NEW.cif, ''), '[^A-Za-z0-9]', '', 'g')), '');
BEGIN
  IF v_nif IS NOT NULL THEN
    UPDATE maestros.terceros SET es_proveedor = true, borrado_en = NULL
     WHERE nif = v_nif RETURNING id INTO NEW.id;
    IF FOUND THEN RETURN NEW; END IF;
  END IF;
  INSERT INTO maestros.terceros (razon_social, nif, direccion, poblacion, cp, telefono, email, cuenta_proveedor, es_proveedor)
  VALUES (NEW.nombre, v_nif, NEW.direccion, NEW.poblacion, NEW.cp, NEW.telefono, NEW.email, COALESCE(NEW.cuenta_contable_defecto,'400'), true)
  RETURNING id INTO NEW.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.proveedores_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE maestros.terceros SET razon_social=NEW.nombre, nif=NEW.cif, direccion=NEW.direccion,
    poblacion=NEW.poblacion, cp=NEW.cp, telefono=NEW.telefono, email=NEW.email,
    cuenta_proveedor=COALESCE(NEW.cuenta_contable_defecto,'400'),
    borrado_en = CASE WHEN NEW.borrado THEN COALESCE(NEW.borrado_en::timestamptz, now()) ELSE NULL END,
    actualizado_en = now()
  WHERE id = OLD.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.transportistas_ins() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_nif TEXT := NULLIF(upper(regexp_replace(COALESCE(NEW.cif, ''), '[^A-Za-z0-9]', '', 'g')), '');
BEGIN
  IF v_nif IS NOT NULL THEN
    UPDATE maestros.terceros SET es_transportista = true, borrado_en = NULL
     WHERE nif = v_nif RETURNING id INTO NEW.id;
    IF FOUND THEN RETURN NEW; END IF;
  END IF;
  INSERT INTO maestros.terceros (razon_social, nif, telefono, email, tarifa_km, es_transportista)
  VALUES (NEW.nombre, v_nif, NEW.telefono, NEW.email, COALESCE(NEW.tarifa,0), true)
  RETURNING id INTO NEW.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.transportistas_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE maestros.terceros SET razon_social=NEW.nombre, nif=NEW.cif, telefono=NEW.telefono, email=NEW.email,
    tarifa_km=COALESCE(NEW.tarifa,0), actualizado_en=now()
  WHERE id = OLD.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION maestros.transportistas_del() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM maestros.terceros WHERE id = OLD.id;
  RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS clientes_ins ON clientes;
CREATE TRIGGER clientes_ins INSTEAD OF INSERT ON clientes FOR EACH ROW EXECUTE FUNCTION maestros.clientes_ins();
DROP TRIGGER IF EXISTS clientes_upd ON clientes;
CREATE TRIGGER clientes_upd INSTEAD OF UPDATE ON clientes FOR EACH ROW EXECUTE FUNCTION maestros.clientes_upd();
DROP TRIGGER IF EXISTS proveedores_ins ON proveedores;
CREATE TRIGGER proveedores_ins INSTEAD OF INSERT ON proveedores FOR EACH ROW EXECUTE FUNCTION maestros.proveedores_ins();
DROP TRIGGER IF EXISTS proveedores_upd ON proveedores;
CREATE TRIGGER proveedores_upd INSTEAD OF UPDATE ON proveedores FOR EACH ROW EXECUTE FUNCTION maestros.proveedores_upd();
DROP TRIGGER IF EXISTS transportistas_ins ON transportistas;
CREATE TRIGGER transportistas_ins INSTEAD OF INSERT ON transportistas FOR EACH ROW EXECUTE FUNCTION maestros.transportistas_ins();
DROP TRIGGER IF EXISTS transportistas_upd ON transportistas;
CREATE TRIGGER transportistas_upd INSTEAD OF UPDATE ON transportistas FOR EACH ROW EXECUTE FUNCTION maestros.transportistas_upd();
DROP TRIGGER IF EXISTS transportistas_del ON transportistas;
CREATE TRIGGER transportistas_del INSTEAD OF DELETE ON transportistas FOR EACH ROW EXECUTE FUNCTION maestros.transportistas_del();
CREATE TABLE IF NOT EXISTS rrhh.conductores (
    id SERIAL PRIMARY KEY,
    empleado_id TEXT NOT NULL UNIQUE REFERENCES empleados(id) ON DELETE CASCADE,
    tarjeta_tacografo TEXT UNIQUE,
    tarifa_km NUMERIC(8,4) DEFAULT 0,
    disponible BOOLEAN NOT NULL DEFAULT true,
    motivo_no_disponible TEXT
);
CREATE OR REPLACE VIEW conductores AS
SELECT c.id, e.nombre, e.dni, e.telefono, e.email,
       (COALESCE(e.fecha_baja,'') = '') AS activo,
       c.empleado_id, c.tarjeta_tacografo AS did, c.tarifa_km
FROM rrhh.conductores c JOIN empleados e ON e.id = c.empleado_id;

CREATE OR REPLACE FUNCTION rrhh.conductores_ins() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE eid TEXT;
BEGIN
  IF NEW.empleado_id IS NOT NULL AND NEW.empleado_id <> '' THEN
    eid := NEW.empleado_id;
    UPDATE empleados SET nombre=COALESCE(NULLIF(NEW.nombre,''),nombre), dni=COALESCE(NULLIF(NEW.dni,''),dni),
      telefono=COALESCE(NULLIF(NEW.telefono,''),telefono), email=COALESCE(NULLIF(NEW.email,''),email)
    WHERE id = eid;
  ELSE
    INSERT INTO empleados (id, nombre, apellidos, dni, telefono, email, categoria)
    VALUES ('EMP-'||upper(substr(md5(random()::text),1,10)), COALESCE(NEW.nombre,''), '', NEW.dni, NEW.telefono, NEW.email, 'Conductor')
    RETURNING id INTO eid;
  END IF;
  INSERT INTO rrhh.conductores (empleado_id, tarjeta_tacografo, tarifa_km)
  VALUES (eid, NEW.did, COALESCE(NEW.tarifa_km,0))
  ON CONFLICT (empleado_id) DO UPDATE SET tarjeta_tacografo=EXCLUDED.tarjeta_tacografo, tarifa_km=EXCLUDED.tarifa_km
  RETURNING id INTO NEW.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION rrhh.conductores_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE empleados SET nombre=COALESCE(NULLIF(NEW.nombre,''),nombre), dni=COALESCE(NULLIF(NEW.dni,''),dni),
    telefono=COALESCE(NULLIF(NEW.telefono,''),telefono), email=COALESCE(NULLIF(NEW.email,''),email)
  WHERE id = OLD.empleado_id;
  UPDATE rrhh.conductores SET tarjeta_tacografo=COALESCE(NEW.did,tarjeta_tacografo),
    tarifa_km=COALESCE(NEW.tarifa_km,tarifa_km)
  WHERE id = OLD.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION rrhh.conductores_del() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM rrhh.conductores WHERE id = OLD.id;
  RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS conductores_ins ON conductores;
CREATE TRIGGER conductores_ins INSTEAD OF INSERT ON conductores FOR EACH ROW EXECUTE FUNCTION rrhh.conductores_ins();
DROP TRIGGER IF EXISTS conductores_upd ON conductores;
CREATE TRIGGER conductores_upd INSTEAD OF UPDATE ON conductores FOR EACH ROW EXECUTE FUNCTION rrhh.conductores_upd();
DROP TRIGGER IF EXISTS conductores_del ON conductores;
CREATE TRIGGER conductores_del INSTEAD OF DELETE ON conductores FOR EACH ROW EXECUTE FUNCTION rrhh.conductores_del();
CREATE TABLE IF NOT EXISTS sistema.roles (
    id SERIAL PRIMARY KEY, nombre TEXT UNIQUE NOT NULL, descripcion TEXT
);
CREATE TABLE IF NOT EXISTS sistema.usuarios (
    id SERIAL PRIMARY KEY, usuario TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL,
    rol_id INTEGER NOT NULL REFERENCES sistema.roles(id), nombre TEXT,
    activo BOOLEAN NOT NULL DEFAULT true, debe_cambiar_clave BOOLEAN NOT NULL DEFAULT false,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sistema.config (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sistema.sync_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sistema.audit_log (
    id SERIAL PRIMARY KEY, tabla TEXT NOT NULL, registro_id TEXT, accion TEXT NOT NULL,
    usuario TEXT DEFAULT 'sistema', antes TEXT, despues TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS sistema.integracion_proveedores (
    id SERIAL PRIMARY KEY, codigo TEXT UNIQUE NOT NULL, nombre TEXT NOT NULL,
    categoria TEXT NOT NULL DEFAULT 'telemetria', icono TEXT DEFAULT '',
    activo BOOLEAN DEFAULT true, orden INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sistema.integracion_campos (
    id SERIAL PRIMARY KEY,
    proveedor_id INTEGER NOT NULL REFERENCES sistema.integracion_proveedores(id) ON DELETE CASCADE,
    clave TEXT NOT NULL, etiqueta TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'texto',
    requerido BOOLEAN DEFAULT false, orden INTEGER DEFAULT 0, UNIQUE(proveedor_id, clave)
);
CREATE TABLE IF NOT EXISTS sistema.integracion_valores (
    id SERIAL PRIMARY KEY,
    campo_id INTEGER NOT NULL UNIQUE REFERENCES sistema.integracion_campos(id) ON DELETE CASCADE,
    valor TEXT
);
CREATE TABLE IF NOT EXISTS sistema.actividades (
    id SERIAL PRIMARY KEY,
    proveedor_id INTEGER NOT NULL REFERENCES sistema.integracion_proveedores(id) ON DELETE CASCADE,
    nombre TEXT NOT NULL, referencia TEXT NOT NULL, activo BOOLEAN DEFAULT true,
    UNIQUE(proveedor_id, nombre)
);

-- Vistas de compatibilidad (el código sigue usando los nombres viejos).
-- Fase 9: las vistas alias puras se eliminaron (código ya cualifica). Quedan las
-- abstracciones legítimas: clientes/proveedores/transportistas/conductores/config.usuarios
-- (JOIN/remap con triggers) + vehiculos/trips (remap PK) + rentabilidad_viaje.
CREATE OR REPLACE VIEW config.usuarios AS
SELECT u.id, u.usuario, u.password_hash, r.nombre AS rol, u.nombre,
       u.activo, u.creado_en, u.debe_cambiar_clave
FROM sistema.usuarios u JOIN sistema.roles r ON r.id = u.rol_id;

CREATE OR REPLACE FUNCTION sistema.usuarios_ins() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO sistema.usuarios (usuario, password_hash, rol_id, nombre, activo, debe_cambiar_clave)
  VALUES (NEW.usuario, NEW.password_hash, (SELECT id FROM sistema.roles WHERE nombre=NEW.rol),
          NEW.nombre, COALESCE(NEW.activo, true), COALESCE(NEW.debe_cambiar_clave, false))
  RETURNING id INTO NEW.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION sistema.usuarios_upd() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE sistema.usuarios SET
    nombre=COALESCE(NEW.nombre, nombre),
    activo=COALESCE(NEW.activo, activo),
    rol_id=COALESCE((SELECT id FROM sistema.roles WHERE nombre=NEW.rol), rol_id),
    debe_cambiar_clave=COALESCE(NEW.debe_cambiar_clave, debe_cambiar_clave),
    password_hash=COALESCE(NULLIF(NEW.password_hash,''), password_hash)
  WHERE id=OLD.id;
  RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION sistema.usuarios_del() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM sistema.usuarios WHERE id=OLD.id;
  RETURN OLD;
END $$;
DROP TRIGGER IF EXISTS usuarios_ins ON config.usuarios;
CREATE TRIGGER usuarios_ins INSTEAD OF INSERT ON config.usuarios FOR EACH ROW EXECUTE FUNCTION sistema.usuarios_ins();
DROP TRIGGER IF EXISTS usuarios_upd ON config.usuarios;
CREATE TRIGGER usuarios_upd INSTEAD OF UPDATE ON config.usuarios FOR EACH ROW EXECUTE FUNCTION sistema.usuarios_upd();
DROP TRIGGER IF EXISTS usuarios_del ON config.usuarios;
CREATE TRIGGER usuarios_del INSTEAD OF DELETE ON config.usuarios FOR EACH ROW EXECUTE FUNCTION sistema.usuarios_del();
CREATE TABLE IF NOT EXISTS finanzas.series (
    codigo TEXT PRIMARY KEY, ultimo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finanzas.ejercicios (
    anno SMALLINT PRIMARY KEY, cerrado BOOLEAN NOT NULL DEFAULT false, cerrado_en TIMESTAMPTZ
);
-- Trigger de cuadre: un asiento no puede quedar descuadrado (suma debe = suma haber).
CREATE OR REPLACE FUNCTION public.chk_asiento_cuadra() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d NUMERIC; h NUMERIC; aid INTEGER := COALESCE(NEW.asiento_id, OLD.asiento_id);
BEGIN
    SELECT COALESCE(SUM(debe),0), COALESCE(SUM(haber),0) INTO d, h FROM finanzas.apuntes WHERE asiento_id = aid;
    IF d <> h THEN RAISE EXCEPTION 'Asiento % descuadrado: debe % / haber %', aid, d, h; END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS apuntes_cuadre ON finanzas.apuntes;
CREATE CONSTRAINT TRIGGER apuntes_cuadre AFTER INSERT OR UPDATE OR DELETE ON finanzas.apuntes
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.chk_asiento_cuadra();
CREATE TABLE IF NOT EXISTS finanzas.vencimientos (
    id SERIAL PRIMARY KEY,
    factura_emitida_id INTEGER, factura_recibida_id INTEGER, nomina_id INTEGER,
    fecha TEXT NOT NULL, importe NUMERIC(12,2) NOT NULL, pagado_en TEXT, asiento_id INTEGER
);
CREATE TABLE IF NOT EXISTS finanzas.inmovilizado (
    id SERIAL PRIMARY KEY,
    vehiculo_id TEXT UNIQUE, fecha_adquisicion TEXT NOT NULL, coste NUMERIC(12,2) NOT NULL,
    valor_residual NUMERIC(12,2) DEFAULT 0, vida_util_meses INTEGER NOT NULL,
    cuenta TEXT NOT NULL, factura_recibida_id INTEGER
);
CREATE TABLE IF NOT EXISTS finanzas.amortizaciones (
    id SERIAL PRIMARY KEY,
    inmovilizado_id INTEGER NOT NULL REFERENCES finanzas.inmovilizado(id),
    periodo TEXT NOT NULL, importe NUMERIC(12,2) NOT NULL, asiento_id INTEGER,
    UNIQUE(inmovilizado_id, periodo)
);
CREATE TABLE IF NOT EXISTS finanzas.facturas_recibidas (
    id SERIAL PRIMARY KEY,
    proveedor_id INTEGER, numero_proveedor TEXT, fecha TEXT NOT NULL, vencimiento TEXT,
    base NUMERIC(12,2) DEFAULT 0, cuota_iva NUMERIC(12,2) DEFAULT 0,
    retencion NUMERIC(12,2) DEFAULT 0, total NUMERIC(12,2) DEFAULT 0,
    estado TEXT DEFAULT 'pendiente', origen TEXT DEFAULT 'manual',
    terminal TEXT, categoria TEXT, storage_key TEXT,
    gasto_origen TEXT, asiento_id INTEGER, creado_en TIMESTAMPTZ DEFAULT now(),
    UNIQUE(proveedor_id, numero_proveedor)
);
CREATE TABLE IF NOT EXISTS finanzas.facturas_recibidas_lineas (
    id SERIAL PRIMARY KEY,
    factura_id INTEGER NOT NULL REFERENCES finanzas.facturas_recibidas(id) ON DELETE CASCADE,
    categoria_id INTEGER, cuenta TEXT, vehiculo_id TEXT, viaje_id TEXT,
    concepto TEXT, litros NUMERIC(10,2) DEFAULT 0, base NUMERIC(12,2) DEFAULT 0, iva_pct NUMERIC(5,2) DEFAULT 21
);
INSERT INTO finanzas.series (codigo, ultimo) VALUES ('F', 0), ('A', 0) ON CONFLICT (codigo) DO NOTHING;
"""

_SCHEMA_VIEWS = """
DROP VIEW IF EXISTS cuentas, asientos, apuntes, facturas, factura_lineas, gastos, gastos_vehiculos, costes_fijos, liquidaciones, vehiculos, mantenimientos, trips, paradas, tramos CASCADE;
DROP VIEW IF EXISTS config.roles, config, sync_state, audit_log, integracion_proveedores, integracion_campos, integracion_valores, actividades CASCADE;
DROP VIEW IF EXISTS finanzas.rentabilidad_viaje CASCADE;
CREATE OR REPLACE VIEW finanzas.rentabilidad_viaje AS
SELECT t.id AS viaje_id, t.codigo, t.referencia, t.estado,
       COALESCE(t.precio, 0) AS ingresos,
       COALESCE(c.coste, 0) AS costes,
       COALESCE(t.precio, 0) - COALESCE(c.coste, 0) AS margen
FROM operaciones.trips t
LEFT JOIN (SELECT frl.viaje_id, SUM(frl.base) AS coste
           FROM finanzas.facturas_recibidas_lineas frl
           JOIN finanzas.facturas_recibidas fr ON fr.id = frl.factura_id
           WHERE fr.estado <> 'anulada'
           GROUP BY frl.viaje_id) c ON c.viaje_id = t.codigo;
-- Sincronización garantizada por la BD: los gastos (tabla plana, fuente de verdad)
-- alimentan facturas_recibidas (capa analítica) vía triggers AFTER.
CREATE OR REPLACE FUNCTION finanzas.gastos_sync_fr() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_base NUMERIC; v_cuota NUMERIC; v_ret NUMERIC; v_id BIGINT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM finanzas.facturas_recibidas WHERE gasto_origen = 'gastos:' || OLD.id;
    RETURN OLD;
  END IF;
  v_base := CASE WHEN COALESCE(NEW.iva,0) > 0 THEN round(COALESCE(NEW.importe,0) / (1 + NEW.iva/100.0), 2) ELSE COALESCE(NEW.importe,0) END;
  v_cuota := round(COALESCE(NEW.importe,0) - v_base, 2);
  v_ret := CASE WHEN COALESCE(NEW.retencion,0) > 0 THEN round(v_base * NEW.retencion/100.0, 2) ELSE 0 END;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO finanzas.facturas_recibidas (proveedor_id, fecha, base, cuota_iva, retencion, total, estado, origen, terminal, categoria, gasto_origen)
    VALUES (NEW.proveedor_id, NEW.fecha, v_base, v_cuota, v_ret, COALESCE(NEW.importe,0),
            CASE WHEN NEW.pagado THEN 'pagada' ELSE 'pendiente' END, 'viaje', NEW.terminal, NEW.categoria,
            'gastos:' || NEW.id)
    RETURNING id INTO v_id;
    INSERT INTO finanzas.facturas_recibidas_lineas (factura_id, categoria_id, cuenta, viaje_id, concepto, base, iva_pct)
    VALUES (v_id, NEW.categoria_id, NEW.cuenta, NEW.trip_id, NEW.concepto, v_base, NEW.iva);
  ELSE
    UPDATE finanzas.facturas_recibidas SET proveedor_id=NEW.proveedor_id, fecha=NEW.fecha,
      base=v_base, cuota_iva=v_cuota, retencion=v_ret, total=COALESCE(NEW.importe,0),
      estado=CASE WHEN NEW.pagado THEN 'pagada' ELSE 'pendiente' END, terminal=NEW.terminal, categoria=NEW.categoria
    WHERE gasto_origen = 'gastos:' || NEW.id;
    UPDATE finanzas.facturas_recibidas_lineas SET viaje_id=NEW.trip_id, categoria_id=NEW.categoria_id,
      cuenta=NEW.cuenta, concepto=NEW.concepto, base=v_base, iva_pct=NEW.iva
    WHERE factura_id = (SELECT id FROM finanzas.facturas_recibidas WHERE gasto_origen = 'gastos:' || NEW.id);
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS gastos_sync_fr ON finanzas.gastos;
CREATE TRIGGER gastos_sync_fr AFTER INSERT OR UPDATE OR DELETE ON finanzas.gastos FOR EACH ROW EXECUTE FUNCTION finanzas.gastos_sync_fr();
CREATE OR REPLACE FUNCTION finanzas.gastos_veh_sync_fr() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_base NUMERIC; v_cuota NUMERIC; v_id BIGINT; v_estado TEXT;
BEGIN
  IF TG_OP = 'DELETE' THEN
    DELETE FROM finanzas.facturas_recibidas WHERE gasto_origen = 'gastos_vehiculos:' || OLD.id;
    RETURN OLD;
  END IF;
  v_base := COALESCE(NEW.base_imponible,0);
  IF v_base <= 0 AND COALESCE(NEW.importe_total,0) > 0 THEN
    v_base := CASE WHEN COALESCE(NEW.iva,0) > 0 THEN round(NEW.importe_total / (1 + NEW.iva/100.0), 2) ELSE NEW.importe_total END;
  END IF;
  v_cuota := round(COALESCE(NEW.importe_total,0) - v_base, 2);
  -- Normaliza el estado de pago: los gastos de vehículo usan 'Pagado'/'Pendiente' en
  -- pantalla, pero la capa analítica (facturas_recibidas) usa 'pagada'/'pendiente'.
  v_estado := CASE WHEN lower(COALESCE(NEW.estado_pago,'')) IN ('pagado','pagada') THEN 'pagada' ELSE 'pendiente' END;
  IF TG_OP = 'INSERT' THEN
    INSERT INTO finanzas.facturas_recibidas (proveedor_id, numero_proveedor, fecha, base, cuota_iva, total, estado, origen, categoria, storage_key, gasto_origen)
    VALUES (NEW.proveedor_id, NEW.factura_ref, NEW.fecha, v_base, v_cuota, COALESCE(NEW.importe_total,0),
            v_estado, 'vehiculo', NEW.tipo, NEW.storage_key, 'gastos_vehiculos:' || NEW.id)
    RETURNING id INTO v_id;
    INSERT INTO finanzas.facturas_recibidas_lineas (factura_id, cuenta, vehiculo_id, concepto, litros, base, iva_pct)
    VALUES (v_id, NEW.cuenta_contable_gasto, NEW.vehiculo_id, COALESCE(NEW.factura_ref, NEW.tipo), COALESCE(NEW.litros,0), v_base, NEW.iva);
  ELSE
    UPDATE finanzas.facturas_recibidas SET proveedor_id=NEW.proveedor_id, numero_proveedor=NEW.factura_ref,
      fecha=NEW.fecha, base=v_base, cuota_iva=v_cuota, total=COALESCE(NEW.importe_total,0), estado=v_estado,
      categoria=NEW.tipo, storage_key=NEW.storage_key
    WHERE gasto_origen = 'gastos_vehiculos:' || NEW.id;
    UPDATE finanzas.facturas_recibidas_lineas SET vehiculo_id=NEW.vehiculo_id,
      cuenta=NEW.cuenta_contable_gasto, litros=COALESCE(NEW.litros,0), base=v_base, iva_pct=NEW.iva
    WHERE factura_id = (SELECT id FROM finanzas.facturas_recibidas WHERE gasto_origen = 'gastos_vehiculos:' || NEW.id);
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS gastos_veh_sync_fr ON finanzas.gastos_vehiculos;
CREATE TRIGGER gastos_veh_sync_fr AFTER INSERT OR UPDATE OR DELETE ON finanzas.gastos_vehiculos FOR EACH ROW EXECUTE FUNCTION finanzas.gastos_veh_sync_fr();
CREATE OR REPLACE VIEW vehiculos AS
SELECT v.codigo AS id, v.id AS _pk, v.codigo, v.terminal_trimble,
       v.categoria, v.matricula, v.marca, v.modelo, v.anno, v.itv, v.seguro,
       v.peaje_categoria, v.ptv_profile, v.ejes, v.mma, v.clase_euro, v.activo,
       v.last_lat, v.last_lng, v.capacidad_peso, v.capacidad_palets,
       v.fecha_caducidad_itv, v.seguro_compania, v.fecha_caducidad_seguro,
       v.tipo_tenencia, v.proveedor_id, v.fecha_alta, v.cuota_mensual, v.km_actuales,
       v.fecha_proxima_revision, v.app_terminal, v.last_position_time, v.device,
       v.coste_adquisicion, v.fecha_adquisicion, v.vida_util, v.valor_residual
FROM flota.vehiculos v;
CREATE OR REPLACE VIEW trips AS
SELECT t.codigo AS id, t.id AS _pk, t.codigo,
       t.nombre, t.matricula, t.conductor, t.tipo_carga, t.origen, t.destino, t.tareas,
       t.estado, t.error, t.creado, t.terminal, t.ecmr_id, t.semirremolque_id, t.remolque_id,
       t.tareas_estado, t.cliente, t.precio, t.km_total, t.km_vacio, t.tiempo_min,
       t.km_inicio, t.km_fin, t.km_real, t.km_fuente, t.pausas_min, t.trafico_min,
       t.peaje_km, t.peaje_estimado, t.peaje_fuente, t.gastos, t.factura, t.estado_pago,
       t.iva, t.cliente_id, t.conductor_id, t.payload, t.fecha_actualizacion,
       t.fecha_esperada_carga, t.fecha_esperada_descarga, t.referencia, t.origen_id,
       t.destino_id, t.modo_tarifa, t.tarifa_id, t.precio_unitario, t.kilos,
       t.subcontratado, t.proveedor_id, t.coste,
       t.anulado_at, t.anulado_por, t.anulado_motivo
FROM operaciones.trips t;
"""


_pools = {}
_pools_lock = threading.Lock()


def _pool_for(dbname):
    with _pools_lock:
        p = _pools.get(dbname)
        if p is None:
            p = psycopg2.pool.ThreadedConnectionPool(
                2, 40,
                host=config.DB_HOST, port=config.DB_PORT, dbname=dbname,
                user=config.DB_USER, password=config.DB_PASSWORD,
                options="-c statement_timeout=30000",
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
            # TimescaleDB: extensión (una BD nueva no la trae) + hypertable de posiciones GPS.
            # SAVEPOINT: si la extensión no está disponible, NO aborta la transacción entera.
            cur.execute("SAVEPOINT sp_timescale")
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
                cur.execute("SELECT create_hypertable('telemetria.posiciones_gps', 'time', if_not_exists => TRUE, migrate_data => TRUE)")
                cur.execute("RELEASE SAVEPOINT sp_timescale")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT sp_timescale")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_posiciones_vehiculo_time ON telemetria.posiciones_gps (vehiculo_id, time)")
            cur.execute("INSERT INTO empresa (id, nombre, pais, iva) VALUES (1, '', 'ES', 21) ON CONFLICT (id) DO NOTHING")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS itv TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS seguro TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS peaje_categoria TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS ptv_profile TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS ejes INTEGER")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS mma INTEGER")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS clase_euro TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS categoria TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS last_lat NUMERIC(10,7)")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS last_lng NUMERIC(10,7)")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS last_position_time TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS device TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS app_terminal TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS capacidad_peso NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS capacidad_palets INTEGER DEFAULT 0")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS coste_adquisicion NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS fecha_adquisicion TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS vida_util INTEGER DEFAULT 5")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS valor_residual NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS fecha_caducidad_itv TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS seguro_compania TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS fecha_caducidad_seguro TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS tipo_tenencia TEXT DEFAULT 'Propiedad'")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS proveedor_id INTEGER")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS fecha_alta TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS cuota_mensual NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS vehiculo_id TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS estado_descarga TEXT DEFAULT 'descargado'")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS intentos INT DEFAULT 0")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS ultimo_error TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS task_id TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS mensaje_id TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS report_id TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS question_id TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS tipo_documento TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS mime TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS paginas INT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS lat DOUBLE PRECISION")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS lng DOUBLE PRECISION")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS vinculado_por TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS revisado_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS revisado_por TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS anulado_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS anulado_por TEXT")
            cur.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS anulado_motivo TEXT")
            cur.execute("ALTER TABLE finanzas.asientos ADD COLUMN IF NOT EXISTS origen_id TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS terminal TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS clase TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS direccion TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS vehiculo_codigo TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS did TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS conductor_id INTEGER")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS report_id TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS report_version TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS respuestas JSONB")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS aty TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS lid TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS task_id TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS estado TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS estado_time TIMESTAMPTZ")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS leido_operador_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS leido_operador_por TEXT")
            cur.execute("ALTER TABLE mensajes ADD COLUMN IF NOT EXISTS incidencia BOOLEAN NOT NULL DEFAULT false")
            cur.execute("ALTER TABLE finanzas.gastos_vehiculos ADD COLUMN IF NOT EXISTS base_imponible NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE finanzas.gastos_vehiculos ADD COLUMN IF NOT EXISTS iva NUMERIC(6,2) DEFAULT 21")
            cur.execute("ALTER TABLE finanzas.gastos_vehiculos ADD COLUMN IF NOT EXISTS cuenta_contable_gasto TEXT")
            cur.execute("ALTER TABLE finanzas.gastos_vehiculos ADD COLUMN IF NOT EXISTS estado_pago TEXT DEFAULT 'Pendiente'")
            cur.execute("ALTER TABLE flota.mantenimientos ADD COLUMN IF NOT EXISTS fecha_fin TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_carnet TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_cap TEXT")
            cur.execute("ALTER TABLE empleados ADD COLUMN IF NOT EXISTS caducidad_medica TEXT")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS km_actuales NUMERIC(12,1) DEFAULT 0")
            cur.execute("ALTER TABLE flota.vehiculos ADD COLUMN IF NOT EXISTS fecha_proxima_revision TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS fecha_actualizacion TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS fecha_esperada_carga TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS anulado_at TIMESTAMPTZ")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS anulado_por TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS anulado_motivo TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS fecha_esperada_descarga TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS km_vacio NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS semirremolque_id TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS remolque_id TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS payload TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS referencia TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS ecmr_id TEXT")
            cur.execute("ALTER TABLE finanzas.facturas ADD COLUMN IF NOT EXISTS coste NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE finanzas.facturas ADD COLUMN IF NOT EXISTS margen NUMERIC(12,2) DEFAULT 0")
            cur.execute("ALTER TABLE finanzas.liquidaciones ADD COLUMN IF NOT EXISTS conductor_id INTEGER")
            cur.execute("ALTER TABLE finanzas.liquidaciones ADD COLUMN IF NOT EXISTS viaje_id TEXT")
            cur.execute("ALTER TABLE finanzas.gastos ADD COLUMN IF NOT EXISTS trip_id TEXT")
            cur.execute("ALTER TABLE finanzas.gastos ADD COLUMN IF NOT EXISTS iva NUMERIC(5,2) DEFAULT 21")
            cur.execute("ALTER TABLE finanzas.gastos ADD COLUMN IF NOT EXISTS retencion NUMERIC(5,2) DEFAULT 0")
            cur.execute("ALTER TABLE finanzas.gastos ADD COLUMN IF NOT EXISTS pagado BOOLEAN DEFAULT false")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS peaje_km NUMERIC(10,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS peaje_estimado NUMERIC(10,2) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS peaje_fuente TEXT")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS tiempo_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS pausas_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS trafico_min NUMERIC(8,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS origen_id INTEGER")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS destino_id INTEGER")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS km_inicio NUMERIC(12,1)")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS km_fin NUMERIC(12,1)")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS km_real NUMERIC(10,1)")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS km_fuente TEXT DEFAULT 'planificado'")
            # Tarifas + valoración del viaje (km/viaje/kilos) y subcontratación.
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS modo_tarifa TEXT DEFAULT 'viaje'")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS tarifa_id INTEGER")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS precio_unitario NUMERIC(12,3)")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS kilos NUMERIC(12,1) DEFAULT 0")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS subcontratado BOOLEAN DEFAULT false")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS proveedor_id INTEGER")
            cur.execute("ALTER TABLE operaciones.trips ADD COLUMN IF NOT EXISTS coste NUMERIC(12,2) DEFAULT 0")
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
            # (audit_log vive en sistema.* desde Fase 4; el CREATE público legacy se eliminó.)
            for _t in ("finanzas.asientos", "finanzas.facturas"):
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado BOOLEAN DEFAULT false")
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado_por TEXT")
                cur.execute(f"ALTER TABLE {_t} ADD COLUMN IF NOT EXISTS borrado_en TEXT")
            # Backfill: asignar referencia interna a viajes existentes (por orden de creación)
            cur.execute("SELECT codigo FROM operaciones.trips WHERE referencia IS NULL OR referencia = '' ORDER BY creado ASC")
            _pend = [r[0] for r in cur.fetchall()]
            if _pend:
                _n = 0
                cur.execute("SELECT referencia FROM operaciones.trips WHERE referencia IS NOT NULL AND referencia != ''")
                for _r in cur.fetchall():
                    _m = re.match(r"^V-(\d+)$", (_r[0] or "").strip())
                    if _m:
                        _n = max(_n, int(_m.group(1)))
                for _tid in _pend:
                    _n += 1
                    cur.execute("UPDATE operaciones.trips SET referencia=%s WHERE codigo=%s", (f"V-{_n:04d}", _tid))
            for c in _CATEGORIAS:
                cur.execute(
                    "INSERT INTO categorias_gasto (nombre) VALUES (%s) "
                    "ON CONFLICT (nombre) DO NOTHING",
                    (c,),
                )
            cur.execute("ALTER TABLE categorias_gasto ADD COLUMN IF NOT EXISTS cuenta TEXT")
            cur.execute("ALTER TABLE finanzas.gastos ADD COLUMN IF NOT EXISTS cuenta TEXT")
            cur.execute("ALTER TABLE finanzas.facturas_recibidas ADD COLUMN IF NOT EXISTS gasto_origen TEXT")
            cur.execute("ALTER TABLE finanzas.facturas_recibidas ADD COLUMN IF NOT EXISTS terminal TEXT")
            cur.execute("ALTER TABLE finanzas.facturas_recibidas ADD COLUMN IF NOT EXISTS categoria TEXT")
            cur.execute("ALTER TABLE finanzas.facturas_recibidas ADD COLUMN IF NOT EXISTS storage_key TEXT")
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
                    "INSERT INTO finanzas.cuentas (codigo, nombre, grupo, tipo, orden) VALUES (%s,%s,%s,%s,%s) "
                    "ON CONFLICT (codigo) DO NOTHING",
                    (cod, nom, grupo, tipo, orden),
                )
            # Normalizar NIFs existentes (una sola vez): mayúsculas, sin separadores, '' -> NULL.
            cur.execute("UPDATE maestros.terceros SET nif = NULLIF(upper(regexp_replace(nif, '[^A-Za-z0-9]', '', 'g')), '')")
            # Vistas de compatibilidad: se recrean DESPUÉS de los ALTER, para que
            # vean las columnas añadidas por migración (device, origen_id, coste, kilos…).
            cur.execute(_SCHEMA_VIEWS)
            conn.commit()
            cur.close()
            _schema_done.add(dbname)
        except Exception as e:
            print(f"[schema] {dbname}: fallo de inicialización — {e}", flush=True)
            conn.rollback()
    return _Conn(conn, pool)

def _get_config(key, default=""):
    conn = _db()
    row = conn.execute("SELECT value FROM sistema.config WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def _valores_proveedor(conn, codigo):
    """{clave: valor} de los valores de configuración de un proveedor de integración (descifrados)."""
    rows = conn.execute(
        "SELECT c.clave, v.valor FROM sistema.integracion_proveedores p "
        "JOIN sistema.integracion_campos c ON c.proveedor_id = p.id "
        "LEFT JOIN sistema.integracion_valores v ON v.campo_id = c.id "
        "WHERE p.codigo=?",
        (codigo,),
    ).fetchall()
    return {r["clave"]: _decrypt_valor(r["valor"] or "") for r in rows}


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


__all__ = ["_tenant_ctx", "_usuario_ctx", "_schema_done", "_Conn", "_SCHEMA", "_SCHEMA_VIEWS", "_db", "_get_config", "_valores_proveedor", "get_conn"]