"""PR 1 — Mensajería: clase (libre/formulario) + parseo del <Report> + normalización de source."""
import pytest

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


def _bloque(mid="MSG-1", messagetype="FORM1", source="PM52 (V3 Bart)", body=""):
    return (
        f"<message><id>{mid}</id><messagetype>{messagetype}</messagetype>"
        f"<source>{source}</source><time>2026-09-26T10:00:00Z</time>"
        f"<needreply>false</needreply><body>{body}</body></message>"
    )


@pytest.mark.integration
def test_store_mensaje_formulario_parsea_report(scratch_db):
    """Un mensaje estructurado con <Report> se guarda como clase=formulario con respuestas parseadas."""
    from services.mensajeria import _store_mensaje

    body = (
        "&lt;Report id=\"FORM1\" version=\"1\" timestamp=\"1700000000000\"&gt;"
        "&lt;Answer question=\"Q1\"&gt;&lt;Value option=\"O1\" value=\"V1\"/&gt;&lt;/Answer&gt;"
        "&lt;/Report&gt;"
    )
    tok, conn = _conn(scratch_db)
    try:
        _store_mensaje(_bloque(body=body), "estructurado")
        row = conn.execute(
            "SELECT clase, report_id, report_version, respuestas, terminal "
            "FROM mensajes WHERE id='MSG-1'"
        ).fetchone()
        assert row["clase"] == "formulario"
        assert row["report_id"] == "FORM1"
        assert row["report_version"] == "1"
        assert row["respuestas"] == [{"question": "Q1", "option": "O1", "value": "V1"}]
        # source "PM52 (V3 Bart)" → terminal normalizado "PM52".
        assert row["terminal"] == "PM52"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_store_mensaje_libre_clase_sin_respuestas(scratch_db):
    """Un mensaje libre (texto plano) se guarda como clase=libre y respuestas NULL."""
    from services.mensajeria import _store_mensaje

    tok, conn = _conn(scratch_db)
    try:
        _store_mensaje(_bloque(mid="MSG-2", messagetype="", body="Hola, todo ok"), "libre")
        row = conn.execute(
            "SELECT clase, report_id, respuestas FROM mensajes WHERE id='MSG-2'"
        ).fetchone()
        assert row["clase"] == "libre"
        assert row["report_id"] is None
        assert row["respuestas"] is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_store_mensaje_body_no_report_no_rompe(scratch_db):
    """Un estructurado cuyo body NO es un <Report> no rompe: se guarda crudo, clase=formulario."""
    from services.mensajeria import _store_mensaje

    tok, conn = _conn(scratch_db)
    try:
        _store_mensaje(_bloque(mid="MSG-3", body="esto no es xml"), "estructurado")
        row = conn.execute(
            "SELECT clase, report_id, respuestas, body FROM mensajes WHERE id='MSG-3'"
        ).fetchone()
        assert row["clase"] == "formulario"
        assert row["report_id"] is None
        assert row["respuestas"] is None
        assert row["body"] == "esto no es xml"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
