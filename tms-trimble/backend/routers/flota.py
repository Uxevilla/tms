"""
Router de flota."""
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


@router.post("/api/mantenimientos")
def add_mantenimiento(m: Mantenimiento, conn = Depends(get_conn)):
    cur = conn.execute(
        "INSERT INTO flota.mantenimientos (vehiculo_id, tipo, fecha, fecha_fin, km, coste, notas, hecho, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?) RETURNING id",
        (m.vehiculo_id, m.tipo, m.fecha, m.fecha_fin, m.km, m.coste, m.notas, m.hecho,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    mid = cur.fetchone()["id"]
    # Integración contable: si se marca Completado y se pide generar gasto → gastos_vehiculos + asiento 622/472/400.
    if m.hecho and m.generar_gasto and m.base_imponible > 0:
        iva_pct = round(float(m.iva or 21), 2)
        importe = round(float(m.base_imponible) * (1 + iva_pct / 100.0), 2)
        cuota = round(importe - float(m.base_imponible), 2)
        concepto = (m.tipo or "Reparación").strip()
        gcur = conn.execute(
            "INSERT INTO finanzas.gastos_vehiculos (vehiculo_id, proveedor_id, fecha, tipo, litros, base_imponible, iva, "
            "importe_total, factura_ref, cuenta_contable_gasto, estado_pago, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (m.vehiculo_id, m.proveedor_id, m.fecha, "reparaciones", 0, m.base_imponible, iva_pct, importe,
             "", "622", "Pendiente", datetime.datetime.utcnow().isoformat() + "Z"),
        )
        gid = gcur.fetchone()["id"]
        lineas = [("622", float(m.base_imponible), 0, concepto)]
        if cuota > 0:
            lineas.append(("472", cuota, 0, "IVA soportado"))
        lineas.append(("400", 0, importe, "Proveedor"))
        try:
            _registrar_asiento((m.fecha or "")[:10], f"Gasto taller: {concepto}", lineas,
                               origen="Gasto_Vehiculo", origen_id=str(gid), conn=conn)
        except ValueError as e:
            conn.rollback()
            raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit()
    return {"ok": True, "id": mid}




@router.post("/api/vehiculos")
def add_vehiculo(v: Vehiculo, conn = Depends(get_conn)):
    coste = float(v.coste_adquisicion or 0)
    # La única referencia del vehículo es la matrícula; el código interno se deriva de ella
    # (la casilla "Código interno" ya no existe en el formulario de alta).
    codigo = (v.id or v.matricula).strip() or ("VH-" + uuid.uuid4().hex[:8].upper())
    # Compra (coste>0) o renting/leasing exigen proveedor vinculado.
    if (v.tipo_tenencia in ("Renting", "Leasing") or coste > 0) and not v.proveedor_id:
        raise HTTPException(status_code=400, detail={"error": "Indica el proveedor (proveedor_id) para este vehículo."})
    conn.execute(
        "INSERT INTO flota.vehiculos (codigo, terminal_trimble, categoria, matricula, marca, modelo, anno, itv, seguro, peaje_categoria, "
        "ptv_profile, ejes, mma, clase_euro, capacidad_peso, capacidad_palets, "
        "coste_adquisicion, fecha_adquisicion, vida_util, valor_residual, "
        "fecha_caducidad_itv, seguro_compania, fecha_caducidad_seguro, tipo_tenencia, proveedor_id, fecha_alta, cuota_mensual, app_terminal) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (codigo) DO UPDATE SET terminal_trimble=EXCLUDED.terminal_trimble, categoria=EXCLUDED.categoria, matricula=EXCLUDED.matricula, marca=EXCLUDED.marca, "
        "modelo=EXCLUDED.modelo, anno=EXCLUDED.anno, itv=EXCLUDED.itv, seguro=EXCLUDED.seguro, "
        "peaje_categoria=EXCLUDED.peaje_categoria, ptv_profile=EXCLUDED.ptv_profile, "
        "ejes=EXCLUDED.ejes, mma=EXCLUDED.mma, clase_euro=EXCLUDED.clase_euro, "
        "capacidad_peso=EXCLUDED.capacidad_peso, capacidad_palets=EXCLUDED.capacidad_palets, "
        "coste_adquisicion=EXCLUDED.coste_adquisicion, fecha_adquisicion=EXCLUDED.fecha_adquisicion, "
        "vida_util=EXCLUDED.vida_util, valor_residual=EXCLUDED.valor_residual, "
        "fecha_caducidad_itv=EXCLUDED.fecha_caducidad_itv, seguro_compania=EXCLUDED.seguro_compania, "
        "fecha_caducidad_seguro=EXCLUDED.fecha_caducidad_seguro, tipo_tenencia=EXCLUDED.tipo_tenencia, "
        "proveedor_id=EXCLUDED.proveedor_id, fecha_alta=EXCLUDED.fecha_alta, cuota_mensual=EXCLUDED.cuota_mensual, app_terminal=EXCLUDED.app_terminal",
        (codigo, v.terminal_trimble, v.categoria, v.matricula, v.marca, v.modelo, v.anno, v.itv, v.seguro, v.peaje_categoria,
         v.ptv_profile, v.ejes, v.mma, v.clase_euro, v.capacidad_peso, v.capacidad_palets,
         v.coste_adquisicion, v.fecha_adquisicion, v.vida_util, v.valor_residual,
         v.fecha_caducidad_itv, v.seguro_compania, v.fecha_caducidad_seguro, v.tipo_tenencia, v.proveedor_id, v.fecha_alta, v.cuota_mensual, v.app_terminal),
    )
    # Asiento de adquisición (solo compra en Propiedad): Debe 218 / Haber 400, una sola vez.
    if coste > 0 and v.proveedor_id:
        ya = conn.execute("SELECT id FROM finanzas.asientos WHERE origen='Compra_Vehiculo' AND origen_id=?", (codigo,)).fetchone()
        if not ya:
            fecha = v.fecha_adquisicion or datetime.date.today().isoformat()
            try:
                _registrar_asiento(
                    fecha, f"Adquisición vehículo {codigo}",
                    [("218", round(coste, 2), 0, "Elementos de transporte"),
                     ("400", 0, round(coste, 2), "Proveedor de inmovilizado")],
                    origen="Compra_Vehiculo", origen_id=codigo, conn=conn,
                )
            except ValueError as exc:
                conn.rollback()
                raise HTTPException(status_code=400, detail={"error": str(exc)})
    conn.commit()
    return {"ok": True}




@router.post("/api/vehiculos/{veh_id}/documentos")
def add_vehiculo_documentos(veh_id: str, req: dict, conn = Depends(get_conn)):
    """Guarda los PDF del vehículo (base64), con source='vehiculo'."""
    docs = req.get("documentos") or []
    if not docs:
        return {"ok": True, "guardados": 0}
    if not conn.execute("SELECT id FROM vehiculos WHERE id=?", (veh_id,)).fetchone():
        raise HTTPException(status_code=404, detail={"error": "Vehículo no encontrado."})
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
            "INSERT INTO files (vehiculo_id, name, ftype, ftime, source, formato, storage_key, sha256, bytes) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT (name) DO NOTHING",
            (veh_id, name, 3, datetime.datetime.utcnow().isoformat() + "Z", "vehiculo", "pdf", storage_key, sha, nbytes),
        )
        guardados += 1
    conn.commit()
    return {"ok": True, "guardados": guardados}




