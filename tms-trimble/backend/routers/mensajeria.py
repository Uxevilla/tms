"""
Router de mensajeria."""
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

router = APIRouter(dependencies=[Depends(require_role(["admin", "dispatcher", "superadmin"]))])


@router.get("/api/clientes/{cliente_id}/pales")
def cliente_pales(cliente_id: int, conn = Depends(get_conn)):
    """Cuenta corriente de palés de un cliente: saldo actual + histórico de movimientos."""
    movs = conn.execute(
        "SELECT * FROM saldos_pales WHERE cliente_id=? ORDER BY id DESC LIMIT 200",
        (cliente_id,),
    ).fetchall()
    saldo = int(movs[0]["balance"]) if movs else 0
    return {
        "ok": True,
        "cliente_id": cliente_id,
        "saldo": saldo,
        "movimientos": [dict(m) for m in movs],
    }




@router.get("/api/messagetypes")
def list_messagetypes(conn = Depends(get_conn)):
    """messagetype válidos configurados en FleetWorks (descubiertos de los mensajes recibidos)."""
    rows = conn.execute(
        "SELECT DISTINCT messagetype FROM mensajes WHERE COALESCE(messagetype,'') <> '' ORDER BY messagetype"
    ).fetchall()
    return {"messagetypes": [r["messagetype"] for r in rows]}




@router.post("/api/mensajeria/{terminal}/adjuntos")
def mensajeria_adjunto(terminal: str, req: dict):
    """Envía un archivo (base64) al terminal APP vía DMS."""
    nombre = (req.get("nombre") or "").strip()
    contenido = (req.get("contenido") or "").strip()
    if not nombre or not contenido:
        return {"ok": False, "error": "Falta nombre o contenido del archivo."}
    if len(contenido) > MAX_DOC_B64:
        return {"ok": False, "error": "El archivo supera ~2 MB."}
    res = get_client().create_document(nombre, contenido, "pdf", True)
    if not res.get("ok") or not res.get("uniqueDocId"):
        return {"ok": False, "error": res.get("error") or "No se pudo subir el documento."}
    get_client().assign_documents([res["uniqueDocId"]], [terminal])
    return {"ok": True, "fileKey": res["uniqueDocId"], "nombre": nombre}




@router.post("/api/mensajeria/{terminal}/mensajes")
def mensajeria_enviar(terminal: str, req: SendMensajeRequest):
    """Envía un mensaje libre a un terminal APP (sin viaje asociado)."""
    subject = (req.subject or "").strip()
    body = (req.body or "").strip()
    if not subject and not body:
        return {"ok": False, "error": "El mensaje está vacío."}
    conn = _db()
    row = conn.execute(
        "SELECT id FROM mensajes WHERE source=? OR terminal=? ORDER BY creado DESC LIMIT 1",
        (terminal, terminal),
    ).fetchone()
    conn.close()
    parent_id = row["id"] if row else None
    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_message(terminal, subject, body, req.needreply,
                                     message_id=msg_id, originid=parent_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    conn = _db()
    conn.execute(
        "INSERT INTO mensajes (id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, terminal, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (id) DO NOTHING",
        (msg_id, None, "enviado", "", parent_id, "TMS", subject, body,
         datetime.datetime.utcnow().isoformat() + "Z", req.needreply, terminal,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "id": msg_id, "terminal": terminal}




@router.get("/api/mensajeria/{terminal}/mensajes")
def mensajeria_mensajes(terminal: str, conn = Depends(get_conn)):
    """Mensajes de un terminal (recibidos: source=terminal; enviados: terminal=terminal)."""
    rows = conn.execute(
        "SELECT id, trip_id, tipo, messagetype, originid, source, subject, body, time, needreply, terminal "
        "FROM mensajes WHERE source=? OR terminal=? ORDER BY time LIMIT 300",
        (terminal, terminal),
    ).fetchall()
    return {"ok": True, "terminal": terminal, "mensajes": [dict(r) for r in rows]}




@router.get("/api/mensajeria/terminales")
def mensajeria_terminales(conn = Depends(get_conn)):
    """Lista los terminales APP (Fleet XPS) disponibles para mensajería."""
    rows = conn.execute(
        "SELECT DISTINCT app_terminal AS id FROM vehiculos "
        "WHERE app_terminal IS NOT NULL AND app_terminal<>'' ORDER BY app_terminal"
    ).fetchall()
    terminales = [dict(r) for r in rows]
    default = config.DEFAULT_TRIMBLE_TERMINAL
    if default and not any(t["id"] == default for t in terminales):
        terminales.insert(0, {"id": default})
    return {"ok": True, "terminales": terminales}




@router.post("/api/trips/{trip_id}/mensajes")
def send_trip_mensaje(trip_id: str, req: SendMensajeRequest, conn = Depends(get_conn)):
    row = conn.execute("SELECT terminal FROM trips WHERE id=?", (trip_id,)).fetchone()
    terminal = (row["terminal"] if row else "") or ""
    if not terminal:
        return {"ok": False, "error": "El viaje no tiene terminal asignado."}
    subject = (req.subject or "").strip()
    body = (req.body or "").strip()
    if not subject and not body:
        return {"ok": False, "error": "El mensaje está vacío."}

    # hilo: responder al último mensaje del chat de este viaje (para no abrir un hilo nuevo)
    conn = _db()
    row = conn.execute(
        "SELECT id FROM mensajes WHERE trip_id=? AND tipo IN ('enviado','estructurado','libre') "
        "ORDER BY creado DESC LIMIT 1", (trip_id,)
    ).fetchone()
    parent_id = row["id"] if row else None

    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_message(terminal, subject, body, req.needreply, message_id=msg_id, originid=parent_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    _save_mensaje(msg_id, trip_id, "enviado", "", parent_id, "TMS", subject, body,
                  datetime.datetime.utcnow().isoformat(), req.needreply)
    return {"ok": True, "id": msg_id, "terminal": terminal}




@router.post("/api/trips/{trip_id}/questionpath")
def send_trip_questionpath(trip_id: str, req: dict, conn = Depends(get_conn)):
    """Envía un question path (mensaje estructurado) al terminal del viaje."""
    row = conn.execute("SELECT terminal FROM trips WHERE id=?", (trip_id,)).fetchone()
    terminal = (row["terminal"] if row else "") or ""
    if not terminal:
        return {"ok": False, "error": "El viaje no tiene terminal asignado."}
    subject = (req.get("subject") or "").strip()
    body = (req.get("body") or "").strip()
    messagetype = (req.get("messagetype") or "").strip()
    if not body:
        return {"ok": False, "error": "El question path está vacío."}
    if not messagetype:
        return {"ok": False, "error": "Selecciona el messagetype del question path."}
    msg_id = f"TMS{int(time.time() * 1000)}"
    resp = get_client().send_structured_message(terminal, subject, body, messagetype, needreply=False, message_id=msg_id)
    fault = get_client()._fault(resp)
    if not resp.get("ok") or fault:
        return {"ok": False, "error": fault or f"HTTP {resp.get('status')}"}
    _save_mensaje(msg_id, trip_id, "enviado", messagetype, None, "TMS", subject, body,
                  datetime.datetime.utcnow().isoformat(), False)
    return {"ok": True, "id": msg_id, "terminal": terminal}




@router.get("/api/trips/{trip_id}/mensajes")
def trip_mensajes(trip_id: str, conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, tipo, messagetype, originid, source, subject, body, time, needreply "
        "FROM mensajes WHERE trip_id=? ORDER BY time DESC", (trip_id,)
    ).fetchall()
    return {"mensajes": [dict(r) for r in rows]}




