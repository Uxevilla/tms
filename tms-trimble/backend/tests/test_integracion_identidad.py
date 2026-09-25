"""Test clave de identidad del vehículo (revisión de Claude, punto G).

codigo ≠ matricula ≠ terminal_trimble ≠ app_terminal: la posición se une por
terminal_trimble (vía código), el viaje se despacha a la APP, y cambiar la matrícula
no rompe los enlaces (viajes/mantenimientos) porque estos van por código.
"""
import pytest

import main
from services.vehiculos import terminal_trimble_de, app_terminal_de, codigo_por_terminal_trimble


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_identidad_cuatro_valores_distintos(scratch_db, monkeypatch):
    from soap_client import TrimbleClient, _fake_calls, _fake_reset
    from routers import viajes, flota
    from services import viajes as sv
    from models import Mantenimiento
    from services import telemetria

    monkeypatch.setenv("TMS_TRIMBLE_FAKE", "1")
    _fake = lambda: TrimbleClient("u", "p", "c", "t")
    monkeypatch.setattr(viajes, "get_client", _fake)
    monkeypatch.setattr(sv, "get_client", _fake)
    _fake_reset()

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, app_terminal, matricula, categoria, activo) "
            "VALUES ('VH-1', 'T4U-1', 'APP-1', '1111-AAA', 'tractora', true)"
        )
        conn.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) "
            "VALUES (now(), 'T4U-1', 40.4, -3.7)"
        )
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, terminal, estado, payload) "
            "VALUES ('VIAJE-1', 'VH-1', 'sin_asignar', "
            "'{\"origen\": {\"nombre\": \"A\"}, \"destino\": {\"nombre\": \"B\"}}')"
        )

        # 1. traducción única: terminal_trimble ↔ codigo ↔ app_terminal
        assert codigo_por_terminal_trimble("T4U-1", conn=conn) == "VH-1"
        assert terminal_trimble_de("VH-1", conn=conn) == "T4U-1"
        assert app_terminal_de("VH-1", conn=conn) == "APP-1"

        # 2. enviar → se despacha (el SOAP usa app_terminal APP-1) y trips.terminal = codigo
        r = viajes.enviar_trip("VIAJE-1", conn=conn)
        assert r["ok"] is True
        ops = [c["op"] for c in _fake_calls()]
        assert "deployTrips" in ops
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='VIAJE-1'").fetchone()
        assert row["terminal"] == "VH-1"      # el código, no el terminal_trimble
        assert row["estado"] == "enviado"

        # 3. el snapshot une la posición por terminal_trimble (vía código)
        snap = telemetria._viajes_snapshot()
        viaje = snap.get("VIAJE-1")
        assert viaje is not None and viaje["lat"] == 40.4

        # 4. cambiar la matrícula NO rompe los enlaces (van por código)
        conn.execute("UPDATE flota.vehiculos SET matricula='2222-BBB' WHERE codigo='VH-1'")
        assert conn.execute("SELECT terminal FROM trips WHERE id='VIAJE-1'").fetchone()["terminal"] == "VH-1"

        # 5. mantenimiento enlazado por código sobrevive al cambio de matrícula
        flota.add_mantenimiento(
            Mantenimiento(vehiculo_id="VH-1", tipo="revision", fecha="2026-01-01", km=0, coste=0),
            conn=conn,
        )
        assert conn.execute(
            "SELECT vehiculo_id FROM flota.mantenimientos WHERE vehiculo_id='VH-1'"
        ).fetchone() is not None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