@router.delete("/api/vehiculos/{veh_id}")
def del_vehiculo(veh_id: str, conn = Depends(get_conn)):
    conn.execute("DELETE FROM vehiculos WHERE id=?", (veh_id,))
    conn.commit()
    return {"ok": True}




@router.delete("/api/vehiculos/{veh_id}/documentos/{file_id}")
def del_vehiculo_documento(veh_id: str, file_id: int, conn = Depends(get_conn)):
    row = conn.execute(
        "SELECT storage_key FROM files WHERE id=? AND vehiculo_id=? AND source='vehiculo'", (file_id, veh_id)
    ).fetchone()
    conn.execute("DELETE FROM files WHERE id=? AND vehiculo_id=? AND source='vehiculo'", (file_id, veh_id))
    conn.commit()
    if row and row["storage_key"]:
        refs = conn.execute(
            "SELECT 1 FROM files WHERE storage_key=? AND id<>?", (row["storage_key"], file_id)
        ).fetchone()
        if not refs:
            _borrar_archivo(row["storage_key"])
    return {"ok": True}




@router.delete("/api/mantenimientos/{mid}")
def delete_mantenimiento(mid: int, conn = Depends(get_conn)):
    conn.execute("DELETE FROM flota.mantenimientos WHERE id=?", (mid,))
    conn.commit()
    return {"ok": True}






