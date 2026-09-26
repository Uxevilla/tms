"""Tablero de planificación (Fase 3): datos para la línea de tiempo de tractoras y
validación de asignación (bloqueos + avisos) SIN asignar.

- GET  /api/planificacion?desde&hasta → {vehiculos (tractoras), viajes (con inicio/fin)}.
- POST /api/planificacion/validar  → {ok, bloqueos, avisos}.

La asignación real se hace SIEMPRE con el POST /api/trips/{id}/asignar existente;
aquí solo se valida. Reutiliza la lógica existente: _vehiculos_en_curso (remolques),
_dstat_terminal (tacógrafo) y las columnas de caducidad/capacidad de vehiculos.
"""
import datetime
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from core import _ESTADOS_FINALES, _EXT_DIA_CONDUCCION_MIN, _MAX_CONDUCCION_CONTINUA_MIN, _MAX_DIA_CONDUCCION_MIN
from clients.trimble import get_client
from db import get_conn
from security import require_role
from services.contabilidad import _auditar
from services.tacografo import _dstat_terminal
from services.telemetria import _del_viaje_activo
from services.viajes import _vehiculos_en_curso

router = APIRouter(dependencies=[Depends(require_role(["admin", "dispatcher"]))])

_ESTADOS_FINALES_SQL = "(" + ",".join(f"'{e}'" for e in _ESTADOS_FINALES) + ")"
# Estados en los que el conductor ya ha empezado el viaje (el viaje vive en el terminal).
_ESTADOS_EN_CURSO = ("Llegada_Origen", "Cargando", "En_Transito", "Llegada_Destino", "Descargando")


def _fecha(v: str) -> str:
    return (v or "").strip()


def _solapan(a_inicio: str, a_fin: str, b_inicio: str, b_fin: str) -> bool:
    """Solapamiento de intervalos abiertos [inicio, fin). Las fechas son TEXT ISO
    (YYYY-MM-DD o YYYY-MM-DDTHH:MM), por lo que la comparación lexicográfica vale."""
    a_inicio, a_fin, b_inicio, b_fin = map(_fecha, (a_inicio, a_fin, b_inicio, b_fin))
    if not a_inicio or not a_fin or not b_inicio or not b_fin:
        return False
    return a_inicio < b_fin and a_fin > b_inicio


def _fin_derivado(inicio: str, fin: str, tiempo_min: float) -> str:
    """Si hay fin devuélvelo; si solo hay inicio, deriva fin = inicio + duración estimada."""
    inicio, fin = _fecha(inicio), _fecha(fin)
    if fin:
        return fin
    if inicio and tiempo_min and tiempo_min > 0:
        # La fecha esperada de carga suele ser 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM'.
        try:
            base = inicio if "T" in inicio else inicio + "T00:00"
            dt = datetime.datetime.fromisoformat(base)
            return (dt + datetime.timedelta(minutes=float(tiempo_min))).isoformat(timespec="minutes")
        except ValueError:
            return ""
    return ""


@router.get("/api/planificacion")
def planificacion(desde: str = "", hasta: str = "", conn=Depends(get_conn)):
    """Tractoras + viajes no finalizados con su ventana temporal (inicio/fin)."""
    vehiculos = [
        dict(r) for r in conn.execute(
            "SELECT id, codigo, matricula, categoria, activo, capacidad_peso, capacidad_palets, "
            "fecha_caducidad_itv, fecha_caducidad_seguro FROM vehiculos "
            "WHERE categoria = 'tractora' ORDER BY matricula",
        ).fetchall()
    ]

    rows = conn.execute(
        f"SELECT id, codigo, terminal, semirremolque_id, remolque_id, conductor, conductor_id, "
        f"estado, origen, destino, cliente, matricula, kilos, tiempo_min, payload, "
        f"fecha_esperada_carga, fecha_esperada_descarga FROM trips "
        f"WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL} AND anulado_at IS NULL ORDER BY codigo",
    ).fetchall()

    viajes = []
    for r in rows:
        palets = 0
        pendiente_reenvio = False
        try:
            payload = json.loads(r["payload"] or "{}")
            palets = int(payload.get("palets") or 0)
            pendiente_reenvio = bool(payload.get("pendiente_reenvio"))
        except (json.JSONDecodeError, TypeError, ValueError):
            palets = 0
        inicio = _fecha(r["fecha_esperada_carga"])
        fin = _fecha(r["fecha_esperada_descarga"])
        fin = _fin_derivado(inicio, fin, float(r["tiempo_min"] or 0))
        # Filtro por rango visible (desde/hasta) si se pide; los sin fecha siempre se incluyen.
        if desde and hasta and inicio and fin and not _solapan(inicio, fin, desde, hasta):
            continue
        viajes.append({
            "id": r["id"], "codigo": r["codigo"], "terminal": r["terminal"],
            "semirremolque_id": r["semirremolque_id"], "remolque_id": r["remolque_id"],
            "conductor": r["conductor"], "conductor_id": r["conductor_id"],
            "estado": r["estado"], "origen": r["origen"], "destino": r["destino"],
            "cliente": r["cliente"], "matricula": r["matricula"],
            "kilos": float(r["kilos"] or 0), "palets": palets,
            "tiempo_min": float(r["tiempo_min"] or 0),
            "inicio": inicio, "fin": fin,
            "pendiente_reenvio": pendiente_reenvio,
        })

    return {"vehiculos": vehiculos, "viajes": viajes}


