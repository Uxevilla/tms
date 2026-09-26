"""PR 2 — Question paths: importación de ReportDefinition + traducción de respuestas."""
import pytest

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


_XML_DEF = (
    '<ReportDefinition id="FORMID" version="1" firstquestion="Q1">'
    '<Question id="Q1" text="¿Entrega correcta?" selectionmodel="single">'
    '<Option id="O1" text="Sí" valuetype="text" nextquestion="Q2"/>'
    '<Option id="O2" text="No" valuetype="text" nextquestion="END"/>'
    '</Question>'
    '<Question id="Q2" text="Observaciones" selectionmodel="single">'
    '<Option id="O3" text="OK" valuetype="text" nextquestion="END"/>'
    '</Question>'
    '</ReportDefinition>'
)


@pytest.mark.integration
def test_importar_definicion_y_traducir(scratch_db):
    """Importa una definición válida y traduce respuestas crudas a texto legible."""
    from services.question_paths import importar_definicion, _traducir_respuestas

    tok, conn = _conn(scratch_db)
    try:
        res = importar_definicion(_XML_DEF)
        assert res["ok"] is True, res
        assert res["report_id"] == "FORMID"
        assert res["preguntas"] == 2

        # Traducción de respuestas crudas.
        crudas = [
            {"question": "Q1", "option": "O1", "value": "V1"},
            {"question": "Q2", "option": "O3", "value": ""},
        ]
        trad = _traducir_respuestas(conn, "FORMID", crudas)
        assert trad[0]["pregunta"] == "¿Entrega correcta?"
        assert trad[0]["opcion"] == "Sí"
        assert trad[1]["pregunta"] == "Observaciones"
        assert trad[1]["opcion"] == "OK"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_importar_definicion_idempotente(scratch_db):
    """Re-importar el mismo report_id sustituye (no duplica)."""
    from services.question_paths import importar_definicion

    tok, conn = _conn(scratch_db)
    try:
        importar_definicion(_XML_DEF)
        importar_definicion(_XML_DEF)
        n = conn.execute("SELECT count(*) AS n FROM qp_definiciones WHERE report_id='FORMID'").fetchone()["n"]
        assert n == 1
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_importar_definicion_invalida(scratch_db):
    """nextquestion inexistente → ok=False con errores, no se guarda."""
    from services.question_paths import importar_definicion

    xml_bad = (
        '<ReportDefinition id="FORMID" version="1" firstquestion="Q1">'
        '<Question id="Q1" text="P" selectionmodel="single">'
        '<Option id="O1" text="Op" valuetype="text" nextquestion="Q99"/>'
        '</Question>'
        '</ReportDefinition>'
    )
    tok, conn = _conn(scratch_db)
    try:
        res = importar_definicion(xml_bad)
        assert res["ok"] is False
        assert any("nextquestion" in e for e in res["errores"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_traducir_sin_definicion_devuelve_crudas(scratch_db):
    """Sin definición importada, _traducir_respuestas devuelve las crudas."""
    from services.question_paths import _traducir_respuestas

    tok, conn = _conn(scratch_db)
    try:
        crudas = [{"question": "Q1", "option": "O1", "value": "V"}]
        trad = _traducir_respuestas(conn, "NOEXISTE", crudas)
        assert trad[0]["pregunta"] == "Q1"
        assert trad[0]["opcion"] == "O1"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
