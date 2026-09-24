-- Fase 6/7: relocar flota + operaciones a sus esquemas.
CREATE SCHEMA IF NOT EXISTS flota;
CREATE SCHEMA IF NOT EXISTS operaciones;
ALTER TABLE vehiculos SET SCHEMA flota;
ALTER TABLE mantenimientos SET SCHEMA flota;
ALTER TABLE trips SET SCHEMA operaciones;
ALTER TABLE paradas SET SCHEMA operaciones;
ALTER TABLE tramos SET SCHEMA operaciones;
