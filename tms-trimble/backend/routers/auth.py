"""
Router de auth."""
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


@router.post("/api/auth/login")
def auth_login(req: dict, request: Request):
    """Login del frontend React: valida contra config.usuarios (bcrypt) y devuelve JWT.

    Acepta `empresa` (slug) opcional para resolver el tenant; sin slug usa el tenant por defecto.
    """
    usuario = (req.get("usuario") or "").strip()
    contrasena = req.get("contrasena") or req.get("password") or ""
    empresa = (req.get("empresa") or "").strip().lower()
    ip = request.client.host if request.client else "?"
    if not _login_rate_ok(f"{ip}:{usuario}"):
        raise HTTPException(status_code=429, detail={"error": "Demasiados intentos de login. Espera unos minutos."})
    token_ctx = None
    if empresa:
        emp = _empresa_por_slug(empresa)
        if not emp:
            raise HTTPException(status_code=401, detail={"error": "Empresa no encontrada"})
        token_ctx = _tenant_ctx.set({"db_name": emp["db_name"], "empresa": emp["slug"],
                                     "nombre": emp["nombre"], "superadmin": False})
    try:
        conn = _db()
        try:
            row = conn.execute(
                "SELECT id, usuario, password_hash, rol, activo, COALESCE(debe_cambiar_clave, false) AS debe_cambiar_clave "
                "FROM config.usuarios WHERE usuario=?",
                (usuario,),
            ).fetchone()
        finally:
            conn.close()
    finally:
        if token_ctx is not None:
            _tenant_ctx.reset(token_ctx)
    if not row or not row["activo"] or not _verify_password(contrasena, row["password_hash"]):
        raise HTTPException(status_code=401, detail={"error": "Usuario o contraseña incorrectos"})
    return {"token": _make_jwt(row["id"], row["usuario"], row["rol"], empresa),
            "usuario": row["usuario"], "rol": row["rol"], "id": row["id"],
            "empresa": empresa,
            "debe_cambiar_clave": bool(row["debe_cambiar_clave"])}




@router.post("/api/auth/superadmin")
def auth_superadmin(req: dict, request: Request):
    """Login del superadmin (gestión de empresas) → JWT con rol 'superadmin'."""
    usuario = (req.get("usuario") or "").strip()
    contrasena = req.get("contrasena") or req.get("password") or ""
    ip = request.client.host if request.client else "?"
    if not _login_rate_ok(f"{ip}:sa:{usuario}"):
        raise HTTPException(status_code=429, detail={"error": "Demasiados intentos de login. Espera unos minutos."})
    if usuario != config.SUPERADMIN_USER or not secrets.compare_digest(config.SUPERADMIN_PASSWORD or "", contrasena):
        raise HTTPException(status_code=401, detail={"error": "Credenciales de administrador incorrectas"})
    return {"token": _make_jwt(0, usuario, "superadmin"),
            "superadmin": True, "usuario": usuario,
            "empresa": "admin", "nombre": "Administración"}




@router.post("/api/auth/change-password")
def change_password(req: dict, user: dict = Depends(require_jwt)):
    """Cambia la contraseña del usuario autenticado y limpia el flag de cambio forzado."""
    antigua = (req.get("old_password") or "").strip()
    nueva = (req.get("new_password") or "").strip()
    if len(nueva) < 12:
        raise HTTPException(status_code=400, detail={"error": "La nueva contraseña debe tener al menos 12 caracteres."})
    conn = _db()
    try:
        row = conn.execute("SELECT password_hash FROM config.usuarios WHERE usuario=?", (user["usuario"],)).fetchone()
        if not row or not _verify_password(antigua, row["password_hash"]):
            raise HTTPException(status_code=401, detail={"error": "Contraseña actual incorrecta."})
        conn.execute("UPDATE config.usuarios SET password_hash=?, debe_cambiar_clave=false WHERE usuario=?",
                     (_hash_password(nueva), user["usuario"]))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


