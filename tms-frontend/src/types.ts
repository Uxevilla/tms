// Tipos compartidos del frontend TMS.

export type ViajeEstado =
  | "Llegada_Origen"
  | "Cargando"
  | "En_Transito"
  | "Llegada_Destino"
  | "Descargando"
  | "Entregado"
  | "En Tránsito"
  | "Incidencia"
  | "Pendiente"
  | "Cancelado";

export interface Viaje {
  id: string;
  referencia?: string;         // identificador único secuencial (V-0001, V-0002…)
  matricula: string;
  conductor: string;
  origen: string;
  destino: string;
  estado: ViajeEstado;
  progreso: number;            // 0..100 (% de la ruta completada)
  velocidad: number | null;    // km/h (última lectura de telemetría)
  lat: number | null;          // última latitud conocida
  lng: number | null;          // última longitud conocida
  eta: string;                 // ISO-8601 (hora estimada de llegada)
  ultima_actualizacion: string; // ISO-8601
  precio?: number;             // base imponible (€)
  cliente?: string;            // nombre del cliente
  fecha_esperada_carga?: string;
  fecha_esperada_descarga?: string;
  estado_pago?: string;        // pendiente | parcial | pagado
  km_total?: number;           // km por carretera (OSRM/PTV)
  peaje_km?: number;           // km de peaje
  peaje_estimado?: number;     // coste estimado de peaje (€)
  tiempo_min?: number;         // tiempo estimado de conducción (min)
  itinerario?: string;         // actividades en orden: "CARGA → REPOSTAJE → DESCARGA"
  n_documentos?: number;       // nº de archivos adjuntos al viaje
  n_tramos?: number;           // nº de tramos (segmentos) del viaje
  modo_tarifa?: string;        // km | viaje | kilos
  tarifa_id?: number | null;   // tarifa aplicada (null = precio manual)
  precio_unitario?: number | null;
  kilos?: number;              // kg para valoración por kilos
  subcontratado?: boolean;     // vendido a un tercero
  proveedor_id?: number | null;
  coste?: number;              // € que pagamos al tercero
}

// Última posición conocida de un vehículo (/api/telemetria/activa).
export interface TelemetriaActiva {
  vehiculo_id: string;
  viaje_id: string | null;
  lat: number;
  lng: number;
  velocidad: number | null;
  heading?: number | null;
  odometer_km?: number | null;
  conductor_taco?: string;
  ultima_telemetria: string | null;
  disponibilidad: "Libre" | "En_Viaje";
  estado: ViajeEstado | "";
  fecha_esperada_descarga: string;
  matricula: string;
  conductor: string;
}

// Eventos emitidos por ws://localhost:8000/ws/operaciones.
export type ViajeEvent =
  | { tipo: "creado"; viaje: Viaje }
  | { tipo: "estado"; id: string; estado: ViajeEstado }
  | {
      tipo: "telemetria";
      id: string;
      velocidad: number | null;
      progreso: number;
      lat: number | null;
      lng: number | null;
      heading?: number | null;
      odometer_km?: number | null;
      fecha_esperada_descarga?: string;
      disponibilidad?: "Libre" | "En_Viaje";
    }
  | { tipo: "eliminado"; id: string };

export type WsStatus = "conectando" | "conectado" | "desconectado";

export type Seccion = "operaciones" | "vehiculos" | "rrhh" | "contabilidad" | "kpi" | "gastos" | "documentos" | "mensajeria" | "configuracion";

// ---------------------------------------------------------------- Torre de control (Fase 2)

export type AtencionTipo =
  | "viaje_retrasado"
  | "conduccion_limite"
  | "caducidad"
  | "viaje_sin_facturar"
  | "gasto_sin_imputar"
  | "mensaje_sin_responder"
  | "mantenimiento_vencido"
  | "envio_trimble_fallido";

export type Severidad = "critico" | "aviso" | "info";

export interface AtencionItem {
  id: string;
  tipo: AtencionTipo;
  severidad: Severidad;
  titulo: string;
  detalle: string;
  entidad: { tipo: "vehiculo" | "viaje" | "conductor" | "factura" | "gasto"; id: string | number; codigo?: string };
  acciones: { id: string; label: string }[];
  ts: string;
}

export interface AtencionResumen {
  critico: number;
  aviso: number;
  info: number;
  facturacion_pendiente?: number;
}

export interface AtencionRespuesta {
  items: AtencionItem[];
  resumen: AtencionResumen;
}

export type EntidadTipo = "vehiculo" | "viaje" | "conductor" | "cliente" | "proveedor" | "factura";

export interface BuscarResultado {
  tipo: EntidadTipo;
  id: string;
  titulo: string;
  subtitulo: string;
}

// ---- Fase 3: tablero de planificación ----
export interface ViajePlanificacion {
  id: string;
  codigo: string;
  terminal: string;
  semirremolque_id: string;
  remolque_id: string;
  conductor: string;
  conductor_id: number | null;
  estado: string;
  origen: string;
  destino: string;
  cliente: string;
  matricula: string;
  kilos: number;
  palets: number;
  tiempo_min: number;
  inicio: string;
  fin: string;
  pendiente_reenvio?: boolean;
}

export interface VehiculoPlanificacion {
  id: string;
  codigo: string;
  matricula: string;
  categoria: string;
  activo: boolean;
  capacidad_peso: number;
  capacidad_palets: number;
  fecha_caducidad_itv: string;
  fecha_caducidad_seguro: string;
}

export interface ValidacionMotivo {
  tipo: string;
  mensaje: string;
}

export interface ValidacionResultado {
  ok: boolean;
  bloqueos: ValidacionMotivo[];
  avisos: ValidacionMotivo[];
}
