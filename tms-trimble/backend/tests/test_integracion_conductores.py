"""Integración de conductores con caducidades (Fase 5).

Verifica que el listado de conductores incluye las tres caducidades del empleado:
- caducidad_carnet
- caducidad_cap
- caducidad_medica
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
def test_list_conductores_incluye_caducidades(scratch_db):
    """El endpoint GET /api/conductores debe devolver caducidad_carnet, caducidad_cap, caducidad_medica."""
    from routers.maestros import list_conductores

    tok, conn = _conn(scratch_db)
    try:
        # 1) Crear un empleado con caducidad_carnet vencida
        hoy = datetime.date.today()
        vencida = (hoy - datetime.timedelta(days=20)).isoformat()
        vigente = (hoy + datetime.timedelta(days=120)).isoformat()

        eid = "EMP-TEST-001"
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, "
            "direccion, ciudad, cp, fecha_alta, categoria, puesto, tipo_contrato, jornada, "
            "banco, iban, titular, caducidad_carnet, caducidad_cap, caducidad_medica, "
            "salario_bruto, irpf, convenio, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "Juan", "Pérez", "12345678A", "2812345678", "juan@test.es", "600000001",
             "Calle Test 1", "Madrid", "28001", hoy.isoformat(), "Conductor", "Conductor",
             "Indefinido", "Completa", "Santander", "ES00 0049 0000 0001 0000000000",
             "Juan Pérez", vencida, vigente, vigente, 2100, 15, "Transporte", datetime.datetime.utcnow().isoformat() + "Z"),
        )

        # 2) Crear un conductor vinculado a ese empleado
        conn.execute(
            "INSERT INTO conductores (nombre, dni, telefono, email, empleado_id) VALUES (?,?,?,?,?)",
            ("Juan Pérez", "12345678A", "600000001", "juan@test.es", eid),
        )

        # 3) Llamar al endpoint
        res = list_conductores(conn=conn)

        # 4) Verificar
        assert "conductores" in res
        assert len(res["conductores"]) == 1
        c = res["conductores"][0]
        assert c["nombre"] == "Juan Pérez"
        assert c["caducidad_carnet"] == vencida
        assert c["caducidad_cap"] == vigente
        assert c["caducidad_medica"] == vigente
        assert "disponible" in c
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_list_conductores_con_fecha_incluye_caducidades(scratch_db):
    """Con fecha_esperada_carga también debe incluir las caducidades."""
    from routers.maestros import list_conductores

    tok, conn = _conn(scratch_db)
    try:
        hoy = datetime.date.today()
        vencida = (hoy - datetime.timedelta(days=20)).isoformat()
        vigente = (hoy + datetime.timedelta(days=120)).isoformat()
        manana = (hoy + datetime.timedelta(days=1)).isoformat()

        eid = "EMP-TEST-002"
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, "
            "direccion, ciudad, cp, fecha_alta, categoria, puesto, tipo_contrato, jornada, "
            "banco, iban, titular, caducidad_carnet, caducidad_cap, caducidad_medica, "
            "salario_bruto, irpf, convenio, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, "Ana", "Gómez", "87654321B", "2887654321", "ana@test.es", "600000002",
             "Calle Test 2", "Madrid", "28001", hoy.isoformat(), "Conductor", "Conductor",
             "Indefinido", "Completa", "BBVA", "ES00 0182 0000 0002 0000000000",
             "Ana Gómez", vencida, vigente, vigente, 2000, 14, "Transporte", datetime.datetime.utcnow().isoformat() + "Z"),
        )

        conn.execute(
            "INSERT INTO conductores (nombre, dni, telefono, email, empleado_id) VALUES (?,?,?,?,?)",
            ("Ana Gómez", "87654321B", "600000002", "ana@test.es", eid),
        )

        # Llamar con fecha (mañana)
        res = list_conductores(fecha_esperada_carga=manana, conn=conn)

        assert "conductores" in res
        assert len(res["conductores"]) == 1
        c = res["conductores"][0]
        assert c["nombre"] == "Ana Gómez"
        assert c["caducidad_carnet"] == vencida
        assert c["caducidad_cap"] == vigente
        assert c["caducidad_medica"] == vigente
        assert "disponible" in c
        assert "motivo_ausencia" in c
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)


@pytest.mark.integration
def test_conductor_guarda_y_edita_did(scratch_db):
    """El DID (ID Trimble del conductor) se guarda en alta y se edita (clave del tacógrafo)."""
    from routers.maestros import add_conductor, upd_conductor, list_conductores
    from models import Conductor

    tok, conn = _conn(scratch_db)
    try:
        add_conductor(Conductor(nombre="Eusebio Álvarez", dni="", telefono="", email="", did="001"), conn=conn)

        res = list_conductores(conn=conn)
        assert len(res["conductores"]) == 1
        assert res["conductores"][0]["did"] == "001"

        cid = res["conductores"][0]["id"]
        upd_conductor(cid, Conductor(nombre="Eusebio Álvarez", dni="", telefono="", email="", did="002"), conn=conn)

        res = list_conductores(conn=conn)
        assert res["conductores"][0]["did"] == "002"
    finally:
        conn.close()
        main._tenant_ctx.reset(tok)