"""Worker de ingesta: trazas/archivos y mensajería en paralelo, por tenant."""
import asyncio

from db import _tenant_ctx
from tenancy import _empresas
from services.sync import _sync_files, _sync_mensajes


def _por_tenant(fn):
    """Ejecuta `fn` (síncrona) una vez por tenant activo, con su contexto."""
    for emp in _empresas():
        tok = _tenant_ctx.set({"db_name": emp["db_name"], "empresa": emp["slug"], "superadmin": False})
        try:
            fn()
        finally:
            _tenant_ctx.reset(tok)


async def run():
    """Bucle de ingesta asíncrono: trazas/archivos y mensajería EN PARALELO.

    Los mensajes se sondean cada 2s en un bucle propio para que el chat no sufra
    el delay del procesado de trazas (que corre cada 5s en otro bucle).
    """

    async def _loop_files():
        while True:
            try:
                await asyncio.to_thread(_por_tenant, _sync_files)
            except Exception as e:
                print(f"[ingesta] error _sync_files: {e}")
            await asyncio.sleep(5)

    async def _loop_mensajes():
        while True:
            try:
                await asyncio.to_thread(_por_tenant, _sync_mensajes)
            except Exception as e:
                print(f"[ingesta] error _sync_mensajes: {e}")
            await asyncio.sleep(2)

    await asyncio.gather(_loop_files(), _loop_mensajes())
