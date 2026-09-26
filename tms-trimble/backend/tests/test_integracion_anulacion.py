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
