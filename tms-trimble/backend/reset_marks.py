"""Resetea los marks de sincronización de la API Trimble.

Los marks son cursores por-cuenta (UTC) que la API devuelve en cada pollTraces/
pollFiles/pollMessages. Al CONECTAR UNA CUENTA NUEVA hay que resetearlos, porque
el cursor de la cuenta anterior no es válido para la nueva.

Uso (desde dentro del contenedor, WORKDIR=/app/backend):
  python reset_marks.py              # vaciar todos los marks → re-sincronizar desde el principio
  python reset_marks.py 2026-09-13   # re-sincronizar desde una fecha concreta (formato UTC)

Equivalentes por SQL directo en psql:
  DELETE FROM sync_state WHERE key IN ('traces_mark','files_mark','mensajes_mark','mensajes_free_mark');
  INSERT INTO sync_state (key,value) VALUES ('traces_mark','2026-09-13T00:00:00.000')
    ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;
"""
import sys
import main

KEYS = ("traces_mark", "files_mark", "mensajes_mark", "mensajes_free_mark")


def main_():
    val = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    for k in KEYS:
        main._set_sync_state(k, val)
    if val:
        print(f"Marks reseteados a {val!r}")
    else:
        print("Marks vaciados → la próxima ingesta re-sincronizará desde el principio")


if __name__ == "__main__":
    main_()
