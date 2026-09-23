# RUNBOOK de Operaciones — TMS

> Plataforma de gestión de flotas (Transport Management System).
> Stack Docker unificado en `/root/tms-stack`, ejecutándose sobre Proxmox/Debian.

---

## 1. Arquitectura general

### Componentes

| Servicio | Contenedor | Rol | Puerto |
|---|---|---|---|
| Nginx | `tms-frontend` | Sirve la SPA (React) + proxy `/api` y `/ws` | `:8080` (host) |
| FastAPI | `tms-backend` | API, lógica de negocio, RBAC JWT | `:8750` (interno) |
| TimescaleDB | `tms-timescaledb` | PostgreSQL 15 + hypertables (telemetría) | `:5432` (interno) |
| Redis | `tms-redis` | Streams de telemetría + Pub/Sub | `:6379` (interno) |
| Worker | `tms-worker` | Ingesta asíncrona de telemetría | — |
| Grafana | `tms-grafana` | KPIs + monitorización (embebido) | `/grafana/` |
| cAdvisor | `tms-cadvisor` | Métricas de consumo de contenedores | `:8080` (interno) |
| Prometheus | `tms-prometheus` | Raspa cAdvisor, alimenta Grafana | `:9090` (interno) |
| Cloudflare | `tms-cloudflared` | Túnel Zero Trust (acceso externo) | perfil `tunnel` |

### Flujo de peticiones

```
Internet
   │  Cloudflare Zero Trust (cloudflared, perfil "tunnel")
   ▼
Nginx (tms-frontend :8080)
   ├── /        → SPA React
   ├── /api     → FastAPI (tms-backend) ──► TimescaleDB / Redis
   ├── /ws      → WebSocket FastAPI (tiempo real)
   └── /grafana → Grafana
                    │
                    ├── Datasource TimescaleDB (KPIs de flota)
                    └── Datasource Prometheus ──► cAdvisor (métricas Docker)
```

### Red

- Red interna `tms_net` (bridge). Solo `tms-frontend` publica `:8080` al host.
- BD, Redis, backend, cAdvisor y Prometheus **no exponen puertos al host**.
- El cifrado y la seguridad perimetral los aporta el túnel de Cloudflare; Nginx sirve HTTP interno.

---

## 2. Operativa básica (Docker)

Todo se orquesta desde `/root/tms-stack` (es donde vive `docker-compose.yml`).

### Levantar / parar la pila

```bash
cd /root/tms-stack

# Levantar todo (reconstruye si hay cambios en imágenes)
docker compose up -d --build

# Levantar sin reconstruir (más rápido si solo cambió .env)
docker compose up -d

# Parar todo (sin borrar datos)
docker compose down

# Estado de los servicios
docker compose ps
```

### Reiniciar un servicio concreto

```bash
docker compose restart tms-backend      # API
docker compose restart tms-worker       # worker de telemetría
docker compose restart tms-frontend     # Nginx/SPA
docker compose restart tms-grafana      # Grafana (obligatorio tras añadir un datasource)
docker compose restart tms-timescaledb  # base de datos (¡cuidado: corta todo)
```

### Reconstruir código (frontend / backend)

El frontend y el backend van **copiados dentro de la imagen**, así que cualquier cambio de código exige reconstruir:

```bash
docker compose up -d --build frontend    # frontend React (puerto :8080)
docker compose up -d --build tms-backend # API FastAPI
docker compose up -d --build tms-worker  # worker de telemetría
```

> Editar `src/` y recargar el navegador **no** basta: hay que reconstruir la imagen.

### Logs

```bash
docker logs -f tms-worker            # worker de telemetría (seguimiento en vivo)
docker logs -f --tail 200 tms-backend   # API (últimas 200 líneas)
docker logs -f --tail 200 tms-frontend  # Nginx
docker logs -f --tail 200 tms-grafana   # Grafana (provisioning de datasources/dashboards)
```

### Acceso a la base de datos

```bash
docker exec -it tms-timescaledb psql -U tms -d tms
```

### Túnel Cloudflare

```bash
# Activar (requiere TUNNEL_TOKEN en .env)
docker compose --profile tunnel up -d
```

---

## 3. Disaster Recovery

### Dónde están los backups

| Elemento | Ruta |
|---|---|
| Volcados | `/var/backups/tms/` |
| Script | `/usr/local/bin/backup_tms.sh` |
| Log del backup | `/var/log/backup_tms.log` |
| Log del cron | `/var/log/backup_tms.cron.log` |

- El cron ejecuta el backup **todos los días a las 03:00**. Línea exacta (verificada 2026-09-22, estaba SIN instalar):
  ```bash
  0 3 * * * [ -f /etc/tms_backup.env ] && . /etc/tms_backup.env; /usr/local/bin/backup_tms.sh
  ```
  **OJO**: el guard `[ -f ... ]` permite que el backup corra aunque falte el `.env` (resiliente). Para las **notificaciones de error por Telegram**, crear `/etc/tms_backup.env` (chmod 600) con `TELEGRAM_BOT_TOKEN=...` y `TELEGRAM_CHAT_ID=...` — sin él, el backup funciona pero NO avisa en caso de fallo (quedó pendiente de materializar el fichero).
- Rotación automática: se eliminan los volcados de más de **7 días**.
- Cada día genera: `tms_YYYYMMDD.sql.gz`, `tms_master_YYYYMMDD.sql.gz` y `globals_YYYYMMDD.sql.gz`.
- El script es **multi-tenant**: detecta todas las bases (`tms`, `tms_master`, `tms_<slug>`) automáticamente.

### Restauración completa (desastre total)

