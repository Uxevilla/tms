"""
Router de maestros."""
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


@router.post("/api/clientes")
def add_cliente(c: Cliente, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO clientes (nombre, cif, direccion, poblacion, cp, telefono, email, cuenta_contable_defecto) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (c.nombre, c.cif, c.direccion, c.poblacion, c.cp, c.telefono, c.email, c.cuenta_contable_defecto),
    )
    conn.commit()
    return {"ok": True}




@router.post("/api/conductores")
def add_conductor(c: Conductor, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO conductores (nombre, dni, telefono, email) VALUES (?,?,?,?)",
        (c.nombre, c.dni, c.telefono, c.email),
    )
    conn.commit()
    return {"ok": True}




@router.post("/api/direcciones")
def add_direccion(d: DireccionMaestro, conn = Depends(get_conn)):
    cur = conn.execute(
        "INSERT INTO direcciones (nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (d.nombre, d.empresa, d.calle, d.numero, d.ciudad, d.cp, d.pais, d.lat, d.lng, d.comentario,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    nuevo_id = cur.fetchone()["id"]
    conn.commit()
    return {"ok": True, "id": nuevo_id}




@router.post("/api/proveedores")
def add_proveedor(p: Proveedor, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO proveedores (nombre, cif, direccion, poblacion, cp, telefono, email, cuenta_contable_defecto) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (p.nombre, p.cif, p.direccion, p.poblacion, p.cp, p.telefono, p.email, p.cuenta_contable_defecto),
    )
    conn.commit()
    return {"ok": True}




@router.post("/api/transportistas")
def add_transportista(t: Transportista, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO transportistas (nombre, cif, telefono, email, tarifa) VALUES (?,?,?,?,?)",
        (t.nombre, t.cif, t.telefono, t.email, t.tarifa),
    )
    conn.commit()
    return {"ok": True}




@router.get("/api/direcciones/buscar")
def buscar_direcciones(q: str = "", conn = Depends(get_conn)):
    """Buscador de origen/destino: primero maestros (direcciones + clientes), luego Photon (externo)."""
    q = (q or "").strip()
    if not q:
        conn = _db()
        rows = conn.execute("SELECT * FROM direcciones ORDER BY ciudad, nombre LIMIT 20").fetchall()
        return {"sugerencias": [_direccion_dict(r) for r in rows]}

    like = f"%{q}%"
    out = []
    rows = conn.execute(
        "SELECT * FROM direcciones WHERE nombre ILIKE ? OR empresa ILIKE ? OR calle ILIKE ? OR ciudad ILIKE ? OR cp ILIKE ? "
        "ORDER BY ciudad, nombre LIMIT 8",
        (like, like, like, like, like),
    ).fetchall()
    for r in rows:
        out.append(_direccion_dict(r))
    cli = conn.execute(
        "SELECT id, nombre, direccion, poblacion, cp FROM clientes WHERE nombre ILIKE ? OR cif ILIKE ? ORDER BY nombre LIMIT 5",
        (like, like),
    ).fetchall()
    for c in cli:
        out.append({"id": None, "tipo": "cliente", "nombre": c["nombre"], "empresa": c["nombre"],
                    "calle": c["direccion"] or "", "numero": "", "ciudad": c["poblacion"] or "",
                    "cp": c["cp"] or "", "pais": "ES", "lat": None, "lng": None})
    out.extend(_buscar_photon(q))
    return {"sugerencias": out}




@router.delete("/api/clientes/{cli_id}")
def del_cliente(cli_id: int, user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM clientes WHERE id=?", (cli_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Cliente no encontrado"})
    quien = user.get("usuario") or user.get("rol") or "sistema"
    conn.execute("UPDATE clientes SET borrado=true, borrado_por=?, borrado_en=? WHERE id=?",
                 (quien, datetime.datetime.utcnow().isoformat() + "Z", cli_id))
    _auditar(conn, "clientes", cli_id, "eliminar", quien, antes=dict(row))
    conn.commit()
    return {"ok": True}




