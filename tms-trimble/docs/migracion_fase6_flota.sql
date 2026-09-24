-- =============================================================================
--  FASE 6 — FLOTA: relocar vehiculos + mantenimientos al esquema `flota`
--  (y, opcionalmente, remapear la PK de vehiculos a BIGINT surrogate).
--  RIESGO ALTO: vehiculos es la flota VIVA (telemetría + integración Trimble).
--  Ejecutar con el worker de ingesta PARADO y probar findAllUnits real después.
-- =============================================================================

-- --- PASO 1 (relocación, segura — igual que Fases 4/5) ---
CREATE SCHEMA IF NOT EXISTS flota;
ALTER TABLE vehiculos SET SCHEMA flota;
ALTER TABLE mantenimientos SET SCHEMA flota;

-- El _SCHEMA del backend debe crear las vistas de compatibilidad (misma técnica):
--   CREATE VIEW vehiculos AS SELECT * FROM flota.vehiculos;
--   CREATE VIEW mantenimientos AS SELECT * FROM flota.mantenimientos;
-- Y redirigir ON CONFLICT (routers/flota.py:89) y ALTER TABLE (db.py) a flota.*.

-- --- PASO 2 (remap PK, ROMPEDOR — requiere parar el worker + migrar FKs) ---
-- vehiculos.id (TEXT = referencia Trimble = name) pasa a:
--   · id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY  (surrogada)
--   · matricula     TEXT UNIQUE   (placa, = antiguo id para tractoras)
--   · terminal_trimble TEXT UNIQUE (referencia SOAP: T4U o APP)
--   · codigo        TEXT UNIQUE   (código de negocio, = antiguo id)
--
-- Impacto (todas las referencias TEXT a vehiculos.id):
--   posiciones_gps.vehiculo_id (hypertable SIN FK) — el worker escribe el NAME;
--     resolver name→id BIGINT en el worker (enrich) antes de INSERT.
--   trips.terminal, gastos.vehiculo_id, mantenimientos.vehiculo_id,
--   files.vehiculo_id, flota.reglas_mantenimiento.vehiculo_id,
--   flota.alertas_mantenimiento.vehiculo_id, finanzas.facturas_recibidas_lineas.vehiculo_id.
--   → reasignar FK a vehiculos.id BIGINT, o mantener la columna TEXT como
--     `terminal_trimble` y hacer JOIN por matrícula (más seguro, sin FK rígida).

-- Secuencia de ejecución recomendada (sesión dedicada):
--   1) docker compose stop tms-worker   (parar ingesta)
--   2) backup pg_dump
--   3) aplicar PASO 1 (relocación) + verificar vistas/CRUD
--   4) aplicar PASO 2 (remap PK) + migrar referencias
--   5) prueba real: findAllUnits + queryTerminal + telemetría
--   6) docker compose start tms-worker
