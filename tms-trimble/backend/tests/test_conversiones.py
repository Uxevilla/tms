"""Tests unitarios para el módulo conversiones."""

from __future__ import annotations

import pytest
from datetime import date

from services.conversiones import convertir_valor


class TestConvertirValor:
    """Tests para la función principal convertir_valor."""

    # --- Casos base ---
    def test_none_devuelve_none_y_vacio(self):
        assert convertir_valor(None, "text") == (None, "")
        assert convertir_valor("", "text") == (None, "")

    def test_valuetype_desconocido_devuelve_original(self):
        assert convertir_valor("foo", "desconocido") == ("foo", "foo")
        assert convertir_valor("123", "weird") == ("123", "123")

    # --- text ---
    def test_text_sin_transformacion(self):
        assert convertir_valor("hola mundo", "text") == ("hola mundo", "hola mundo")
        assert convertir_valor("123", "text") == ("123", "123")

    # --- number / numeric ---
    def test_number_entero_devuelve_int_y_formato_es(self):
        typed, readable = convertir_valor("1234", "number")
        assert typed == 1234
        assert readable == "1.234"

    def test_number_decimal_devuelve_float_y_formato_es(self):
        typed, readable = convertir_valor("1234.5", "number")
        assert typed == 1234.5
        assert readable == "1.234,5"

    def test_number_decimal_muchos_decimales_redondea_a_dos(self):
        typed, readable = convertir_valor("1234.567", "number")
        assert typed == 1234.567
        assert readable == "1.234,57"

    def test_number_negativo(self):
        typed, readable = convertir_valor("-1234.5", "number")
        assert typed == -1234.5
        assert readable == "-1.234,5"

    def test_number_invalido_devuelve_original(self):
        assert convertir_valor("no-es-numero", "number") == ("no-es-numero", "no-es-numero")

    def test_numeric_alias_de_number(self):
        assert convertir_valor("1000", "numeric") == (1000, "1.000")

    # --- date ---
    def test_date_iso_yyyy_mm_dd(self):
        typed, readable = convertir_valor("2024-03-15", "date")
        assert typed == date(2024, 3, 15)
        assert readable == "15/03/2024"

    def test_date_dd_mm_yyyy(self):
        typed, readable = convertir_valor("15/03/2024", "date")
        assert typed == date(2024, 3, 15)
        assert readable == "15/03/2024"

    def test_date_dd_mm_yyyy_con_guion(self):
        typed, readable = convertir_valor("15-03-2024", "date")
        assert typed == date(2024, 3, 15)
        assert readable == "15/03/2024"

    def test_date_con_inputmask_dd_mm_yyyy_prioriza_formato(self):
        typed, readable = convertir_valor("15/03/2024", "date", inputmask="dd/MM/yyyy")
        assert typed == date(2024, 3, 15)
        assert readable == "15/03/2024"

    def test_date_yyyymmdd_sin_separadores(self):
        typed, readable = convertir_valor("20240315", "date")
        assert typed == date(2024, 3, 15)
        assert readable == "15/03/2024"

    def test_date_invalida_devuelve_original(self):
        assert convertir_valor("no-es-fecha", "date") == ("no-es-fecha", "no-es-fecha")
        assert convertir_valor("32/13/2024", "date") == ("32/13/2024", "32/13/2024")

    # --- time ---
    def test_time_hh_mm(self):
        typed, readable = convertir_valor("14:30", "time")
        assert typed == "14:30"
        assert readable == "14:30"

    def test_time_hh_mm_ss_se_trunca_a_hh_mm(self):
        typed, readable = convertir_valor("14:30:45", "time")
        assert typed == "14:30"
        assert readable == "14:30"

    def test_time_hora_sin_cero_izquierda_se_normaliza(self):
        typed, readable = convertir_valor("9:05", "time")
        assert typed == "09:05"
        assert readable == "09:05"

    def test_time_invalido_devuelve_original(self):
        assert convertir_valor("no-es-hora", "time") == ("no-es-hora", "no-es-hora")

    # --- boolean / checkbox ---
    @pytest.mark.parametrize("val", ["true", "True", "TRUE", "1", "yes", "on", "si", "sí"])
    def test_boolean_true_variaciones(self, val):
        typed, readable = convertir_valor(val, "boolean")
        assert typed is True
        assert readable == "Sí"

    @pytest.mark.parametrize("val", ["false", "False", "0", "no", "off"])
    def test_boolean_false_variaciones(self, val):
        typed, readable = convertir_valor(val, "boolean")
        assert typed is False
        assert readable == "No"

    def test_checkbox_igual_que_boolean(self):
        assert convertir_valor("true", "checkbox") == (True, "Sí")
        assert convertir_valor("false", "checkbox") == (False, "No")

    # --- select ---
    def test_select_mantiene_valor_option_id(self):
        assert convertir_valor("opt-123", "select") == ("opt-123", "opt-123")

    # --- inputmask ---
    def test_inputmask_digitos_y_letras(self):
        # Máscara "ES 0000 AAA" → "ES 1234 ABC" (la "ES " es literal de prefijo).
        typed, readable = convertir_valor("1234ABC", "text", inputmask="ES 0000 AAA")
        assert typed == "1234ABC"
        assert readable == "ES 1234 ABC"

    def test_inputmask_solo_digitos(self):
        typed, readable = convertir_valor("12345", "text", inputmask="00-000")
        assert typed == "12345"
        assert readable == "12-345"

    def test_inputmask_no_cuadra_devuelve_original(self):
        # Texto no tiene suficientes dígitos para la máscara
        typed, readable = convertir_valor("12", "text", inputmask="0000")
        assert typed == "12"
        assert readable == "12"

    def test_inputmask_letras_en_lugar_de_digitos_no_cuadra(self):
        typed, readable = convertir_valor("ABCD", "text", inputmask="0000")
        assert typed == "ABCD"
        assert readable == "ABCD"

    def test_inputmask_caracteres_literales_se_mantienen(self):
        typed, readable = convertir_valor("AB12CD", "text", inputmask="AA-00-AA")
        assert typed == "AB12CD"
        assert readable == "AB-12-CD"


class TestFuncionesAuxiliares:
    """Tests para funciones auxiliares internas (vía convertir_valor)."""

    def test_formatear_numero_es_grandes(self):
        _, readable = convertir_valor("1000000", "number")
        assert readable == "1.000.000"

    def test_formatear_numero_es_decimales(self):
        _, readable = convertir_valor("1234.5", "number")
        assert readable == "1.234,5"

    def test_parsear_fecha_febrero_29_bisiesto(self):
        typed, _ = convertir_valor("2024-02-29", "date")
        assert typed == date(2024, 2, 29)

    def test_parsear_fecha_febrero_29_no_bisiesto_invalido(self):
        assert convertir_valor("2023-02-29", "date") == ("2023-02-29", "2023-02-29")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])