-- =====================================================================
-- Ingesta telemática — esquema base (TMS)
-- Se ejecuta una sola vez al inicializar el contenedor de Postgres.
-- =====================================================================

-- La imagen timescaledb ya precarga la extensión en shared_preload_libraries;
-- aquí se habilita en la base de datos actual.
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS telemetria;

-- ---------------------------------------------------------------------
-- Núcleo relacional: viajes (normalizado, sin series temporales)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.viajes (
    viaje_id     TEXT        PRIMARY KEY,
    vehiculo_id  TEXT        NOT NULL,
    conductor_id BIGINT,
    origen       TEXT,
    destino      TEXT,
    estado       TEXT        NOT NULL DEFAULT 'planificado',
    fecha_inicio TIMESTAMPTZ,
    fecha_fin    TIMESTAMPTZ,
    creado_en    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_viajes_vehiculo_estado
    ON ops.viajes (vehiculo_id, estado);
CREATE INDEX IF NOT EXISTS idx_viajes_fecha_inicio
    ON ops.viajes (fecha_inicio);

-- ---------------------------------------------------------------------
-- Series temporales: posiciones GPS (tabla normal -> hypertable)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetria.posiciones_gps (
    time        TIMESTAMPTZ      NOT NULL,
    vehiculo_id TEXT             NOT NULL,
    viaje_id    TEXT,             -- NULL = posición sin viaje activo en caché
    lat         DOUBLE PRECISION NOT NULL,
    lng         DOUBLE PRECISION NOT NULL,
    speed_kmh   DOUBLE PRECISION,
    heading     DOUBLE PRECISION,
    odometer_km DOUBLE PRECISION,
    ignicion    BOOLEAN,
    fuente      TEXT
);

-- Convierte la tabla en hypertable particionada en chunks de 1 día.
SELECT create_hypertable(
    'telemetria.posiciones_gps',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists        => TRUE,
    migrate_data         => TRUE
);

-- Índices para las consultas típicas (por vehículo y por viaje).
CREATE INDEX IF NOT EXISTS idx_posiciones_vehiculo_time
    ON telemetria.posiciones_gps (vehiculo_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_posiciones_viaje_time
    ON telemetria.posiciones_gps (viaje_id, time DESC);

-- Deduplicación para entrega at-least-once.
-- En hypertables el índice UNIQUE debe incluir la columna de partición (time).
CREATE UNIQUE INDEX IF NOT EXISTS uq_posiciones_vehiculo_time
    ON telemetria.posiciones_gps (vehiculo_id, time);

-- ---- Opcional (producción) -----------------------------------------
-- Compresión automática de chunks antiguos:
--   SELECT add_compression_policy('telemetria.posiciones_gps', INTERVAL '7 days');
-- Retención (borrado de chunks muy antiguos):
--   SELECT add_retention_policy('telemetria.posiciones_gps', INTERVAL '365 days');
