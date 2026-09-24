-- =============================================================================
-- RECONCILIACIÓN DEL ESQUEMA DESTINO
-- Sustituye la sección `sistema.integraciones` del modelo propuesto por el
-- modelo RELACIONAL de integraciones + tipos de actividad configurables por
-- cuenta. Es la feature que la sección Configuración ya implementa en
-- producción y que el JSONB plano (proveedor -> credenciales) borraba.
--
-- Por qué: `sistema.integraciones` no soporta "tipos de actividad con
-- referencia editable por cuenta", que es justo el arreglo del bug de Trimble
-- `activity.type` (producción rechazaba tanto '040' como 'CARGA'). El modelo
-- relacional sí lo soporta, y además admite más proveedores (Geotab, Webfleet,
-- Samsara, Verizon) sin un CHECK cerrado.
-- =============================================================================

DROP TABLE IF EXISTS sistema.integraciones;

CREATE TABLE sistema.integracion_proveedores (
    id         SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    codigo     TEXT NOT NULL UNIQUE,          -- trimble, ptv, transfollow, smtp, geotab, webfleet, samsara, verizon...
    nombre     TEXT NOT NULL,
    categoria  TEXT NOT NULL CHECK (categoria IN ('telemetria','rutas','ecmr','correo','notificaciones')),
    icono      TEXT,
    activo     BOOLEAN NOT NULL DEFAULT false,
    orden      SMALLINT NOT NULL DEFAULT 0
);

CREATE TABLE sistema.integracion_campos (
    id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proveedor_id SMALLINT NOT NULL REFERENCES sistema.integracion_proveedores(id) ON DELETE CASCADE,
    clave        TEXT NOT NULL,               -- username, password, api_key, database...
    etiqueta     TEXT NOT NULL,               -- "Usuario SOAP", "API Key"...
    tipo         TEXT NOT NULL CHECK (tipo IN ('texto','password','numero','url')),
    requerido    BOOLEAN NOT NULL DEFAULT false,
    orden        SMALLINT NOT NULL DEFAULT 0,
    UNIQUE (proveedor_id, clave)
);

CREATE TABLE sistema.integracion_valores (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    campo_id SMALLINT NOT NULL UNIQUE REFERENCES sistema.integracion_campos(id) ON DELETE CASCADE,
    valor    TEXT
);
-- NOTA: `valor` de credenciales debe cifrarse en la app (pgcrypto) antes de
-- escribir; NO en texto plano como la tabla `config` actual. Decisión de
-- cifrado aún pendiente con el usuario.

CREATE TABLE sistema.actividades (            -- tipo de actividad y su referencia, por proveedor
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proveedor_id SMALLINT NOT NULL REFERENCES sistema.integracion_proveedores(id) ON DELETE CASCADE,
    nombre       TEXT NOT NULL,               -- CARGA, DESCARGA, REPOSTAJE...
    referencia   TEXT NOT NULL,               -- 040, 041, EASOL01... (lo que acepta el proveedor)
    activo       BOOLEAN NOT NULL DEFAULT true,
    UNIQUE (proveedor_id, nombre)
);

-- =============================================================================
-- EQUIVALENCIA con las tablas YA construidas (solo cambia el esquema: public -> sistema)
--   integracion_proveedores  -> sistema.integracion_proveedores
--   integracion_campos       -> sistema.integracion_campos
--   integracion_valores      -> sistema.integracion_valores
--   actividades              -> sistema.actividades
-- La migración aquí es un ALTER TABLE ... SET SCHEMA + renombrar, no un re-diseño.
-- =============================================================================

-- Otras dos correcciones menores al esquema propuesto (anotar, no bloqueantes):
--  1) sistema.usuarios: la app hoy resuelve el rol por `nombre` (TEXT), no por id.
--     Mantener `sistema.roles.nombre UNIQUE` como clave natural y que el login
--     lea el nombre vía JOIN con rol_id. Sin cambio de modelo.
--  2) finanzas.facturas_recibidas UNIQUE(proveedor_id, numero_proveedor):
--     los tickets sin proveedor (proveedor_id NULL) no chocan (NULLs distintos
--     en UNIQUE de Postgres), pero conviene un CHECK (numero_proveedor IS NOT NULL
--     OR proveedor_id IS NULL) para no permitir facturas sin ningún identificador.
