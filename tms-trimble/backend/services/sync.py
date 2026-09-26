"""Servicios de negocio (lógica compartida; sin FastAPI)."""

import datetime
import json
import os
import secrets
import urllib.parse
import urllib.request

import config
from db import *
from services.documentos import _guardar_archivo, _leer_archivo
from services.tipos_documento import clasificar_documento
from services.cuestionarios import parse_report
from core import *
from security import *
from tenancy import *
from clients.trimble import get_client
from clients.transfollow import get_transfollow_client
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from clients.geocoding import _buscar_photon, reverse_geocode

import base64
import io
import re
import subprocess
import uuid

import psycopg2
import redis

from models import *
from config import TRANSFOLLOW_WEBHOOK_USER, TRANSFOLLOW_WEBHOOK_PASSWORD

from config import REDIS_URL, REDIS_STREAM, REDIS_CHANNEL, ACTIVITY_TYPES
from services.telemetria import _extraer_posicion, _parse_trimble_ts, _guardar_telemetria, _source_a_vehiculo


def _query_terminal_states(terminal=None) -> dict:
    r = get_client().query_terminal(terminal)
    states = {}
    if r.get("ok"):
        for m in re.finditer(r"<return>(.*?)</return>", r["body"], re.S):
            block = m.group(1)
            tid = re.search(r"<id>([^<]*)</id>", block)
            st = re.search(r"<lastKnownState>([^<]*)</lastKnownState>", block)
            if tid:
                states[tid.group(1)] = st.group(1) if st else "new"
    return states


def _sync_status():
    conn = _db()
    terminals = [r["terminal"] for r in conn.execute(
        "SELECT DISTINCT terminal FROM trips WHERE terminal IS NOT NULL AND terminal != ''"
    ).fetchall()]
    conn.close()
    terminals = list(set(terminals))
    states = {}
    for t in terminals:
        states.update(_query_terminal_states(t))
    conn = _db()
    rows = conn.execute(
        f"SELECT id, terminal, tareas, tareas_estado FROM trips "
        f"WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL} "
        f"AND COALESCE(estado,'') NOT IN ('sin_asignar', 'pedido') "
        f"AND terminal IS NOT NULL AND terminal != ''"
    ).fetchall()
    for row in rows:
        trip_id = row["id"]
        n = row["tareas"] or 0
        prev = json.loads(row["tareas_estado"] or "{}")
        task_states = {}
        for i in range(1, n + 1):
            tid = f"{trip_id}_T{i:02d}"
            if tid in states:
                task_states[tid] = states[tid]
            else:
                p = prev.get(tid)
                task_states[tid] = "finalizado" if (p and p != "finalizado") else (p or None)
        vals = [s for s in task_states.values() if s]
        if vals and all(s == "finalizado" for s in vals):
            estado = "finalizado"
        elif any(s == "busy" for s in vals):
            estado = "en curso"
        elif any(s == "accepted" for s in vals):
            estado = "aceptado"
        elif any(s == "received" for s in vals):
            estado = "recibido"
        else:
            estado = "enviado"
        conn.execute(
            "UPDATE trips SET estado=?, tareas_estado=? WHERE id=?",
            (estado, json.dumps(task_states), trip_id),
        )
        # Sellar el puente: liberar el viaje activo (DEL) al finalizar.
        if estado in _ESTADOS_FINALES and row["terminal"]:
            _del_viaje_activo(row["terminal"])
        # Al finalizar: la última posición conocida del vehículo pasa a ser el destino del viaje
        if estado == "finalizado" and row["terminal"]:
            dest = conn.execute(
                "SELECT lat, lng FROM operaciones.paradas WHERE trip_id=? ORDER BY orden DESC LIMIT 1", (trip_id,)
            ).fetchone()
            if dest and dest["lat"] is not None and dest["lng"] is not None:
                conn.execute(
                    "UPDATE vehiculos SET last_lat=?, last_lng=? WHERE terminal_trimble=?",
                    (dest["lat"], dest["lng"], row["terminal"]),
                )
    conn.commit()
    conn.close()


