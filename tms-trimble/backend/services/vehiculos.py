"""Traducción única entre identificadores de vehículo.

Regla de oro (revisión de Claude, adoptada):
- `codigo` = clave interna FIJA. Se genera al alta, no se edita nunca, y es lo
  ÚNICO que se guarda en otras tablas (viajes.terminal, gastos, files,
  mantenimientos, reglas).
- `matricula` = dato editable; se muestra y se busca, pero NO enlaza tablas.
- `terminal_trimble` = clave de telemetría; `app_terminal` = clave de despacho.
  Ambas se traducen SOLO aquí, en el borde.

Nadie más cruza a mano. La clave de Redis del viaje activo es `terminal_trimble`
(lo que recibe el worker de ingesta): _set/_del_viaje_activo(terminal_trimble_de(codigo)).
"""
from db import _db


def _vehiculo(conn, *, codigo=None, terminal_trimble=None):
    if codigo is not None:
        return conn.execute(
            "SELECT codigo, terminal_trimble, app_terminal, matricula FROM vehiculos WHERE codigo=?",
            (codigo,),
        ).fetchone()
    if terminal_trimble is not None:
        return conn.execute(
            "SELECT codigo, terminal_trimble, app_terminal, matricula FROM vehiculos WHERE terminal_trimble=?",
            (terminal_trimble,),
        ).fetchone()
    return None


def codigo_por_terminal_trimble(terminal_trimble, conn=None):
    """terminal_trimble → codigo (para la ingesta: posiciones, tacógrafo, trazas)."""
    if not terminal_trimble:
        return ""
    if conn is not None:
        row = _vehiculo(conn, terminal_trimble=terminal_trimble)
        return row["codigo"] if row else ""
    with _db() as c:
        row = _vehiculo(c, terminal_trimble=terminal_trimble)
        return row["codigo"] if row else ""


def terminal_trimble_de(codigo, conn=None):
    """codigo → terminal_trimble (telemetría + clave Redis del viaje activo)."""
    if not codigo:
        return ""
    if conn is not None:
        row = _vehiculo(conn, codigo=codigo)
        return row["terminal_trimble"] if row else ""
    with _db() as c:
        row = _vehiculo(c, codigo=codigo)
        return row["terminal_trimble"] if row else ""


def app_terminal_de(codigo, conn=None):
    """codigo → app_terminal (despacho SOAP). Devuelve '' si no hay APP; el llamador decide el error."""
    if not codigo:
        return ""
    if conn is not None:
        row = _vehiculo(conn, codigo=codigo)
        return (row["app_terminal"] or "") if row else ""
    with _db() as c:
        row = _vehiculo(c, codigo=codigo)
        return (row["app_terminal"] or "") if row else ""


def validar_unicidad(conn, *, matricula="", terminal_trimble="", app_terminal="", excluir_codigo=None):
    """Devuelve un mensaje de error (o None) si matrícula (entre activos),
    terminal_trimble o app_terminal (no vacío) ya están en uso por OTRO vehículo."""
    matricula = (matricula or "").strip()
    terminal_trimble = (terminal_trimble or "").strip()
    app_terminal = (app_terminal or "").strip()

    def _ya(cond, params):
        q = f"SELECT codigo, matricula FROM vehiculos WHERE {cond}"
        if excluir_codigo:
            q += " AND codigo != ?"
            params = (*params, excluir_codigo)
        return conn.execute(q, params).fetchone()

    if matricula:
        row = _ya("matricula = ? AND activo", (matricula,))
        if row:
            return f"La matrícula {matricula} ya está en uso (vehículo {row['codigo']})."
    if terminal_trimble:
        row = _ya("terminal_trimble = ?", (terminal_trimble,))
        if row:
            return f"El terminal de telemetría {terminal_trimble} ya está asignado (vehículo {row['codigo']})."
    if app_terminal:
        row = _ya("app_terminal = ?", (app_terminal,))
        if row:
            return f"El terminal APP {app_terminal} ya está asignado (vehículo {row['codigo']})."
    return None
