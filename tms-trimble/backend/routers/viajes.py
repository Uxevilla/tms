"""
Router de viajes."""
import base64, csv, datetime, io, json, math, os, re, secrets, subprocess, tempfile, time, threading, urllib.parse, urllib.request, uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import psycopg2
import redis
import config

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from models import *
from db import *
from core import *
from security import *
from tenancy import *
from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES, TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD
from clients.trimble import get_client, _client_cache
from clients.transfollow import get_transfollow_client, _tf_cache
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from clients.geocoding import _buscar_photon, reverse_geocode
from services.contabilidad import _categoria_cuenta, _next_referencia, _auditar, _post_asiento, _registrar_asiento, _gasto_subcontrata, _facturar_viaje, _norm_fecha, _norm_total
from services.viajes import _save_trip, _save_tramos, _save_paradas, _upsert_direccion, _peaje_rate, _vehiculo_peaje_categoria, _vehiculos_en_curso, _puntos_del_viaje, _build_trip, _calcular_ruta, _viaje_payload, _guardar_documentos_pedido, _valorar_viaje, _crear_pedido, _enviar_viaje
from services.telemetria import _get_redis, _set_viaje_activo, _del_viaje_activo, _json_safe, _viajes_snapshot
from services.sync import _query_terminal_states, _sync_status, _get_sync_state, _set_sync_state, _parse_props, _save_file, _extraer_reporte_xml, _extraer_documento_ecmr, _guardar_documento_entrega, _publicar_estado, _odometro_vehiculo, _aplicar_estado_viaje, _cerrar_viaje, _cerrar_viaje_por_ecmr, _entrega_confirmada, _sync_files, _sync_mensajes
from services.tacografo import _decode_dstat, _ingestar_dstat, _dstat_terminal, _chequear_conduccion_legal
from services.rrhh import _imputar_dieta_nomina, _procesar_dieta, _sync_conductor
from services.mantenimiento import _insertar_alerta_publica, _revisar_caducidades, _revisar_revision_fecha, _revisar_mantenimiento
from services.mensajeria import _save_mensaje, _store_mensaje, _extraer_pales, _procesar_pales, _webhook_autenticado, _direccion_dict
from services.ocr import _parse_ticket, _parse_documento, _pdf_a_texto, _regex_matricula, _regex_litros, _regex_importe, _regex_fecha
from services.empresa import _empresa
from services.documentos import _guardar_archivo, _leer_archivo, _borrar_archivo

router = APIRouter(dependencies=[Depends(require_role(["admin", "dispatcher"]))])


@router.post("/api/trips/{trip_id}/documentos")
def add_trip_documentos(trip_id: str, req: dict, conn = Depends(get_conn)):
    """Guarda los PDF del pedido (se suben al DMS al asignar el viaje)."""
    docs = req.get("documentos") or []
    if not docs:
        return {"ok": True, "guardados": 0}
    if not conn.execute("SELECT id FROM trips WHERE id=?", (trip_id,)).fetchone():
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    guardados = 0
    for d in docs:
        contenido = d.get("contenido") or ""
        if not contenido:
            continue
        nombre = (d.get("nombre") or "documento.pdf").rsplit("/", 1)[-1][:120] or "documento.pdf"
        name = f"{uuid.uuid4().hex[:10]}__{nombre}"
        g = _guardar_archivo(name, contenido)
        if not g:
            continue
        storage_key, sha, nbytes, _mime = g
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, storage_key, sha256, bytes) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (trip_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf", storage_key, sha, nbytes),
        )
        guardados += 1
    conn.commit()
    return {"ok": True, "guardados": guardados}