def _get_sync_state(key):
    with _db() as conn:
        row = conn.execute("SELECT value FROM sistema.sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _set_sync_state(key, value):
    with _db() as conn:
        conn.execute("INSERT INTO sistema.sync_state (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))
        conn.commit()


def migrar_lid_map_json(conn=None):
    """Migra el JSON antiguo de sync_state (key 'lid_map') a la tabla lid_map, una sola vez.

    Re-vincula ficheros con trip_id NULL y lid conocido. Devuelve cuántos mapeos migró.
    """
    owns = conn is None
    if owns:
        conn = _db()
    try:
        raw = conn.execute("SELECT value FROM sistema.sync_state WHERE key='lid_map'").fetchone()
        if not raw:
            return 0
        try:
            data = json.loads(raw["value"] or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        n = 0
        if isinstance(data, dict):
            for lid, trip in data.items():
                if lid and trip:
                    conn.execute(
                        "INSERT INTO lid_map (lid, trip_id) VALUES (?,?) ON CONFLICT (lid) DO NOTHING",
                        (lid, trip),
                    )
                    n += 1
        conn.execute(
            "UPDATE files f SET trip_id=l.trip_id, vinculado_por='lid' "
            "FROM lid_map l WHERE f.lid=l.lid AND f.trip_id IS NULL AND l.trip_id IS NOT NULL"
        )
        conn.execute("DELETE FROM sistema.sync_state WHERE key='lid_map'")
        conn.commit()
        return n
    finally:
        if owns:
            conn.close()


def _mark_inicial():
    """Cursor de arranque: 2 días atrás en UTC (formato del cursor de Trimble).

    Al levantar el stack por primera vez (o con las marks vacías), el sync arranca
    desde hace 2 días en vez de tragarse todo el histórico de colas de Trimble.
    """
    ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000")


def _parse_props(block):
    props = {}
    for m in re.finditer(
        r"<property>\s*<key>([^<]*)</key>\s*<value>([^<]*)</value>\s*</property>",
        block, re.S,
    ):
        props[m.group(1)] = m.group(2)
    return props


def _save_file(trip_id, name, ftype, ftime, source, driver, lid, content_b64, error="",
               report_id="", question_id="", mensaje_id=""):
    """Guarda un fichero descargado o registra el fallo para reintentar (nunca se descarta).

    - content_b64 no vacío → estado 'descargado' (binario en disco vía _guardar_archivo).
    - si falla (error) → estado 'error' + ultimo_error + intentos++ (se reintenta en el siguiente ciclo).
    - report_id/question_id/mensaje_id: se rellenan al vincular el fichero con la respuesta del
      cuestionario (nombres de fichero en las trazas 12/13/300, §8.4) y permiten clasificar por
      pregunta (cfg_tipo_documento_pregunta) en vez de por el nombre del fichero.
    """
    g = _guardar_archivo(name, content_b64) if content_b64 else None
    tipo = clasificar_documento(ftype, name)
    with _db() as conn:
        if report_id and question_id:
            mp = conn.execute(
                "SELECT tipo_documento FROM cfg_tipo_documento_pregunta "
                "WHERE report_id=? AND question_id=? LIMIT 1", (report_id, question_id),
            ).fetchone()
            if mp and mp["tipo_documento"]:
                tipo = mp["tipo_documento"]
        if g:
            storage_key, sha, nbytes, mime = g
            conn.execute(
                "INSERT INTO files (trip_id, name, ftype, ftime, source, driver, lid, storage_key, sha256, bytes, mime, estado_descarga, intentos, tipo_documento, report_id, question_id, mensaje_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,'descargado',1,?,?,?,?) ON CONFLICT (name) DO UPDATE SET "
                "estado_descarga='descargado', storage_key=EXCLUDED.storage_key, sha256=EXCLUDED.sha256, "
                "bytes=EXCLUDED.bytes, mime=EXCLUDED.mime, intentos=files.intentos+1, ultimo_error=NULL, "
                "tipo_documento=EXCLUDED.tipo_documento, report_id=EXCLUDED.report_id, "
                "question_id=EXCLUDED.question_id, mensaje_id=EXCLUDED.mensaje_id",
                (trip_id, name, ftype, ftime, source, driver, lid, storage_key, sha, nbytes, mime, tipo,
                 report_id or None, question_id or None, mensaje_id or None),
            )
        else:
            conn.execute(
                "INSERT INTO files (trip_id, name, ftype, ftime, source, driver, lid, estado_descarga, intentos, ultimo_error, tipo_documento, report_id, question_id, mensaje_id) "
                "VALUES (?,?,?,?,?,?,?,'error',1,?,?,?,?,?) ON CONFLICT (name) DO UPDATE SET "
                "estado_descarga='error', intentos=files.intentos+1, ultimo_error=EXCLUDED.ultimo_error, "
                "tipo_documento=EXCLUDED.tipo_documento, report_id=EXCLUDED.report_id, "
                "question_id=EXCLUDED.question_id, mensaje_id=EXCLUDED.mensaje_id",
                (trip_id, name, ftype, ftime, source, driver, lid, error or "descarga vacía", tipo,
                 report_id or None, question_id or None, mensaje_id or None),
            )
        conn.commit()


def _reintentar_descargas(max_intentos=5):
    """Reintenta descargas con estado 'error' (el mark del poll ya las pasó de largo).

    El pollFiles usa un cursor high-water: una descarga fallida queda ATRÁS del mark y no
    se re-poll-ea; por eso se reintenta leyendo la tabla y re-descargando por nombre.
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT trip_id, name, ftype, ftime, source, driver, lid FROM files "
            "WHERE estado_descarga='error' AND intentos < ? AND anulado_at IS NULL",
            (max_intentos,),
        ).fetchall()
    for row in rows:
        dl = get_client().download_file(row["name"])
        if dl.get("ok"):
            content = re.search(r"<return>([^<]*)</return>", dl.get("body", ""))
            if content:
                _save_file(row["trip_id"], row["name"], row["ftype"] or 0,
                           row["ftime"] or "", row["source"] or "", row["driver"] or "",
                           row["lid"] or "", content.group(1))
                continue
            err = "contenido vacío"
        else:
            fault = re.search(r"<faultstring>([^<]*)</faultstring>", dl.get("body", ""))
            err = f"SOAP {dl.get('status')} {fault.group(1) if fault else 'sin detalle'}"
        _save_file(row["trip_id"], row["name"], row["ftype"] or 0,
                   row["ftime"] or "", row["source"] or "", row["driver"] or "",
                   row["lid"] or "", "", error=err)


def _extraer_reporte_xml(block):
    """Extrae el reporte XML (ARE o AFRE) de una traza de actividad (type 12 o 13).

    Devuelve (tipo_reporte, xml): tipo_reporte ∈ {'AFRE','ARE',''} y el XML sin envoltorio.
    AFRE = informe final (obligatorio); ARE = informe intermedio (opcional). El XML puede venir
    en CDATA o escapado (&lt;Report …). Devuelve ('', '') si no lo encuentra.
    """
    import html
    for key in ("AFRE", "ARE"):
        m = re.search(rf"<property>\s*<key>{key}</key>\s*<value>(.*?)</value>\s*</property>", block, re.S)
        if not m:
            continue
        val = m.group(1).strip()
        cdata = re.search(r"<!\[CDATA\[(.*?)\]\]>", val, re.S)
        if cdata:
            return key, cdata.group(1).strip()
        rep = re.search(r"(<Report\b.*?</Report>)", val, re.S)
        if rep:
            return key, rep.group(1)
        rep = re.search(r"(&lt;Report\b.*?&lt;/Report&gt;)", val, re.S)
        if rep:
            return key, html.unescape(rep.group(1))
        return key, val
    return "", ""


def _extraer_documento_ecmr(qp):
    """Extrae el documento/firma base64 del reporte AFRE (e-CMR).

    Busca en el XML del reporte: data URIs (imagen/pdf), nodos de firma/adjunto
    con contenido base64 y atributos base64. Devuelve (nombre, b64, formato) o None.
    AJUSTAR a la estructura real del CDATA de Trimble si es necesario.
    """
    if not qp:
        return None
    # 1) data URI embebida (imagen o pdf)
    m = re.search(r"data:(image/[a-z+]+|application/pdf);base64,([A-Za-z0-9+/=]+)", qp)
    if m:
        fmt = "png" if "png" in m.group(1) else ("jpg" if "jpeg" in m.group(1) else "pdf")
        return "firma_ecmr", m.group(2), fmt
    # 2) nodos con base64 en el cuerpo (firma/imagen/adjunto)
    for tag in ("Signature", "Image", "Photo", "Attachment", "File", "Document",
                "Firma", "Imagen", "Adjunto"):
        m = re.search(rf"<{tag}[^>]*>\s*([A-Za-z0-9+/=]{{20,}})\s*</{tag}>", qp, re.S)
        if m:
            return f"ecmr_{tag.lower()}", m.group(1).strip(), "png"
    # 3) atributo src/value/content con data URI
    m = re.search(r'(?:src|value|content)\s*=\s*["\']data:(image/[a-z+]+|application/pdf);base64,([A-Za-z0-9+/=]+)["\']', qp)
    if m:
        fmt = "png" if "png" in m.group(1) else ("jpg" if "jpeg" in m.group(1) else "pdf")
        return "firma_ecmr", m.group(2), fmt
    # 4) bloque CDATA con base64 puro (firma escaneada)
    m = re.search(r"<!\[CDATA\[\s*([A-Za-z0-9+/=]{100,})\s*\]\]>", qp)
    if m:
        return "firma_ecmr", m.group(1).strip(), "png"
    return None


def _guardar_documento_entrega(trip_id, nombre, contenido_b64, formato="png", ftime=None):
    """Guarda el documento del e-CMR (firma/escaneo base64) en la tabla files."""
    if not trip_id or not contenido_b64:
        return
    name = f"ecmr_{uuid.uuid4().hex[:8]}_{nombre}"
    g = _guardar_archivo(name, contenido_b64)
    with _db() as conn:
        if g:
            storage_key, sha, nbytes, _mime = g
            conn.execute(
                "INSERT INTO files (trip_id, name, ftype, ftime, source, storage_key, sha256, bytes, formato) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
                (trip_id, name, 3, ftime or (datetime.datetime.utcnow().isoformat() + "Z"),
                 "ecmr", storage_key, sha, nbytes, formato),
            )
        else:
            conn.execute(
                "INSERT INTO files (trip_id, name, ftype, ftime, source, content_b64, formato) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
                (trip_id, name, 3, ftime or (datetime.datetime.utcnow().isoformat() + "Z"),
                 "ecmr", contenido_b64, formato),
            )
        conn.commit()


def _publicar_estado(trip_id, estado):
    """Publica un evento de estado en Redis Pub/Sub (best-effort)."""
    try:
        _get_redis().publish(REDIS_CHANNEL,
                             json.dumps({"tipo": "estado", "id": trip_id, "estado": estado}))
    except Exception:
        pass


def _odometro_vehiculo(conn, vehicle_id):
    """Último odómetro conocido de un vehículo (telemetría); None si no hay datos."""
    r = conn.execute(
        "SELECT odometer_km FROM telemetria.posiciones_gps "
        "WHERE vehiculo_id=? AND odometer_km IS NOT NULL ORDER BY time DESC LIMIT 1", (vehicle_id,)
    ).fetchone()
    return r["odometer_km"] if r else None


def _aplicar_estado_viaje(trip_id, nuevo_estado, ts=None):
    """Actualiza transaccionalmente el estado del viaje y lo publica en Redis.

    - UPDATE trips (estado + fecha_actualizacion + captura de km por odómetro).
    - Captura km_inicio al primer estado activo (Cargando/En_Transito) y km_fin/km_real
      al Entregado, con guard de sanidad (caída a planificado si el delta es inválido).
    - Publica {"tipo":"estado","id":trip_id,"estado":nuevo_estado} en canal_operaciones
      (es exactamente lo que _facturacion_listener escucha para facturar al Entregado).
    - Si el nuevo estado es 'Entregado', libera el viaje activo (DEL del SET en Redis).
    Devuelve True si el viaje existía, False si no se encontró.
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT id, terminal, km_inicio, km_fin, km_total FROM trips WHERE id=?", (trip_id,)
        ).fetchone()
        if not row:
            conn.close()
            return False
        terminal = row["terminal"]
        ts_iso = ts or (datetime.datetime.utcnow().isoformat() + "Z")

        # Captura de km por odómetro (km_real = km_fin - km_inicio, con guard de sanidad).
        km_updates, km_params = "", []
        if terminal:
            if nuevo_estado in ("Cargando", "En_Transito") and row["km_inicio"] is None:
                odo = _odometro_vehiculo(conn, terminal)
                if odo is not None:
                    km_updates += ", km_inicio=?"
                    km_params.append(odo)
            if nuevo_estado == "Entregado" and row["km_fin"] is None:
                odo = _odometro_vehiculo(conn, terminal)
                if odo is not None:
                    km_updates += ", km_fin=?"
                    km_params.append(odo)
                    if row["km_inicio"] is not None and float(odo) >= float(row["km_inicio"]):
                        km_real = float(odo) - float(row["km_inicio"])
                        km_total = float(row["km_total"] or 0)
                        if km_real <= 0 or (km_total > 0 and km_real > km_total * 2.5):
                            km_updates += ", km_fuente='invalido'"
                        else:
                            km_updates += ", km_real=?, km_fuente='real'"
                            km_params.append(km_real)
                    else:
                        km_updates += ", km_fuente='planificado'"

        conn.execute(
            f"UPDATE trips SET estado=?, fecha_actualizacion=?{km_updates} WHERE id=?",
            [nuevo_estado, ts_iso, *km_params, trip_id],
        )
        conn.commit()
    _publicar_estado(trip_id, nuevo_estado)
    if nuevo_estado == "Entregado" and terminal:
        _del_viaje_activo(terminal)
    return True


def _cerrar_viaje(trip_id):
    """Marca el viaje como Entregado (usa la vía transaccional + Redis)."""
    _aplicar_estado_viaje(trip_id, "Entregado")
    return trip_id


def _cerrar_viaje_por_ecmr(fd_id):
    """Cierra el viaje cuyo ecmr_id coincide con el freightDocumentId recibido."""
    conn = _db()
    try:
        row = conn.execute("SELECT id, terminal FROM trips WHERE ecmr_id=? LIMIT 1", (fd_id,)).fetchone()
        if not row:
            return None
        trip_id = row["id"]
        conn.execute("UPDATE trips SET estado='Entregado' WHERE id=?", (trip_id,))
        conn.commit()
    finally:
        conn.close()
    _publicar_estado(trip_id, "Entregado")
    if row["terminal"]:
        _del_viaje_activo(row["terminal"])
    return trip_id


def _entrega_confirmada(qp):
    """Detecta si el question path (ARE/AFRE) confirma entrega o firma del e-CMR.

    Heurística sobre el texto de las respuestas <Answer>…</Answer> y del reporte
    completo. AJUSTAR a la estructura real del CDATA de Trimble si es necesario.
    """
    if not qp:
        return False
    respuestas = " ".join(re.findall(r"<Answer[^>]*>(.*?)</Answer>", qp, re.S))
    texto = (respuestas + " " + qp).lower()
    firmado = any(k in texto for k in ("signed", "firmado", "signature", "firma", "signed by"))
    sin_incidencias = any(k in texto for k in (
        "sin incidencia", "sin incidencias", "sin anomal", "no incident", "no incidents",
        "without incident", "sin daños", "sin faltas", "no damage"))
    return firmado or sin_incidencias


def _lid_map_trip(conn, lid):
    """Devuelve el trip_id de un LID (tabla lid_map) o None si no está mapeado.

    Si conn es None abre una conexión corta (evita mantener una transacción abierta
    durante los poll SOAP lentos).
    """
    if not lid:
        return None
    own = conn is None
    if own:
        conn = _db()
    try:
        row = conn.execute("SELECT trip_id FROM lid_map WHERE lid=?", (lid,)).fetchone()
        return row["trip_id"] if row else None
    finally:
        if own:
            conn.close()


def _lid_map_put(conn, lid, trip_id, task_id="", terminal="", desde=""):
    """Registra el mapeo LID→trip_id (la traza 10 lo alimenta)."""
    if not lid:
        return
    conn.execute(
        "INSERT INTO lid_map (lid, trip_id, task_id, terminal, desde) VALUES (?,?,?,?,?) "
        "ON CONFLICT (lid) DO UPDATE SET trip_id=EXCLUDED.trip_id, task_id=EXCLUDED.task_id, "
        "terminal=EXCLUDED.terminal, desde=EXCLUDED.desde",
        (lid, trip_id, task_id, terminal, desde),
    )


def _sync_files():
    # 1) trazas tipo 10 (activity started) -> tabla lid_map (LID -> trip_id)
    mark = _get_sync_state("traces_mark") or _mark_inicial()
    with _db() as pos_conn:
        pos_conn.set_autocommit(True)  # no mantener transacción abierta durante los poll SOAP (evita lock en cascada)
        posiciones = {}
        eventos = []  # cambios de actividad a publicar en Redis Pub/Sub
        for _ in range(30):
            r = get_client().poll_traces(mark)
            if not r.get("ok"):
                break
            for block in re.findall(r"<traces>(.*?)</traces>", r["body"], re.S):
                ttype = re.search(r"<type>(\d+)</type>", block)
                props = _parse_props(block)
                t = ttype.group(1) if ttype else ""
                if t == "10" and props.get("LID") and props.get("TRID"):
                    _lid_map_put(pos_conn, props["LID"], props["TRID"])
                    eventos.append({"tipo": "actividad", "traza": "10",
                                    "trip_id": props["TRID"], "reporte": ""})
                elif t in ("12", "13") and props.get("LID"):
                    # activity report (12) o activity end (13) -> question path (ARE/AFRE)
                    trip_id = props.get("TRID") or _lid_map_trip(pos_conn, props["LID"])
                    src = re.search(r"<source>([^<]*)</source>", block)
                    tm = re.search(r"<time>([^<]*)</time>", block)
                    rt, qp = _extraer_reporte_xml(block)
                    if not qp:
                        continue  # sin reporte (ARE/AFRE), no es question path
                    eseq = props.get("ESEQ", "")
                    suf = f"_{rt.lower()}" + (f"_{eseq}" if eseq else "")
                    # Informe de actividad (trazas 12/13): clase propia con el reporte parseado.
                    rep = None
                    try:
                        rep = parse_report(qp)
                    except ValueError:
                        rep = None
                    _save_mensaje(
                        f"qp_{props['LID']}{suf}", trip_id, "cuestionario", props.get("ATY", ""),
                        props["LID"], src.group(1) if src else "", rt,
                        qp, tm.group(1) if tm else "", False,
                        clase="informe_actividad", direccion="entrante",
                        report_id=rep["report_id"] if rep else None,
                        report_version=rep["version"] if rep else None,
                        respuestas=rep["respuestas"] if rep else None,
                    )
                    # Regla de negocio: CMR/DESCARGA firmada o sin incidencias => cerrar viaje.
                    aty = (props.get("ATY") or "").upper()
                    if trip_id and aty in ("CMR", "DESCARGA") and _entrega_confirmada(qp):
                        _cerrar_viaje(trip_id)
                        doc = _extraer_documento_ecmr(qp)
                        if doc:
                            _guardar_documento_entrega(trip_id, doc[0], doc[1], doc[2],
                                                       tm.group(1) if tm else None)
                    # AFRE (informe final, traza 13) puede llevar el e-CMR embebido.
                    eventos.append({"tipo": "actividad", "traza": t,
                                    "trip_id": trip_id, "reporte": rt})
                elif t == "82":
                    # Traza 82: cambio de estado del tacógrafo con DSTAT (estadísticas del conductor).
                    did = props.get("DID", "")
                    dstat_b64 = props.get("DSTAT", "")
                    if did and dstat_b64:
                        src = re.search(r"<source>([^<]*)</source>", block)
                        tm = re.search(r"<time>([^<]*)</time>", block)
                        source = src.group(1) if src else ""
                        veh = _source_a_vehiculo(pos_conn, source) if source else None
                        _ingestar_dstat(did, source, veh, dstat_b64,
                                        _decode_dstat(dstat_b64), tm.group(1) if tm else "")
                pos = _extraer_posicion(block)
                if pos:
                    veh = _source_a_vehiculo(pos_conn, pos["source"])
                    if veh:
                        posiciones[veh] = (pos["lat"], pos["lng"], pos["time"])
                        # Solo el flujo GPS periódico (tipo 0) va a la telemetría histórica.
                        if t == "0":
                            _guardar_telemetria(pos, veh)
            m = re.search(r"<mark>([^<]*)</mark>", r["body"])
            more = re.search(r"<more>([^<]*)</more>", r["body"])
            if m:
                mark = m.group(1)
            if not (more and more.group(1) == "true"):
                break
        for veh, (lat, lng, ttime) in posiciones.items():
            pos_conn.execute(
                "UPDATE vehiculos SET last_lat=?, last_lng=?, last_position_time=? WHERE terminal_trimble=?",
                (lat, lng, ttime, veh),
            )
        pos_conn.commit()
    _set_sync_state("traces_mark", mark or "")

    # Publicar cambios de actividad en Redis Pub/Sub (tiempo real).
    for ev in eventos:
        try:
            _get_redis().publish(REDIS_CHANNEL, json.dumps(ev))
        except Exception:
            pass

    # re-asignar archivos ya descargados cuyo LID ya tiene mapeo (tabla lid_map)
    conn = _db()
    conn.execute(
        "UPDATE files f SET trip_id=l.trip_id, vinculado_por='lid' "
        "FROM lid_map l WHERE f.lid=l.lid AND f.trip_id IS NULL AND l.trip_id IS NOT NULL"
    )
    conn.commit()
    conn.close()

    # 2) poll files + descarga. Solo se descartan los de tacógrafo (0=tarjeta, 1=memoria masiva, 2=memoria CSV, 4=registro de accidente);
    #    el resto (documentos/fotos/firmas de cuestionarios y mensajes) se descarga y, si falla,
    #    queda 'error' para reintentar en el siguiente ciclo (nunca se descarta).
    fmark = _get_sync_state("files_mark") or _mark_inicial()
    for _ in range(10):
        r = get_client().poll_files(fmark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<files>(.*?)</files>", r["body"], re.S):
            name = re.search(r"<name>([^<]*)</name>", block)
            ftype = re.search(r"<type>([^<]*)</type>", block)
            if not name or (ftype and ftype.group(1) in ("0", "1", "2")):
                continue
            ftime = re.search(r"<time>([^<]*)</time>", block)
            source = re.search(r"<source>([^<]*)</source>", block)
            driver = re.search(r"<driver>([^<]*)</driver>", block)
            props = _parse_props(block)
            lid = props.get("LID", "")
            trip_id = _lid_map_trip(None, lid)
            dl = get_client().download_file(name.group(1))
            if not dl.get("ok"):
                fault = re.search(r"<faultstring>([^<]*)</faultstring>", dl.get("body", ""))
                _save_file(trip_id, name.group(1), int(ftype.group(1)) if ftype else 0,
                           ftime.group(1) if ftime else "", source.group(1) if source else "",
                           driver.group(1) if driver else "", lid, "",
                           error=f"SOAP {dl.get('status')} {fault.group(1) if fault else 'sin detalle'}")
                continue
            content = re.search(r"<return>([^<]*)</return>", dl.get("body", ""))
            _save_file(
                trip_id, name.group(1),
                int(ftype.group(1)) if ftype else 0,
                ftime.group(1) if ftime else "",
                source.group(1) if source else "",
                driver.group(1) if driver else "",
                lid, content.group(1) if content else "",
                error="" if content else "contenido vacío",
            )
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            fmark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("files_mark", fmark or "")

    # 3) Reintentar descargas fallidas (el mark ya pasó; re-descargar desde la tabla).
    _reintentar_descargas()


def _sync_mensajes():
    """Polls mensajes estructurados y libres del conductor (Messaging), anexándolos al viaje vía originid→LID."""

    # 1) mensajes estructurados (candidato del question path)
    smark = _get_sync_state("mensajes_mark") or _mark_inicial()
    for _ in range(10):
        r = get_client().poll_structured_messages(smark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<messages>(.*?)</messages>", r["body"], re.S):
            _store_mensaje(block, "estructurado")
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            smark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("mensajes_mark", smark or "")

    # 2) mensajes libres
    fmark = _get_sync_state("mensajes_free_mark") or _mark_inicial()
    for _ in range(10):
        r = get_client().poll_messages(fmark)
        if not r.get("ok"):
            break
        for block in re.findall(r"<messages>(.*?)</messages>", r["body"], re.S):
            _store_mensaje(block, "libre")
        m = re.search(r"<mark>([^<]*)</mark>", r["body"])
        more = re.search(r"<more>([^<]*)</more>", r["body"])
        if m:
            fmark = m.group(1)
        if not (more and more.group(1) == "true"):
            break
    _set_sync_state("mensajes_free_mark", fmark or "")


from services.tacografo import _ingestar_dstat, _decode_dstat
from services.telemetria import _del_viaje_activo, _get_redis
from services.mensajeria import _save_mensaje, _store_mensaje