class ValidarRequest(BaseModel):
    trip_id: str = ""
    codigo: str = ""               # tractora destino (código interno del vehículo)
    semirremolque_id: str = ""
    remolque_id: str = ""
    conductor_id: Optional[int] = None
    inicio: str = ""
    fin: str = ""
    kilos: float = 0.0
    palets: int = 0

    @field_validator("trip_id", "codigo", "semirremolque_id", "remolque_id", "inicio", "fin", mode="before")
    @classmethod
    def _nulo_a_vacio(cls, v):
        return "" if v is None else v

    @field_validator("kilos", mode="before")
    @classmethod
    def _kilos_nulo(cls, v):
        return 0.0 if v is None else v

    @field_validator("palets", mode="before")
    @classmethod
    def _palets_nulo(cls, v):
        return 0 if v is None else v


def _bloqueos_y_avisos(req: ValidarRequest, conn) -> tuple:
    """Bloqueos + avisos de la asignación propuesta. Lógica compartida por /validar y /mover."""
    bloqueos: list[dict] = []
    avisos: list[dict] = []
    terminal = _fecha(req.codigo)

    # ---- BLOQUEO 1: remolque/semirremolque ocupado en un viaje no finalizado ----
    remolques_ocupados = _vehiculos_en_curso(exclude_trip_id=req.trip_id or None)
    for etiqueta, rid in (("Semirremolque", req.semirremolque_id), ("Remolque", req.remolque_id)):
        rid = _fecha(rid)
        if rid and rid in remolques_ocupados:
            bloqueos.append({"tipo": "remolque_ocupado", "mensaje": f"{etiqueta} {rid} ocupado en otro viaje activo"})

    # ---- BLOQUEO 2: tractora con otro viaje solapado en el tiempo ----
    if terminal and req.inicio and req.fin:
        otros = conn.execute(
            f"SELECT id, fecha_esperada_carga, fecha_esperada_descarga FROM trips "
            f"WHERE terminal = ? AND id != ? AND COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL}",
            (terminal, req.trip_id or ""),
        ).fetchall()
        for o in otros:
            if _solapan(req.inicio, req.fin, o["fecha_esperada_carga"], o["fecha_esperada_descarga"]):
                bloqueos.append({
                    "tipo": "tractora_solapada",
                    "mensaje": f"La tractora {terminal} ya tiene el viaje {o['id']} solapado en ese intervalo",
                })
                break

    # ---- BLOQUEO 3: conductor de ausencia ----
    if req.conductor_id is not None and req.inicio:
        aus = conn.execute(
            "SELECT 1 FROM conductores c JOIN ausencias_empleados a ON a.empleado_id = c.empleado_id "
            "WHERE c.id = ? AND ? >= a.fecha_inicio AND ? <= a.fecha_fin",
            (req.conductor_id, req.inicio[:10], req.inicio[:10]),
        ).fetchone()
        if aus:
            bloqueos.append({"tipo": "conductor_ausente", "mensaje": "El conductor tiene una ausencia en ese periodo"})

    # ---- AVISO 1: conducción legal ajustada (tacógrafo, reutiliza _dstat_terminal) ----
    if terminal:
        # Resuelve el terminal_trimble (ID del proveedor de telemetría) desde la matrícula.
        vrow = conn.execute("SELECT terminal_trimble FROM vehiculos WHERE codigo=? LIMIT 1", (terminal,)).fetchone()
        ttrim = (vrow["terminal_trimble"] if vrow and vrow["terminal_trimble"] else terminal)
        stats = _dstat_terminal(ttrim)
        if stats and req.fin:
            # Duración estimada del viaje: de la ventana o del tiempo PTV (no llega aquí; usamos la ventana).
            duracion_min = 0.0
            if req.inicio and req.fin:
                try:
                    i = datetime.datetime.fromisoformat(req.inicio if "T" in req.inicio else req.inicio + "T00:00")
                    f = datetime.datetime.fromisoformat(req.fin if "T" in req.fin else req.fin + "T00:00")
                    duracion_min = max(0.0, (f - i).total_seconds() / 60.0)
                except ValueError:
                    duracion_min = 0.0
            coupure = float(stats.get("driving_coupure") or 0)
            day = float(stats.get("day_driving") or 0)
            long_count = int(stats.get("week_long_driving_count") or 0)
            restante_semana = float(stats.get("remaining_week_available") or 0)
            if coupure >= _MAX_CONDUCCION_CONTINUA_MIN:
                avisos.append({"tipo": "conduccion_continua", "mensaje": "Conducción continua agotada (4,5 h): el conductor debe pausar 45 min"})
            limite_diario = _EXT_DIA_CONDUCCION_MIN if long_count < 2 else _MAX_DIA_CONDUCCION_MIN
            restante = min(limite_diario - day, restante_semana)
            if duracion_min > 0 and duracion_min > restante:
                avisos.append({
                    "tipo": "conduccion_insuficiente",
                    "mensaje": f"Conducción legal insuficiente: quedan {round(max(0.0, restante), 0)} min y el viaje dura ~{round(duracion_min, 0)} min",
                })

    # ---- AVISO 2: ITV o seguro caducados (tractora + semirremolque) ----
    hoy = datetime.date.today().isoformat()
    ids_a_revisar = [i for i in (terminal, _fecha(req.semirremolque_id)) if i]
    for vid in ids_a_revisar:
        vr = conn.execute(
            "SELECT matricula, fecha_caducidad_itv, fecha_caducidad_seguro FROM vehiculos "
            "WHERE matricula = ? OR codigo = ? OR terminal_trimble = ? LIMIT 1",
            (vid, vid, vid),
        ).fetchone()
        if not vr:
            continue
        for campo, etiqueta in (("fecha_caducidad_itv", "ITV"), ("fecha_caducidad_seguro", "Seguro")):
            v = _fecha(vr[campo])
            if v and v <= hoy:
                avisos.append({"tipo": f"{etiqueta.lower()}_caducada", "mensaje": f"{etiqueta} de {vr['matricula'] or vid} caducada ({v})"})

    # ---- AVISO 3: capacidad (kg o palés) superada (semirremolque) ----
    semi = _fecha(req.semirremolque_id)
    if semi:
        cap = conn.execute(
            "SELECT matricula, capacidad_peso, capacidad_palets FROM vehiculos "
            "WHERE matricula = ? OR codigo = ? OR terminal_trimble = ? LIMIT 1", (semi, semi, semi),
        ).fetchone()
        if cap:
            if float(cap["capacidad_peso"] or 0) > 0 and req.kilos > float(cap["capacidad_peso"]):
                avisos.append({"tipo": "capacidad_kg", "mensaje": f"Supera la capacidad de peso de {cap['matricula'] or semi} ({req.kilos} kg > {cap['capacidad_peso']} kg)"})
            if int(cap["capacidad_palets"] or 0) > 0 and req.palets > int(cap["capacidad_palets"]):
                avisos.append({"tipo": "capacidad_palets", "mensaje": f"Supera la capacidad de palés de {cap['matricula'] or semi} ({req.palets} > {cap['capacidad_palets']})"})

    return bloqueos, avisos


