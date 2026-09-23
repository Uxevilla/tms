"""Núcleo puro del TMS: constantes y funciones sin dependencias de BD ni de la app FastAPI.

Se importa en main.py vía `from core import *` (ver __all__) para que los nombres
sigan accesibles como `main._map_estado`, etc. y los tests no se rompan.
"""
import re

# --- Estados del viaje (telemetría Trimble) ---------------------------------
# El messagetype (código del macro) se traduce a estado interno. AJUSTAR los
# códigos numéricos a la configuración real de macros de cada equipo.
_MAPA_ESTADOS_TRIMBLE = {
    "1": "Llegada_Origen",
    "2": "Cargando",
    "3": "En_Transito",
    "4": "Llegada_Destino",
    "5": "Descargando",
    "6": "Entregado",
}

# Alias textuales (por si el macro llega con nombre en vez de código numérico).
_MAPA_ESTADOS_ALIAS = {
    "llegada_origen": "Llegada_Origen", "arrived": "Llegada_Origen", "llegada": "Llegada_Origen",
    "cargando": "Cargando", "loading": "Cargando", "carga": "Cargando",
    "en_transito": "En_Transito", "transito": "En_Transito", "transit": "En_Transito", "en_ruta": "En_Transito",
    "llegada_destino": "Llegada_Destino", "arrived_destination": "Llegada_Destino",
    "descargando": "Descargando", "unloading": "Descargando", "descarga": "Descargando",
    "entregado": "Entregado", "delivered": "Entregado", "fin_viaje": "Entregado", "entrega": "Entregado",
}


def _estado_desde_codigo(codigo) -> str:
    """Traduce un código/mensaje Trimble (macro) a estado interno; '' si no mapea."""
    c = str(codigo or "").strip()
    if not c:
        return ""
    if c in _MAPA_ESTADOS_TRIMBLE:
        return _MAPA_ESTADOS_TRIMBLE[c]
    return _MAPA_ESTADOS_ALIAS.get(c.lower(), "")


def _map_estado(estado: str) -> str:
    e = (estado or "").strip().lower()
    # Estados internos del flujo Trimble (canonicalizados).
    if e == "llegada_origen":
        return "Llegada_Origen"
    if e == "cargando":
        return "Cargando"
    if e == "en_transito":
        return "En_Transito"
    if e == "llegada_destino":
        return "Llegada_Destino"
    if e == "descargando":
        return "Descargando"
    if e in ("finalizado", "finished", "entregado", "delivered"):
        return "Entregado"
    if e in ("error", "rechazado", "refused", "incidencia"):
        return "Incidencia"
    if e in ("cancelado", "canceled"):
        return "Cancelado"
    if e in ("recibido", "en curso", "en tránsito", "asignado"):
        return "En Tránsito"
    return "Pendiente"


def _progreso(estado: str) -> int:
    if estado == "Entregado":
        return 100
    if estado == "En Tránsito":
        return 50
    return 0


# --- Categorías y plan contable ---------------------------------------------
_CATEGORIAS = [
    "Combustible", "Peajes", "Mantenimiento", "Neumáticos", "ITV", "Seguro",
    "Impuestos", "Dietas", "Lavado", "Multas", "Parking", "AdBlue",
    "Leasing/Renting", "Transportes", "Otros",
]

_CATEGORIAS_EMPLEADO = ["Conductor", "Administrativo", "Director", "Mecánico", "Comercial", "Mozo", "Otro"]

# Plan contable (PGC) reducido para transportes. (código, nombre, grupo, tipo, orden)
_PLAN_CONTABLE = [
    # Patrimonio neto (Grupo 1)
    ("100", "Capital social", "1", "patrimonio", 10),
    ("129", "Resultado del ejercicio", "1", "patrimonio", 20),
    # Inmovilizado (Grupo 2)
    ("218", "Elementos de transporte", "2", "activo", 30),
    ("281", "Amortización acumulada del inmovilizado material", "2", "activo", 31),
    # Acreedores y deudores (Grupo 4)
    ("400", "Proveedores", "4", "pasivo", 40),
    ("410", "Acreedores por prestaciones de servicios", "4", "pasivo", 41),
    ("430", "Clientes", "4", "activo", 42),
    ("440", "Deudores", "4", "activo", 43),
    ("470", "H.P. deudora por diversos conceptos", "4", "activo", 44),
    ("472", "H.P. IVA soportado", "4", "activo", 45),
    ("473", "H.P. retenciones y pagos a cuenta", "4", "activo", 46),
    ("475", "H.P. acreedora por conceptos fiscales", "4", "pasivo", 47),
    ("4751", "H.P. acreedora por retenciones practicadas", "4", "pasivo", 48),
    ("476", "Organismos de la Seguridad Social acreedores", "4", "pasivo", 49),
    ("465", "Remuneraciones pendientes de pago", "4", "pasivo", 50),
    ("477", "H.P. IVA repercutido", "4", "pasivo", 51),
    # Tesorería (Grupo 5)
    ("520", "Deudas a corto plazo con entidades de crédito", "5", "pasivo", 60),
    ("523", "Proveedores de inmovilizado a corto plazo", "5", "pasivo", 61),
    ("570", "Caja", "5", "activo", 62),
    ("572", "Bancos e instituciones de crédito", "5", "activo", 63),
    # Compras y gastos (Grupo 6)
    ("600", "Compras de mercaderías", "6", "gasto", 70),
    ("621", "Arrendamientos y cánones", "6", "gasto", 71),
    ("622", "Reparaciones y conservación", "6", "gasto", 72),
    ("623", "Servicios de profesionales independientes", "6", "gasto", 73),
    ("624", "Transportes", "6", "gasto", 74),
    ("625", "Primas de seguros", "6", "gasto", 75),
    ("626", "Servicios bancarios y similares", "6", "gasto", 76),
    ("627", "Publicidad, propaganda y RR.PP.", "6", "gasto", 77),
    ("628", "Suministros", "6", "gasto", 78),
    ("629", "Otros servicios", "6", "gasto", 79),
    ("631", "Otros tributos", "6", "gasto", 80),
    ("640", "Sueldos y salarios", "6", "gasto", 81),
    ("642", "Seguridad Social a cargo de la empresa", "6", "gasto", 82),
    ("669", "Otros gastos financieros", "6", "gasto", 83),
    ("678", "Gastos excepcionales", "6", "gasto", 84),
    ("681", "Amortización del inmovilizado material", "6", "gasto", 85),
    # Ventas e ingresos (Grupo 7)
    ("705", "Prestaciones de servicios", "7", "ingreso", 90),
    ("740", "Subvenciones oficiales a la explotación", "7", "ingreso", 91),
    ("759", "Ingresos por servicios diversos", "7", "ingreso", 92),
    ("769", "Otros ingresos financieros", "7", "ingreso", 93),
    ("778", "Ingresos excepcionales", "7", "ingreso", 94),
]

