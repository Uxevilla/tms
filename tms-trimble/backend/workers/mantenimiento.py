"""Worker de mantenimiento predictivo (revisión periódica)."""
import asyncio
import os

from services.mantenimiento import _revisar_mantenimiento

MANTENIMIENTO_INTERVALO = int(os.environ.get("MANTENIMIENTO_INTERVALO", "3600"))  # segundos


async def run():
    """Bucle de revisión de mantenimiento (cada MANTENIMIENTO_INTERVALO segundos)."""
    while True:
        try:
            await asyncio.to_thread(_revisar_mantenimiento)
        except Exception as e:
            print(f"[mantenimiento] error: {e}")
        await asyncio.sleep(MANTENIMIENTO_INTERVALO)
