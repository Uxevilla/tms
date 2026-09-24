"""contabilidad router (extraído de main.py)."""
import uuid
import datetime
import io

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import Response

import config
from db import _db, get_conn, _get_config, _valores_proveedor
from security import require_role
import re
from core import *
from models import *
from services.contabilidad import _costes_reales_viaje, _crear_factura_borrador, _generar_factura_pdf, _liquidar_conductor
from services.contabilidad import _post_asiento, _gasto_subcontrata, _costes_reales_viaje, _crear_factura_borrador

router = APIRouter(dependencies=[Depends(require_role(["admin"]))])

# ---------------------------------------------------------------------- #
# Contabilidad: doble partida (plan contable, asientos, informes, facturas)
# ---------------------------------------------------------------------- #








def _sumas_por_tipo(conn, desde="", hasta=""):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY c.tipo", params,
    ).fetchall()
    sums = {}
    for r in rows:
        sums[r["tipo"]] = [float(r["debe"] or 0), float(r["haber"] or 0)]
    return sums




def _suma_cuenta(conn, cuenta, desde="", hasta=""):
    conds, params = ["p.cuenta = ?"], [cuenta]
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    row = conn.execute(
        f"SELECT SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id WHERE {' AND '.join(conds)}", params,
    ).fetchone()
    return float(row["debe"] or 0), float(row["haber"] or 0)




@router.get("/api/contabilidad/cuentas")


