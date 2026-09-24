"""
Router de la Torre de control (Fase 2): bandeja de atención, búsqueda global y
paneles de entidad. Solo lectura/validación, multi-tenant, roles admin+dispatcher.
Reutiliza la lógica existente (tacógrafo, caducidades, mantenimiento); no duplica cálculos.
"""
import datetime
import unicodedata
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from db import get_conn, _Conn
from security import require_role

router = APIRouter(dependencies=[Depends(require_role(["admin", "dispatcher"]))])

# Límites legales de conducción (core.py).
_MAX_CONDUCCION_CONTINUA_MIN = 270.0
_MAX_DIA_CONDUCCION_MIN = 540.0

_ORDEN_SEVERIDAD = {"critico": 0, "aviso": 1, "info": 2}


def _hoy() -> str:
    return datetime.date.today().isoformat()


def _dias_hasta(fecha) -> int | None:
    if not fecha:
        return None
    try:
        return (datetime.date.fromisoformat(str(fecha)[:10]) - datetime.date.today()).days
    except ValueError:
        return None


def _normalizar(q: str) -> str:
    """Minúsculas y sin acentos (búsqueda insensible a mayúsculas/acentos)."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", q.lower()) if not unicodedata.combining(c)
    )


def _item(tipo, severidad, titulo, detalle, entidad, ts=None, acciones=None):
    return {
        "id": f"{tipo}:{entidad.get('tipo', '')}:{entidad.get('codigo') or entidad.get('id')}",
        "tipo": tipo,
        "severidad": severidad,
        "titulo": titulo,
        "detalle": detalle,
        "entidad": entidad,
        "acciones": acciones or [{"id": "abrir", "label": "Abrir"}],
        "ts": ts or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.get("/api/atencion")
def atencion(user: dict = Depends(require_role(["admin", "dispatcher"])), conn: _Conn = Depends(get_conn)):
    """Bandeja de 'Requiere atención': 8 clases de avisos, agrupadas por severidad."""
    items = []
    hoy = _hoy()
    es_admin = user.get("rol") == "admin"

    # 1. viaje_retrasado: en tránsito y con fecha_esperada_descarga ya pasada.
    for r in conn.execute(
        "SELECT codigo, matricula, cliente, origen, destino, fecha_esperada_descarga, estado "
        "FROM operaciones.trips "
        "WHERE COALESCE(estado,'') NOT IN ('Entregado','Cancelado','sin_asignar','planificado','') "
        "AND fecha_esperada_descarga IS NOT NULL AND fecha_esperada_descarga != '' "
        "AND fecha_esperada_descarga < ? ORDER BY fecha_esperada_descarga ASC LIMIT 50",
        (hoy,),
    ).fetchall():
        items.append(_item(
            "viaje_retrasado", "aviso",
            f"Viaje {r['codigo']} retrasado",
            f"Descarga prevista {r['fecha_esperada_descarga']} · {r['origen']} → {r['destino']}",
            {"tipo": "viaje", "id": r["codigo"], "codigo": r["codigo"]},
        ))

    # 2. conduccion_limite: < 30 min de conducción continua (>=240/270) o diaria (>=510/540).
    for r in conn.execute(
        "SELECT d.did, d.vehiculo_id, d.driving_coupure_min, d.day_driving_min, d.remaining_week_available_min, c.id AS conductor_id, c.nombre "
        "FROM (SELECT DISTINCT ON (vehiculo_id) * FROM tacografo_dstat "
        "      ORDER BY vehiculo_id, COALESCE(time, creado) DESC) d "
        "LEFT JOIN conductores c ON c.did = d.did "
        "WHERE COALESCE(d.driving_coupure_min,0) >= 240 OR COALESCE(d.day_driving_min,0) >= 510 "
        "ORDER BY GREATEST(COALESCE(d.driving_coupure_min,0), COALESCE(d.day_driving_min,0)) DESC LIMIT 50",
    ).fetchall():
        restante = min(
            _MAX_CONDUCCION_CONTINUA_MIN - float(r["driving_coupure_min"] or 0),
            _MAX_DIA_CONDUCCION_MIN - float(r["day_driving_min"] or 0),
        )
        items.append(_item(
            "conduccion_limite", "critico",
            f"Conducción al límite — {r['nombre'] or r['did']}",
            f"Quedan {max(0, round(restante))} min de conducción legal",
            {"tipo": "conductor", "id": r["conductor_id"] or r["did"], "codigo": r["nombre"] or r["did"]},
        ))

    # 3. caducidad: ITV/seguro (vehículos) + carné/CAP/médico (conductores).
    for r in conn.execute(
        "SELECT id, codigo, matricula, fecha_caducidad_itv AS itv, fecha_caducidad_seguro AS seguro "
        "FROM vehiculos WHERE activo = true",
    ).fetchall():
        for campo, etiqueta in (("itv", "ITV"), ("seguro", "Seguro")):
            dias = _dias_hasta(r[campo])
            if dias is None:
                continue
            if dias < 0:
                items.append(_item("caducidad", "critico", f"{etiqueta} de {r['matricula'] or r['codigo']} caducada",
                                   f"Venció hace {-dias} días",
                                   {"tipo": "vehiculo", "id": r["codigo"], "codigo": r["codigo"]}))
            elif dias <= 30:
                items.append(_item("caducidad", "aviso", f"{etiqueta} de {r['matricula'] or r['codigo']} próxima",
                                   f"Vence en {dias} días",
                                   {"tipo": "vehiculo", "id": r["codigo"], "codigo": r["codigo"]}))
    for r in conn.execute(
        "SELECT c.id, e.nombre, e.apellidos, e.caducidad_carnet, e.caducidad_cap, e.caducidad_medica "
        "FROM empleados e JOIN rrhh.conductores c ON c.empleado_id = e.id "
        "WHERE COALESCE(e.fecha_baja,'') = ''",
    ).fetchall():
        nombre = f"{r['nombre']} {r['apellidos']}".strip()
        for campo, etiqueta in (("caducidad_carnet", "Carné"), ("caducidad_cap", "CAP"), ("caducidad_medica", "Reconocimiento médico")):
            dias = _dias_hasta(r[campo])
            if dias is None:
                continue
            if dias < 0:
                items.append(_item("caducidad", "critico", f"{etiqueta} de {nombre} caducado",
                                   f"Venció hace {-dias} días",
                                   {"tipo": "conductor", "id": r["id"], "codigo": nombre}))
            elif dias <= 30:
                items.append(_item("caducidad", "aviso", f"{etiqueta} de {nombre} próximo",
                                   f"Vence en {dias} días",
                                   {"tipo": "conductor", "id": r["id"], "codigo": nombre}))

    # 4. viaje_sin_facturar: entregado sin factura (solo admin).
    facturacion_pendiente = 0.0
    if es_admin:
        for r in conn.execute(
            "SELECT codigo, cliente, origen, destino, precio FROM operaciones.trips "
            "WHERE estado = 'Entregado' AND (factura IS NULL OR factura = '') "
            "ORDER BY COALESCE(fecha_actualizacion, creado) DESC LIMIT 50",
        ).fetchall():
            facturacion_pendiente += float(r["precio"] or 0)
            items.append(_item(
                "viaje_sin_facturar", "aviso",
                f"Viaje {r['codigo']} sin facturar",
                f"Entregado · {r['cliente'] or '?'} · {r['origen']} → {r['destino']}",
                {"tipo": "viaje", "id": r["codigo"], "codigo": r["codigo"]},
                acciones=[{"id": "abrir", "label": "Abrir"}, {"id": "facturar", "label": "Facturar"}],
            ))

    # 5. gasto_sin_imputar: gasto sin vehículo ni viaje.
    for r in conn.execute(
        "SELECT id, concepto, importe, fecha FROM finanzas.gastos "
        "WHERE (terminal IS NULL OR terminal = '') AND (trip_id IS NULL OR trip_id = '') "
        "ORDER BY fecha DESC LIMIT 50",
    ).fetchall():
        items.append(_item(
            "gasto_sin_imputar", "info",
            f"Gasto sin imputar: {r['concepto'] or 'sin concepto'}",
            f"{r['importe']} € · {r['fecha']}",
            {"tipo": "gasto", "id": r["id"], "codigo": f"G-{r['id']}"},
        ))
    for r in conn.execute(
        "SELECT id, tipo, importe_total, fecha FROM finanzas.gastos_vehiculos "
        "WHERE (vehiculo_id IS NULL OR vehiculo_id = '') ORDER BY fecha DESC LIMIT 50",
    ).fetchall():
        items.append(_item(
            "gasto_sin_imputar", "info",
            f"Gasto de vehículo sin imputar: {r['tipo']}",
            f"{r['importe_total']} € · {r['fecha']}",
            {"tipo": "gasto", "id": r["id"], "codigo": f"GV-{r['id']}"},
        ))

    # 6. mensaje_sin_responder: needreply y sin mensaje posterior en el hilo.
    for r in conn.execute(
        "SELECT m.id, m.trip_id, m.messagetype, m.time, m.source, t.codigo FROM mensajes m "
        "LEFT JOIN operaciones.trips t ON t.codigo = m.trip_id "
        "WHERE m.needreply = true AND NOT EXISTS ("
        "  SELECT 1 FROM mensajes m2 WHERE m2.trip_id = m.trip_id AND m2.time > m.time"
        ") ORDER BY m.time DESC LIMIT 50",
    ).fetchall():
        items.append(_item(
            "mensaje_sin_responder", "aviso",
            f"Mensaje sin responder ({r['messagetype'] or '?'})",
            f"De {r['source'] or '?'} · viaje {r['codigo'] or r['trip_id']}",
            {"tipo": "viaje", "id": r["trip_id"], "codigo": r["trip_id"]},
        ))

    # 7. mantenimiento_vencido: km_actuales por encima de ultimo_km_realizado + intervalo_km.
    for r in conn.execute(
        "SELECT r.vehiculo_id, r.tipo_mantenimiento, r.intervalo_km, r.ultimo_km_realizado, v.matricula, v.codigo "
        "FROM flota.reglas_mantenimiento r JOIN vehiculos v ON v.id = r.vehiculo_id "
        "WHERE COALESCE(v.km_actuales,0) > COALESCE(r.ultimo_km_realizado,0) + COALESCE(r.intervalo_km,0) "
        "AND COALESCE(r.intervalo_km,0) > 0 ORDER BY v.km_actuales DESC LIMIT 50",
    ).fetchall():
        exceso = round(float(r["ultimo_km_realizado"] or 0) + float(r["intervalo_km"] or 0), 0)
        items.append(_item(
            "mantenimiento_vencido", "critico",
            f"Mantenimiento vencido: {r['tipo_mantenimiento']} — {r['matricula'] or r['vehiculo_id']}",
            f"Supera los {exceso:,.0f} km del plan",
            {"tipo": "vehiculo", "id": r["codigo"] or r["vehiculo_id"], "codigo": r["codigo"] or r["vehiculo_id"]},
        ))

    # 8. envio_trimble_fallido: viaje en estado error.
    for r in conn.execute(
        "SELECT codigo, terminal, cliente, error FROM operaciones.trips "
        "WHERE COALESCE(estado,'') = 'error' OR (error IS NOT NULL AND error != '') "
        "ORDER BY COALESCE(fecha_actualizacion, creado) DESC LIMIT 50",
    ).fetchall():
        items.append(_item(
            "envio_trimble_fallido", "critico",
            f"Envío a Trimble fallido: {r['codigo']}",
            f"{r['error'] or 'error desconocido'} · terminal {r['terminal'] or '?'}",
            {"tipo": "viaje", "id": r["codigo"], "codigo": r["codigo"]},
        ))

    # Orden: por severidad (critico > aviso > info) y, dentro, por antigüedad.
    items.sort(key=lambda it: (_ORDEN_SEVERIDAD.get(it["severidad"], 9), it.get("ts") or ""))

    resumen: dict = {"critico": 0, "aviso": 0, "info": 0}
    for it in items:
        resumen[it["severidad"]] += 1
    if es_admin:
        resumen["facturacion_pendiente"] = round(facturacion_pendiente, 2)

    return {"items": items, "resumen": resumen}


@router.get("/api/buscar")
def buscar(q: Annotated[str, Query(max_length=80)] = "", limite: Annotated[int, Query(ge=1, le=20)] = 8,
           user: dict = Depends(require_role(["admin", "dispatcher"])), conn: _Conn = Depends(get_conn)):
    """Búsqueda global para la paleta. Mínimo 2 caracteres; insensible a mayúsculas y acentos (normalización Python)."""
    q_norm = _normalizar(q.strip())
    if len(q_norm) < 2:
        return {"resultados": []}
    es_admin = user.get("rol") == "admin"
    resultados = []

    def _coincide(r, *campos):
        return q_norm in _normalizar(" ".join(str(r.get(c) or "") for c in campos))

    for r in conn.execute(
        "SELECT codigo, referencia, cliente, origen, destino FROM operaciones.trips "
        "ORDER BY COALESCE(fecha_actualizacion, creado) DESC LIMIT 300",
    ).fetchall():
        if _coincide(r, "codigo", "referencia", "cliente", "origen", "destino"):
            resultados.append({
                "tipo": "viaje", "id": r["codigo"],
                "titulo": r["codigo"] or r["referencia"] or "Viaje",
                "subtitulo": f"{r['cliente'] or ''} · {r['origen']} → {r['destino']}",
            })

    for r in conn.execute(
        "SELECT id, codigo, matricula, terminal_trimble FROM vehiculos WHERE activo = true LIMIT 300",
    ).fetchall():
        if _coincide(r, "matricula", "codigo", "terminal_trimble"):
            resultados.append({
                "tipo": "vehiculo", "id": r["id"],
                "titulo": r["matricula"] or r["codigo"],
                "subtitulo": f"Vehículo · {r['codigo'] or ''}",
            })

    for r in conn.execute(
        "SELECT id, nombre, dni FROM conductores WHERE activo = true LIMIT 300",
    ).fetchall():
        if _coincide(r, "nombre", "dni"):
            resultados.append({
                "tipo": "conductor", "id": r["id"],
                "titulo": r["nombre"],
                "subtitulo": f"Conductor · DNI {r['dni']}" if es_admin and r["dni"] else "Conductor",
            })

    for r in conn.execute(
        "SELECT id, razon_social, nombre_comercial, nif FROM maestros.terceros "
        "WHERE es_cliente = true AND activo = true LIMIT 300",
    ).fetchall():
        if _coincide(r, "razon_social", "nombre_comercial", "nif"):
            resultados.append({
                "tipo": "cliente", "id": r["id"],
                "titulo": r["nombre_comercial"] or r["razon_social"],
                "subtitulo": f"Cliente · {r['nif'] or ''}",
            })

    for r in conn.execute(
        "SELECT id, razon_social, nombre_comercial, nif FROM maestros.terceros "
        "WHERE es_proveedor = true AND activo = true LIMIT 300",
    ).fetchall():
        if _coincide(r, "razon_social", "nombre_comercial", "nif"):
            resultados.append({
                "tipo": "proveedor", "id": r["id"],
                "titulo": r["nombre_comercial"] or r["razon_social"],
                "subtitulo": f"Proveedor · {r['nif'] or ''}",
            })

    if es_admin:
        for r in conn.execute(
            "SELECT id, numero, cliente_nombre, fecha FROM finanzas.facturas "
            "WHERE COALESCE(borrado,false) = false ORDER BY id DESC LIMIT 300",
        ).fetchall():
            if _coincide(r, "numero", "cliente_nombre"):
                resultados.append({
                    "tipo": "factura", "id": r["numero"],
                    "titulo": r["numero"],
                    "subtitulo": f"Factura · {r['cliente_nombre'] or ''}",
                })

    return {"resultados": resultados[:limite]}


@router.get("/api/entidad/vehiculo/{codigo}")
def entidad_vehiculo(codigo: str, user: dict = Depends(require_role(["admin", "dispatcher"])),
                     conn: _Conn = Depends(get_conn)):
    v = conn.execute(
        "SELECT * FROM vehiculos WHERE id = ? OR codigo = ? LIMIT 1", (codigo, codigo),
    ).fetchone()
    if not v:
        raise HTTPException(status_code=404, detail={"error": "Vehículo no encontrado"})
    es_admin = user.get("rol") == "admin"

    pos = conn.execute(
        "SELECT lat, lng, speed_kmh, heading, odometer_km, time FROM telemetria.posiciones_gps "
        "WHERE vehiculo_id = ? ORDER BY time DESC LIMIT 1", (codigo,),
    ).fetchone()

    viaje_actual = conn.execute(
        "SELECT codigo, estado, origen, destino, cliente, fecha_esperada_descarga FROM operaciones.trips "
        "WHERE terminal = ? AND COALESCE(estado,'') NOT IN ('Entregado','Cancelado','sin_asignar','') "
        "ORDER BY creado DESC LIMIT 1", (codigo,),
    ).fetchone()

    proximos = conn.execute(
        "SELECT codigo, estado, origen, destino, fecha_esperada_carga FROM operaciones.trips "
        "WHERE terminal = ? AND (estado IN ('sin_asignar','planificado') OR estado IS NULL) "
        "ORDER BY creado DESC LIMIT 5", (codigo,),
    ).fetchall()

    dstat = conn.execute(
        "SELECT d.*, c.nombre AS conductor_nombre FROM (SELECT DISTINCT ON (vehiculo_id) * FROM tacografo_dstat "
        "WHERE vehiculo_id = ? ORDER BY vehiculo_id, COALESCE(time, creado) DESC) d "
        "LEFT JOIN conductores c ON c.did = d.did", (codigo,),
    ).fetchone()

    caducidades = []
    for campo, etiqueta in (("fecha_caducidad_itv", "ITV"), ("fecha_caducidad_seguro", "Seguro")):
        dias = _dias_hasta(v[campo])
        if dias is not None:
            caducidades.append({"tipo": etiqueta, "fecha": v[campo], "dias": dias})

    mantenimientos = conn.execute(
        "SELECT tipo, fecha, km, coste, hecho FROM flota.mantenimientos "
        "WHERE vehiculo_id = ? ORDER BY fecha DESC LIMIT 5", (codigo,),
    ).fetchall()

    documentos = conn.execute(
        "SELECT name, formato, bytes FROM files WHERE vehiculo_id = ? ORDER BY ftime DESC LIMIT 20",
        (codigo,),
    ).fetchall()

    resultado = {
        "vehiculo": {
            "codigo": v["codigo"], "matricula": v["matricula"], "categoria": v["categoria"],
            "marca": v["marca"], "modelo": v["modelo"], "km_actuales": v["km_actuales"],
            "terminal_trimble": v["terminal_trimble"],
        },
        "posicion": {
            "lat": float(pos["lat"]) if pos and pos["lat"] is not None else None,
            "lng": float(pos["lng"]) if pos and pos["lng"] is not None else None,
            "velocidad": float(pos["speed_kmh"]) if pos and pos["speed_kmh"] is not None else None,
            "heading": float(pos["heading"]) if pos and pos["heading"] is not None else None,
            "odometer_km": float(pos["odometer_km"]) if pos and pos["odometer_km"] is not None else None,
            "time": pos["time"].isoformat() if pos and pos["time"] else None,
        } if pos else None,
        "viaje_actual": viaje_actual,
        "proximos": [dict(p) for p in proximos],
        "tacografo": {
            "conductor": dstat["conductor_nombre"] if dstat else None,
            "did": dstat["did"] if dstat else None,
            "driving_coupure_min": float(dstat["driving_coupure_min"] or 0) if dstat else None,
            "day_driving_min": float(dstat["day_driving_min"] or 0) if dstat else None,
            "remaining_week_available_min": float(dstat["remaining_week_available_min"] or 0) if dstat else None,
            "next_rest_due_ts": int(dstat["next_rest_due_ts"] or 0) if dstat else None,
        } if dstat else None,
        "caducidades": caducidades,
        "mantenimientos": [dict(m) for m in mantenimientos],
        "documentos": [{"nombre": d["name"], "formato": d["formato"], "bytes": d["bytes"]} for d in documentos],
    }

    if es_admin:
        mes = datetime.date.today().strftime("%Y-%m")
        r = conn.execute(
            "SELECT "
            "  (SELECT COALESCE(SUM(COALESCE(t.precio,0)),0) FROM operaciones.trips t "
            "   WHERE t.terminal = ? AND substr(COALESCE(t.fecha_actualizacion, t.creado),1,7) = ?) AS ingresos, "
            "  (SELECT COALESCE(SUM(COALESCE(g.importe_total,0)),0) FROM finanzas.gastos_vehiculos g "
            "   WHERE g.vehiculo_id = ? AND substr(COALESCE(g.fecha,''),1,7) = ?) AS costes",
            (codigo, mes, codigo, mes),
        ).fetchone()
        ingresos = float(r["ingresos"] or 0)
        costes = float(r["costes"] or 0)
        resultado["coste_margen_mes"] = {"ingresos": ingresos, "costes": costes, "margen": round(ingresos - costes, 2)}

    return resultado


@router.get("/api/entidad/viaje/{codigo}")
def entidad_viaje(codigo: str, user: dict = Depends(require_role(["admin", "dispatcher"])),
                  conn: _Conn = Depends(get_conn)):
    t = conn.execute("SELECT * FROM operaciones.trips WHERE codigo = ? LIMIT 1", (codigo,)).fetchone()
    if not t:
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado"})
    es_admin = user.get("rol") == "admin"

    paradas = conn.execute(
        "SELECT orden, nombre, ciudad, actividad, comentario FROM operaciones.paradas "
        "WHERE trip_id = ? ORDER BY orden", (codigo,),
    ).fetchall()

    documentos = conn.execute(
        "SELECT name, formato, bytes, source FROM files WHERE trip_id = ? ORDER BY ftime DESC LIMIT 20",
        (codigo,),
    ).fetchall()

    mensajes = conn.execute(
        "SELECT tipo, messagetype, source, subject, time, needreply FROM mensajes "
        "WHERE trip_id = ? ORDER BY time DESC LIMIT 30", (codigo,),
    ).fetchall()

    resultado = {
        "viaje": {
            "codigo": t["codigo"], "referencia": t["referencia"], "estado": t["estado"],
            "cliente": t["cliente"], "origen": t["origen"], "destino": t["destino"],
            "terminal": t["terminal"], "matricula": t["matricula"], "conductor": t["conductor"],
            "precio": t["precio"], "km_total": t["km_total"], "fecha_esperada_carga": t["fecha_esperada_carga"],
            "fecha_esperada_descarga": t["fecha_esperada_descarga"],
        },
        "paradas": [dict(p) for p in paradas],
        "documentos": [{"nombre": d["name"], "formato": d["formato"], "bytes": d["bytes"], "origen": d["source"]} for d in documentos],
        "mensajes": [dict(m) for m in mensajes],
    }

    if es_admin:
        costes = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(importe,0)),0) AS c FROM finanzas.gastos WHERE trip_id = ?",
            (codigo,),
        ).fetchone()
        ingresos = float(t["precio"] or 0)
        costes_v = float(costes["c"] or 0) if costes else 0.0
        resultado["rentabilidad"] = {"ingresos": ingresos, "costes": costes_v, "margen": round(ingresos - costes_v, 2)}

    return resultado


@router.get("/api/entidad/conductor/{conductor_id}")
def entidad_conductor(conductor_id, user: dict = Depends(require_role(["admin", "dispatcher"])),
                      conn: _Conn = Depends(get_conn)):
    c = conn.execute(
        "SELECT id, nombre, dni, telefono, email, did FROM conductores WHERE id = ? LIMIT 1",
        (conductor_id,),
    ).fetchone()
    if not c:
        raise HTTPException(status_code=404, detail={"error": "Conductor no encontrado"})

    dstat = conn.execute(
        "SELECT * FROM tacografo_dstat WHERE did = ? ORDER BY COALESCE(time, creado) DESC LIMIT 1",
        (c["did"],),
    ).fetchone()

    viaje_actual = conn.execute(
        "SELECT codigo, estado, origen, destino, fecha_esperada_descarga FROM operaciones.trips "
        "WHERE conductor_id = ? AND COALESCE(estado,'') NOT IN ('Entregado','Cancelado','sin_asignar','') "
        "ORDER BY creado DESC LIMIT 1", (conductor_id,),
    ).fetchone()

    proximos = conn.execute(
        "SELECT codigo, estado, origen, destino, fecha_esperada_carga FROM operaciones.trips "
        "WHERE conductor_id = ? AND (estado IN ('sin_asignar','planificado') OR estado IS NULL) "
        "ORDER BY creado DESC LIMIT 5", (conductor_id,),
    ).fetchall()

    empleado = conn.execute(
        "SELECT caducidad_carnet, caducidad_cap, caducidad_medica FROM empleados WHERE id = "
        "(SELECT empleado_id FROM rrhh.conductores WHERE id = ?)", (conductor_id,),
    ).fetchone()

    caducidades = []
    if empleado:
        for campo, etiqueta in (("caducidad_carnet", "Carné"), ("caducidad_cap", "CAP"), ("caducidad_medica", "Reconocimiento médico")):
            dias = _dias_hasta(empleado[campo])
            if dias is not None:
                caducidades.append({"tipo": etiqueta, "fecha": empleado[campo], "dias": dias})

    ausencias = conn.execute(
        "SELECT tipo, fecha_inicio, fecha_fin FROM ausencias_empleados WHERE empleado_id = "
        "(SELECT empleado_id FROM rrhh.conductores WHERE id = ?) "
        "AND fecha_fin >= ? ORDER BY fecha_inicio ASC LIMIT 5", (conductor_id, _hoy()),
    ).fetchall()

    return {
        "conductor": {"id": c["id"], "nombre": c["nombre"], "dni": c["dni"] if user.get("rol") == "admin" else None,
                      "telefono": c["telefono"], "email": c["email"]},
        "tacografo": {
            "did": dstat["did"] if dstat else None,
            "driving_coupure_min": float(dstat["driving_coupure_min"] or 0) if dstat else None,
            "day_driving_min": float(dstat["day_driving_min"] or 0) if dstat else None,
            "remaining_week_available_min": float(dstat["remaining_week_available_min"] or 0) if dstat else None,
            "next_rest_due_ts": int(dstat["next_rest_due_ts"] or 0) if dstat else None,
        } if dstat else None,
        "viaje_actual": viaje_actual,
        "proximos": [dict(p) for p in proximos],
        "caducidades": caducidades,
        "ausencias": [dict(a) for a in ausencias],
    }
