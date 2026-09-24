"""Router de configuración: proveedores de integración, sus campos y tipos de actividad."""
from fastapi import APIRouter, Depends, HTTPException

from db import get_conn
from crypto import _encrypt_valor, _decrypt_valor
from security import require_role, _hash_password
from services.configuracion import _invalida_actividades

router = APIRouter(dependencies=[Depends(require_role(["admin"]))])


@router.get("/api/configuracion/proveedores")
def listar_proveedores(conn=Depends(get_conn)):
    """Lista proveedores con sus campos de configuración y valores actuales."""
    provs = conn.execute(
        "SELECT id, codigo, nombre, categoria, icono, activo, orden "
        "FROM sistema.integracion_proveedores ORDER BY orden, nombre"
    ).fetchall()
    out = []
    for p in provs:
        campos = conn.execute(
            "SELECT c.id, c.clave, c.etiqueta, c.tipo, c.requerido, c.orden, v.valor "
            "FROM sistema.integracion_campos c LEFT JOIN sistema.integracion_valores v ON v.campo_id = c.id "
            "WHERE c.proveedor_id=? ORDER BY c.orden",
            (p["id"],),
        ).fetchall()
        # Enmascarar secretos (nunca devolver la credencial al navegador); el resto va en claro.
        campos_salida = []
        for c in campos:
            d = dict(c)
            if d["tipo"] == "password":
                d["valor"] = ""  # vacío = "no cambiar" en el formulario
            else:
                d["valor"] = _decrypt_valor(d["valor"] or "")
            campos_salida.append(d)
        out.append({
            "id": p["id"], "codigo": p["codigo"], "nombre": p["nombre"],
            "categoria": p["categoria"], "icono": p["icono"] or "",
            "activo": p["activo"], "orden": p["orden"],
            "campos": campos_salida,
        })
    return {"proveedores": out}


@router.put("/api/configuracion/proveedores/{codigo}")
def editar_proveedor(codigo: str, body: dict, conn=Depends(get_conn)):
    """Activa/desactiva un proveedor (activo: bool)."""
    b = body or {}
    if "activo" not in b:
        raise HTTPException(status_code=400, detail={"error": "Falta 'activo'"})
    cur = conn.execute(
        "UPDATE sistema.integracion_proveedores SET activo=? WHERE codigo=? RETURNING id",
        (bool(b["activo"]), codigo),
    )
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    conn.commit()
    return {"ok": True}


@router.put("/api/configuracion/proveedores/{codigo}/valores")
def guardar_valores(codigo: str, body: dict, conn=Depends(get_conn)):
    """Guarda los valores de configuración de un proveedor ({clave: valor})."""
    prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    for clave, valor in (body or {}).items():
        campo = conn.execute(
            "SELECT id, tipo FROM sistema.integracion_campos WHERE proveedor_id=? AND clave=?",
            (prov["id"], clave),
        ).fetchone()
        if campo:
            v = str(valor) if valor is not None else ""
            # Campo secreto vacío = "no cambiar" (el cliente lo devuelve enmascarado).
            if campo["tipo"] == "password" and not v:
                continue
            conn.execute(
                "INSERT INTO sistema.integracion_valores (campo_id, valor) VALUES (?,?) "
                "ON CONFLICT (campo_id) DO UPDATE SET valor=EXCLUDED.valor",
                (campo["id"], _encrypt_valor(v)),
            )
    conn.commit()
    return {"ok": True}


