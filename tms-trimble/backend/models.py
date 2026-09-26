"""Modelos Pydantic del TMS (requests/responses de la API).

Re-exportados en main.py vía `from models import *` (ver __all__).
"""
from typing import Optional
from pydantic import BaseModel, Field

class CategoriaGasto(BaseModel):
    nombre: str
    cuenta: str = ""


class Direccion(BaseModel):
    nombre: str = ""
    empresa: str = ""
    calle: str = ""
    numero: str = ""
    ciudad: str = ""
    cp: str = ""
    pais: str = "ES"
    lat: Optional[float] = None
    lng: Optional[float] = None
    comentario: str = ""
    fecha_inicio: str = ""   # ISO (UTC) de la ventana planificada
    fecha_fin: str = ""
    actividad: str = ""



class Parada(Direccion):
    actividad: str = "DESCARGA"


class ViaPoint(BaseModel):
    """Punto de paso de navegación (via point, manual §5.3.6): no es una parada,
    es un punto arbitrario por el que debe pasar la ruta. distance = radio (m)."""
    nombre: str = ""
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance: int = 5000

class TramoRequest(BaseModel):
    orden: int = 1
    origen_nombre: str = ""
    origen_ciudad: str = ""
    origen_lat: Optional[float] = None
    origen_lng: Optional[float] = None
    destino_nombre: str = ""
    destino_ciudad: str = ""
    destino_lat: Optional[float] = None
    destino_lng: Optional[float] = None
    terminal: str = ""
    conductor: str = ""
    fecha_carga: str = ""
    fecha_descarga: str = ""


class ViajeRequest(BaseModel):
    origen: Direccion
    paradas: list[Parada] = Field(default_factory=list)
    destino: Direccion
    conductor: str = ""
    tipo_carga: str = ""
    documentos: list["Documento"] = Field(default_factory=list)
    matricula: str = ""
    semirremolque_id: str = ""
    remolque_id: str = ""
    tramos: list[TramoRequest] = Field(default_factory=list)
    waypoints: list[ViaPoint] = Field(default_factory=list)
    ecmr_provider: str = ""
    ecmr_id: str = ""
    cliente: str = ""
    cliente_id: Optional[int] = None
    precio: float = 0.0
    gastos: float = 0.0
    factura: str = ""
    estado_pago: str = "pendiente"
    iva: float = 21.0
    conductor_id: Optional[int] = None
    conduccion_acumulada_min: float = 0.0
    fecha_esperada_carga: str = ""
    fecha_esperada_descarga: str = ""
    peso: float = 0.0
    palets: int = 0
    modo_tarifa: str = "viaje"
    tarifa_id: Optional[int] = None
    kilos: float = 0.0
    subcontratado: bool = False
    proveedor_id: Optional[int] = None
    coste: float = 0.0


class TarifaRequest(BaseModel):
    nombre: str = ""
    tipo: str = "viaje"  # km | viaje | kilos
    precio: float = 0.0
    cliente_id: Optional[int] = None
    activo: bool = True


class Documento(BaseModel):
    nombre: str
    contenido: str = ""  # base64 del PDF


class PuntoRuta(BaseModel):
    lat: float
    lng: float
    nombre: str = ""


class RutaRequest(BaseModel):
    puntos: list[PuntoRuta]
    matricula: str = ""
    conduccion_acumulada_min: float = 0.0


class TripUpdate(BaseModel):
    factura: Optional[str] = None
    estado_pago: Optional[str] = None
    cliente: Optional[str] = None
    tipo_carga: Optional[str] = None
    conductor: Optional[str] = None
    matricula: Optional[str] = None
    semirremolque_id: Optional[str] = None
    remolque_id: Optional[str] = None
    fecha_esperada_carga: Optional[str] = None
    fecha_esperada_descarga: Optional[str] = None
    origen: Optional[str] = None
    destino: Optional[str] = None
    precio: Optional[float] = None
    gastos: Optional[float] = None
    iva: Optional[float] = None


