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

router = APIRouter()


@router.post("/api/costes-fijos")
def add_coste(c: CosteFijo, conn = Depends(get_conn)):
    conn.execute("INSERT INTO costes_fijos (terminal, concepto, importe) VALUES (?,?,?)",
                 (c.terminal, c.concepto, c.importe))
    conn.commit()
    return {"ok": True}




@router.post("/api/gastos")
def add_gasto(g: Gasto, conn = Depends(get_conn)):
    # importe = total (IVA incluido); se deriva base y cuota de IVA soportado
    total = round(float(g.importe or 0), 2)
    iva_pct = round(float(g.iva or 21), 2)
    ret_pct = round(float(g.retencion or 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    cuota = round(total - base, 2)
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    cuenta = (g.cuenta or "").strip() or _categoria_cuenta(conn, g.categoria)
    cur = conn.execute(
        "INSERT INTO gastos (terminal, trip_id, categoria, fecha, importe, concepto, foto, creado, proveedor_id, iva, retencion, cuenta) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (g.terminal, g.trip_id, g.categoria, g.fecha, g.importe, g.concepto, g.foto,
         datetime.datetime.utcnow().isoformat() + "Z", g.proveedor_id, iva_pct, ret_pct, cuenta),
    )
    gasto_id = cur.fetchone()["id"]
    if total != 0:
        lineas = [(cuenta, base, 0, g.concepto or g.categoria or "Gasto")]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("410", 0, a_pagar, g.concepto or g.categoria or "Gasto"))
        if retencion > 0:
            lineas.append(("4751", 0, retencion, "Retención IRPF"))
        try:
            _post_asiento((g.fecha or "")[:10], f"Gasto {g.categoria or 'Otros'}: {g.concepto or ''}",
                          lineas, origen="gasto", gasto_id=gasto_id, conn=conn)
        except ValueError as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    return {"ok": True, "gasto_id": gasto_id}




@router.post("/api/gastos/vehiculos")
def add_gasto_vehiculo(g: GastoVehiculo, conn = Depends(get_conn)):
    tipo = (g.tipo or "combustible").lower()
    if tipo not in _TIPO_GASTO_CUENTA:
        raise HTTPException(status_code=400, detail={"error": "Tipo inválido."})
    iva_pct = round(float(g.iva or 21), 2)
    importe = round(float(g.importe_total or 0), 2)
    base = round(float(g.base_imponible or 0), 2)
    # Si solo viene el total (p. ej. flujo OCR), se deriva la base.
    if base <= 0 and importe > 0:
        base = round(importe / (1 + iva_pct / 100.0), 2) if iva_pct > 0 else importe
    if importe <= 0 and base > 0:
        importe = round(base * (1 + iva_pct / 100.0), 2)
    cuota = round(importe - base, 2)
    litros = round(float(g.litros or 0), 2)
    cuenta = (g.cuenta_contable_gasto or "").strip() or _TIPO_GASTO_CUENTA[tipo]
    estado = (g.estado_pago or "Pendiente").strip() or "Pendiente"
    cur = conn.execute(
        "INSERT INTO gastos_vehiculos (vehiculo_id, proveedor_id, fecha, tipo, litros, base_imponible, iva, "
        "importe_total, factura_ref, cuenta_contable_gasto, estado_pago, archivo_base64, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (g.vehiculo_id, g.proveedor_id, g.fecha, tipo, litros, base, iva_pct, importe,
         g.factura_ref, cuenta, estado, g.archivo_base64, datetime.datetime.utcnow().isoformat() + "Z"),
    )
    gasto_id = cur.fetchone()["id"]
    if importe > 0:
        # Debe: cuenta de gasto (base) + 472 IVA soportado (cuota) · Haber: 400 Proveedores (total)
        label = tipo.capitalize()
        concepto = f"{label} {g.factura_ref or ''}".strip()
        lineas = [(cuenta, base, 0, concepto)]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("400", 0, importe, "Proveedor"))
        try:
            _registrar_asiento((g.fecha or "")[:10], f"Gasto {label}: {concepto}", lineas,
                               origen="Gasto_Vehiculo", origen_id=str(gasto_id), conn=conn)
        except ValueError as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    return {"ok": True, "gasto_id": gasto_id}




