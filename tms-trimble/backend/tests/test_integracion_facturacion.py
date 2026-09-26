"""Facturación (Fase 6): borradores, agrupada, fecha, cobro, documentación.

Cubre la revisión del PR #20 contra una BD scratch real.
"""
import datetime

import pytest
from fastapi import HTTPException

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    # Autocommit OFF: el trigger de cuadre de asientos es DEFERRABLE INITIALLY DEFERRED
    # y con autocommit cada apunte dispara la comprobación a medio asiento.
    c.set_autocommit(False)
    # Sembrar el plan contable (apuntes tiene FK a finanzas.cuentas.codigo).
    for cod, nom, grupo, tipo, orden in main._PLAN_CONTABLE:
        c.execute(
            "INSERT INTO finanzas.cuentas (codigo, nombre, grupo, tipo, orden) "
            "VALUES (?,?,?,?,?) ON CONFLICT (codigo) DO NOTHING",
            (cod, nom, grupo, tipo, orden),
        )
    c.commit()
    return tok, c


def _trip(conn, codigo, estado="Entregado", cliente="Cliente A", cliente_id=1, precio=200.0,
          iva=21.0, creado="2026-09-26"):
    conn.execute(
        "INSERT INTO operaciones.trips (codigo, estado, cliente, cliente_id, precio, iva, creado) "
        "VALUES (?,?,?,?,?,?,?)",
        (codigo, estado, cliente, cliente_id, precio, iva, creado),
    )


def _hoy():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("Europe/Madrid")).date().isoformat()


