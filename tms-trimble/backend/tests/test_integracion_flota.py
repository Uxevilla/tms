"""Integración de alta/edición de vehículos (Fase 5).

El ID de telemetría (`terminal_trimble`) es un campo propio, distinto de la
matrícula (referencia del vehículo en el TMS) y del código interno (`codigo`).
Se guarda al dar de alta (POST) y se puede editar (PATCH).
"""
import pytest

import main
from models import Vehiculo
from routers import flota


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_alta_vehiculo_guarda_terminal_trimble(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        v = Vehiculo(
            id="VH-001",
            terminal_trimble="TRIMBLE-123",
            matricula="1234-ABC",
            categoria="tractora",
            marca="Volvo",
            modelo="FH",
            anno=2020,
        )
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT codigo, terminal_trimble, matricula FROM flota.vehiculos WHERE codigo = 'VH-001'"
        ).fetchone()
        assert row is not None
        assert row["terminal_trimble"] == "TRIMBLE-123", "el ID de telemetría debe guardarse aparte"
        assert row["matricula"] == "1234-ABC"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_alta_vehiculo_sin_terminal_trimble(scratch_db):
    """El ID de telemetría es opcional: un vehículo sin terminal no debe romper el alta."""
    tok, conn = _conn(scratch_db)
    try:
        v = Vehiculo(id="VH-002", matricula="5678-DEF", categoria="furgon", anno=2021)
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT terminal_trimble FROM flota.vehiculos WHERE codigo = 'VH-002'"
        ).fetchone()
        assert row is not None
        assert row["terminal_trimble"] in (None, ""), "sin terminal debe quedar vacío"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_edicion_vehiculo_actualiza_terminal_trimble(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES ('VH-003', 'TRIMBLE-OLD', '9012-GHI', true)"
        )
        flota.upd_vehiculo("VH-003", {"terminal_trimble": "TRIMBLE-NEW"}, conn=conn)

        row = conn.execute(
            "SELECT terminal_trimble FROM flota.vehiculos WHERE codigo = 'VH-003'"
        ).fetchone()
        assert row["terminal_trimble"] == "TRIMBLE-NEW", "el PATCH debe actualizar el ID de telemetría"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_alta_vehiculo_guarda_app_terminal(scratch_db):
    """El terminal APP (Fleet XPS) se guarda en el alta (POST)."""
    tok, conn = _conn(scratch_db)
    try:
        v = Vehiculo(
            id="VH-004",
            terminal_trimble="TRIMBLE-124",
            app_terminal="APP-CONDUCTOR-1",
            matricula="1111-AAA",
            categoria="tractora",
        )
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT terminal_trimble, app_terminal FROM flota.vehiculos WHERE codigo = 'VH-004'"
        ).fetchone()
        assert row is not None
        assert row["app_terminal"] == "APP-CONDUCTOR-1", "el terminal APP debe guardarse en el alta"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_edicion_vehiculo_actualiza_app_terminal(scratch_db):
    """El terminal APP se puede editar con el PATCH (visible en alta y edición)."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, app_terminal, activo) "
            "VALUES ('VH-005', 'TRIMBLE-125', '2222-BBB', 'APP-ORIGINAL', true)"
        )
        flota.upd_vehiculo("VH-005", {"app_terminal": "APP-CAMBIADA"}, conn=conn)

        row = conn.execute(
            "SELECT app_terminal FROM flota.vehiculos WHERE codigo = 'VH-005'"
        ).fetchone()
        assert row["app_terminal"] == "APP-CAMBIADA", "el PATCH debe actualizar el terminal APP"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
