"""Almacén de documentos en disco (saca el base64 de la BD).

Los binarios se guardan en `DOCS_DIR` con clave de contenido (SHA-256), de modo que
dos subidas idénticas comparten fichero. La BD solo guarda la referencia
(`storage_key` + `sha256` + `bytes` + `mime`), nunca el binario.
"""
import base64
import binascii
import hashlib
import os
import re

import config

DOCS_DIR = os.environ.get("DOCS_DIR", "/app/backend/data/docs")

_EXT = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "text/plain": ".txt",
    "application/octet-stream": ".bin",
}


def _decodificar(b64: str):
    """De un base64 (puro o data URI) devuelve (bytes, mime). Devuelve (None, '') si está vacío."""
    if not b64:
        return None, ""
    s = b64.strip()
    mime = ""
    m = re.match(r"^data:([a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+);base64,(.*)$", s, re.S)
    if m:
        mime = m.group(1).lower()
        s = m.group(2)
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None, ""
    return raw, mime


def _guardar_archivo(nombre: str, b64: str):
    """Escribe el base64 a disco. Devuelve (storage_key, sha256, bytes, mime) o None si vacío."""
    raw, mime = _decodificar(b64)
    if raw is None or not raw:
        return None
    sha = hashlib.sha256(raw).hexdigest()
    ext = _EXT.get(mime, "") or (os.path.splitext(nombre or "")[1] or ".bin")
    storage_key = f"{sha[:2]}/{sha}{ext}"
    ruta = os.path.join(DOCS_DIR, storage_key)
    if not os.path.exists(ruta):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.write(raw)
    return storage_key, sha, len(raw), mime


def _leer_archivo(storage_key: str) -> str:
    """Lee un fichero de disco y devuelve su base64 (o '' si no existe)."""
    if not storage_key:
        return ""
    ruta = os.path.join(DOCS_DIR, storage_key)
    if not os.path.exists(ruta):
        return ""
    with open(ruta, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _borrar_archivo(storage_key: str) -> None:
    """Borra un fichero de disco si existe (no falla si no está)."""
    if not storage_key:
        return
    ruta = os.path.join(DOCS_DIR, storage_key)
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass
