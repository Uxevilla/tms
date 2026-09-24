-- Fase 2 (maestros): sustituye clientes/proveedores/transportistas por maestros.terceros.
-- El nuevo esquema (db.py _SCHEMA) crea maestros.terceros + vistas de compatibilidad + triggers.
-- Esto solo elimina las FK y las tablas viejas (0 datos en producción), que bloquean CREATE VIEW.
ALTER TABLE gastos DROP CONSTRAINT IF EXISTS gastos_proveedor_id_fkey;
ALTER TABLE gastos_vehiculos DROP CONSTRAINT IF EXISTS gastos_vehiculos_proveedor_id_fkey;
ALTER TABLE liquidaciones DROP CONSTRAINT IF EXISTS liquidaciones_transportista_id_fkey;
ALTER TABLE saldos_pales DROP CONSTRAINT IF EXISTS saldos_pales_cliente_id_fkey;
ALTER TABLE trips DROP CONSTRAINT IF EXISTS trips_cliente_id_fkey;
DROP TABLE IF EXISTS clientes CASCADE;
DROP TABLE IF EXISTS proveedores CASCADE;
DROP TABLE IF EXISTS transportistas CASCADE;
