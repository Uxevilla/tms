"""Router de RRHH: empleados, nóminas, ausencias."""
import uuid
import datetime
import io

from fastapi import APIRouter, HTTPException

import config
from db import _db
from models import Empleado, Nomina, Ausencia, AusenciaPlanificada
import main as _m

router = APIRouter()

# ---------------------------------------------------------------------- #
# Recursos Humanos (RRHH): empleados, nóminas, ausencias
# ---------------------------------------------------------------------- #



@router.get("/api/empleados")
def list_empleados():
    conn = _db()
    rows = conn.execute("SELECT * FROM empleados ORDER BY fecha_baja IS NOT NULL, nombre, apellidos").fetchall()
    conn.close()
    for r in rows:
        r["activo"] = not r["fecha_baja"]
    return {"empleados": [dict(r) for r in rows]}


@router.post("/api/empleados")
def add_empleado(e: Empleado):
    if not (e.nombre or "").strip():
        raise HTTPException(status_code=400, detail={"error": "Indica el nombre del empleado."})
    conn = _db()
    eid = "EMP-" + uuid.uuid4().hex[:10].upper()
    conn.execute(
        "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, direccion, ciudad, cp, "
        "fecha_alta, fecha_baja, categoria, puesto, tipo_contrato, jornada, banco, iban, titular, "
        "salario_bruto, irpf, disponibilidad, motivo_no_dispo, convenio, observaciones, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, e.nombre, e.apellidos, e.dni, e.nss, e.email, e.telefono, e.direccion, e.ciudad, e.cp,
         e.fecha_alta, e.fecha_baja, e.categoria, e.puesto, e.tipo_contrato, e.jornada, e.banco, e.iban, e.titular,
         e.salario_bruto, e.irpf, e.disponibilidad, e.motivo_no_dispo, e.convenio, e.observaciones,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    if e.categoria == "Conductor":
        _m._sync_conductor(conn, {"id": eid, "nombre": e.nombre, "apellidos": e.apellidos, "dni": e.dni,
                               "telefono": e.telefono, "email": e.email, "fecha_baja": e.fecha_baja})
    conn.commit()
    conn.close()
    return {"ok": True, "id": eid}


@router.patch("/api/empleados/{emp_id}")
def upd_empleado(emp_id: str, body: dict):
    allow = ("nombre", "apellidos", "dni", "nss", "email", "telefono", "direccion", "ciudad", "cp",
             "fecha_alta", "fecha_baja", "categoria", "puesto", "tipo_contrato", "jornada",
             "banco", "iban", "titular", "salario_bruto", "irpf", "disponibilidad",
             "motivo_no_dispo", "convenio", "observaciones",
             "caducidad_carnet", "caducidad_cap", "caducidad_medica")
    fields = {k: body[k] for k in allow if k in body}
    if not fields:
        return {"ok": False, "error": "Sin campos editables"}
    for k in ("salario_bruto", "irpf"):
        if k in fields and fields[k] is not None:
            fields[k] = float(fields[k])
    conn = _db()
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE empleados SET {sets} WHERE id=?", (*fields.values(), emp_id))
    # Re-sincroniza el conductor local si se tocaron datos relevantes y es Conductor.
    if any(k in fields for k in ("nombre", "apellidos", "dni", "telefono", "email", "categoria", "fecha_baja")):
        row = conn.execute(
            "SELECT nombre, apellidos, dni, telefono, email, categoria, fecha_baja FROM empleados WHERE id=?",
            (emp_id,)).fetchone()
        if row and (row["categoria"] or "") == "Conductor":
            _m._sync_conductor(conn, {"id": emp_id, "nombre": row["nombre"], "apellidos": row["apellidos"],
                                   "dni": row["dni"], "telefono": row["telefono"], "email": row["email"],
                                   "fecha_baja": row["fecha_baja"]})
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/empleados/{emp_id}")
def del_empleado(emp_id: str):
    conn = _db()
    conn.execute("DELETE FROM empleados WHERE id=?", (emp_id,))
    conn.commit()
    conn.close()
    return {"ok": True}




def _calc_nomina(bruto, irpf_pct, ss_t_pct, ss_e_pct):
    ss_trabajador = round(bruto * ss_t_pct / 100.0, 2)
    ss_empresa = round(bruto * ss_e_pct / 100.0, 2)
    irpf = round(bruto * irpf_pct / 100.0, 2)
    neto = round(bruto - ss_trabajador - irpf, 2)
    coste = round(bruto + ss_empresa, 2)
    return ss_trabajador, ss_empresa, irpf, neto, coste


@router.get("/api/nominas")
def list_nominas(periodo: str = "", empleado_id: str = ""):
    conn = _db()
    conds, params = [], []
    if periodo: conds.append("n.periodo=?"); params.append(periodo)
    if empleado_id: conds.append("n.empleado_id=?"); params.append(empleado_id)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT n.*, e.nombre, e.apellidos, e.categoria FROM nominas n JOIN empleados e ON e.id=n.empleado_id "
        f"{where} ORDER BY n.periodo DESC, n.id DESC", params,
    ).fetchall()
    conn.close()
    return {"nominas": [dict(r) for r in rows]}


