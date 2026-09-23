#!/usr/bin/env python3
"""
Worker de ingesta telemática (TMS).

Soporta dos brokers intercambiables mediante la variable BROKER:
  * redis    -> Redis Streams (grupo de consumidores, XREADGROUP)
  * rabbitmq -> RabbitMQ (cola durable + confirmación manual)

Flujo común (independiente del broker):
  1. Lee una posición.
  2. Consulta la caché Redis `vehiculo:{id}:viaje_activo` e inyecta el viaje_id.
  3. Acumula en memoria y hace batch insert en la hypertable
     `telemetria.posiciones_gps` de TimescaleDB.

Semántica de entrega: at-least-once (se confirma el mensaje solo tras un
insert con éxito). La clave única (vehiculo_id, time) + ON CONFLICT DO NOTHING
hace el redelivery idempotente.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg
import redis.asyncio as redis

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("ingest")

# Orden de columnas para el INSERT (debe coincidir con la tabla).
COLUMNS = (
    "time", "vehiculo_id", "viaje_id", "lat", "lng",
    "speed_kmh", "heading", "odometer_km", "ignicion", "fuente",
)

INSERT_SQL = (
    "INSERT INTO telemetria.posiciones_gps "
    "(time, vehiculo_id, viaje_id, lat, lng, speed_kmh, heading, odometer_km, ignicion, fuente) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
    "ON CONFLICT (vehiculo_id, time) DO NOTHING"
)


@dataclass
class Settings:
    broker: str
    pg_dsn: str
    redis_url: str
    stream_key: str
    consumer_group: str
    consumer_name: str
    dead_stream: str
    rabbitmq_url: str
    rabbitmq_queue: str
    rabbitmq_dead_queue: str
    batch_size: int
    flush_interval: float
    block_ms: int

    @classmethod
    def from_env(cls) -> "Settings":
        # DSN explícito o, por defecto, las mismas variables DB_* que usa el backend TMS
        # (permite compartir el .env del TMS vía env_file y apuntar al mismo timescaledb).
        pg_dsn = os.environ.get("PG_DSN") or (
            f"postgresql://{os.environ.get('DB_USER', 'tms')}:{os.environ.get('DB_PASSWORD', '')}"
            f"@{os.environ.get('DB_HOST', 'timescaledb')}:{os.environ.get('DB_PORT', '5432')}"
            f"/{os.environ.get('DB_NAME', 'tms')}"
        )
        return cls(
            broker=os.environ.get("BROKER", "redis"),
            pg_dsn=pg_dsn,
            redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            stream_key=os.environ.get("STREAM_KEY", "telemetria:ingesta"),
            consumer_group=os.environ.get("CONSUMER_GROUP", "workers"),
            consumer_name=os.environ.get("CONSUMER_NAME", f"worker-{os.getpid()}"),
            dead_stream=os.environ.get("DEAD_STREAM", "telemetria:ingesta:dead"),
            rabbitmq_url=os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/"),
            rabbitmq_queue=os.environ.get("RABBITMQ_QUEUE", "telemetria.ingesta"),
            rabbitmq_dead_queue=os.environ.get("RABBITMQ_DEAD_QUEUE", "telemetria.ingesta.dead"),
            batch_size=int(os.environ.get("BATCH_SIZE", "500")),
            flush_interval=float(os.environ.get("FLUSH_INTERVAL", "2.0")),
            block_ms=int(os.environ.get("BLOCK_MS", "5000")),
        )


# ----------------------------------------------------------------------
# Parseo y validación (compartido)
# ----------------------------------------------------------------------

def parse_ts(value: str) -> datetime:
    """ISO-8601 -> datetime con timezone; si viene sin tz se asume UTC."""
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_position(payload: bytes) -> Optional[dict[str, Any]]:
    """Valida y normaliza un mensaje de posición. Devuelve dict o None."""
    try:
        p = json.loads(payload)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        log.warning("mensaje no-JSON descartado: %.120r", payload)
        return None

    if not isinstance(p, dict) or not all(k in p for k in ("vehiculo_id", "time", "lat", "lng")):
        log.warning("posición sin campos obligatorios: %s", p)
        return None

    try:
        p["time"] = parse_ts(str(p["time"]))
        p["vehiculo_id"] = str(p["vehiculo_id"])
        p["lat"] = float(p["lat"])
        p["lng"] = float(p["lng"])
    except (ValueError, TypeError, KeyError):
        log.warning("tipos inválidos en posición: %.120r", payload)
        return None

    if not (-90.0 <= p["lat"] <= 90.0 and -180.0 <= p["lng"] <= 180.0):
        log.warning("coordenadas fuera de rango: lat=%s lng=%s", p["lat"], p["lng"])
        return None

    return p


def trip_key(vehiculo_id: str) -> str:
    return f"vehiculo:{vehiculo_id}:viaje_activo"


async def lookup_active_trip(r: redis.Redis, vehiculo_id: str) -> Optional[str]:
    """Devuelve el viaje_id activo del vehículo (TEXT), o None si no hay viaje en curso."""
    raw = await r.get(trip_key(vehiculo_id))
    if raw is None:
        return None
    return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)


async def enrich(r: redis.Redis, payload: bytes) -> tuple[Optional[tuple], Optional[bytes], Optional[str]]:
    """Normaliza un mensaje y lo enriquece con el viaje_id.

    Devuelve (row, bad_payload, motivo):
      * row = tupla lista para el INSERT, o None si no insertable.
      * bad_payload/motivo = rellenados cuando el mensaje va a dead-letter.
    """
    pos = parse_position(payload)
    if pos is None:
        return None, payload, "invalid"

    viaje_id = await lookup_active_trip(r, pos["vehiculo_id"])
    # Persistimos SIEMPRE la posición: el mapa de flota necesita ver también los
    # vehículos libres. viaje_id=None = vehículo sin viaje activo (no descartar).
    row = (
        pos["time"], pos["vehiculo_id"], viaje_id,
        pos["lat"], pos["lng"],
        pos.get("speed_kmh"), pos.get("heading"),
        pos.get("odometer_km"), pos.get("ignicion"),
        pos.get("fuente"),
    )
    return row, None, None


# ----------------------------------------------------------------------
# Postgres
# ----------------------------------------------------------------------

async def connect_pg(settings: Settings, attempts: int = 10) -> asyncpg.Pool:
    """Crea el pool con reintento con backoff (arranque antes que la BD)."""
    for i in range(attempts):
        try:
            return await asyncpg.create_pool(settings.pg_dsn, min_size=1, max_size=4)
        except (OSError, asyncpg.PostgresError) as e:
            wait = min(30.0, 2 ** i)
            log.warning("Postgres no disponible (%s); reintento en %.0fs…", e, wait)
            await asyncio.sleep(wait)
    raise RuntimeError("No se pudo conectar a Postgres")


async def flush_batch(pool: asyncpg.Pool, rows: list[tuple]) -> None:
    """Inserta el lote en una transacción (todo o nada) y actualiza km_actuales por vehículo."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(INSERT_SQL, rows)
            # Actualiza km_actuales de cada vehículo con su último odómetro del lote.
            max_odo: dict[str, float] = {}
            for r in rows:
                vid, odo = r[1], r[7]  # vehiculo_id, odometer_km
                if not vid or odo is None:
                    continue
                try:
                    odo_f = float(odo)
                except (TypeError, ValueError):
                    continue
                if vid not in max_odo or odo_f > max_odo[vid]:
                    max_odo[vid] = odo_f
            if max_odo:
                await conn.executemany(
                    "UPDATE vehiculos SET km_actuales = $1 WHERE id = $2",
                    [(odo, vid) for vid, odo in max_odo.items()],
                )


