"""Servicios de negocio (lógica compartida; sin FastAPI)."""

import datetime
import json
import os
import secrets
import urllib.parse
import urllib.request

import config
from fastapi import HTTPException
from db import *
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


def _save_trip(trip_id, nombre, matricula, conductor, tipo_carga,
               origen, destino, n_tareas, estado, error, terminal="",
               semirremolque_id="", remolque_id="",
               cliente="", precio=0.0, km_total=0.0, gastos=0.0,
               factura="", estado_pago="pendiente", iva=21.0,
               cliente_id=None, conductor_id=None,
               peaje_km=0.0, peaje_estimado=0.0, peaje_fuente="estimado",
               tiempo_min=0.0, trafico_min=0.0, pausas_min=0.0,
               fecha_esperada_carga="", fecha_esperada_descarga="",
               modo_tarifa="viaje", tarifa_id=None, precio_unitario=None,
               kilos=0.0, subcontratado=False, proveedor_id=None, coste=0.0):
    conn = _db()
    # referencia interna: conservar la existente o generar una nueva
    row = conn.execute("SELECT referencia FROM trips WHERE id=?", (trip_id,)).fetchone()
    referencia = (row["referencia"] if row and row["referencia"] else "") or ""
    if not referencia:
        referencia = _next_referencia(conn)
    conn.execute(
        "INSERT INTO trips "
        "(id, nombre, matricula, conductor, tipo_carga, origen, destino, "
        "tareas, estado, error, creado, terminal, semirremolque_id, remolque_id, "
        "cliente, cliente_id, conductor_id, "
        "precio, km_total, tiempo_min, pausas_min, trafico_min, peaje_km, peaje_estimado, peaje_fuente, "
        "gastos, factura, estado_pago, iva, referencia, fecha_esperada_carga, fecha_esperada_descarga, "
        "modo_tarifa, tarifa_id, precio_unitario, kilos, subcontratado, proveedor_id, coste) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET "
        "nombre=EXCLUDED.nombre, matricula=EXCLUDED.matricula, conductor=EXCLUDED.conductor, "
        "tipo_carga=EXCLUDED.tipo_carga, origen=EXCLUDED.origen, destino=EXCLUDED.destino, "
        "tareas=EXCLUDED.tareas, estado=EXCLUDED.estado, error=EXCLUDED.error, "
        "creado=EXCLUDED.creado, terminal=EXCLUDED.terminal, "
        "semirremolque_id=EXCLUDED.semirremolque_id, remolque_id=EXCLUDED.remolque_id, "
        "cliente=EXCLUDED.cliente, cliente_id=EXCLUDED.cliente_id, conductor_id=EXCLUDED.conductor_id, "
        "precio=EXCLUDED.precio, km_total=EXCLUDED.km_total, tiempo_min=EXCLUDED.tiempo_min, "
        "pausas_min=EXCLUDED.pausas_min, trafico_min=EXCLUDED.trafico_min, peaje_km=EXCLUDED.peaje_km, "
        "peaje_estimado=EXCLUDED.peaje_estimado, peaje_fuente=EXCLUDED.peaje_fuente, "
        "gastos=EXCLUDED.gastos, factura=EXCLUDED.factura, "
        "estado_pago=EXCLUDED.estado_pago, iva=EXCLUDED.iva, referencia=EXCLUDED.referencia, "
        "fecha_esperada_carga=EXCLUDED.fecha_esperada_carga, fecha_esperada_descarga=EXCLUDED.fecha_esperada_descarga, "
        "modo_tarifa=EXCLUDED.modo_tarifa, tarifa_id=EXCLUDED.tarifa_id, precio_unitario=EXCLUDED.precio_unitario, "
        "kilos=EXCLUDED.kilos, subcontratado=EXCLUDED.subcontratado, proveedor_id=EXCLUDED.proveedor_id, coste=EXCLUDED.coste",
        (trip_id, nombre, matricula, conductor, tipo_carga, origen,
         destino, n_tareas, estado, error,
         datetime.datetime.utcnow().isoformat() + "Z", terminal, semirremolque_id, remolque_id,
         cliente, cliente_id, conductor_id,
         precio, km_total, tiempo_min, pausas_min, trafico_min, peaje_km, peaje_estimado, peaje_fuente,
         gastos, factura, estado_pago, iva, referencia, fecha_esperada_carga, fecha_esperada_descarga,
         modo_tarifa, tarifa_id, precio_unitario, kilos, subcontratado, proveedor_id, coste),
    )
    conn.commit()
    conn.close()


