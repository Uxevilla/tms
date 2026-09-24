"""Cifrado simétrico de valores sensibles (Fernet) con clave propia de cifrado.

`integracion_valores.valor` guarda credenciales (contraseñas, API keys). Se cifran en
reposo con Fernet. La clave de cifrado es INDEPENDIENTE de `TMS_SECRET_KEY` (que firma
tokens y puede rotarse sin romper el descifrado): se usa `TMS_ENCRYPTION_KEY` si está
definida; si no, se deriva de `TMS_SECRET_KEY` (retrocompatibilidad). Los fallos de
descifrado se registran para no fallar en silencio.
"""
import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

import config

log = logging.getLogger("crypto")

_fernet_cache = None


def _fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is None:
        raw = config.ENCRYPTION_KEY or config.SECRET_KEY
        if config.ENCRYPTION_KEY:
            key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        else:
            # Retrocompatibilidad: clave derivada de TMS_SECRET_KEY (no rotar sin migrar).
            key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        _fernet_cache = Fernet(key)
    return _fernet_cache


def _encrypt_valor(val: str) -> str:
    """Cifra un valor. Devuelve '' si está vacío."""
    if not val:
        return ""
    return _fernet().encrypt(val.encode()).decode()


def _decrypt_valor(val: str) -> str:
    """Descifra un valor. Si no se puede (legacy en claro o clave distinta) lo devuelve
    tal cual, pero registra el fallo para que no pase desapercibido."""
    if not val:
        return ""
    try:
        return _fernet().decrypt(val.encode()).decode()
    except InvalidToken:
        log.error("crypto: no se pudo descifrar un valor (¿clave TMS_ENCRYPTION_KEY cambiada?)")
        return val
    except Exception:
        return val
