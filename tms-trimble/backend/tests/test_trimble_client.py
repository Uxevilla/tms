"""Tests de clients.trimble.get_client: debe leer la config de la BD (integracion_valores)
y no solo los defaults del .env (bug del frontend JWT sin 'empresa')."""
import datetime

import pytest
from fastapi import HTTPException

import clients.trimble as tc


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _limpia_cache():
    tc._client_cache.clear()
    yield
    tc._client_cache.clear()


def _mock_db(monkeypatch, valores):
    monkeypatch.setattr(tc, "_db", lambda: _FakeConn())
    monkeypatch.setattr(tc, "_valores_proveedor", lambda conn, codigo: dict(valores))


def test_get_client_lee_config_bd_con_tenant_none(monkeypatch):
    # JWT del frontend React (tenant en None): debe leer la BD, no el .env.
    _mock_db(monkeypatch, {"username": "u_bd", "password": "p_bd", "customer": "c_bd", "terminal": "t_bd"})
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_USERNAME", "u_env")
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_CUSTOMER", "c_env")
    tc.get_client()
    assert ("u_bd", "p_bd", "c_bd", "t_bd") in tc._client_cache


def test_get_client_fallback_env_si_bd_vacia(monkeypatch):
    _mock_db(monkeypatch, {})
    monkeypatch.setattr(tc.config, "TMS_ENV", "dev")
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_USERNAME", "u_env")
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_PASSWORD", "p_env")
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_CUSTOMER", "c_env")
    monkeypatch.setattr(tc.config, "DEFAULT_TRIMBLE_TERMINAL", "t_env")
    tc.get_client()
    assert ("u_env", "p_env", "c_env", "t_env") in tc._client_cache


def test_get_client_503_sin_credenciales(monkeypatch):
    _mock_db(monkeypatch, {})
    for k in ("DEFAULT_TRIMBLE_USERNAME", "DEFAULT_TRIMBLE_PASSWORD",
              "DEFAULT_TRIMBLE_CUSTOMER", "DEFAULT_TRIMBLE_TERMINAL"):
        monkeypatch.setattr(tc.config, k, "")
    with pytest.raises(HTTPException) as ei:
        tc.get_client()
    assert ei.value.status_code == 503


def test_get_client_cambio_password_invalida_cache(monkeypatch):
    # Mismo username/customer, distinta password → NO devolver el cliente cacheado.
    _mock_db(monkeypatch, {"username": "u", "password": "p1", "customer": "c", "terminal": "t"})
    tc.get_client()
    _mock_db(monkeypatch, {"username": "u", "password": "p2", "customer": "c", "terminal": "t"})
    tc.get_client()
    assert ("u", "p1", "c", "t") in tc._client_cache
    assert ("u", "p2", "c", "t") in tc._client_cache


def test_mark_inicial_dos_dias_atras():
    from services.sync import _mark_inicial
    m = _mark_inicial()
    assert m.endswith(".000")
    ts = datetime.datetime.strptime(m, "%Y-%m-%dT%H:%M:%S.000").replace(tzinfo=datetime.timezone.utc)
    delta = datetime.datetime.now(datetime.timezone.utc) - ts
    assert datetime.timedelta(days=1.5) < delta < datetime.timedelta(days=2.5)


@pytest.mark.integration
def test_get_client_lee_config_del_tenant_activo(scratch_db):
    """Multi-tenant: cada empresa tiene su propia BD y sus propias credenciales.
    get_client() debe leer las del tenant activo (scratch), no las del .env."""
    import main as _m
    from crypto import _encrypt_valor
    from services.configuracion import _seed_integraciones

    _seed_integraciones(scratch_db)  # proveedores/campos/actividades (idempotente)

    tok = _m._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    try:
        with _m._db() as conn:
            prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo='trimble'").fetchone()
            vals = {"username": "u_tenant", "password": "p_tenant", "customer": "c_tenant"}
            for clave, valor in vals.items():
                campo = conn.execute("SELECT id FROM sistema.integracion_campos WHERE proveedor_id=? AND clave=?", (prov["id"], clave)).fetchone()
                conn.execute("INSERT INTO sistema.integracion_valores (campo_id, valor) VALUES (?,?) ON CONFLICT (campo_id) DO UPDATE SET valor=EXCLUDED.valor", (campo["id"], _encrypt_valor(valor)))
            conn.commit()

        tc._client_cache.clear()
        tc.get_client()
        assert ("u_tenant", "p_tenant", "c_tenant", tc.config.DEFAULT_TRIMBLE_TERMINAL) in tc._client_cache
    finally:
        _m._tenant_ctx.reset(tok)
        tc._client_cache.clear()

