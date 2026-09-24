"""Cifrado simétrico de valores sensibles (Fernet) con clave derivada de TMS_SECRET_KEY.

`integracion_valores.valor` guarda credenciales (contraseñas, API keys). Se cifran en
reposo con Fernet; la clave se deriva de `config.SECRET_KEY` (SHA-256 -> base64url), de
modo que robar la BD no expone los secretos. En memoria/tránsito los clientes trabajan
con el valor en claro, pero la API de administración los enmascara.
"""
import base64
import hashlib

from cryptography.fernet import Fernet

import config


def _fernet() -> Fernet:
    key = hashlib.sha256(config.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt_valor(val: str) -> str:
    """Cifra un valor. Devuelve '' si está vacío."""
    if not val:
        return ""
    return _fernet().encrypt(val.encode()).decode()


def _decrypt_valor(val: str) -> str:
    """Descifra un valor. Si no se puede (legacy en claro) lo devuelve tal cual."""
    if not val:
        return ""
    try:
        return _fernet().decrypt(val.encode()).decode()
    except Exception:
        return val
