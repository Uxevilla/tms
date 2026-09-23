"""Tests de la pista de auditoría contable (sin BD)."""
import main


class _RecordingConn:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return {"id": 1}


def test_auditar_registra_insert():
    conn = _RecordingConn()
    main._auditar(conn, "asientos", 5, "crear", "admin", despues={"numero": 1})
    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "INSERT INTO audit_log" in sql
    assert params[0] == "asientos"
    assert params[1] == "5"          # registro_id serializado a str
    assert params[2] == "crear"
    assert params[3] == "admin"
    assert params[4] is None         # antes ausente
    assert '"numero": 1' in params[5]  # despues serializado a JSON


def test_auditar_serializa_antes():
    conn = _RecordingConn()
    main._auditar(conn, "clientes", 9, "eliminar", "admin", antes={"nombre": "X", "cif": "B1"})
    _, params = conn.calls[0]
    assert '"nombre": "X"' in params[4]
    assert params[5] is None


def test_auditar_usuario_desde_contexto():
    conn = _RecordingConn()
    token = main._usuario_ctx.set("eugenio")
    try:
        main._auditar(conn, "asientos", 1, "crear")  # sin usuario explícito
        assert conn.calls[0][1][3] == "eugenio"
    finally:
        main._usuario_ctx.reset(token)


def test_auditar_usuario_default_sistema():
    conn = _RecordingConn()
    main._auditar(conn, "asientos", 1, "crear")  # contexto por defecto
    assert conn.calls[0][1][3] == "sistema"
