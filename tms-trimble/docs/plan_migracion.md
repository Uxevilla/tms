# Plan de migración incremental → esquema destino

## Principios

- **Big-bang NO.** Migración por esquemas, de menor a mayor acoplamiento con Trimble.
- Cada fase: **crear tablas destino → migrar datos (INSERT…SELECT con mapeo) → cambiar el código → verificar → soltar tablas viejas**. No se tocan dos fases a la vez.
- **Referencias Trimble se preservan en columna dedicada** (no en la PK surrogada):
  - `vehiculos.id` (hoy = matrícula) → `flota.vehiculos.matricula` + `terminal_trimble`
  - `conductores.did` (DID FleetWorks) → `rrhh.conductores.tarjeta_tacografo`
  - `trips.id` (tripId) → `operaciones.viajes.codigo`
- **Vistas de compatibilidad** (técnica de Claude, adoptada): al fusionar/renombrar una tabla,
  se deja una VIEW con el nombre antiguo (`clientes`, `proveedores`, `transportistas`) para que
  el código actual siga funcionando sin cambios. Se migra el código a la tabla nueva y **solo
  entonces** se suelta la vista. Reduce el riesgo de cada fase a "mover datos + crear vista",
  dejando la reescritura del código para un paso posterior ya verificado.
- Script de migración **idempotente** + **snapshot del Postgres real** antes de empezar.
- Verificación por fase: recuento de filas + checksum por tabla origen/destino + smoke de rutas.

---

## Fase 0 — Preparación (obligatoria)

1. `pg_dump` del tenant real (`tms`) a un fichero de respaldo + una copia `tms_mig` para probar.
2. Script `migrar.py` (o SQL) idempotente, con `--fase=N` para ejecutar fases sueltas.
3. Harness de verificación: para cada tabla migrada, `COUNT(*)` y `md5` de un subconjunto
   de columnas clave, comparado origen vs destino.
4. Congelar cambios de esquema en producción durante la migración.

**Riesgo: nulo.** **Coste: bajo.**

---

## Fase 1 — Sistema (roles, usuarios, config, audit, sync, integraciones)

| Origen (actual) | Destino |
|---|---|
| `config.roles` | `sistema.roles` |
| `config.usuarios` | `sistema.usuarios` (+ `debe_cambiar_clave`, `empleado_id` NULL) |
| `config` | `sistema.config` |
| `sync_state` | `sistema.sync_state` |
| `audit_log` | `sistema.audit_log` (normalizar accion/antes/despues a JSONB) |
| `integracion_proveedores` | `sistema.integracion_proveedores` |
| `integracion_campos` | `sistema.integracion_campos` |
| `integracion_valores` | `sistema.integracion_valores` |
| `actividades` | `sistema.actividades` |

Transformación: `usuarios.rol` (TEXT) → `rol_id` (JOIN a `roles.nombre`). El resto es
`ALTER TABLE … SET SCHEMA sistema` + renombrar. Integraciones/actividades ya están
construidas: es un reubicar, no un rediseñar.

**Riesgo: BAJO.** **Primera fase a ejecutar.** Verifica login + matriz de roles + Configuración.

---

## Fase 2 — Maestros (terceros, direcciones, tarifas)

| Origen | Destino | Nota |
|---|---|---|
| `clientes` | `maestros.terceros` | `es_cliente=true` |
| `proveedores` | `maestros.terceros` | `es_proveedor=true` |
| `transportistas` | `maestros.terceros` | `es_transportista=true` |
| `direcciones` | `maestros.direcciones` | + `tercero_id`, `es_fiscal` |
| `tarifas` | `maestros.tarifas` | |
| `tarifas_peaje` | `maestros.tarifas_peaje` | |
| `categorias_gasto` | `maestros.categorias_gasto` | |

Punto delicado: el **merge de 3 tablas en `terceros`**. Mismo NIF puede aparecer como
cliente y proveedor → una fila con ambos flags. Migrar con `ON CONFLICT (nif)` y OR de flags.
Las FKs que hoy apuntan a `clientes.id`/`proveedores.id`/`transportistas.id` se remapean a `terceros.id`.

**Riesgo: MEDIO.** No toca Trimble.

---

## Fase 3 — RRHH (empleados, conductores 1:1, ausencias, nóminas)

| Origen | Destino | Nota |
|---|---|---|
| `empleados` | `rrhh.empleados` | |
| `conductores` | `rrhh.conductores` | `empleado_id` PK/FK; `did` → `tarjeta_tacografo` |
| `ausencias` + `ausencias_empleados` | `rrhh.ausencias` | unificar |
| `nominas` | `rrhh.nominas` | + `ss_*`, `fecha_pago`, `asiento_id` |
| `lineas_nomina` | `rrhh.nomina_lineas` | `tipo` CHECK devengo/deduccion |

