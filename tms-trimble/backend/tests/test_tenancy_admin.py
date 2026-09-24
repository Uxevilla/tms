"""Reseteo de contraseña del admin en _seed_rbac (Fase 0, fix 2).

La contraseña del admin solo se restablece desde DEFAULT_ADMIN_PASSWORD cuando
TMS_RESET_ADMIN=1. En un arranque normal, el INSERT ... ON CONFLICT DO NOTHING crea
el admin la primera vez y el UPDATE posterior lleva la cláusula debe_cambiar_clave,
de modo que no pisa la contraseña de un admin que ya la cambió.
"""
import tenancy


class _Cur:
    def __init__(self, executed):
        self._executed = executed

    def execute(self, sql, params=None):
        self._executed.append(sql)


class _Conn:
    def __init__(self, executed):
        self._executed = executed

    def cursor(self):
        return _Cur(self._executed)

    def commit(self):
        pass

    def close(self):
        pass


def _ejecutar(monkeypatch, password, reset_admin):
    executed = []
    monkeypatch.setattr(tenancy.psycopg2, "connect", lambda *a, **k: _Conn(executed))
    monkeypatch.setattr(tenancy, "DEFAULT_ADMIN_PASSWORD", password)
    monkeypatch.setattr(tenancy, "DEFAULT_ADMIN_USER", "admin")
    if reset_admin:
        monkeypatch.setenv("TMS_RESET_ADMIN", "1")
    else:
        monkeypatch.delenv("TMS_RESET_ADMIN", raising=False)
    tenancy._seed_rbac("tms")
    return [s for s in executed if s.strip().upper().startswith("UPDATE SISTEMA.USUARIOS")]


def test_seed_rbac_no_reset_sin_flag(monkeypatch):
    updates = _ejecutar(monkeypatch, password="secreta", reset_admin=False)
    assert len(updates) == 1
    # Sin flag → el UPDATE NO es un reset incondicional: conserva debe_cambiar_clave.
    assert "debe_cambiar_clave IS NULL" in updates[0]


def test_seed_rbac_reset_con_flag(monkeypatch):
    updates = _ejecutar(monkeypatch, password="secreta", reset_admin=True)
    assert len(updates) == 1
    # Con flag → reset incondicional (sin cláusula debe_cambiar_clave).
    assert "debe_cambiar_clave IS NULL" not in updates[0]
