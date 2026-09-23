# TMS — Pipeline de ingesta telemática

Infraestructura base para ingerir telemetría GPS del hardware embarcado:

- **PostgreSQL + TimescaleDB**: núcleo relacional (`ops.viajes`) + hypertable de
  posiciones (`telemetria.posiciones_gps`) particionada por 1 día.
- **Redis**: doble rol — caché del viaje activo (`vehiculo:{id}:viaje_activo`) y
  broker de colas vía **Redis Streams** (elegido frente a Pub/Sub porque Streams
  es durable y admite grupos de consumidores con confirmación; Pub/Sub pierde
  mensajes si no hay suscriptor). RabbitMQ sería la alternativa si se prefiere un
  broker dedicado.
- **Worker** (`worker/ingest_worker.py`): consume posiciones del broker (Redis
  Streams o RabbitMQ según `BROKER`), enriquece con el `viaje_id` desde la caché
  y hace *batch insert* en la hypertable.

## Levantar el stack

```bash
cd /root/tms-telemetry

# (opcional) credenciales reales
cp .env.example .env

# construir y arrancar
docker compose up -d --build

# estado
docker compose ps

# logs del worker
docker compose logs -f worker
```

## Broker alternativo: RabbitMQ

Por defecto el worker consume de **Redis Streams**. Para usar **RabbitMQ** como
broker (Redis sigue siendo la caché del viaje activo), se aplica el override:

```bash
docker compose -f docker-compose.yml -f docker-compose.rabbitmq.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.rabbitmq.yml logs -f worker
```

- Cola de ingesta: `telemetria.ingesta` (durable, confirmación manual).
- Cola de no-procesables: `telemetria.ingesta.dead` (motivo en cabecera `x-reason`).
- Panel de administración: http://localhost:15672 (guest/guest).

Publicar un mensaje (con el worker arriba, la cola ya está declarada):

```bash
docker compose -f docker-compose.yml -f docker-compose.rabbitmq.yml exec rabbitmq \
  rabbitmqadmin publish routing_key=telemetria.ingesta \
  payload='{"vehiculo_id":1234,"time":"2026-09-21T13:12:26Z","lat":41.38,"lng":2.16}'
```

## Probar la ingesta (end-to-end)

```bash
# 1) Marcar un viaje activo para el vehículo 1234
docker compose exec redis redis-cli SET vehiculo:1234:viaje_activo 42

# 2) Publicar una posición en el stream
docker compose exec redis redis-cli XADD telemetria:ingesta '*' data '{"vehiculo_id":1234,"time":"2026-09-21T13:12:26Z","lat":41.38,"lng":2.16,"speed_kmh":94,"heading":90,"odometer_km":5771532,"ignicion":true,"fuente":"can"}'

# 3) Verificar que llegó a la hypertable
docker compose exec postgres psql -U tms -d tms \
  -c "SELECT time, vehiculo_id, viaje_id, lat, lng FROM telemetria.posiciones_gps ORDER BY time DESC LIMIT 5;"
```

Posiciones de un vehículo **sin** viaje activo en caché se derivan a
`telemetria:ingesta:dead` (motivo `no_viaje_activo`) en lugar de perderse.

## Formato del mensaje (stream)

Campo `data` con JSON:

```json
{
  "vehiculo_id": 1234,
  "time": "2026-09-21T13:12:26Z",
  "lat": 41.38,
  "lng": 2.16,
  "speed_kmh": 94,
  "heading": 90,
  "odometer_km": 5771532,
  "ignicion": true,
  "fuente": "can"
}
```

## Variables del worker

| Variable | Defecto | Descripción |
|---|---|---|
| `PG_DSN` | (requerida) | DSN de Postgres/TimescaleDB |
| `REDIS_URL` | `redis://localhost:6379/0` | Conexión Redis |
| `STREAM_KEY` | `telemetria:ingesta` | Stream de entrada |
| `CONSUMER_GROUP` | `workers` | Grupo de consumidores |
| `CONSUMER_NAME` | `worker-<pid>` | Identidad del consumidor |
| `DEAD_STREAM` | `telemetria:ingesta:dead` | Stream de mensajes no procesables |
| `BATCH_SIZE` | `500` | Tamaño de lote |
| `FLUSH_INTERVAL` | `2.0` | Segundos máx. entre volcados |
| `BLOCK_MS` | `5000` | Bloqueo de `XREADGROUP` |

## Decisiones de diseño

- **Entrega at-least-once**: `XACK` solo tras el insert. La clave única
  `(vehiculo_id, time)` + `ON CONFLICT DO NOTHING` hace idempotente el redelivery.
- **Lotes atómicos**: `executemany` dentro de una transacción (todo o nada).
  Para volúmenes muy altos se puede pasar a `COPY` (`copy_records_to_table`).
- **`viaje_id` nullable**: una posición sin viaje activo no se pierde; se deriva
  a dead-letter para reprocesarla cuando el viaje se abra.
- **Comandos solo backend/BD**: no incluye frontend ni orquestación de viajes.
