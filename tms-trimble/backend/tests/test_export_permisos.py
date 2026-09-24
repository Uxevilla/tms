"""Permisos de la exportación a Excel (Fase 0, fix 1).

Los datos sensibles (facturas, asientos, balance, pyg, liquidaciones, empleados,
nominas, ausencias, costes_fijos) solo pueden descargarse como admin. Un dispatcher
recibe 403; los listados operativos (trips, vehiculos, clientes, ...) siguen visibles.
"""
import pytest
from fastapi import HTTPException

from routers.integraciones import export_xlsx, _EXPORT_SOLO_ADMIN


class _Cur:
    """Cursor simulado: fetchall vacío (no se necesitan filas para probar permisos)."""

    def execute(self, *a, **k):
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _Conn:
    def execute(self, *a, **k):
        return _Cur()


@pytest.mark.parametrize("tipo", sorted(_EXPORT_SOLO_ADMIN))
def test_export_sensible_dispatcher_bloqueado(tipo):
    with pytest.raises(HTTPException) as e:
        export_xlsx(tipo=tipo, user={"rol": "dispatcher"}, conn=_Conn())
    assert e.value.status_code == 403


@pytest.mark.parametrize("tipo", sorted(_EXPORT_SOLO_ADMIN))
def test_export_sensible_admin_permitido(tipo):
    # admin + sensible → no 403; construye el Excel y devuelve 200.
    res = export_xlsx(tipo=tipo, user={"rol": "admin"}, conn=_Conn())
    assert res.status_code == 200


@pytest.mark.parametrize(
    "tipo",
    ["trips", "ingresos", "gastos", "clientes", "conductores", "vehiculos",
     "proveedores", "transportistas", "mantenimientos", "direcciones"],
)
def test_export_operativo_dispatcher_permitido(tipo):
    # Datos operativos → visibles para dispatcher (200, no 403).
    res = export_xlsx(tipo=tipo, user={"rol": "dispatcher"}, conn=_Conn())
    assert res.status_code == 200
