"""Módulo de conversiones de valores crudos de Trimble a tipos Python y texto legible en español."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any


def convertir_valor(
    value: str | None,
    valuetype: str,
    inputmask: str = "",
) -> tuple[Any, str]:
    """
    Convierte el valor crudo de un <Value value="..."> de Trimble a su valor tipado
    y su texto legible en español.

    Args:
        value: Valor crudo del XML (string o None)
        valuetype: Tipo de valor (text, number, numeric, date, time, boolean, checkbox, select)
        inputmask: Máscara de formato opcional (ej. "ES 0000 AAA")

    Returns:
        Tupla (valor_tipado, texto_legible)
    """
    if value is None or value == "":
        return (None, "")

    vt = (valuetype or "").lower().strip()

    # Text - sin transformación
    if vt == "text":
        typed = value
        readable = value

    # Number / Numeric - convertir a float/int, formatear con separador de miles es-ES
    elif vt in ("number", "numeric"):
        try:
            num = float(value)
            typed = int(num) if num.is_integer() else num
            readable = _formatear_numero_es(num)
        except ValueError:
            typed = value
            readable = value

    # Date - convertir a datetime.date, texto dd/MM/yyyy
    elif vt == "date":
        parsed_date = _parsear_fecha(value, inputmask)
        if parsed_date:
            typed = parsed_date
            readable = parsed_date.strftime("%d/%m/%Y")
        else:
            typed = value
            readable = value

    # Time - mantener como texto HH:mm
    elif vt == "time":
        typed = _normalizar_hora(value)
        readable = typed

    # Boolean / Checkbox - convertir a bool, texto Sí/No
    elif vt in ("boolean", "checkbox"):
        typed = _parsear_booleano(value)
        readable = "Sí" if typed else "No"

    # Select - mantener valor (option_id se resuelve en otra capa)
    elif vt == "select":
        typed = value
        readable = value

    # Valuetype desconocido - sin transformación
    else:
        typed = value
        readable = value

    # Aplicar inputmask SOLO si es una máscara 0/A (no un formato de fecha dd/MM/yyyy).
    if inputmask and readable and any(c in inputmask for c in "0A"):
        readable = _aplicar_mascara(readable, inputmask)

    return (typed, readable)


def _formatear_numero_es(num: float) -> str:
    """Formatea un número con separador de miles es-ES (1.234,5)."""
    if num.is_integer():
        return f"{int(num):,}".replace(",", ".")
    s = f"{num:.2f}".rstrip("0").rstrip(".")   # quita ceros finales: 1.50 → 1.5
    ent, dec = s.split(".") if "." in s else (s, "")
    ent = f"{int(ent):,}".replace(",", ".")
    return f"{ent},{dec}" if dec else ent


def _parsear_fecha(value: str, inputmask: str = "") -> date | None:
    """
    Parsea una fecha aceptando ISO (YYYY-MM-DD) o formato dd/MM/yyyy.
    Si inputmask indica dd/MM/yyyy, prioriza ese formato.
    """
    value = value.strip()

    # Detectar formato por inputmask
    mask_lower = inputmask.lower()
    if "dd" in mask_lower and "mm" in mask_lower and "yyyy" in mask_lower:
        # Formato dd/MM/yyyy
        match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", value)
        if match:
            d, m, y = map(int, match.groups())
            try:
                return date(y, m, d)
            except ValueError:
                return None

    # Intentar ISO YYYY-MM-DD
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", value)
    if match:
        y, m, d = map(int, match.groups())
        try:
            return date(y, m, d)
        except ValueError:
            return None

    # Intentar dd/MM/yyyy sin inputmask
    match = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", value)
    if match:
        d, m, y = map(int, match.groups())
        try:
            return date(y, m, d)
        except ValueError:
            return None

    # Intentar YYYYMMDD
    match = re.match(r"^(\d{4})(\d{2})(\d{2})$", value)
    if match:
        y, m, d = map(int, match.groups())
        try:
            return date(y, m, d)
        except ValueError:
            return None

    return None


def _normalizar_hora(value: str) -> str:
    """Normaliza hora a formato HH:mm (acepta HH:mm o HH:mm:ss)."""
    value = value.strip()
    # HH:mm:ss -> HH:mm
    match = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})$", value)
    if match:
        h, m, _ = match.groups()
        return f"{int(h):02d}:{m}"
    # HH:mm
    match = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if match:
        h, m = match.groups()
        return f"{int(h):02d}:{m}"
    return value


def _parsear_booleano(value: str) -> bool:
    """Parsea un valor a booleano (true/1/yes/on/si -> True)."""
    return value.strip().lower() in ("true", "1", "yes", "on", "si", "sí")


def _aplicar_mascara(texto: str, mascara: str) -> str:
    """
    Aplica una máscara simple al texto.
    0 = dígito, A = letra, resto = literal.
    Si no cuadra, devuelve el texto sin máscara.
    """
    # Filtrar solo caracteres alfanuméricos del texto para rellenar la máscara
    chars = [c for c in texto if c.isalnum()]
    char_idx = 0
    resultado = []

    for c in mascara:
        if c == "0":
            if char_idx < len(chars) and chars[char_idx].isdigit():
                resultado.append(chars[char_idx])
                char_idx += 1
            else:
                # No cuadra - devolver texto original
                return texto
        elif c == "A":
            if char_idx < len(chars) and chars[char_idx].isalpha():
                resultado.append(chars[char_idx])
                char_idx += 1
            else:
                # No cuadra - devolver texto original
                return texto
        else:
            # Carácter literal
            resultado.append(c)

    # Si sobran caracteres, añadir al final
    while char_idx < len(chars):
        resultado.append(chars[char_idx])
        char_idx += 1

    return "".join(resultado)