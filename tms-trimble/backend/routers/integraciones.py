"""
Router de integraciones."""
import base64, csv, datetime, io, json, math, os, re, secrets, subprocess, tempfile, time, threading, urllib.parse, urllib.request, uuid
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import psycopg2
import redis
import config
import asyncio

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
from transfollow_client import build_waybill
from clients.ptv import _ptv_route, _calc_ruta, _haversine_km
from clients.geocoding import _buscar_photon, reverse_geocode
from services.contabilidad import _categoria_cuenta, _next_referencia, _auditar, _post_asiento, _registrar_asiento, _gasto_subcontrata, _facturar_viaje, _norm_fecha, _norm_total
from services.viajes import _vehiculo_ptv, _save_trip, _save_tramos, _save_paradas, _upsert_direccion, _peaje_rate, _vehiculo_peaje_categoria, _vehiculos_en_curso, _puntos_del_viaje, _build_trip, _calcular_ruta, _viaje_payload, _guardar_documentos_pedido, _valorar_viaje, _crear_pedido, _enviar_viaje
from services.telemetria import _get_redis, _set_viaje_activo, _del_viaje_activo, _json_safe, _viajes_snapshot
from services.sync import _query_terminal_states, _sync_status, _get_sync_state, _set_sync_state, _parse_props, _save_file, _extraer_reporte_xml, _extraer_documento_ecmr, _guardar_documento_entrega, _publicar_estado, _odometro_vehiculo, _aplicar_estado_viaje, _cerrar_viaje, _cerrar_viaje_por_ecmr, _entrega_confirmada, _sync_files, _sync_mensajes
from services.tacografo import _decode_dstat, _ingestar_dstat, _dstat_terminal, _chequear_conduccion_legal
from services.rrhh import _imputar_dieta_nomina, _procesar_dieta, _sync_conductor
from services.mantenimiento import _insertar_alerta_publica, _revisar_caducidades, _revisar_revision_fecha, _revisar_mantenimiento
from services.mensajeria import _save_mensaje, _store_mensaje, _extraer_pales, _procesar_pales, _webhook_autenticado, _direccion_dict
from services.ocr import _parse_ticket, _parse_documento, _pdf_a_texto, _regex_matricula, _regex_litros, _regex_importe, _regex_fecha
from services.empresa import _empresa

router = APIRouter()


@router.get("/api/activity-types")
def activity_types():
    return {"actividades": ACTIVITY_TYPES}




@router.post("/api/ruta")
def calcular_ruta(req: RutaRequest):
    """Km estimados (OSRM) + ruta completa PTV (distancia, tiempo, tráfico, peaje, polyline)."""
    puntos = [p.dict() for p in req.puntos]
    tramos, total, metodo, toll_km = _calc_ruta(puntos)
    resp = {"tramos": tramos, "total_km": total, "metodo": metodo, "total_toll_km": toll_km}
    if len(puntos) >= 2:
        ptv = _ptv_route(puntos, _vehiculo_ptv(req.terminal), req.conduccion_acumulada_min)
        if ptv:
            resp["ptv"] = ptv
    return resp






@router.post("/api/ecmr/crear")
def crear_ecmr(req: dict):
    """Crea un e-CMR (Freight Document) en TransFollow y devuelve su freightDocumentId."""
    carrier_email = (req.get("carrier_email") or "").strip()
    if not carrier_email:
        raise HTTPException(status_code=400, detail={"error": "Indica el email del transportista (carrier)"})
    payload = build_waybill(
        req.get("trip") or {},
        carrier_email=carrier_email,
        consignor=req.get("consignor") or {},
        consignee=req.get("consignee") or {},
        goods=req.get("goods") or [],
    )
    r = get_transfollow_client().create_freight_document(payload)
    if not r.get("ok"):
        detail = r.get("description") or r.get("error") or "error desconocido"
        raise HTTPException(status_code=502, detail={"error": f"TransFollow: {detail}"})
    fd_id = r.get("freightDocumentId")
    # Persistir el vínculo freightDocumentId -> viaje para el webhook de cierre.
    trip_id = (req.get("trip") or {}).get("id")
    if trip_id and fd_id:
        conn = _db()
        conn.execute("UPDATE trips SET ecmr_id=? WHERE id=?", (fd_id, trip_id))
        conn.commit()
        conn.close()
    return {"ok": True, "freightDocumentId": fd_id}




