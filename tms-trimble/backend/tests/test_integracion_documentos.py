"""PR 0 — Documentos del viaje: riesgos de pérdida (0.1) corregidos con test."""
import pytest

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


_PDF_MIN = "JVBERi0xLjQKJcOkw7zDtsOfCjIgMCBvYmoK"


@pytest.mark.integration
def test_save_file_estado_descarga(scratch_db, monkeypatch, tmp_path):
    """_save_file: contenido → descargado; error → error+intentos+ultimo_error; reintento exitoso → descargado."""
    from services.sync import _save_file
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        # Descarga exitosa.
        _save_file(None, "doc-ok.pdf", 3, "2026-01-01", "APP-1", "D1", "L1", _PDF_MIN)
        row = conn.execute(
            "SELECT estado_descarga, intentos, storage_key, mime FROM files WHERE name='doc-ok.pdf'"
        ).fetchone()
        assert row["estado_descarga"] == "descargado"
        assert row["storage_key"]

        # Descarga fallida (tipo distinto de 3 también se registra, nunca se descarta).
        _save_file(None, "doc-fail.jpg", 4, "2026-01-01", "APP-1", "D1", "L1", "", error="SOAP 500 fault")
        row = conn.execute(
            "SELECT estado_descarga, intentos, ultimo_error FROM files WHERE name='doc-fail.jpg'"
        ).fetchone()
        assert row["estado_descarga"] == "error"
        assert row["intentos"] == 1
        assert "SOAP 500" in (row["ultimo_error"] or "")

        # Reintento fallido → intentos++ (nunca se pierde la fila).
        _save_file(None, "doc-fail.jpg", 4, "", "", "", "", "", error="SOAP 500 fault")
        row = conn.execute("SELECT intentos FROM files WHERE name='doc-fail.jpg'").fetchone()
        assert row["intentos"] == 2

        # Reintento exitoso → descargado.
        _save_file(None, "doc-fail.jpg", 4, "", "", "", "", _PDF_MIN)
        row = conn.execute("SELECT estado_descarga, storage_key FROM files WHERE name='doc-fail.jpg'").fetchone()
        assert row["estado_descarga"] == "descargado"
        assert row["storage_key"]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_reintentar_descargas(scratch_db, monkeypatch, tmp_path):
    """Un fichero 'error' se reintenta en el siguiente ciclo (el mark del poll ya pasó)."""
    from services import sync as svc
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, estado_descarga, intentos, ultimo_error) "
            "VALUES ('T1', 'retry.pdf', 3, 'error', 1, 'SOAP 500 fault')"
        )

        class FakeClient:
            def __init__(self):
                self.calls = []

            def download_file(self, name):
                self.calls.append(name)
                return {"ok": True, "status": 200, "body": f"<return>{_PDF_MIN}</return>"}

        fake = FakeClient()
        monkeypatch.setattr(svc, "get_client", lambda: fake)

        svc._reintentar_descargas()

        assert fake.calls == ["retry.pdf"]
        row = conn.execute("SELECT estado_descarga, storage_key FROM files WHERE name='retry.pdf'").fetchone()
        assert row["estado_descarga"] == "descargado"
        assert row["storage_key"]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_delete_trip_soft_con_documentos(scratch_db):
    """Borrar un viaje CON documentos → se anula (soft) y los documentos se conservan."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, estado, factura) VALUES ('T-DOC', 'Entregado', NULL)"
        )
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ('T-DOC', 'cmr.pdf', 3, 'descargado')"
        )
        conn.execute(
            "INSERT INTO mensajes (id, trip_id, tipo) VALUES ('M1', 'T-DOC', 'libre')"
        )

        res = delete_trip("T-DOC", user={"rol": "admin", "usuario": "test"}, conn=conn)

        assert res["ok"] is True
        assert res["anulado"] is True
        # El viaje sigue (anulado) y los documentos/mensajes se conservan.
        row = conn.execute("SELECT anulado_at, anulado_por FROM trips WHERE id='T-DOC'").fetchone()
        assert row is not None and row["anulado_at"] is not None
        assert row["anulado_por"] == "test"
        assert conn.execute("SELECT count(*) AS n FROM files WHERE trip_id='T-DOC'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM mensajes WHERE trip_id='T-DOC'").fetchone()["n"] == 1
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_delete_trip_hard_sin_documentos(scratch_db):
    """Borrar un viaje SIN documentos → borrado físico."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-SIN', 'sin_asignar')")
        res = delete_trip("T-SIN", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert res["ok"] is True
        assert res["anulado"] is False
        assert conn.execute("SELECT 1 FROM trips WHERE id='T-SIN'").fetchone() is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_lid_map_tabla_y_vinculacion(scratch_db):
    """0.1d: lid_map es tabla (no JSON). La traza 10 registra LID→trip y el fichero se vincula."""
    from services.sync import _lid_map_put, _lid_map_trip

    tok, conn = _conn(scratch_db)
    try:
        _lid_map_put(conn, "LID-1", "TRIP-1")
        assert _lid_map_trip(conn, "LID-1") == "TRIP-1"
        assert _lid_map_trip(conn, "LID-NA") is None

        conn.execute(
            "INSERT INTO files (name, lid, trip_id, estado_descarga) VALUES ('f1.pdf', 'LID-1', NULL, 'descargado')"
        )
        conn.execute(
            "UPDATE files f SET trip_id=l.trip_id, vinculado_por='lid' "
            "FROM lid_map l WHERE f.lid=l.lid AND f.trip_id IS NULL AND l.trip_id IS NOT NULL"
        )
        row = conn.execute("SELECT trip_id, vinculado_por FROM files WHERE name='f1.pdf'").fetchone()
        assert row["trip_id"] == "TRIP-1"
        assert row["vinculado_por"] == "lid"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


def test_sha256_no_cuadra_devuelve_vacio(monkeypatch, tmp_path):
    """0.1e: sha256 alterado en disco → _leer_archivo devuelve '' (alerta de corrupción)."""
    from services.documentos import _guardar_archivo, _leer_archivo

    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))
    storage_key, sha, _, _ = _guardar_archivo("d.pdf", _PDF_MIN)
    assert _leer_archivo(storage_key, sha) != ""          # sha correcto → sirve el binario
    assert _leer_archivo(storage_key, "0" * 64) == ""     # sha alterado → '' (alerta)


@pytest.mark.integration
def test_checklist_documentacion(scratch_db):
    """PR 0.4: checklist de facturación — faltan los tipos no presentes."""
    from routers.viajes import trip_documentacion

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, cliente_id, estado) VALUES ('TRIP-DOC', 1, 'Entregado')"
        )
        conn.execute(
            "INSERT INTO files (name, trip_id, tipo_documento, estado_descarga) "
            "VALUES ('cmr1.pdf', 'TRIP-DOC', 'CMR', 'descargado')"
        )
        res = trip_documentacion("TRIP-DOC", conn)
        assert res["ok"] is False
        assert "carta_porte" in res["faltan"]
        assert "CMR" in res["presentes"]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