@router.delete("/api/conductores/{con_id}")
def del_conductor(con_id: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM conductores WHERE id=?", (con_id,))
    conn.commit()
    return {"ok": True}






@router.delete("/api/direcciones/{did}")
def del_direccion(did: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM direcciones WHERE id=?", (did,))
    conn.commit()
    return {"ok": True}




@router.delete("/api/proveedores/{prov_id}")
def del_proveedor(prov_id: int, user: dict = Depends(require_role(["admin", "dispatcher"])), conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM proveedores WHERE id=?", (prov_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    quien = user.get("usuario") or user.get("rol") or "sistema"
    conn.execute("UPDATE proveedores SET borrado=true, borrado_por=?, borrado_en=? WHERE id=?",
                 (quien, datetime.datetime.utcnow().isoformat() + "Z", prov_id))
    _auditar(conn, "proveedores", prov_id, "eliminar", quien, antes=dict(row))
    conn.commit()
    return {"ok": True}




@router.delete("/api/transportistas/{tid}")
def del_transportista(tid: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM liquidaciones WHERE transportista_id=?", (tid,))
    conn.execute("DELETE FROM transportistas WHERE id=?", (tid,))
    conn.commit()
    return {"ok": True}




@router.get("/api/clientes")
def list_clientes(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM clientes WHERE COALESCE(borrado, false) = false ORDER BY nombre").fetchall()
    return {"clientes": [dict(r) for r in rows]}




@router.get("/api/conductores")
def list_conductores(fecha_esperada_carga: str = "", conn = Depends(get_conn)):
    """Conductores con disponibilidad según ausencias_empleados para la fecha de carga indicada."""
    fecha = (fecha_esperada_carga or "")[:10]
    if fecha:
        rows = conn.execute(
            """
            WITH aus AS (
                SELECT DISTINCT ON (empleado_id) empleado_id, tipo
                FROM ausencias_empleados
                WHERE ? >= fecha_inicio AND ? <= fecha_fin
                ORDER BY empleado_id, fecha_inicio
            )
            SELECT c.*, (aus.empleado_id IS NULL) AS disponible, aus.tipo AS motivo_ausencia
            FROM conductores c
            LEFT JOIN aus ON aus.empleado_id = c.empleado_id
            ORDER BY c.nombre
            """,
            (fecha, fecha),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT c.*, true AS disponible, NULL AS motivo_ausencia FROM conductores c ORDER BY c.nombre"
        ).fetchall()
    return {"conductores": [dict(r) for r in rows]}




@router.get("/api/direcciones")
def list_direcciones(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM direcciones ORDER BY ciudad, nombre, calle").fetchall()
    return {"direcciones": [dict(r) for r in rows]}




@router.get("/api/proveedores")
def list_proveedores(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM proveedores WHERE COALESCE(borrado, false) = false ORDER BY nombre").fetchall()
    return {"proveedores": [dict(r) for r in rows]}




@router.get("/api/transportistas")
def list_transportistas(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM transportistas ORDER BY nombre").fetchall()
    return {"transportistas": [dict(r) for r in rows]}




@router.post("/api/conductores/sincronizar-rrhh")
def sincronizar_conductores_rrhh(conn = Depends(get_conn)):
    """Nutre el maestro de conductores desde los empleados (categoría Conductor)."""
    emps = conn.execute(
        "SELECT * FROM empleados WHERE categoria='Conductor' ORDER BY nombre, apellidos"
    ).fetchall()
    creados, actualizados = 0, 0
    for e in emps:
        exist = conn.execute("SELECT id FROM conductores WHERE empleado_id=?", (e["id"],)).fetchone()
        if exist:
            actualizados += 1
        else:
            creados += 1
        _sync_conductor(conn, e)
    conn.commit()
    return {"ok": True, "creados": creados, "actualizados": actualizados, "empleados_conductor": len(emps)}




@router.patch("/api/clientes/{cli_id}")
def upd_cliente(cli_id: int, body: dict, conn = Depends(get_conn)):
    allow = ("nombre", "cif", "direccion", "poblacion", "cp", "telefono", "email", "cuenta_contable_defecto")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE clientes SET {sets} WHERE id=?", (*fields.values(), cli_id))
    conn.commit()
    return {"ok": True}




@router.patch("/api/conductores/{con_id}")
def upd_conductor(con_id: int, c: Conductor, conn = Depends(get_conn)):
    conn.execute("UPDATE conductores SET nombre=?, dni=?, telefono=?, email=? WHERE id=?",
                 (c.nombre, c.dni, c.telefono, c.email, con_id))
    conn.commit()
    return {"ok": True}




@router.patch("/api/direcciones/{did}")
def upd_direccion(did: int, d: DireccionMaestro, conn = Depends(get_conn)):
    conn.execute(
        "UPDATE direcciones SET nombre=?, empresa=?, calle=?, numero=?, ciudad=?, cp=?, pais=?, lat=?, lng=?, comentario=? WHERE id=?",
        (d.nombre, d.empresa, d.calle, d.numero, d.ciudad, d.cp, d.pais, d.lat, d.lng, d.comentario, did),
    )
    conn.commit()
    return {"ok": True}




@router.patch("/api/proveedores/{prov_id}")
def upd_proveedor(prov_id: int, body: dict, conn = Depends(get_conn)):
    allow = ("nombre", "cif", "direccion", "poblacion", "cp", "telefono", "email", "cuenta_contable_defecto")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE proveedores SET {sets} WHERE id=?", (*fields.values(), prov_id))
    conn.commit()
    return {"ok": True}




@router.patch("/api/transportistas/{tid}")
def upd_transportista(tid: int, t: Transportista, conn = Depends(get_conn)):
    conn.execute("UPDATE transportistas SET nombre=?, cif=?, telefono=?, email=?, tarifa=? WHERE id=?",
                 (t.nombre, t.cif, t.telefono, t.email, t.tarifa, tid))
    conn.commit()
    return {"ok": True}