@router.get("/api/export")
def export_xlsx(tipo: str = "trips", conn = Depends(get_conn)):
    """Descarga Excel (.xlsx) del histórico (trips), gastos o ingresos."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = (tipo or "hoja")[:28]

    HEADER_FONT = Font(bold=True, color="FFFFFF")
    HEADER_FILL = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")

    def _write(headers, data):
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = Alignment(horizontal="center")
        for r_idx, row in enumerate(data, 2):
            for c_idx, val in enumerate(row, 1):
                ws.cell(row=r_idx, column=c_idx, value=val)
        ws.freeze_panes = "A2"

    if tipo == "ingresos":
        rows = conn.execute(
            "SELECT terminal, cliente, precio, gastos, km_total, estado_pago, factura, creado "
            "FROM trips ORDER BY creado DESC"
        ).fetchall()
        data = []
        for r in rows:
            p = float(r["precio"] or 0)
            g = float(r["gastos"] or 0)
            data.append([r["terminal"], r["cliente"], p, g, round(p - g, 2),
                         r["km_total"], r["estado_pago"], r["factura"], r["creado"]])
        _write(["Vehículo", "Cliente", "Precio (€)", "Gastos (€)", "Margen (€)", "Km", "Cobro", "Factura", "Creado"], data)
    elif tipo == "gastos":
        rows = conn.execute(
            "SELECT g.fecha, g.terminal, g.categoria, p.nombre AS proveedor, p.cif AS proveedor_cif, "
            "g.concepto, g.importe, g.pagado FROM finanzas.gastos g "
            "LEFT JOIN proveedores p ON p.id = g.proveedor_id ORDER BY g.fecha DESC"
        ).fetchall()
        data = [[r["fecha"], r["terminal"], r["categoria"],
                 (r["proveedor"] or "") + ((" (" + r["proveedor_cif"] + ")") if r["proveedor_cif"] else ""),
                 r["concepto"], float(r["importe"] or 0), "Sí" if r["pagado"] else "No"] for r in rows]
        _write(["Fecha", "Vehículo", "Categoría", "Proveedor", "Concepto", "Importe (€)", "Pagado"], data)
    elif tipo == "clientes":
        rows = conn.execute("SELECT nombre, cif, direccion, poblacion, cp, telefono, email FROM clientes ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Dirección", "Población", "CP", "Teléfono", "Email"],
               [[r["nombre"], r["cif"], r["direccion"], r["poblacion"], r["cp"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "conductores":
        rows = conn.execute("SELECT nombre, dni, telefono, email FROM conductores ORDER BY nombre").fetchall()
        _write(["Nombre", "DNI", "Teléfono", "Email"],
               [[r["nombre"], r["dni"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "vehiculos":
        rows = conn.execute(
            "SELECT categoria, id, matricula, marca, modelo, anno, itv, seguro, peaje_categoria, ejes, mma, "
            "clase_euro, capacidad_peso, capacidad_palets, coste_adquisicion, fecha_adquisicion, vida_util, valor_residual "
            "FROM vehiculos ORDER BY categoria, id"
        ).fetchall()
        _write(["Categoría", "ID", "Matrícula", "Marca", "Modelo", "Año", "ITV", "Seguro", "Peaje", "Ejes", "MMA", "Euro",
                "Cap. peso", "Cap. palets", "Coste", "Fecha adq.", "Vida útil", "Residual"],
               [[r["categoria"], r["id"], r["matricula"], r["marca"], r["modelo"], r["anno"], r["itv"], r["seguro"],
                 r["peaje_categoria"], r["ejes"], r["mma"], r["clase_euro"], r["capacidad_peso"], r["capacidad_palets"],
                 r["coste_adquisicion"], r["fecha_adquisicion"], r["vida_util"], r["valor_residual"]] for r in rows])
    elif tipo == "proveedores":
        rows = conn.execute("SELECT nombre, cif, direccion, poblacion, cp, telefono, email FROM proveedores ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Dirección", "Población", "CP", "Teléfono", "Email"],
               [[r["nombre"], r["cif"], r["direccion"], r["poblacion"], r["cp"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "transportistas":
        rows = conn.execute("SELECT nombre, cif, telefono, email, tarifa FROM transportistas ORDER BY nombre").fetchall()
        _write(["Nombre", "CIF", "Teléfono", "Email", "Tarifa €/km"],
               [[r["nombre"], r["cif"], r["telefono"], r["email"], float(r["tarifa"] or 0)] for r in rows])
    elif tipo == "categorias_gasto":
        rows = conn.execute("SELECT nombre FROM categorias_gasto ORDER BY nombre").fetchall()
        _write(["Categoría"], [[r["nombre"]] for r in rows])
    elif tipo == "tarifas_peaje":
        rows = conn.execute("SELECT categoria, eur_km FROM tarifas_peaje ORDER BY categoria").fetchall()
        _write(["Categoría", "€/km"], [[r["categoria"], float(r["eur_km"] or 0)] for r in rows])
    elif tipo == "costes_fijos":
        rows = conn.execute("SELECT terminal, concepto, importe FROM finanzas.costes_fijos ORDER BY terminal").fetchall()
        _write(["Vehículo", "Concepto", "€/mes"], [[r["terminal"], r["concepto"], float(r["importe"] or 0)] for r in rows])
    elif tipo == "mantenimientos":
        rows = conn.execute(
            "SELECT m.fecha, v.matricula, m.tipo, m.km, m.coste, m.notas, m.hecho "
            "FROM flota.mantenimientos m LEFT JOIN vehiculos v ON v.id=m.vehiculo_id ORDER BY m.fecha DESC"
        ).fetchall()
        _write(["Fecha", "Vehículo", "Tipo", "Km", "Coste (€)", "Notas", "Hecho"],
               [[r["fecha"], r["matricula"], r["tipo"], r["km"], float(r["coste"] or 0), r["notas"],
                 "Sí" if r["hecho"] else "No"] for r in rows])
    elif tipo == "direcciones":
        rows = conn.execute(
            "SELECT nombre, empresa, calle, numero, ciudad, cp, pais, lat, lng, comentario "
            "FROM direcciones ORDER BY ciudad, nombre, calle"
        ).fetchall()
        _write(["Nombre", "Empresa", "Calle", "Número", "Ciudad", "CP", "País", "Lat", "Lng", "Comentario"],
               [[r["nombre"], r["empresa"], r["calle"], r["numero"], r["ciudad"], r["cp"], r["pais"],
                 r["lat"], r["lng"], r["comentario"]] for r in rows])
    elif tipo == "liquidaciones":
        rows = conn.execute(
            "SELECT l.fecha, t.nombre AS transportista, l.concepto, l.importe, l.pagado "
            "FROM finanzas.liquidaciones l LEFT JOIN transportistas t ON t.id=l.transportista_id ORDER BY l.fecha DESC"
        ).fetchall()
        _write(["Fecha", "Transportista", "Concepto", "Importe (€)", "Pagado"],
               [[r["fecha"], r["transportista"], r["concepto"], float(r["importe"] or 0),
                 "Sí" if r["pagado"] else "No"] for r in rows])
    elif tipo == "facturas":
        rows = conn.execute("SELECT numero, fecha, cliente_nombre, base, iva, cuota_iva, total, estado FROM finanzas.facturas ORDER BY fecha DESC").fetchall()
        _write(["Nº", "Fecha", "Cliente", "Base", "IVA %", "Cuota IVA", "Total", "Estado"],
               [[r["numero"], r["fecha"], r["cliente_nombre"], float(r["base"] or 0), float(r["iva"] or 0),
                 float(r["cuota_iva"] or 0), float(r["total"] or 0), r["estado"]] for r in rows])
    elif tipo == "asientos":
        rows = conn.execute(
            "SELECT a.numero, a.fecha, a.concepto, a.origen, a.documento, p.cuenta, c.nombre AS cuenta_nombre, p.debe, p.haber "
            "FROM finanzas.asientos a JOIN finanzas.apuntes p ON p.asiento_id=a.id JOIN finanzas.cuentas c ON c.codigo=p.cuenta "
            "ORDER BY a.fecha DESC, a.numero DESC, p.id"
        ).fetchall()
        _write(["Nº", "Fecha", "Concepto", "Origen", "Documento", "Cuenta", "Descripción", "Debe", "Haber"],
               [[r["numero"], r["fecha"], r["concepto"], r["origen"], r["documento"], r["cuenta"],
                 r["cuenta_nombre"], float(r["debe"] or 0), float(r["haber"] or 0)] for r in rows])
    elif tipo == "balance":
        rows = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
            "FROM finanzas.apuntes p JOIN finanzas.asientos a ON a.id=p.asiento_id JOIN finanzas.cuentas c ON c.codigo=p.cuenta "
            "GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden, p.cuenta"
        ).fetchall()
        _write(["Cuenta", "Nombre", "Debe", "Haber", "Saldo"],
               [[r["cuenta"], r["nombre"], round(float(r["debe"] or 0), 2), round(float(r["haber"] or 0), 2),
                 round(float(r["debe"] or 0) - float(r["haber"] or 0), 2)] for r in rows])
    elif tipo == "pyg":
        gastos = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.debe)-SUM(p.haber) AS importe "
            "FROM finanzas.apuntes p JOIN finanzas.asientos a ON a.id=p.asiento_id JOIN finanzas.cuentas c ON c.codigo=p.cuenta "
            "WHERE c.tipo='gasto' GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden"
        ).fetchall()
        ingresos = conn.execute(
            "SELECT p.cuenta, c.nombre, SUM(p.haber)-SUM(p.debe) AS importe "
            "FROM finanzas.apuntes p JOIN finanzas.asientos a ON a.id=p.asiento_id JOIN finanzas.cuentas c ON c.codigo=p.cuenta "
            "WHERE c.tipo='ingreso' GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden"
        ).fetchall()
        data = [["GASTOS", "", ""]]
        data += [[r["cuenta"], r["nombre"], round(float(r["importe"] or 0), 2)] for r in gastos]
        data += [["INGRESOS", "", ""]]
        data += [[r["cuenta"], r["nombre"], round(float(r["importe"] or 0), 2)] for r in ingresos]
        _write(["Cuenta", "Concepto", "Importe"], data)
    elif tipo == "empleados":
        rows = conn.execute(
            "SELECT nombre, apellidos, dni, categoria, puesto, tipo_contrato, jornada, fecha_alta, fecha_baja, "
            "salario_bruto, irpf, disponibilidad, banco, iban, telefono, email FROM empleados ORDER BY nombre"
        ).fetchall()
        _write(["Nombre", "Apellidos", "DNI/NIF", "Categoría", "Puesto", "Contrato", "Jornada", "Alta", "Baja",
                "Salario bruto (€)", "IRPF %", "Disponibilidad", "Banco", "IBAN", "Teléfono", "Email"],
               [[r["nombre"], r["apellidos"], r["dni"], r["categoria"], r["puesto"], r["tipo_contrato"], r["jornada"],
                 r["fecha_alta"], r["fecha_baja"], float(r["salario_bruto"] or 0), float(r["irpf"] or 0),
                 r["disponibilidad"], r["banco"], r["iban"], r["telefono"], r["email"]] for r in rows])
    elif tipo == "nominas":
        rows = conn.execute(
            "SELECT n.periodo, e.nombre, e.apellidos, n.salario_bruto, n.irpf_pct, n.ss_trabajador, n.irpf_importe, "
            "n.neto, n.ss_empresa, n.coste_empresa, n.estado, n.pagado FROM nominas n "
            "JOIN empleados e ON e.id=n.empleado_id ORDER BY n.periodo DESC, n.id DESC"
        ).fetchall()
        _write(["Periodo", "Nombre", "Apellidos", "Salario bruto", "IRPF %", "SS trabajador", "IRPF", "Neto",
                "SS empresa", "Coste empresa", "Estado", "Pagado"],
               [[r["periodo"], r["nombre"], r["apellidos"], float(r["salario_bruto"] or 0), float(r["irpf_pct"] or 0),
                 float(r["ss_trabajador"] or 0), float(r["irpf_importe"] or 0), float(r["neto"] or 0),
                 float(r["ss_empresa"] or 0), float(r["coste_empresa"] or 0), r["estado"],
                 "Sí" if r["pagado"] else "No"] for r in rows])
    elif tipo == "ausencias":
        rows = conn.execute(
            "SELECT e.nombre, e.apellidos, a.tipo, a.fecha_inicio, a.fecha_fin, a.dias, a.estado, a.nota "
            "FROM ausencias a JOIN empleados e ON e.id=a.empleado_id ORDER BY a.fecha_inicio DESC"
        ).fetchall()
        _write(["Nombre", "Apellidos", "Tipo", "Inicio", "Fin", "Días", "Estado", "Nota"],
               [[r["nombre"], r["apellidos"], r["tipo"], r["fecha_inicio"], r["fecha_fin"], float(r["dias"] or 0),
                 r["estado"], r["nota"]] for r in rows])
    else:  # trips
        rows = conn.execute("SELECT * FROM trips ORDER BY creado DESC").fetchall()
        cols = ["referencia", "id", "nombre", "cliente", "terminal", "conductor", "tipo_carga", "origen", "destino",
                "tareas", "estado", "estado_pago", "factura", "precio", "gastos", "km_total", "iva", "creado"]
        data = [[r[c] if c in r.keys() else "" for c in cols] for r in rows]
        _write(cols, data)


    # Ajustar ancho de columnas al contenido
    for col_cells in ws.columns:
        max_len = 0
        letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[letter].width = min(max_len + 2, 60)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="tms_{tipo}.xlsx"'},
    )




@router.get("/api/geocode")
def geocode(q: str = "", limit: int = 5):
    """Búsqueda de lugares vía Nominatim (OpenStreetMap). Gratis, sin API key."""
    if not q.strip():
        return {"resultados": []}
    url = (
        "https://nominatim.openstreetmap.org/search?"
        + urllib.parse.urlencode({
            "q": q,
            "format": "json",
            "addressdetails": 1,
            "limit": min(max(limit, 1), 10),
            "accept-language": "es",
        })
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "tms-trimble/0.1 (contacto: uxevilla@gmail.com)",
        "Accept-Language": "es",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"resultados": [], "error": "No se pudo consultar el geocodificador."}

    resultados = []
    for item in data:
        a = item.get("address", {}) or {}
        resultados.append({
            "display_name": item.get("display_name", ""),
            "lat": float(item.get("lat", 0) or 0),
            "lng": float(item.get("lon", 0) or 0),
            "nombre": a.get("name") or a.get("amenity") or a.get("shop")
                      or a.get("tourism") or a.get("building") or "",
            "calle": a.get("road") or a.get("pedestrian") or "",
            "numero": a.get("house_number", ""),
            "ciudad": a.get("city") or a.get("town") or a.get("village")
                      or a.get("municipality") or a.get("county") or "",
            "cp": a.get("postcode", ""),
            "pais": (a.get("country_code") or "").upper(),
        })
    return {"resultados": resultados}




@router.post("/api/sync/files")
def sync_files():
    try:
        _sync_files()
        _sync_mensajes()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e)})




@router.post("/api/sync/mensajes")
def sync_mensajes():
    """Sync ligero: solo mensajería (estructurados + libres), para refrescar el chat con poco delay."""
    try:
        _sync_mensajes()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail={"error": str(e)})








@router.post("/api/webhooks/transfollow")
async def webhook_transfollow(req: dict, authorization: str = Header(default="")):
    """Webhook de TransFollow: cierra el viaje cuando el e-CMR se entrega.

    Seguridad: TransFollow autentica sus webhooks por Basic auth (o mTLS / path
    aleatorio); aquí validamos la cabecera Authorization de forma constante en el
    tiempo. Estados TransFollow: DRAFT→ISSUED→TRANSIT→DELIVERED / CANCELLED /
    DELIVERED FOR FURTHER INSPECTION. Solo DELIVERED limpio cierra el viaje.
    """
    if not _webhook_autenticado(authorization):
        raise HTTPException(status_code=401, detail={"error": "No autorizado"})

    data = req.get("data") if isinstance(req.get("data"), dict) else req
    fd_id = (data.get("freightDocumentId") or data.get("documentId")
             or data.get("id") or req.get("freightDocumentId"))
    estado = str(data.get("status") or data.get("state")
                 or data.get("event") or data.get("eventType") or "").lower()
    # DELIVERED FOR FURTHER INSPECTION NO es entrega limpia: no cerrar.
    if "delivered for further inspection" in estado:
        return {"ok": True, "ignored": True, "estado": estado}
    entregado = any(k in estado for k in ("delivered", "entregado", "completed",
                                          "finalizado", "signed", "firmado"))
    if not entregado:
        return {"ok": True, "ignored": True, "estado": estado}
    if not fd_id:
        raise HTTPException(status_code=400, detail={"error": "freightDocumentId ausente"})
    trip_id = await asyncio.to_thread(_cerrar_viaje_por_ecmr, str(fd_id))
    if not trip_id:
        return {"ok": True, "ignored": True, "motivo": "sin viaje asociado"}
    return {"ok": True, "trip_id": trip_id, "estado": "Entregado"}


