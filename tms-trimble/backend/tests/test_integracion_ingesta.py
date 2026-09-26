"""Ingesta multi-tenant: salta tenants sin credenciales Trimble propias.

Cubre la revisión de PR #14: un tenant "aaa" sin credenciales en la maestra no
aborta la ingesta del primer tenant ni duplica su cuenta (fallback al .env).
"""
import pytest

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_tiene_trimble_sin_credenciales(scratch_db):
    """Un tenant sin credenciales Trimble en BD → _tiene_trimble() es False."""
    from workers.ingesta import _tiene_trimble

    tok, conn = _conn(scratch_db)
    try:
        # El scratch_db nace sin integracion_valores de trimble.
        assert _tiene_trimble() is False
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_por_tenant_salta_sin_trimble(scratch_db, monkeypatch):
    """_por_tenant salta 'aaa' (sin credenciales) y sincroniza el primer tenant."""
    from workers.ingesta import _por_tenant

    tok, conn = _conn(scratch_db)
    try:
        empresas = [
            {"slug": "eusebio", "nombre": "Eusebio", "db_name": scratch_db},
            {"slug": "aaa", "nombre": "AAA", "db_name": "tms_aaa"},
        ]
        monkeypatch.setattr("workers.ingesta._empresas", lambda: empresas)

        # 'aaa' no tiene credenciales; 'eusebio' (primer tenant) se sincroniza igual.
        monkeypatch.setattr(
            "workers.ingesta._tiene_trimble",
            lambda: (main._tenant_ctx.get() or {}).get("empresa") != "aaa",
        )

        sincronizados = []
        _por_tenant(lambda: sincronizados.append((main._tenant_ctx.get() or {}).get("empresa")))

        assert sincronizados == ["eusebio"]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_por_tenant_no_aborta_por_un_tenant(scratch_db, monkeypatch):
    """Un error en un tenant no aborta al resto (log + continúa)."""
    from workers.ingesta import _por_tenant

    tok, conn = _conn(scratch_db)
    try:
        empresas = [
            {"slug": "aaa", "nombre": "AAA", "db_name": "tms_aaa"},
            {"slug": "eusebio", "nombre": "Eusebio", "db_name": scratch_db},
        ]
        monkeypatch.setattr("workers.ingesta._empresas", lambda: empresas)
        monkeypatch.setattr("workers.ingesta._tiene_trimble", lambda: True)

        sincronizados = []

        def fn():
            slug = (main._tenant_ctx.get() or {}).get("empresa")
            if slug == "aaa":
                raise RuntimeError("trimble 503")
            sincronizados.append(slug)

        _por_tenant(fn)  # no debe lanzar

        assert sincronizados == ["eusebio"]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
