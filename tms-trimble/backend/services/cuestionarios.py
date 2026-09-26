"""Parser de reportes (question paths) de Trimble.

Extrae los datos CRUDOS de los XML <Report> y <ReportDefinition> sin
interpretar su significado (esa tarea corresponde a otra capa).

Dependencias: stdlib (html, re, xml.etree.ElementTree). No usa defusedxml para
evitar una dependencia nueva; el XML viene de Trimble y el tamaño es acotado.
"""

import html
import re
import xml.etree.ElementTree as ET

_CDATA_RE = re.compile(r"<!\[CDATA\[(.*)\]\]>", re.DOTALL)
_ALNUM_RE = re.compile(r"[A-Za-z0-9]+\Z")


def _clean_xml(xml_str):
    """Limpia el envoltorio CDATA y las entidades HTML antes de parsear."""
    s = xml_str.strip()
    m = _CDATA_RE.match(s)
    if m:
        s = m.group(1).strip()
    s = html.unescape(s).strip()
    # Doble/triple escape (&amp;lt;Report …): deshacer capas hasta ver el root.
    for _ in range(3):
        if s.lstrip().startswith("<"):
            break
        prev = s
        s = html.unescape(s).strip()
        if s == prev:
            break
    return s


def _to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "on")


def _es_alfanumerico(s):
    return bool(_ALNUM_RE.match(s))


def _parse_root(xml_str, root_tag):
    s = _clean_xml(xml_str)
    try:
        root = ET.fromstring(s)
    except Exception as exc:
        raise ValueError(f"XML inválido: {exc}") from exc
    if root.tag != root_tag:
        raise ValueError(f"El XML no es un <{root_tag}> válido (recibido: {root.tag!r})")
    return root


def parse_report(xml_str):
    """Parsea un <Report> con respuestas.

    Entrada: XML que puede venir tal cual, envuelto en <![CDATA[ ... ]]> o con
    entidades HTML (&lt;Report ...&gt;), incluso doblemente escapado.

    Salida exacta:
      {"report_id": str, "version": str, "timestamp": str,
       "respuestas": [{"question": str, "option": str|None, "value": str}, ...]}
    """
    root = _parse_root(xml_str, "Report")

    respuestas = []
    for answer in root.findall("Answer"):
        question = answer.get("question", "")
        for value in answer.findall("Value"):
            respuestas.append({
                "question": question,
                "option": value.get("option"),      # None si falta
                "value": value.get("value", ""),    # "" si falta
            })

    return {
        "report_id": root.get("id", ""),
        "version": root.get("version", ""),
        "timestamp": root.get("timestamp", ""),
        "respuestas": respuestas,
    }


def parse_report_definition(xml_str):
    """Parsea un <ReportDefinition> con sus preguntas y opciones.

    Salida exacta:
      {"report_id": str, "version": str, "firstquestion": str,
       "preguntas": [{"question_id": str, "texto": str, "selectionmodel": str,
                      "hide": bool,
                      "opciones": [{"option_id": str, "texto": str,
                                    "valuetype": str, "inputmask": str,
                                    "readonly": bool, "nextquestion": str}, ...]},
                     ...]}
    """
    root = _parse_root(xml_str, "ReportDefinition")

    preguntas = []
    for q in root.findall("Question"):
        opciones = []
        for o in q.findall("Option"):
            opciones.append({
                "option_id": o.get("id", ""),
                "texto": o.get("text", ""),
                "valuetype": o.get("valuetype", ""),
                "inputmask": o.get("inputmask", ""),
                "readonly": _to_bool(o.get("readonly", "false")),
                "nextquestion": o.get("nextquestion", "END"),
            })

        preguntas.append({
            "question_id": q.get("id", ""),
            "texto": q.get("text", ""),
            "selectionmodel": q.get("selectionmodel", "single"),
            "hide": _to_bool(q.get("hide", "false")),
            "opciones": opciones,
        })

    return {
        "report_id": root.get("id", ""),
        "version": root.get("version", ""),
        "firstquestion": root.get("firstquestion", ""),
        "preguntas": preguntas,
    }


def validar_definicion(d):
    """Devuelve una lista de errores (vacía si la definición es válida)."""
    errores = []

    report_id = d.get("report_id", "")
    version = d.get("version", "")
    firstquestion = d.get("firstquestion", "")
    preguntas = d.get("preguntas", [])

    # report_id / version alfanuméricos (version vacía es válida).
    if not report_id or not _es_alfanumerico(report_id):
        errores.append(f"report_id no alfanumérico: {report_id!r}")
    if version and not _es_alfanumerico(version):
        errores.append(f"version no alfanumérica: {version!r}")

    qids = [q.get("question_id", "") for q in preguntas]

    # firstquestion debe existir entre las preguntas.
    if firstquestion not in qids:
        errores.append(f"firstquestion no existe entre las preguntas: {firstquestion!r}")

    # ids de pregunta únicos.
    if len(qids) != len(set(qids)):
        errores.append("ids de pregunta duplicados")

    for q in preguntas:
        qid = q.get("question_id", "")

        # selectionmodel con funciones (contiene '(' o '#').
        sm = q.get("selectionmodel", "") or ""
        if "(" in sm or "#" in sm:
            errores.append(f"selectionmodel con funcion no soportado ({qid}): {sm!r}")

        # nextquestion de pregunta (si estuviera presente).
        q_next = q.get("nextquestion")
        if q_next is not None and q_next != "END" and q_next not in qids:
            errores.append(f"nextquestion de pregunta {qid!r} apunta a un id inexistente: {q_next!r}")

        opciones = q.get("opciones", [])
        oids = [o.get("option_id", "") for o in opciones]

        # ids de opción únicos dentro de cada pregunta.
        if len(oids) != len(set(oids)):
            errores.append(f"ids de opcion duplicados en la pregunta {qid!r}")

        # nextquestion de opción.
        for o in opciones:
            o_next = o.get("nextquestion", "END")
            if o_next != "END" and o_next not in qids:
                errores.append(
                    f"nextquestion de opcion {o.get('option_id', '')!r} "
                    f"apunta a un id inexistente: {o_next!r}"
                )

    return errores
