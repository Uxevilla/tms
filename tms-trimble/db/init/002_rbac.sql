-- =====================================================================
-- RBAC: roles y usuarios en PostgreSQL (esquema `config`)
--
-- NOTA IMPORTANTE: `config` aquí es un ESQUEMA, distinto de la tabla
-- `public.config` (key/value) que ya existe. En SQL siempre hay que
-- cualificar: config.roles / config.usuarios (no confundir con public.config).
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS config;

-- Roles del sistema.
CREATE TABLE IF NOT EXISTS config.roles (
    id          SERIAL  PRIMARY KEY,
    nombre      TEXT    UNIQUE NOT NULL,   -- 'admin', 'dispatcher', 'conductor'
    descripcion TEXT
);

-- Usuarios con contraseña bcrypt (passlib) y un rol asociado.
CREATE TABLE IF NOT EXISTS config.usuarios (
    id                 SERIAL       PRIMARY KEY,
    usuario            TEXT         UNIQUE NOT NULL,
    password_hash      TEXT         NOT NULL,   -- bcrypt
    rol                TEXT         NOT NULL REFERENCES config.roles(nombre),
    nombre             TEXT,
    activo             BOOLEAN      NOT NULL DEFAULT true,
    debe_cambiar_clave BOOLEAN      NOT NULL DEFAULT false,
    creado_en          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_usuarios_rol ON config.usuarios (rol);

-- ---------------------------------------------------------------------
-- Seeding: roles básicos
-- ---------------------------------------------------------------------
INSERT INTO config.roles (nombre, descripcion) VALUES
    ('admin',      'Administrador: acceso total'),
    ('dispatcher', 'Dispatcher / planificador de operaciones'),
    ('conductor',  'Conductor')
ON CONFLICT (nombre) DO NOTHING;

-- NOTA: el usuario admin ya NO se siembra aquí con contraseña hardcodeada.
-- Lo crea/rota la app en `_seed_rbac` con DEFAULT_ADMIN_PASSWORD (del .env)
-- o una contraseña aleatoria + forzado de cambio en el primer login.
