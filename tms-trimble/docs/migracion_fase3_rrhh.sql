-- Fase 3 (rrhh): conductores pasa a ser 1:1 sobre empleados (rrhh.conductores).
-- Migra datos (id + empleado_id + did -> tarjeta_tacografo) y suelta la tabla vieja;
-- el _SCHEMA del backend crea la vista de compatibilidad + triggers al reiniciar.
ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_conductor_id_fkey;
CREATE SCHEMA IF NOT EXISTS rrhh;
CREATE TABLE IF NOT EXISTS rrhh.conductores (
    id SERIAL PRIMARY KEY,
    empleado_id TEXT NOT NULL UNIQUE REFERENCES empleados(id) ON DELETE CASCADE,
    tarjeta_tacografo TEXT UNIQUE,
    tarifa_km NUMERIC(8,4) DEFAULT 0,
    disponible BOOLEAN NOT NULL DEFAULT true,
    motivo_no_disponible TEXT
);
INSERT INTO rrhh.conductores (id, empleado_id, tarjeta_tacografo)
SELECT id, empleado_id, did FROM conductores
WHERE COALESCE(empleado_id,'') <> ''
ON CONFLICT (id) DO NOTHING;
SELECT setval('rrhh_conductores_id_seq', (SELECT MAX(id) FROM rrhh.conductores));
DROP TABLE IF EXISTS conductores CASCADE;
