"""Cliente SOAP de Trimble FleetWorks (cacheado por credenciales del tenant)."""
from fastapi import HTTPException

import config
from db import _db, _tenant_ctx, _valores_proveedor
from soap_client import TrimbleClient, _trimble_fake

_client_cache = {}


def get_client():
    """Cliente SOAP del tenant actual (cacheado por credenciales Trimble).

    Lee SIEMPRE la config del proveedor 'trimble' de la BD actual (integracion_valores)
    y solo cae a los valores por defecto del .env si la BD no tiene credenciales. Antes,
    para el JWT del frontend React (token sin 'empresa'), el tenant quedaba en None y
    se usaba solo el .env, ignorando lo guardado en /configuración.
    """
    t = _tenant_ctx.get()
    # Modo fake (CI/e2e): sin credenciales ni red.
    if _trimble_fake():
        return TrimbleClient("fake", "fake", "fake", "")
    if t and t.get("superadmin"):
        # BD maestra (sin schema): no hay tabla de integraciones; solo defaults del .env.
        cfg = {}
    else:
        with _db() as conn:
            cfg = _valores_proveedor(conn, "trimble")
    u = cfg.get("username", "") or ""
    p = cfg.get("password", "") or ""
    c = cfg.get("customer", "") or ""
    # El .env solo como fallback en desarrollo; en producción, sin credenciales → 503.
    if (config.TMS_ENV or "").lower() in ("dev", "development", "local"):
        u = u or config.DEFAULT_TRIMBLE_USERNAME or ""
        p = p or config.DEFAULT_TRIMBLE_PASSWORD or ""
        c = c or config.DEFAULT_TRIMBLE_CUSTOMER or ""
    term = cfg.get("terminal", "") or config.DEFAULT_TRIMBLE_TERMINAL or ""
    if not u or not c:
        raise HTTPException(status_code=503, detail={"error": "Trimble no configurado para este cliente"})
    # La clave incluye password y terminal: cambiar cualquiera invalida la caché.
    key = (u, p, c, term)
    if key not in _client_cache:
        _client_cache[key] = TrimbleClient(u, p, c, term)
    return _client_cache[key]