class AsignarRequest(BaseModel):
    codigo: str = ""
    semirremolque_id: str = ""
    remolque_id: str = ""
    conductor: str = ""
    conductor_id: Optional[int] = None
    conduccion_acumulada_min: float = 0.0
    ecmr_provider: str = ""
    ecmr_id: str = ""
    documentos: list["Documento"] = Field(default_factory=list)
    force: bool = False  # saltar el chequeo de Safe-Dispatching (conducción legal)


class Empresa(BaseModel):
    nombre: str = ""
    cif: str = ""
    direccion: str = ""
    poblacion: str = ""
    cp: str = ""
    pais: str = "ES"
    telefono: str = ""
    email: str = ""
    web: str = ""
    iva: float = 21.0
    iban: str = ""


class Mantenimiento(BaseModel):
    vehiculo_id: str = ""
    tipo: str = ""
    fecha: str = ""
    fecha_fin: str = ""
    km: int = 0
    coste: float = 0.0
    notas: str = ""
    hecho: bool = False
    proveedor_id: Optional[int] = None
    base_imponible: float = 0.0
    iva: float = 21.0
    generar_gasto: bool = False


class AlertaUpdate(BaseModel):
    estado: str = ""  # resuelta | descartada | abierta


class Transportista(BaseModel):
    nombre: str = ""
    cif: str = ""
    telefono: str = ""
    email: str = ""
    tarifa: float = 0.0


class Liquidacion(BaseModel):
    transportista_id: int = 0
    fecha: str = ""
    importe: float = 0.0
    concepto: str = ""
    pagado: bool = False


class OcrRequest(BaseModel):
    imagen: str  # base64 o data URL


class Gasto(BaseModel):
    terminal: str = ""
    trip_id: str = ""
    categoria: str = ""
    fecha: str = ""
    importe: float = 0.0
    concepto: str = ""
    foto: str = ""
    proveedor_id: Optional[int] = None
    iva: float = 21.0
    retencion: float = 0.0
    cuenta: str = ""


class Proveedor(BaseModel):
    nombre: str = ""
    cif: str = ""
    direccion: str = ""
    poblacion: str = ""
    cp: str = ""
    telefono: str = ""
    email: str = ""
    cuenta_contable_defecto: str = "400"


class GastoVehiculo(BaseModel):
    vehiculo_id: str = ""
    proveedor_id: Optional[int] = None
    fecha: str = ""
    tipo: str = ""           # combustible|peajes|neumaticos|reparaciones|seguros|dietas|otros
    litros: float = 0.0
    base_imponible: float = 0.0
    iva: float = 21.0
    importe_total: float = 0.0
    factura_ref: str = ""
    cuenta_contable_gasto: str = ""
    estado_pago: str = "Pendiente"
    archivo_base64: str = ""


class CosteFijo(BaseModel):
    terminal: str = ""
    concepto: str = ""
    importe: float = 0.0


class SendMensajeRequest(BaseModel):
    subject: str = ""
    body: str = ""
    needreply: bool = False


class Cliente(BaseModel):
    nombre: str
    cif: str = ""
    direccion: str = ""
    poblacion: str = ""
    cp: str = ""
    telefono: str = ""
    email: str = ""
    cuenta_contable_defecto: str = "430"


class Conductor(BaseModel):
    nombre: str
    dni: str = ""
    telefono: str = ""
    email: str = ""
    did: str = ""


