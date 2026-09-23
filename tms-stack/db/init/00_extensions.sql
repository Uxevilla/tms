-- Habilitar la extensión TimescaleDB en la BD `tms` (necesaria para la hypertable
-- de telemetría). El esquema, la hypertable y la BD maestra los crea el backend
-- (main.py) al arrancar (_ensure_master + create_hypertable).
CREATE EXTENSION IF NOT EXISTS timescaledb;
