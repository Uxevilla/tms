-- Fase 5 parte 2: relocar las tablas financieras al esquema `finanzas`.
-- Las vistas de compatibilidad + tablas nuevas las crea el _SCHEMA al reiniciar.
CREATE SCHEMA IF NOT EXISTS finanzas;
ALTER TABLE cuentas SET SCHEMA finanzas;
ALTER TABLE asientos SET SCHEMA finanzas;
ALTER TABLE apuntes SET SCHEMA finanzas;
ALTER TABLE facturas SET SCHEMA finanzas;
ALTER TABLE factura_lineas SET SCHEMA finanzas;
ALTER TABLE gastos SET SCHEMA finanzas;
ALTER TABLE gastos_vehiculos SET SCHEMA finanzas;
ALTER TABLE costes_fijos SET SCHEMA finanzas;
ALTER TABLE liquidaciones SET SCHEMA finanzas;
