"""Migración de identidad del vehículo: código interno fijo + terminal_trimble.

Idempotente. Uso (desde backend/):

    .venv/bin/python scripts/migrar_identidad.py [--dry-run] [--db NOMBRE]

Pasos:
1. terminal_trimble NULL/vacío → = codigo (las posiciones se guardaban con esa clave).
2. mantenimientos.vehiculo_id que apunta a una matrícula → codigo del vehículo
   (revertir las filas guardadas con matrícula durante las pruebas del modelo anterior).
3. Informe de vehículos sin terminal_trimble o sin app_terminal.
"""
import argparse

import main as m
from db import _db


def migrar(dbname, dry_run):
    tok = m._tenant_ctx.set({"db_name": dbname, "empresa": "migracion", "superadmin": False})
    conn = _db()
    conn.set_autocommit(False)
    try:
        # 1. terminal_trimble NULL/vacío → codigo
        n1 = conn.execute(
            "UPDATE flota.vehiculos SET terminal_trimble = codigo "
            "WHERE terminal_trimble IS NULL OR terminal_trimble = ''"
        ).rowcount
        print(f"[1] terminal_trimble NULL -> codigo: {n1} vehículos")

        # 2. mantenimientos.vehiculo_id matrícula -> codigo
        n2 = conn.execute(
            "UPDATE flota.mantenimientos m SET vehiculo_id = v.codigo "
            "FROM flota.vehiculos v WHERE v.matricula = m.vehiculo_id AND v.codigo != m.vehiculo_id"
        ).rowcount
        print(f"[2] mantenimientos.vehiculo_id matrícula -> codigo: {n2} filas")

        # 3. informe
        sin_tt = conn.execute(
            "SELECT codigo, matricula FROM vehiculos WHERE terminal_trimble IS NULL OR terminal_trimble = ''"
        ).fetchall()
        sin_app = conn.execute(
            "SELECT codigo, matricula FROM vehiculos WHERE app_terminal IS NULL OR app_terminal = ''"
        ).fetchall()
        print(f"[3] sin terminal_trimble: {len(sin_tt)}  " + ", ".join(r["codigo"] for r in sin_tt))
        print(f"[3] sin app_terminal: {len(sin_app)}  " + ", ".join(r["codigo"] for r in sin_app))

        if dry_run:
            conn.rollback()
            print("(dry-run: sin cambios)")
        else:
            conn.commit()
            print("migración aplicada")
    finally:
        conn.close()
        m._tenant_ctx.reset(tok)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--db", default=None)
    a = p.parse_args()
    import config
    migrar(a.db or config.DB_NAME, a.dry_run)