def contabilidad_cuentas(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM cuentas ORDER BY orden, codigo").fetchall()
    return {"cuentas": [dict(r) for r in rows]}




@router.post("/api/contabilidad/cuentas")


def upsert_cuenta(c: CuentaContable, conn = Depends(get_conn)):
    conn.execute(
        "INSERT INTO cuentas (codigo, nombre, grupo, tipo, orden) VALUES (?,?,?,?,?) "
        "ON CONFLICT (codigo) DO UPDATE SET nombre=EXCLUDED.nombre, grupo=EXCLUDED.grupo, "
        "tipo=EXCLUDED.tipo, orden=EXCLUDED.orden",
        (c.codigo.strip(), c.nombre.strip(), c.grupo.strip(), c.tipo.strip(), c.orden),
    )
    conn.commit()
    return {"ok": True}




@router.delete("/api/contabilidad/cuentas/{codigo}")


def del_cuenta(codigo: str, conn = Depends(get_conn)):
    n = conn.execute("SELECT COUNT(*) AS n FROM apuntes WHERE cuenta=?", (codigo,)).fetchone()["n"]
    if n:
        raise HTTPException(status_code=409, detail={"error": "La cuenta tiene apuntes; no se puede borrar."})
    conn.execute("DELETE FROM cuentas WHERE codigo=?", (codigo,))
    conn.commit()
    return {"ok": True}




@router.get("/api/contabilidad/asientos")


def contabilidad_asientos(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    conds, params = [], []
    if desde:
        conds.append("fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT * FROM asientos{where} ORDER BY fecha DESC, numero DESC, id DESC LIMIT 500", params,
    ).fetchall()
    apuntes = {}
    if rows:
        ids = [r["id"] for r in rows]
        ph = ",".join("?" for _ in ids)
        arows = conn.execute(
            f"SELECT ap.*, c.nombre AS cuenta_nombre FROM apuntes ap JOIN cuentas c ON c.codigo=ap.cuenta "
            f"WHERE ap.asiento_id IN ({ph}) ORDER BY ap.id", ids,
        ).fetchall()
        for r in arows:
            apuntes.setdefault(r["asiento_id"], []).append(dict(r))
    return {"asientos": [dict(r) for r in rows], "apuntes": apuntes}




@router.post("/api/contabilidad/asientos")


def contabilidad_crear_asiento(a: AsientoManual):
    lineas = [(l.cuenta.strip(), l.debe, l.haber, l.concepto) for l in a.lineas if l.cuenta and l.cuenta.strip()]
    if not lineas:
        raise HTTPException(status_code=400, detail={"error": "El asiento no tiene líneas."})
    try:
        aid = _post_asiento(a.fecha, a.concepto, lineas, origen="manual", documento=a.documento)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": str(e)})
    return {"ok": True, "asiento_id": aid}




@router.delete("/api/contabilidad/asientos/{asiento_id}")


def contabilidad_del_asiento(asiento_id: int, conn = Depends(get_conn)):
    row = conn.execute("SELECT origen FROM asientos WHERE id=?", (asiento_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"error": "Asiento no encontrado."})
    if row["origen"] != "manual":
        raise HTTPException(status_code=409, detail={"error": "Es un asiento automático; borra la factura o el gasto asociado."})
    conn.execute("DELETE FROM asientos WHERE id=?", (asiento_id,))
    conn.commit()
    return {"ok": True}




@router.get("/api/contabilidad/balance")


def contabilidad_balance(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT p.cuenta, c.nombre, c.grupo, c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY p.cuenta, c.nombre, c.grupo, c.tipo, c.orden ORDER BY c.orden, p.cuenta", params,
    ).fetchall()
    total_debe = total_haber = 0.0
    cuentas = []
    for r in rows:
        d = float(r["debe"] or 0); h = float(r["haber"] or 0)
        total_debe += d; total_haber += h
        cuentas.append({"cuenta": r["cuenta"], "nombre": r["nombre"], "grupo": r["grupo"],
                        "tipo": r["tipo"], "debe": round(d, 2), "haber": round(h, 2),
                        "saldo": round(d - h, 2)})
    return {"cuentas": cuentas, "total_debe": round(total_debe, 2), "total_haber": round(total_haber, 2)}




@router.get("/api/contabilidad/pyg")


def contabilidad_pyg(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    base = ("FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta ")
    gastos = conn.execute(
        f"SELECT p.cuenta, c.nombre, SUM(p.debe)-SUM(p.haber) AS importe {base} "
        f"WHERE {' AND '.join(conds + ['c.tipo=?'])} GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden",
        params + ["gasto"],
    ).fetchall()
    ingresos = conn.execute(
        f"SELECT p.cuenta, c.nombre, SUM(p.haber)-SUM(p.debe) AS importe {base} "
        f"WHERE {' AND '.join(conds + ['c.tipo=?'])} GROUP BY p.cuenta, c.nombre, c.orden ORDER BY c.orden",
        params + ["ingreso"],
    ).fetchall()
    tg = sum(float(r["importe"] or 0) for r in gastos)
    ti = sum(float(r["importe"] or 0) for r in ingresos)
    return {
        "gastos": [{"cuenta": r["cuenta"], "nombre": r["nombre"], "importe": round(float(r["importe"] or 0), 2)} for r in gastos],
        "ingresos": [{"cuenta": r["cuenta"], "nombre": r["nombre"], "importe": round(float(r["importe"] or 0), 2)} for r in ingresos],
        "total_gastos": round(tg, 2),
        "total_ingresos": round(ti, 2),
        "resultado": round(ti - tg, 2),
    }




@router.get("/api/contabilidad/situacion")


def contabilidad_situacion(hasta: str = "", conn = Depends(get_conn)):
    sums = _sumas_por_tipo(conn, "", hasta)
    def saldo(tipo):
        d, h = sums.get(tipo, [0, 0])
        return d - h
    activo = saldo("activo")
    pasivo = -(saldo("pasivo"))
    patrimonio = -(saldo("patrimonio"))
    resultado = -(saldo("ingreso")) - saldo("gasto")
    neto = patrimonio + resultado
    return {
        "activo": round(activo, 2),
        "pasivo": round(pasivo, 2),
        "patrimonio": round(patrimonio, 2),
        "resultado": round(resultado, 2),
        "patrimonio_neto": round(neto, 2),
        "cuadra": abs(activo - (pasivo + neto)) < 0.01,
    }




@router.get("/api/contabilidad/dashboard")


def contabilidad_dashboard(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    sums = _sumas_por_tipo(conn, desde, hasta)
    def saldo(tipo):
        d, h = sums.get(tipo, [0, 0])
        return d - h
    ingresos = -saldo("ingreso")
    gastos = saldo("gasto")
    iva_rep = conn.execute(
        "SELECT SUM(haber)-SUM(debe) AS v FROM apuntes WHERE cuenta='477'"
    ).fetchone()["v"] or 0
    iva_sop = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta='472'"
    ).fetchone()["v"] or 0
    bancos = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')"
    ).fetchone()["v"] or 0
    frows = conn.execute(
        "SELECT estado, COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM facturas GROUP BY estado"
    ).fetchall()
    fact = {"emitida": 0, "cobrada": 0}
    for r in frows:
        fact[r["estado"]] = round(float(r["total"] or 0), 2)
    return {
        "ingresos": round(ingresos, 2),
        "gastos": round(gastos, 2),
        "margen": round(ingresos - gastos, 2),
        "iva_repercutido": round(float(iva_rep), 2),
        "iva_soportado": round(float(iva_sop), 2),
        "iva_neto": round(float(iva_rep) - float(iva_sop), 2),
        "tesoreria": round(float(bancos), 2),
        "facturas": fact,
    }




@router.get("/api/contabilidad/cierre")


def contabilidad_cierre_get():
    return {"cierre_fecha": _get_config("cierre_fecha", "")}




@router.post("/api/contabilidad/cierre")


def contabilidad_cierre(req: dict, conn = Depends(get_conn)):
    fecha = (req.get("fecha") or "").strip()
    if not fecha:
        raise HTTPException(status_code=400, detail={"error": "Indica la fecha de cierre."})
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        ("cierre_fecha", fecha),
    )
    conn.commit()
    return {"ok": True, "cierre_fecha": fecha}




@router.get("/api/contabilidad/tesoreria-prevision")


def contabilidad_tesoreria_prevision(conn = Depends(get_conn)):
    saldo = conn.execute("SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')").fetchone()["v"] or 0
    fact = conn.execute("SELECT fecha, COALESCE(SUM(total),0) AS t FROM facturas WHERE estado != 'cobrada' GROUP BY fecha").fetchall()
    gast = conn.execute("SELECT fecha, COALESCE(SUM(importe),0) AS t FROM gastos WHERE COALESCE(pagado,false)=false AND COALESCE(importe,0)>0 GROUP BY fecha").fetchall()
    hoy = datetime.date.today()
    months = []
    for i in range(3):
        y = hoy.year + (hoy.month + i - 1) // 12
        m = (hoy.month + i - 1) % 12 + 1
        months.append(f"{y:04d}-{m:02d}")
    por_mes = {m: {"entradas": 0.0, "salidas": 0.0} for m in months}
    sin_fecha = {"entradas": 0.0, "salidas": 0.0}
    for r in fact:
        mes = (r["fecha"] or "")[:7]
        if mes in por_mes: por_mes[mes]["entradas"] += float(r["t"] or 0)
        else: sin_fecha["entradas"] += float(r["t"] or 0)
    for r in gast:
        mes = (r["fecha"] or "")[:7]
        if mes in por_mes: por_mes[mes]["salidas"] += float(r["t"] or 0)
        else: sin_fecha["salidas"] += float(r["t"] or 0)
    saldo_proy = float(saldo)
    prevision = []
    for m in months:
        e = round(por_mes[m]["entradas"], 2); s = round(por_mes[m]["salidas"], 2)
        saldo_proy += e - s
        prevision.append({"mes": m, "entradas": e, "salidas": s, "saldo_proyectado": round(saldo_proy, 2)})
    return {
        "saldo": round(float(saldo), 2),
        "entradas_pendientes": round(sum(por_mes[m]["entradas"] for m in months) + sin_fecha["entradas"], 2),
        "salidas_pendientes": round(sum(por_mes[m]["salidas"] for m in months) + sin_fecha["salidas"], 2),
        "sin_fecha": {"entradas": round(sin_fecha["entradas"], 2), "salidas": round(sin_fecha["salidas"], 2)},
        "prevision": prevision,
    }




@router.get("/api/contabilidad/modelo347")


def contabilidad_modelo347(anio: str = "", conn = Depends(get_conn)):
    if not anio:
        anio = str(datetime.date.today().year)
    cli = conn.execute(
        "SELECT COALESCE(NULLIF(cliente_nombre,''),'Sin nombre') AS tercero, SUM(total) AS total "
        "FROM facturas WHERE substr(fecha,1,4)=? GROUP BY 1 ORDER BY total DESC", (anio,)
    ).fetchall()
    prov = conn.execute(
        "SELECT COALESCE(NULLIF(p.nombre,''),'Sin nombre') AS tercero, SUM(g.importe) AS total "
        "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id=p.id "
        "WHERE substr(g.fecha,1,4)=? AND COALESCE(g.proveedor_id,0) > 0 GROUP BY 1 ORDER BY total DESC", (anio,)
    ).fetchall()
    LIMITE = 3005.06
    clientes = [{"tercero": r["tercero"], "total": round(float(r["total"] or 0), 2)} for r in cli if float(r["total"] or 0) > LIMITE]
    proveedores = [{"tercero": r["tercero"], "total": round(float(r["total"] or 0), 2)} for r in prov if float(r["total"] or 0) > LIMITE]
    return {"anio": anio, "limite": LIMITE, "clientes": clientes, "proveedores": proveedores,
            "total_clientes": round(sum(x["total"] for x in clientes), 2),
            "total_proveedores": round(sum(x["total"] for x in proveedores), 2)}




@router.get("/api/contabilidad/tesoreria")


def contabilidad_tesoreria(conn = Depends(get_conn)):
    saldo = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta IN ('570','572')"
    ).fetchone()["v"] or 0
    gpend = conn.execute(
        "SELECT g.id, g.fecha, g.categoria, g.concepto, g.importe, g.iva, g.retencion, p.nombre AS proveedor "
        "FROM gastos g LEFT JOIN proveedores p ON g.proveedor_id=p.id "
        "WHERE COALESCE(g.pagado, false) = false AND COALESCE(g.importe,0) > 0 "
        "ORDER BY g.fecha DESC LIMIT 100"
    ).fetchall()
    fpend = conn.execute(
        "SELECT id, numero, fecha, cliente_nombre, total FROM facturas WHERE estado != 'cobrada' ORDER BY fecha DESC"
    ).fetchall()
    movs = conn.execute(
        "SELECT a.id, a.fecha, a.concepto, a.origen, "
        "COALESCE(SUM(CASE WHEN p.cuenta IN ('570','572') THEN p.debe - p.haber ELSE 0 END),0) AS importe "
        "FROM asientos a JOIN apuntes p ON p.asiento_id=a.id "
        "WHERE a.origen IN ('cobro','pago') "
        "GROUP BY a.id, a.fecha, a.concepto, a.origen "
        "ORDER BY a.fecha DESC, a.id DESC LIMIT 100"
    ).fetchall()
    return {
        "saldo": round(float(saldo), 2),
        "gastos_pendientes": [dict(r) for r in gpend],
        "facturas_pendientes": [dict(r) for r in fpend],
        "movimientos": [dict(r) for r in movs],
    }