@pytest.mark.integration
def test_facturables_entregado_sin_emitida(scratch_db):
    """Un Entregado sin factura (o solo borrador) aparece; con emitida no."""
    from routers.contabilidad import contabilidad_facturables

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-ENTREGADO", estado="Entregado")
        _trip(conn, "T-BORRADOR", estado="Entregado")
        _trip(conn, "T-EMITIDA", estado="Entregado")
        _trip(conn, "T-TRANSITO", estado="En_Transito")
        # Borrador (no emitida) sobre T-BORRADOR.
        conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-09-01', 'T-BORRADOR', 'borrador', 200, 21, 42, 242, '')"
        )
        # Emitida sobre T-EMITIDA.
        conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('F-2026-0001', '2026-09-01', 'T-EMITIDA', 'emitida', 200, 21, 42, 242, '')"
        )
        ids = {v["id"] for v in contabilidad_facturables(conn)["viajes"]}
        assert "T-ENTREGADO" in ids
        assert "T-BORRADOR" in ids  # borrador aún no emitido → sigue facturable
        assert "T-EMITIDA" not in ids
        assert "T-TRANSITO" not in ids
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_facturables_excluye_anulados(scratch_db):
    """Un viaje anulado (anulado_at NOT NULL) no aparece en facturables."""
    from routers.contabilidad import contabilidad_facturables

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-ANULADO", estado="Entregado")
        _trip(conn, "T-VIVO", estado="Entregado")
        conn.execute("UPDATE operaciones.trips SET anulado_at='2026-09-26' WHERE codigo='T-ANULADO'")
        ids = {v["id"] for v in contabilidad_facturables(conn)["viajes"]}
        assert "T-ANULADO" not in ids
        assert "T-VIVO" in ids
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_agrupada_clientes_distintos_409(scratch_db):
    """Agrupar viajes de clientes distintos → 409."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-A", cliente_id=1, cliente="A")
        _trip(conn, "T-B", cliente_id=2, cliente="B")
        with pytest.raises(HTTPException) as e:
            contabilidad_factura_agrupada({"trip_ids": ["T-A", "T-B"], "force": True}, conn)
        assert e.value.status_code == 409
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_agrupada_sin_cliente_400(scratch_db):
    """Viaje sin cliente (ni cliente_id) → 400."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-SINC", cliente_id=None, cliente=None)
        with pytest.raises(HTTPException) as e:
            contabilidad_factura_agrupada({"trip_ids": ["T-SINC"], "force": True}, conn)
        assert e.value.status_code == 400
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_agrupada_iva_distinto_409(scratch_db):
    """IVA distinto entre viajes → 409."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-21", cliente_id=1, iva=21)
        _trip(conn, "T-0", cliente_id=1, iva=0)
        with pytest.raises(HTTPException) as e:
            contabilidad_factura_agrupada({"trip_ids": ["T-21", "T-0"], "force": True}, conn)
        assert e.value.status_code == 409
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_agrupada_fecha_hoy_y_fecha_operacion(scratch_db):
    """Viaje de 2025 emitido hoy → F-<año actual>-NNNN, fecha hoy y fecha_operacion 2025."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-2025", creado="2025-06-01", precio=100, iva=21, cliente_id=1)
        r = contabilidad_factura_agrupada({"trip_ids": ["T-2025"], "force": True}, conn)
        anio = _hoy()[:4]
        assert r["factura"].startswith(f"F-{anio}-")
        row = conn.execute("SELECT fecha, fecha_operacion FROM finanzas.facturas WHERE id=?", (r["factura_id"],)).fetchone()
        assert row["fecha"] == _hoy()
        assert row["fecha_operacion"] == "2025-06-01"
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_agrupada_borra_borrador_previo(scratch_db):
    """Agrupar sobre un viaje con borrador previo borra el borrador y sus líneas."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-DRAFT", creado="2026-01-01", precio=100, iva=21, cliente_id=1)
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-01-01', 'T-DRAFT', 'borrador', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO finanzas.factura_lineas (factura_id, trip_id, concepto, base, iva, cuota_iva, total) "
            "VALUES (?, 'T-DRAFT', 'ruta', 100, 21, 21, 121)", (fid,)
        )
        r = contabilidad_factura_agrupada({"trip_ids": ["T-DRAFT"], "force": True}, conn)
        assert conn.execute("SELECT 1 FROM finanzas.facturas WHERE id=?", (fid,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM finanzas.factura_lineas WHERE factura_id=?", (fid,)).fetchone() is None
        # La nueva factura emitida existe.
        assert conn.execute("SELECT 1 FROM finanzas.facturas WHERE id=?", (r["factura_id"],)).fetchone() is not None
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_cobrar_borrador_409(scratch_db):
    """Cobrar un borrador → 409 y ningún asiento creado."""
    from routers.contabilidad import contabilidad_cobrar_factura

    tok, conn = _conn(scratch_db)
    try:
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2026-01-01', 'T-X', 'borrador', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        with pytest.raises(HTTPException) as e:
            contabilidad_cobrar_factura(fid, {}, conn)
        assert e.value.status_code == 409
        n = conn.execute(
            "SELECT count(*) AS n FROM finanzas.asientos WHERE origen='cobro'"
        ).fetchone()["n"]
        assert n == 0
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_cobrar_emitida(scratch_db):
    """Cobrar una emitida → asiento 572/430 con fecha_cobro y estado cobrada."""
    from routers.contabilidad import contabilidad_cobrar_factura

    tok, conn = _conn(scratch_db)
    try:
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('F-2026-0001', '2026-01-01', 'T-Y', 'emitida', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        r = contabilidad_cobrar_factura(fid, {"fecha_cobro": "2026-09-26"}, conn)
        row = conn.execute("SELECT estado, fecha_cobro FROM finanzas.facturas WHERE id=?", (fid,)).fetchone()
        assert row["estado"] == "cobrada"
        assert row["fecha_cobro"] == "2026-09-26"
        ap = conn.execute(
            "SELECT cuenta, debe, haber FROM finanzas.apuntes WHERE asiento_id=? ORDER BY cuenta",
            (r["asiento_id"],),
        ).fetchall()
        cuentas = {a["cuenta"] for a in ap}
        assert "572" in cuentas and "430" in cuentas
        # El asiento usa la fecha de cobro.
        a = conn.execute("SELECT fecha FROM finanzas.asientos WHERE id=?", (r["asiento_id"],)).fetchone()
        assert a["fecha"] == "2026-09-26"
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_documentacion_incompleta_409_y_force(scratch_db):
    """Documentación incompleta → 409 salvo force=true."""
    from routers.contabilidad import contabilidad_factura_agrupada

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-DOC", creado="2026-01-01", precio=100, iva=21, cliente_id=1)
        # Sin docs → faltan CMR/carta_porte (default).
        with pytest.raises(HTTPException) as e:
            contabilidad_factura_agrupada({"trip_ids": ["T-DOC"], "force": False}, conn)
        assert e.value.status_code == 409
        # Con force=true → pasa.
        r = contabilidad_factura_agrupada({"trip_ids": ["T-DOC"], "force": True}, conn)
        assert r["ok"] is True
    finally:
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_emitir_borrador_fecha_hoy(scratch_db):
    """Emitir un borrador asigna número con el año de hoy y fecha de expedición hoy."""
    from routers.contabilidad import contabilidad_emitir_borrador

    tok, conn = _conn(scratch_db)
    try:
        _trip(conn, "T-E", creado="2025-01-01", precio=100, iva=21, cliente_id=1)
        fid = conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, trip_id, estado, base, iva, cuota_iva, total, creado) "
            "VALUES ('', '2025-01-01', 'T-E', 'borrador', 100, 21, 21, 121, '') RETURNING id"
        ).fetchone()["id"]
        conn.commit()  # emitir_borrador abre su propia conexión y debe ver el borrador
        r = contabilidad_emitir_borrador(fid, {"force": True}, user={"rol": "admin"})
        anio = _hoy()[:4]
        assert r["factura"].startswith(f"F-{anio}-")
        row = conn.execute("SELECT estado, fecha, fecha_operacion FROM finanzas.facturas WHERE id=?", (fid,)).fetchone()
        assert row["estado"] == "emitida"
        assert row["fecha"] == _hoy()
        assert row["fecha_operacion"] == "2025-01-01"
    finally:
        main._tenant_ctx.reset(tok)
