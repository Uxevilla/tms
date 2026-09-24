-- Fase 4 (sistema): roles/usuarios/config/sync_state/audit_log/integraciones/actividades
-- pasan al esquema `sistema`; usuarios.rol (TEXT) se transforma en rol_id (FK a roles.id).
-- Las vistas de compatibilidad + triggers los crea el _SCHEMA del backend al reiniciar.
CREATE SCHEMA IF NOT EXISTS sistema;

-- 1. roles -> sistema.roles
ALTER TABLE config.roles SET SCHEMA sistema;

-- 2. usuarios -> sistema.usuarios + rol -> rol_id
ALTER TABLE config.usuarios SET SCHEMA sistema;
ALTER TABLE sistema.usuarios ADD COLUMN IF NOT EXISTS rol_id INTEGER;
UPDATE sistema.usuarios u SET rol_id = r.id FROM sistema.roles r WHERE r.nombre = u.rol;
ALTER TABLE sistema.usuarios DROP CONSTRAINT IF EXISTS usuarios_rol_fkey;
ALTER TABLE sistema.usuarios DROP COLUMN IF EXISTS rol;
ALTER TABLE sistema.usuarios ALTER COLUMN rol_id SET NOT NULL;
ALTER TABLE sistema.usuarios ADD CONSTRAINT usuarios_rol_fk FOREIGN KEY (rol_id) REFERENCES sistema.roles(id);

-- 3. relocaciones simples (public -> sistema)
ALTER TABLE public.config SET SCHEMA sistema;
ALTER TABLE sync_state SET SCHEMA sistema;
ALTER TABLE audit_log SET SCHEMA sistema;
ALTER TABLE integracion_proveedores SET SCHEMA sistema;
ALTER TABLE integracion_campos SET SCHEMA sistema;
ALTER TABLE integracion_valores SET SCHEMA sistema;
ALTER TABLE actividades SET SCHEMA sistema;