@router.get("/api/contabilidad/impuestos")


def contabilidad_impuestos(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    conds.append("p.cuenta IN ('477','472')")
    where = " WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"SELECT substr(a.fecha,1,7) AS mes, p.cuenta, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id {where} "
        f"GROUP BY substr(a.fecha,1,7), p.cuenta ORDER BY mes",
        params,
    ).fetchall()
    trim = {}
    for r in rows:
        mes = r["mes"]
        y, m = int(mes[:4]), int(mes[5:7])
        q = (m - 1) // 3 + 1
        key = f"{y}-T{q}"
        d = trim.setdefault(key, {"repercutido": 0.0, "soportado": 0.0})
        if r["cuenta"] == "477":
            d["repercutido"] += float(r["haber"] or 0) - float(r["debe"] or 0)
        else:
            d["soportado"] += float(r["debe"] or 0) - float(r["haber"] or 0)
    trimestres = []
    for k in sorted(trim.keys()):
        d = trim[k]
        trimestres.append({
            "trimestre": k, "repercutido": round(d["repercutido"], 2),
            "soportado": round(d["soportado"], 2),
            "a_ingresar": round(d["repercutido"] - d["soportado"], 2),
        })
    ret = conn.execute(
        "SELECT SUM(haber)-SUM(debe) AS v FROM apuntes WHERE cuenta='4751'"
    ).fetchone()["v"] or 0
    tot_rep = sum(t["repercutido"] for t in trimestres)
    tot_sop = sum(t["soportado"] for t in trimestres)
    return {
        "trimestres": trimestres,
        "total_repercutido": round(tot_rep, 2),
        "total_soportado": round(tot_sop, 2),
        "a_ingresar_total": round(tot_rep - tot_sop, 2),
        "retenciones": round(float(ret), 2),
    }






@router.get("/api/contabilidad/inmovilizado")


