"""A2 — Anulación de viajes: motivo obligatorio, remove_trips, filtrado en listas,
no editable, reactivar con auditoría.

Cubre los fallos reproducidos contra la API (PR #13).
"""
import pytest
from fastapi import HTTPException

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_anular_sin_motivo_400(scratch_db):
    """Anular un viaje con documentos sin motivo → 400 y no se anula."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, factura) VALUES ('T-NOMOT', 'Entregado', NULL)")
        conn.execute("INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ('T-NOMOT', 'cmr.pdf', 3, 'descargado')")
        with pytest.raises(HTTPException) as e:
            delete_trip("T-NOMOT", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert e.value.status_code == 400
        # No se anuló.
        row = conn.execute("SELECT anulado_at FROM trips WHERE id='T-NOMOT'").fetchone()
        assert row["anulado_at"] is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anular_con_motivo_y_audit(scratch_db):
    """Anular con motivo → anulado_at/por/motivo + auditoría 'anular'."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, factura) VALUES ('T-MOT', 'Entregado', NULL)")
        conn.execute("INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ('T-MOT', 'cmr.pdf', 3, 'descargado')")
        res = delete_trip("T-MOT", motivo="Viaje duplicado", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert res["anulado"] is True
        row = conn.execute("SELECT anulado_at, anulado_por, anulado_motivo FROM trips WHERE id='T-MOT'").fetchone()
        assert row["anulado_at"] is not None
        assert row["anulado_por"] == "test"
        assert row["anulado_motivo"] == "Viaje duplicado"
        audit = conn.execute(
            "SELECT accion FROM sistema.audit_log WHERE tabla='trips' AND registro_id='T-MOT' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None and audit["accion"] == "anular"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anular_enviado_llama_remove_trips(scratch_db, monkeypatch):
    """Anular un viaje 'enviado' (en el terminal) llama a remove_trips antes de anular."""
    import routers.viajes as V

    calls = []

    class _Fake:
        def remove_trips(self, ids):
            calls.append(list(ids))
            return {"ok": True, "status": 200}

    monkeypatch.setattr(V, "get_client", lambda: _Fake())

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, factura) VALUES ('T-ENV', 'enviado', 'F-1')")
        res = V.delete_trip("T-ENV", motivo="duplicado", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert res["anulado"] is True
        assert calls == [["T-ENV"]]
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anulado_no_editable_409(scratch_db):
    """Un viaje anulado no se puede editar (PATCH → 409)."""
    from routers.viajes import update_trip
    from models import TripUpdate

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, anulado_at) VALUES ('T-ANU', 'sin_asignar', now())")
        with pytest.raises(HTTPException) as e:
            update_trip("T-ANU", TripUpdate(cliente="Otro"), conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_reactivar_admin(scratch_db):
    """Reactivar un viaje anulado (admin) → limpia anulado_* y audita 'reactivar'."""
    from routers.viajes import reactivar_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, estado, anulado_at, anulado_por, anulado_motivo) "
            "VALUES ('T-ANU', 'Entregado', now(), 'test', 'duplicado')"
        )
        res = reactivar_trip("T-ANU", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert res["reactivado"] is True
        row = conn.execute("SELECT anulado_at, anulado_por, anulado_motivo FROM trips WHERE id='T-ANU'").fetchone()
        assert row["anulado_at"] is None
        assert row["anulado_por"] is None
        assert row["anulado_motivo"] is None
        audit = conn.execute(
            "SELECT accion FROM sistema.audit_log WHERE tabla='trips' AND registro_id='T-ANU' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None and audit["accion"] == "reactivar"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_reactivar_no_anulado_409(scratch_db):
    """Reactivar un viaje no anulado → 409."""
    from routers.viajes import reactivar_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-VIVO', 'sin_asignar')")
        with pytest.raises(HTTPException) as e:
            reactivar_trip("T-VIVO", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anulado_no_aparece_en_list_trips(scratch_db):
    """Un viaje anulado no sale en /api/trips salvo con ver_anulados=true."""
    from routers.viajes import list_trips

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado) VALUES ('T-VIVO', 'sin_asignar')")
        conn.execute("INSERT INTO trips (id, estado, anulado_at) VALUES ('T-ANU', 'Entregado', now())")
        vivos = {v["id"] for v in list_trips(conn=conn)["viajes"]}
        assert "T-VIVO" in vivos
        assert "T-ANU" not in vivos
        anu = {v["id"] for v in list_trips(ver_anulados=True, conn=conn)["viajes"]}
        assert "T-ANU" in anu
        assert "T-VIVO" not in anu
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_validar_sin_solape_anulado(scratch_db):
    """Un hueco ocupado solo por un anulado no bloquea /validar."""
    from routers.planificacion import validar, ValidarRequest

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, estado, terminal, fecha_esperada_carga, fecha_esperada_descarga, anulado_at) "
            "VALUES ('T-ANU', 'sin_asignar', 'VH-TRAC', '2026-09-26T08:00', '2026-09-26T10:00', now())"
        )
        r = validar(ValidarRequest(trip_id="T-NEW", codigo="VH-TRAC", inicio="2026-09-26T08:00", fin="2026-09-26T10:00"), conn=conn)
        assert r["ok"] is True
        assert not any(b["tipo"] == "tractora_solapada" for b in r["bloqueos"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_mover_anulado_409(scratch_db):
    from routers.planificacion import mover, MoverRequest

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, anulado_at) VALUES ('T-ANU', 'sin_asignar', now())")
        with pytest.raises(HTTPException) as e:
            mover(MoverRequest(trip_id="T-ANU", codigo="VH-X"), conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_enviar_anulado_409(scratch_db):
    from routers.viajes import enviar_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, terminal, anulado_at) VALUES ('T-ANU', 'sin_asignar', 'VH-X', now())")
        with pytest.raises(HTTPException) as e:
            enviar_trip("T-ANU", conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_enviar_mensaje_anulado_409(scratch_db):
    from routers.mensajeria import send_trip_mensaje
    from models import SendMensajeRequest

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, terminal, anulado_at) VALUES ('T-ANU', 'sin_asignar', 'VH-X', now())")
        with pytest.raises(HTTPException) as e:
            send_trip_mensaje("T-ANU", SendMensajeRequest(subject="h", body="h"), conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_enviar_questionpath_anulado_409(scratch_db):
    from routers.mensajeria import send_trip_questionpath

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, terminal, anulado_at) VALUES ('T-ANU', 'sin_asignar', 'VH-X', now())")
        with pytest.raises(HTTPException) as e:
            send_trip_questionpath("T-ANU", {"body": "<Report/>", "messagetype": "X"}, conn=conn)
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_duplicar_anulado_nace_sin_anular(scratch_db):
    from routers.viajes import duplicar_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, estado, cliente, precio, iva, payload, anulado_at) "
            "VALUES ('T-ANU', 'Entregado', 'Cliente', 100, 21, '{}', now())"
        )
        r = duplicar_trip("T-ANU")
        assert r["ok"] is True
        new_id = r["trip_id"]
        row = conn.execute("SELECT anulado_at FROM trips WHERE id=?", (new_id,)).fetchone()
        assert row is not None and row["anulado_at"] is None
    finally:
        conn.execute("DELETE FROM operaciones.trips WHERE codigo LIKE 'VIAJE-%%' OR codigo='T-ANU'")
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anular_borra_borrador(scratch_db):
    """Anular un viaje borra su borrador (y sus líneas) en la misma transacción."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, factura) VALUES ('T-BR', 'Entregado', NULL)")
        conn.execute("INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ('T-BR', 'cmr-br.pdf', 3, 'descargado')")
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-09-01', 'T-BR', 'borrador', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO finanzas.factura_lineas (factura_id, trip_id, concepto, base, iva, cuota_iva, total) "
            "VALUES (?, 'T-BR', 'ruta', 100, 21, 21, 121)", (fid,)
        )
        res = delete_trip("T-BR", motivo="prueba", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert res["anulado"] is True
        assert conn.execute("SELECT 1 FROM finanzas.facturas WHERE id=?", (fid,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM finanzas.factura_lineas WHERE factura_id=?", (fid,)).fetchone() is None
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_anular_con_emitida_409(scratch_db):
    """Anular un viaje con factura EMITIDA → 409 (primero rectificativa)."""
    from routers.viajes import delete_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, factura) VALUES ('T-EM', 'Entregado', 'F-2026-0001')")
        conn.execute("INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ('T-EM', 'cmr-em.pdf', 3, 'descargado')")
        conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('F-2026-0001', '2026-09-01', 'T-EM', 'emitida', 100, 21, 21, 121, '')"
        )
        with pytest.raises(HTTPException) as e:
            delete_trip("T-EM", motivo="prueba", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert e.value.status_code == 409
        assert "rectificativa" in str(e.value.detail)
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_borradores_excluyen_anulado(scratch_db):
    """Un borrador de un viaje anulado no sale en /api/contabilidad/borradores."""
    from routers.contabilidad import contabilidad_borradores

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, anulado_at) VALUES ('T-ANU', 'Entregado', now())")
        conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-09-01', 'T-ANU', 'borrador', 100, 21, 21, 121, '')"
        )
        r = contabilidad_borradores(user={"rol": "admin"}, conn=conn)
        assert all(b["trip_id"] != "T-ANU" for b in r["borradores"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_emitir_borrador_anulado_409(scratch_db):
    """Emitir un borrador cuyo viaje está anulado → 409."""
    from routers.contabilidad import contabilidad_emitir_borrador

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, cliente, precio, iva, anulado_at) VALUES ('T-ANU', 'Entregado', 'Cliente', 100, 21, now())")
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-09-01', 'T-ANU', 'borrador', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        with pytest.raises(HTTPException) as e:
            contabilidad_emitir_borrador(fid, {"force": True}, user={"rol": "admin"})
        assert e.value.status_code == 409
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_reactivar_requiere_reenvio(scratch_db):
    """Reactivar un viaje anulado que estaba 'enviado' → sin_asignar + pendiente_reenvio."""
    from routers.viajes import reactivar_trip

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO trips (id, estado, terminal, fecha_esperada_carga, fecha_esperada_descarga, payload, anulado_at) "
            "VALUES ('T-ENV', 'enviado', 'VH-X', '2026-09-26T08:00', '2026-09-26T10:00', '{}', now())"
        )
        r = reactivar_trip("T-ENV", user={"rol": "admin", "usuario": "test"}, conn=conn)
        assert r["reactivado"] is True
        assert r["requiere_reenvio"] is True
        row = conn.execute("SELECT estado, terminal, payload FROM trips WHERE id='T-ENV'").fetchone()
        assert row["estado"] == "sin_asignar"
        assert row["terminal"] == "VH-X"  # conserva tractora
        import json as _json
        assert _json.loads(row["payload"] or "{}").get("pendiente_reenvio") is True
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_explotacion_excluye_anulado(scratch_db):
    """El KPI de explotación no cuenta km/ingresos de viajes anulados."""
    from routers.contabilidad import contabilidad_explotacion_vehiculos

    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO trips (id, estado, terminal, precio, km_total, anulado_at) VALUES ('T-VIVO', 'Entregado', 'VH-X', 100, 50, NULL)")
        conn.execute("INSERT INTO trips (id, estado, terminal, precio, km_total, anulado_at) VALUES ('T-ANU', 'Entregado', 'VH-X', 100, 50, now())")
        r = contabilidad_explotacion_vehiculos(conn=conn)
        v = next((x for x in r["vehiculos"] if x["vehiculo"] == "VH-X"), None)
        assert v is not None
        assert v["ingresos"] == 100.0
        assert v["km"] == 50.0
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
