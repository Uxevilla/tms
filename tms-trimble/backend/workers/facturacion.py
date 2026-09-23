"""Worker de facturación automática (listener del canal de operaciones)."""
import asyncio
import json

import redis.asyncio as redis_asyncio

from config import REDIS_URL, REDIS_CHANNEL
from services.contabilidad import _facturar_viaje


async def run():
    """Escucha canal_operaciones y factura automáticamente los viajes entregados."""
    while True:
        pubsub = None
        r = None
        try:
            r = redis_asyncio.from_url(REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe(REDIS_CHANNEL)
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if not msg or msg.get("type") != "message":
                    continue
                try:
                    ev = json.loads(msg.get("data") or "{}")
                except (ValueError, TypeError):
                    continue
                if ev.get("tipo") == "estado" and ev.get("estado") == "Entregado" and ev.get("id"):
                    await asyncio.to_thread(_facturar_viaje, ev["id"])
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[facturacion] error de suscripción: {e}")
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(REDIS_CHANNEL)
                    await pubsub.aclose()
                except Exception:
                    pass
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass
        await asyncio.sleep(5)  # reintentar la suscripción si Redis cayó
