"""Integración del script de migración de identidad (scripts/migrar_identidad.py)."""
import importlib.util
import os

import psycopg2
import pytest

import config


def _load():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "migrar_identidad.py")
    spec = importlib.util.spec_from_file_location("migrar_identidad", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migrar = _load()


def _raw(scratch_db):
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        user=config.DB_USER, password=config.DB_PASSWORD, dbname=scratch_db,
    )


@pytest.mark.integration
def test_matricula_ambigua_no_se_toca(scratch_db, capsys):
    """2 vehículos con la misma matrícula (uno inactivo) + viaje con esa matrícula:
    el viaje NO se toca y aparece en el informe de ambiguos."""
    conn = _raw(scratch_db)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, app_terminal, matricula, categoria, activo) "
            "VALUES ('VH-A', 'VH-A', 'VH-A', 'DUP-1', 'tractora', true)")
        cur.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, app_terminal, matricula, categoria, activo) "
            "VALUES ('VH-B', 'VH-B', 'VH-B', 'DUP-1', 'tractora', false)")
        # Viaje con terminal = matrícula (no el codigo): referencia ambigua.
        cur.execute("INSERT INTO operaciones.trips (codigo, estado, terminal) VALUES ('T-DUP', 'sin_asignar', 'DUP-1')")

        migrar._migrar(scratch_db, dry_run=False, backup_ok=True)

        # El viaje NO se toca: la matrícula es ambigua entre 2 vehículos.
        cur.execute("SELECT terminal FROM operaciones.trips WHERE codigo='T-DUP'")
        assert cur.fetchone()[0] == "DUP-1"

        # Y aparece en el informe de ambiguos.
        assert "T-DUP" in capsys.readouterr().out
    finally:
        cur.close()
        conn.close()


@pytest.mark.integration
def test_backup_falla_aborta_sin_cambios(scratch_db, monkeypatch):
    """Si pg_dump falla, el script aborta (exit != 0) y la BD queda intacta."""
    class _R:
        returncode = 1

    monkeypatch.setattr(migrar.subprocess, "run", lambda *a, **k: _R())

    conn = _raw(scratch_db)
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-C', NULL, '1234-C', 'tractora', true)")

        with pytest.raises(SystemExit) as e:
            migrar._migrar(scratch_db, dry_run=False, backup_ok=False)
        assert e.value.code != 0

        # La migración no llegó a aplicarse: terminal_trimble sigue NULL.
        cur.execute("SELECT terminal_trimble FROM flota.vehiculos WHERE codigo='VH-C'")
        assert cur.fetchone()[0] is None
    finally:
        cur.close()
        conn.close()
