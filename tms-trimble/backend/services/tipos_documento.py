"""Clasificación de adjuntos de Trimble (TMS).

Clasifica los ficheros descargados (cuestionarios de actividad y mensajes)
en un tipo de documento legible para el checklist de facturación.

Funciones PURAS: sin _db, sin imports del proyecto, sin BD ni red.
Dependencias: stdlib únicamente.
"""

import unicodedata


def clasificar_documento(ftype, name, report_id=""):
    """Devuelve UNO de: "CMR", "carta_porte", "albaran", "ticket", "firma",
    "escaner", "tacografo", "otro".
    """
    n = (name or "").lower()

    if "cmr" in n:
        return "CMR"
    if "carta" in n or "porte" in n:
        return "carta_porte"
    if "albaran" in n or "delivery" in n or "deliverynote" in n:
        return "albaran"
    if "ticket" in n or "gasto" in n or "fuel" in n:
        return "ticket"
    if "firma" in n or "signature" in n or "_sign" in n or "sign." in n:
        return "firma"
    if "scan" in n or "escaner" in n or "multipage" in n or "multi_page" in n:
        return "escaner"
    if ftype in (0, 1, 2):
        return "tacografo"
    return "otro"


def docs_requeridos_default():
    """Documentos exigidos por defecto para el checklist de facturación."""
    return ["CMR", "carta_porte"]


def _normalizar(s):
    """Minúsculas, sin tildes (NFKD) y sin guiones/espacios repetidos."""
    s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("-", " ")
    s = " ".join(s.split())
    return s


def checklist_facturacion(tipos_presentes, tipos_requeridos):
    """Devuelve {"ok": bool, "presentes": [...], "faltan": [...]}.

    - "faltan": tipos_requeridos que NO están en tipos_presentes (orden preservado, valores originales).
    - "ok": True si no falta ninguno.
    Comparación normalizada (minúsculas, sin tildes, sin guiones/espacios repetidos).
    """
    presentes_norm = {_normalizar(t) for t in tipos_presentes}
    faltan = [t for t in tipos_requeridos if _normalizar(t) not in presentes_norm]

    return {
        "ok": not faltan,
        "presentes": list(tipos_presentes),
        "faltan": faltan,
    }


def clasificar_con_mapeo(ftype, name, tipo_mapeado):
    """tipo_mapeado (de cfg_tipo_documento_pregunta) manda; si no hay, cae al nombre/ftype."""
    if tipo_mapeado:
        return tipo_mapeado
    return clasificar_documento(ftype, name)


def sugerir_tipo_documento(inputmask, texto_pregunta):
    """Sugerencia de tipo_documento desde el inputmask de la definición + texto de la pregunta.

    inputmask ∈ {docuscan, multidocuscan, picturescan, photo, signature, doc-edit, annotate, signoff…}.
    Devuelve un tipo (CMR/carta_porte/albaran/ticket/firma/escaner) o '' si no hay pista.
    """
    m = (inputmask or "").lower()
    t = _normalizar(texto_pregunta or "")
    if m in ("signature", "signoff"):
        return "firma"
    if m in ("docuscan", "multidocuscan", "picturescan", "photo", "doc-edit", "annotate", "special"):
        if "cmr" in t:
            return "CMR"
        if "carta" in t or "porte" in t:
            return "carta_porte"
        if "albaran" in t or "delivery" in t:
            return "albaran"
        if "ticket" in t or "gasto" in t or "fuel" in t:
            return "ticket"
        return "escaner"
    return ""


def checklist_documentacion(conn, cliente_id, presentes):
    """Checklist de facturación unificado (PR 0.4 + A3.6): docs requeridos vs presentes.

    Excluye siempre 'tacografo' y 'otro' de los presentes. Único sitio donde se
    resuelve la lista de requeridos (cfg_docs_requeridos por cliente o default).
    """
    requeridos = None
    if cliente_id:
        req = conn.execute(
            "SELECT tipo_documento FROM cfg_docs_requeridos "
            "WHERE cliente_id=? AND requerido ORDER BY orden", (cliente_id,),
        ).fetchall()
        requeridos = [r["tipo_documento"] for r in req]
    if not requeridos:
        requeridos = docs_requeridos_default()
    presentes_filtrados = [t for t in presentes if t and _normalizar(t) not in ("tacografo", "otro")]
    return checklist_facturacion(presentes_filtrados, requeridos)
