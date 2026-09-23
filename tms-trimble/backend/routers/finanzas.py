"""
Router de gastos."""
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

router = APIRouter(dependencies=[Depends(require_role(["admin"]))])


@router.get("/api/liquidaciones")
def list_liquidaciones(conn = Depends(get_conn)):
    liq = conn.execute(
        "SELECT l.*, t.nombre AS transportista FROM liquidaciones l "
        "LEFT JOIN transportistas t ON l.transportista_id = t.id ORDER BY l.fecha"
    ).fetchall()
    acum = conn.execute(
        "SELECT t.id, t.nombre, t.cif, t.tarifa, "
        "COALESCE(SUM(CASE WHEN l.pagado THEN 0 ELSE l.importe END), 0) AS pendiente, "
        "COALESCE(SUM(l.importe), 0) AS total "
        "FROM transportistas t LEFT JOIN liquidaciones l ON l.transportista_id = t.id "
        "GROUP BY t.id, t.nombre, t.cif, t.tarifa ORDER BY t.nombre"
    ).fetchall()
    return {"liquidaciones": [dict(r) for r in liq], "por_transportista": [dict(r) for r in acum]}




@router.post("/api/liquidaciones")
def add_liquidacion(l: Liquidacion, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO liquidaciones (transportista_id, fecha, importe, concepto, pagado, creado) "
        "VALUES (?,?,?,?,?,?)",
        (l.transportista_id, l.fecha, l.importe, l.concepto, l.pagado,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    conn.commit()
    return {"ok": True}




@router.patch("/api/liquidaciones/{lid}")
def upd_liquidacion(lid: int, l: Optional[Liquidacion] = None, conn = Depends(get_conn)):
    if l is None:
        conn.execute("UPDATE liquidaciones SET pagado = NOT pagado WHERE id=?", (lid,))
    else:
        conn.execute(
            "UPDATE liquidaciones SET transportista_id=?, fecha=?, importe=?, concepto=?, pagado=? WHERE id=?",
            (l.transportista_id, l.fecha, l.importe, l.concepto, l.pagado, lid),
        )
    conn.commit()
    return {"ok": True}




@router.delete("/api/liquidaciones/{lid}")
def del_liquidacion(lid: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM liquidaciones WHERE id=?", (lid,))
    conn.commit()
    return {"ok": True}




@router.get("/api/ingresos")
def ingresos(desde: str = "", hasta: str = "", estado: str = "", conn = Depends(get_conn)):
    """Agrega los ingresos por vehículo y por cliente (precio de los viajes).

    Filtros opcionales: desde/hasta (fecha YYYY-MM-DD) y estado.
    """
    query = ("SELECT id, nombre, terminal, cliente, conductor, tipo_carga, origen, destino, "
             "precio, gastos, km_total, estado_pago, factura, iva, creado FROM trips")
    conds, params = [], []
    if desde:
        conds.append("substr(creado, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(creado, 1, 10) <= ?")
        params.append(hasta)
    if estado:
        conds.append("estado = ?")
        params.append(estado)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    rows = conn.execute(query, params).fetchall()

    cf_map = {}
    for r in conn.execute("SELECT terminal, SUM(importe) AS t FROM costes_fijos GROUP BY terminal").fetchall():
        cf_map[(r["terminal"] or "").strip()] = round(r["t"] or 0, 2)
    gv_map = {}
    gv_conds, gv_params = [], []
    if desde:
        gv_conds.append("substr(fecha, 1, 10) >= ?")
        gv_params.append(desde)
    if hasta:
        gv_conds.append("substr(fecha, 1, 10) <= ?")
        gv_params.append(hasta)
    gv_where = (" WHERE " + " AND ".join(gv_conds)) if gv_conds else ""
    for r in conn.execute(f"SELECT terminal, SUM(importe) AS t FROM gastos{gv_where} GROUP BY terminal", gv_params).fetchall():
        gv_map[(r["terminal"] or "").strip()] = round(r["t"] or 0, 2)

    # Costes de estructura: % sobre ingresos (configurable en /api/config)
    try:
        pct_estructura = float(_get_config("costes_estructura_pct", "0") or 0)
    except Exception:
        pct_estructura = 0.0

    total = 0.0
    total_km = 0.0
    total_gastos = 0.0
    cobrado = 0.0
    pendiente = 0.0
    por_vehiculo = {}
    por_cliente = {}
    por_mes = {}
    detalle = []
    for r in rows:
        p = float(r["precio"] or 0) or 0.0
        g = float(r["gastos"] or 0) or 0.0
        km = float(r["km_total"] or 0) or 0.0
        detalle.append({
            "id": r["id"], "nombre": r["nombre"], "terminal": r["terminal"] or "",
            "cliente": r["cliente"] or "", "conductor": r["conductor"] or "",
            "tipo_carga": r["tipo_carga"] or "", "origen": r["origen"] or "",
            "destino": r["destino"] or "", "precio": round(p, 2), "gastos": round(g, 2),
            "margen": round(p - g, 2), "km_total": round(km, 1),
            "estado_pago": r["estado_pago"] or "", "factura": r["factura"] or "",
            "creado": r["creado"] or "",
        })
        if p <= 0:
            continue
        ep = (r["estado_pago"] or "").strip().lower()
        mes = (r["creado"] or "")[:7] or "—"
        total += p
        total_gastos += g
        total_km += km
        if ep == "cobrada":
            cobrado += p
        else:
            pendiente += p
        term = (r["terminal"] or "").strip() or "Sin vehículo"
        cli = (r["cliente"] or "").strip() or "Sin cliente"
        v = por_vehiculo.setdefault(term, {"total": 0.0, "gastos": 0.0, "km": 0.0, "viajes": 0})
        v["total"] += p
        v["gastos"] += g
        v["km"] += km
        v["viajes"] += 1
        c = por_cliente.setdefault(cli, {"total": 0.0, "gastos": 0.0, "km": 0.0, "viajes": 0})
        c["total"] += p
        c["gastos"] += g
        c["km"] += km
        c["viajes"] += 1
        m = por_mes.setdefault(mes, {"total": 0.0, "gastos": 0.0, "cobrado": 0.0, "viajes": 0})
        m["total"] += p
        m["gastos"] += g
        if ep == "cobrada":
            m["cobrado"] += p
        m["viajes"] += 1

    def _eur_km(t, k):
        return round(t / k, 2) if k > 0 else None

    def _fmt(d):
        return sorted(
            [{"nombre": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
              "margen": round(v["total"] - v["gastos"], 2), "km": round(v["km"], 1),
              "eur_km": _eur_km(v["total"], v["km"]), "viajes": v["viajes"]}
             for k, v in d.items()],
            key=lambda x: -x["total"],
        )

    def _fmt_vehiculo(d):
        out = []
        for k, v in d.items():
            cf = cf_map.get(k, 0.0)
            gv = gv_map.get(k, 0.0)
            ce = round(v["total"] * pct_estructura / 100, 2)
            out.append({"nombre": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
                        "margen": round(v["total"] - v["gastos"], 2), "costes_fijos": cf,
                        "gastos_vehiculo": gv, "costes_estructura": ce,
                        "margen_real": round(v["total"] - v["gastos"] - cf - gv - ce, 2),
                        "km": round(v["km"], 1), "eur_km": _eur_km(v["total"], v["km"]),
                        "viajes": v["viajes"]})
        return sorted(out, key=lambda x: -x["total"])

    def _fmt_mes():
        return sorted(
            [{"mes": k, "total": round(v["total"], 2), "gastos": round(v["gastos"], 2),
              "margen": round(v["total"] - v["gastos"], 2),
              "cobrado": round(v["cobrado"], 2),
              "pendiente": round(v["total"] - v["cobrado"], 2), "viajes": v["viajes"]}
             for k, v in por_mes.items()],
            key=lambda x: x["mes"],
        )

    total_cf = round(sum(cf_map.values()), 2)
    total_gv = round(sum(gv_map.values()), 2)
    costes_estructura = round(total * pct_estructura / 100, 2)
    return {
        "total": round(total, 2),
        "total_gastos": round(total_gastos, 2),
        "margen": round(total - total_gastos, 2),
        "cobrado": round(cobrado, 2),
        "pendiente": round(pendiente, 2),
        "total_km": round(total_km, 1),
        "eur_km": _eur_km(total, total_km),
        "viajes": sum(v["viajes"] for v in por_vehiculo.values()),
        "costes_fijos": total_cf,
        "gastos_vehiculo": total_gv,
        "costes_estructura": costes_estructura,
        "costes_estructura_pct": round(pct_estructura, 2),
        "margen_real": round(total - total_gastos - total_cf - total_gv - costes_estructura, 2),
        "por_vehiculo": _fmt_vehiculo(por_vehiculo),
        "por_cliente": _fmt(por_cliente),
        "por_mes": _fmt_mes(),
        "detalle": detalle,
    }


