"""Integración de mantenimientos (Fase 5 - Taller).

Verifica:
1. Alta de mantenimiento (POST /api/mantenimientos)
2. Edición en línea parcial (PATCH /api/mantenimientos/{id}/campos)
3. Toggle de estado hecho (PATCH /api/mantenimientos/{id} sin body)
"""
import pytest
import datetime

import main


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_alta_mantenimiento(scratch_db):
    """POST /api/mantenimientos crea un mantenimiento y devuelve su id."""
    from routers.flota import add_mantenimiento, list_mantenimientos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        # Crear un vehículo primero
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-001', 'TRIM-001', '1111-AAA', 'tractora', true)"
        )

        m = Mantenimiento(
            vehiculo_id="VH-MANT-001",
            tipo="revision",
            fecha="2026-01-15",
            km=100000,
            coste=450.00,
            notas="Revisión 100k km",
            hecho=False,
        )
        res = add_mantenimiento(m, conn=conn)

        assert res["ok"] is True
        assert "id" in res
        mid = res["id"]

        # Verificar en la BD
        row = conn.execute(
            "SELECT id, vehiculo_id, tipo, fecha, km, coste, notas, hecho FROM flota.mantenimientos WHERE id = ?",
            (mid,),
        ).fetchone()
        assert row is not None
        assert row["vehiculo_id"] == "VH-MANT-001"
        assert row["tipo"] == "revision"
        assert row["fecha"] == "2026-01-15"
        assert row["km"] == 100000
        assert row["coste"] == 450.00
        assert row["notas"] == "Revisión 100k km"
        assert row["hecho"] is False

        # Verificar que aparece en el listado
        lista = list_mantenimientos(conn=conn)
        assert "mantenimientos" in lista
        assert any(m["id"] == mid for m in lista["mantenimientos"])
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_edicion_en_linea_campos_mantenimiento(scratch_db):
    """PATCH /api/mantenimientos/{id}/campos permite editar campos no críticos parcialmente."""
    from routers.flota import add_mantenimiento, upd_mantenimiento_campos, list_mantenimientos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        # Crear vehículo y mantenimiento
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-002', 'TRIM-002', '2222-BBB', 'tractora', true)"
        )

        m = Mantenimiento(
            vehiculo_id="VH-MANT-002",
            tipo="aceite",
            fecha="2026-02-01",
            km=50000,
            coste=120.00,
            notas="Cambio aceite",
            hecho=False,
        )
        res = add_mantenimiento(m, conn=conn)
        mid = res["id"]

        # Editar solo coste y notas (campos permitidos)
        upd_mantenimiento_campos(mid, {"coste": 135.50, "notas": "Cambio aceite + filtro"}, conn=conn)

        # Verificar que se actualizó
        row = conn.execute(
            "SELECT coste, notas FROM flota.mantenimientos WHERE id = ?",
            (mid,),
        ).fetchone()
        assert row["coste"] == 135.50
        assert row["notas"] == "Cambio aceite + filtro"

        # El tipo y fecha no deberían haber cambiado
        row2 = conn.execute(
            "SELECT tipo, fecha FROM flota.mantenimientos WHERE id = ?",
            (mid,),
        ).fetchone()
        assert row2["tipo"] == "aceite"
        assert row2["fecha"] == "2026-02-01"

        # Editar km y fecha_fin
        upd_mantenimiento_campos(mid, {"km": 50100, "fecha_fin": "2026-02-02"}, conn=conn)

        row3 = conn.execute(
            "SELECT km, fecha_fin FROM flota.mantenimientos WHERE id = ?",
            (mid,),
        ).fetchone()
        assert row3["km"] == 50100
        assert row3["fecha_fin"] == "2026-02-02"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_toggle_hecho_mantenimiento(scratch_db):
    """PATCH /api/mantenimientos/{id} sin body invierte el estado 'hecho'."""
    from routers.flota import add_mantenimiento, upd_mantenimiento, list_mantenimientos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-003', 'TRIM-003', '3333-CCC', 'semirremolque', true)"
        )

        m = Mantenimiento(
            vehiculo_id="VH-MANT-003",
            tipo="frenos",
            fecha="2026-03-01",
            km=80000,
            coste=300.00,
            notas="Pastillas frenos",
            hecho=False,
        )
        res = add_mantenimiento(m, conn=conn)
        mid = res["id"]

        # Estado inicial: hecho = False
        row = conn.execute("SELECT hecho FROM flota.mantenimientos WHERE id = ?", (mid,)).fetchone()
        assert row["hecho"] is False

        # Toggle 1: False -> True
        upd_mantenimiento(mid, None, conn=conn)

        row = conn.execute("SELECT hecho FROM flota.mantenimientos WHERE id = ?", (mid,)).fetchone()
        assert row["hecho"] is True

        # Toggle 2: True -> False
        upd_mantenimiento(mid, None, conn=conn)

        row = conn.execute("SELECT hecho FROM flota.mantenimientos WHERE id = ?", (mid,)).fetchone()
        assert row["hecho"] is False
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_list_mantenimientos_filtro_vehiculo(scratch_db):
    """GET /api/mantenimientos?vehiculo_id=... filtra por vehículo."""
    from routers.flota import add_mantenimiento, list_mantenimientos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-004', 'TRIM-004', '4444-DDD', 'tractora', true)"
        )
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-005', 'TRIM-005', '5555-EEE', 'semirremolque', true)"
        )

        # Mantenimiento para VH-MANT-004 (vehiculo_id = codigo, no matrícula)
        m1 = Mantenimiento(
            vehiculo_id="VH-MANT-004",
            tipo="revision",
            fecha="2026-01-10",
            km=10000,
            coste=100.0,
            notas="Veh 1",
            hecho=False,
        )
        add_mantenimiento(m1, conn=conn)

        # Mantenimiento para VH-MANT-005
        m2 = Mantenimiento(
            vehiculo_id="VH-MANT-005",
            tipo="aceite",
            fecha="2026-01-11",
            km=20000,
            coste=200.0,
            notas="Veh 2",
            hecho=False,
        )
        add_mantenimiento(m2, conn=conn)

        # Listar sin filtro -> 2
        todos = list_mantenimientos(conn=conn)
        assert len(todos["mantenimientos"]) == 2

        # Filtrar por VH-MANT-004 -> 1
        filtrados = list_mantenimientos(vehiculo_id="VH-MANT-004", conn=conn)
        assert len(filtrados["mantenimientos"]) == 1
        assert filtrados["mantenimientos"][0]["vehiculo_id"] == "VH-MANT-004"
        assert filtrados["mantenimientos"][0]["matricula"] == "4444-DDD"
        assert filtrados["mantenimientos"][0]["notas"] == "Veh 1"

        # Filtrar por VH-MANT-005 -> 1
        filtrados2 = list_mantenimientos(vehiculo_id="VH-MANT-005", conn=conn)
        assert len(filtrados2["mantenimientos"]) == 1
        assert filtrados2["mantenimientos"][0]["vehiculo_id"] == "VH-MANT-005"
        assert filtrados2["mantenimientos"][0]["notas"] == "Veh 2"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_edicion_campos_solo_allow(scratch_db):
    """PATCH /campos ignora campos que no están en la lista allow."""
    from routers.flota import add_mantenimiento, upd_mantenimiento_campos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-MANT-006', 'TRIM-006', '6666-FFF', 'tractora', true)"
        )

        m = Mantenimiento(
            vehiculo_id="VH-MANT-006",
            tipo="neumaticos",
            fecha="2026-04-01",
            km=60000,
            coste=600.0,
            notas="4 neumáticos",
            hecho=False,
        )
        res = add_mantenimiento(m, conn=conn)
        mid = res["id"]

        # Intentar editar vehiculo_id y tipo (vehiculo_id no está en allow, tipo sí)
        # vehiculo_id debería ignorarse, tipo debería actualizarse
        upd_mantenimiento_campos(mid, {"vehiculo_id": "OTRO-VH", "tipo": "itv"}, conn=conn)

        row = conn.execute(
            "SELECT vehiculo_id, tipo FROM flota.mantenimientos WHERE id = ?",
            (mid,),
        ).fetchone()
        # vehiculo_id no debe cambiar (no está en allow)
        assert row["vehiculo_id"] == "VH-MANT-006"
        # tipo sí debe cambiar (está en allow)
        assert row["tipo"] == "itv"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_mantenimiento_generar_gasto(scratch_db):
    """Completar un mantenimiento con 'generar gasto' → gasto en gastos_vehiculos (cuenta 622)."""
    from routers.flota import add_mantenimiento
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    conn.set_autocommit(False)  # transacción: el asiento 622/472/400 debe cuadrar al commit
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-GASTO', 'T-GASTO', 'MAT-GASTO', 'tractora', true)"
        )
        m = Mantenimiento(
            vehiculo_id="VH-GASTO",
            tipo="revision",
            fecha="2026-01-01",
            km=0,
            coste=0,
            hecho=True,
            generar_gasto=True,
            base_imponible=100.0,
            iva=21.0,
            proveedor_id=None,
        )
        res = add_mantenimiento(m, conn=conn)
        assert res["ok"] is True

        row = conn.execute(
            "SELECT cuenta_contable_gasto, base_imponible, importe_total FROM finanzas.gastos_vehiculos "
            "WHERE vehiculo_id='VH-GASTO'"
        ).fetchone()
        assert row is not None
        assert row["cuenta_contable_gasto"] == "622"
        assert float(row["base_imponible"]) == 100.0
        assert float(row["importe_total"]) == 121.0  # 100 + 21% IVA
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_mantenimiento_generar_gasto_al_editar(scratch_db):
    """Planificar (hecho=false) → editar con Completado+Generar gasto → 1 gasto 622; repetir → sigue 1."""
    from routers.flota import add_mantenimiento, upd_mantenimiento_campos
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    conn.set_autocommit(False)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES ('VH-EDIT', 'T-EDIT', 'MAT-EDIT', 'tractora', true)"
        )
        # Planificar sin completar.
        m = Mantenimiento(vehiculo_id="VH-EDIT", tipo="revision", fecha="2026-02-01",
                          km=0, coste=0, hecho=False)
        mid = add_mantenimiento(m, conn=conn)["id"]
        conn.commit()

        n_gastos = lambda: conn.execute(
            "SELECT count(*) AS n FROM finanzas.gastos_vehiculos "
            "WHERE vehiculo_id='VH-EDIT' AND cuenta_contable_gasto='622'"
        ).fetchone()["n"]
        assert n_gastos() == 0

        # Editar: Completado + Generar gasto → dispara el gasto.
        upd_mantenimiento_campos(mid, {"hecho": True, "generar_gasto": True,
                                       "base_imponible": 200.0, "iva": 21.0}, conn=conn)
        conn.commit()
        assert n_gastos() == 1

        # Repetir la edición → idempotente: sigue 1.
        upd_mantenimiento_campos(mid, {"hecho": True, "generar_gasto": True,
                                       "base_imponible": 200.0, "iva": 21.0}, conn=conn)
        conn.commit()
        assert n_gastos() == 1

        # El gasto_id quedó guardado en el mantenimiento.
        gid = conn.execute("SELECT gasto_id FROM flota.mantenimientos WHERE id=?", (mid,)).fetchone()["gasto_id"]
        assert gid is not None

        # GET /api/mantenimientos incluye gasto_id.
        from routers.flota import list_mantenimientos
        lista = list_mantenimientos(vehiculo_id="VH-EDIT", conn=conn)
        assert lista["mantenimientos"][0]["gasto_id"] == gid
    finally:
        conn.rollback()
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_mantenimiento_vehiculo_obligatorio(scratch_db):
    """POST /api/mantenimientos con vehiculo_id vacío → 422 y 0 gastos creados."""
    from fastapi import HTTPException
    from routers.flota import add_mantenimiento
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        m = Mantenimiento(vehiculo_id="", tipo="revision", fecha="2026-01-01",
                          hecho=True, generar_gasto=True, base_imponible=100.0)
        with pytest.raises(HTTPException) as ei:
            add_mantenimiento(m, conn=conn)
        assert ei.value.status_code == 422
        assert conn.execute("SELECT count(*) AS n FROM finanzas.gastos_vehiculos").fetchone()["n"] == 0
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_mantenimiento_vehiculo_no_existe(scratch_db):
    """POST /api/mantenimientos con vehiculo_id inexistente → 422."""
    from fastapi import HTTPException
    from routers.flota import add_mantenimiento
    from models import Mantenimiento

    tok, conn = _conn(scratch_db)
    try:
        m = Mantenimiento(vehiculo_id="NO-EXISTE", tipo="revision", fecha="2026-01-01", hecho=False)
        with pytest.raises(HTTPException) as ei:
            add_mantenimiento(m, conn=conn)
        assert ei.value.status_code == 422
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