def _save_tramos(conn, trip_id, tramos):
    """Persiste los tramos (segmentos) de un viaje; cada tramo con su vehículo/conductor."""
    conn.execute("DELETE FROM tramos WHERE trip_id=?", (trip_id,))
    for t in tramos:
        conn.execute(
            "INSERT INTO tramos (trip_id, orden, origen_nombre, origen_ciudad, origen_lat, origen_lng, "
            "destino_nombre, destino_ciudad, destino_lat, destino_lng, terminal, conductor, fecha_carga, fecha_descarga, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (trip_id, t.orden, t.origen_nombre, t.origen_ciudad, t.origen_lat, t.origen_lng,
             t.destino_nombre, t.destino_ciudad, t.destino_lat, t.destino_lng,
             t.terminal, t.conductor, t.fecha_carga, t.fecha_descarga,
             datetime.datetime.utcnow().isoformat() + "Z"),
        )


def _save_paradas(trip_id, viaje):
    with _db() as conn:
        conn.execute("DELETE FROM paradas WHERE trip_id=?", (trip_id,))

        def insert(i, d, actividad):
            conn.execute(
                "INSERT INTO paradas (trip_id, orden, nombre, ciudad, lat, lng, actividad, comentario) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (trip_id, i, d.nombre or "", d.ciudad or "", d.lat, d.lng, actividad, d.comentario or ""),
            )

        insert(0, viaje.origen, viaje.origen.actividad or "CARGA")
        for i, p in enumerate(viaje.paradas, start=1):
            insert(i, p, p.actividad or "DESCARGA")
        insert(len(viaje.paradas) + 1, viaje.destino, viaje.destino.actividad or "DESCARGA")
        conn.commit()