def contabilidad_inmovilizado(conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, categoria, matricula, marca, modelo, coste_adquisicion, fecha_adquisicion, vida_util, valor_residual "
        "FROM vehiculos WHERE COALESCE(coste_adquisicion,0) > 0 ORDER BY matricula"
    ).fetchall()
    acumulado = conn.execute(
        "SELECT SUM(debe)-SUM(haber) AS v FROM apuntes WHERE cuenta='281'"
    ).fetchone()["v"] or 0
    adq_docs = {r["documento"] for r in conn.execute(
        "SELECT documento FROM asientos WHERE origen='adquisicion'"
    ).fetchall()}
    hoy = datetime.date.today()
    items = []
    for r in rows:
        coste = float(r["coste_adquisicion"] or 0)
        residual = float(r["valor_residual"] or 0)
        vida = int(r["vida_util"] or 5)
        anual = round((coste - residual) / vida, 2) if vida > 0 else 0.0
        fecha = r["fecha_adquisicion"] or ""
        am_acum = 0.0
        if fecha and anual > 0:
            try:
                d0 = datetime.date.fromisoformat(fecha[:10])
                meses = max(0, (hoy.year - d0.year) * 12 + (hoy.month - d0.month))
                am_acum = round(min(anual * meses / 12.0, coste - residual), 2)
            except Exception:
                am_acum = 0.0
        items.append({
            "id": r["id"], "matricula": r["matricula"] or r["id"], "categoria": r["categoria"],
            "marca": r["marca"] or "", "modelo": r["modelo"] or "",
            "coste": coste, "residual": residual, "vida_util": vida,
            "fecha": fecha, "anual": anual, "acumulado": am_acum, "vnc": round(coste - am_acum, 2),
            "tiene_adquisicion": f"adquisicion-{r['id']}" in adq_docs,
        })
    return {"vehiculos": items, "amort_acumulada_contable": round(float(acumulado), 2), "anio": str(hoy.year)}




@router.post("/api/contabilidad/inmovilizado/{veh_id}")


def set_amortizacion(veh_id: str, a: AmortizacionRequest, conn = Depends(get_conn)):
    conn.execute(
        "UPDATE vehiculos SET coste_adquisicion=?, fecha_adquisicion=?, vida_util=?, valor_residual=? WHERE id=?",
        (a.coste_adquisicion, a.fecha_adquisicion, a.vida_util, a.valor_residual, veh_id),
    )
    conn.commit()
    return {"ok": True}




@router.post("/api/contabilidad/inmovilizado/{veh_id}/adquisicion")


def registrar_adquisicion(veh_id: str, conn = Depends(get_conn)):
    v = conn.execute("SELECT * FROM vehiculos WHERE id=?", (veh_id,)).fetchone()
    if not v:
        raise HTTPException(status_code=404, detail={"error": "Vehículo no encontrado."})
    coste = float(v["coste_adquisicion"] or 0)
    if coste <= 0:
        raise HTTPException(status_code=400, detail={"error": "El vehículo no tiene coste de adquisición."})
    exist = conn.execute(
        "SELECT id FROM asientos WHERE origen='adquisicion' AND documento=?", (f"adquisicion-{veh_id}",)
    ).fetchone()
    if exist:
        raise HTTPException(status_code=409, detail={"error": "Este vehículo ya tiene asiento de adquisición."})
    fecha = (v["fecha_adquisicion"] or "")[:10] or datetime.date.today().isoformat()
    nombre = v["matricula"] or veh_id
    aid = _post_asiento(
        fecha, f"Adquisición {nombre}",
        [("218", coste, 0, f"Adquisición {nombre}"),
         ("572", 0, coste, f"Pago adquisición {nombre}")],
        origen="adquisicion", documento=f"adquisicion-{veh_id}", conn=conn,
    )
    conn.commit()
    return {"ok": True, "asiento_id": aid, "coste": coste}




@router.post("/api/contabilidad/amortizar")


def contabilidad_amortizar(periodo: str = "", conn = Depends(get_conn)):
    hoy = datetime.date.today()
    if not periodo:
        periodo = f"{hoy.year:04d}-{hoy.month:02d}"  # mes actual por defecto
    es_anual = len(periodo) == 4
    if es_anual:
        fecha = f"{periodo}-12-31"
    else:
        y, m = int(periodo[:4]), int(periodo[5:7])
        _dias = {1:31,2:28,3:31,4:30,5:31,6:30,7:31,8:31,9:30,10:31,11:30,12:31}
        last = _dias[m]
        if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
            last = 29
        fecha = f"{y:04d}-{m:02d}-{last:02d}"
    exist = conn.execute(
        "SELECT id FROM asientos WHERE origen='amortizacion' AND documento=?", (f"amortizacion-{periodo}",)
    ).fetchone()
    if exist:
        raise HTTPException(status_code=409, detail={"error": f"Ya hay amortización para {periodo}."})
    rows = conn.execute(
        "SELECT id, matricula, coste_adquisicion, valor_residual, vida_util FROM vehiculos WHERE COALESCE(coste_adquisicion,0) > 0"
    ).fetchall()
    lineas = []
    total = 0.0
    for r in rows:
        coste = float(r["coste_adquisicion"] or 0)
        residual = float(r["valor_residual"] or 0)
        vida = int(r["vida_util"] or 5)
        anual = round((coste - residual) / vida, 2) if vida > 0 else 0.0
        cuota = anual if es_anual else round(anual / 12.0, 2)
        if cuota > 0:
            nombre = r["matricula"] or r["id"]
            lineas.append(("681", cuota, 0, f"Amortización {nombre}"))
            lineas.append(("281", 0, cuota, f"Amortización {nombre}"))
            total += cuota
    if not lineas:
        raise HTTPException(status_code=400, detail={"error": "No hay vehículos con datos de amortización."})
    aid = _post_asiento(fecha, f"Amortización {'anual' if es_anual else 'mensual'} {periodo}", lineas,
                        origen="amortizacion", documento=f"amortizacion-{periodo}", conn=conn)
    conn.commit()
    return {"ok": True, "asiento_id": aid, "total": round(total, 2), "periodo": periodo, "mensual": not es_anual}




