"""Router de configuración: proveedores de integración, sus campos y tipos de actividad."""
from fastapi import APIRouter, Depends, HTTPException

from db import get_conn
from security import require_role
from services.configuracion import _invalida_actividades

router = APIRouter(dependencies=[Depends(require_role(["admin"]))])


@router.get("/api/configuracion/proveedores")
def listar_proveedores(conn=Depends(get_conn)):
    """Lista proveedores con sus campos de configuración y valores actuales."""
    provs = conn.execute(
        "SELECT id, codigo, nombre, categoria, icono, activo, orden "
        "FROM integracion_proveedores ORDER BY orden, nombre"
    ).fetchall()
    out = []
    for p in provs:
        campos = conn.execute(
            "SELECT c.id, c.clave, c.etiqueta, c.tipo, c.requerido, c.orden, v.valor "
            "FROM integracion_campos c LEFT JOIN integracion_valores v ON v.campo_id = c.id "
            "WHERE c.proveedor_id=? ORDER BY c.orden",
            (p["id"],),
        ).fetchall()
        out.append({
            "id": p["id"], "codigo": p["codigo"], "nombre": p["nombre"],
            "categoria": p["categoria"], "icono": p["icono"] or "",
            "activo": p["activo"], "orden": p["orden"],
            "campos": [dict(c) for c in campos],
        })
    return {"proveedores": out}


@router.put("/api/configuracion/proveedores/{codigo}/valores")
def guardar_valores(codigo: str, body: dict, conn=Depends(get_conn)):
    """Guarda los valores de configuración de un proveedor ({clave: valor})."""
    prov = conn.execute("SELECT id FROM integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    for clave, valor in (body or {}).items():
        campo = conn.execute(
            "SELECT id FROM integracion_campos WHERE proveedor_id=? AND clave=?",
            (prov["id"], clave),
        ).fetchone()
        if campo:
            conn.execute(
                "INSERT INTO integracion_valores (campo_id, valor) VALUES (?,?) "
                "ON CONFLICT (campo_id) DO UPDATE SET valor=EXCLUDED.valor",
                (campo["id"], valor),
            )
    conn.commit()
    return {"ok": True}


@router.get("/api/configuracion/proveedores/{codigo}/actividades")
def listar_actividades(codigo: str, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    rows = conn.execute(
        "SELECT id, nombre, referencia, activo FROM actividades WHERE proveedor_id=? ORDER BY id",
        (prov["id"],),
    ).fetchall()
    return {"actividades": [dict(r) for r in rows]}


@router.post("/api/configuracion/proveedores/{codigo}/actividades")
def crear_actividad(codigo: str, body: dict, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    nombre = (body or {}).get("nombre", "").strip()
    referencia = (body or {}).get("referencia", "").strip()
    if not nombre or not referencia:
        raise HTTPException(status_code=400, detail={"error": "nombre y referencia son obligatorios"})
    cur = conn.execute(
        "INSERT INTO actividades (proveedor_id, nombre, referencia) VALUES (?,?,?) "
        "ON CONFLICT (proveedor_id, nombre) DO UPDATE SET referencia=EXCLUDED.referencia RETURNING id",
        (prov["id"], nombre, referencia),
    )
    aid = cur.fetchone()["id"]
    conn.commit()
    _invalida_actividades()
    return {"ok": True, "id": aid}


@router.put("/api/configuracion/proveedores/{codigo}/actividades/{act_id}")
def editar_actividad(codigo: str, act_id: int, body: dict, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    b = body or {}
    sets, params = [], []
    for k in ("nombre", "referencia"):
        if k in b:
            sets.append(f"{k}=?")
            params.append(b[k])
    if "activo" in b:
        sets.append("activo=?")
        params.append(bool(b["activo"]))
    if not sets:
        raise HTTPException(status_code=400, detail={"error": "Sin cambios"})
    params += [act_id, prov["id"]]
    conn.execute(f"UPDATE actividades SET {', '.join(sets)} WHERE id=? AND proveedor_id=?", params)
    conn.commit()
    _invalida_actividades()
    return {"ok": True}


@router.delete("/api/configuracion/proveedores/{codigo}/actividades/{act_id}")
def borrar_actividad(codigo: str, act_id: int, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    conn.execute("DELETE FROM actividades WHERE id=? AND proveedor_id=?", (act_id, prov["id"]))
    conn.commit()
    _invalida_actividades()
    return {"ok": True}
