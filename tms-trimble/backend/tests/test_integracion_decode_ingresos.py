"""Tests de la ronda 4 de Claude: decode_ok del DSTAT + ingresos por codigo en torre."""
import datetime

import pytest

import main
from routers import torre, flota


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_dstat_decode_fallido_no_pisa_valida(scratch_db):
    """Lectura válida (250 min) + otra posterior con decode fallido → el lector sigue viendo 250."""
    from services.tacografo import _ingestar_dstat, _dstat_terminal

    tok, conn = _conn(scratch_db)
    try:
        # Lectura válida: 250 min de conducción continua.
        _ingestar_dstat("DID-1", "src", "T4U-1", "raw-ok",
                        {"driving_coupure": 250, "day_driving": 0}, "2026-09-24T08:00:00Z")
        # Decode fallido posterior (decoded vacío) → decode_ok=false, columnas NULL.
        _ingestar_dstat("DID-1", "src", "T4U-1", "raw-malo", {}, "2026-09-24T09:00:00Z")

        # El lector sigue devolviendo la lectura válida (ignora la fallida).
        stats = _dstat_terminal("T4U-1")
        assert stats is not None
        assert stats["driving_coupure"] == 250.0

        # La fila fallida se guardó con decode_ok=false y driving_coupure_min NULL.
        row = conn.execute(
            "SELECT decode_ok, driving_coupure_min FROM tacografo_dstat WHERE dstat_raw='raw-malo'"
        ).fetchone()
        assert row is not None
        assert row["decode_ok"] is False
        assert row["driving_coupure_min"] is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_entidad_vehiculo_ingresos_por_codigo(scratch_db):
    """Cambiar la matrícula NO cambia los ingresos del mes (trips.terminal = codigo)."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('V-ING', 'T-ING', 'MAT-ING', 'tractora', true)"
        )
        hoy = datetime.date.today().isoformat()
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, terminal, precio, creado) "
            "VALUES ('TRIP-ING', 'Entregado', 'V-ING', 100, ?)", (hoy,)
        )

        r = torre.entidad_vehiculo("V-ING", user={"rol": "admin"}, conn=conn)
        assert r["coste_margen_mes"]["ingresos"] == 100.0

        # Cambiar la matrícula → los ingresos del mes no cambian (cruza por codigo, no matrícula).
        flota.upd_vehiculo("V-ING", {"matricula": "MAT-NUEVA"}, conn=conn)
        r2 = torre.entidad_vehiculo("V-ING", user={"rol": "admin"}, conn=conn)
        assert r2["coste_margen_mes"]["ingresos"] == 100.0
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
