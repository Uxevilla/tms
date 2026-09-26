"""Tests del parser de question paths (services/cuestionarios.py)."""
import pytest

from services.cuestionarios import parse_report, parse_report_definition, validar_definicion


# --- parse_report -----------------------------------------------------------

def test_parse_report_cdata():
    xml = (
        '<![CDATA[<Report id="FORMID" version="1" timestamp="1700000000000">'
        '<Answer question="Q1"><Value option="O1" value="V1"/></Answer>'
        '</Report>]]>'
    )
    assert parse_report(xml) == {
        "report_id": "FORMID",
        "version": "1",
        "timestamp": "1700000000000",
        "respuestas": [{"question": "Q1", "option": "O1", "value": "V1"}],
    }


def test_parse_report_entidades_html():
    xml = (
        '&lt;Report id="FORMID" version="2" timestamp="1700000000001"&gt;'
        '&lt;Answer question="Q2"&gt;'
        '&lt;Value option="O2" value="V2"/&gt;'
        '&lt;/Answer&gt;&lt;/Report&gt;'
    )
    out = parse_report(xml)
    assert out["report_id"] == "FORMID"
    assert out["version"] == "2"
    assert out["timestamp"] == "1700000000001"
    assert out["respuestas"] == [{"question": "Q2", "option": "O2", "value": "V2"}]


def test_parse_report_doblemente_escapado():
    xml = (
        '&amp;lt;Report id="FORMID" version="1" timestamp="1"&amp;gt;'
        '&amp;lt;Answer question="Q1"&amp;gt;'
        '&amp;lt;Value option="O1" value="V1"/&amp;gt;'
        '&amp;lt;/Answer&amp;gt;&amp;lt;/Report&amp;gt;'
    )
    assert parse_report(xml)["respuestas"] == [{"question": "Q1", "option": "O1", "value": "V1"}]


def test_parse_report_multi_value():
    xml = (
        '<Report id="FORMID" version="1" timestamp="1700000000000">'
        '<Answer question="Q1">'
        '<Value option="O1" value="A"/>'
        '<Value option="O2" value="B"/>'
        '</Answer>'
        '</Report>'
    )
    assert parse_report(xml)["respuestas"] == [
        {"question": "Q1", "option": "O1", "value": "A"},
        {"question": "Q1", "option": "O2", "value": "B"},
    ]


def test_parse_report_value_sin_option_ni_value():
    xml = (
        '<Report id="F" version="1" timestamp="1">'
        '<Answer question="Q"><Value/></Answer>'
        '</Report>'
    )
    assert parse_report(xml)["respuestas"] == [
        {"question": "Q", "option": None, "value": ""},
    ]


def test_parse_report_no_es_report():
    with pytest.raises(ValueError):
        parse_report("<NotReport/>")


# --- parse_report_definition -------------------------------------------------

def test_parse_report_definition_valida():
    xml = (
        '<ReportDefinition id="FORMID" version="1" firstquestion="Q1">'
        '<Question id="Q1" text="Pregunta 1" selectionmodel="single">'
        '<Option id="O1" text="Op 1" valuetype="text" inputmask="" nextquestion="Q2"/>'
        '</Question>'
        '<Question id="Q2" text="Pregunta 2" selectionmodel="single">'
        '<Option id="O2" text="Op 2" valuetype="text" inputmask="" nextquestion="END"/>'
        '</Question>'
        '</ReportDefinition>'
    )
    out = parse_report_definition(xml)

    assert out["report_id"] == "FORMID"
    assert out["version"] == "1"
    assert out["firstquestion"] == "Q1"
    assert len(out["preguntas"]) == 2

    q1 = out["preguntas"][0]
    assert q1["question_id"] == "Q1"
    assert q1["texto"] == "Pregunta 1"
    assert q1["selectionmodel"] == "single"
    assert q1["hide"] is False
    assert q1["opciones"] == [{
        "option_id": "O1",
        "texto": "Op 1",
        "valuetype": "text",
        "inputmask": "",
        "readonly": False,
        "nextquestion": "Q2",
    }]

    assert validar_definicion(out) == []


def test_parse_report_definition_defaults():
    xml = (
        '<ReportDefinition id="F" version="" firstquestion="Q1">'
        '<Question id="Q1" text="P">'
        '<Option id="O1" text="Op"/>'
        '</Question>'
        '</ReportDefinition>'
    )
    out = parse_report_definition(xml)
    q = out["preguntas"][0]
    assert q["selectionmodel"] == "single"
    assert q["hide"] is False
    o = q["opciones"][0]
    assert o["valuetype"] == ""
    assert o["inputmask"] == ""
    assert o["readonly"] is False
    assert o["nextquestion"] == "END"


def test_parse_report_definition_no_es_definition():
    with pytest.raises(ValueError):
        parse_report_definition("<Report id='F'/>")


# --- validar_definicion ------------------------------------------------------

def test_validar_nextquestion_inexistente():
    d = {
        "report_id": "FORMID", "version": "1", "firstquestion": "Q1",
        "preguntas": [
            {
                "question_id": "Q1", "texto": "", "selectionmodel": "single",
                "hide": False,
                "opciones": [{
                    "option_id": "O1", "texto": "", "valuetype": "text",
                    "inputmask": "", "readonly": False, "nextquestion": "Q99",
                }],
            },
        ],
    }
    errores = validar_definicion(d)
    assert any("nextquestion" in e for e in errores)


def test_validar_id_pregunta_duplicado():
    d = {
        "report_id": "FORMID", "version": "1", "firstquestion": "Q1",
        "preguntas": [
            {"question_id": "Q1", "texto": "", "selectionmodel": "single",
             "hide": False, "opciones": []},
            {"question_id": "Q1", "texto": "", "selectionmodel": "single",
             "hide": False, "opciones": []},
        ],
    }
    errores = validar_definicion(d)
    assert any("duplicado" in e for e in errores)


def test_validar_selectionmodel_con_funcion():
    d = {
        "report_id": "F", "version": "1", "firstquestion": "Q1",
        "preguntas": [
            {"question_id": "Q1", "texto": "", "selectionmodel": "max(value)",
             "hide": False, "opciones": []},
        ],
    }
    errores = validar_definicion(d)
    assert any("funcion" in e for e in errores)


def test_validar_id_opcion_duplicado():
    d = {
        "report_id": "F", "version": "1", "firstquestion": "Q1",
        "preguntas": [
            {
                "question_id": "Q1", "texto": "", "selectionmodel": "single",
                "hide": False,
                "opciones": [
                    {"option_id": "O1", "texto": "", "valuetype": "text",
                     "inputmask": "", "readonly": False, "nextquestion": "END"},
                    {"option_id": "O1", "texto": "", "valuetype": "text",
                     "inputmask": "", "readonly": False, "nextquestion": "END"},
                ],
            },
        ],
    }
    errores = validar_definicion(d)
    assert any("duplicado" in e for e in errores)
