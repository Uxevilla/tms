"""Tests de integración del motor contable contra una BD Postgres scratch real.

Se ejecuta DENTRO del contenedor backend (donde `timescaledb` resuelve por nombre):
    docker exec -i tms-backend python - < tests/integration_contabilidad.py

Cubre: asiento balanceado, numeración secuencial, descuadre con rollback,
periodo cerrado, y pista de auditoría. Crea/destruye la BD `tms_it_test`.
"""
import sys

import psycopg2

import config
import main

SCRATCH = "tms_it_test"

_passed = 0
_failed = 0


def check(name, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  \u2714 {name}")
    else:
        _failed += 1
        print(f"  \u2718 {name}  {detail}")


def _raw(dbname):
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT,
        user=config.DB_USER, password=config.DB_PASSWORD, dbname=dbname,
    )


def setup():
    admin = _raw(config.DB_NAME)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
    cur.execute(f"CREATE DATABASE {SCRATCH}")
    admin.close()
    conn = _raw(SCRATCH)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(main._SCHEMA)
    # Sembrar el plan contable (apuntes tiene FK a cuentas.codigo).
    for cod, nom, grupo, tipo, orden in main._PLAN_CONTABLE:
        cur.execute(
            "INSERT INTO finanzas.cuentas (codigo, nombre, grupo, tipo, orden) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (codigo) DO NOTHING",
            (cod, nom, grupo, tipo, orden),
        )
    conn.close()
    # Marcar el esquema como aplicado para que _db() NO reintente el DDL
    # (CREATE TABLE/ALTER toman AccessExclusiveLock y chocan con la conexión abierta del test).
    main._schema_done.add(SCRATCH)


def teardown():
    admin = _raw(config.DB_NAME)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
    admin.close()


def main_tests():
    setup()
    main._tenant_ctx.set({"db_name": SCRATCH, "empresa": "it", "superadmin": False})
    conn = main._db()
    try:
        # T1: asiento balanceado -> creado + apuntes + numero 1
        print("[T1] asiento balanceado...")
        aid = main._registrar_asiento(
            "2026-01-15", "Compra test",
            [("600", 100, 0, "compra"), ("400", 0, 100, "proveedor")],
            "it_manual",
        )
        a = conn.execute("SELECT numero, concepto FROM finanzas.asientos WHERE id=?", (aid,)).fetchone()
        n_apuntes = conn.execute("SELECT COUNT(*) c FROM finanzas.apuntes WHERE asiento_id=?", (aid,)).fetchone()["c"]
        check("asiento balanceado se crea", a is not None and a["numero"] == 1, f"num={a and a['numero']}")
        check("asiento genera 2 apuntes", n_apuntes == 2, f"apuntes={n_apuntes}")

        # T2: numeración secuencial por año
        aid2 = main._registrar_asiento(
            "2026-02-01", "Venta test",
            [("430", 200, 0, "cliente"), ("705", 0, 200, "venta")],
            "it_manual",
        )
        num2 = conn.execute("SELECT numero FROM finanzas.asientos WHERE id=?", (aid2,)).fetchone()["numero"]
        check("numeración secuencial (2º asiento = 2)", num2 == 2, f"num2={num2}")

        # T3: descuadre -> ValueError + sin asiento parcial (rollback)
        antes = conn.execute("SELECT COUNT(*) c FROM finanzas.asientos").fetchone()["c"]
        try:
            main._registrar_asiento(
                "2026-03-01", "Descuadrado",
                [("600", 100, 0, "x"), ("400", 0, 90, "y")],
                "it_bad",
            )
            check("descuadre lanza ValueError", False)
        except ValueError:
            check("descuadre lanza ValueError", True)
            despues = conn.execute("SELECT COUNT(*) c FROM finanzas.asientos").fetchone()["c"]
            check("descuadre no deja asiento parcial", antes == despues, f"{antes}->{despues}")

        # T4: pista de auditoría
        audit = conn.execute(
            "SELECT accion, usuario FROM sistema.audit_log WHERE tabla='asientos' AND registro_id=?",
            (str(aid),),
        ).fetchone()
        check("audit_log registra 'crear' del asiento", audit is not None and audit["accion"] == "crear",
              f"audit={audit}")

        # T5: periodo cerrado -> rechaza fecha anterior al cierre
        print("[T5] periodo cerrado...")
        conn.execute(
            "INSERT INTO sistema.config (key, value) VALUES ('cierre_fecha', '2026-01-31') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        )
        conn.commit()  # visible para la conexión nueva de _registrar_asiento
        try:
            main._registrar_asiento(
                "2026-01-10", "Periodo cerrado",
                [("600", 10, 0, "x"), ("400", 0, 10, "y")],
                "it_cierre",
            )
            check("periodo cerrado rechaza fecha anterior", False)
        except ValueError:
            check("periodo cerrado rechaza fecha anterior", True)
        conn.execute("DELETE FROM sistema.config WHERE key='cierre_fecha'")
        conn.commit()

        # T6: soft-delete columnas presentes en tablas contables
        print("[T6] soft-delete...")
        cols = conn.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name='borrado' AND table_name IN ('asientos','clientes','proveedores','facturas') "
            "ORDER BY table_name",
        ).fetchall()
        check("columnas soft-delete en las 4 tablas contables", len(cols) == 4, f"tablas={[c['table_name'] for c in cols]}")

        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
        teardown()

    print(f"\nResultado integración: {_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main_tests())
