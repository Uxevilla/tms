"""
Router de empresas."""
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
from services.configuracion import _sync_valor_integracion
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


@router.post("/api/empresas")
def create_empresa(req: dict):
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    slug = (req.get("slug") or "").strip().lower()
    nombre = (req.get("nombre") or "").strip()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail={"error": "Identificador no válido (2-32 chars: minúsculas, números, guiones)"})
    if slug in ("admin", "_admin", "master", "postgres", "tms"):
        raise HTTPException(status_code=400, detail={"error": "Identificador reservado"})
    if not nombre:
        raise HTTPException(status_code=400, detail={"error": "Indica el nombre de la empresa"})
    auth_users = (req.get("auth_users") or "").strip()
    auth_password = req.get("auth_password") or req.get("contraseña") or ""
    seed = {"auth_users": auth_users, "auth_password": auth_password}
    db_name, err = _provision_tenant(slug, nombre, seed)
    if err:
        raise HTTPException(status_code=500, detail={"error": f"No se pudo crear la base de datos: {err}"})
    return {"ok": True, "slug": slug, "nombre": nombre, "db_name": db_name}




@router.delete("/api/empresas/{slug}")
def delete_empresa(slug: str):
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    conn = _db_master()
    db_name = None
    try:
        row = conn.execute("SELECT slug, db_name FROM empresas WHERE slug=?", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "Empresa no encontrada"})
        db_name = row["db_name"]
        if db_name == config.DB_NAME:
            raise HTTPException(status_code=400, detail={"error": "No se puede borrar la empresa principal"})
        conn.execute("DELETE FROM empresas WHERE slug=?", (slug,))
        conn.commit()
    finally:
        conn.close()
    # soltar la base de datos (fuera de transacción)
    if db_name:
        try:
            c = psycopg2.connect(host=config.DB_HOST, port=config.DB_PORT, dbname=config.DB_NAME,
                                 user=config.DB_USER, password=config.DB_PASSWORD)
            c.autocommit = True
            cur = c.cursor()
            cur.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            c.close()
        except Exception as e:
            return {"ok": True, "slug": slug, "aviso": f"Registro eliminado pero no se pudo soltar la BD: {e}"}
    return {"ok": True, "slug": slug, "db_name": db_name}




@router.post("/api/empresas/{slug}/entrar")
def entrar_empresa(slug: str):
    """Superadmin entra a una empresa: genera un token de tenant sin pedir contraseña."""
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    emp = _empresa_por_slug(slug)
    if not emp:
        raise HTTPException(status_code=404, detail={"error": "Empresa no encontrada"})
    return {"token": _make_jwt(0, "admin", "admin", emp["slug"]),
            "empresa": emp["slug"], "nombre": emp["nombre"],
            "usuario": "admin", "superadmin": False}




@router.get("/api/config")
def get_config(conn = Depends(get_conn)):
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    out = {}
    for r in rows:
        k, v = r["key"], r["value"]
        kl = k.lower()
        if any(s in kl for s in ("password", "api_key", "token", "secret", "clave", "contraseña")):
            v = "••••••••" if v else ""
        out[k] = v
    return {"config": out}




@router.get("/api/empresa")
def get_empresa(conn = Depends(get_conn)):
    row = conn.execute("SELECT * FROM empresa WHERE id=1").fetchone()
    return {"empresa": dict(row) if row else {}}




@router.get("/api/health")
def health():
    return {
        "ok": True,
        "terminal": _get_config("trimble_terminal", config.DEFAULT_TRIMBLE_TERMINAL),
        "customer": _get_config("trimble_customer", config.DEFAULT_TRIMBLE_CUSTOMER),
    }




@router.get("/api/empresas")
def list_empresas():
    if not _es_superadmin():
        raise HTTPException(status_code=403, detail={"error": "Solo administrador"})
    conn = _db_master()
    try:
        rows = conn.execute("SELECT slug, nombre, db_name, creado FROM empresas ORDER BY slug").fetchall()
        return {"empresas": [dict(r) for r in rows]}
    finally:
        conn.close()




@router.post("/api/config")
def set_config(req: dict, conn = Depends(get_conn)):
    for k, v in req.items():
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (k, str(v)),
        )
        # dual-write: si es una clave de integración, replicar a integracion_valores
        _sync_valor_integracion(conn, k, str(v))
    conn.commit()
    # Invalidar clientes cacheados cuando cambian sus credenciales
    if any(k.startswith("trimble_") for k in req.keys()):
        _client_cache.clear()
    if any(k.startswith("transfollow_") for k in req.keys()):
        _tf_cache.clear()
    return {"ok": True}




@router.post("/api/empresa")
def set_empresa(e: Empresa, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO empresa (id, nombre, cif, direccion, poblacion, cp, pais, telefono, email, web, iva, iban) "
        "VALUES (1,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (id) DO UPDATE SET nombre=EXCLUDED.nombre, cif=EXCLUDED.cif, direccion=EXCLUDED.direccion, "
        "poblacion=EXCLUDED.poblacion, cp=EXCLUDED.cp, pais=EXCLUDED.pais, telefono=EXCLUDED.telefono, "
        "email=EXCLUDED.email, web=EXCLUDED.web, iva=EXCLUDED.iva, iban=EXCLUDED.iban",
        (e.nombre, e.cif, e.direccion, e.poblacion, e.cp, e.pais, e.telefono, e.email, e.web, e.iva, e.iban),
    )
    conn.commit()
    return {"ok": True}




