"""Worker de ingesta: trazas/archivos y mensajería en paralelo."""
import asyncio

from services.sync import _sync_files, _sync_mensajes


async def run():
    """Bucle de ingesta asíncrono: trazas/archivos y mensajería EN PARALELO.

    Los mensajes se sondean cada 2s en un bucle propio para que el chat no sufra
    el delay del procesado de trazas (que corre cada 5s en otro bucle).
    """

    async def _loop_files():
        while True:
            try:
                await asyncio.to_thread(_sync_files)
            except Exception as e:
                print(f"[ingesta] error _sync_files: {e}")
            await asyncio.sleep(5)

    async def _loop_mensajes():
        while True:
            try:
                await asyncio.to_thread(_sync_mensajes)
            except Exception as e:
                print(f"[ingesta] error _sync_mensajes: {e}")
            await asyncio.sleep(2)

    await asyncio.gather(_loop_files(), _loop_mensajes())