`conductores` hoy referencia `empleados` por id y expone `did`. En destino `conductores.empleado_id`
es la PK (1:1). El código que busca conductor por `did` pasa a leer `tarjeta_tacografo`.

**Riesgo: MEDIO.** Afecta a la sincronización de conductores Trimble (solo la lectura del DID).

---

## Fase 4 — Flota (vehículos + mantenimiento + inspecciones + alertas)

| Origen | Destino | Nota |
|---|---|---|
| `vehiculos` | `flota.vehiculos` | **remapeo de PK**: `id`(matrícula) → `matricula` + `terminal_trimble` |
| `flota.reglas_mantenimiento` + `flota.alertas_mantenimiento` + `alertas_mantenimiento` | `flota.planes_mantenimiento` + `flota.alertas` | unificar |
| `mantenimientos` | `flota.mantenimientos` | + `plan_id`, `taller_id`, `factura_recibida_id` |
| `inspecciones` | `flota.inspecciones` | |

**El punto más delicado de todo el plan**: `vehiculos.id` ES la referencia que usa Trimble
(`findAllUnits`, telemetría, viajes). Al pasar a PK surrogada, toda lectura/escritura del SOAP
debe apuntar a `terminal_trimble`. Hacerlo en una fase aislada con el worker de ingesta parado
y una prueba de `findAllUnits` + telemetría real antes de reanudar.

**Riesgo: ALTO (Trimble).** No hacer junto a la Fase 6.

---

## Fase 5 — Finanzas (contabilidad)

| Origen | Destino | Nota |
|---|---|---|
| `cuentas` | `finanzas.cuentas` | + `padre`, `grupo` |
| `asientos` | `finanzas.asientos` | + `ejercicio`, `origen`, anulación por contra-asiento |
| `apuntes` | `finanzas.apuntes` | + trigger diferido de cuadre |
| `facturas` | `finanzas.facturas_emitidas` + `series` | |
| `factura_lineas` | `finanzas.facturas_emitidas_lineas` | + índice único anti-doble-facturación |
| `gastos` + `gastos_vehiculos` | `finanzas.facturas_recibidas` + `_lineas` | unificar + imputación por vehículo/viaje |
| `costes_fijos` | `finanzas.costes_fijos` | |
| `liquidaciones` | `finanzas.liquidaciones` | |
| (nuevo) | `finanzas.vencimientos`, `ejercicios`, `inmovilizado`, `amortizaciones` | |

Nuevo trigger `chk_asiento_cuadra` DEBE activarse solo tras migrar todos los asientos (no antes,
o rompería asientos históricos descuadrados por redondeo). Validar cuadre global con un script.

**Riesgo: ALTO (integridad contable).** Verificar contra el Postgres real, no solo tests.

---

## Fase 6 — Operaciones (viajes, paradas, mercancía, eCMR, palés, mensajería)

| Origen | Destino | Nota |
|---|---|---|
| `trips` | `operaciones.viajes` + `operaciones.pedidos` | `trips.id`(tripId) → `viajes.codigo` |
| `paradas` | `operaciones.paradas` | `tarea_ext_id` = id tarea Trimble |
| `tramos` | (se elimina / se recalcula) | |
| `ecmr` | `operaciones.ecmr` | |
| `saldos_pales` | `operaciones.movimientos_pales` + vista `saldo_pales` | saldo pasa a ser SUM, no columna |
| `mensajes` | `operaciones.mensajes` | |

`pedidos` es nuevo: los viajes con cliente/precio pasan a tener `pedido_id`; los viajes en vacío
quedan con `pedido_id NULL`. Remapeo de `trips.id` → `viajes.codigo` es el segundo punto crítico
junto al de vehículos.

**Riesgo: ALTO (Trimble tripId).** Ejecutar DESPUÉS de la Fase 4 y con el worker parado.

---

## Fase 7 — Telemetría (hypertables)

| Origen | Destino | Nota |
|---|---|---|
| `telemetria` + `telemetria.posiciones_gps` | `telemetria.posiciones` (hypertable) | `odometro_m`, `ignicion`, `fuente` |
| `tacografo_estados` + `tacografo_dstat` | `telemetria.tacografo_estado` (hypertable) | |
| (nuevo) | `telemetria.tacografo_actividades` | |

Hypertable sin FK: el `vehiculo_id` pasa de TEXT (matrícula) a BIGINT (FK lógica no declarada).
Migrar con `INSERT…SELECT` + `time_bucket`; verificar que `create_hypertable` no pierde datos.

**Riesgo: MEDIO.** Timescale real aún sin probar (simulado hasta ahora).

---

## Fase 8 — Docs

| Origen | Destino | Nota |
|---|---|---|
| `files` | `docs.documentos` | binario a disco/S3/MinIO, no base64 |
| (vinculación implícita hoy) | `docs.documento_vinculos` | FK reales en vez de entidad+id |

