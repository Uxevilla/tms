"""Cliente SOAP de Trimble FleetWorks (cacheado por credenciales del tenant)."""
from fastapi import HTTPException

import config
from db import _db, _tenant_ctx, _valores_proveedor
from soap_client import TrimbleClient

_client_cache = {}


def get_client():
    """Cliente SOAP del tenant actual (cacheado por credenciales Trimble).

    Lee SIEMPRE la config del proveedor 'trimble' de la BD actual (integracion_valores)
    y solo cae a los valores por defecto del .env si la BD no tiene credenciales. Antes,
    para el JWT del frontend React (token sin 'empresa'), el tenant quedaba en None y
    se usaba solo el .env, ignorando lo guardado en /configuración.
    """
    t = _tenant_ctx.get()
    if t and t.get("superadmin"):
        # BD maestra (sin schema): no hay tabla de integraciones; solo defaults del .env.
        cfg = {}
    else:
        with _db() as conn:
            cfg = _valores_proveedor(conn, "trimble")
    u = cfg.get("username", "") or config.DEFAULT_TRIMBLE_USERNAME or ""
    p = cfg.get("password", "") or config.DEFAULT_TRIMBLE_PASSWORD or ""
    c = cfg.get("customer", "") or config.DEFAULT_TRIMBLE_CUSTOMER or ""
    term = ""  # sin terminal por defecto: el terminal se pasa explícito por operación
    if not u or not c:
        raise HTTPException(status_code=503, detail={"error": "Trimble no configurado para este cliente"})
    # La clave incluye password y terminal: cambiar cualquiera invalida la caché.
    key = (u, p, c, term)
    if key not in _client_cache:
        _client_cache[key] = TrimbleClient(u, p, c, term)
    return _client_cache[key]
