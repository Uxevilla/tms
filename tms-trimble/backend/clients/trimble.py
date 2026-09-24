"""Cliente SOAP de Trimble FleetWorks (cacheado por credenciales del tenant)."""
from fastapi import HTTPException

import config
from db import _db, _tenant_ctx, _valores_proveedor
from soap_client import TrimbleClient

_client_cache = {}


def get_client():
    """Cliente SOAP del tenant actual (cacheado por credenciales Trimble)."""
    t = _tenant_ctx.get()
    if not t:
        u = config.DEFAULT_TRIMBLE_USERNAME
        p = config.DEFAULT_TRIMBLE_PASSWORD
        c = config.DEFAULT_TRIMBLE_CUSTOMER
        term = config.DEFAULT_TRIMBLE_TERMINAL
    else:
        with _db() as conn:
            cfg = _valores_proveedor(conn, "trimble")
        u = cfg.get("username", "") or ""
        p = cfg.get("password", "") or ""
        c = cfg.get("customer", "") or ""
        term = cfg.get("terminal", "") or ""
        if not u or not c:
            raise HTTPException(status_code=503, detail={"error": "Trimble no configurado para este cliente"})
    key = (u, c)
    if key not in _client_cache:
        _client_cache[key] = TrimbleClient(u, p, c, term)
    return _client_cache[key]
