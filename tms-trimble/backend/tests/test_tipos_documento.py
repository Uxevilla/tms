"""Tests de clasificación de documentos (services/tipos_documento.py)."""
from services.tipos_documento import clasificar_documento, docs_requeridos_default, checklist_facturacion


def test_cmr_por_nombre():
    assert clasificar_documento(ftype=99, name="FOTO_CMR.jpg") == "CMR"


def test_carta_porte_por_nombre():
    assert clasificar_documento(ftype=99, name="carta_de_porte.pdf") == "carta_porte"
    assert clasificar_documento(ftype=99, name="porte.pdf") == "carta_porte"


def test_tacografo_por_ftype():
    assert clasificar_documento(ftype=0, name="archivo.bin") == "tacografo"
    assert clasificar_documento(ftype=1, name="archivo.bin") == "tacografo"
    assert clasificar_documento(ftype=2, name="archivo.bin") == "tacografo"


def test_firma():
    assert clasificar_documento(ftype=99, name="firma_conductor.png") == "firma"
    assert clasificar_documento(ftype=99, name="cliente_signature.png") == "firma"
    assert clasificar_documento(ftype=99, name="cliente_sign.png") == "firma"


def test_escaner_multipagina():
    assert clasificar_documento(ftype=99, name="multipage_scan.pdf") == "escaner"
    assert clasificar_documento(ftype=99, name="multi_page.pdf") == "escaner"


def test_ticket():
    assert clasificar_documento(ftype=99, name="ticket_combustible.jpg") == "ticket"


def test_desconocido_otro():
    assert clasificar_documento(ftype=99, name="foto_generica.jpg") == "otro"
    assert clasificar_documento(ftype=99, name="") == "otro"


def test_albaran():
    assert clasificar_documento(ftype=99, name="albaran_firmado.pdf") == "albaran"
    assert clasificar_documento(ftype=99, name="delivery_note.pdf") == "albaran"


def test_docs_requeridos_default():
    assert docs_requeridos_default() == ["CMR", "carta_porte"]


def test_checklist_ok():
    out = checklist_facturacion(["CMR", "carta_porte"], ["CMR", "carta_porte"])
    assert out["ok"] is True
    assert out["faltan"] == []


def test_checklist_con_faltantes():
    out = checklist_facturacion(["CMR"], ["CMR", "carta_porte"])
    assert out["ok"] is False
    assert out["faltan"] == ["carta_porte"]


def test_checklist_faltantes_orden_preservado():
    out = checklist_facturacion([], ["carta_porte", "CMR", "ticket"])
    assert out["faltan"] == ["carta_porte", "CMR", "ticket"]


def test_checklist_normaliza_tildes():
    assert checklist_facturacion(["Cártá"], ["carta"])["ok"] is True


def test_checklist_normaliza_guiones_espacios():
    assert checklist_facturacion(["carta  -   porte"], ["carta-porte"])["ok"] is True


def test_checklist_normaliza_mayusculas():
    assert checklist_facturacion(["CMR"], ["cmr"])["ok"] is True


def test_clasificar_con_mapeo_manda_sobre_nombre():
    from services.tipos_documento import clasificar_con_mapeo
    # nombre 'pod-*.png' no da pista; el mapeo a CMR manda.
    assert clasificar_con_mapeo(3, "pod-1428065934030.png", "CMR") == "CMR"
    # sin mapeo → cae al nombre/ftype.
    assert clasificar_con_mapeo(3, "cmr_scan.png", "") == "CMR"


def test_sugerir_tipo_documento():
    from services.tipos_documento import sugerir_tipo_documento
    assert sugerir_tipo_documento("docuscan", "Firma del CMR") == "CMR"
    assert sugerir_tipo_documento("photo", "Carta de porte escaneada") == "carta_porte"
    assert sugerir_tipo_documento("signature", "Firma del conductor") == "firma"
    assert sugerir_tipo_documento("text", "Observaciones") == ""