@router.get("/api/contabilidad/explotacion")


def contabilidad_explotacion(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    conds, params = [], []
    if desde:
        conds.append("a.fecha >= ?"); params.append(desde)
    if hasta:
        conds.append("a.fecha <= ?"); params.append(hasta)
    conds.append("c.tipo IN ('gasto','ingreso')")
    where = " WHERE " + " AND ".join(conds)
    rows = conn.execute(
        f"SELECT substr(a.fecha,1,7) AS mes, c.tipo, SUM(p.debe) AS debe, SUM(p.haber) AS haber "
        f"FROM apuntes p JOIN asientos a ON a.id=p.asiento_id JOIN cuentas c ON c.codigo=p.cuenta "
        f"{where} GROUP BY substr(a.fecha,1,7), c.tipo ORDER BY mes", params,
    ).fetchall()
    by_mes = {}
    for r in rows:
        d = by_mes.setdefault(r["mes"], {"ingresos": 0.0, "gastos": 0.0})
        if r["tipo"] == "ingreso":
            d["ingresos"] += float(r["haber"] or 0) - float(r["debe"] or 0)
        else:
            d["gastos"] += float(r["debe"] or 0) - float(r["haber"] or 0)
    meses = []
    for m in sorted(by_mes):
        d = by_mes[m]
        meses.append({"mes": m, "ingresos": round(d["ingresos"], 2), "gastos": round(d["gastos"], 2),
                      "resultado": round(d["ingresos"] - d["gastos"], 2)})
    return {"meses": meses, "total_ingresos": round(sum(x["ingresos"] for x in meses), 2),
            "total_gastos": round(sum(x["gastos"] for x in meses), 2),
            "total_resultado": round(sum(x["resultado"] for x in meses), 2)}




@router.get("/api/contabilidad/explotacion-vehiculos")


def contabilidad_explotacion_vehiculos(conn = Depends(get_conn)):
    """Explotación por vehículo: ingresos y gastos directos por tractora + gastos generales repartibles."""
    trips = conn.execute(
        "SELECT COALESCE(NULLIF(terminal,''),'General') AS veh, COALESCE(SUM(precio),0) AS ingresos, "
        "COALESCE(SUM(COALESCE(km_real, km_total)),0) AS km, COALESCE(SUM(km_vacio),0) AS km_vacio FROM trips GROUP BY 1"
    ).fetchall()
    gastos = conn.execute(
        "SELECT COALESCE(NULLIF(terminal,''),'General') AS veh, COALESCE(SUM(importe),0) AS gastos FROM gastos GROUP BY 1"
    ).fetchall()
    by = {}
    for r in trips:
        by[r["veh"]] = {"vehiculo": r["veh"], "ingresos": float(r["ingresos"] or 0),
                        "km": float(r["km"] or 0), "km_vacio": float(r["km_vacio"] or 0), "gastos": 0.0}
    for r in gastos:
        v = by.setdefault(r["veh"], {"vehiculo": r["veh"], "ingresos": 0.0, "km": 0.0, "km_vacio": 0.0, "gastos": 0.0})
        v["gastos"] = float(r["gastos"] or 0)
    generales = by.pop("General", {"ingresos": 0.0, "km": 0.0, "km_vacio": 0.0, "gastos": 0.0})
    vehiculos = []
    for v in by.values():
        vehiculos.append({
            "vehiculo": v["vehiculo"], "ingresos": round(v["ingresos"], 2),
            "gastos": round(v["gastos"], 2), "km": round(v["km"], 1),
            "resultado": round(v["ingresos"] - v["gastos"], 2),
        })
    vehiculos.sort(key=lambda x: -x["ingresos"])
    total_km = round(sum(v["km"] for v in vehiculos), 1)
    return {"vehiculos": vehiculos, "gastos_generales": round(float(generales["gastos"] or 0), 2), "total_km": total_km}




@router.get("/api/contabilidad/reconciliacion-km")


def contabilidad_reconciliacion_km(desde: str = "", hasta: str = "", conn = Depends(get_conn)):
    """Reconciliación de km por vehículo: Δ odómetro (telemetría) vs Σ km de viajes.

    km_odometro = odómetro_fin - odómetro_inicio en el periodo (MIN/MAX odometer_km).
    km_viajes   = Σ km_efectivo (COALESCE(km_real, km_total)) de los viajes del periodo.
    km_vacio    = km_odometro - km_viajes (km huérfano: maniobras/reposicionamiento/viajes sin registrar).
    """

    conds_t = ["COALESCE(NULLIF(t.terminal,''),'') <> ''"]
    params_t = []
    if desde:
        conds_t.append("substr(COALESCE(t.fecha_actualizacion, t.creado),1,10) >= ?"); params_t.append(desde)
    if hasta:
        conds_t.append("substr(COALESCE(t.fecha_actualizacion, t.creado),1,10) <= ?"); params_t.append(hasta)
    where_t = " WHERE " + " AND ".join(conds_t)

    conds_o = ["odometer_km IS NOT NULL"]
    params_o = []
    if desde:
        conds_o.append("time >= ?::timestamptz"); params_o.append(desde + "T00:00:00Z")
    if hasta:
        conds_o.append("time <= ?::timestamptz"); params_o.append(hasta + "T23:59:59Z")
    where_o = " WHERE " + " AND ".join(conds_o)

    viajes = conn.execute(
        f"SELECT t.terminal AS veh, COALESCE(SUM(COALESCE(t.km_real, t.km_total)),0) AS km "
        f"FROM trips t{where_t} GROUP BY t.terminal", params_t,
    ).fetchall()

    odos = conn.execute(
        f"SELECT vehiculo_id AS veh, MIN(odometer_km) AS odo_inicio, MAX(odometer_km) AS odo_fin "
        f"FROM telemetria.posiciones_gps{where_o} GROUP BY vehiculo_id", params_o,
    ).fetchall()

    by = {}
    for r in odos:
        by[r["veh"]] = {
            "vehiculo": r["veh"],
            "odo_inicio": float(r["odo_inicio"] or 0),
            "odo_fin": float(r["odo_fin"] or 0),
            "km_odometro": round(float(r["odo_fin"] or 0) - float(r["odo_inicio"] or 0), 1),
            "km_viajes": 0.0,
        }
    for r in viajes:
        v = by.setdefault(r["veh"], {"vehiculo": r["veh"], "odo_inicio": None, "odo_fin": None, "km_odometro": 0.0})
        v["km_viajes"] = round(float(r["km"] or 0), 1)

    vehiculos = []
    for v in by.values():
        km_odo = round(v["km_odometro"] or 0, 1)
        km_via = round(v["km_viajes"] or 0, 1)
        km_vacio = round(km_odo - km_via, 1)
        pct_vacio = round((km_vacio / km_odo * 100), 1) if km_odo > 0 else 0.0
        vehiculos.append({
            "vehiculo": v["vehiculo"],
            "odo_inicio": v["odo_inicio"],
            "odo_fin": v["odo_fin"],
            "km_odometro": km_odo,
            "km_viajes": km_via,
            "km_vacio": km_vacio,
            "pct_vacio": pct_vacio,
        })
    vehiculos.sort(key=lambda x: -(x["km_odometro"]))
    total = {
        "km_odometro": round(sum(v["km_odometro"] for v in vehiculos), 1),
        "km_viajes": round(sum(v["km_viajes"] for v in vehiculos), 1),
        "km_vacio": round(sum(v["km_vacio"] for v in vehiculos), 1),
    }
    return {"vehiculos": vehiculos, "total": total, "desde": desde, "hasta": hasta}




@router.get("/api/contabilidad/auditoria")


def contabilidad_auditoria(limite: int = 200, user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Pista de auditoría contable (solo administradores): quién hizo qué y cuándo."""
    limite = max(1, min(int(limite), 1000))
    rows = conn.execute(
        "SELECT id, tabla, registro_id, accion, usuario, antes, despues, ts "
        "FROM audit_log ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()
    return {"auditoria": [dict(r) for r in rows]}




@router.get("/api/contabilidad/facturas")


def contabilidad_facturas(conn = Depends(get_conn)):
    rows = conn.execute("SELECT * FROM facturas ORDER BY fecha DESC, id DESC").fetchall()
    return {"facturas": [dict(r) for r in rows]}




@router.get("/api/contabilidad/facturables")


def contabilidad_facturables(conn = Depends(get_conn)):
    rows = conn.execute(
        "SELECT t.id, t.referencia, t.cliente, t.precio, t.iva, t.origen, t.destino, t.creado, t.estado "
        "FROM trips t "
        "WHERE NOT EXISTS (SELECT 1 FROM facturas f WHERE f.trip_id=t.id) "
        "AND NOT EXISTS (SELECT 1 FROM factura_lineas fl WHERE fl.trip_id=t.id) "
        "AND COALESCE(t.precio,0) > 0 "
        "AND LOWER(COALESCE(t.estado,'')) NOT IN ('sin_asignar','cancelado','canceled','rechazado','refused','error') "
        "ORDER BY t.creado DESC LIMIT 200"
    ).fetchall()
    return {"viajes": [dict(r) for r in rows]}








def _desglose_costes(conn, trip_id):
    """Desglose exacto de costes de un viaje: peajes PTV + gastos por categoría (trip_id)."""
    desglose = []
    peaje = 0.0
    if trip_id:
        t = conn.execute("SELECT peaje_estimado FROM trips WHERE id=?", (trip_id,)).fetchone()
        if t:
            peaje = round(float(t["peaje_estimado"] or 0), 2)
    if peaje > 0:
        desglose.append({"concepto": "Peajes PTV", "importe": peaje})
    if trip_id:
        for g in conn.execute(
            "SELECT categoria, COALESCE(SUM(importe), 0) AS total FROM gastos "
            "WHERE trip_id=? GROUP BY categoria ORDER BY total DESC",
            (trip_id,),
        ).fetchall():
            imp = round(float(g["total"] or 0), 2)
            if imp > 0:
                etiqueta = (g["categoria"] or "Otros").strip().title() or "Otros"
                desglose.append({"concepto": etiqueta, "importe": imp})
    return desglose




def _factura_numero(conn, fecha):
    fr = conn.execute("SELECT numero FROM facturas WHERE substr(fecha,1,4)=?", (fecha[:4],)).fetchall()
    max_n = 0
    for r in fr:
        m = re.match(r"^F-\d{4}-(\d+)$", r["numero"] or "")
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"F-{fecha[:4]}-{max_n + 1:04d}"




def _crear_factura(conn, trips, cliente_id, cliente_nombre, fecha):
    """Crea una factura (y sus líneas) para una lista de viajes del mismo cliente.
    Devuelve (factura_id, numero, base, cuota, total)."""
    base = round(sum(float(t["precio"] or 0) for t in trips), 2)
    iva = round(float(trips[0]["iva"] or 21), 2)
    cuota = round(base * iva / 100.0, 2)
    total = round(base + cuota, 2)
    numero = _factura_numero(conn, fecha)
    asiento_id = _post_asiento(
        fecha, f"Factura {numero} - {cliente_nombre or 'varios'}",
        [("430", total, 0, f"Factura {numero}"),
         ("705", 0, base, f"Ventas {numero}"),
         ("477", 0, cuota, f"IVA repercutido {numero}")],
        origen="viaje", documento=numero, conn=conn,
    )
    cur = conn.execute(
        "INSERT INTO facturas (numero, fecha, trip_id, cliente_id, cliente_nombre, base, iva, cuota_iva, total, estado, asiento_id, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (numero, fecha, trips[0]["id"] if len(trips) == 1 else None, cliente_id, cliente_nombre,
         base, iva, cuota, total, "emitida", asiento_id, datetime.datetime.utcnow().isoformat() + "Z"),
    )
    factura_id = cur.fetchone()["id"]
    for t in trips:
        tbase = round(float(t["precio"] or 0), 2)
        tcuota = round(tbase * iva / 100.0, 2)
        ttotal = round(tbase + tcuota, 2)
        concepto = f"{t['origen'] or ''} → {t['destino'] or ''}".strip().strip("→").strip() or t["id"]
        conn.execute(
            "INSERT INTO factura_lineas (factura_id, trip_id, concepto, base, iva, cuota_iva, total) VALUES (?,?,?,?,?,?,?)",
            (factura_id, t["id"], concepto, tbase, iva, tcuota, ttotal),
        )
        conn.execute("UPDATE trips SET factura=? WHERE id=?", (numero, t["id"]))
    return factura_id, numero, base, cuota, total




@router.post("/api/contabilidad/facturas/agrupada")


def contabilidad_factura_agrupada(req: dict, conn = Depends(get_conn)):
    trip_ids = [t for t in (req.get("trip_ids") or []) if t]
    if not trip_ids:
        raise HTTPException(status_code=400, detail={"error": "Selecciona al menos un viaje para facturar."})
    ph = ",".join("?" for _ in trip_ids)
    trips = conn.execute(f"SELECT * FROM trips WHERE id IN ({ph}) ORDER BY creado", trip_ids).fetchall()
    if len(trips) != len(set(trip_ids)):
        raise HTTPException(status_code=404, detail={"error": "Algún viaje seleccionado no existe."})
    clientes = {t["cliente_id"] for t in trips}
    if len(clientes) > 1:
        raise HTTPException(status_code=409, detail={"error": "Todos los viajes deben ser del mismo cliente para agruparlos."})
    for t in trips:
        if conn.execute("SELECT 1 FROM facturas WHERE trip_id=?", (t["id"],)).fetchone() or \
           conn.execute("SELECT 1 FROM factura_lineas WHERE trip_id=?", (t["id"],)).fetchone():
            raise HTTPException(status_code=409, detail={"error": f"El viaje {t['id']} ya tiene factura."})
        if float(t["precio"] or 0) <= 0:
            raise HTTPException(status_code=400, detail={"error": f"El viaje {t['id']} no tiene precio."})
    cliente_nombre = trips[0]["cliente"] or ""
    cliente_id = trips[0]["cliente_id"]
    fecha = (trips[0]["creado"] or "")[:10] or datetime.date.today().isoformat()
    try:
        factura_id, numero, base, cuota, total = _crear_factura(conn, trips, cliente_id, cliente_nombre, fecha)
    except ValueError as e:
        conn.rollback(); conn.close()
        raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit(); conn.close()
    return {"ok": True, "factura": numero, "total": total, "factura_id": factura_id, "viajes": len(trips)}




@router.post("/api/contabilidad/facturas/{trip_id}")


def contabilidad_generar_factura(trip_id: str, conn = Depends(get_conn)):
    trip = conn.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail={"error": "Viaje no encontrado."})
    if conn.execute("SELECT 1 FROM facturas WHERE trip_id=?", (trip_id,)).fetchone() or \
       conn.execute("SELECT 1 FROM factura_lineas WHERE trip_id=?", (trip_id,)).fetchone():
        raise HTTPException(status_code=409, detail={"error": "Este viaje ya tiene factura."})
    if float(trip["precio"] or 0) <= 0:
        raise HTTPException(status_code=400, detail={"error": "El viaje no tiene precio."})
    fecha = (trip["creado"] or "")[:10] or datetime.date.today().isoformat()
    try:
        # Subcontratación: generar el gasto (624/410) antes de calcular costes/margen.
        if trip["subcontratado"]:
            _gasto_subcontrata(conn, trip, fecha)
        factura_id, numero, base, cuota, total = _crear_factura(
            conn, [trip], trip["cliente_id"], trip["cliente"] or "", fecha)
    except ValueError as e:
        conn.rollback(); conn.close()
        raise HTTPException(status_code=400, detail={"error": str(e)})
    conn.commit(); conn.close()
    return {"ok": True, "factura": numero, "total": total, "factura_id": factura_id}




@router.get("/api/contabilidad/borradores")


def contabilidad_borradores(user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Facturas autogeneradas en estado Borrador, listas para validar y emitir."""
    rows = conn.execute(
        "SELECT f.id, f.numero, f.fecha, f.trip_id, f.cliente_nombre, f.base, f.iva, "
        "f.cuota_iva, f.total, f.coste, f.margen, f.creado, t.origen, t.destino "
        "FROM facturas f LEFT JOIN trips t ON t.id = f.trip_id "
        "WHERE f.estado='Borrador' ORDER BY f.creado DESC"
    ).fetchall()
    salida = []
    for r in rows:
        d = dict(r)
        d["desglose"] = _desglose_costes(conn, r["trip_id"])
        salida.append(d)
    return {"borradores": salida}




@router.get("/api/contabilidad/liquidaciones")


def contabilidad_liquidaciones(user: dict = Depends(require_role(["admin"])), conn = Depends(get_conn)):
    """Liquidaciones de conductores autogeneradas (modelo variable por km)."""
    rows = conn.execute(
        "SELECT l.id, c.nombre AS conductor, l.viaje_id, l.fecha, "
        "COALESCE(t.km_total, 0) AS km_total, COALESCE(c.tarifa_km, 0) AS tarifa, "
        "l.importe, (CASE WHEN l.pagado THEN 'Pagada' ELSE 'Pendiente' END) AS estado "
        "FROM liquidaciones l "
        "LEFT JOIN conductores c ON l.conductor_id = c.id "
        "LEFT JOIN trips t ON l.viaje_id = t.id "
        "WHERE l.conductor_id IS NOT NULL "
        "ORDER BY l.fecha DESC, l.id DESC"
    ).fetchall()
    return {"liquidaciones": [dict(r) for r in rows]}




@router.post("/api/contabilidad/borradores/{factura_id}/emitir")


def contabilidad_emitir_borrador(factura_id: int,
                                 user: dict = Depends(require_role(["admin"]))):
    """Valida y emite un borrador: asigna número, publica el asiento y marca 'emitida'."""
    conn = _db()
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Borrador no encontrado"})
    if f["estado"] != "Borrador":
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "La factura ya no está en borrador"})
    fecha = (f["fecha"] or "")[:10] or datetime.date.today().isoformat()
    numero = _factura_numero(conn, fecha)
    total = float(f["total"] or 0)
    base = float(f["base"] or 0)
    cuota = float(f["cuota_iva"] or 0)
    asiento_id = _post_asiento(
        fecha, f"Factura {numero} - {f['cliente_nombre'] or 'varios'}",
        [("430", total, 0, f"Factura {numero}"),
         ("705", 0, base, f"Ventas {numero}"),
         ("477", 0, cuota, f"IVA repercutido {numero}")],
        origen="viaje", documento=numero, conn=conn,
    )
    conn.execute(
        "UPDATE facturas SET estado='emitida', numero=?, asiento_id=? WHERE id=?",
        (numero, asiento_id, factura_id),
    )
    if f["trip_id"]:
        conn.execute("UPDATE trips SET factura=? WHERE id=?", (numero, f["trip_id"]))
    conn.commit()
    conn.close()
    return {"ok": True, "factura": numero, "factura_id": factura_id}








