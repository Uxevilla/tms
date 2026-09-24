-- Conciliación gastos <-> facturas_recibidas (capa analítica).
-- Debe devolver 0 filas. Se ejecuta como tarea diaria (script conciliacion_gastos.sh).
-- NOTA: lanzada desde Python/psycopg2 con parámetros, el LIKE 'gastos:%' necesita %% (gastos:%%). Desde psql, tal cual.

SELECT 'gastos' AS origen, g.id::text AS id, g.importe AS en_gasto, fr.total AS en_factura, 'falta o importe distinto' AS problema
FROM finanzas.gastos g LEFT JOIN finanzas.facturas_recibidas fr ON fr.gasto_origen = 'gastos:' || g.id
WHERE fr.id IS NULL OR fr.total <> g.importe
UNION ALL
SELECT 'gastos', g.id::text, NULL, NULL, 'estado de pago distinto'
FROM finanzas.gastos g JOIN finanzas.facturas_recibidas fr ON fr.gasto_origen = 'gastos:' || g.id
WHERE COALESCE(g.pagado, false) <> (fr.estado = 'pagada')
UNION ALL
SELECT 'gastos_vehiculos', g.id::text, g.importe_total, fr.total, 'falta o importe distinto'
FROM finanzas.gastos_vehiculos g LEFT JOIN finanzas.facturas_recibidas fr ON fr.gasto_origen = 'gastos_vehiculos:' || g.id
WHERE fr.id IS NULL OR fr.total <> g.importe_total
UNION ALL
SELECT 'lineas', fr.id::text, fr.base, sum(l.base), 'base de cabecera != suma de lineas'
FROM finanzas.facturas_recibidas fr JOIN finanzas.facturas_recibidas_lineas l ON l.factura_id = fr.id
GROUP BY fr.id, fr.base HAVING fr.base <> sum(l.base)
UNION ALL
SELECT 'huerfana', fr.id::text, NULL, fr.total, 'factura de un gasto que ya no existe'
FROM finanzas.facturas_recibidas fr
WHERE fr.gasto_origen LIKE 'gastos:%'
  AND NOT EXISTS (SELECT 1 FROM finanzas.gastos g WHERE fr.gasto_origen = 'gastos:' || g.id);
