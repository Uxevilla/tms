"""Integración de la revisión Fase 2: búsqueda por DNI solo admin, entidad por matrícula,
y las 8 clases de aviso de la bandeja de atención (datos de prueba por clase)."""
import datetime

import pytest

import main
from routers import torre


def _conn(scratch_db):
    tok = main._tenant_ctx.set({"db_name": scratch_db, "empresa": "it", "superadmin": False})
    c = main._db()
    c.set_autocommit(True)
    return tok, c


@pytest.mark.integration
def test_buscar_dni_solo_admin(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute("INSERT INTO empleados (id, nombre, dni) VALUES (?, 'Prueba', '12345678Z')", ("EMP-DNI",))
        conn.execute("INSERT INTO rrhh.conductores (empleado_id, tarjeta_tacografo) VALUES (?, 'DID-X')", ("EMP-DNI",))

        disp = torre.buscar(q="12345678Z", user={"rol": "dispatcher"}, conn=conn)["resultados"]
        assert not any(r["tipo"] == "conductor" for r in disp), "dispatcher no busca por DNI"

        admin = torre.buscar(q="12345678Z", user={"rol": "admin"}, conn=conn)["resultados"]
        assert any(r["tipo"] == "conductor" for r in admin), "admin sí busca por DNI"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_entidad_vehiculo_por_matricula(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, activo) VALUES (?, ?, ?, true)",
            ("V-MAT", "V-MAT", "9999-ZZZ"),
        )
        conn.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) VALUES (?, ?, ?, ?)",
            ("2026-09-24T08:00:00Z", "V-MAT", 40.4, -3.7),
        )
        # Se resuelve por matrícula y usa el codigo resuelto en las subconsultas (posición).
        r = torre.entidad_vehiculo("9999-ZZZ", user={"rol": "dispatcher"}, conn=conn)
        assert r["vehiculo"]["codigo"] == "V-MAT"
        assert r["posicion"]["lat"] == 40.4
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_atencion_8_clases(scratch_db):
    tok, conn = _conn(scratch_db)
    try:
        now = (datetime.datetime.utcnow() - datetime.timedelta(minutes=5)).isoformat() + "Z"
        ayer = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()

        # 1. viaje_retrasado
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, fecha_esperada_descarga, origen, destino) "
            "VALUES ('R1', 'En_Transito', ?, 'A', 'B')", (ayer,),
        )
        # 2. conduccion_limite (lectura < 12h)
        conn.execute(
            "INSERT INTO tacografo_dstat (did, vehiculo_id, driving_coupure_min, day_driving_min, time, creado) "
            "VALUES ('DID-LIM', 'V-LIM', 250, 0, ?, ?)", (now, now),
        )
        # 3. caducidad (ITV vencida)
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, fecha_caducidad_itv, activo) "
            "VALUES ('V-CAD', 'V-CAD', 'CAD-1', ?, true)", (ayer,),
        )
        # 4. viaje_sin_facturar (admin)
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, factura, precio) VALUES ('F1', 'Entregado', NULL, 100)",
        )
        # 5. gasto_sin_imputar
        conn.execute(
            "INSERT INTO finanzas.gastos (terminal, trip_id, concepto, importe, fecha) "
            "VALUES (NULL, NULL, 'suelto', 10, '2026-01-01')",
        )
        # 6. mensaje_sin_responder (con viaje → entidad viaje)
        conn.execute(
            "INSERT INTO mensajes (id, trip_id, needreply, source, time) VALUES ('M1', 'R1', true, 'demo', ?)",
            (now,),
        )
        # 7. mantenimiento_vencido
        conn.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, km_actuales, activo) "
            "VALUES ('V-MANT', 'V-MANT', 'MANT-1', 12000, true)",
        )
        conn.execute(
            "INSERT INTO flota.reglas_mantenimiento (vehiculo_id, tipo_mantenimiento, intervalo_km, ultimo_km_realizado) "
            "VALUES ('V-MANT', 'aceite', 1000, 10000)",
        )
        # 8. envio_trimble_fallido
        conn.execute(
            "INSERT INTO operaciones.trips (codigo, estado, error) VALUES ('E1', 'error', 'fallo')",
        )

        res = torre.atencion(user={"rol": "admin"}, conn=conn)
        tipos = {it["tipo"] for it in res["items"]}
        esperados = {
            "viaje_retrasado", "conduccion_limite", "caducidad", "viaje_sin_facturar",
            "gasto_sin_imputar", "mensaje_sin_responder", "mantenimiento_vencido", "envio_trimble_fallido",
        }
        assert esperados <= tipos, f"faltan clases: {esperados - tipos}"
        # id único de caducidad incluye el subtipo
        cad = next(it for it in res["items"] if it["tipo"] == "caducidad")
        assert cad["id"].startswith("caducidad:itv:")
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)
