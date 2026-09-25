"""Migración de identidad del vehículo: código interno fijo + terminal_trimble.

Idempotente. Uso (desde backend/):

    .venv/bin/python scripts/migrar_identidad.py             # dry-run, todas las BD de cliente
    .venv/bin/python scripts/migrar_identidad.py --apply      # aplica (pg_dump por BD salvo --backup-ok)
    .venv/bin/python scripts/migrar_identidad.py --db tms     # solo una BD

Por cada BD:
1. trips.terminal guardado como matrícula → codigo del vehículo.
2. mantenimientos.vehiculo_id guardado como matrícula → codigo.
3. terminal_trimble NULL/vacío → codigo (reportando colisiones de unicidad, sin fallar).
4. Informe de vehículos sin terminal_trimble o sin app_terminal.

No importa `main` (no arranca la app): usa psycopg2 + config directamente.
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime

# Permite importar config/db del backend aunque se lance desde scripts/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

import config


def _conn(dbname):
    return psycopg2.connect(
        host=config.DB_HOST, port=config.DB_PORT, dbname=dbname,
        user=config.DB_USER, password=config.DB_PASSWORD,
    )


def _tenant_dbs():
    """BD de cliente desde la maestra (empresas.db_name); cae a la por defecto si no hay."""
    try:
        conn = _conn(config.MASTER_DB_NAME)
    except Exception:
        return [config.DB_NAME]
    try:
        cur = conn.cursor()
        cur.execute("SELECT db_name FROM empresas ORDER BY db_name")
        dbs = [r[0] for r in cur.fetchall()]
        return dbs or [config.DB_NAME]
    finally:
        conn.close()


BACKUP_DIR = os.environ.get("TMS_BACKUP_DIR", os.path.expanduser("~/tms_backups"))


def _backup(dbname):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    out = os.path.join(BACKUP_DIR, f"identidad_{dbname}_{datetime.now():%Y%m%d_%H%M%S}.dump")
    env = {**os.environ, "PGPASSWORD": config.DB_PASSWORD or ""}
    r = subprocess.run(
        ["pg_dump", "-Fc", "-h", config.DB_HOST, "-p", str(config.DB_PORT),
         "-U", config.DB_USER, "-d", dbname, "-f", out], env=env,
    )
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        sys.exit(f"ABORTADO: backup de {dbname} falló (rc={r.returncode}); no se aplica nada.")
    print(f"    backup OK: {out}")


_UNICAS = (
    "(SELECT matricula, min(codigo) AS codigo FROM flota.vehiculos "
    " WHERE COALESCE(matricula,'') <> '' GROUP BY matricula HAVING count(*) = 1)"
)


def _migrar(dbname, dry_run, backup_ok):
    print(f"== {dbname} ==")
    if not dry_run and not backup_ok:
        _backup(dbname)
    conn = _conn(dbname)
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # 1. trips.terminal guardado como matrícula ÚNICA -> codigo (las ambiguas se informan)
        cur.execute(
            f"UPDATE operaciones.trips t SET terminal = v.codigo FROM {_UNICAS} v "
            "WHERE t.terminal = v.matricula AND t.terminal <> v.codigo "
            "AND NOT EXISTS (SELECT 1 FROM flota.vehiculos x WHERE x.codigo = t.terminal)"
        )
        print(f"    [1] trips.terminal matrícula única -> codigo: {cur.rowcount}")
        cur.execute(
            "SELECT t.codigo, t.terminal FROM operaciones.trips t "
            "WHERE t.terminal IN (SELECT matricula FROM flota.vehiculos GROUP BY matricula HAVING count(*) > 1) "
            "AND NOT EXISTS (SELECT 1 FROM flota.vehiculos x WHERE x.codigo = t.terminal)"
        )
        ambiguos = cur.fetchall()
        print(f"    [1] viajes con matrícula ambigua (revisar a mano): {ambiguos or 'ninguno'}")

        # 2. mantenimientos.vehiculo_id guardado como matrícula ÚNICA -> codigo
        cur.execute(
            f"UPDATE flota.mantenimientos m SET vehiculo_id = v.codigo FROM {_UNICAS} v "
            "WHERE m.vehiculo_id = v.matricula AND m.vehiculo_id <> v.codigo "
            "AND NOT EXISTS (SELECT 1 FROM flota.vehiculos x WHERE x.codigo = m.vehiculo_id)"
        )
        print(f"    [2] mantenimientos.vehiculo_id matrícula única -> codigo: {cur.rowcount}")

        # 3. terminal_trimble NULL/vacío -> codigo (reportando colisiones, sin fallar)
        cur.execute(
            "SELECT codigo FROM flota.vehiculos WHERE terminal_trimble IS NULL OR terminal_trimble = ''"
        )
        sin_tt = [r[0] for r in cur.fetchall()]
        colisiones = []
        aplicadas = 0
        for codigo in sin_tt:
            cur.execute(
                "SELECT codigo FROM flota.vehiculos WHERE terminal_trimble = %s AND codigo <> %s",
                (codigo, codigo),
            )
            if cur.fetchone():
                colisiones.append(codigo)
            else:
                cur.execute(
                    "UPDATE flota.vehiculos SET terminal_trimble = codigo WHERE codigo = %s",
                    (codigo,),
                )
                aplicadas += 1
        print(f"    [3] terminal_trimble NULL -> codigo: {aplicadas} aplicadas, colisiones: {colisiones or 'ninguna'}")

        # 4. informe
        cur.execute(
            "SELECT codigo FROM vehiculos WHERE terminal_trimble IS NULL OR terminal_trimble = ''"
        )
        sin_tt2 = [r[0] for r in cur.fetchall()]
        cur.execute(
            "SELECT codigo FROM vehiculos WHERE app_terminal IS NULL OR app_terminal = ''"
        )
        sin_app = [r[0] for r in cur.fetchall()]
        print(f"    [4] sin terminal_trimble: {sin_tt2 or 'ninguno'}")
        print(f"    [4] sin app_terminal: {sin_app or 'ninguno'}")

        if dry_run:
            conn.rollback()
            print("    (dry-run: sin cambios)")
        else:
            conn.commit()
            print("    migración aplicada")
    finally:
        conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Aplica los cambios (por defecto es dry-run)")
    p.add_argument("--backup-ok", action="store_true", help="Salta el pg_dump de seguridad")
    p.add_argument("--db", default=None, help="Solo una BD (si no, todas las de cliente)")
    a = p.parse_args()
    dry = not a.apply
    dbs = [a.db] if a.db else _tenant_dbs()
    for db in dbs:
        _migrar(db, dry, a.backup_ok)
