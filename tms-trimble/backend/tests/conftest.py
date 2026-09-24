import os
import sys
from pathlib import Path

# El backend exige TMS_SECRET_KEY (>=32 chars) para arrancar; sin esto los tests no importan main.
os.environ.setdefault("TMS_SECRET_KEY", "t" * 40)

# Hace importable `main` y sus módulos hermanos (config, soap_client, ...) desde tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2  # noqa: E402
import pytest  # noqa: E402

import config  # noqa: E402
import main  # noqa: E402


@pytest.fixture
def scratch_db():
    """BD PostgreSQL real de un solo uso con el esquema completo aplicado.

    Crea `tms_it_<hex>`, aplica el DDL igual que producción (`main._db()`:
    _SCHEMA + migraciones + _SCHEMA_VIEWS) y la destruye al terminar.
    Requiere Postgres accesible vía config.DB_HOST/DB_PORT/DB_USER/DB_PASSWORD
    y un usuario con CREATEDB (en CI, el servicio Postgres de GitHub Actions).
    """
    import secrets

    scratch = f"tms_it_{secrets.token_hex(4)}"

    def _admin():
        return psycopg2.connect(
            host=config.DB_HOST, port=config.DB_PORT,
            user=config.DB_USER, password=config.DB_PASSWORD,
            dbname=config.DB_NAME,
        )

    a = _admin()
    a.autocommit = True
    a.cursor().execute(f"CREATE DATABASE {scratch}")
    a.close()

    # Apunta _db() al scratch y aplica el esquema completo (tablas + migraciones + vistas/triggers).
    tok = main._tenant_ctx.set({"db_name": scratch, "empresa": "it", "superadmin": False})
    try:
        conn = main._db()
        conn.close()  # devuelve la conexión al pool
    finally:
        main._tenant_ctx.reset(tok)

    yield scratch

    # Teardown: cerrar el pool del scratch ANTES de soltar la BD (si no, DROP falla).
    import db

    pool = db._pools.pop(scratch, None)
    if pool is not None:
        pool.closeall()
    main._schema_done.discard(scratch)

    a = _admin()
    a.autocommit = True
    a.cursor().execute(f"DROP DATABASE IF EXISTS {scratch}")
    a.close()