# ----------------------------------------------------------------------
# Broker 1: Redis Streams
# ----------------------------------------------------------------------

async def ensure_consumer_group(r: redis.Redis, settings: Settings) -> None:
    try:
        await r.xgroup_create(
            settings.stream_key, settings.consumer_group, id="0", mkstream=True
        )
        log.info("grupo %s creado en %s", settings.consumer_group, settings.stream_key)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def read_batch(r: redis.Redis, settings: Settings):
    return await r.xreadgroup(
        settings.consumer_group,
        settings.consumer_name,
        {settings.stream_key: ">"},
        count=settings.batch_size,
        block=settings.block_ms,
    )


async def redis_dead_letter(r: redis.Redis, settings: Settings, payload: bytes, reason: str) -> None:
    await r.xadd(settings.dead_stream, {"data": payload, "reason": reason})


async def consume_redis(settings: Settings, pool: asyncpg.Pool, r: redis.Redis, stop: asyncio.Event) -> None:
    await ensure_consumer_group(r, settings)

    buffer: list[tuple] = []
    ack_ids: list[bytes] = []
    last_flush = time.monotonic()
    stats = {"inserted": 0, "dead_invalid": 0, "dead_no_viaje_activo": 0}

    async def do_flush() -> None:
        nonlocal last_flush
        if not buffer:
            return
        await flush_batch(pool, buffer)
        await r.xack(settings.stream_key, settings.consumer_group, *ack_ids)
        stats["inserted"] += len(buffer)
        log.info("lote insertado: %d posiciones (total %d)", len(buffer), stats["inserted"])
        buffer.clear()
        ack_ids.clear()
        last_flush = time.monotonic()

    log.info("consumiendo de Redis Streams: stream=%s grupo=%s", settings.stream_key, settings.consumer_group)

    while not stop.is_set():
        try:
            messages = await read_batch(r, settings)
            if messages:
                for _stream, entries in messages:
                    for entry_id, fields in entries:
                        payload = fields.get(b"data") or fields.get(b"payload")
                        if payload is None:
                            ack_ids.append(entry_id)  # sin cuerpo: descartar
                            continue

                        row, bad, motivo = await enrich(r, payload)
                        if bad is not None:
                            await redis_dead_letter(r, settings, bad, motivo)
                            stats[f"dead_{motivo}"] += 1
                            ack_ids.append(entry_id)
                            continue

                        buffer.append(row)
                        ack_ids.append(entry_id)

            if buffer and (
                len(buffer) >= settings.batch_size
                or time.monotonic() - last_flush >= settings.flush_interval
            ):
                await do_flush()

        except (redis.RedisError, OSError) as e:
            log.warning("error de Redis (reconectando…): %s", e)
            await asyncio.sleep(2)
        except asyncpg.PostgresError as e:
            # El lote NO se confirma: se reentregará. Backoff para no apretar la BD.
            log.warning("error de Postgres (reintentando en 2s): %s", e)
            await asyncio.sleep(2)
        except Exception:
            log.exception("error inesperado; continuando en 2s")
            await asyncio.sleep(2)

    try:
        await do_flush()
    except Exception:
        log.exception("no se pudo volcar el lote final (se reentregará al reiniciar)")
    log.info("consume_redis detenido. stats=%s", stats)


