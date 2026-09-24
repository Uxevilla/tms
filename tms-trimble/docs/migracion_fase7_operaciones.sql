-- =============================================================================
--  FASE 7 — OPERACIONES: relocar trips + paradas + tramos a `operaciones`
--  (y, opcionalmente, remapear la PK de trips a BIGINT surrogate).
--  RIESGO ALTO: trips es el núcleo operativo (sync Trimble + telemetría + facturación).
-- =============================================================================

-- --- PASO 1 (relocación, segura) ---
CREATE SCHEMA IF NOT EXISTS operaciones;
ALTER TABLE trips SET SCHEMA operaciones;
ALTER TABLE paradas SET SCHEMA operaciones;
ALTER TABLE tramos SET SCHEMA operaciones;

-- Vistas de compatibilidad (misma técnica):
--   CREATE VIEW trips AS SELECT * FROM operaciones.trips;
--   CREATE VIEW paradas AS SELECT * FROM operaciones.paradas;
--   CREATE VIEW tramos AS SELECT * FROM operaciones.tramos;
-- Redirigir ON CONFLICT (services/viajes.py:63) y ALTER TABLE (db.py) a operaciones.*.

-- --- PASO 2 (remap PK, ROMPEDOR) ---
-- trips.id (TEXT = tripId Trimble) pasa a:
--   · id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY  (surrogada)
--   · codigo TEXT UNIQUE  (código de negocio, = antiguo id / referencia del viaje)
--
-- Impacto:
--   posiciones_gps.viaje_id (TEXT, nullable) — el worker escribe el tripId;
--     resolver tripId→id BIGINT en el worker, o mantener TEXT y JOIN por codigo.
--   paradas.trip_id, tramos.trip_id, gastos.trip_id, facturas.trip_id,
--   factura_lineas.trip_id, asientos.trip_id, lineas_nomina, saldos_pales…
--   → reasignar FK a trips.id BIGINT, o mantener trip_id TEXT = trips.codigo.

-- Nota: `tramos` ya estaba planificado para DESAPARECER (se recalcula entre
-- paradas consecutivas); confirmar antes de migrar si se conserva o se elimina.