class Vehiculo(BaseModel):
    id: str = ""
    terminal_trimble: str = ""
    categoria: str = "tractora"
    matricula: str = ""
    app_terminal: str = ""
    marca: str = ""
    modelo: str = ""
    anno: int = 0
    itv: str = ""
    seguro: str = ""
    peaje_categoria: str = "pesado4"
    ptv_profile: str = "EUR_TRAILER_TRUCK"
    ejes: int = 0
    mma: int = 0
    clase_euro: str = ""
    capacidad_peso: float = 0.0
    capacidad_palets: int = 0
    coste_adquisicion: float = 0.0
    fecha_adquisicion: str = ""
    vida_util: int = 5
    valor_residual: float = 0.0
    fecha_caducidad_itv: str = ""
    seguro_compania: str = ""
    fecha_caducidad_seguro: str = ""
    tipo_tenencia: str = "Propiedad"
    proveedor_id: Optional[int] = None
    fecha_alta: str = ""
    cuota_mensual: float = 0.0
    fecha_proxima_revision: str = ""
    app_terminal: str = ""


class DireccionMaestro(BaseModel):
    nombre: str = ""
    empresa: str = ""
    calle: str = ""
    numero: str = ""
    ciudad: str = ""
    cp: str = ""
    pais: str = "ES"
    lat: Optional[float] = None
    lng: Optional[float] = None
    comentario: str = ""


class TarifaPeaje(BaseModel):
    categoria: str
    eur_km: float


class CuentaContable(BaseModel):
    codigo: str
    nombre: str
    grupo: str = ""
    tipo: str = ""
    orden: int = 0


class LineaAsiento(BaseModel):
    cuenta: str
    debe: float = 0.0
    haber: float = 0.0
    concepto: str = ""


class AsientoManual(BaseModel):
    fecha: str
    concepto: str
    documento: str = ""
    lineas: list[LineaAsiento]


class AmortizacionRequest(BaseModel):
    coste_adquisicion: float = 0.0
    fecha_adquisicion: str = ""
    vida_util: int = 5
    valor_residual: float = 0.0


class Empleado(BaseModel):
    nombre: str = ""
    apellidos: str = ""
    dni: str = ""
    nss: str = ""
    email: str = ""
    telefono: str = ""
    direccion: str = ""
    ciudad: str = ""
    cp: str = ""
    fecha_alta: str = ""
    fecha_baja: str = ""
    categoria: str = "Conductor"
    puesto: str = ""
    tipo_contrato: str = "Indefinido"
    jornada: str = "Completa"
    banco: str = ""
    iban: str = ""
    titular: str = ""
    salario_bruto: float = 0.0
    irpf: float = 15.0
    disponibilidad: str = "disponible"
    motivo_no_dispo: str = ""
    convenio: str = ""
    observaciones: str = ""
    caducidad_carnet: str = ""
    caducidad_cap: str = ""
    caducidad_medica: str = ""


class Nomina(BaseModel):
    empleado_id: str = ""
    periodo: str = ""
    salario_bruto: float = 0.0
    irpf_pct: float = 15.0
    ss_trabajador_pct: float = 6.35
    ss_empresa_pct: float = 30.0
    notas: str = ""


class Ausencia(BaseModel):
    empleado_id: str = ""
    tipo: str = "Vacaciones"
    fecha_inicio: str = ""
    fecha_fin: str = ""
    dias: float = 0.0
    estado: str = "Pendiente"
    nota: str = ""


class AusenciaPlanificada(BaseModel):
    empleado_id: str = ""
    fecha_inicio: str = ""
    fecha_fin: str = ""
    tipo: str = "vacaciones"   # vacaciones | baja_medica | permiso_retribuido
    observaciones: str = ""


__all__ = ["AlertaUpdate", "AmortizacionRequest", "AsientoManual", "AsignarRequest", "Ausencia", "AusenciaPlanificada", "CategoriaGasto", "Cliente", "Conductor", "CosteFijo", "CuentaContable", "Direccion", "DireccionMaestro", "Documento", "Empleado", "Empresa", "Gasto", "GastoVehiculo", "LineaAsiento", "Liquidacion", "Mantenimiento", "Nomina", "OcrRequest", "Parada", "Proveedor", "PuntoRuta", "RutaRequest", "SendMensajeRequest", "TarifaPeaje", "TarifaRequest", "TramoRequest", "Transportista", "TripUpdate", "Vehiculo", "ViajeRequest"]