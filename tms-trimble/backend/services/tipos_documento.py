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