def _generar_nomina_pdf(nomina_id):
    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    conn = _db()
    n = conn.execute(
        "SELECT n.*, e.nombre, e.apellidos, e.dni, e.nss, e.categoria, e.puesto, e.iban "
        "FROM nominas n JOIN empleados e ON e.id=n.empleado_id WHERE n.id=?",
        (nomina_id,),
    ).fetchone()
    if not n:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Nómina no encontrada."})
    emp = _m._empresa()
    # Líneas de devengo extra (dietas/pernocta) desde lineas_nomina.
    dietas = conn.execute(
        "SELECT concepto, importe FROM lineas_nomina WHERE nomina_id=? AND tipo='devengo' ORDER BY id",
        (nomina_id,),
    ).fetchall()
    conn.close()

    def eur(v):
        n = f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{n} €"

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    styles = getSampleStyleSheet()
    normal = ParagraphStyle("normal", parent=styles["Normal"], fontSize=10, leading=14)
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=22, spaceAfter=0)

    story = []
    emp_nombre = emp.get("nombre") or "Mi empresa"
    emp_txt = [emp_nombre]
    if emp.get("cif"): emp_txt.append(f"CIF: {emp['cif']}")
    if emp.get("direccion"): emp_txt.append(emp["direccion"])
    if emp.get("cp") or emp.get("poblacion"): emp_txt.append(f"{emp.get('cp','')} {emp.get('poblacion','')}".strip())
    if emp.get("telefono"): emp_txt.append(f"Tel: {emp['telefono']}")
    emp_block = [Paragraph(x, normal) for x in emp_txt]
    nom_block = [Paragraph("NÓMINA", title), Paragraph(f"Periodo: {n['periodo']}", normal)]
    header = Table([[emp_block, nom_block]], colWidths=[doc.width*0.55, doc.width*0.45])
    header.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP"),
                                ("LEFTPADDING", (0,0), (-1,-1), 0), ("RIGHTPADDING", (0,0), (-1,-1), 0)]))
    story.append(header)
    story.append(Spacer(1, 10*mm))

    nombre_completo = f"{n['nombre']} {n['apellidos'] or ''}".strip()
    story.append(Paragraph(f"<b>Empleado:</b> {nombre_completo}", normal))
    if n.get("dni"): story.append(Paragraph(f"DNI: {n['dni']}", normal))
    if n.get("nss"): story.append(Paragraph(f"Afiliación SS: {n['nss']}", normal))
    if n.get("categoria") or n.get("puesto"): story.append(Paragraph(f"Categoría: {n['categoria']} — {n.get('puesto') or ''}", normal))
    story.append(Spacer(1, 8*mm))

    bruto = float(n["salario_bruto"] or 0)
    irpf_imp = float(n["irpf_importe"] or 0)
    ss_t = float(n["ss_trabajador"] or 0)
    ss_e = float(n["ss_empresa"] or 0)
    neto = float(n["neto"] or 0)
    coste = float(n["coste_empresa"] or 0)

    dietas_total = sum(float(d["importe"] or 0) for d in dietas)
    base = max(0.0, bruto - dietas_total)

    data = [["Concepto", "Devengos", "Deducciones"]]
    if dietas:
        data.append(["Salario base", eur(base), ""])
        for d in dietas:
            data.append([d["concepto"], eur(float(d["importe"] or 0)), ""])
    data.append(["Salario bruto", eur(bruto), ""])
    data.append([f"IRPF ({float(n['irpf_pct'] or 0):.1f}%)", "", eur(irpf_imp)])
    data.append([f"Seg. Social trabajador ({float(n['ss_trabajador_pct'] or 0):.2f}%)", "", eur(ss_t)])
    data.append(["LÍQUIDO A PERCIBIR", "", eur(neto)])
    t = Table(data, colWidths=[doc.width*0.46, doc.width*0.27, doc.width*0.27])
    t.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (1,1), (-1,-1), "RIGHT"),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f1f5f9")),
        ("LINEABOVE", (0,-1), (-1,-1), 1, colors.black),
    ]))
    story.append(t)
    story.append(Spacer(1, 8*mm))

    coste_data = [
        ["Coste para la empresa", ""],
        ["Seguridad Social a cargo de la empresa", eur(ss_e)],
        ["COSTE TOTAL EMPRESA", eur(coste)],
    ]
    ct = Table(coste_data, colWidths=[doc.width*0.6, doc.width*0.4])
    ct.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("LINEABOVE", (0,-1), (-1,-1), 1, colors.black),
    ]))
    story.append(ct)
    story.append(Spacer(1, 12*mm))

    if n.get("iban"):
        story.append(Paragraph(f"<b>IBAN:</b> {n['iban']}", normal))
    estado = f"{n.get('estado') or 'borrador'}" + (" · PAGADA" if n.get("pagado") else "")
    story.append(Paragraph(f"Estado: {estado}", normal))

    doc.build(story)
    return buf.getvalue()


