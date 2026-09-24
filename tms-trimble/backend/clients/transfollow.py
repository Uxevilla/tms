"""Cliente REST de TransFollow e-CMR (cacheado por API key del tenant)."""
from fastapi import HTTPException

import config
from db import _db, _valores_proveedor
from transfollow_client import TransFollowClient, PROD_BASE_URL

_tf_cache = {}


def get_transfollow_client():
    """Cliente REST de TransFollow del tenant actual (cacheado)."""
    with _db() as conn:
        cfg = _valores_proveedor(conn, "transfollow")
    api_key = cfg.get("api_key", "") or ""
    if not api_key:
        raise HTTPException(status_code=503, detail={"error": "TransFollow no configurado para este cliente"})
    base_url = cfg.get("base_url", "") or PROD_BASE_URL
    if api_key not in _tf_cache:
        _tf_cache[api_key] = TransFollowClient(api_key, base_url)
    return _tf_cache[api_key]
