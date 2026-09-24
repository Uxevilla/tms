"""Siembra datos de prueba para los tests e2e de la Torre (8 clases de aviso + vehículo/viaje).

Idempotente (ON CONFLICT). Se ejecuta contra la BD de test (CI) o la local antes de Playwright.
Uso: python scripts/seed_e2e.py  (con DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME en el entorno)
"""
import datetime
import os
import sys

import psycopg2


def main() -> None:
    # Guardas: solo en entornos de test explícitos, nunca en producción.
    if os.environ.get("E2E_SEED_OK") != "1":
        print("ERROR: seed_e2e.py requiere E2E_SEED_OK=1 para ejecutarse.", file=sys.stderr)
        sys.exit(1)
    if os.environ.get("TMS_ENV") == "prod":
        print("ERROR: seed_e2e.py no debe ejecutarse en producción (TMS_ENV=prod).", file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(
        host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "5432")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        dbname=os.environ["DB_NAME"],
    )
    conn.autocommit = True
    cur = conn.cursor()

    now = (datetime.datetime.utcnow() - datetime.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    ayer = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()

    # Vehículo de prueba: ITV caducada (caducidad) + km alto (mantenimiento vencido).
    cur.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, fecha_caducidad_itv, km_actuales, activo) "
        "VALUES ('E2E-VEH', 'E2E-VEH', '0001-TST', %s, 12000, true) "
        "ON CONFLICT (codigo) DO UPDATE SET fecha_caducidad_itv=EXCLUDED.fecha_caducidad_itv, km_actuales=EXCLUDED.km_actuales",
        (ayer,),
    )
    cur.execute(
        "INSERT INTO flota.reglas_mantenimiento (vehiculo_id, tipo_mantenimiento, intervalo_km, ultimo_km_realizado) "
        "VALUES ('E2E-VEH', 'aceite', 1000, 10000) ON CONFLICT DO NOTHING",
    )

    # Viaje retrasado (descarga pasada) + envío fallido (error).
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, fecha_esperada_descarga, origen, destino, matricula) "
        "VALUES ('E2E-TRIP-1', 'En_Transito', %s, 'Madrid', 'Barcelona', '0001-TST') "
        "ON CONFLICT (codigo) DO UPDATE SET estado='En_Transito', fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga",
        (ayer,),
    )
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, error) VALUES ('E2E-ERR', 'error', 'fallo de envío') "
        "ON CONFLICT (codigo) DO UPDATE SET estado='error'",
    )
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, factura, precio) VALUES ('E2E-FACT', 'Entregado', NULL, 200) "
        "ON CONFLICT (codigo) DO UPDATE SET estado='Entregado', factura=NULL",
    )

    # Conducción al límite (lectura < 12 h).
    cur.execute(
        "INSERT INTO tacografo_dstat (did, vehiculo_id, driving_coupure_min, day_driving_min, time, creado) "
        "VALUES ('E2E-DID', '0001-TST', 250, 0, %s, %s) ON CONFLICT DO NOTHING",
        (now, now),
    )

    # Posición GPS para que el mapa (telemetría activa) muestre un marcador en e2e.
    # posiciones_gps.vehiculo_id referencia el CODIGO del vehículo (vista vehiculos.id = codigo).
    cur.execute("SELECT 1 FROM telemetria.posiciones_gps WHERE vehiculo_id = 'E2E-VEH' LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) VALUES (%s, %s, %s, %s)",
            (now, "E2E-VEH", 40.4, -3.7),
        )
    # Segundo vehículo con posición: para que el test del encuadre pueda rastrear un marcador
    # fijo mientras cambia la posición de OTRO vehículo (la cámara no debe re-encuadrar).
    cur.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
        "VALUES ('E2E-VEH2', 'E2E-VEH2', '0002-TST', 'tractora', true) ON CONFLICT (codigo) DO NOTHING",
    )
    cur.execute("SELECT 1 FROM telemetria.posiciones_gps WHERE vehiculo_id = 'E2E-VEH2' LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) VALUES (%s, %s, %s, %s)",
            (now, "E2E-VEH2", 41.4, 2.17),
        )

    # Gasto sin imputar (idempotente: solo si no existe el concepto marcador).
    cur.execute("SELECT 1 FROM finanzas.gastos WHERE concepto = 'gasto suelto e2e' LIMIT 1")
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO finanzas.gastos (terminal, trip_id, concepto, importe, fecha) "
            "VALUES (NULL, NULL, 'gasto suelto e2e', 10, '2026-01-01')",
        )

    # Mensaje sin responder (con viaje → entidad viaje). Terminal único para que los mensajes
    # reales de producción (source distinta + terminal vacío + hora posterior) no lo den por respondido.
    cur.execute(
        "INSERT INTO mensajes (id, trip_id, needreply, source, terminal, time) VALUES ('E2E-M1', 'E2E-TRIP-1', true, 'demo', 'E2E-TERMINAL', %s) "
        "ON CONFLICT (id) DO UPDATE SET needreply=true, source='demo', trip_id='E2E-TRIP-1', terminal='E2E-TERMINAL', time=EXCLUDED.time",
        (now,),
    )

    # Conductor con carné caducado (caducidad de conductor).
    cur.execute(
        "INSERT INTO empleados (id, nombre, apellidos, caducidad_carnet) VALUES ('EMP-E2E', 'Conductor', 'Prueba', %s) "
        "ON CONFLICT (id) DO UPDATE SET caducidad_carnet=EXCLUDED.caducidad_carnet",
        (ayer,),
    )
    cur.execute(
        "INSERT INTO rrhh.conductores (empleado_id, tarjeta_tacografo) VALUES ('EMP-E2E', 'E2E-DID') "
        "ON CONFLICT (empleado_id) DO NOTHING",
    )

    # Tablero de planificación (Fase 3): tractora limpia (validación ok → verde) + semirremolque.
    # matricula 0003-TST → se ordena al inicio del timeline (junto a las de test), visible sin scroll.
    # DO UPDATE (no DO NOTHING) para corregir valores viejos de seeds anteriores.
    cur.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
        "VALUES ('E2E-TRAC', 'E2E-TRAC', '0003-TST', 'tractora', true) "
        "ON CONFLICT (codigo) DO UPDATE SET terminal_trimble=EXCLUDED.terminal_trimble, matricula=EXCLUDED.matricula, categoria=EXCLUDED.categoria, activo=EXCLUDED.activo",
    )
    cur.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, capacidad_peso, capacidad_palets) "
        "VALUES ('E2E-SEMI', 'E2E-SEMI', 'SEMI-TST', 'semirremolque', true, 25000, 33) "
        "ON CONFLICT (codigo) DO UPDATE SET matricula=EXCLUDED.matricula, categoria=EXCLUDED.categoria, activo=EXCLUDED.activo, capacidad_peso=EXCLUDED.capacidad_peso, capacidad_palets=EXCLUDED.capacidad_palets",
    )

    # Fase 3 — arrastre/validación/reasignación. Fechas relativas a HOY para que aparezcan
    # en la vista del día actual y los casos de solapamiento/caducidad sean deterministas.
    hoy = datetime.date.today().isoformat()
    ayer_r = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    # Tractoras de test adicionales (bloqueo por solapamiento + reasignación).
    for codigo, matricula in (("E2E-TRAC2", "0004-TST"), ("E2E-TRAC3", "0005-TST")):
        cur.execute(
            "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo) "
            "VALUES (%s, %s, %s, 'tractora', true) "
            "ON CONFLICT (codigo) DO UPDATE SET terminal_trimble=EXCLUDED.terminal_trimble, matricula=EXCLUDED.matricula, categoria=EXCLUDED.categoria, activo=EXCLUDED.activo",
            (codigo, codigo, matricula),
        )

    # Semirremolque con ITV caducada (aviso itv_caducada).
    cur.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, matricula, categoria, activo, fecha_caducidad_itv) "
        "VALUES ('E2E-SEMI-OLD', 'E2E-SEMI-OLD', 'SEMI-OLD', 'semirremolque', true, %s) "
        "ON CONFLICT (codigo) DO UPDATE SET fecha_caducidad_itv=EXCLUDED.fecha_caducidad_itv",
        (ayer,),
    )

    # Viaje ya asignado a E2E-TRAC2 hoy (bloqueo tractora_solapada al solapar con otro viaje).
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, terminal, fecha_esperada_carga, fecha_esperada_descarga, origen, destino) "
        "VALUES ('E2E-PLAN-SOLAP', 'sin_asignar', 'E2E-TRAC2', %s, %s, 'Madrid', 'Valencia') "
        "ON CONFLICT (codigo) DO UPDATE SET terminal='E2E-TRAC2', fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga",
        (f"{hoy}T09:00", f"{hoy}T11:00"),
    )

    # Pendiente limpio (ok → verde), hoy. Resetea terminal/semirremolque (idempotente).
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, fecha_esperada_carga, fecha_esperada_descarga, origen, destino, kilos) "
        "VALUES ('E2E-PLAN-OK', 'sin_asignar', %s, %s, 'Madrid', 'Barcelona', 0) "
        "ON CONFLICT (codigo) DO UPDATE SET terminal=NULL, semirremolque_id=NULL, remolque_id=NULL, fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga",
        (f"{hoy}T10:00", f"{hoy}T12:00"),
    )

    # Pendiente con aviso (semirremolque con ITV caducada), hoy.
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, semirremolque_id, fecha_esperada_carga, fecha_esperada_descarga, origen, destino) "
        "VALUES ('E2E-PLAN-AVISO', 'sin_asignar', 'E2E-SEMI-OLD', %s, %s, 'Madrid', 'Sevilla') "
        "ON CONFLICT (codigo) DO UPDATE SET terminal=NULL, semirremolque_id='E2E-SEMI-OLD', fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga",
        (f"{hoy}T14:00", f"{hoy}T16:00"),
    )

    # Viaje de OTRA fecha (ayer) → no aparece en la vista del día actual.
    cur.execute(
        "INSERT INTO operaciones.trips (codigo, estado, fecha_esperada_carga, fecha_esperada_descarga, origen, destino) "
        "VALUES ('E2E-PLAN-OTRO', 'sin_asignar', %s, %s, 'Madrid', 'Bilbao') "
        "ON CONFLICT (codigo) DO UPDATE SET terminal=NULL, fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga",
        (f"{ayer_r}T08:00", f"{ayer_r}T20:00"),
    )

    conn.close()
    print("seed e2e OK")


if __name__ == "__main__":
    main()