@router.get("/api/nominas/{nomina_id}/pdf")
def nomina_pdf(nomina_id: int):
    pdf = _generar_nomina_pdf(nomina_id)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=nomina_{nomina_id}.pdf"})


@router.get("/api/nominas/{nomina_id}/lineas")
def nomina_lineas(nomina_id: int):
    """Líneas de devengo/deducción (dietas, pluses…) de una nómina."""
    conn = _db()
    lineas = conn.execute(
        "SELECT * FROM lineas_nomina WHERE nomina_id=? ORDER BY id", (nomina_id,)
    ).fetchall()
    conn.close()
    return {"ok": True, "nomina_id": nomina_id, "lineas": [dict(l) for l in lineas]}


@router.post("/api/nominas")
def add_nomina(n: Nomina):
    conn = _db()
    emp = conn.execute("SELECT salario_bruto, irpf FROM empleados WHERE id=?", (n.empleado_id,)).fetchone()
    bruto = n.salario_bruto if n.salario_bruto else (float(emp["salario_bruto"] or 0) if emp else 0.0)
    irpf = n.irpf_pct if n.irpf_pct else (float(emp["irpf"] or 15) if emp else 15.0)
    ss_t, ss_e, irpf_imp, neto, coste = _calc_nomina(bruto, irpf, n.ss_trabajador_pct, n.ss_empresa_pct)
    cur = conn.execute(
        "INSERT INTO nominas (empleado_id, periodo, salario_bruto, irpf_pct, ss_trabajador_pct, ss_empresa_pct, "
        "ss_trabajador, ss_empresa, irpf_importe, neto, coste_empresa, estado, pagado, contabilizado, notas, creado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
        (n.empleado_id, n.periodo, bruto, irpf, n.ss_trabajador_pct, n.ss_empresa_pct,
         ss_t, ss_e, irpf_imp, neto, coste, "borrador", False, False, n.notas,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    nid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"ok": True, "id": nid, "neto": neto, "coste_empresa": coste}


@router.patch("/api/nominas/{nomina_id}")
def upd_nomina(nomina_id: int, n: Nomina):
    conn = _db()
    ss_t, ss_e, irpf_imp, neto, coste = _calc_nomina(n.salario_bruto, n.irpf_pct, n.ss_trabajador_pct, n.ss_empresa_pct)
    conn.execute(
        "UPDATE nominas SET salario_bruto=?, irpf_pct=?, ss_trabajador_pct=?, ss_empresa_pct=?, "
        "ss_trabajador=?, ss_empresa=?, irpf_importe=?, neto=?, coste_empresa=?, notas=? WHERE id=?",
        (n.salario_bruto, n.irpf_pct, n.ss_trabajador_pct, n.ss_empresa_pct, ss_t, ss_e, irpf_imp, neto, coste, n.notas, nomina_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "neto": neto, "coste_empresa": coste}


@router.delete("/api/nominas/{nomina_id}")
def del_nomina(nomina_id: int):
    conn = _db()
    conn.execute("DELETE FROM asientos WHERE id IN (SELECT asiento_id FROM nominas WHERE id=?)", (nomina_id,))
    conn.execute("DELETE FROM nominas WHERE id=?", (nomina_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.post("/api/nominas/generar")
def generar_nominas(req: dict):
    periodo = (req.get("periodo") or "").strip()
    if not periodo:
        hoy = datetime.date.today()
        periodo = f"{hoy.year:04d}-{hoy.month:02d}"
    conn = _db()
    emps = conn.execute("SELECT * FROM empleados WHERE fecha_baja IS NULL OR fecha_baja=''").fetchall()
    creadas, saltadas = 0, 0
    for e in emps:
        exist = conn.execute("SELECT id FROM nominas WHERE empleado_id=? AND periodo=?", (e["id"], periodo)).fetchone()
        if exist:
            saltadas += 1
            continue
        bruto = float(e["salario_bruto"] or 0)
        irpf = float(e["irpf"] or 15)
        ss_t, ss_e, irpf_imp, neto, coste = _calc_nomina(bruto, irpf, 6.35, 30.0)
        conn.execute(
            "INSERT INTO nominas (empleado_id, periodo, salario_bruto, irpf_pct, ss_trabajador_pct, ss_empresa_pct, "
            "ss_trabajador, ss_empresa, irpf_importe, neto, coste_empresa, estado, pagado, contabilizado, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["id"], periodo, bruto, irpf, 6.35, 30.0, ss_t, ss_e, irpf_imp, neto, coste, "borrador", False, False,
             datetime.datetime.utcnow().isoformat() + "Z"),
        )
        creadas += 1
    conn.commit()
    conn.close()
    return {"ok": True, "periodo": periodo, "creadas": creadas, "saltadas": saltadas}


@router.post("/api/nominas/{nomina_id}/contabilizar")
def contabilizar_nomina(nomina_id: int):
    conn = _db()
    n = conn.execute("SELECT * FROM nominas WHERE id=?", (nomina_id,)).fetchone()
    if not n:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Nómina no encontrada."})
    if n["contabilizado"]:
        conn.close()
        raise HTTPException(status_code=409, detail={"error": "La nómina ya está contabilizada."})
    bruto = float(n["salario_bruto"] or 0)
    ss_e = float(n["ss_empresa"] or 0)
    ss_t = float(n["ss_trabajador"] or 0)
    irpf = float(n["irpf_importe"] or 0)
    neto = float(n["neto"] or 0)
    fecha = ((n["periodo"] or "") + "-28")[:10] if n["periodo"] else datetime.date.today().isoformat()
    aid = _m._post_asiento(
        fecha, f"Nómina {n['periodo']}",
        [("640", bruto, 0, "Sueldos y salarios"),
         ("642", ss_e, 0, "Seguridad Social empresa"),
         ("465", 0, neto, "Remuneraciones pendientes"),
         ("476", 0, round(ss_t + ss_e, 2), "Seguridad Social"),
         ("4751", 0, irpf, "Retención IRPF")],
        origen="nomina", documento=f"nomina-{nomina_id}", conn=conn,
    )
    conn.execute("UPDATE nominas SET contabilizado=TRUE, asiento_id=?, estado='emitida' WHERE id=?", (aid, nomina_id))
    conn.commit()
    conn.close()
    return {"ok": True, "asiento_id": aid}


@router.post("/api/nominas/{nomina_id}/pagar")
def pagar_nomina(nomina_id: int):
    conn = _db()
    n = conn.execute("SELECT * FROM nominas WHERE id=?", (nomina_id,)).fetchone()
    if not n:
        conn.close()
        raise HTTPException(status_code=404, detail={"error": "Nómina no encontrada."})
    if n["pagado"]:
        conn.close()
        return {"ok": True, "ya_pagada": True}
    neto = float(n["neto"] or 0)
    ss = round(float(n["ss_trabajador"] or 0) + float(n["ss_empresa"] or 0), 2)
    irpf = float(n["irpf_importe"] or 0)
    total = round(neto + ss + irpf, 2)
    fecha = datetime.date.today().isoformat()
    aid = _m._post_asiento(
        fecha, f"Pago nómina {n['periodo']}",
        [("465", neto, 0, "Pago remuneraciones"),
         ("476", ss, 0, "Pago Seguridad Social"),
         ("4751", irpf, 0, "Pago retención IRPF"),
         ("572", 0, total, "Pago nómina")],
        origen="nomina", documento=f"pago-nomina-{nomina_id}", conn=conn,
    )
    conn.execute("UPDATE nominas SET pagado=TRUE, fecha_pago=?, estado='pagada' WHERE id=?", (fecha, nomina_id))
    conn.commit()
    conn.close()
    return {"ok": True, "asiento_id": aid}


def _seed_demo(conn):
    """Inyecta datos demo realistas (vehículos, empleados, mantenimientos, alertas, nóminas).
    Idempotente: si ya existen empleados de seed, no hace nada."""
    # --- 0) Empresa (si está vacía, rellenar para que facturas/nóminas tengan cabecera) ---
    if not conn.execute("SELECT nombre FROM empresa WHERE id=1 AND COALESCE(nombre,'')<>''").fetchone():
        conn.execute(
            "INSERT INTO empresa (id, nombre, cif, direccion, poblacion, cp, pais, telefono, email, web, iva, iban) "
            "VALUES (1,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO UPDATE SET nombre=EXCLUDED.nombre, cif=EXCLUDED.cif, direccion=EXCLUDED.direccion, "
            "poblacion=EXCLUDED.poblacion, cp=EXCLUDED.cp, pais=EXCLUDED.pais, telefono=EXCLUDED.telefono, "
            "email=EXCLUDED.email, web=EXCLUDED.web, iva=EXCLUDED.iva, iban=EXCLUDED.iban",
            ("Transportes Eusebio S.L.", "B12345678", "Calle Logística 15, Nave 7", "Madrid", "28021", "ES",
             "912 345 678", "info@transportes-eusebio.es", "www.transportes-eusebio.es", 21,
             "ES00 0000 0000 0000 0000 0000"),
        )

    if conn.execute("SELECT 1 FROM empleados WHERE id LIKE ? LIMIT 1", ("EMP-SEED-%",)).fetchone():
        return {"seeded": False, "reason": "demo ya presente"}

    import datetime as _dt
    hoy = _dt.date.today()
    def d(offset):
        return (hoy + _dt.timedelta(days=offset)).isoformat()
    creado = _dt.datetime.utcnow().isoformat() + "Z"
    n_veh = n_emp = n_man = n_ale = n_nom = 0

    # --- 1) Vehículos (12 semirremolques + 4 turismos + 4 ligeros) ---
    semis = [
        ("R-1234-BC", "Lecitrailer", "Lona 13,60", 33), ("R-2345-BD", "Schmitz Cargobull", "Frigorífico", 33),
        ("R-3456-BE", "Krone", "Portacontenedor 40'", 2), ("R-4567-BF", "Lecitrailer", "Basculante", 0),
        ("R-5678-BG", "Schmitz Cargobull", "Lona 13,60", 33), ("R-6789-BH", "SOR Ibérica", "Góndola", 0),
        ("R-7890-BJ", "Krone", "Frigorífico", 33), ("R-8901-BK", "Lecitrailer", "Portacontenedor 20'", 1),
        ("R-9012-BL", "Schmitz Cargobull", "Lona 13,60", 33), ("R-1122-BM", "SOR Ibérica", "Cisterna", 0),
        ("R-2233-BN", "Krone", "Lona 13,60", 33), ("R-3344-BP", "Lecitrailer", "Frigorífico", 33),
    ]
    for i, (mat, marca, modelo, palets) in enumerate(semis):
        conn.execute(
            "INSERT INTO vehiculos (id, categoria, matricula, marca, modelo, anno, ejes, mma, clase_euro, "
            "capacidad_peso, capacidad_palets, fecha_caducidad_itv, seguro_compania, fecha_caducidad_seguro, "
            "tipo_tenencia, fecha_alta, cuota_mensual, km_actuales, fecha_proxima_revision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (mat, "semirremolque", mat, marca, modelo, 2016 + (i % 8), 3, 38000, "",
             24000, palets, d(-30 * (i % 5)), "Mapfre", d(60 + 40 * (i % 3)), "Propiedad", d(-3000), 0, 0,
             d(-10 + 40 * (i % 4))),
        )
        n_veh += 1
    turismos = [
        ("9876 XYZ", "Seat", "León", 148000), ("1122 JKL", "Volkswagen", "Golf", 96000),
        ("3344 MNP", "Renault", "Clio", 187000), ("5566 QRS", "Ford", "Focus", 75000),
    ]
    for i, (mat, marca, modelo, km) in enumerate(turismos):
        conn.execute(
            "INSERT INTO vehiculos (id, categoria, matricula, marca, modelo, anno, ejes, mma, clase_euro, "
            "capacidad_peso, capacidad_palets, fecha_caducidad_itv, seguro_compania, fecha_caducidad_seguro, "
            "tipo_tenencia, fecha_alta, cuota_mensual, km_actuales, fecha_proxima_revision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (mat, "turismo", mat, marca, modelo, 2019 + (i % 5), 4, 1800, "Euro 6",
             500, 0, d(15 * (i + 1)), "AXA", d(120 + 50 * i), "Propiedad", d(-2000), 0, km,
             d(30 + 90 * (i % 3))),
        )
        n_veh += 1
    ligeros = [
        ("7788 TUV", "Renault", "Master", 132000), ("9911 WXY", "Ford", "Transit", 88000),
        ("2200 ZAB", "Fiat", "Ducato", 210000), ("4400 CDE", "Mercedes-Benz", "Sprinter", 64000),
    ]
    for i, (mat, marca, modelo, km) in enumerate(ligeros):
        conn.execute(
            "INSERT INTO vehiculos (id, categoria, matricula, marca, modelo, anno, ejes, mma, clase_euro, "
            "capacidad_peso, capacidad_palets, fecha_caducidad_itv, seguro_compania, fecha_caducidad_seguro, "
            "tipo_tenencia, fecha_alta, cuota_mensual, km_actuales, fecha_proxima_revision) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (mat, "ligero", mat, marca, modelo, 2020 + (i % 4), 4, 3500, "Euro 6",
             1200, 0, d(-5 + 20 * (i % 4)), "AXA", d(90 + 60 * i), "Propiedad", d(-1800), 0, km,
             d(20 + 70 * (i % 3))),
        )
        n_veh += 1

    # --- 2) Empleados (10 conductores + 3 administrativos + 2 mecánicos) ---
    conductores = [
        ("Carlos", "García López", "45678901A", 2100, 15), ("María", "Fernández Ruiz", "51234567B", 1950, 14),
        ("Javier", "Martínez Gil", "60123456C", 2200, 16), ("Lucía", "Sánchez Ortega", "49876543D", 1850, 13),
        ("Pedro", "Romero Vega", "51239876E", 2050, 15), ("Ana", "Navarro Gil", "46987123F", 1900, 14),
        ("Miguel", "Torres Campos", "51827364G", 2300, 17), ("Elena", "Domínguez Sanz", "47123654H", 1750, 12),
        ("Sergio", "Molina Prieto", "49283615J", 2150, 15), ("Raquel", "Ortega Núñez", "47561928K", 2000, 14),
    ]
    for i, (nombre, apellidos, dni, bruto, irpf) in enumerate(conductores):
        eid = f"EMP-SEED-{i+1:02d}"
        # caducidades variadas para ejercitar el semáforo: caducado, <30d, 30-90d, >90d, NULL
        carnet = [d(-20), d(10), d(45), d(120), d(200), None, d(25), d(80), d(365), d(-5)][i]
        cap = [d(30), d(-15), d(200), d(60), None, d(90), d(15), d(150), d(45), d(180)][i]
        medica = [d(-8), d(50), d(100), d(12), d(30), d(-30), d(75), d(220), d(40), d(60)][i]
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, direccion, ciudad, cp, "
            "fecha_alta, categoria, puesto, tipo_contrato, jornada, banco, iban, titular, "
            "caducidad_carnet, caducidad_cap, caducidad_medica, salario_bruto, irpf, convenio, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (eid, nombre, apellidos, dni, f"28{1234567890+i}", f"{nombre.lower().split()[0]}.{apellidos.lower().split()[0]}@transportes.es",
             f"600{i}0000{i}", "Calle Real 12", "Madrid", "28001", d(-365 * (i % 6)),
             "Conductor", "Conductor", "Indefinido", "Completa", "Santander", f"ES00 0049 0000 00{i:02d} 0000000000", f"{nombre} {apellidos}",
             carnet, cap, medica, bruto, irpf, "Transporte de mercancías", creado),
        )
        _m._sync_conductor(conn, {"id": eid, "nombre": nombre, "apellidos": apellidos, "dni": dni,
                               "telefono": f"600{i}0000{i}", "email": f"{nombre.lower()}@transportes.es", "fecha_baja": ""})
        n_emp += 1
    administrativos = [
        ("Isabel", "Castro Mena", "48234567L", 1650, 13, "Administrativo", "Administración"),
        ("Óscar", "Blanco Rico", "47239856M", 1750, 14, "Administrativo", "Facturación"),
        ("Nuria", "Cabrera Soler", "46281937N", 1580, 12, "Administrativo", "Recursos Humanos"),
    ]
    for i, (nombre, apellidos, dni, bruto, irpf, cat, puesto) in enumerate(administrativos):
        eid = f"EMP-SEED-{11+i:02d}"
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, direccion, ciudad, cp, "
            "fecha_alta, categoria, puesto, tipo_contrato, jornada, banco, iban, titular, "
            "caducidad_carnet, caducidad_cap, caducidad_medica, salario_bruto, irpf, convenio, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (eid, nombre, apellidos, dni, f"28{2234567890+i}", f"{nombre.lower().split()[0]}@transportes.es",
             f"610{i}1111{i}", "Av. Industria 5", "Madrid", "28021", d(-500 - 100 * i),
             cat, puesto, "Indefinido", "Completa", "BBVA", f"ES00 0182 0000 00{i:02d} 0000000000", f"{nombre} {apellidos}",
             None, None, d(150 + 60 * i), bruto, irpf, "Oficinas", creado),
        )
        n_emp += 1
    mecanicos = [
        ("Rubén", "Iglesias Paz", "45263748P", 1850, 15, "Mecánico"),
        ("Iván", "Lozano Gil", "44182736Q", 1900, 16, "Mecánico"),
    ]
    for i, (nombre, apellidos, dni, bruto, irpf, puesto) in enumerate(mecanicos):
        eid = f"EMP-SEED-{14+i:02d}"
        conn.execute(
            "INSERT INTO empleados (id, nombre, apellidos, dni, nss, email, telefono, direccion, ciudad, cp, "
            "fecha_alta, categoria, puesto, tipo_contrato, jornada, banco, iban, titular, "
            "caducidad_carnet, caducidad_cap, caducidad_medica, salario_bruto, irpf, convenio, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (id) DO NOTHING",
            (eid, nombre, apellidos, dni, f"28{3234567890+i}", f"{nombre.lower().split()[0]}@talleres.es",
             f"620{i}2222{i}", "Pol. La Vereda 3", "Getafe", "28904", d(-700 - 100 * i),
             "Mecánico", puesto, "Indefinido", "Completa", "CaixaBank", f"ES00 2100 0000 00{i:02d} 0000000000", f"{nombre} {apellidos}",
             d(90), d(30), d(-10), bruto, irpf, "Taller", creado),
        )
        n_emp += 1

    # --- 3) Mantenimientos (~50) repartidos entre los vehículos seed ---
    veh_ids = [v[0] for v in semis + turismos + ligeros]
    tipos = ["ITV", "Taller", "Preventivo", "Aceite", "Revisión"]
    for i in range(50):
        veh = veh_ids[i % len(veh_ids)]
        tipo = tipos[i % len(tipos)]
        fecha = d(-(i * 11) % 350)
        hecho = (i % 3 != 0)
        conn.execute(
            "INSERT INTO mantenimientos (vehiculo_id, tipo, fecha, km, coste, notas, hecho, fecha_fin, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (veh, tipo, fecha, (i * 9500) % 320000, round(120 + (i * 47) % 1400, 2),
             f"Mantenimiento {tipo.lower()} ({i+1})", hecho, (d((i * 11) % 350 + 2) if not hecho else None), creado),
        )
        n_man += 1

    # --- 4) Alertas preventivas (~15) ---
    alertas = [
        ("MANT-ACEITE", "media", "Cambio de aceite próximo (80.000 km)"),
        ("MANT-ITV", "alta", "ITV vence en menos de 30 días"),
        ("MANT-REVISION", "media", "Revisión programada próxima"),
        ("MANT-SEGURO", "alta", "Seguro a punto de caducar"),
    ]
    for i in range(15):
        veh = veh_ids[i % len(veh_ids)]
        codigo, severidad, mensaje = alertas[i % len(alertas)]
        conn.execute(
            "INSERT INTO alertas_mantenimiento (vehiculo_id, codigo, severidad, mensaje, estado) "
            "VALUES (?,?,?,?,?)",
            (veh, codigo, severidad, mensaje, "abierta"),
        )
        n_ale += 1

    # --- 5) Nóminas del mes corriente (para probar el PDF) ---
    periodo = f"{hoy.year:04d}-{hoy.month:02d}"
    emps = conn.execute("SELECT * FROM empleados WHERE id LIKE ?", ("EMP-SEED-%",)).fetchall()
    for e in emps:
        bruto = float(e["salario_bruto"] or 0)
        irpf = float(e["irpf"] or 15)
        ss_t, ss_e, irpf_imp, neto, coste = _calc_nomina(bruto, irpf, 6.35, 30.0)
        conn.execute(
            "INSERT INTO nominas (empleado_id, periodo, salario_bruto, irpf_pct, ss_trabajador_pct, ss_empresa_pct, "
            "ss_trabajador, ss_empresa, irpf_importe, neto, coste_empresa, estado, pagado, contabilizado, creado) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["id"], periodo, bruto, irpf, 6.35, 30.0, ss_t, ss_e, irpf_imp, neto, coste, "borrador", False, False, creado),
        )
        n_nom += 1

    return {"seeded": True, "vehiculos": n_veh, "empleados": n_emp,
            "mantenimientos": n_man, "alertas": n_ale, "nominas": n_nom}


