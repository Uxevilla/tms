"""Importador y traducción de question paths (ReportDefinition) de Trimble.

PR 2 — Fase 5b: las definiciones importadas (ReportDefinition) permiten
traducir las respuestas crudas (QID/OID) a texto legible y validar los reportes.
"""

from db import _db
from services.cuestionarios import parse_report_definition, validar_definicion


def importar_definicion(xml_str, importado_por=""):
    """Parsea + valida + guarda una <ReportDefinition> (idempotente por report_id).

    Devuelve {"ok": True, "definicion_id", "preguntas", "report_id", "version"}
    o {"ok": False, "errores": [...]}.
    """
    try:
        d = parse_report_definition(xml_str)
    except ValueError as exc:
        return {"ok": False, "errores": [str(exc)]}

    errores = validar_definicion(d)
    if errores:
        return {"ok": False, "errores": errores}

    report_id = d["report_id"]
    version = d["version"]

    with _db() as conn:
        conn.set_autocommit(True)
        # Sustituye la definición anterior del mismo report_id (idempotente).
        prev = conn.execute(
            "SELECT id FROM qp_definiciones WHERE report_id=? ORDER BY version DESC LIMIT 1",
            (report_id,),
        ).fetchone()
        if prev:
            conn.execute("DELETE FROM qp_definiciones WHERE id=?", (prev["id"],))  # cascada

        defid = conn.execute(
            "INSERT INTO qp_definiciones (report_id, version, firstquestion, xml_original, importado_por) "
            "VALUES (?,?,?,?,?) RETURNING id",
            (report_id, version, d["firstquestion"], xml_str, importado_por),
        ).fetchone()["id"]

        for qi, q in enumerate(d["preguntas"]):
            pid = conn.execute(
                "INSERT INTO qp_preguntas (definicion_id, question_id, texto, selectionmodel, hide, orden) "
                "VALUES (?,?,?,?,?,?) RETURNING id",
                (defid, q["question_id"], q["texto"], q["selectionmodel"], q["hide"], qi),
            ).fetchone()["id"]
            for oi, o in enumerate(q["opciones"]):
                conn.execute(
                    "INSERT INTO qp_opciones (pregunta_id, option_id, texto, valuetype, inputmask, readonly, nextquestion, orden) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (pid, o["option_id"], o["texto"], o["valuetype"], o["inputmask"],
                     o["readonly"], o["nextquestion"], oi),
                )

    return {"ok": True, "definicion_id": defid, "preguntas": len(d["preguntas"]),
            "report_id": report_id, "version": version}


def listar_definiciones(conn):
    """Lista las definiciones importadas con su conteo de preguntas."""
    rows = conn.execute(
        "SELECT d.id, d.report_id, d.version, d.firstquestion, d.activa, d.importado_en, "
        "(SELECT count(*) FROM qp_preguntas p WHERE p.definicion_id=d.id) AS n_preguntas "
        "FROM qp_definiciones d ORDER BY d.report_id, d.version DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def _respuestas_lista(v):
    """Normaliza respuestas (JSONB → string en psycopg2) a lista de dicts."""
    if not v:
        return []
    if isinstance(v, str):
        import json as _json
        try:
            return _json.loads(v)
        except (ValueError, TypeError):
            return []
    if isinstance(v, list):
        return v
    return []


def _traducir_respuestas(conn, report_id, respuestas):
    """Mapea respuestas crudas (QID/OID) a texto legible usando la definición activa.

    Devuelve [{"question", "pregunta", "option", "opcion", "value"}, ...].
    Sin definición importada devuelve las crudas (pregunta=question_id, opcion=option).
    """
    if not respuestas:
        return []
    defid = conn.execute(
        "SELECT id FROM qp_definiciones WHERE report_id=? AND activa ORDER BY version DESC LIMIT 1",
        (report_id,),
    ).fetchone()
    if not defid:
        return [
            {"question": r.get("question"), "pregunta": r.get("question"),
             "option": r.get("option"), "opcion": r.get("option") or "",
             "value": r.get("value", "")}
            for r in respuestas
        ]
    out = []
    for r in respuestas:
        q = conn.execute(
            "SELECT texto FROM qp_preguntas WHERE definicion_id=? AND question_id=?",
            (defid["id"], r.get("question")),
        ).fetchone()
        o = None
        if r.get("option"):
            o = conn.execute(
                "SELECT op.texto FROM qp_opciones op "
                "JOIN qp_preguntas p ON op.pregunta_id=p.id "
                "WHERE p.definicion_id=? AND p.question_id=? AND op.option_id=?",
                (defid["id"], r.get("question"), r.get("option")),
            ).fetchone()
        out.append({
            "question": r.get("question"),
            "pregunta": q["texto"] if q else r.get("question"),
            "option": r.get("option"),
            "opcion": o["texto"] if o else (r.get("option") or ""),
            "value": r.get("value", ""),
        })
    return out