Los 4 pasos, exactamente como documenta el script:

```bash
# 1. Recrear las bases de datos (solo si fueron destruidas)
docker exec tms-timescaledb psql -U tms -d postgres -c "CREATE DATABASE tms OWNER tms;"
docker exec tms-timescaledb psql -U tms -d postgres -c "CREATE DATABASE tms_master OWNER tms;"

# 2. Extensión TimescaleDB (en cada base)
docker exec tms-timescaledb psql -U tms -d tms -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
docker exec tms-timescaledb psql -U tms -d tms_master -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"

# 3. Restaurar roles globales
gunzip -c /var/backups/tms/globals_YYYYMMDD.sql.gz | docker exec -i tms-timescaledb psql -U tms -d postgres

# 4. Restaurar cada base (el SET restoring=on debe ir en la MISMA sesión que el restore)
( echo "SET timescaledb.restoring = 'on';"; gunzip -c /var/backups/tms/tms_YYYYMMDD.sql.gz ) \
  | docker exec -i tms-timescaledb psql -U tms -d tms

( echo "SET timescaledb.restoring = 'on';"; gunzip -c /var/backups/tms/tms_master_YYYYMMDD.sql.gz ) \
  | docker exec -i tms-timescaledb psql -U tms -d tms_master
```

> **Nota TimescaleDB**: `pg_dump` emite un warning de FKs circulares sobre `continuous_agg` (normal). El flag `timescaledb.restoring='on'` evita problemas al restaurar hypertables.

Terminada la restauración, levanta la pila:

```bash
cd /root/tms-stack && docker compose up -d
```

### Verificación post-restauración

```bash
docker exec tms-timescaledb psql -U tms -d tms -c "SELECT count(*) FROM vehiculos;"
docker exec tms-timescaledb psql -U tms -d tms -c "SELECT count(*) FROM telemetria.posiciones_gps;"
curl -s http://localhost:8080/grafana/api/health   # debe devolver "database": "ok"
```

---

## 4. Credenciales y entorno

Los secretos viven en **`/root/tms-stack/.env`** (un fichero, no en el repositorio). Lista completa de variables:

### Críticas (obligatorias)

| Variable | Para qué |
|---|---|
| `DB_PASSWORD` | Contraseña de la BD TimescaleDB (usuario `tms`) |
| `TMS_SECRET_KEY` | Firma HMAC de tokens de sesión / JWT |
| `GRAFANA_DB_PASSWORD` | Rol read-only `tms_admin` de Grafana |
| `SUPERADMIN_USER` / `SUPERADMIN_PASSWORD` | Login del super-admin multi-tenant |

### Integraciones (por defecto del primer cliente, luego editables por cliente en la UI)

| Variable | Integración |
|---|---|
| `TRIMBLE_USERNAME` / `TRIMBLE_PASSWORD` / `TRIMBLE_CUSTOMER` / `TRIMBLE_TERMINAL` | Trimble FleetWorks (telemetría) |
| `PTV_API_KEY` / `PTV_BASE_URL` | PTV (rutas y peajes) |
| `TRANSFOLLOW_API_KEY` / `TRANSFOLLOW_BASE_URL` | TransFollow (e-CMR) |
| `TRANSFOLLOW_WEBHOOK_USER` / `TRANSFOLLOW_WEBHOOK_PASSWORD` | Webhook de cierre de viajes e-CMR |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Correo saliente (nóminas/facturas por email) |

### Otros

| Variable | Para qué |
|---|---|
| `DB_USER` / `DB_NAME` | Usuario/BD (defaults `tms`) |
| `TUNNEL_TOKEN` | Túnel Cloudflare (solo perfil `tunnel`) |
| `DEFAULT_ADMIN_USER` / `DEFAULT_ADMIN_PASSWORD` | Admin del primer tenant |
| `FIRST_TENANT_SLUG` / `FIRST_TENANT_NAME` | Primer cliente (default `eusebio`) |

> **Seguridad del admin (desde 2026-09-22)**: `admin123` está ELIMINADO por completo.
> El admin del tenant se crea/rota con `DEFAULT_ADMIN_PASSWORD` del `.env` (o una
> contraseña aleatoria volcada a los logs si falta) y se **fuerza el cambio en el
> primer login** vía el flag `debe_cambiar_clave`. Si `.env` trae contraseña, rota en
> cada arranque hasta que el admin la cambie — útil como *break-glass*: actualiza
> `DEFAULT_ADMIN_PASSWORD`, reinicia el backend y entra con la nueva clave. El cambio
> de clave lo hace `POST /api/auth/change-password` (mín. 12 caracteres).

### Telegram (notificación de errores del backup)

Estas **NO van en `.env`**, sino en `/etc/tms_backup.env` (**ya creado con `chmod 600`**, valores vacíos pendientes de rellenar a mano por SSH):

```bash
TELEGRAM_BOT_TOKEN="..."
TELEGRAM_CHAT_ID="..."
```

La línea de cron que las carga:

```cron
0 3 * * * . /etc/tms_backup.env; /usr/local/bin/backup_tms.sh >>/var/log/backup_tms.cron.log 2>&1
```

---

## Resumen rápido

```bash
cd /root/tms-stack && docker compose up -d --build   # levantar/actualizar todo
docker compose ps                                      # estado
docker compose restart <servicio>                      # reiniciar uno
docker compose up -d --build frontend                  # reconstruir frontend
docker logs -f tms-worker                              # logs telemetría
docker exec -it tms-timescaledb psql -U tms -d tms     # consola BD
/usr/local/bin/backup_tms.sh                           # backup manual
tail -f /var/log/backup_tms.log                        # seguimiento del backup
```
