"""Integración del tablero de planificación (Fase 3): un caso por bloqueo, uno por aviso
y un caso ok. NO se asigna (validar es solo lectura); la asignación es POST /api/trips/{id}/asignar."""
import pytest

import main
from routers import planificacion
from routers.planificacion import ValidarRequest


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


def _v(**kw):
    base = dict(trip_id="T1", terminal="TRAC", inicio="2026-09-24T08:00", fin="2026-09-24T12:00")
    base.update(kw)
    return ValidarRequest(**base)


@pytest.mark.integration
def test_bloqueo_remolque_ocupado(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) VALUES ('SEMI', 'SEMI', 'SEMI-1', 'semirremolque', true)")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, semirremolque_id) VALUES ('A1', 'En_Transito', 'SEMI')")
        r = planificacion.validar(_v(semirremolque_id="SEMI"), conn=conn)
        assert any(b["tipo"] == "remolque_ocupado" for b in r["bloqueos"])
        assert r["ok"] is False
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_bloqueo_tractora_solapada(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) VALUES ('TRAC', 'TRAC', 'TRAC-1', 'tractora', true)")
        conn.execute("INSERT INTO operaciones.trips (codigo, estado, terminal, fecha_esperada_carga, fecha_esperada_descarga) VALUES ('A1', 'En_Transito', 'TRAC', '2026-09-24T09:00', '2026-09-24T14:00')")
        r = planificacion.validar(_v(), conn=conn)  # T1 solapa 08:00-12:00 con A1 09:00-14:00
        assert any(b["tipo"] == "tractora_solapada" for b in r["bloqueos"])
        assert r["ok"] is False
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_bloqueo_conductor_ausente(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO empleados (id, nombre, dni) VALUES ('EMP-C', 'Conductor', '00000001T')")
        conn.execute("INSERT INTO rrhh.conductores (id, empleado_id, tarjeta_tacografo) VALUES (1, 'EMP-C', 'DID-C')")
        conn.execute("INSERT INTO ausencias_empleados (empleado_id, fecha_inicio, fecha_fin, tipo) VALUES ('EMP-C', '2026-09-20', '2026-09-30', 'vacaciones')")
        r = planificacion.validar(_v(conductor_id=1), conn=conn)
        assert any(b["tipo"] == "conductor_ausente" for b in r["bloqueos"])
        assert r["ok"] is False
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_aviso_conduccion_continua(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) VALUES ('TRAC', 'TRAC', 'TRAC-1', 'tractora', true)")
        conn.execute("INSERT INTO tacografo_dstat (did, vehiculo_id, driving_coupure_min, day_driving_min, time, creado) VALUES ('DID-1', 'TRAC', 280, 0, '2026-09-24T07:00:00Z', '2026-09-24T07:00:00Z')")
        r = planificacion.validar(_v(), conn=conn)
        assert any(a["tipo"] == "conduccion_continua" for a in r["avisos"])
        assert r["ok"] is True  # es aviso, no bloqueo
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_aviso_itv_caducada(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, fecha_caducidad_itv) VALUES ('TRAC', 'TRAC', 'TRAC-1', 'tractora', true, '2020-01-01')")
        r = planificacion.validar(_v(), conn=conn)
        assert any(a["tipo"] == "itv_caducada" for a in r["avisos"])
        assert r["ok"] is True
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_aviso_capacidad_superada(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, capacidad_peso, capacidad_palets) VALUES ('SEMI', 'SEMI', 'SEMI-1', 'semirremolque', true, 1000, 10)")
        r = planificacion.validar(_v(semirremolque_id="SEMI", kilos=2000, palets=15), conn=conn)
        assert any(a["tipo"] == "capacidad_kg" for a in r["avisos"])
        assert any(a["tipo"] == "capacidad_palets" for a in r["avisos"])
        assert r["ok"] is True
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_validar_ok(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, capacidad_peso, capacidad_palets) VALUES ('TRAC', 'TRAC', 'TRAC-1', 'tractora', true, 0, 0)")
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, capacidad_peso, capacidad_palets) VALUES ('SEMI', 'SEMI', 'SEMI-1', 'semirremolque', true, 5000, 30)")
        conn.execute("INSERT INTO empleados (id, nombre, dni) VALUES ('EMP-C', 'Conductor', '00000001T')")
        conn.execute("INSERT INTO rrhh.conductores (id, empleado_id, tarjeta_tacografo) VALUES (1, 'EMP-C', 'DID-C')")
        r = planificacion.validar(_v(semirremolque_id="SEMI", conductor_id=1, kilos=1000, palets=5), conn=conn)
        assert r["ok"] is True
        assert r["bloqueos"] == []
        assert r["avisos"] == []
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_validar_campos_nulos_200(scratch_db):
    """validar con semirremolque_id/remolque_id/fin/kilos/palets = null → 200 (no 422):
    los validadores de ValidarRequest coaccionan None → ''/0."""
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) VALUES ('TRAC', 'TRAC', 'TRAC-1', 'tractora', true)")
        req = _v(semirremolque_id=None, remolque_id=None, fin=None, kilos=None, palets=None)
        # Los validadores normalizan los nulos antes de la lógica.
        assert req.semirremolque_id == "" and req.remolque_id == "" and req.fin == ""
        assert req.kilos == 0.0 and req.palets == 0
        r = planificacion.validar(req, conn=conn)  # no lanza => 200
        assert r["ok"] is True
        assert r["bloqueos"] == [] and r["avisos"] == []
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
