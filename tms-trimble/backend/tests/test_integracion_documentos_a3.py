"""A3 — Documentos del viaje: clasificación por pregunta, vinculación por ventana,
migración lid_map y alertas de descarga.
"""
import pytest

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_clasificacion_por_pregunta_mapping(scratch_db, monkeypatch, tmp_path):
    """Un fichero 'pod-*.png' con (report_id, question_id) mapeado a CMR → tipo CMR."""
    from services.sync import _save_file
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-DOC', 'Entregado')")
        conn.execute(
            "INSERT INTO cfg_tipo_documento_pregunta (report_id, question_id, tipo_documento) "
            "VALUES ('REP-1', 'Q-1', 'CMR')"
        )
        _save_file("T-DOC", "pod-1428065934030.png", 3, "2026-09-26T10:00:00Z", "APP-1", "D1", "L1",
                   "aGVsbG8=", report_id="REP-1", question_id="Q-1")
        row = conn.execute(
            "SELECT tipo_documento, report_id, question_id FROM files WHERE name='pod-1428065934030.png'"
        ).fetchone()
        assert row["tipo_documento"] == "CMR"
        assert row["report_id"] == "REP-1"
        assert row["question_id"] == "Q-1"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_clasificacion_sin_mapeo_cae_a_nombre(scratch_db, monkeypatch, tmp_path):
    """Sin mapeo, el nombre sigue mandando (fallback)."""
    from services.sync import _save_file
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-DOC2', 'Entregado')")
        _save_file("T-DOC2", "cmr_scan.png", 3, "2026-09-26T10:00:00Z", "APP-1", "D1", "L1", "aGVsbG8=")
        row = conn.execute("SELECT tipo_documento FROM files WHERE name='cmr_scan.png'").fetchone()
        assert row["tipo_documento"] == "CMR"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_vincular_por_vehiculo_ventana(scratch_db):
    """Fichero sin viaje dentro de la franja horaria del viaje → vinculado."""
    from routers.viajes import _vincular_por_vehiculo_ventana

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, terminal, fecha_esperada_carga, fecha_esperada_descarga, estado) "
            "VALUES ('T-WIN', 'APP-1', '2026-09-26T08:00', '2026-09-26T12:00', 'enviado')"
        )
        conn.execute(
            "INSERT INTO files (name, source, ftime, trip_id, estado_descarga) "
            "VALUES ('pod-x.png', 'APP-1', '2026-09-26T10:00:00Z', NULL, 'descargado')"
        )
        n = _vincular_por_vehiculo_ventana(conn)
        assert n == 1
        row = conn.execute("SELECT trip_id, vinculado_por FROM files WHERE name='pod-x.png'").fetchone()
        assert row["trip_id"] == "T-WIN"
        assert row["vinculado_por"] == "vehiculo_ventana"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_fichero_fuera_de_ventana_queda_sin_viaje(scratch_db):
    """Fichero fuera de cualquier ventana → queda en la bandeja 'sin viaje'."""
    from routers.viajes import _vincular_por_vehiculo_ventana, documentos_sin_viaje

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, terminal, fecha_esperada_carga, fecha_esperada_descarga, estado) "
            "VALUES ('T-WIN', 'APP-1', '2026-09-26T08:00', '2026-09-26T12:00', 'enviado')"
        )
        conn.execute(
            "INSERT INTO files (name, source, ftime, trip_id, estado_descarga) "
            "VALUES ('pod-fuera.png', 'APP-1', '2026-09-27T10:00:00Z', NULL, 'descargado')"
        )
        n = _vincular_por_vehiculo_ventana(conn)
        assert n == 0
        bandeja = documentos_sin_viaje(user={"rol": "admin"}, conn=conn)
        assert any(d["name"] == "pod-fuera.png" for d in bandeja["documentos"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_migrar_lid_map_json(scratch_db):
    """El JSON antiguo de sync_state (lid_map) se migra a la tabla y re-vincula ficheros."""
    from services.sync import migrar_lid_map_json

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-MIG', 'Entregado')")
        conn.execute(
            "INSERT INTO sistema.sync_state (key, value) VALUES ('lid_map', '{\"LID-1\": \"T-MIG\"}')"
        )
        conn.execute(
            "INSERT INTO files (name, lid, trip_id, estado_descarga) "
            "VALUES ('pod-lid.png', 'LID-1', NULL, 'descargado')"
        )
        n = migrar_lid_map_json(conn)
        assert n == 1
        row = conn.execute("SELECT trip_id, vinculado_por FROM files WHERE name='pod-lid.png'").fetchone()
        assert row["trip_id"] == "T-MIG"
        assert row["vinculado_por"] == "lid"
        assert conn.execute("SELECT 1 FROM sistema.sync_state WHERE key='lid_map'").fetchone() is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_alerta_descarga_error_en_atencion(scratch_db):
    """Fichero con descarga fallida repetida (>=5 intentos) → alerta en Torre › Atención."""
    from routers.torre import atencion

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-ERR', 'Entregado')")
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, estado_descarga, intentos, ultimo_error) "
            "VALUES ('T-ERR', 'pod-err.png', 3, 'error', 5, 'SOAP 500')"
        )
        r = atencion(user={"rol": "admin"}, conn=conn)
        assert any(it["tipo"] == "documento_descarga_error" for it in r["items"])
        assert r["resumen"]["documentos_sin_viaje"] == 0
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_reclasificar_documento_audita(scratch_db):
    """Reclasificar un documento actualiza el tipo y deja auditoría."""
    from routers.viajes import reclasificar_documento

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-REC', 'Entregado')")
        fid = conn.execute(
            "INSERT INTO files (trip_id, name, ftype, estado_descarga, tipo_documento) "
            "VALUES ('T-REC', 'pod-r.png', 3, 'descargado', 'otro') RETURNING id"
        ).fetchone()["id"]
        r = reclasificar_documento("T-REC", fid, {"tipo_documento": "CMR"},
                                   user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert r["ok"] is True
        row = conn.execute("SELECT tipo_documento FROM files WHERE id=?", (fid,)).fetchone()
        assert row["tipo_documento"] == "CMR"
        audit = conn.execute(
            "SELECT accion FROM documentos_auditoria WHERE file_id=? ORDER BY id DESC LIMIT 1", (fid,)
        ).fetchone()
        assert audit is not None and audit["accion"] == "reclasificar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_respuesta_fichero_clasifica_informe_primero(scratch_db, monkeypatch, tmp_path):
    """E2E: informe (traza 13) primero → luego pollFiles → tipo por pregunta + trip_id."""
    from services.sync import _registrar_files_origen, _save_file
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-1', 'Entregado')")
        conn.execute(
            "INSERT INTO cfg_tipo_documento_pregunta (report_id, question_id, tipo_documento) "
            "VALUES ('SOLERA_DESCARGA', 'SCAN_CMR', 'CMR')"
        )
        # 1) informe llega primero
        _registrar_files_origen(conn, [{"question": "SCAN_CMR", "option": "O1", "value": "doc_1.gif"}],
                                "SOLERA_DESCARGA", "T-1", "MSG-1", "LID-1")
        # 2) luego el fichero (pollFiles)
        _save_file(None, "doc_1.gif", 3, "2026-09-26T10:00:00Z", "APP-1", "D1", "LID-1", "aGVsbG8=")
        row = conn.execute(
            "SELECT trip_id, tipo_documento, report_id, question_id, vinculado_por FROM files WHERE name='doc_1.gif'"
        ).fetchone()
        assert row["tipo_documento"] == "CMR"
        assert row["trip_id"] == "T-1"
        assert row["report_id"] == "SOLERA_DESCARGA"
        assert row["question_id"] == "SCAN_CMR"
        assert row["vinculado_por"] == "respuesta"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_respuesta_fichero_clasifica_fichero_primero(scratch_db, monkeypatch, tmp_path):
    """E2E: fichero primero (pollFiles) → luego informe → re-clasifica por pregunta."""
    from services.sync import _registrar_files_origen, _save_file
    monkeypatch.setattr("services.documentos.DOCS_DIR", str(tmp_path))

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-1', 'Entregado')")
        conn.execute(
            "INSERT INTO cfg_tipo_documento_pregunta (report_id, question_id, tipo_documento) "
            "VALUES ('SOLERA_DESCARGA', 'SCAN_CMR', 'CMR')"
        )
        # 1) fichero primero (sin informe)
        _save_file(None, "doc_1.gif", 3, "2026-09-26T10:00:00Z", "APP-1", "D1", "LID-1", "aGVsbG8=")
        row = conn.execute("SELECT tipo_documento FROM files WHERE name='doc_1.gif'").fetchone()
        assert row["tipo_documento"] == "otro"  # nombre sin pista
        # 2) luego el informe → re-clasifica
        _registrar_files_origen(conn, [{"question": "SCAN_CMR", "option": "O1", "value": "doc_1.gif"}],
                                "SOLERA_DESCARGA", "T-1", "MSG-1", "LID-1")
        row = conn.execute(
            "SELECT trip_id, tipo_documento, report_id, question_id FROM files WHERE name='doc_1.gif'"
        ).fetchone()
        assert row["tipo_documento"] == "CMR"
        assert row["trip_id"] == "T-1"
        assert row["report_id"] == "SOLERA_DESCARGA"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_ventana_utc(scratch_db):
    """Fichero a las 07:30Z y viaje 09:00–12:00 Madrid → vinculado (comparación UTC)."""
    from services.sync import _vincular_por_vehiculo_ventana

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, terminal, fecha_esperada_carga, fecha_esperada_descarga, estado) "
            "VALUES ('T-UTC', 'APP-1', '2026-09-26T09:00', '2026-09-26T12:00', 'enviado')"
        )
        conn.execute(
            "INSERT INTO files (name, source, ftime, trip_id, estado_descarga) "
            "VALUES ('pod-utc.png', 'APP-1', '2026-09-26T07:30:00Z', NULL, 'descargado')"
        )
        n = _vincular_por_vehiculo_ventana(conn)
        assert n == 1
        row = conn.execute("SELECT trip_id FROM files WHERE name='pod-utc.png'").fetchone()
        assert row["trip_id"] == "T-UTC"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_itv_vehiculo_no_bandeja(scratch_db):
    """Un documento de vehículo (ITV) no aparece en la bandeja 'sin viaje' ni se vincula."""
    from services.sync import _vincular_por_vehiculo_ventana
    from routers.viajes import documentos_sin_viaje

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO files (name, vehiculo_id, source, ftime, estado_descarga) "
            "VALUES ('itv.pdf', 'VH-1', 'vehiculo', '2026-09-26T10:00:00Z', 'descargado')"
        )
        n = _vincular_por_vehiculo_ventana(conn)
        assert n == 0
        bandeja = documentos_sin_viaje(user={"rol": "admin"}, conn=conn)
        assert all(d["name"] != "itv.pdf" for d in bandeja["documentos"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