@router.get("/api/telemetria/activa")
def api_telemetria_activa(user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Última coordenada de TODOS los vehículos + disponibilidad (Libre / En_Viaje)."""
    rows = conn.execute(
        f"WITH ultima AS ("
        f"  SELECT DISTINCT ON (vehiculo_id) vehiculo_id, lat, lng, speed_kmh, heading, odometer_km, time "
        f"  FROM telemetria.posiciones_gps ORDER BY vehiculo_id, time DESC"
        f"), activa AS ("
        f"  SELECT DISTINCT ON (terminal) terminal, id, estado, fecha_esperada_descarga, "
        f"         conductor, matricula "
        f"  FROM trips WHERE (estado = 'enviado' OR estado IN ('Llegada_Origen','Cargando','En_Transito','Llegada_Destino','Descargando')) AND anulado_at IS NULL "
        f"  ORDER BY terminal, creado DESC"
        f"), dstat AS ("
        f"  SELECT DISTINCT ON (vehiculo_id) vehiculo_id, did "
        f"  FROM tacografo_dstat WHERE decode_ok ORDER BY vehiculo_id, COALESCE(time, creado) DESC"
        f") "
        f"SELECT u.vehiculo_id, u.lat, u.lng, u.speed_kmh AS velocidad, "
        f"       u.heading, u.odometer_km, u.time, "
        f"       a.id AS viaje_id, COALESCE(a.estado,'') AS estado, "
        f"       a.fecha_esperada_descarga AS fecha_esperada_descarga, "
        f"       COALESCE(v.matricula, a.matricula, '') AS matricula, "
        f"       COALESCE(a.conductor,'') AS conductor, "
        f"       c.nombre AS conductor_taco "
        f"FROM ultima u "
        f"LEFT JOIN vehiculos v ON v.terminal_trimble = u.vehiculo_id "
        f"LEFT JOIN activa a ON a.terminal = v.codigo "
        f"LEFT JOIN dstat d ON d.vehiculo_id = u.vehiculo_id "
        f"LEFT JOIN conductores c ON c.did = d.did "
        f"ORDER BY u.time DESC"
    ).fetchall()
    out = []
    for r in rows:
        tiene_viaje = bool(r["viaje_id"])
        out.append({
            "vehiculo_id": r["vehiculo_id"],
            "viaje_id": r["viaje_id"],
            "lat": r["lat"], "lng": r["lng"], "velocidad": r["velocidad"],
            "heading": r["heading"],
            "odometer_km": (r["odometer_km"] / 1000.0) if r["odometer_km"] is not None else None,
            "conductor_taco": r["conductor_taco"] or "",
            "ultima_telemetria": r["time"].isoformat() if r["time"] else None,
            "disponibilidad": "En_Viaje" if tiene_viaje else "Libre",
            "estado": _map_estado(r["estado"]) if tiene_viaje else "",
            "fecha_esperada_descarga": r["fecha_esperada_descarga"] or "",
            "matricula": r["matricula"] or "", "conductor": r["conductor"] or "",
        })
    return {"telemetria": out}




@router.get("/api/telemetria/trayectoria")
def api_telemetria_trayectoria(user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Últimos 20 puntos por vehículo (rastro) para pintar polilíneas en el mapa."""
    rows = conn.execute(
        "SELECT vehiculo_id, lat, lng FROM ("
        "  SELECT vehiculo_id, lat, lng, "
        "         ROW_NUMBER() OVER (PARTITION BY vehiculo_id ORDER BY time DESC) AS rn "
        "  FROM telemetria.posiciones_gps"
        ") x WHERE rn <= 20 ORDER BY vehiculo_id, rn DESC"
    ).fetchall()
    out: dict = {}
    for r in rows:
        out.setdefault(r["vehiculo_id"], []).append([r["lat"], r["lng"]])
    return {"trayectorias": out}




@router.get("/api/viajes")
def api_viajes(user: dict = Depends(require_role(["admin", "dispatcher"]))):
    snap = _viajes_snapshot()
    return {"viajes": list(snap.values())}




@router.post("/api/trips/{trip_id}/asignar")
def asignar_trip(trip_id: str, req: AsignarRequest, conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if row["anulado_at"]:
        raise HTTPException(status_code=409, detail={"error": "El viaje está anulado y no se puede editar."})
    if (row["estado"] or "") not in ("sin_asignar", ""):
        raise HTTPException(status_code=409, detail={"error": f"El viaje ya está asignado (estado: {row['estado']})"})

    viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    codigo = (req.codigo or "").strip()
    if not codigo:
        raise HTTPException(status_code=400, detail={"error": "Indica la tractora para asignar."})

    # Fusionar ediciones en línea (columnas de trips) sobre el payload original
    viaje.cliente = row["cliente"] or viaje.cliente
    viaje.tipo_carga = row["tipo_carga"] or viaje.tipo_carga
    viaje.precio = float(row["precio"] or 0)
    viaje.gastos = float(row["gastos"] or 0)
    viaje.iva = float(row["iva"] or 0)
    viaje.factura = row["factura"] or viaje.factura
    viaje.conductor = req.conductor or row["conductor"] or viaje.conductor or ""
    viaje.conductor_id = req.conductor_id if req.conductor_id is not None else row["conductor_id"]

    viaje.matricula = row["matricula"] or viaje.matricula or ""
    viaje.semirremolque_id = (req.semirremolque_id or row["semirremolque_id"] or "").strip()
    viaje.remolque_id = (req.remolque_id or row["remolque_id"] or "").strip()
    viaje.conduccion_acumulada_min = req.conduccion_acumulada_min
    viaje.ecmr_provider = req.ecmr_provider
    viaje.ecmr_id = req.ecmr_id
    viaje.documentos = req.documentos

    # Safe-Dispatching: valida la conducción legal antes de despachar (saltable con force=true).
    if not req.force:
        _chequear_conduccion_legal(viaje, row)

    return _enviar_viaje(trip_id, viaje, codigo, viaje.semirremolque_id, viaje.remolque_id)




@router.post("/api/tarifas")
def create_tarifa(t: TarifaRequest, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO tarifas (nombre, tipo, precio, cliente_id, activo, creado_en) VALUES (?,?,?,?,?,?)",
        (t.nombre.strip(), t.tipo.strip().lower(), float(t.precio or 0), t.cliente_id, t.activo,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    return {"ok": True}




@router.post("/api/trips")
def create_trip(viaje: ViajeRequest):
    trip_id = "VIAJE-" + uuid.uuid4().hex[:10].upper()
    matricula = (viaje.matricula or "").strip()
    semirremolque = (viaje.semirremolque_id or "").strip()
    remolque = (viaje.remolque_id or "").strip()

    # Sin camión asignado → pedido (se planifica, aún no se envía a Trimble)
    if not matricula:
        return _crear_pedido(trip_id, viaje)

    return _enviar_viaje(trip_id, viaje, matricula, semirremolque, remolque)




@router.delete("/api/trips/{trip_id}/documentos/{file_id}")
def del_trip_documento(trip_id: str, file_id: int, conn = Depends(get_conn)):
    row = conn.execute(
        "SELECT storage_key FROM files WHERE id=? AND trip_id=? AND source='pedido'", (file_id, trip_id)
    ).fetchone()
    conn.execute("DELETE FROM files WHERE id=? AND trip_id=? AND source='pedido'", (file_id, trip_id))
    conn.commit()
    if row and row["storage_key"]:
        refs = conn.execute(
            "SELECT 1 FROM files WHERE storage_key=? AND id<>?", (row["storage_key"], file_id)
        ).fetchone()
        if not refs:
            _borrar_archivo(row["storage_key"])
    return {"ok": True}




@router.delete("/api/tarifas/{tarifa_id}")
def delete_tarifa(tarifa_id: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM tarifas WHERE id=?", (tarifa_id,))
    conn.commit()
    return {"ok": True}




@router.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str, force: bool = False, motivo: str = "", user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Elimina o anula un viaje. Los viajes finalizados solo puede eliminarlos un administrador.

    Un viaje con documentos o facturado NO se borra físicamente: se ANULA (soft delete)
    conservando documentos y mensajes (son prueba de cobro). force=True fuerza el borrado
    físico (solo admin)."""
    row = conn.execute("SELECT estado, terminal, factura FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    estado = (row["estado"] or "").lower()
    if estado in _ESTADOS_FINALES and user.get("rol") != "admin":
        raise HTTPException(status_code=403, detail={"error": "Solo un administrador puede eliminar un viaje finalizado."})
    if force and user.get("rol") != "admin":
        raise HTTPException(status_code=403, detail={"error": "Solo un administrador puede forzar el borrado físico."})

    tiene_docs = conn.execute("SELECT 1 FROM files WHERE trip_id=? LIMIT 1", (trip_id,)).fetchone()
    tiene_factura = bool(row["factura"])

    in_trimble = estado == "enviado" or estado in ("llegada_origen", "cargando", "en_transito", "llegada_destino", "descargando")

    # No borrar (ni siquiera con force) un viaje con factura EMITIDA: primero rectificativa.
    emitida = conn.execute(
        "SELECT f.numero FROM finanzas.facturas f WHERE f.trip_id=? AND COALESCE(f.estado,'') NOT IN ('','borrador') "
        "UNION SELECT f.numero FROM finanzas.factura_lineas fl JOIN finanzas.facturas f ON f.id=fl.factura_id "
        "WHERE fl.trip_id=? AND COALESCE(f.estado,'') NOT IN ('','borrador') LIMIT 1", (trip_id, trip_id),
    ).fetchone()
    if emitida:
        raise HTTPException(status_code=409, detail={
            "error": f"El viaje tiene la factura {emitida['numero']} emitida: anúlala con una rectificativa antes."})

    if (tiene_docs or tiene_factura) and not force:
        # Soft delete: conservar documentos y mensajes (prueba de cobro).
        motivo = (motivo or "").strip()
        if not motivo:
            raise HTTPException(status_code=400, detail={"error": "El motivo de anulación es obligatorio."})
        # Si estaba en el terminal, borrarlo de Trimble ANTES de anular (misma lógica que el borrado:
        # si Trimble falla y no hay force, no se anula).
        if in_trimble:
            r = get_client().remove_trips([trip_id])
            if not r["ok"] and not force:
                raise HTTPException(status_code=502, detail={
                    "error": f"Trimble: no se pudo borrar del terminal ({r['fault'] or r['status']})"})
        usuario = user.get("usuario") or user.get("sub") or ""
        # Borrar el borrador pendiente (y sus líneas) en la misma transacción.
        for fid in [r["id"] for r in conn.execute(
            "SELECT id FROM finanzas.facturas WHERE trip_id=? AND COALESCE(estado,'')='borrador'", (trip_id,)
        ).fetchall()]:
            conn.execute("DELETE FROM finanzas.factura_lineas WHERE factura_id=?", (fid,))
            conn.execute("DELETE FROM finanzas.facturas WHERE id=?", (fid,))
        conn.execute(
            "UPDATE trips SET anulado_at=now(), anulado_por=?, anulado_motivo=? WHERE id=?",
            (usuario, motivo, trip_id),
        )
        _auditar(conn, "trips", trip_id, "anular", usuario, despues={"motivo": motivo})
        conn.commit()
        return {"ok": True, "trip_id": trip_id, "anulado": True}

    # Si estaba en el terminal, borrarlo de Trimble ANTES (si Trimble falla, la BD no cambia).
    if in_trimble:
        r = get_client().remove_trips([trip_id])
        if not r["ok"] and not force:
            raise HTTPException(status_code=502, detail={
                "error": f"Trimble: no se pudo borrar del terminal ({r['fault'] or r['status']})"})

    # Limpiar tablas hijas sin ON DELETE CASCADE.
    conn.execute("DELETE FROM files WHERE trip_id=?", (trip_id,))
    conn.execute("DELETE FROM mensajes WHERE trip_id=?", (trip_id,))
    conn.execute("DELETE FROM finanzas.gastos WHERE trip_id=?", (trip_id,))
    conn.execute("UPDATE telemetria.posiciones_gps SET viaje_id=NULL WHERE viaje_id=?", (trip_id,))
    # paradas y tramos se borran por ON DELETE CASCADE.
    conn.execute("DELETE FROM trips WHERE id=?", (trip_id,))
    conn.commit()
    return {"ok": True, "trip_id": trip_id, "anulado": False}


@router.post("/api/trips/{trip_id}/reactivar")
def reactivar_trip(trip_id: str, user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Reactivar un viaje anulado (solo admin), con auditoría.

    Si al anular se borró del terminal Trimble (estado enviado/en curso), vuelve a
    'sin_asignar' conservando tractora y horas, y marca pendiente de reenvío.
    """
    row = conn.execute("SELECT anulado_at, estado, payload FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if not row["anulado_at"]:
        raise HTTPException(status_code=409, detail={"error": "El viaje no está anulado."})
    usuario = user.get("usuario") or user.get("sub") or ""
    estado = (row["estado"] or "").lower()
    requiere_reenvio = estado == "enviado" or estado in ("llegada_origen", "cargando", "en_transito", "llegada_destino", "descargando")
    if requiere_reenvio:
        payload = json.loads(row["payload"] or "{}")
        payload["pendiente_reenvio"] = True
        conn.execute(
            "UPDATE trips SET anulado_at=NULL, anulado_por=NULL, anulado_motivo=NULL, estado='sin_asignar', payload=? WHERE id=?",
            (json.dumps(payload), trip_id),
        )
    else:
        conn.execute(
            "UPDATE trips SET anulado_at=NULL, anulado_por=NULL, anulado_motivo=NULL WHERE id=?",
            (trip_id,),
        )
    _auditar(conn, "trips", trip_id, "reactivar", usuario)
    conn.commit()
    return {"ok": True, "trip_id": trip_id, "reactivado": True, "requiere_reenvio": requiere_reenvio}




@router.post("/api/trips/{trip_id}/duplicar")
def duplicar_trip(trip_id: str):
    """Duplica un viaje: copia los datos pero lo crea SIN asignar (sin vehículo ni conductor)."""
    conn = _db()
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    try:
        viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    except Exception:
        # Viajes sincronizados de Trimble sin payload estructurado → reconstruir desde columnas
        viaje = ViajeRequest(
            origen=Direccion(ciudad=row["origen"] or "", nombre=row["origen"] or ""),
            destino=Direccion(ciudad=row["destino"] or "", nombre=row["destino"] or ""),
            tipo_carga=row["tipo_carga"] or "General",
            cliente=row["cliente"] or "",
            cliente_id=row["cliente_id"],
            precio=float(row["precio"] or 0),
            iva=float(row["iva"] or 21),
        )
    # Forzar sin asignar (sin vehículo ni conductor)
    viaje.matricula = ""
    viaje.conductor = ""
    viaje.conductor_id = None
    viaje.semirremolque_id = ""
    viaje.remolque_id = ""
    viaje.factura = ""
    viaje.estado_pago = "pendiente"
    viaje.documentos = []
    new_id = "VIAJE-" + uuid.uuid4().hex[:10].upper()
    _crear_pedido(new_id, viaje)
    # Copiar los documentos PDF del pedido original
    conn = _db()
    docs = conn.execute(
        "SELECT name, storage_key, sha256, bytes FROM files WHERE trip_id=? AND source='pedido'", (trip_id,)
    ).fetchall()
    for d in docs:
        base = d["name"].split("__", 1)[1] if "__" in d["name"] else d["name"]
        name = f"{uuid.uuid4().hex[:10]}__{base}"
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, storage_key, sha256, bytes) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (new_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf",
             d["storage_key"], d["sha256"], d["bytes"]),
        )
    conn.commit()
    conn.close()
    return {"ok": True, "trip_id": new_id}




@router.post("/api/trips/{trip_id}/enviar")
def enviar_trip(trip_id: str, force: bool = False, conn = Depends(get_conn)):
    """Envía (o reenvía) el viaje al terminal Trimble asignado.

    `force=true` (query) salta el chequeo de Safe-Dispatching.
    """
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if row["anulado_at"]:
        raise HTTPException(status_code=409, detail={"error": "El viaje está anulado."})
    viaje = ViajeRequest(**json.loads(row["payload"] or "{}"))
    codigo = (row["terminal"] or "").strip()
    if not codigo:
        raise HTTPException(status_code=400, detail={"error": "Asigna la tractora antes de enviar el viaje a Trimble."})
    # Fusionar el estado actual de la fila sobre el payload original.
    viaje.cliente = row["cliente"] or viaje.cliente
    viaje.precio = float(row["precio"] or 0)
    viaje.gastos = float(row["gastos"] or 0)
    viaje.iva = float(row["iva"] or 0)
    viaje.conductor = row["conductor"] or viaje.conductor or ""
    viaje.conductor_id = row["conductor_id"] if row["conductor_id"] is not None else viaje.conductor_id
    viaje.matricula = row["matricula"] or viaje.matricula or ""
    viaje.fecha_esperada_carga = row["fecha_esperada_carga"] or viaje.fecha_esperada_carga
    viaje.fecha_esperada_descarga = row["fecha_esperada_descarga"] or viaje.fecha_esperada_descarga
    viaje.semirremolque_id = (row["semirremolque_id"] or "").strip()
    viaje.remolque_id = (row["remolque_id"] or "").strip()

    # Safe-Dispatching: valida la conducción legal antes de despachar (saltable con force=true).
    if not force:
        _chequear_conduccion_legal(viaje, row)

    resp = _enviar_viaje(trip_id, viaje, codigo, viaje.semirremolque_id, viaje.remolque_id)
    # Limpiar la marca de reenvío SOLO si el envío fue bien (releyendo el payload actual).
    if resp.get("estado") == "enviado":
        actual = conn.execute("SELECT payload FROM trips WHERE id=?", (trip_id,)).fetchone()
        payload = json.loads((actual and actual["payload"]) or "{}")
        if payload.pop("pendiente_reenvio", None):
            conn.execute("UPDATE trips SET payload=? WHERE id=?", (json.dumps(payload), trip_id))
            conn.commit()
    return resp


@router.post("/api/trips/{trip_id}/quitar-terminal")
def quitar_terminal(trip_id: str, conn = Depends(get_conn)):
    """Quita el viaje del terminal (unassignTrips) y lo deja planificado, CONSERVANDO
    la tractora asignada, para volver a enviarlo cuando toque (menú contextual)."""
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    estado = (row["estado"] or "").strip()
    in_trimble = estado == "enviado" or estado in ("Llegada_Origen", "Cargando", "En_Transito", "Llegada_Destino", "Descargando")
    if not in_trimble:
        raise HTTPException(status_code=409, detail={"error": "El viaje no está en el terminal"})
    r = get_client().unassign_trips([trip_id])
    if not r["ok"]:
        raise HTTPException(status_code=502, detail={
            "error": f"Trimble: no se pudo quitar del terminal ({r['fault'] or r['status']})"})
    terminal = row["terminal"] or ""
    conn.execute("UPDATE trips SET estado='sin_asignar' WHERE id=?", (trip_id,))
    conn.commit()
    _del_viaje_activo(terminal)
    _auditar(conn, "trips", trip_id, "quitar_terminal", None, {"estado": estado}, {"estado": "sin_asignar"})
    return {"ok": True}




@router.get("/api/trips/status")
def trips_status(conn = Depends(get_conn)):
    _sync_status()
    rows = conn.execute("SELECT * FROM trips ORDER BY creado DESC LIMIT 100").fetchall()
    return {"viajes": [dict(r) for r in rows]}


@router.get("/api/trips/{trip_id}")
def get_trip(trip_id: str, conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    paradas = conn.execute("SELECT * FROM operaciones.paradas WHERE trip_id=? ORDER BY orden", (trip_id,)).fetchall()
    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return {"trip": dict(row), "payload": payload, "paradas": [dict(p) for p in paradas]}




@router.get("/api/tarifas")
def list_tarifas(conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT t.id, t.nombre, t.tipo, t.precio, t.cliente_id, t.activo, t.creado_en, "
        "COALESCE(c.nombre, '') AS cliente_nombre "
        "FROM tarifas t LEFT JOIN clientes c ON c.id = t.cliente_id ORDER BY t.id"
    ).fetchall()
    return {"tarifas": [dict(r) for r in rows]}




@router.get("/api/trips/{trip_id}/documentos")
def list_trip_documentos(trip_id: str, conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, name, content_b64, storage_key, sha256, source, driver, lid, formato, mime, "
        "tipo_documento, estado_descarga, intentos, ultimo_error, ftime, report_id, question_id, mensaje_id, "
        "revisado_at, revisado_por, anulado_at, paginas "
        "FROM files WHERE trip_id=? AND anulado_at IS NULL ORDER BY ftime DESC, id DESC", (trip_id,)
    ).fetchall()
    docs = []
    for r in rows:
        c = _leer_archivo(r["storage_key"], r.get("sha256") or "") if r["storage_key"] else (r["content_b64"] or "")
        name = r["name"]
        nombre = name.split("__", 1)[1] if "__" in name else name
        docs.append({
            "id": r["id"], "nombre": nombre, "contenido": c, "size": round(len(c) * 3 / 4),
            "source": r["source"] or "", "formato": r["formato"] or "", "mime": r["mime"] or "",
            "tipo_documento": r["tipo_documento"] or "", "estado_descarga": r["estado_descarga"] or "",
            "intentos": r["intentos"] or 0, "ultimo_error": r["ultimo_error"] or "",
            "ftime": r["ftime"] or "", "report_id": r["report_id"] or "", "question_id": r["question_id"] or "",
            "mensaje_id": r["mensaje_id"] or "", "revisado_at": r["revisado_at"], "revisado_por": r["revisado_por"],
            "paginas": r["paginas"] or 0,
        })
    return {"documentos": docs}


@router.post("/api/trips/{trip_id}/documentos/{file_id}/tipo")
def reclasificar_documento(trip_id: str, file_id: int, body: dict,
                           user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Reclasificación manual de un documento (queda en documentos_auditoria)."""
    tipo = (body or {}).get("tipo_documento") or ""
    if not tipo:
        raise HTTPException(status_code=400, detail={"error": "Indica el tipo de documento."})
    row = conn.execute("SELECT id FROM files WHERE id=? AND trip_id=?", (file_id, trip_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Documento no encontrado."})
    usuario = user.get("usuario") or user.get("sub") or ""
    conn.execute("UPDATE files SET tipo_documento=? WHERE id=?", (tipo, file_id))
    conn.execute(
        "INSERT INTO documentos_auditoria (file_id, accion, usuario, detalle) VALUES (?,?,?,?)",
        (file_id, "reclasificar", usuario, json.dumps({"tipo_documento": tipo})),
    )
    conn.commit()
    return {"ok": True}


@router.post("/api/trips/{trip_id}/documentos/{file_id}/revisar")
def marcar_documento_revisado(trip_id: str, file_id: int, body: dict,
                              user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Marca/desmarca un documento como revisado."""
    row = conn.execute("SELECT id FROM files WHERE id=? AND trip_id=?", (file_id, trip_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Documento no encontrado."})
    usuario = user.get("usuario") or user.get("sub") or ""
    revisado = bool((body or {}).get("revisado"))
    if revisado:
        conn.execute("UPDATE files SET revisado_at=now(), revisado_por=? WHERE id=?", (usuario, file_id))
    else:
        conn.execute("UPDATE files SET revisado_at=NULL, revisado_por=NULL WHERE id=?", (file_id,))
    conn.commit()
    return {"ok": True, "revisado": revisado}


@router.post("/api/trips/{trip_id}/documentos/manual")
def subir_documento_manual(trip_id: str, body: dict,
                           user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Subida manual de un documento (papel escaneado en oficina). Body {nombre, tipo_documento, contenido_b64}."""
    nombre = (body or {}).get("nombre") or ""
    tipo = (body or {}).get("tipo_documento") or ""
    b64 = (body or {}).get("contenido_b64") or ""
    if not nombre or not b64:
        raise HTTPException(status_code=400, detail={"error": "Nombre y contenido son obligatorios."})
    if not conn.execute("SELECT id FROM trips WHERE id=?", (trip_id,)).fetchone():
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    import uuid as _uuid
    name = f"{_uuid.uuid4().hex[:10]}__{nombre}"
    g = _guardar_archivo(name, b64)
    if not g:
        raise HTTPException(status_code=400, detail={"error": "Contenido base64 no válido."})
    storage_key, sha, nbytes, mime = g
    conn.execute(
        "INSERT INTO files (trip_id, name, ftype, ftime, source, storage_key, sha256, bytes, mime, estado_descarga, tipo_documento, vinculado_por) "
        "VALUES (?,?,3,?,?,?,?,?,?,'descargado',?,'manual')",
        (trip_id, name, datetime.datetime.utcnow().isoformat() + "Z", user.get("usuario") or user.get("sub") or "",
         storage_key, sha, nbytes, mime, tipo or "otro"),
    )
    conn.commit()
    return {"ok": True, "id": name}


@router.get("/api/documentos/sin-viaje")
def documentos_sin_viaje(user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    """Bandeja de ficheros sin viaje asignado (asignación manual con auditoría)."""
    rows = conn.execute(
        "SELECT id, name, source, driver, lid, ftime, tipo_documento, estado_descarga, mime, report_id, question_id "
        "FROM files WHERE trip_id IS NULL AND anulado_at IS NULL ORDER BY ftime DESC LIMIT 200"
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["nombre"] = d["name"].split("__", 1)[1] if "__" in d["name"] else d["name"]
        out.append(d)
    return {"documentos": out}


@router.post("/api/documentos/sin-viaje/{file_id}/vincular")
def vincular_documento_sin_viaje(file_id: int, body: dict,
                                 user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Asigna manualmente un fichero sin viaje a un viaje (auditoría)."""
    trip_id = (body or {}).get("trip_id") or ""
    if not trip_id:
        raise HTTPException(status_code=400, detail={"error": "Indica el viaje de destino."})
    row = conn.execute("SELECT id FROM files WHERE id=? AND trip_id IS NULL", (file_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Fichero no encontrado o ya vinculado."})
    if not conn.execute("SELECT id FROM trips WHERE id=?", (trip_id,)).fetchone():
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    usuario = user.get("usuario") or user.get("sub") or ""
    conn.execute("UPDATE files SET trip_id=?, vinculado_por='manual' WHERE id=?", (trip_id, file_id))
    conn.execute(
        "INSERT INTO documentos_auditoria (file_id, accion, usuario, detalle) VALUES (?,?,?,?)",
        (file_id, "vincular", usuario, json.dumps({"trip_id": trip_id})),
    )
    conn.commit()
    return {"ok": True}


def _vincular_por_vehiculo_ventana(conn):
    """Vincula ficheros sin viaje (trip_id NULL) al viaje del terminal cuya franja horaria
    contiene la hora del fichero (vinculado_por='vehiculo_ventana'). Devuelve cuántos vinculó."""
    huerfanos = conn.execute(
        "SELECT id, source, vehiculo_id, ftime FROM files "
        "WHERE trip_id IS NULL AND anulado_at IS NULL AND COALESCE(source,'') <> ''"
    ).fetchall()
    n = 0
    for f in huerfanos:
        ft = (f["ftime"] or "")[:19]
        if not ft:
            continue
        veh = conn.execute(
            "SELECT codigo FROM vehiculos WHERE terminal_trimble=? OR matricula=? OR codigo=? LIMIT 1",
            (f["source"], f["source"], f["source"]),
        ).fetchone()
        codigo = (veh["codigo"] if veh else (f["vehiculo_id"] or f["source"]))
        viaje = conn.execute(
            "SELECT id FROM trips WHERE terminal=? AND anulado_at IS NULL "
            "AND fecha_esperada_carga IS NOT NULL AND fecha_esperada_carga<>'' "
            "AND fecha_esperada_descarga IS NOT NULL AND fecha_esperada_descarga<>'' "
            "AND ? >= substr(fecha_esperada_carga,1,19) AND ? <= substr(fecha_esperada_descarga,1,19) "
            "ORDER BY creado DESC LIMIT 1",
            (codigo, ft, ft),
        ).fetchone()
        if viaje:
            conn.execute(
                "UPDATE files SET trip_id=?, vinculado_por='vehiculo_ventana' WHERE id=?",
                (viaje["id"], f["id"]),
            )
            n += 1
    if n:
        conn.commit()
    return n




@router.get("/api/reverse-geocode")
@router.get("/api/trips")
def list_trips(ver_anulados: bool = False, conn = Depends(get_conn)):
    if ver_anulados:
        rows = conn.execute(
            "SELECT * FROM trips WHERE anulado_at IS NOT NULL "
            "ORDER BY anulado_at DESC NULLS LAST LIMIT 200"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trips WHERE anulado_at IS NULL ORDER BY creado DESC LIMIT 200"
        ).fetchall()
    return {"viajes": [dict(r) for r in rows]}




@router.get("/api/trips/{trip_id}/files")
def trip_files(trip_id: str, conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, name, ftype, ftime, source, driver, lid, content_b64, storage_key, sha256, "
        "estado_descarga, intentos, ultimo_error, mime, bytes, tipo_documento, paginas "
        "FROM files WHERE trip_id=? ORDER BY ftime DESC", (trip_id,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("storage_key"):
            d["content_b64"] = _leer_archivo(d["storage_key"], d.get("sha256") or "")
        out.append(d)
    return {"archivos": out}


@router.get("/api/trips/{trip_id}/documentacion")
def trip_documentacion(trip_id: str, conn = Depends(get_conn)):
    """Checklist de facturación: qué documentos faltan para facturar el viaje."""
    from services.tipos_documento import checklist_documentacion
    trip = conn.execute(
        "SELECT cliente_id FROM operaciones.trips WHERE codigo=?", (trip_id,)
    ).fetchone()
    presentes = [r["tipo_documento"] for r in conn.execute(
        "SELECT DISTINCT tipo_documento FROM files "
        "WHERE trip_id=? AND tipo_documento IS NOT NULL",
        (trip_id,),
    ).fetchall()]
    return checklist_documentacion(conn, trip["cliente_id"] if trip else None, presentes)




@router.get("/api/trips/{trip_id}/paradas")
def trip_paradas(trip_id: str, conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM operaciones.paradas WHERE trip_id=? ORDER BY orden", (trip_id,)).fetchall()
    return {"paradas": [dict(r) for r in rows]}










@router.get("/api/trips/{trip_id}/tramos")
def trip_tramos(trip_id: str, conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, orden, origen_nombre, origen_ciudad, origen_lat, origen_lng, "
        "destino_nombre, destino_ciudad, destino_lat, destino_lng, terminal, conductor, "
        "km_total, km_real, km_fuente, estado, fecha_carga, fecha_descarga "
        "FROM operaciones.tramos WHERE trip_id=? ORDER BY orden", (trip_id,)
    ).fetchall()
    return {"tramos": [dict(r) for r in rows]}








@router.put("/api/trips/{trip_id}")
def update_pedido(trip_id: str, viaje: ViajeRequest, conn = Depends(get_conn)):
    """Actualiza un pedido sin asignar (direcciones, paradas y datos). Recalcula km/peaje."""
    row = conn.execute("SELECT estado, anulado_at FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if row["anulado_at"]:
        raise HTTPException(status_code=409, detail={"error": "El viaje está anulado y no se puede editar."})
    if (row["estado"] or "") not in ("sin_asignar", ""):
        raise HTTPException(status_code=409, detail={"error": "Solo se puede editar un pedido sin asignar."})
    return _crear_pedido(trip_id, viaje)




@router.put("/api/tarifas/{tarifa_id}")
def update_tarifa(tarifa_id: int, t: TarifaRequest, conn = Depends(get_conn)):
    conn.execute(
        "UPDATE tarifas SET nombre=?, tipo=?, precio=?, cliente_id=?, activo=? WHERE id=?",
        (t.nombre.strip(), t.tipo.strip().lower(), float(t.precio or 0), t.cliente_id, t.activo, tarifa_id),
    )
    conn.commit()
    return {"ok": True}




@router.patch("/api/trips/{trip_id}")
def update_trip(trip_id: str, upd: TripUpdate, conn = Depends(get_conn)):
    """Actualiza campos de un viaje (contables + planificación)."""
    row = conn.execute("SELECT estado, anulado_at FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": f"Viaje {trip_id} no encontrado"})
    if row["anulado_at"]:
        raise HTTPException(status_code=409, detail={"error": "El viaje está anulado y no se puede editar."})
    if (row["estado"] or "").lower() in _ESTADOS_FINALES:
        # en viajes finalizados solo se permite el cambio de estado de pago (cobro)
        otros = [f for f in ("factura", "cliente", "tipo_carga", "conductor", "matricula",
                             "semirremolque_id", "remolque_id", "precio", "gastos", "iva", "origen", "destino")
                 if getattr(upd, f) is not None]
        if otros:
            raise HTTPException(status_code=409, detail={"error": "El viaje está finalizado y no se puede modificar."})
    sets, params = [], []
    for field in ("factura", "estado_pago", "cliente", "tipo_carga", "conductor", "matricula", "semirremolque_id", "remolque_id", "fecha_esperada_carga", "fecha_esperada_descarga", "origen", "destino"):
        val = getattr(upd, field)
        if val is not None:
            sets.append(f"{field}=?")
            params.append(val)
    for field in ("precio", "gastos", "iva"):
        val = getattr(upd, field)
        if val is not None:
            sets.append(f"{field}=?")
            params.append(float(val))
    if sets:
        params.append(trip_id)
        conn.execute(f"UPDATE trips SET {', '.join(sets)} WHERE id=?", params)
        conn.commit()
    return {"ok": True, "trip_id": trip_id}