def _enviar_email(para, asunto, cuerpo, adjuntos=None):
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.application import MIMEApplication

    host = _get_config("smtp_host", "")
    port = int(_get_config("smtp_port", "587") or 587)
    user = _get_config("smtp_user", "")
    pwd = _get_config("smtp_password", "")
    from_addr = _get_config("smtp_from", "") or user
    # Preferir la config de integración (integracion_valores) si existe.
    try:
        with _db() as conn:
            smtp = _valores_proveedor(conn, "smtp")
        host = smtp.get("host", "") or host
        port = int(smtp.get("port") or port)
        user = smtp.get("user", "") or user
        pwd = smtp.get("password", "") or pwd
        from_addr = smtp.get("from", "") or user
    except Exception:
        pass
    if not host or not user:
        raise HTTPException(status_code=400, detail={"error": "Configura la cuenta de correo saliente (SMTP) en Configuración."})

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = para
    msg["Subject"] = asunto
    msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
    for nombre, contenido in (adjuntos or []):
        part = MIMEApplication(contenido, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=nombre)
        msg.attach(part)
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo(); s.starttls(); s.ehlo()
        s.login(user, pwd)
        s.sendmail(from_addr, [para], msg.as_string())




@router.get("/api/contabilidad/facturas/{factura_id}/pdf")


