"""Integración de la relación telemetría ↔ vehículo (modelo corregido).

La telemetría (posiciones) se asocia al terminal_trimble (ID del proveedor de
telemetría, hoy Trimble), y ese ID se relaciona con la matrícula del TMS. El
codigo es un campo interno distinto de ambos.
"""
import pytest

import main
from services.telemetria import _viajes_snapshot


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_snapshot_une_posicion_por_terminal_trimble_y_matricula(scratch_db):
    """codigo ≠ terminal_trimble ≠ matricula: la posición se une por terminal_trimble vía matricula."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES ('COD-A', 'TRIM-A', 'MAT-A', true)"
        )
        conn.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) "
            "VALUES (now(), 'TRIM-A', 40.4, -3.7)"
        )
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, matricula, estado) "
            "VALUES ('VIAJE-A', 'MAT-A', 'En_Transito')"
        )
        snap = _viajes_snapshot()
        viaje = snap.get("VIAJE-A")
        assert viaje is not None
        assert viaje["lat"] == 40.4, "la posición debe unirse por terminal_trimble (vía matrícula)"
        assert viaje["lng"] == -3.7
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_source_a_vehiculo_resuelve_por_terminal_trimble(scratch_db):
    """El source de una traza (ID Trimble) resuelve por terminal_trimble, no por codigo."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES ('AAA-1', 'BBB-2', 'CCC-3', true)"
        )
        from services.telemetria import _source_a_vehiculo
        assert _source_a_vehiculo(conn, "BBB-2") == "BBB-2"
        assert _source_a_vehiculo(conn, "AAA-1") is None, "el codigo no es clave de telemetría"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