@router.get("/api/drivers")
def drivers():
    """Lista de conductores del cliente."""
    ds = get_client().list_drivers()
    result = []
    for d in ds:
        nombre = f"{d['firstName']} {d['lastName']}".strip()
        if not nombre:
            nombre = d.get("id") or "(sin nombre)"
        result.append({"id": d.get("id") or "", "nombre": nombre})
    result.sort(key=lambda d: d["nombre"].lower())
    return {"conductores": result}




@router.get("/api/alertas")
def list_alertas(estado: str = "", conn = Depends(get_conn)):
    base = (
        "SELECT a.id, a.vehiculo_id, a.codigo, a.severidad, a.mensaje, a.estado, a.creado_en, "
        "i.reporte_id, v.matricula, v.marca, v.modelo "
        "FROM alertas_mantenimiento a "
        "LEFT JOIN inspecciones i ON i.id = a.inspeccion_id "
        "LEFT JOIN vehiculos v ON v.id = a.vehiculo_id "
    )
    if estado:
        rows = conn.execute(base + " WHERE a.estado=? ORDER BY a.creado_en DESC", (estado,)).fetchall()
    else:
        rows = conn.execute(base + " ORDER BY a.creado_en DESC").fetchall()
    return {"alertas": [dict(r) for r in rows]}




@router.get("/api/documentos")
def list_documentos(conn = Depends(get_conn)):
    """Gestor documental global: unifica files (e-CMR, pedidos, vehículos) y facturas OCR de gastos."""
    rows = conn.execute(
        """
        SELECT f.id, 'files' AS origen, f.name AS nombre, f.source, f.formato, f.ftime AS fecha,
               f.content_b64, f.storage_key, f.trip_id, f.vehiculo_id, v.matricula, t.cliente
        FROM files f
        LEFT JOIN vehiculos v ON v.id = f.vehiculo_id
        LEFT JOIN trips t ON t.id = f.trip_id
        UNION ALL
        SELECT g.id, 'gastos', COALESCE(g.factura_ref,''), 'gasto', 'pdf', g.fecha,
               g.archivo_base64, g.storage_key, NULL, g.vehiculo_id, v.matricula, NULL
        FROM finanzas.gastos_vehiculos g
        LEFT JOIN vehiculos v ON v.id = g.vehiculo_id
        WHERE COALESCE(g.archivo_base64,'') <> '' OR COALESCE(g.storage_key,'') <> ''
        ORDER BY fecha DESC
        """,
    ).fetchall()
    out = []
    for r in rows:
        nombre = r["nombre"] or "documento.pdf"
        if "__" in nombre:
            nombre = nombre.split("__", 1)[1]
        src = r["source"] or ""
        # Clasificación real: la columna `source` de files guarda el ID de terminal/device en
        # los docs del DMS de Trimble (POD/signoff/DOC_CARGA), no el tipo. Se clasifica por contexto.
        if r["origen"] == "gastos":
            modulo = "Gastos"
        elif src == "vehiculo":
            modulo = "Vehículos"
        else:
            modulo = "Operaciones"
        ref = r["matricula"] or r["trip_id"] or ""
        formato = (r["formato"] or "").upper()
        if not formato:
            ext = nombre.rsplit(".", 1)[-1].upper() if "." in nombre else ""
            formato = ext or "PDF"
        out.append({
            "id": f"{r['origen']}:{r['id']}",
            "nombre": nombre,
            "modulo_origen": modulo,
            "referencia": ref or "—",
            "formato": formato,
            "fecha": (r["fecha"] or "")[:10],
            "content_b64": (_leer_archivo(r["storage_key"]) if r["storage_key"] else (r["content_b64"] or "")),
            "source": src,
        })
    return {"documentos": out}




