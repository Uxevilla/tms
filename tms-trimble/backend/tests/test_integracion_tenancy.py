"""Integración de _seed_rbac contra Postgres real (Fase 0, fix 2).

La contraseña del admin solo se restablece desde DEFAULT_ADMIN_PASSWORD cuando
TMS_RESET_ADMIN=1. En un arranque normal, el INSERT ... ON CONFLICT DO NOTHING crea
el admin la primera vez y no pisa la contraseña de un admin que ya la cambió.
"""
import psycopg2
import pytest

import config
import tenancy
from security import _hash_password, _verify_password


@pytest.mark.integration
def test_seed_rbac_reset_solo_con_flag(scratch_db, monkeypatch):
    monkeypatch.setattr(tenancy, "DEFAULT_ADMIN_USER", "admin")
    monkeypatch.setattr(tenancy, "DEFAULT_ADMIN_PASSWORD", "secreta-1")
    monkeypatch.delenv("TMS_RESET_ADMIN", raising=False)

    # 1er arranque: crea el admin con la contraseña del .env + cambio obligatorio.
    tenancy._seed_rbac(scratch_db)

    conn = psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        user=config.DB_USER, password=config.DB_PASSWORD, dbname=scratch_db,
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        cur.execute("SELECT password_hash, debe_cambiar_clave FROM sistema.usuarios WHERE usuario='admin'")
        h0, debe0 = cur.fetchone()
        assert debe0 is True, "el alta debe forzar el cambio de contraseña"
        assert _verify_password("secreta-1", h0)

        # El admin cambia su contraseña.
        cur.execute(
            "UPDATE sistema.usuarios SET password_hash=%s, debe_cambiar_clave=false WHERE usuario='admin'",
            (_hash_password("nueva-123"),),
        )

        # 2º arranque SIN flag: la contraseña nueva persiste.
        tenancy._seed_rbac(scratch_db)
        cur.execute("SELECT password_hash, debe_cambiar_clave FROM sistema.usuarios WHERE usuario='admin'")
        h1, debe1 = cur.fetchone()
        assert debe1 is False, "un arranque normal no debe forzar el cambio"
        assert _verify_password("nueva-123", h1), "no debe pisar la contraseña que cambió el admin"

        # 3er arranque CON flag: reset explícito a la del .env.
        monkeypatch.setenv("TMS_RESET_ADMIN", "1")
        tenancy._seed_rbac(scratch_db)
        cur.execute("SELECT password_hash, debe_cambiar_clave FROM sistema.usuarios WHERE usuario='admin'")
        h2, debe2 = cur.fetchone()
        assert debe2 is True
        assert _verify_password("secreta-1", h2)
    finally:
        conn.close()