@router.get("/api/configuracion/proveedores/{codigo}/actividades")
def listar_actividades(codigo: str, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    rows = conn.execute(
        "SELECT id, nombre, referencia, activo FROM sistema.actividades WHERE proveedor_id=? ORDER BY id",
        (prov["id"],),
    ).fetchall()
    return {"actividades": [dict(r) for r in rows]}


@router.post("/api/configuracion/proveedores/{codigo}/actividades")
def crear_actividad(codigo: str, body: dict, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    nombre = (body or {}).get("nombre", "").strip()
    referencia = (body or {}).get("referencia", "").strip()
    if not nombre or not referencia:
        raise HTTPException(status_code=400, detail={"error": "nombre y referencia son obligatorios"})
    cur = conn.execute(
        "INSERT INTO sistema.actividades (proveedor_id, nombre, referencia) VALUES (?,?,?) "
        "ON CONFLICT (proveedor_id, nombre) DO UPDATE SET referencia=EXCLUDED.referencia RETURNING id",
        (prov["id"], nombre, referencia),
    )
    aid = cur.fetchone()["id"]
    conn.commit()
    _invalida_actividades()
    return {"ok": True, "id": aid}


@router.put("/api/configuracion/proveedores/{codigo}/actividades/{act_id}")
def editar_actividad(codigo: str, act_id: int, body: dict, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
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
    conn.execute(f"UPDATE sistema.actividades SET {', '.join(sets)} WHERE id=? AND proveedor_id=?", params)
    conn.commit()
    _invalida_actividades()
    return {"ok": True}


@router.delete("/api/configuracion/proveedores/{codigo}/actividades/{act_id}")
def borrar_actividad(codigo: str, act_id: int, conn=Depends(get_conn)):
    prov = conn.execute("SELECT id FROM sistema.integracion_proveedores WHERE codigo=?", (codigo,)).fetchone()
    if not prov:
        raise HTTPException(status_code=404, detail={"error": "Proveedor no encontrado"})
    conn.execute("DELETE FROM sistema.actividades WHERE id=? AND proveedor_id=?", (act_id, prov["id"]))
    conn.commit()
    _invalida_actividades()
    return {"ok": True}


# --- Usuarios y roles (RBAC) ---

@router.get("/api/configuracion/roles")
def listar_roles(conn=Depends(get_conn)):
    rows = conn.execute("SELECT id, nombre, descripcion FROM sistema.roles ORDER BY id").fetchall()
    return {"roles": [dict(r) for r in rows]}


@router.get("/api/configuracion/usuarios")
def listar_usuarios(conn=Depends(get_conn)):
    rows = conn.execute(
        "SELECT id, usuario, rol, nombre, activo, creado_en FROM config.usuarios ORDER BY id"
    ).fetchall()
    return {"usuarios": [dict(r) for r in rows]}


@router.post("/api/configuracion/usuarios")
def crear_usuario(body: dict, conn=Depends(get_conn)):
    b = body or {}
    usuario = (b.get("usuario") or "").strip()
    password = b.get("password") or ""
    rol = (b.get("rol") or "").strip()
    nombre = (b.get("nombre") or "").strip()
    if not usuario or not password or not rol:
        raise HTTPException(status_code=400, detail={"error": "usuario, password y rol son obligatorios"})
    if not conn.execute("SELECT nombre FROM sistema.roles WHERE nombre=?", (rol,)).fetchone():
        raise HTTPException(status_code=400, detail={"error": "Rol no existe"})
    debe_cambiar = bool(b.get("debe_cambiar_clave", False))
    try:
        conn.execute(
            "INSERT INTO config.usuarios (usuario, password_hash, rol, nombre, debe_cambiar_clave) VALUES (?,?,?,?,?)",
            (usuario, _hash_password(password), rol, nombre, debe_cambiar),
        )
    except Exception:
        raise HTTPException(status_code=409, detail={"error": "Ese usuario ya existe"})
    conn.commit()
    return {"ok": True}


@router.put("/api/configuracion/usuarios/{uid}")
def editar_usuario(uid: int, body: dict, conn=Depends(get_conn)):
    b = body or {}
    if "rol" in b and not conn.execute("SELECT nombre FROM sistema.roles WHERE nombre=?", (b["rol"],)).fetchone():
        raise HTTPException(status_code=400, detail={"error": "Rol no existe"})
    sets, params = [], []
    for k in ("rol", "nombre"):
        if k in b:
            sets.append(f"{k}=?")
            params.append(b[k])
    if "activo" in b:
        sets.append("activo=?")
        params.append(bool(b["activo"]))
    if "debe_cambiar_clave" in b:
        sets.append("debe_cambiar_clave=?")
        params.append(bool(b["debe_cambiar_clave"]))
    if b.get("password"):
        sets.append("password_hash=?")
        params.append(_hash_password(b["password"]))
    if not sets:
        raise HTTPException(status_code=400, detail={"error": "Sin cambios"})
    params.append(uid)
    conn.execute(f"UPDATE config.usuarios SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    return {"ok": True}


@router.delete("/api/configuracion/usuarios/{uid}")
def borrar_usuario(uid: int, conn=Depends(get_conn)):
    conn.execute("DELETE FROM config.usuarios WHERE id=?", (uid,))
    conn.commit()
    return {"ok": True}