def _upsert_direccion(conn, d):
    """Guarda (o reutiliza) una dirección en el maestro y devuelve su id, o None si no hay datos."""
    ciudad = (d.ciudad or "").strip()
    calle = (d.calle or "").strip()
    numero = (d.numero or "").strip()
    nombre = (d.nombre or "").strip()
    empresa = (d.empresa or "").strip()
    cp = (d.cp or "").strip()
    if not (ciudad or calle or nombre or empresa):
        return None
    row = conn.execute(
        "SELECT id FROM direcciones WHERE ciudad=? AND calle=? AND numero=? AND nombre=? AND empresa=? AND cp=? LIMIT 1",
        (ciudad, calle, numero, nombre, empresa, cp),
    ).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO direcciones (nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (nombre, empresa, calle, numero, ciudad, cp, d.pais or "ES", d.lat, d.lng, d.comentario or "",
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    return cur.fetchone()["id"]


def _peaje_rate(categoria):
    if not categoria:
        return 0.0
    with _db() as conn:
        row = conn.execute("SELECT eur_km FROM tarifas_peaje WHERE categoria=?", (categoria,)).fetchone()
    if row and row["eur_km"] is not None:
        return float(row["eur_km"])
    return float(_PEAJE_CATEGORIAS.get(categoria, {}).get("eur_km", 0.0))


def _vehiculo_peaje_categoria(terminal):
    if not terminal:
        return "pesado4"
    with _db() as conn:
        row = conn.execute("SELECT peaje_categoria FROM vehiculos WHERE id=?", (terminal,)).fetchone()
    return (row["peaje_categoria"] if row and row["peaje_categoria"] else "pesado4")


def _vehiculos_en_curso(exclude_trip_id=None):
    """Ids de REMOLQUES (semirremolque/remolque) con un viaje activo (no finalizado).
    El terminal (tractora) NO se incluye: admite varios viajes en cola (se ejecutan uno tras otro)."""
    with _db() as conn:
        q = (f"SELECT semirremolque_id, remolque_id FROM trips "
             f"WHERE COALESCE(estado,'') NOT IN {_ESTADOS_FINALES_SQL}")
        params = []
        if exclude_trip_id:
            q += " AND id != ?"
            params.append(exclude_trip_id)
        rows = conn.execute(q, params).fetchall()
    ids = set()
    for r in rows:
        for v in (r["semirremolque_id"], r["remolque_id"]):
            if v:
                ids.add(v)
    return ids


def _puntos_del_viaje(viaje):
    """Puntos ordenados (origen → paradas → destino) que tienen coordenadas."""
    puntos = []
    paradas = [p for p in viaje.paradas if (p.ciudad or p.nombre or p.calle or p.lat is not None)]
    for d, default in [(viaje.origen, "Origen")] + [(p, "Parada") for p in paradas] + [(viaje.destino, "Destino")]:
        if d.lat is not None and d.lng is not None:
            puntos.append({"lat": d.lat, "lng": d.lng, "nombre": d.ciudad or d.nombre or default})
    return puntos


def _build_trip(viaje: ViajeRequest, trip_id: str, documentos: list = None) -> dict:
    def contacto(d) -> dict:
        return {
            "nombre": d.nombre, "empresa": d.empresa, "calle": d.calle,
            "numero": d.numero, "ciudad": d.ciudad, "cp": d.cp, "pais": d.pais,
            "lat": d.lat, "lng": d.lng,
        }

    act_map = _actividades_map()  # {nombre: referencia} editable por cuenta

    tasks = []

    def add(idx, label, actividad, d, comentario=""):
        ciudad = d.ciudad or d.nombre or "?"
        descripcion = f"{actividad} - {ciudad}"
        if comentario:
            descripcion += f" — {comentario}"
        task = {
            "id": f"{trip_id}_T{idx:02d}",
            "nombre": label,
            "descripcion": descripcion,
            "actividad": actividad,
            "tipo": act_map.get(actividad, actividad),
            "contacto": contacto(d),
        }
        inicio = _to_trimble_ts(getattr(d, "fecha_inicio", "") or "")
        fin = _to_trimble_ts(getattr(d, "fecha_fin", "") or "")
        if inicio:
            task["timewindowstart"] = inicio
        if fin:
            task["timewindowend"] = fin
        tasks.append(task)

    add(1, f"Origen: {viaje.origen.ciudad or viaje.origen.nombre or '?'}",
        viaje.origen.actividad or "CARGA", viaje.origen, viaje.origen.comentario)
    i = 2
    for p in viaje.paradas:
        # omitir paradas vacías (sin ciudad, nombre, calle ni coordenadas)
        if not (p.ciudad or p.nombre or p.calle or p.lat is not None):
            continue
        act = p.actividad if p.actividad in act_map else "DESCARGA"
        add(i, f"Parada: {p.ciudad or p.nombre or '?'}", act, p, p.comentario)
        i += 1
    add(i, f"Destino: {viaje.destino.ciudad or viaje.destino.nombre or '?'}",
        viaje.destino.actividad or "DESCARGA", viaje.destino, viaje.destino.comentario)

    extra = []
    if viaje.conductor:
        extra.append(f"Conductor: {viaje.conductor}")
    if viaje.tipo_carga:
        extra.append(f"Carga: {viaje.tipo_carga}")
    if viaje.cliente:
        extra.append(f"Cliente: {viaje.cliente}")

    # adjuntar documentos a la primera tarea (origen)
    if documentos and tasks:
        tasks[0]["documentos"] = documentos

    # navegación: propiedades de ruta (routing.*) en la primera tarea (origen de la ruta)
    if tasks:
        tasks[0]["routing"] = True
        # via points (puntos de paso de navegación, manual §5.3.6) en la primera tarea
        if viaje.waypoints:
            tasks[0]["waypoints"] = [
                {"nombre": w.nombre, "lat": w.lat, "lng": w.lng, "distance": w.distance}
                for w in viaje.waypoints
                if w.lat is not None and w.lng is not None
            ]

    # eCMR: adjuntar a la tarea de destino (última tarea = entrega)
    if viaje.ecmr_provider and viaje.ecmr_id and tasks:
        tasks[-1]["ecmr_provider"] = viaje.ecmr_provider
        tasks[-1]["ecmr_id"] = viaje.ecmr_id

    propiedades = {
        "conductor": viaje.conductor,
        "cargo.tipo": viaje.tipo_carga,
    }
    if viaje.cliente:
        propiedades["cliente"] = viaje.cliente
    if viaje.precio:
        propiedades["precio"] = f"{viaje.precio:.2f}"

    return {
        "id": trip_id,
        "nombre": f"Viaje {viaje.origen.ciudad or '?'} -> {viaje.destino.ciudad or '?'}",
        "descripcion": " | ".join(extra) or "Viaje TMS",
        "propiedades": propiedades,
        "tasks": tasks,
    }


def _calcular_ruta(viaje, terminal, conduccion_acumulada_min):
    km_total = 0.0
    peaje_km = 0.0
    peaje_estimado = 0.0
    peaje_fuente = "estimado"
    tiempo_min = 0.0
    trafico_min = 0.0
    pausas_min = 0.0
    puntos = _puntos_del_viaje(viaje)
    if len(puntos) >= 2:
        _, km_total, _, peaje_km = _calc_ruta(puntos)
        peaje_estimado = round(peaje_km * _peaje_rate(_vehiculo_peaje_categoria(terminal)), 2)
        ptv = _ptv_route(puntos, _vehiculo_ptv(terminal), conduccion_acumulada_min)
        if ptv:
            sch = ptv.get("schedule") or {}
            tiempo_min = sch.get("total_min") or ptv.get("travel_time_min") or 0.0
            pausas_min = (sch.get("break_min") or 0.0) + (sch.get("rest_min") or 0.0)
            trafico_min = ptv.get("traffic_delay_min") or 0.0
            if ptv.get("toll") is not None:
                peaje_estimado = float(ptv["toll"])
                peaje_fuente = "ptv"
    return km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min


def _viaje_payload(viaje):
    try:
        return viaje.model_dump(exclude={"documentos"})
    except AttributeError:
        return json.loads(viaje.json(exclude={"documentos"}))


def _guardar_documentos_pedido(conn, trip_id, documentos):
    """Guarda los PDF del pedido en `files` (source='pedido'); se suben al DMS al asignar."""
    for d in documentos:
        contenido = (d.contenido or "").strip()
        if not contenido:
            continue
        nombre = ((d.nombre or "documento.pdf").rsplit("/", 1)[-1])[:120] or "documento.pdf"
        name = f"{uuid.uuid4().hex[:10]}__{nombre}"
        conn.execute(
            "INSERT INTO files (trip_id, name, ftype, ftime, source, formato, content_b64) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (trip_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "pedido", "pdf", contenido),
        )


def _valorar_viaje(viaje, km_total):
    """Aplica la tarifa (km/viaje/kilos) al viaje. Devuelve (precio, precio_unitario).

    Si el viaje tiene tarifa_id, calcula el precio desde la tarifa; si no, usa el
    precio manual. `precio_unitario` es None si no hay tarifa.
    """
    modo = (viaje.modo_tarifa or "viaje").strip().lower()
    if viaje.tarifa_id:
        conn = _db()
        try:
            row = conn.execute("SELECT tipo, precio FROM tarifas WHERE id=?", (viaje.tarifa_id,)).fetchone()
        finally:
            conn.close()
        if row:
            unit = float(row["precio"] or 0)
            if modo == "km":
                return round(km_total * unit, 2), unit
            if modo == "kilos":
                return round(float(viaje.kilos or 0) * unit, 2), unit
            return round(unit, 2), unit
    return round(float(viaje.precio or 0), 2), None


def _crear_pedido(trip_id, viaje):
    km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min = \
        _calcular_ruta(viaje, "", 0.0)
    viaje.precio, precio_unitario = _valorar_viaje(viaje, km_total)
    precio, gastos, margen, iva_pct, base, cuota_iva = _calcular_importes(viaje)
    nombre = f"Viaje {viaje.origen.ciudad or viaje.origen.nombre or '?'} -> {viaje.destino.ciudad or viaje.destino.nombre or '?'}"
    n_tareas = 2 + len([p for p in viaje.paradas if (p.ciudad or p.nombre or p.calle or p.lat is not None)])
    _save_trip(
        trip_id, nombre, "", viaje.conductor, viaje.tipo_carga,
        viaje.origen.ciudad or viaje.origen.nombre,
        viaje.destino.ciudad or viaje.destino.nombre,
        n_tareas, "sin_asignar", None, "",
        cliente=viaje.cliente or "",
        precio=precio, km_total=km_total,
        peaje_km=peaje_km, peaje_estimado=peaje_estimado, peaje_fuente=peaje_fuente,
        tiempo_min=tiempo_min, trafico_min=trafico_min, pausas_min=pausas_min,
        gastos=gastos, factura=viaje.factura or "",
        estado_pago=viaje.estado_pago or "pendiente", iva=iva_pct,
        cliente_id=viaje.cliente_id, conductor_id=viaje.conductor_id,
        fecha_esperada_carga=viaje.fecha_esperada_carga,
        fecha_esperada_descarga=viaje.fecha_esperada_descarga,
        modo_tarifa=viaje.modo_tarifa, tarifa_id=viaje.tarifa_id,
        precio_unitario=precio_unitario, kilos=viaje.kilos,
        subcontratado=viaje.subcontratado, proveedor_id=viaje.proveedor_id, coste=viaje.coste,
    )
    _save_paradas(trip_id, viaje)
    with _db() as conn:
        origen_id = _upsert_direccion(conn, viaje.origen)
        destino_id = _upsert_direccion(conn, viaje.destino)
        _guardar_documentos_pedido(conn, trip_id, viaje.documentos)
        _save_tramos(conn, trip_id, viaje.tramos)
        conn.execute("UPDATE trips SET payload=?, origen_id=?, destino_id=? WHERE id=?",
                     (json.dumps(_viaje_payload(viaje), ensure_ascii=False), origen_id, destino_id, trip_id))
        conn.commit()
    return {
        "ok": True, "trip_id": trip_id, "nombre": nombre, "estado": "sin_asignar",
        "km_total": km_total, "peaje_km": peaje_km, "peaje_estimado": peaje_estimado,
        "peaje_fuente": peaje_fuente, "tiempo_min": tiempo_min, "trafico_min": trafico_min,
        "pausas_min": pausas_min, "precio": precio, "gastos": gastos, "margen": margen,
    }


def _enviar_viaje(trip_id, viaje, terminal, semirremolque, remolque):
    # Resolver el terminal APP (Fleet XPS) para el despacho SOAP; el T4U (`terminal`) queda
    # para telemetría/PTV/posición. Sin APP vinculada cae al terminal APP por defecto.
    conn = _db()
    soap_terminal = _terminal_app(conn, terminal)
    conn.close()

    # Bloqueo: un semirremolque/remolque con viaje activo no puede asignarse de nuevo.
    # El terminal (tractora) SÍ admite varios viajes en cola: se ejecutan uno tras otro,
    # por eso NO se bloquea el terminal (solo los remolques).
    # (se excluye el propio viaje para que su remolque/semirremolque no se bloquee a sí mismo)
    activos = _vehiculos_en_curso(exclude_trip_id=trip_id)
    asignados = [v for v in (semirremolque, remolque) if v]
    bloqueados = [v for v in asignados if v in activos]
    if bloqueados:
        raise HTTPException(
            status_code=409,
            detail={"error": f"Remolque(s) ya asignados a un viaje en curso: {', '.join(bloqueados)}"},
        )

    # 1) subir documentos al DMS de Trimble (límite ~3 MB base64 por PDF)
    documentos = []
    for doc in viaje.documentos:
        if len(doc.contenido) > MAX_DOC_B64:
            mb = round(len(doc.contenido) * 3 / 4 / 1024 / 1024, 1)
            raise HTTPException(
                status_code=413,
                detail={"error": f"El documento «{doc.nombre}» pesa demasiado "
                                 f"(~{mb} MB). Trimble admite hasta ~{MAX_DOC_MB} MB por PDF. "
                                 "Comprímelo o redúcelo."},
            )
        res = get_client().create_document(doc.nombre, doc.contenido, "pdf", True)
        if res.get("ok") and res.get("uniqueDocId"):
            documentos.append({"fileKey": res["uniqueDocId"], "fileName": doc.nombre})
        else:
            raise HTTPException(
                status_code=502,
                detail={
                    "trip_id": trip_id,
                    "error": f"No se pudo subir el documento '{doc.nombre}': "
                             f"{res.get('error') or res.get('status') or 'error desconocido'}",
                },
            )

    # Asignar los documentos al terminal para que el dispositivo pueda descargarlos
    if documentos:
        try:
            get_client().assign_documents([d["fileKey"] for d in documentos], [soap_terminal])
        except Exception as e:
            # No bloquea el envío: el adjunto a la tarea (activity.file.N.fileKey) sigue vigente
            print(f"[DMS] assign_documents falló (no bloquea): {e}")

    trip = _build_trip(viaje, trip_id, documentos)
    nombre = trip["nombre"]
    n_tareas = len(trip["tasks"])

    pasos = get_client().send_trip(trip, soap_terminal)

    errores = [p["error"] for p in pasos if not p["ok"]]
    estado = "enviado" if pasos and all(p["ok"] for p in pasos) else "error"
    error = "; ".join(e for e in errores if e) or None
    # Sellar el puente: marcar el viaje activo en Redis para el worker de telemetría.
    if estado == "enviado":
        _set_viaje_activo(terminal, trip_id)

    km_total, peaje_km, peaje_estimado, peaje_fuente, tiempo_min, trafico_min, pausas_min = \
        _calcular_ruta(viaje, terminal, viaje.conduccion_acumulada_min)
    viaje.precio, precio_unitario = _valorar_viaje(viaje, km_total)
    precio, gastos, margen, iva_pct, base, cuota_iva = _calcular_importes(viaje)

    # km en vacío: distancia desde la última posición conocida del vehículo hasta el origen
    km_vacio = 0.0
    pos = _vehiculo_posicion(terminal)
    if pos and viaje.origen.lat is not None and viaje.origen.lng is not None:
        km_vacio = _haversine_km(pos[0], pos[1], viaje.origen.lat, viaje.origen.lng) or 0.0

    _save_trip(
        trip_id, nombre, terminal, viaje.conductor, viaje.tipo_carga,
        viaje.origen.ciudad or viaje.origen.nombre,
        viaje.destino.ciudad or viaje.destino.nombre,
        n_tareas, estado, error, terminal,
        semirremolque_id=semirremolque,
        remolque_id=remolque,
        cliente=viaje.cliente or "",
        precio=precio,
        km_total=km_total,
        peaje_km=peaje_km,
        peaje_estimado=peaje_estimado,
        peaje_fuente=peaje_fuente,
        tiempo_min=tiempo_min,
        trafico_min=trafico_min,
        pausas_min=pausas_min,
        gastos=gastos,
        factura=viaje.factura or "",
        estado_pago=viaje.estado_pago or "pendiente",
        iva=iva_pct,
        cliente_id=viaje.cliente_id,
        conductor_id=viaje.conductor_id,
        fecha_esperada_carga=viaje.fecha_esperada_carga,
        fecha_esperada_descarga=viaje.fecha_esperada_descarga,
        modo_tarifa=viaje.modo_tarifa, tarifa_id=viaje.tarifa_id,
        precio_unitario=precio_unitario, kilos=viaje.kilos,
        subcontratado=viaje.subcontratado, proveedor_id=viaje.proveedor_id, coste=viaje.coste,
    )
    _save_paradas(trip_id, viaje)
    conn = _db()
    conn.execute("UPDATE trips SET km_vacio=? WHERE id=?", (km_vacio, trip_id))
    _save_tramos(conn, trip_id, viaje.tramos)
    conn.commit()
    conn.close()

    if estado == "error":
        raise HTTPException(
            status_code=502,
            detail={"trip_id": trip_id, "pasos": pasos, "error": error},
        )

    return {
        "ok": True,
        "trip_id": trip_id,
        "nombre": nombre,
        "terminal": terminal,
        "tareas": n_tareas,
        "km_total": km_total,
        "km_vacio": km_vacio,
        "peaje_km": peaje_km,
        "peaje_estimado": peaje_estimado,
        "peaje_fuente": peaje_fuente,
        "tiempo_min": tiempo_min,
        "trafico_min": trafico_min,
        "pausas_min": pausas_min,
        "precio": precio,
        "gastos": gastos,
        "margen": margen,
        "base": base,
        "iva": cuota_iva,
        "factura": viaje.factura or "",
        "estado_pago": viaje.estado_pago or "pendiente",
        "pasos": pasos,
    }


def _terminal_app(conn, vehiculo_id):
    """Resuelve el terminal APP (Fleet XPS) para el despacho SOAP, desde un vehículo T4U.

    El T4U (id = matrícula) solo da telemetría/tacógrafo; los viajes y question paths
    se envían a la APP vinculada (id con sufijo 'APP'). Si no hay APP vinculada,
    cae al terminal APP por defecto (config.DEFAULT_TRIMBLE_TERMINAL, p. ej. 'demo').
    """
    row = conn.execute("SELECT app_terminal FROM vehiculos WHERE id=?", (vehiculo_id,)).fetchone()
    if row and row["app_terminal"]:
        return row["app_terminal"]
    return config.DEFAULT_TRIMBLE_TERMINAL or vehiculo_id


def _vehiculo_ptv(terminal):
    """Atributos PTV del vehículo para el cálculo de peaje exacto."""
    with _db() as conn:
        row = conn.execute(
            "SELECT ptv_profile, ejes, mma, clase_euro FROM vehiculos WHERE id=?", (terminal,)
        ).fetchone()
    return {
        "ptv_profile": (row["ptv_profile"] if row and row["ptv_profile"] else "EUR_TRAILER_TRUCK"),
        "ejes": (row["ejes"] if row else None),
        "mma": (row["mma"] if row else None),
        "clase_euro": (row["clase_euro"] if row and row["clase_euro"] else ""),
    }


from services.telemetria import _set_viaje_activo
from services.contabilidad import _next_referencia
from services.configuracion import _actividades_map

def _vehiculo_posicion(terminal):
    """Última posición conocida de un vehículo (lat, lng) o None."""
    with _db() as conn:
        row = conn.execute("SELECT last_lat, last_lng FROM vehiculos WHERE id=?", (terminal,)).fetchone()
    if row and row["last_lat"] is not None and row["last_lng"] is not None:
        return (float(row["last_lat"]), float(row["last_lng"]))
    return None


def _to_trimble_ts(iso_str: str) -> str:
    """Convierte un timestamp ISO a formato Trimble (UTC, sin 'Z', con ms)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
    except ValueError:
        return ""
    dt = dt.astimezone(datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