# Mapeo categoría de gasto -> cuenta contable (grupo 6)
_CATEGORIA_CUENTA = {
    "Combustible": "629",
    "Peajes": "624",
    "Mantenimiento": "622",
    "Neumáticos": "622",
    "ITV": "631",
    "Seguro": "625",
    "Impuestos": "631",
    "Dietas": "640",
    "Lavado": "629",
    "Multas": "678",
    "Parking": "629",
    "AdBlue": "629",
    "Leasing/Renting": "621",
    "Transportes": "624",
    "Otros": "629",
}

# Mapeo tipo de gasto de vehículo -> cuenta contable (grupo 6)
_TIPO_GASTO_CUENTA = {
    "combustible": "628",   # Suministros
    "peajes": "629",        # Otros servicios
    "neumaticos": "622",    # Reparaciones y conservación
    "reparaciones": "622",  # Reparaciones y conservación
    "seguros": "625",       # Primas de seguros
    "dietas": "629",        # Otros servicios
    "otros": "629",         # Otros servicios
}


# --- Peajes, flota y estados finales -----------------------------------------
_PEAJE_CATEGORIAS = {
    "ligero": {"label": "Ligero (turismo/furgoneta)", "eur_km": 0.08},
    "pesado2": {"label": "Pesado 2 ejes", "eur_km": 0.16},
    "pesado3": {"label": "Pesado 3 ejes", "eur_km": 0.22},
    "pesado4": {"label": "Pesado 4+ ejes (tractora + semirremolque)", "eur_km": 0.30},
}

# Categorías de vehículo (flota)
_VEHICULO_CATEGORIAS = {
    "tractora": "Tractora",
    "rigido": "Rígido",
    "ligero": "Ligero",
    "turismo": "Turismo",
    "semirremolque": "Semirremolque",
    "remolque": "Remolque",
}

# Estados que se consideran "viaje terminado": no bloquean vehículo y no se re-sincronizan desde Trimble
_ESTADOS_FINALES = ('finalizado', 'finished', 'entregado', 'Entregado', 'error', 'cancelado', 'canceled', 'rechazado', 'refused')
_ESTADOS_FINALES_SQL = "(" + ",".join(f"'{e}'" for e in _ESTADOS_FINALES) + ")"


# --- Peaje por vía (OSRM) ----------------------------------------------------
_TOLL_RE = re.compile(r"\b(AP|R)-(\d+)", re.IGNORECASE)
# Autopistas que quedaron LIBRES al revertirse su concesión (no cuentan como peaje):
_FREE_AP = {"1", "2", "4", "7"}  # AP-1, AP-2, AP-4, AP-7 (desde 2018-2021)


def _is_toll_step(step):
    """True si el paso de OSRM discurre por una vía de peaje (AP-*, R-*), salvo las liberadas."""
    ref = (step.get("ref") or "").upper()
    name = (step.get("name") or "").upper()
    for m in _TOLL_RE.finditer(ref + " " + name):
        if m.group(1) == "AP" and m.group(2) in _FREE_AP:
            continue
        return True
    return False


# --- Importes -----------------------------------------------------------------
def _calcular_importes(viaje):
    precio = float(viaje.precio or 0)
    gastos = float(viaje.gastos or 0)
    margen = round(precio - gastos, 2)
    iva_pct = float(viaje.iva or 0)
    base = round(precio / (1 + iva_pct / 100), 2) if iva_pct > 0 else precio
    cuota_iva = round(precio - base, 2)
    return precio, gastos, margen, iva_pct, base, cuota_iva


__all__ = [
    "_MAPA_ESTADOS_TRIMBLE", "_MAPA_ESTADOS_ALIAS",
    "_estado_desde_codigo", "_map_estado", "_progreso",
    "_CATEGORIAS", "_CATEGORIAS_EMPLEADO", "_PLAN_CONTABLE",
    "_CATEGORIA_CUENTA", "_TIPO_GASTO_CUENTA",
    "_PEAJE_CATEGORIAS", "_VEHICULO_CATEGORIAS",
    "_ESTADOS_FINALES", "_ESTADOS_FINALES_SQL",
    "_TOLL_RE", "_FREE_AP", "_is_toll_step",
    "_calcular_importes",
]
