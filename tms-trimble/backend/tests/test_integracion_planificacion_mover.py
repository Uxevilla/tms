"""Integración del cambio de planificación: mover (unassign + BD), enviar, quitar-terminal,
delete con remove. Usa TMS_TRIMBLE_FAKE=1 (sin red) + un cliente SOAP falso que registra
las llamadas (create/assign/deploy/unassign/remove)."""
import json

import pytest
from fastapi import HTTPException

import main
from routers import planificacion, viajes
from routers.planificacion import MoverRequest
from soap_client import TrimbleClient, _fake_calls, _fake_reset


@pytest.fixture(autouse=True)
def _fake_trimble(monkeypatch):
    monkeypatch.setenv("TMS_TRIMBLE_FAKE", "1")
    fake = lambda: TrimbleClient("u", "p", "c", "t")
    monkeypatch.setattr(planificacion, "get_client", fake)
    monkeypatch.setattr(viajes, "get_client", fake)
    _fake_reset()


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


def _mv(**kw):
    base = dict(trip_id="T1", terminal="TRAC2", inicio="2026-09-24T08:00", fin="2026-09-24T12:00")
    base.update(kw)
    return MoverRequest(**base)


def _tractoras(conn, *codigos):
    for c in codigos:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            f"VALUES ('{c}', '{c}', '{c}-1', 'tractora', true)")


# 1. mover no enviado: sin SOAP.
@pytest.mark.integration
def test_mover_no_enviado_sin_soap(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC2")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado) VALUES ('T1', 'sin_asignar')")
        r = planificacion.mover(_mv(), conn=conn)
        assert r["ok"] is True
        assert _fake_calls() == []  # 0 llamadas a Trimble
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='T1'").fetchone()
        assert row["terminal"] == "TRAC2"
        assert row["estado"] == "sin_asignar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 2. mover enviado a otra tractora: unassign y después BD.
@pytest.mark.integration
def test_mover_enviado_a_otra_tractora(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC1", "TRAC2")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T1', 'enviado', 'TRAC1')")
        r = planificacion.mover(_mv(), conn=conn)
        assert r["ok"] is True
        assert [c["op"] for c in _fake_calls()].count("unAssignTrips") == 1
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='T1'").fetchone()
        assert row["terminal"] == "TRAC2"
        assert row["estado"] == "sin_asignar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 3. mover enviado a Pendientes (terminal null): unassign.
@pytest.mark.integration
def test_mover_enviado_a_pendientes(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC1")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T1', 'enviado', 'TRAC1')")
        r = planificacion.mover(_mv(terminal=None), conn=conn)
        assert r["ok"] is True
        assert [c["op"] for c in _fake_calls()].count("unAssignTrips") == 1
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='T1'").fetchone()
        assert row["terminal"] is None
        assert row["estado"] == "sin_asignar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 4. fault de Trimble al quitar: 502 y BD intacta.
@pytest.mark.integration
def test_mover_fault_trimble_502(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC1", "TRAC2")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('E2E-FAIL-1', 'enviado', 'TRAC1')")
        with pytest.raises(HTTPException) as e:
            planificacion.mover(_mv(trip_id="E2E-FAIL-1"), conn=conn)
        assert e.value.status_code == 502
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='E2E-FAIL-1'").fetchone()
        assert row["terminal"] == "TRAC1" and row["estado"] == "enviado"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 5. bloqueo (tractora solapada): 409.
@pytest.mark.integration
def test_mover_bloqueo_409(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC2")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal, fecha_esperada_carga, fecha_esperada_descarga) "
                     "VALUES ('A1', 'En_Transito', 'TRAC2', '2026-09-24T09:00', '2026-09-24T14:00')")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado) VALUES ('T1', 'sin_asignar')")
        with pytest.raises(HTTPException) as e:
            planificacion.mover(_mv(), conn=conn)  # T1 08:00-12:00 solapa A1 09:00-14:00
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 6. en curso sin force: 409.
@pytest.mark.integration
def test_mover_en_curso_sin_force_409(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC1", "TRAC2")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T1', 'En_Transito', 'TRAC1')")
        with pytest.raises(HTTPException) as e:
            planificacion.mover(_mv(), conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 7. enviar: create+assign+deploy (cadena send_trip).
def test_enviar_create_assign_deploy():
    client = TrimbleClient("u", "p", "c", "t")
    trip = {"id": "T1", "nombre": "T1", "tasks": []}
    _fake_reset()
    results = client.send_trip(trip, "TERM")
    ops = [c["op"] for c in _fake_calls()]
    assert ops == ["createTrips", "assignTrips", "deployTrips"]
    assert all(r["ok"] for r in results)


# 8. quitar-terminal: unassign + conserva la tractora.
@pytest.mark.integration
def test_quitar_terminal(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T1', 'enviado', 'TRAC')")
        r = viajes.quitar_terminal("T1", conn=conn)
        assert r["ok"] is True
        assert [c["op"] for c in _fake_calls()].count("unAssignTrips") == 1
        row = conn.execute("SELECT terminal, estado FROM trips WHERE id='T1'").fetchone()
        assert row["terminal"] == "TRAC" and row["estado"] == "sin_asignar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 9. DELETE de un viaje enviado: remove.
@pytest.mark.integration
def test_delete_enviado_remove(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T1', 'enviado', 'TRAC')")
        r = viajes.delete_trip("T1", force=False, user={"rol": "admin"}, conn=conn)
        assert r["ok"] is True
        assert [c["op"] for c in _fake_calls()].count("removeTrips") == 1
        assert conn.execute("SELECT 1 FROM trips WHERE id='T1'").fetchone() is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 10. mover enviado a la MISMA tractora: mantiene estado, 0 SOAP, marca pendiente_reenvio.
@pytest.mark.integration
def test_mover_enviado_misma_tractora(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        _tractoras(conn, "TRAC1")
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, terminal, payload) "
            "VALUES ('T1', 'enviado', 'TRAC1', "
            "'{\"origen\": {\"nombre\": \"A\"}, \"destino\": {\"nombre\": \"B\"}}')")
        r = planificacion.mover(_mv(terminal="TRAC1", inicio="2026-09-24T10:00", fin="2026-09-24T14:00"), conn=conn)
        assert r["ok"] is True
        assert _fake_calls() == []  # 0 llamadas a Trimble (misma tractora)
        row = conn.execute("SELECT terminal, estado, payload FROM trips WHERE id='T1'").fetchone()
        assert row["terminal"] == "TRAC1"
        assert row["estado"] == "enviado"  # NO pasa a sin_asignar
        assert json.loads(row["payload"]).get("pendiente_reenvio") is True
        assert r["viaje"]["pendiente_reenvio"] is True
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


# 11. /enviar quita la marca pendiente_reenvio.
@pytest.mark.integration
def test_enviar_quita_pendiente_reenvio(scratch_db, monkeypatch):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, terminal, payload) "
            "VALUES ('T1', 'enviado', 'TRAC', "
            "'{\"origen\": {\"nombre\": \"A\"}, \"destino\": {\"nombre\": \"B\"}, "
            "\"pendiente_reenvio\": true}')")
        # Saltar el despacho SOAP/PTV real; solo se prueba que la marca se limpia.
        monkeypatch.setattr(viajes, "_enviar_viaje", lambda *a, **k: {"ok": True, "estado": "enviado"})
        r = viajes.enviar_trip("T1", force=True, conn=conn)
        assert r["ok"] is True
        row = conn.execute("SELECT payload FROM trips WHERE id='T1'").fetchone()
        assert json.loads(row["payload"]).get("pendiente_reenvio") is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