# ----------------------------------------------------------------------
# Broker 2: RabbitMQ
# ----------------------------------------------------------------------

async def consume_rabbitmq(settings: Settings, pool: asyncpg.Pool, r: redis.Redis, stop: asyncio.Event) -> None:
    import aio_pika

    # connect_robust reconecta automáticamente y re-declara/re-suscribe canales y colas.
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=max(1, settings.batch_size))
        queue = await channel.declare_queue(settings.rabbitmq_queue, durable=True)
        await channel.declare_queue(settings.rabbitmq_dead_queue, durable=True)

        buffer: list[tuple] = []
        pending: list = []  # mensajes pendientes de confirmar (ack)
        last_flush = time.monotonic()
        stats = {"inserted": 0, "dead_invalid": 0, "dead_no_viaje_activo": 0}
        flush_lock = asyncio.Lock()

        async def flush_and_ack() -> None:
            nonlocal buffer, pending, last_flush
            async with flush_lock:
                if not buffer:
                    return
                b, p = buffer, pending
                buffer, pending = [], []  # intercambio atómico antes del await
                await flush_batch(pool, b)
                for msg in p:
                    await msg.ack()
                stats["inserted"] += len(b)
                log.info("lote insertado: %d posiciones (total %d)", len(b), stats["inserted"])
                last_flush = time.monotonic()

        async def on_message(message) -> None:
            nonlocal last_flush
            row, bad, motivo = await enrich(r, message.body)
            if bad is not None:
                await channel.default_exchange.publish(
                    aio_pika.Message(body=bad, headers={"x-reason": motivo}),
                    routing_key=settings.rabbitmq_dead_queue,
                )
                await message.ack()
                stats[f"dead_{motivo}"] += 1
                return
            buffer.append(row)
            pending.append(message)
            if len(buffer) >= settings.batch_size:
                await flush_and_ack()

        await queue.consume(on_message, no_ack=False)
        log.info("consumiendo de RabbitMQ: cola=%s", settings.rabbitmq_queue)

        while not stop.is_set():
            await asyncio.sleep(settings.flush_interval)
            await flush_and_ack()

        await flush_and_ack()
        log.info("consume_rabbitmq detenido. stats=%s", stats)


# ----------------------------------------------------------------------
# Punto de entrada
# ----------------------------------------------------------------------

async def run(settings: Settings) -> None:
    pool = await connect_pg(settings)
    r = redis.from_url(
        settings.redis_url,
        decode_responses=False,
        health_check_interval=30,
        retry_on_timeout=True,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    log.info("worker arrancado: broker=%s", settings.broker)
    if settings.broker == "rabbitmq":
        await consume_rabbitmq(settings, pool, r, stop)
    else:
        await consume_redis(settings, pool, r, stop)

    await pool.close()
    await r.aclose()
    log.info("worker detenido")


def main() -> None:
    asyncio.run(run(Settings.from_env()))


if __name__ == "__main__":
    main()
