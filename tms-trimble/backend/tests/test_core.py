"""Tests de las funciones puras del núcleo: estados, peajes, importes y numeración.

Sin BD: ejercitan lógica de dinero y de transición de estados que es crítica.
"""
import types

import main


class TestEstados:
    def test_map_estado_flujo_trimble(self):
        assert main._map_estado("llegada_origen") == "Llegada_Origen"
        assert main._map_estado("cargando") == "Cargando"
        assert main._map_estado("en_transito") == "En_Transito"
        assert main._map_estado("llegada_destino") == "Llegada_Destino"
        assert main._map_estado("descargando") == "Descargando"

    def test_map_estado_finales(self):
        for e in ("finalizado", "finished", "entregado", "delivered"):
            assert main._map_estado(e) == "Entregado"

    def test_map_estado_incidencia_y_cancelado(self):
        for e in ("error", "rechazado", "refused", "incidencia"):
            assert main._map_estado(e) == "Incidencia"
        for e in ("cancelado", "canceled"):
            assert main._map_estado(e) == "Cancelado"

    def test_map_estado_en_curso(self):
        for e in ("recibido", "en curso", "en tránsito", "asignado"):
            assert main._map_estado(e) == "En Tránsito"

    def test_map_estado_default(self):
        assert main._map_estado("") == "Pendiente"
        assert main._map_estado("cosa_rara") == "Pendiente"
        assert main._map_estado(None) == "Pendiente"

    def test_progreso(self):
        assert main._progreso("Entregado") == 100
        assert main._progreso("En Tránsito") == 50
        assert main._progreso("Cargando") == 0

    def test_estado_desde_codigo_numerico(self):
        assert main._estado_desde_codigo("1") == "Llegada_Origen"
        assert main._estado_desde_codigo("2") == "Cargando"
        assert main._estado_desde_codigo("3") == "En_Transito"
        assert main._estado_desde_codigo("6") == "Entregado"

    def test_estado_desde_codigo_alias(self):
        assert main._estado_desde_codigo("cargando") == "Cargando"
        assert main._estado_desde_codigo("entregado") == "Entregado"

    def test_estado_desde_codigo_desconocido(self):
        assert main._estado_desde_codigo("999") == ""
        assert main._estado_desde_codigo("") == ""
        assert main._estado_desde_codigo(None) == ""


class TestPeaje:
    def test_autopistas_liberadas_no_cuentan(self):
        for ref in ("AP-1", "AP-2", "AP-4", "AP-7"):
            assert main._is_toll_step({"ref": ref}) is False

    def test_autopistas_de_peaje(self):
        for ref in ("AP-6", "AP-8", "AP-15", "R-4", "R-2"):
            assert main._is_toll_step({"ref": ref}) is True

    def test_peaje_por_nombre(self):
        assert main._is_toll_step({"name": "Autopista AP-8"}) is True
        assert main._is_toll_step({"name": "Autopista AP-2"}) is False  # liberada

    def test_sin_via_de_peaje(self):
        assert main._is_toll_step({}) is False
        assert main._is_toll_step({"ref": "A-2", "name": "Autovía"}) is False


class TestImportes:
    def test_sin_iva(self):
        v = types.SimpleNamespace(precio=1000, gastos=0, iva=0)
        assert main._calcular_importes(v) == (1000.0, 0.0, 1000.0, 0.0, 1000.0, 0.0)

    def test_con_iva_21(self):
        v = types.SimpleNamespace(precio=1210, gastos=100, iva=21)
        precio, gastos, margen, iva_pct, base, cuota = main._calcular_importes(v)
        assert precio == 1210.0
        assert gastos == 100.0
        assert margen == 1110.0
        assert iva_pct == 21.0
        assert base == 1000.0
        assert cuota == 210.0

    def test_precio_cero(self):
        v = types.SimpleNamespace(precio=0, gastos=0, iva=21)
        precio, gastos, margen, iva_pct, base, cuota = main._calcular_importes(v)
        assert precio == 0.0
        assert base == 0.0
        assert cuota == 0.0


class _FakeConn:
    def __init__(self, refs):
        self._refs = refs

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return [{"referencia": r} for r in self._refs]


class TestReferencia:
    def test_primera_referencia(self):
        assert main._next_referencia(_FakeConn([])) == "V-0001"

    def test_siguiente_referencia(self):
        assert main._next_referencia(_FakeConn(["V-0001", "V-0005", "V-0003"])) == "V-0006"

    def test_referencia_con_huecos_y_basura(self):
        assert main._next_referencia(_FakeConn(["V-0009", "", "V-0010"])) == "V-0011"
