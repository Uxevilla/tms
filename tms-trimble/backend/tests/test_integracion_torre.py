"""Integración de los endpoints de la Torre de control (Fase 2): atencion, buscar, entidad.

Cada endpoint se prueba contra Postgres real (fixture scratch_db) llamando a la función
directamente, con el dict `user` inyectado (igual que require_role) y la conexión `_Conn`.
"""
import pytest

import main
from routers import torre


def _conn(scratch_db):
    """Apunta el tenant al scratch y devuelve (token, _Conn) con autocommit para sembrar."""
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_atencion_caducidad_y_facturacion_por_rol(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, fecha_caducidad_itv, activo) "
            "VALUES (?, ?, ?, ?, true)", ("V-1", "T-1", "1111-AAA", "2020-01-01"),
        )
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, factura, cliente, origen, destino) "
            "VALUES (?, 'Entregado', NULL, 'Cliente A', 'Madrid', 'Barcelona')", ("TRIP-1",),
        )

        admin = torre.atencion(user={"rol": "admin"}, conn=conn)
        tipos = [it["tipo"] for it in admin["items"]]
        assert "caducidad" in tipos
        assert "viaje_sin_facturar" in tipos
        cad = next(it for it in admin["items"] if it["tipo"] == "caducidad")
        assert cad["severidad"] == "critico", "ITV vencida debe ser crítica"
        assert cad["entidad"]["tipo"] == "vehiculo"
        assert admin["resumen"]["critico"] >= 1

        disp = torre.atencion(user={"rol": "dispatcher"}, conn=conn)
        disp_tipos = [it["tipo"] for it in disp["items"]]
        assert "viaje_sin_facturar" not in disp_tipos, "viaje_sin_facturar es solo admin"
        assert "caducidad" in disp_tipos
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_buscar_insensible_acentos_y_filtro_rol(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES (?, ?, ?, true)", ("V-2", "T-2", "1234-GARCÍA"),
        )
        conn.execute(
            "INSERT INTO finanzas.facturas (numero, fecha, cliente_nombre, total) "
            "VALUES ('F-2026-1', '2026-09-01', 'Cliente A', 100)",
        )

        # Acentos: "garcia" debe encontrar la matrícula "GARCÍA".
        res = torre.buscar(q="garcia", user={"rol": "admin"}, conn=conn)["resultados"]
        assert any(r["tipo"] == "vehiculo" for r in res), "búsqueda insensible a acentos"

        # Facturas: visibles para admin, ocultas para dispatcher.
        res_admin = torre.buscar(q="F-2026", user={"rol": "admin"}, conn=conn)["resultados"]
        assert any(r["tipo"] == "factura" for r in res_admin)
        res_disp = torre.buscar(q="F-2026", user={"rol": "dispatcher"}, conn=conn)["resultados"]
        assert not any(r["tipo"] == "factura" for r in res_disp)

        # Mínimo 2 caracteres → vacío.
        assert torre.buscar(q="a", user={"rol": "admin"}, conn=conn)["resultados"] == []
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_entidad_vehiculo_posicion_y_margen_admin(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) "
            "VALUES (?, ?, ?, true)", ("V-3", "T-3", "2222-BBB"),
        )
        conn.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng, speed_kmh, heading) "
            "VALUES (?, ?, ?, ?, ?, ?)", ("2026-09-24T08:00:00Z", "V-3", 40.4, -3.7, 80.0, 90.0),
        )

        admin = torre.entidad_vehiculo("V-3", user={"rol": "admin"}, conn=conn)
        assert admin["vehiculo"]["codigo"] == "V-3"
        assert admin["posicion"]["lat"] == 40.4
        assert "coste_margen_mes" in admin

        disp = torre.entidad_vehiculo("V-3", user={"rol": "dispatcher"}, conn=conn)
        assert "coste_margen_mes" not in disp, "margen del mes es solo admin"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_entidad_viaje_paradas(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, cliente, origen, destino, precio) "
            "VALUES (?, 'En_Transito', 'Cliente A', 'Madrid', 'Barcelona', 500)", ("TRIP-2",),
        )
        conn.execute(
            "INSERT INTO operaciones.paradas (trip_id, orden, nombre, ciudad) VALUES (?, 1, 'Carga', 'Madrid')",
            ("TRIP-2",),
        )

        r = torre.entidad_viaje("TRIP-2", user={"rol": "dispatcher"}, conn=conn)
        assert r["viaje"]["codigo"] == "TRIP-2"
        assert len(r["paradas"]) == 1
        assert "rentabilidad" not in r  # no admin

        r_admin = torre.entidad_viaje("TRIP-2", user={"rol": "admin"}, conn=conn)
        assert "rentabilidad" in r_admin
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_entidad_conductor_caducidades(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, caducidad_carnet) "
            "VALUES (?, 'Juan', 'García', '12345678A', '2020-01-01')", ("EMP-1",),
        )
        cur = conn.execute(
            "INSERT INTO rrhh.conductores (empleado_id, tarjeta_tacografo) VALUES (?, ?) RETURNING id",
            ("EMP-1", "DID-1"),
        )
        cid = cur.fetchone()["id"]

        r = torre.entidad_conductor(str(cid), user={"rol": "admin"}, conn=conn)
        assert r["conductor"]["nombre"] == "Juan"
        assert r["conductor"]["dni"] == "12345678A"  # admin ve el DNI
        assert any(c["tipo"] == "Carné" for c in r["caducidades"])

        r_disp = torre.entidad_conductor(str(cid), user={"rol": "dispatcher"}, conn=conn)
        assert r_disp["conductor"]["dni"] is None  # dispatcher no ve el DNI
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