**Riesgo: MEDIO.** Requiere decidir el almacén de objetos (hoy ficheros en disco).

---

## Fase 9 — Vistas, limpieza y verificación final

1. Crear vistas: `finanzas.rentabilidad_viaje`, `flota.disponibilidad`, `operaciones.saldo_pales`.
2. `DROP` de tablas viejas (tras una semana sin incidencias).
3. Verificación integral contra Postgres real: 27 tests + smoke de todas las rutas + una
   prueba real de `findAllUnits`/`findAllDrivers`/telemetría + facturación y nóminas.

---

## Orden recomendado de ejecución (revisado con Claude)

`0 → 1(docs) → 2(maestros) → 3(rrhh) → 4(sistema) → 5(finanzas) → 6(flota) → 7(operaciones) → 8(telemetria) → 9(limpieza)`

Razonamiento (coincide con Claude):
- **Primero lo que no rompe nada**: `docs` (sacar base64 a disco), `maestros` (terceros) y
  `rrhh` (conductores 1:1), todas con **vistas de compatibilidad** para que el código siga igual.
- **Después finanzas** (facturas_recibidas, trigger de cuadre, series): es donde un error cuesta
  dinero; exige tests de integración contra BD real (`integration_contabilidad.py`).
- **Por último operaciones** (pedidos/viajes/paradas), lo más ligado a Trimble; hacerlo cuando
  `services/viajes.py` y `services/sync.py` tengan tests, con el worker de ingesta parado.
- `sistema` (roles/usuarios/config/integraciones/actividades) y `flota` (remap PK de vehiculos)
  se intercalan antes de operaciones; flota justo antes de operaciones por el vehiculo_id.

## Decisiones

1. ~~Cifrado de credenciales~~ → **DECIDIDO e implementado: Fernet** (clave derivada de `TMS_SECRET_KEY`), valores cifrados en reposo + enmascarados en la API. `crypto.py` + `_valores_proveedor` descifra.
2. ~~Almacén de objetos para docs~~ → **DECIDIDO: disco** (`/app/backend/data/docs`, content-addressed por SHA-256).
3. ~~`tramos`~~ → **DECIDIDO: se recalcula**, se elimina la tabla.
4. ~~`pedidos` como entidad separada~~ → **DECIDIDO: sí**.

## Progreso