@router.get("/api/mantenimientos")
def list_mantenimientos(vehiculo_id: str = "", conn = Depends(get_conn)):
    if vehiculo_id:
        rows = conn.execute(
            "SELECT m.*, v.matricula, v.categoria FROM flota.mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id "
            "WHERE m.vehiculo_id=? ORDER BY m.fecha", (vehiculo_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT m.*, v.matricula, v.categoria FROM flota.mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id ORDER BY m.fecha"
        ).fetchall()
    return {"mantenimientos": [dict(r) for r in rows]}




@router.get("/api/tarifas-peaje")
def list_tarifas_peaje(conn = Depends(get_conn)):
    rows = conn.execute("SELECT categoria, eur_km FROM tarifas_peaje ORDER BY eur_km").fetchall()
    labels = {k: v["label"] for k, v in _PEAJE_CATEGORIAS.items()}
    return {
        "tarifas": [
            {"categoria": r["categoria"], "label": labels.get(r["categoria"], r["categoria"]),
             "eur_km": float(r["eur_km"] or 0)}
            for r in rows
        ]
    }




@router.get("/api/vehiculos/{veh_id}/documentos")
def list_vehiculo_documentos(veh_id: str, conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, name, content_b64, storage_key FROM files WHERE vehiculo_id=? AND source='vehiculo' ORDER BY id", (veh_id,)
    ).fetchall()
    docs = []
    for r in rows:
        c = _leer_archivo(r["storage_key"]) if r["storage_key"] else (r["content_b64"] or "")
        nombre = r["name"].split("__", 1)[1] if "__" in r["name"] else r["name"]
        docs.append({"id": r["id"], "nombre": nombre, "contenido": c, "size": round(len(c) * 3 / 4)})
    return {"documentos": docs}




@router.get("/api/vehiculos")
def list_vehiculos(categoria: str = "", conn = Depends(get_conn)):
    if categoria:
        rows = conn.execute("SELECT * FROM vehiculos WHERE categoria=? ORDER BY id", (categoria,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vehiculos ORDER BY id").fetchall()
    activos = _vehiculos_en_curso()
    return {"vehiculos": [{**dict(r), "disponible": r["id"] not in activos} for r in rows]}




@router.get("/api/vehiculos/disponibles")
def list_vehiculos_disponibles(fecha_esperada_carga: str = "", categoria: str = "", conn = Depends(get_conn)):
    """Vehículos con disponibilidad para una fecha de carga: bloquea si tiene mantenimiento solapado o viaje en curso."""
    fecha = (fecha_esperada_carga or "")[:10]
    en_curso = _vehiculos_en_curso()
    conds, params = [], []
    if categoria:
        conds.append("v.categoria=?")
        params.append(categoria)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    if fecha:
        rows = conn.execute(
            f"""
            WITH man AS (
                SELECT DISTINCT ON (vehiculo_id) vehiculo_id, tipo AS tipo_man
                FROM flota.mantenimientos
                WHERE ? >= fecha AND ? <= COALESCE(NULLIF(fecha_fin,''), fecha)
                  AND COALESCE(hecho, false) = false
                ORDER BY vehiculo_id, fecha
            )
            SELECT v.*, man.tipo_man
            FROM vehiculos v
            LEFT JOIN man ON man.vehiculo_id = v.id
            {where}
            ORDER BY v.id
            """,
            (fecha, fecha, *params),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            tipo_man = d.pop("tipo_man", None)
            ocupado = r["id"] in en_curso
            d["disponible"] = (tipo_man is None) and (not ocupado)
            d["motivo_bloqueo"] = tipo_man if tipo_man else ("En viaje" if ocupado else None)
            out.append(d)
    else:
        rows = conn.execute(
            f"SELECT v.* FROM vehiculos v {where} ORDER BY v.id", params,
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            ocupado = r["id"] in en_curso
            d["disponible"] = not ocupado
            d["motivo_bloqueo"] = "En viaje" if ocupado else None
            out.append(d)
    return {"vehiculos": out}




@router.get("/api/mantenimiento/alertas")
def mantenimiento_alertas(user: dict = Depends(require_role(["admin", "dispatcher"])),
                          estado: str = "abierta"):
    """Consulta las alertas (tabla pública unificada: inspecciones + predictivas) por estado."""
    conn = _db()
    rows = conn.execute(
        "SELECT a.id, a.vehiculo_id, a.codigo, a.severidad, a.mensaje, a.estado, a.creado_en, "
        "v.matricula, v.marca, v.modelo, v.categoria "
        "FROM alertas_mantenimiento a "
        "LEFT JOIN vehiculos v ON v.id = a.vehiculo_id "
        "WHERE a.estado = ? ORDER BY a.creado_en DESC",
        (estado,),
    ).fetchall()
    conn.close()
    return {"alertas": [dict(r) for r in rows]}




@router.post("/api/mantenimiento/convertir/{alerta_id}")
def mantenimiento_convertir(alerta_id: int,
                            user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Convierte una alerta (tabla pública) en orden de taller y la resuelve."""
    conn = _db()
    row = conn.execute("SELECT * FROM alertas_mantenimiento WHERE id=?", (alerta_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Alerta no encontrada"})
    tipo = (row["codigo"] or "").replace("MANT-", "") or "Mantenimiento"
    reg = conn.execute(
        "SELECT tipo_mantenimiento FROM flota.reglas_mantenimiento WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?) LIMIT 1",
        (row["vehiculo_id"], tipo),
    ).fetchone()
    if reg:
        tipo = reg["tipo_mantenimiento"]
    km = conn.execute("SELECT km_actuales FROM vehiculos WHERE id=?", (row["vehiculo_id"],)).fetchone()
    km_val = km["km_actuales"] if km and km["km_actuales"] else 0
    hoy = datetime.date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO flota.mantenimientos (vehiculo_id, tipo, fecha, km, coste, notas, hecho, creado) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        (row["vehiculo_id"], tipo, hoy, km_val, 0,
         "Convertida desde alerta preventiva", False,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    mid = cur.fetchone()["id"]
    conn.execute("UPDATE alertas_mantenimiento SET estado='resuelta' WHERE id=?", (alerta_id,))
    conn.execute(
        "UPDATE flota.reglas_mantenimiento SET ultimo_km_realizado=? WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?)",
        (km_val, row["vehiculo_id"], tipo),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "mantenimiento_id": mid}






@router.post("/api/mantenimiento/resolver/{alerta_id}")
def mantenimiento_resolver(alerta_id: int,
                           user: dict = Depends(require_role(["admin", "dispatcher"]))):
    """Marca la alerta (tabla pública) como Resuelta y actualiza ultimo_km_realizado de su regla."""
    conn = _db()
    row = conn.execute("SELECT * FROM alertas_mantenimiento WHERE id=?", (alerta_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Alerta no encontrada"})
    conn.execute("UPDATE alertas_mantenimiento SET estado='resuelta' WHERE id=?", (alerta_id,))
    tipo = (row["codigo"] or "").replace("MANT-", "")
    reg = conn.execute(
        "SELECT tipo_mantenimiento FROM flota.reglas_mantenimiento WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?) LIMIT 1",
        (row["vehiculo_id"], tipo),
    ).fetchone()
    if reg:
        tipo = reg["tipo_mantenimiento"]
    km = conn.execute("SELECT km_actuales FROM vehiculos WHERE id=?", (row["vehiculo_id"],)).fetchone()
    conn.execute(
        "UPDATE flota.reglas_mantenimiento SET ultimo_km_realizado=? WHERE vehiculo_id=? AND LOWER(tipo_mantenimiento)=LOWER(?)",
        ((km["km_actuales"] if km and km["km_actuales"] else 0), row["vehiculo_id"], tipo),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "alerta_id": alerta_id}




@router.patch("/api/documentos/{doc_id}/renombrar")
def renombrar_documento(doc_id: str, body: dict, conn = Depends(get_conn)):
    """Renombra un documento (files.name o gastos_vehiculos.factura_ref)."""
    nuevo = (body.get("nombre") or "").strip()[:120]
    if not nuevo:
        return {"ok": False, "error": "Nombre vacío"}
    if doc_id.startswith("files:"):
        fid = int(doc_id.split(":", 1)[1])
        row = conn.execute("SELECT name FROM files WHERE id=?", (fid,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail={"error": "No encontrado"})
        prefijo = row["name"].split("__", 1)[0] + "__" if "__" in row["name"] else ""
        conn.execute("UPDATE files SET name=? WHERE id=?", (prefijo + nuevo, fid))
    elif doc_id.startswith("gastos:"):
        gid = int(doc_id.split(":", 1)[1])
        conn.execute("UPDATE finanzas.gastos_vehiculos SET factura_ref=? WHERE id=?", (nuevo, gid))
    else:
        raise HTTPException(status_code=400, detail={"error": "ID inválido"})
    conn.commit()
    return {"ok": True}






@router.post("/api/tarifas-peaje")
def set_tarifa_peaje(t: TarifaPeaje, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO tarifas_peaje (categoria, eur_km) VALUES (?,?) "
        "ON CONFLICT (categoria) DO UPDATE SET eur_km=EXCLUDED.eur_km",
        (t.categoria, t.eur_km),
    )
    conn.commit()
    return {"ok": True}




@router.get("/api/tacografo/{terminal}/dstat")
def tacografo_dstat(terminal: str):
    """Estadísticas de conducción (DSTAT, traza 82) del conductor logueado en el terminal."""
    stats = _dstat_terminal(terminal.strip())
    if not stats:
        return {"ok": False, "terminal": terminal, "error": "Sin datos de tacógrafo para este terminal."}
    limite_diario = _EXT_DIA_CONDUCCION_MIN if stats["week_long_driving_count"] < 2 else _MAX_DIA_CONDUCCION_MIN
    return {
        "ok": True,
        "terminal": terminal,
        "did": stats["did"],
        "driving_coupure_min": stats["driving_coupure"],
        "day_driving_min": stats["day_driving"],
        "week_driving_min": stats["week_driving"],
        "remaining_week_available_min": stats["remaining_week_available"],
        "week_long_driving_count": stats["week_long_driving_count"],
        "next_rest_due_ts": stats["next_rest_due_ts"],
        "next_rest_due": datetime.datetime.fromtimestamp(
            stats["next_rest_due_ts"], tz=datetime.timezone.utc
        ).strftime("%d/%m %H:%M") if stats["next_rest_due_ts"] else "",
        "conduccion_continua_restante_min": round(max(0.0, _MAX_CONDUCCION_CONTINUA_MIN - stats["driving_coupure"]), 1),
        "dia_restante_min": round(max(0.0, limite_diario - stats["day_driving"]), 1),
        "limite_diario_min": limite_diario,
    }




@router.get("/api/telemetria")
def telemetria(vehiculo: str = "", desde: str = "", hasta: str = "", limit: int = 500, conn = Depends(get_conn)):
    """Historial de telemetría (posiciones/velocidad/rumbo) de un vehículo (hypertable activa posiciones_gps)."""
    q = ("SELECT time, fuente AS source, vehiculo_id, lat, lng, speed_kmh AS speed, heading, odometer_km AS mileage "
         "FROM telemetria.posiciones_gps WHERE 1=1")
    params = []
    if vehiculo:
        q += " AND vehiculo_id=?"
        params.append(vehiculo)
    if desde:
        q += " AND time >= ?"
        params.append(desde)
    if hasta:
        q += " AND time <= ?"
        params.append(hasta)
    q += " ORDER BY time DESC LIMIT ?"
    params.append(min(int(limit), 5000))
    rows = conn.execute(q, params).fetchall()
    return {"puntos": [dict(r) for r in rows]}




@router.get("/api/terminals")
def terminals():
    """Lista de terminales (unidades) habilitados del cliente."""
    units = get_client().list_units()
    enabled = sorted(
        (u for u in units if u.get("enabled") and u.get("id")),
        key=lambda u: u["id"],
    )
    return {"terminales": [{"id": u["id"], "name": u["name"]} for u in enabled]}




@router.patch("/api/alertas/{aid}")
def upd_alerta(aid: int, body: AlertaUpdate, conn = Depends(get_conn)):
    if body.estado in ("resuelta", "descartada", "abierta"):
        conn.execute("UPDATE alertas_mantenimiento SET estado=? WHERE id=?", (body.estado, aid))
    conn.commit()
    return {"ok": True}








@router.patch("/api/mantenimientos/{mid}")
def upd_mantenimiento(mid: int, m: Optional[Mantenimiento] = None, conn = Depends(get_conn)):
    if m is None:
        conn.execute("UPDATE flota.mantenimientos SET hecho = NOT hecho WHERE id=?", (mid,))
    else:
        conn.execute(
            "UPDATE flota.mantenimientos SET vehiculo_id=?, tipo=?, fecha=?, km=?, coste=?, notas=?, hecho=? WHERE id=?",
            (m.vehiculo_id, m.tipo, m.fecha, m.km, m.coste, m.notas, m.hecho, mid),
        )
    conn.commit()
    return {"ok": True}




@router.patch("/api/mantenimientos/{mid}/campos")
def upd_mantenimiento_campos(mid: int, body: dict, conn = Depends(get_conn)):
    """Edición en línea parcial: estado (hecho), coste, km, fechas, tipo o notas."""
    allow = ("hecho", "coste", "km", "fecha", "fecha_fin", "notas", "tipo")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE flota.mantenimientos SET {sets} WHERE id=?", (*fields.values(), mid))
    conn.commit()
    return {"ok": True}




@router.patch("/api/vehiculos/{veh_id}")
def upd_vehiculo(veh_id: str, body: dict, conn = Depends(get_conn)):
    """Edita datos técnicos y costes fijos de un vehículo (ITV, seguro, costes...)."""
    allow = ("matricula", "terminal_trimble", "app_terminal", "itv", "seguro", "coste_adquisicion", "valor_residual", "vida_util",
             "clase_euro", "capacidad_peso", "capacidad_palets", "mma", "ejes",
             "fecha_caducidad_itv", "seguro_compania", "fecha_caducidad_seguro",
             "tipo_tenencia", "proveedor_id", "fecha_alta", "cuota_mensual", "fecha_proxima_revision")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    # La matrícula es la única referencia: el código interno (id de la vista) la sigue.
    if "matricula" in fields:
        fields["codigo"] = fields["matricula"]
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE vehiculos SET {sets} WHERE id=?", (*fields.values(), veh_id))
    conn.commit()
    return {"ok": True}




@router.get("/api/vehiculos/cercano")
def vehiculo_cercano(lat: float, lng: float, conn = Depends(get_conn)):
    """Devuelve la tractora libre más cercana al punto dado (por última posición conocida)."""
    activos = _vehiculos_en_curso()
    rows = conn.execute("SELECT id, last_lat, last_lng, matricula FROM vehiculos WHERE categoria='tractora'").fetchall()
    best = None
    for r in rows:
        if r["id"] in activos:
            continue
        if r["last_lat"] is None or r["last_lng"] is None:
            continue
        d = _haversine_km(lat, lng, r["last_lat"], r["last_lng"])
        if d is not None and (best is None or d < best["dist"]):
            best = {"id": r["id"], "matricula": r["matricula"], "dist": d}
    return {"cercano": best}




@router.get("/api/vehiculos/en-curso")
def vehiculos_en_curso():
    return {"en_curso": sorted(_vehiculos_en_curso())}




@router.get("/api/vehiculos/posiciones")
def vehiculos_posiciones(conn = Depends(get_conn)):
    """Última posición conocida de cada vehículo (de las trazas de Trimble)."""
    rows = conn.execute(
        "SELECT id, matricula, categoria, marca, modelo, last_lat, last_lng, last_position_time "
        "FROM vehiculos WHERE last_lat IS NOT NULL AND last_lng IS NOT NULL"
    ).fetchall()
    return {"vehiculos": [dict(r) for r in rows]}


