"""Normalización del estado de pago en gastos_veh_sync_fr (Fase 0, fix 3).

El trigger que alimenta facturas_recibidas desde gastos_vehiculos debe normalizar
estado_pago ('Pagado'/'Pendiente') a 'pagada'/'pendiente' para que tesorería y
vencimientos (que filtran por estado='pagada') cuenten los gastos de vehículo.
"""
import db


def test_gastos_veh_sync_fr_normaliza_estado():
    sql = db._SCHEMA_VIEWS
    # La normalización está declarada...
    assert (
        "v_estado := CASE WHEN lower(COALESCE(NEW.estado_pago,'')) "
        "IN ('pagado','pagada') THEN 'pagada' ELSE 'pendiente' END" in sql
    )
    # ...y se usa en el INSERT y en el UPDATE (no se inserta NEW.estado_pago sin normalizar).
    assert "estado=v_estado" in sql
    assert "v_estado, 'vehiculo'" in sql