@router.post("/api/dev/seed")
def dev_seed():
    """Endpoint temporal de desarrollo: inyecta datos demo. Idempotente."""
    conn = _db()
    res = _seed_demo(conn)
    conn.commit()
    conn.close()
    return {"ok": True, **res}




@router.get("/api/ausencias")
def list_ausencias(empleado_id: str = ""):
    conn = _db()
    conds, params = [], []
    if empleado_id: conds.append("a.empleado_id=?"); params.append(empleado_id)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT a.*, e.nombre, e.apellidos, e.categoria FROM ausencias a JOIN empleados e ON e.id=a.empleado_id "
        f"{where} ORDER BY a.fecha_inicio DESC", params,
    ).fetchall()
    conn.close()
    return {"ausencias": [dict(r) for r in rows]}


@router.post("/api/ausencias")
def add_ausencia(a: Ausencia):
    if not (a.empleado_id or "").strip():
        raise HTTPException(status_code=400, detail={"error": "Selecciona el empleado."})
    conn = _db()
    cur = conn.execute(
        "INSERT INTO ausencias (empleado_id, tipo, fecha_inicio, fecha_fin, dias, estado, nota, creado) "
        "VALUES (?,?,?,?,?,?,?,?) RETURNING id",
        (a.empleado_id, a.tipo, a.fecha_inicio, a.fecha_fin, a.dias, a.estado, a.nota,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    aid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"ok": True, "id": aid}


@router.patch("/api/ausencias/{aus_id}")
def upd_ausencia(aus_id: int, a: Ausencia):
    conn = _db()
    conn.execute(
        "UPDATE ausencias SET tipo=?, fecha_inicio=?, fecha_fin=?, dias=?, estado=?, nota=? WHERE id=?",
        (a.tipo, a.fecha_inicio, a.fecha_fin, a.dias, a.estado, a.nota, aus_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True}




@router.get("/api/empleados/ausencias")
def list_ausencias_planificadas(empleado_id: str = ""):
    conn = _db()
    conds, params = [], []
    if empleado_id:
        conds.append("a.empleado_id=?")
        params.append(empleado_id)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(
        f"SELECT a.*, e.nombre, e.apellidos FROM ausencias_empleados a "
        f"JOIN empleados e ON e.id = a.empleado_id {where} ORDER BY a.fecha_inicio DESC",
        params,
    ).fetchall()
    conn.close()
    return {"ausencias": [dict(r) for r in rows]}


@router.post("/api/empleados/ausencias")
def add_ausencia_planificada(a: AusenciaPlanificada):
    if not (a.empleado_id or "").strip():
        raise HTTPException(status_code=400, detail={"error": "Selecciona el empleado."})
    if not a.fecha_inicio or not a.fecha_fin:
        raise HTTPException(status_code=400, detail={"error": "Indica fecha de inicio y fin."})
    if a.tipo not in ("vacaciones", "baja_medica", "permiso_retribuido"):
        raise HTTPException(status_code=400, detail={"error": "Tipo de ausencia inválido."})
    conn = _db()
    cur = conn.execute(
        "INSERT INTO ausencias_empleados (empleado_id, fecha_inicio, fecha_fin, tipo, observaciones, creado) "
        "VALUES (?,?,?,?,?,?) RETURNING id",
        (a.empleado_id, a.fecha_inicio, a.fecha_fin, a.tipo, a.observaciones,
         datetime.datetime.utcnow().isoformat() + "Z"),
    )
    aid = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return {"ok": True, "id": aid}


@router.delete("/api/empleados/ausencias/{ausencia_id}")
def del_ausencia_planificada(ausencia_id: int):
    conn = _db()
    conn.execute("DELETE FROM ausencias_empleados WHERE id=?", (ausencia_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.delete("/api/ausencias/{aus_id}")
def del_ausencia(aus_id: int):
    conn = _db()
    conn.execute("DELETE FROM ausencias WHERE id=?", (aus_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@router.get("/api/rrhh/resumen")
def rrhh_resumen():
    conn = _db()
    activos = conn.execute("SELECT COUNT(*) AS c FROM empleados WHERE fecha_baja IS NULL OR fecha_baja=''").fetchone()["c"]
    bajas = conn.execute("SELECT COUNT(*) AS c FROM empleados WHERE fecha_baja IS NOT NULL AND fecha_baja != ''").fetchone()["c"]
    hoy = datetime.date.today()
    nomina_mes = f"{hoy.year:04d}-{hoy.month:02d}"
    coste = conn.execute("SELECT COALESCE(SUM(coste_empresa),0) AS v FROM nominas WHERE periodo=?", (nomina_mes,)).fetchone()["v"] or 0
    por_categoria = conn.execute(
        "SELECT categoria, COUNT(*) AS c FROM empleados WHERE fecha_baja IS NULL OR fecha_baja='' GROUP BY categoria ORDER BY c DESC"
    ).fetchall()
    prox = conn.execute(
        "SELECT a.fecha_inicio, a.fecha_fin, a.tipo, e.nombre, e.apellidos FROM ausencias a JOIN empleados e ON e.id=a.empleado_id "
        "WHERE a.fecha_fin >= ? ORDER BY a.fecha_inicio LIMIT 10", (hoy.isoformat(),),
    ).fetchall()
    conn.close()
    return {
        "activos": activos, "bajas": bajas,
        "coste_nomina_mes": round(float(coste), 2), "nomina_mes": nomina_mes,
        "por_categoria": [dict(r) for r in por_categoria],
        "prox_ausencias": [dict(r) for r in prox],
    }