- ✅ **Fase 0** — backup `pg_dump` (tms + tms_master) + copia de prueba `tms_mig`.
- ✅ **Fase 1 (docs)** — base64 sale de la BD a disco (`files.storage_key/sha256/bytes`, `gastos_vehiculos.storage_key`); 10+ rutas migradas (subida/serve/sync/OCR/pedido/e-CMR) + borrado con chequeo de referencias. Verificado en producción (0 base64 previo, sin datos que migrar).
- ✅ **Fase 2 (maestros)** — `maestros.terceros` (merge de clientes/proveedores/transportistas por NIF + flags `es_cliente/es_proveedor/es_transportista`); las 3 tablas viejas son ahora **vistas de compatibilidad con triggers INSTEAD OF**, así que el código existente funciona sin cambios. Verificado en producción (CRUD vía API cae en `terceros` con el flag correcto). 0 datos a migrar (tablas vacías).
- ✅ **Fase 3 (rrhh)** — `rrhh.conductores` 1:1 sobre `empleados` (`empleado_id` PK/FK, `tarjeta_tacografo` = antiguo `did`, `tarifa_km`). La tabla vieja `conductores` es ahora una **vista (JOIN empleados) con triggers INSTEAD OF**: los campos nombre/dni/telefono/email viven en `empleados` (fin de la desnormalización y del doble mantenimiento de `_sync_conductor`). 64 conductores migrados; CRUD + sincronización verificados en producción.
- ✅ **Fase 4 (sistema)** — `roles`/`usuarios`/`config`/`sync_state`/`audit_log`/`integraciones`/`actividades` movidas al esquema `sistema`; `usuarios.rol` (TEXT) transformado en `rol_id` (FK a `sistema.roles.id`). Vistas de compatibilidad (`config.roles`, `config.usuarios`, `config`, `sync_state`, `audit_log`, `integracion_*`, `actividades`) para que el código siga funcionando; escrituras con `ON CONFLICT` redirigidas a `sistema.*` (no funcionan sobre vistas). Login + roles + Configuración verificados en producción.
- ✅ **Fase 5 (finanzas, parte 1)** — **trigger de cuadre** (`apuntes_cuadre`, DEFERRABLE): un asiento descuadrado se rechaza en el commit (verificado: 100/90 → rechazado; 100/100 → pasa). Tablas `finanzas.series` (numeración) y `finanzas.ejercicios` (ejercicios fiscales) creadas.
- ✅ **Fase 5 (parte 2a, relocación)** — 9 tablas financieras movidas al esquema `finanzas` (`cuentas/asientos/apuntes/facturas/factura_lineas/gastos/gastos_vehiculos/costes_fijos/liquidaciones`) + vistas de compatibilidad con los nombres viejos; `ON CONFLICT` y `ALTER` redirigidos a `finanzas.*`; tablas `vencimientos`/`inmovilizado`/`amortizaciones` creadas. Migración `migracion_fase5_finanzas.sql` aplicada a `tms_mig` y `tms`. Verificado en producción: 41 cuentas + asiento F-2026-0001 intactos, lectura/escritura vía vista + upsert OK.
- ✅ **Fase 5 (parte 2b, merge de código)** — dual-write: los 4 flujos de gasto (`add_gasto` viaje, `add_gasto_vehiculo`, mantenimiento→reparaciones, `_gasto_subcontrata`) escriben ahora TAMBIÉN en `finanzas.facturas_recibidas(_lineas)` con imputación por vehículo/viaje, en la misma transacción (merge incremental sin romper el flujo actual). Vista `finanzas.rentabilidad_viaje` (ingresos−costes por viaje desde facturas_recibidas) creada. Verificado en producción: gasto 121€ → factura_recibida (base 100 + IVA 21) + línea imputada. La migración de LECTURAS a `facturas_recibidas` + borrado de `gastos`/`gastos_vehiculos` queda para Fase 9.
- ✅ **Fase 5 (parte 2b, esquema + series)** — tablas `finanzas.facturas_recibidas` + `facturas_recibidas_lineas` creadas (con UNIQUE proveedor+num, imputación vehículo/viaje por línea); `finanzas.series` sembrada ('F'/'A') y cableada en `_factura_numero` (contador atómico + re-sync con MAX emitido). El esquema `finanzas` queda COMPLETO (16 tablas). Verificado en producción.
- ✅ **Fase 8 (telemetría)** — verificado: `telemetria` y `telemetria.posiciones_gps` son hypertables TimescaleDB activas (sin FK, ingesta rápida). El merge a `telemetria.posiciones` queda como refactor cosmético (difiere del esquema actual que usa ambas).
- ⏳ **Fase 6 (flota)** — relocación `vehiculos`/`mantenimientos` → `flota.*` + **remap PK** (`vehiculos.id` TEXT→BIGINT surrogate + `matricula`/`terminal_trimble`/`codigo`). ROMPEDOR sobre la flota viva (telemetría + Trimble). Script listo en `migracion_fase6_flota.sql`; requiere worker parado + prueba `findAllUnits` real.
- ⏳ **Fase 7 (operaciones)** — relocación `trips`/`paradas`/`tramos` → `operaciones.*` + **remap PK** (`trips.id` TEXT→BIGINT + `codigo`). ROMPEDOR (sync Trimble + telemetría + facturación). Script en `migracion_fase7_operaciones.sql`; mismo procedimiento que Fase 6.
- ⏳ **Fase 9 (limpieza)** — eliminar vistas de compatibilidad + tablas legacy una vez el código use nombres cualificados por esquema. Depende de completar la migración del código; no bloquea el funcionamiento.
- ✅ **Fase 6/7 (relocación)** — `vehiculos`/`mantenimientos` movidas a `flota.*` y `trips`/`paradas`/`tramos` a `operaciones.*` (los 8 esquemas del modelo destino ya quedan poblados); vistas de compatibilidad + `ON CONFLICT`/`ALTER` redirigidos. Migración `migracion_fase67_relocacion.sql` aplicada a `tms_mig` y `tms`. Verificado en producción: 50 vehículos + viajes + telemetría en vivo intactos vía vistas; FKs `paradas/tramos→trips` OK. **Pendiente solo el remap de PK** (rompedor, script listo).

## Nota sobre el punto 8 de Claude (secretos)

Claude propone `sistema.integraciones` (JSONB). **No lo adopto tal cual**: perdería los
"tipos de actividad con referencia editable por cuenta" (la feature de Configuración que ya
está en producción y que arregla el bug de `activity.type` de Trimble). El modelo relacional
`integracion_proveedores → campos → valores` + `actividades` (ver `reconciliacion_schema.sql`)
da lo mismo que el JSONB **y además** soporta esa feature. El principio sí se mantiene:
secretos separados de `sistema.config` y cifrados en la app.

Aclaración verificada: `GET /api/config` **ya enmascara** los valores sensibles
(`password`, `api_key`, `token`, `secret`, `clave`, `contraseña`) con `••••••••` — ese leak
concreto ya está cerrado. Lo que queda es purgar los secretos legacy que aún viven en la
tabla `config` y cifrar los valores en `integracion_valores`.
