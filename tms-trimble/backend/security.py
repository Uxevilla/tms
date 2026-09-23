"""Seguridad: JWT, bcrypt, dependencias de rol y rate limiting de login.

Hoja del proyecto: importa `config` y `fastapi`, no depende de main ni de services.
"""
import os
import time
import threading

import jwt
from fastapi import Depends, Header, HTTPException
from passlib.context import CryptContext

import config

# Hash de contraseñas (bcrypt vía passlib).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

DEFAULT_ADMIN_USER = os.environ.get("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "").strip()

if not config.SECRET_KEY or len(config.SECRET_KEY) < 32:
    raise RuntimeError("TMS_SECRET_KEY no definido o < 32 caracteres; rechazo el arranque para no emitir JWT falsificables.")
_JWT_KEY = config.SECRET_KEY


def _hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        return False


def _make_jwt(user_id, usuario: str, rol: str, empresa: str = "", ttl: int = 12 * 3600) -> str:
    payload = {"sub": str(user_id), "rol": rol, "usuario": usuario, "exp": int(time.time()) + ttl}
    if empresa:
        payload["empresa"] = empresa
    return jwt.encode(payload, _JWT_KEY, algorithm="HS256")


# Rate limiting de login (anti fuerza bruta, en memoria por proceso).
_login_attempts = {}
_login_lock = threading.Lock()
_LOGIN_MAX_INTENTOS = 8
_LOGIN_VENTANA_S = 300


def _login_rate_ok(key: str) -> bool:
    now = time.time()
    with _login_lock:
        times = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_VENTANA_S]
        if len(times) >= _LOGIN_MAX_INTENTOS:
            return False
        times.append(now)
        _login_attempts[key] = times
        return True


def _verify_jwt(token: str):
    try:
        return jwt.decode(token, _JWT_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def require_jwt(authorization: str = Header(default="")):
    """Dependencia: valida el JWT de la cabecera Authorization: Bearer."""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    payload = _verify_jwt(token) if token else None
    if not payload:
        raise HTTPException(status_code=401, detail={"error": "No autenticado"})
    return payload


def require_role(required_roles):
    """Factoría de dependencias: exige que el rol del JWT esté en `required_roles`.

    Uso: `def ruta(user: dict = Depends(require_role(["admin"]))): ...`
    Devuelve 403 si el rol no tiene permisos suficientes.
    """
    def _check(payload: dict = Depends(require_jwt)):
        if payload.get("rol") not in required_roles:
            raise HTTPException(status_code=403, detail={"error": "Permisos insuficientes"})
        return payload
    return _check