def factura_pdf(factura_id: int):
    pdf = _generar_factura_pdf(factura_id)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=factura_{factura_id}.pdf"})




@router.post("/api/contabilidad/facturas/{factura_id}/enviar")


def factura_enviar(factura_id: int, req: dict, conn = Depends(get_conn)):
    email_to = (req.get("email") or "").strip()
    if not email_to:
        raise HTTPException(status_code=400, detail={"error": "Indica el email del destinatario."})
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    pdf = _generar_factura_pdf(factura_id)
    asunto = req.get("asunto") or f"Factura {f['numero']}"
    cuerpo = req.get("cuerpo") or f"Adjuntamos la factura {f['numero']}."
    try:
        _enviar_email(email_to, asunto, cuerpo, [(f"factura_{f['numero']}.pdf", pdf)])
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": f"No se pudo enviar el email: {e}"})
    return {"ok": True}




@router.post("/api/contabilidad/facturas/{factura_id}/cobrar")


def contabilidad_cobrar_factura(factura_id: int, conn = Depends(get_conn)):
    f = conn.execute("SELECT * FROM facturas WHERE id=?", (factura_id,)).fetchone()
    if not f:
        raise HTTPException(status_code=404, detail={"error": "Factura no encontrada."})
    if f["estado"] == "cobrada":
        return {"ok": True, "ya_cobrada": True}
    total = round(float(f["total"] or 0), 2)
    fecha = (f["fecha"] or "")[:10] or datetime.date.today().isoformat()
    asiento_id = _post_asiento(
        fecha, f"Cobro factura {f['numero']}",
        [("572", total, 0, f"Cobro {f['numero']}"),
         ("430", 0, total, f"Cobro {f['numero']}")],
        origen="cobro", documento=f["numero"], conn=conn,
    )
    conn.execute("UPDATE facturas SET estado='cobrada' WHERE id=?", (factura_id,))
    conn.commit()
    return {"ok": True, "asiento_id": asiento_id}