@router.post("/api/planificacion/validar")
def validar(req: ValidarRequest, conn=Depends(get_conn)):
    """Devuelve {ok, bloqueos, avisos} para la asignación propuesta. NO asigna."""
    bloqueos, avisos = _bloqueos_y_avisos(req, conn)
    return {"ok": not bloqueos, "bloqueos": bloqueos, "avisos": avisos}


class MoverRequest(ValidarRequest):
    force: bool = False


@router.post("/api/planificacion/mover")
def mover(req: MoverRequest, conn=Depends(get_conn)):
    """Mueve un viaje en el tablero (asignar tractora/semi/conductor/horas o devolver a
    Pendientes). UNA sola llamada por movimiento; valida en el servidor y, si el viaje ya
    está en Trimble, lo desvincula del terminal (unassignTrips) antes de tocar la BD."""
    trip_id = (req.trip_id or "").strip()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})

    estado = (row["estado"] or "").strip()
    if estado.lower() in _ESTADOS_FINALES:
        raise HTTPException(status_code=409, detail={"error": f"El viaje está en estado final ({estado})"})

    en_curso = estado in _ESTADOS_EN_CURSO
    if en_curso and not req.force:
        raise HTTPException(status_code=409, detail={"error": f"El conductor ya ha empezado este viaje ({estado})"})

    # Validación server-side (la misma que /validar): bloqueos → 409 con los motivos.
    bloqueos, avisos = _bloqueos_y_avisos(req, conn)
    if bloqueos:
        raise HTTPException(status_code=409, detail={"bloqueos": bloqueos})

    terminal_anterior = (row["terminal"] or "").strip()
    terminal_nuevo = (req.codigo or "").strip()
    in_trimble = estado == "enviado" or en_curso
    # Misma tractora (cambio de hora/semi/conductor sin mover de camión): NO desasignar,
    # NO tocar Trimble, mantener el estado y marcar pendiente de reenvío.
    misma_tractora = in_trimble and terminal_nuevo == terminal_anterior and terminal_nuevo != ""

    # Si está en Trimble y cambia de tractora (o vuelve a Pendientes) → desvincular ANTES de la BD.
    if in_trimble and not misma_tractora and terminal_nuevo != terminal_anterior:
        r = get_client().unassign_trips([trip_id])
        if not r["ok"]:
            raise HTTPException(status_code=502, detail={
                "error": f"Trimble: no se pudo quitar del terminal ({r['fault'] or r['status']})"})

    conductor_id = req.conductor_id
    conductor_nombre = ""
    if conductor_id is not None:
        c = conn.execute("SELECT nombre FROM conductores WHERE id=?", (conductor_id,)).fetchone()
        conductor_nombre = c["nombre"] if c else ""

    semirremolque = (req.semirremolque_id or "").strip()
    inicio = (req.inicio or "").strip()
    fin = (req.fin or "").strip()

    # Estado final: sin_asignar al desasignar; misma tractora → se mantiene (enviado/en curso).
    estado_nuevo = estado if misma_tractora else "sin_asignar"
    pendiente_reenvio = misma_tractora
    payload = json.loads(row["payload"] or "{}")
    if pendiente_reenvio:
        payload["pendiente_reenvio"] = True

    conn.execute(
        "UPDATE trips SET terminal=?, semirremolque_id=?, conductor=?, conductor_id=?, "
        "fecha_esperada_carga=?, fecha_esperada_descarga=?, estado=?, payload=? WHERE id=?",
        (terminal_nuevo or None, semirremolque or None, conductor_nombre or None, conductor_id,
         inicio or None, fin or None, estado_nuevo, json.dumps(payload), trip_id),
    )
    conn.commit()

    # Liberar el viaje activo del terminal anterior solo si SALIÓ de Trimble (no en misma tractora).
    if in_trimble and not misma_tractora:
        _del_viaje_activo(terminal_anterior)

    _auditar(conn, "trips", trip_id, "mover", None,
             {"terminal": terminal_anterior, "semirremolque_id": row["semirremolque_id"],
              "conductor_id": row["conductor_id"], "estado": estado},
             {"terminal": terminal_nuevo or None, "semirremolque_id": semirremolque or None,
              "conductor_id": conductor_id, "estado": estado_nuevo})

    return {
        "ok": True,
        "viaje": {
            "id": trip_id, "terminal": terminal_nuevo or None,
            "semirremolque_id": semirremolque or None, "conductor": conductor_nombre or None,
            "conductor_id": conductor_id, "estado": estado_nuevo,
            "fecha_esperada_carga": inicio or None, "fecha_esperada_descarga": fin or None,
            "pendiente_reenvio": pendiente_reenvio,
        },
        "avisos": avisos,
    }


@router.get("/api/trimble/fake-calls")
def trimble_fake_calls():
    """Registro de llamadas SOAP en modo TMS_TRIMBLE_FAKE=1 (para los e2e)."""
    from soap_client import _fake_calls, _trimble_fake
    if not _trimble_fake():
        raise HTTPException(status_code=404, detail="Modo falso de Trimble no activo")
    return {"calls": _fake_calls()}


@router.post("/api/trimble/fake-reset")
def trimble_fake_reset():
    """Limpia el registro de llamadas SOAP en modo falso."""
    from soap_client import _fake_reset, _trimble_fake
    if not _trimble_fake():
        raise HTTPException(status_code=404, detail="Modo falso de Trimble no activo")
    _fake_reset()
    return {"ok": True}
