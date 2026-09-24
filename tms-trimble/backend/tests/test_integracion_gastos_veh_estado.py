"""Integración del trigger gastos_veh_sync_fr contra Postgres real (Fase 0, fix 3).

El trigger que alimenta finanzas.facturas_recibidas desde finanzas.gastos_vehiculos
debe normalizar estado_pago ('Pagado'/'Pendiente') a 'pagada'/'pendiente', y sincronizar
altas, ediciones y borrados.
"""
import psycopg2
import pytest

import config


@pytest.mark.integration
def test_trigger_normaliza_estado_y_sincroniza(scratch_db):
    conn = psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        user=config.DB_USER, password=config.DB_PASSWORD, dbname=scratch_db,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        # Alta con estado 'Pagado' → la factura debe quedar 'pagada'.
        cur.execute(
            "INSERT INTO finanzas.gastos_vehiculos "
            "(vehiculo_id, proveedor_id, fecha, tipo, importe_total, base_imponible, iva, estado_pago, factura_ref) "
            "VALUES ('VEH-1', 1, '2026-09-24', 'combustible', 121, 100, 21, 'Pagado', 'FR-1') RETURNING id",
        )
        gid = cur.fetchone()[0]
        cur.execute(
            "SELECT estado, total, base FROM finanzas.facturas_recibidas WHERE gasto_origen = %s",
            (f"gastos_vehiculos:{gid}",),
        )
        fr = cur.fetchone()
        assert fr is not None, "la alta debe crear su factura recibida"
        assert fr[0] == "pagada", f"estado esperado 'pagada', obtenido {fr[0]!r}"

        # Edición a 'Pendiente' → la factura pasa a 'pendiente'.
        cur.execute("UPDATE finanzas.gastos_vehiculos SET estado_pago='Pendiente' WHERE id=%s", (gid,))
        cur.execute(
            "SELECT estado FROM finanzas.facturas_recibidas WHERE gasto_origen = %s",
            (f"gastos_vehiculos:{gid}",),
        )
        assert cur.fetchone()[0] == "pendiente"

        # Edición de importes → la factura se actualiza (base sin IVA).
        cur.execute("UPDATE finanzas.gastos_vehiculos SET importe_total=242, base_imponible=200 WHERE id=%s", (gid,))
        cur.execute(
            "SELECT total, base FROM finanzas.facturas_recibidas WHERE gasto_origen = %s",
            (f"gastos_vehiculos:{gid}",),
        )
        total, base = cur.fetchone()
        assert total == 242 and base == 200

        # Borrado → la factura desaparece.
        cur.execute("DELETE FROM finanzas.gastos_vehiculos WHERE id=%s", (gid,))
        cur.execute(
            "SELECT COUNT(*) FROM finanzas.facturas_recibidas WHERE gasto_origen = %s",
            (f"gastos_vehiculos:{gid}",),
        )
        assert cur.fetchone()[0] == 0
    finally:
        conn.close()
