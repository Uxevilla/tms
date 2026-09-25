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
            terminal_trimble="TRIMBLE-123",
            matricula="1234-ABC",
            categoria="tractora",
            marca="Volvo",
            modelo="FH",
            anno=2020,
        )
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT codigo, terminal_trimble, matricula FROM flota.vehiculos WHERE matricula = '1234-ABC'"
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
        v = Vehiculo(matricula="5678-DEF", categoria="furgon", anno=2021)
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT terminal_trimble FROM flota.vehiculos WHERE matricula = '5678-DEF'"
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
            terminal_trimble="TRIMBLE-124",
            app_terminal="APP-CONDUCTOR-1",
            matricula="1111-AAA",
            categoria="tractora",
        )
        flota.add_vehiculo(v, conn=conn)

        row = conn.execute(
            "SELECT terminal_trimble, app_terminal FROM flota.vehiculos WHERE matricula = '1111-AAA'"
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


@pytest.mark.integration
def test_source_a_vehiculo_resuelve_por_terminal_trimble(scratch_db):
    """El source de la traza se resuelve por terminal_trimble, no por código ni sufijo."""
    from services.telemetria import _source_a_vehiculo

    tok, conn = _conn(scratch_db)
    try:
        flota.add_vehiculo(Vehiculo(terminal_trimble="CCV6-EUSEBIO", matricula="7000NLT", categoria="tractora"), conn=conn)
        flota.add_vehiculo(Vehiculo(terminal_trimble="APP_EUSEBIO", matricula="6090NLT", categoria="tractora"), conn=conn)

        # El terminal CCV6 no debe caer en el fallback por sufijo (que lo asignaba a APP_EUSEBIO)
        assert _source_a_vehiculo(conn, "CCV6-EUSEBIO") == "CCV6-EUSEBIO"
        assert _source_a_vehiculo(conn, "APP_EUSEBIO") == "APP_EUSEBIO"
        # Fallback por sufijo: "T4U-EUSEBIO" → sufijo "EUSEBIO" resuelve a CCV6-EUSEBIO.
        assert _source_a_vehiculo(conn, "T4U-EUSEBIO") == "CCV6-EUSEBIO"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_edicion_vehiculo_codigo_fijo(scratch_db):
    """Al editar la matrícula, el código interno NO cambia (es fijo)."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES ('VH-006', 'TRIMBLE-126', '3333-CCC', true)"
        )
        flota.upd_vehiculo("VH-006", {"matricula": "4444-DDD"}, conn=conn)

        row = conn.execute(
            "SELECT codigo, matricula FROM flota.vehiculos WHERE codigo = 'VH-006'"
        ).fetchone()
        assert row is not None
        assert row["codigo"] == "VH-006", "el código interno no cambia al editar la matrícula"
        assert row["matricula"] == "4444-DDD", "la matrícula sí se actualiza"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