@router.delete("/api/costes-fijos/{coste_id}")
def del_coste(coste_id: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM costes_fijos WHERE id=?", (coste_id,))
    conn.commit()
    return {"ok": True}




@router.delete("/api/gastos/{gasto_id}")
def del_gasto(gasto_id: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM asientos WHERE gasto_id=?", (gasto_id,))
    conn.execute("DELETE FROM gastos WHERE id=?", (gasto_id,))
    conn.commit()
    return {"ok": True}




@router.post("/api/gastos/ocr")
def gastos_ocr(req: dict):
    b64 = (req.get("archivo_base64") or req.get("file_base64") or "").strip()
    if not b64:
        raise HTTPException(status_code=400, detail={"error": "Sin archivo (archivo_base64)."})
    if b64.startswith("data:") and "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "Base64 inválido."})
    texto = _pdf_a_texto(raw)
    return {
        "ok": True,
        "borrador": {
            "matricula": _regex_matricula(texto),
            "litros": _regex_litros(texto),
            "importe_total": _regex_importe(texto),
            "fecha": _regex_fecha(texto),
        },
        "archivo_base64": b64,
        "texto_extraido": texto[:2000],
    }




@router.get("/api/gastos/vehiculos/{gasto_id}")
def get_gasto_vehiculo(gasto_id: int, conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM gastos_vehiculos WHERE id=?", (gasto_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    return dict(row)




@router.get("/api/costes-fijos")
def list_costes(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM costes_fijos ORDER BY terminal, concepto").fetchall()
    return {"costes": [dict(r) for r in rows]}






@router.get("/api/gastos")
def list_gastos(terminal: str = "", categoria: str = "", desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    query = ("SELECT g.*, p.nombre AS proveedor, p.cif AS proveedor_cif "
             "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id = p.id")
    conds, params = [], []
    if terminal:
        conds.append("g.terminal = ?")
        params.append(terminal)
    if categoria:
        conds.append("g.categoria = ?")
        params.append(categoria)
    if desde:
        conds.append("substr(g.fecha, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(g.fecha, 1, 10) <= ?")
        params.append(hasta)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY g.fecha DESC, g.id DESC"
    rows = conn.execute(query, params).fetchall()
    return {"gastos": [dict(r) for r in rows]}




@router.get("/api/gastos/vehiculos")
def list_gastos_vehiculos(conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT g.id, g.vehiculo_id, g.proveedor_id, g.fecha, g.tipo, g.litros, "
        "g.importe_total, g.factura_ref, g.creado, "
        "COALESCE(v.matricula,'') AS matricula, COALESCE(p.nombre,'') AS proveedor "
        "FROM gastos_vehiculos g "
        "LEFT JOIN vehiculos v ON v.id = g.vehiculo_id "
        "LEFT JOIN proveedores p ON p.id = g.proveedor_id "
        "ORDER BY g.fecha DESC, g.id DESC LIMIT 500"
    ).fetchall()
    return {"gastos": [dict(r) for r in rows]}




@router.post("/api/ocr")
def ocr_ticket(req: OcrRequest):
    """Extrae proveedor/fecha/total de una foto de ticket o factura (Tesseract)."""
    data = req.imagen
    if data.startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        img = base64.b64decode(data)
    except Exception:
        raise HTTPException(status_code=400, detail={"error": "Imagen base64 inválida"})
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(img)
        tmp = f.name
    texto = ""
    try:
        proc = subprocess.run(["tesseract", tmp, "stdout", "-l", "spa+eng"],
                              capture_output=True, text=True, timeout=60)
        texto = (proc.stdout or "").strip()
    except Exception:
        texto = ""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    proveedor, fecha, total = _parse_ticket(texto)
    num_factura, cif, base, iva = _parse_documento(texto)
    return {"texto": texto, "proveedor": proveedor, "fecha": fecha, "total": total,
            "numero": num_factura, "cif": cif, "base": base, "iva": iva}




@router.post("/api/gastos/{gasto_id}/pagar")
def pagar_gasto(gasto_id: int, conn = Depends(get_conn)):
    """Marca un gasto como pagado: Debe 410 / Haber 572 por el importe a pagar."""
    g = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    if not g:
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    if g["pagado"]:
        return {"ok": True, "ya_pagado": True}
    total = round(float(g["importe"] or 0), 2)
    iva_pct = round(float(g["iva"] or 21), 2)
    ret_pct = round(float(g["retencion"] or 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    fecha = (g["fecha"] or "")[:10] or datetime.date.today().isoformat()
    if a_pagar != 0:
        _post_asiento(
            fecha, f"Pago gasto {g['concepto'] or g['categoria'] or gasto_id}",
            [("410", a_pagar, 0, "Pago acreedor"),
             ("572", 0, a_pagar, "Pago gasto")],
            origen="pago", gasto_id=gasto_id, conn=conn,
        )
    conn.execute("UPDATE gastos SET pagado=true WHERE id=?", (gasto_id,))
    conn.commit()
    return {"ok": True, "a_pagar": a_pagar}




@router.get("/api/kpis/rentabilidad-flota")
def rentabilidad_flota(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    """Rentabilidad por vehículo: cruza ingresos (trips Entregado) con gastos (gastos_vehiculos).

    Por defecto filtra el mes en curso. Devuelve por tractora: vehiculo_id, matricula,
    total_ingresos, total_gastos, margen_neto y margen_porcentaje.
    """
    hoy = datetime.date.today()
    if not desde:
        desde = f"{hoy.year:04d}-{hoy.month:02d}-01"
    if not hasta:
        nxt = hoy.replace(day=28) + datetime.timedelta(days=4)
        hasta = (nxt - datetime.timedelta(days=nxt.day)).isoformat()
    rows = conn.execute(
        "WITH ingresos AS ("
        "  SELECT terminal AS vehiculo_id, COALESCE(SUM(precio),0) AS ing "
        "  FROM trips "
        "  WHERE LOWER(COALESCE(estado,'')) = 'entregado' "
        "    AND substr(COALESCE(fecha_actualizacion, creado),1,10) >= ? "
        "    AND substr(COALESCE(fecha_actualizacion, creado),1,10) <= ? "
        "  GROUP BY terminal"
        "), gastos AS ("
        "  SELECT vehiculo_id, COALESCE(SUM(importe_total),0) AS gas "
        "  FROM gastos_vehiculos "
        "  WHERE substr(COALESCE(fecha,''),1,10) >= ? AND substr(COALESCE(fecha,''),1,10) <= ? "
        "  GROUP BY vehiculo_id"
        ") "
        "SELECT v.id AS vehiculo_id, COALESCE(v.matricula,'') AS matricula, "
        "       COALESCE(i.ing,0) AS total_ingresos, COALESCE(g.gas,0) AS total_gastos "
        "FROM vehiculos v "
        "LEFT JOIN ingresos i ON i.vehiculo_id = v.id "
        "LEFT JOIN gastos g ON g.vehiculo_id = v.id "
        "WHERE COALESCE(i.ing,0) <> 0 OR COALESCE(g.gas,0) <> 0 "
        "ORDER BY (COALESCE(i.ing,0) - COALESCE(g.gas,0)) DESC",
        (desde, hasta, desde, hasta),
    ).fetchall()
    flota = []
    for r in rows:
        ing = float(r["total_ingresos"] or 0)
        gas = float(r["total_gastos"] or 0)
        margen = round(ing - gas, 2)
        pct = round((margen / ing) * 100, 2) if ing > 0 else None
        flota.append({
            "vehiculo_id": r["vehiculo_id"],
            "matricula": r["matricula"] or "",
            "total_ingresos": round(ing, 2),
            "total_gastos": round(gas, 2),
            "margen_neto": margen,
            "margen_porcentaje": pct,
        })
    return {"desde": desde, "hasta": hasta, "flota": flota}




@router.get("/api/gastos/resumen")
def resumen_gastos(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    where, conds, params = "", [], []
    if desde:
        conds.append("substr(fecha, 1, 10) >= ?")
        params.append(desde)
    if hasta:
        conds.append("substr(fecha, 1, 10) <= ?")
        params.append(hasta)
    if conds:
        where = " WHERE " + " AND ".join(conds)
    cat_rows = conn.execute(
        f"SELECT categoria, SUM(importe) AS total, COUNT(*) AS n FROM gastos{where} GROUP BY categoria ORDER BY total DESC",
        params,
    ).fetchall()
    prov_rows = conn.execute(
        f"SELECT p.id, p.nombre, p.cif, SUM(g.importe) AS total, COUNT(*) AS n "
        f"FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id = p.id{where} "
        f"GROUP BY p.id ORDER BY total DESC",
        params,
    ).fetchall()
    return {
        "resumen": [
            {"categoria": r["categoria"] or "Sin categoría", "total": round(r["total"] or 0, 2), "n": r["n"]}
            for r in cat_rows
        ],
        "por_proveedor": [
            {"id": r["id"], "nombre": r["nombre"] or "Sin proveedor", "cif": r["cif"] or "",
             "total": round(r["total"] or 0, 2), "n": r["n"]}
            for r in prov_rows
        ],
    }




@router.patch("/api/gastos/{gasto_id}")
def upd_gasto(gasto_id: int, g: Gasto, conn = Depends(get_conn)):
    existing = conn.execute("SELECT * FROM gastos WHERE id=?", (gasto_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail={"error": "Gasto no encontrado."})
    total = round(float(g.importe or 0), 2)
    iva_pct = round(float(existing["iva"] if existing["iva"] is not None else 21), 2)
    ret_pct = round(float(existing["retencion"] if existing["retencion"] is not None else 0), 2)
    if iva_pct > 0:
        base = round(total / (1 + iva_pct / 100.0), 2)
    else:
        base = total
    cuota = round(total - base, 2)
    retencion = round(base * ret_pct / 100.0, 2) if ret_pct > 0 else 0.0
    a_pagar = round(total - retencion, 2)
    cuenta = (g.cuenta or "").strip() or _categoria_cuenta(conn, g.categoria)
    conn.execute(
        "UPDATE gastos SET terminal=?, categoria=?, fecha=?, importe=?, concepto=?, proveedor_id=?, cuenta=? WHERE id=?",
        (g.terminal, g.categoria, g.fecha, g.importe, g.concepto, g.proveedor_id, cuenta, gasto_id),
    )
    conn.execute("DELETE FROM asientos WHERE origen='gasto' AND gasto_id=?", (gasto_id,))
    if total != 0:
        lineas = [(cuenta, base, 0, g.concepto or g.categoria or "Gasto")]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("410", 0, a_pagar, g.concepto or g.categoria or "Gasto"))
        if retencion > 0:
            lineas.append(("4751", 0, retencion, "Retención IRPF"))
        try:
            _post_asiento((g.fecha or "")[:10], f"Gasto {g.categoria or 'Otros'}: {g.concepto or ''}",
                          lineas, origen="gasto", gasto_id=gasto_id, conn=conn)
        except ValueError as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    return {"ok": True}




@router.patch("/api/gastos/vehiculos/{gasto_id}")
def upd_gasto_vehiculo(gasto_id: int, body: dict, conn = Depends(get_conn)):
    """Edición en línea segura: solo estado de pago y referencia (no toca importes/contabilidad)."""
    allow = ("estado_pago", "factura_ref")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE gastos_vehiculos SET {sets} WHERE id=?", (*fields.values(), gasto_id))
    conn.commit()
    return {"ok": True}


