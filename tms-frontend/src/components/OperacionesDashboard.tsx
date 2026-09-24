import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type {
  ColDef,
  GridApi,
  GridReadyEvent,
  ValueFormatterParams,
  CellValueChangedEvent,
} from "ag-grid-community";
import { useSocketSubscribe } from "../context/SocketContext";
import { StatusBadge } from "./StatusBadge";
import { LiveMap, type FocusMapa } from "./LiveMap";
import type { Viaje, ViajeEvent, ViajeEstado } from "../types";
import { REST_VIAJES, REST_CLIENTES, REST_CONDUCTORES, REST_DIRECCIONES, REST_VEHICULOS_DISPONIBLES, CREAR_VIAJE, RUTA, REST_TARIFAS, REST_PROVEEDORES } from "../config";
import { BuscadorDireccion } from "./BuscadorDireccion";
import { ChatViaje } from "./ChatViaje";
import { DetalleViaje } from "./DetalleViaje";
import { api, ApiError } from "../api";
import { MapContainer, TileLayer, Marker, Polyline, useMapEvents } from "react-leaflet";
import L from "leaflet";
import { Plus, X, MapPin, Trash2, MessageCircle, FileText, RotateCcw, Download, Split, Copy, Send, Info, Pencil, Gauge, AlertTriangle } from "lucide-react";
import { useAgGridState } from "../hooks/useAgGridState";

// Registro único de los módulos Community (filtros, ordenación, transacciones…).
import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";
ModuleRegistry.registerModules([AllCommunityModule]);

// Tema claro, limpio, sin bordes excesivos. `spacing`/`fontSize` controlan la
// densidad; la altura de fila se ajusta vía opción del grid (rowHeight).

// Celdas editables: cursor de texto + fondo gris sutil al hover.
const editableCell = "cursor-text hover:bg-slate-100";

// Motivo de ausencia (planning RRHH) → etiqueta legible para el selector de conductores.
const MOTIVO_AUSENCIA: Record<string, string> = {
  vacaciones: "Vacaciones",
  baja_medica: "Baja Médica",
  permiso_retribuido: "Permiso Retribuido",
};
const labelMotivo = (m?: string) => (m ? MOTIVO_AUSENCIA[m] || m : "");

// Motivo de bloqueo de vehículo (mantenimiento/taller) → etiqueta legible.
const labelBloqueo = (m?: string) => {
  if (!m) return "";
  const s = m.toLowerCase();
  if (s.includes("itv")) return "ITV programada";
  if (s.includes("taller") || s.includes("correctiv")) return "En Taller";
  if (s.includes("preventiv") || s.includes("revis") || s.includes("rutina")) return "Revisión preventiva";
  return m;
};

// Formatea minutos → "4h30" / "45m".
const fmtMin = (m?: number) => {
  const v = Math.round(m ?? 0);
  if (v <= 0) return "0m";
  const h = Math.floor(v / 60);
  const mm = v % 60;
  return h > 0 ? `${h}h${mm ? String(mm).padStart(2, "0") : ""}` : `${mm}m`;
};

type Vista = "dividido" | "grid" | "mapa";

const VISTAS: { id: Vista; label: string }[] = [
  { id: "dividido", label: "Dividido" },
  { id: "grid", label: "Grid" },
  { id: "mapa", label: "Mapa" },
];

/** Barra de progreso sencilla para la columna "Progreso". */
function ProgresoCell({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full bg-blue-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-slate-500">{pct}%</span>
    </div>
  );
}

/** Resalta una fila (o columnas concretas) al recibir una actualización. */
function flashRow(api: GridApi<Viaje>, id: string, columns?: string[]) {
  const node = api.getRowNode(id);
  if (node) {
    api.flashCells({ rowNodes: [node], columns });
  }
}

/** Decodifica la polyline comprimida de Google (formato que devuelve PTV). */
function decodePolyline(encoded: string): [number, number][] {
  const pts: [number, number][] = [];
  let index = 0, lat = 0, lng = 0;
  while (index < encoded.length) {
    let b, shift = 0, result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lat += (result & 1) ? ~(result >> 1) : (result >> 1);
    shift = 0; result = 0;
    do { b = encoded.charCodeAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    lng += (result & 1) ? ~(result >> 1) : (result >> 1);
    pts.push([lat / 1e5, lng / 1e5]);
  }
  return pts;
}

interface PuntoMapa { id: string; lat: number; lng: number; nombre?: string; }

interface ParadaState {
  id: string;
  dirId: string;
  actividad: string;
  comentario: string;
  lat: number | null;
  lng: number | null;
}

interface WaypointState {
  id: string;
  nombre: string;
  lat: number | null;
  lng: number | null;
  distance: number;
}

/** Icono de chincheta (L.divIcon) con color configurable: evita el marker-icon.png roto de Leaflet en Vite. */
function _divIconPunto(color: string): L.DivIcon {
  return L.divIcon({
    className: "bg-transparent",
    html: `<svg width="18" height="24" viewBox="0 0 24 32" xmlns="http://www.w3.org/2000/svg" style="filter: drop-shadow(0 1px 2px rgba(0,0,0,.4))"><path d="M12 0C5.4 0 0 5.4 0 12c0 8 12 20 12 20s12-12 12-20C24 5.4 18.6 0 12 0z" fill="${color}" stroke="#fff" stroke-width="1.5"/><circle cx="12" cy="12" r="4.5" fill="#fff"/></svg>`,
    iconSize: [18, 24],
    iconAnchor: [9, 24],
  });
}

const ICON_ORIGEN = _divIconPunto("#10b981");   // verde (origen)
const ICON_WAYPOINT = _divIconPunto("#2563eb"); // azul (punto de paso)
const ICON_DESTINO = _divIconPunto("#ef4444");  // rojo (destino)

function MapaClick({ onAdd }: { onAdd: (lat: number, lng: number) => void }) {
  useMapEvents({ click: (e) => onAdd(e.latlng.lat, e.latlng.lng) });
  return null;
}

function RutaMiniMapa({ puntos, polyline, onAddWaypoint, onMovePoint }: {
  puntos: PuntoMapa[];
  polyline: [number, number][];
  onAddWaypoint: (lat: number, lng: number) => void;
  onMovePoint: (id: string, lat: number, lng: number) => void;
}) {
  const center: [number, number] = puntos.length ? [puntos[0].lat, puntos[0].lng] : [40.0, -3.0];
  return (
    <MapContainer center={center} zoom={6} className="h-56 w-full rounded-lg border border-slate-200" zoomControl scrollWheelZoom>
      <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <MapaClick onAdd={onAddWaypoint} />
      {puntos.map((p, i) => {
        const icon = i === 0 ? ICON_ORIGEN : i === puntos.length - 1 ? ICON_DESTINO : ICON_WAYPOINT;
        return (
          <Marker
            key={p.id}
            position={[p.lat, p.lng]}
            icon={icon}
            draggable
            eventHandlers={{
              dragend: (e: any) => {
                const pos = e.target.getLatLng();
                onMovePoint(p.id, pos.lat, pos.lng);
              },
            }}
          />
        );
      })}
      {polyline.length > 1 && <Polyline positions={polyline} pathOptions={{ color: "#2563eb", weight: 4 }} />}
    </MapContainer>
  );
}

export function OperacionesDashboard() {
  const [rowData, setRowData] = useState<Viaje[]>([]);
  const [vista, setVista] = useState<Vista>("dividido");
  const [focus, setFocus] = useState<FocusMapa | null>(null);
  const gridApiRef = useRef<GridApi<Viaje> | null>(null);
  // Ref espejo del estado actual por id: permite mergear actualizaciones
  // parciales del socket sin re-render de React.
  const rowsRef = useRef(new Map<string, Viaje>());
  const subscribe = useSocketSubscribe();

  // ---- Nuevo viaje (drawer de creación) ----
  const [formAbierto, setFormAbierto] = useState(false);
  const [cargandoOpciones, setCargandoOpciones] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const revertiendoRef = useRef(false);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);
  const [clientes, setClientes] = useState<Record<string, any>[]>([]);
  const [direcciones, setDirecciones] = useState<Record<string, any>[]>([]);
  const [conductores, setConductores] = useState<Record<string, any>[]>([]);
  const [vehiculos, setVehiculos] = useState<Record<string, any>[]>([]);
  const [clienteId, setClienteId] = useState("");
  const [origenId, setOrigenId] = useState("");
  const [destinoId, setDestinoId] = useState("");
  const [terminal, setTerminal] = useState("");
  const [conductorId, setConductorId] = useState("");
  const [semirremolqueId, setSemirremolqueId] = useState("");
  const [precio, setPrecio] = useState("");
  const [tarifas, setTarifas] = useState<Record<string, any>[]>([]);
  const [proveedores, setProveedores] = useState<Record<string, any>[]>([]);
  const [tarifaId, setTarifaId] = useState("");
  const [kilos, setKilos] = useState("");
  const [subcontratado, setSubcontratado] = useState(false);
  const [proveedorId, setProveedorId] = useState("");
  const [coste, setCoste] = useState("");
  const [fechaCarga, setFechaCarga] = useState("");
  const [fechaDescarga, setFechaDescarga] = useState("");
  const [paradas, setParadas] = useState<ParadaState[]>([]);
  const [waypoints, setWaypoints] = useState<WaypointState[]>([]);
  const [actividades, setActividades] = useState<string[]>([]);
  const [actividadOrigen, setActividadOrigen] = useState("CARGA");
  const [actividadDestino, setActividadDestino] = useState("DESCARGA");
  const [partirTramos, setPartirTramos] = useState(false);
  const [tramoAsign, setTramoAsign] = useState<{ terminal: string; conductor: string }[]>([]);
  const [ruta, setRuta] = useState<{ polyline: [number, number][]; distancia: number; tiempo: number; peaje: number | null; toll_km: number; metodo: string; calculando: boolean }>({ polyline: [], distancia: 0, tiempo: 0, peaje: null, toll_km: 0, metodo: "", calculando: false });
  const [objetivoMapa, setObjetivoMapa] = useState<"origen" | "destino" | `parada:${string}` | `waypoint:${string}`>("origen");
  const [geocodificando, setGeocodificando] = useState(false);
  const [dstat, setDstat] = useState<any>(null);

  // Horas legales restantes del conductor logueado en la tractora seleccionada (DSTAT/traza 82).
  useEffect(() => {
    if (!terminal) { setDstat(null); return; }
    let cancel = false;
    api(`/api/tacografo/${encodeURIComponent(terminal)}/dstat`)
      .then((d) => { if (!cancel) setDstat(d); })
      .catch(() => { if (!cancel) setDstat(null); });
    return () => { cancel = true; };
  }, [terminal]);
  const [chatTripId, setChatTripId] = useState<string | null>(null);
  const [tripDetalle, setTripDetalle] = useState<Viaje | null>(null);
  const [editTripId, setEditTripId] = useState<string | null>(null);
  const [documentos, setDocumentos] = useState<{ nombre: string; contenido: string }[]>([]);

  // lat/lng de una parada: desde su dirección o desde el pin directo en el mapa.
  function paradaLatLng(p: ParadaState): { lat: number; lng: number } | null {
    if (p.lat != null && p.lng != null) return { lat: p.lat, lng: p.lng };
    const d = p.dirId ? direcciones.find((x) => String(x.id) === p.dirId) : null;
    if (d && d.lat != null && d.lng != null) return { lat: d.lat, lng: d.lng };
    return null;
  }

  // Puntos de la ruta: origen + paradas + destino.
  const puntosRuta = useMemo<PuntoMapa[]>(() => {
    const pts: PuntoMapa[] = [];
    const o = direcciones.find((d) => String(d.id) === origenId);
    if (o && o.lat != null && o.lng != null) pts.push({ id: "origen", lat: o.lat, lng: o.lng, nombre: o.nombre || o.ciudad });
    for (const p of paradas) {
      const ll = paradaLatLng(p);
      if (ll) pts.push({ id: p.id, lat: ll.lat, lng: ll.lng });
    }
    const de = direcciones.find((d) => String(d.id) === destinoId);
    if (de && de.lat != null && de.lng != null) pts.push({ id: "destino", lat: de.lat, lng: de.lng, nombre: de.nombre || de.ciudad });
    return pts;
  }, [origenId, destinoId, paradas, direcciones]);

  // Segmentos de la ruta (para partir en tramos): origen → paradas → destino.
  const segmentos = useMemo(() => {
    type Pt = { nombre: string; ciudad: string; lat: number | null; lng: number | null };
    const pts: Pt[] = [];
    const o = direcciones.find((d) => String(d.id) === origenId);
    if (o) pts.push({ nombre: o.nombre || "Origen", ciudad: o.ciudad || "", lat: o.lat ?? null, lng: o.lng ?? null });
    for (const p of paradas) {
      const d = p.dirId ? direcciones.find((x) => String(x.id) === p.dirId) : null;
      const ll = paradaLatLng(p);
      pts.push({ nombre: d?.nombre || "Parada", ciudad: d?.ciudad || "", lat: ll?.lat ?? null, lng: ll?.lng ?? null });
    }
    const de = direcciones.find((d) => String(d.id) === destinoId);
    if (de) pts.push({ nombre: de.nombre || "Destino", ciudad: de.ciudad || "", lat: de.lat ?? null, lng: de.lng ?? null });
    const segs: { origen: Pt; destino: Pt }[] = [];
    for (let i = 0; i < pts.length - 1; i++) segs.push({ origen: pts[i], destino: pts[i + 1] });
    return segs;
  }, [origenId, destinoId, paradas, direcciones]);

  function updateTramoAsign(i: number, patch: Partial<{ terminal: string; conductor: string }>) {
    setTramoAsign((p) => p.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  }

  // Mantener las asignaciones de tramos alineadas con el número de segmentos.
  useEffect(() => {
    setTramoAsign((prev) => {
      if (prev.length === segmentos.length) return prev;
      return segmentos.map((_, i) => prev[i] ?? { terminal: "", conductor: "" });
    });
  }, [segmentos.length]);

  // Recalcula la ruta PTV cada vez que cambian los puntos o el vehículo.
  useEffect(() => {
    if (puntosRuta.length < 2) {
      setRuta((p) => ({ ...p, polyline: [], distancia: 0, tiempo: 0, peaje: null, toll_km: 0, metodo: "" }));
      return;
    }
    let cancel = false;
    setRuta((p) => ({ ...p, calculando: true }));
    api<any>(RUTA, {
      method: "POST",
      body: JSON.stringify({ puntos: puntosRuta, terminal: terminal || "" }),
    })
      .then((d) => {
        if (cancel) return;
        const ptv = d.ptv;
        setRuta({
          polyline: ptv?.polyline ? decodePolyline(ptv.polyline) : [],
          distancia: ptv?.distance_km ?? d.total_km ?? 0,
          tiempo: ptv?.travel_time_min ?? 0,
          peaje: ptv?.toll ?? null,
          toll_km: d.total_toll_km ?? 0,
          metodo: ptv ? "carretera" : (d.metodo || ""),
          calculando: false,
        });
      })
      .catch(() => { if (!cancel) setRuta((p) => ({ ...p, calculando: false })); });
    return () => { cancel = true; };
  }, [puntosRuta, terminal]);

  async function cargarOpciones() {
    setCargandoOpciones(true);
    try {
      const [cli, dir, act, tar, prov] = await Promise.all([
        api<any>(REST_CLIENTES),
        api<any>(REST_DIRECCIONES),
        api<any>("/api/activity-types"),
        api<any>(REST_TARIFAS),
        api<any>(REST_PROVEEDORES),
      ]);
      setClientes(cli.clientes ?? []);
      setDirecciones(dir.direcciones ?? []);
      setActividades(Object.keys(act.actividades ?? {}));
      setTarifas(tar.tarifas ?? []);
      setProveedores(prov.proveedores ?? []);
    } catch (err) {
      console.error("Error cargando opciones del formulario:", err);
    } finally {
      setCargandoOpciones(false);
    }
  }

  async function cargarConductores(fecha?: string) {
    try {
      const q = fecha ? `?fecha_esperada_carga=${encodeURIComponent(fecha)}` : "";
      const d = await api<any>(`${REST_CONDUCTORES}${q}`);
      const lista = d.conductores ?? [];
      setConductores(lista);
      setConductorId((prev) => {
        if (!prev) return prev;
        const c = lista.find((x: Record<string, any>) => String(x.id) === String(prev));
        return c && c.disponible === false ? "" : prev;
      });
    } catch (err) {
      console.error("Error cargando conductores:", err);
    }
  }

  useEffect(() => {
    cargarConductores(fechaCarga);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fechaCarga]);

  async function cargarVehiculos(fecha?: string) {
    try {
      const q = fecha ? `?fecha_esperada_carga=${encodeURIComponent(fecha)}` : "";
      const d = await api<any>(`${REST_VEHICULOS_DISPONIBLES}${q}`);
      const lista = d.vehiculos ?? [];
      setVehiculos(lista);
      setTerminal((prev) => {
        if (!prev) return prev;
        const v = lista.find((x: Record<string, any>) => String(x.id) === String(prev));
        return v && v.disponible === false ? "" : prev;
      });
    } catch (err) {
      console.error("Error cargando vehículos:", err);
    }
  }

  useEffect(() => {
    cargarVehiculos(fechaCarga);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fechaCarga]);

  function dirNombre(id: string): string {
    const d = direcciones.find((x) => String(x.id) === id);
    return d ? (d.nombre || d.ciudad || "") : "";
  }

  const btnObjetivo = (activo: boolean) =>
    `rounded-md px-2.5 py-1 text-xs font-medium transition ${activo ? "bg-blue-600 text-white shadow" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`;

  function resetForm() {
    setEditTripId(null);
    setClienteId("");
    setOrigenId("");
    setDestinoId("");
    setTerminal("");
    setConductorId("");
    setSemirremolqueId("");
    setPrecio("");
    setTarifaId("");
    setKilos("");
    setSubcontratado(false);
    setProveedorId("");
    setCoste("");
    setFechaCarga("");
    setFechaDescarga("");
    setParadas([]);
    setWaypoints([]);
    setActividadOrigen("CARGA");
    setActividadDestino("DESCARGA");
    setPartirTramos(false);
    setTramoAsign([]);
    setDocumentos([]);
    setRuta({ polyline: [], distancia: 0, tiempo: 0, peaje: null, toll_km: 0, metodo: "", calculando: false });
    setMsg(null);
  }

  function abrirForm() {
    resetForm();
    setFormAbierto(true);
    if (clientes.length === 0) cargarOpciones();
  }

  function abrirEditar(v: Viaje) {
    resetForm();
    setEditTripId(v.id);
    setFormAbierto(true);
    if (clientes.length === 0) cargarOpciones();
    // Precarga los campos simples desde la fila del grid.
    setPrecio(v.precio != null ? String(v.precio) : "");
    setTarifaId(v.tarifa_id != null ? String(v.tarifa_id) : "");
    setKilos(v.kilos != null ? String(v.kilos) : "");
    setSubcontratado(!!v.subcontratado);
    setProveedorId(v.proveedor_id != null ? String(v.proveedor_id) : "");
    setCoste(v.coste != null ? String(v.coste) : "");
    setFechaCarga(v.fecha_esperada_carga ? v.fecha_esperada_carga.slice(0, 16) : "");
    setFechaDescarga(v.fecha_esperada_descarga ? v.fecha_esperada_descarga.slice(0, 16) : "");
    const cli = clientes.find((c) => c.nombre === v.cliente);
    if (cli) setClienteId(String(cli.id));
    const con = conductores.find((c) => c.nombre === v.conductor);
    if (con) setConductorId(String(con.id));
    // Precarga ruta + asignación desde el payload completo del viaje.
    (async () => {
      try {
        const t = await api<any>(`/api/trips/${encodeURIComponent(v.id)}`);
        const p = (t.payload ?? {}) as Record<string, any>;
        const newDirs: Record<string, any>[] = [];
        const synth = (nombre: string, lat: any, lng: any, ciudad = "") => {
          const id = `_edit_${nombre}_${Math.random().toString(36).slice(2, 8)}`;
          newDirs.push({ id, nombre, ciudad, lat: lat ?? null, lng: lng ?? null });
          return id;
        };
        const o = p.origen ?? {};
        const d = p.destino ?? {};
        setOrigenId(o.nombre ? synth(o.nombre, o.lat, o.lng, o.ciudad || "") : "");
        setDestinoId(d.nombre ? synth(d.nombre, d.lat, d.lng, d.ciudad || "") : "");
        setActividadOrigen(o.actividad || "CARGA");
        setActividadDestino(d.actividad || "DESCARGA");
        setParadas((p.paradas ?? []).map((par: any) => ({
          id: `_edit_p_${Math.random().toString(36).slice(2, 8)}`,
          dirId: par.nombre ? synth(par.nombre, par.lat, par.lng, par.ciudad || "") : "",
          actividad: par.actividad || "DESCARGA",
          comentario: par.comentario || "",
          lat: par.lat ?? null,
          lng: par.lng ?? null,
        })));
        setWaypoints((p.waypoints ?? []).map((w: any) => ({
          id: `_edit_w_${Math.random().toString(36).slice(2, 8)}`,
          nombre: w.nombre || "",
          lat: w.lat ?? null,
          lng: w.lng ?? null,
          distance: w.distance ?? 5000,
        })));
        setTerminal(p.terminal || "");
        setSemirremolqueId(p.semirremolque_id || "");
        if (p.cliente_id != null) setClienteId(String(p.cliente_id));
        if (p.conductor_id != null) setConductorId(String(p.conductor_id));
        if (Array.isArray(p.tramos) && p.tramos.length) {
          setPartirTramos(true);
          setTramoAsign(p.tramos.map((tr: any) => ({ terminal: tr.terminal || "", conductor: tr.conductor || "" })));
        }
        if (newDirs.length) setDirecciones((prev) => [...prev, ...newDirs]);
      } catch {
        /* noop */
      }
    })();
  }

  function recargar() {
    api<any>(REST_VIAJES)
      .then((data) => {
        const lista: Viaje[] = Array.isArray(data) ? data : data?.viajes ?? [];
        rowsRef.current = new Map(lista.map((v) => [v.id, v]));
        setRowData(lista);
      })
      .catch((err) => console.error("Error recargando viajes:", err));
  }

  async function duplicarViaje(id: string) {
    try {
      await api(`/api/trips/${encodeURIComponent(id)}/duplicar`, { method: "POST" });
      setBanner({ tipo: "ok", texto: "Viaje duplicado (sin asignar)." });
      recargar();
    } catch (err) {
      setBanner({ tipo: "error", texto: err instanceof ApiError ? err.message : "Error de red al duplicar." });
    }
  }

  async function enviarTrip(id: string, force = false) {
    try {
      await api(`/api/trips/${encodeURIComponent(id)}/enviar${force ? "?force=true" : ""}`, { method: "POST" });
      setBanner({ tipo: "ok", texto: "Viaje enviado al terminal Trimble." });
      recargar();
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) {
        const d = e.detail as any;
        const err = d?.detail?.error || e.message;
        if (d?.detail?.forzar) {
          const rest = d.detail.conduccion_restante_min;
          const dur = d.detail.duracion_viaje_min;
          const extra = rest != null && dur != null ? `\n\nConducción restante: ${fmtMin(rest)} · Viaje: ${fmtMin(dur)}` : "";
          if (window.confirm(`${err}${extra}\n\n¿Forzar el envío de todos modos?`)) {
            return enviarTrip(id, true);
          }
        }
        setBanner({ tipo: "error", texto: err });
      } else {
        setBanner({ tipo: "error", texto: "Error de red al enviar." });
      }
    }
  }

  async function eliminarViaje(v: Viaje) {
    if (!window.confirm(`¿Eliminar el viaje ${v.referencia || v.id} (${v.origen || "—"} → ${v.destino || "—"})? No se puede deshacer.`)) return;
    try {
      await api(`/api/trips/${encodeURIComponent(v.id)}`, { method: "DELETE" });
      setBanner({ tipo: "ok", texto: "Viaje eliminado." });
      recargar();
    } catch (err) {
      setBanner({ tipo: "error", texto: err instanceof ApiError ? err.message : "Error de red al eliminar." });
    }
  }

  async function onFiles(e: any) {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    const docs: { nombre: string; contenido: string }[] = [];
    for (const f of files as File[]) {
      if (f.size > 2 * 1024 * 1024) {
        setMsg({ tipo: "error", texto: `«${f.name}» supera 2 MB. Comprímelo o redúcelo.` });
        continue;
      }
      try {
        const b64 = await new Promise<string>((res, rej) => {
          const r = new FileReader();
          r.onload = () => res(String(r.result).split(",")[1] || "");
          r.onerror = () => rej(new Error("lectura"));
          r.readAsDataURL(f);
        });
        docs.push({ nombre: f.name, contenido: b64 });
      } catch { /* skip */ }
    }
    if (docs.length) setDocumentos((p) => [...p, ...docs]);
    e.target.value = "";
  }

  function addParada(lat: number | null = null, lng: number | null = null) {
    const id = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setParadas((p) => [...p, { id, dirId: "", actividad: "DESCARGA", comentario: "", lat, lng }]);
    setObjetivoMapa(`parada:${id}`);
  }

  // Clic en el mapa: geocodifica la coordenada y rellena la casilla activa
  // (origen / parada / waypoint / destino) según el selector de objetivo.
  async function onMapClick(lat: number, lng: number) {
    setGeocodificando(true);
    let nombre = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    try {
      const d = await api<any>(`/api/reverse-geocode?lat=${lat}&lng=${lng}`);
      const res = d.resultado;
      if (res) nombre = [res.nombre || res.calle, res.ciudad].filter(Boolean).join(", ") || res.display_name || nombre;
    } catch {
      /* noop */
    } finally {
      setGeocodificando(false);
    }
    if (objetivoMapa === "origen") {
      const id = `_map_${Math.random().toString(36).slice(2, 8)}`;
      setDirecciones((prev) => [...prev, { id, nombre, lat, lng }]);
      setOrigenId(id);
    } else if (objetivoMapa === "destino") {
      const id = `_map_${Math.random().toString(36).slice(2, 8)}`;
      setDirecciones((prev) => [...prev, { id, nombre, lat, lng }]);
      setDestinoId(id);
    } else if (objetivoMapa.startsWith("parada:")) {
      const pid = objetivoMapa.slice(7);
      const did = `_map_${Math.random().toString(36).slice(2, 8)}`;
      setDirecciones((prev) => [...prev, { id: did, nombre, lat, lng }]);
      setParadas((prev) => prev.map((p) => (p.id === pid ? { ...p, dirId: did, lat, lng } : p)));
    } else if (objetivoMapa.startsWith("waypoint:")) {
      const wid = objetivoMapa.slice(9);
      setWaypoints((prev) => prev.map((w) => (w.id === wid ? { ...w, nombre, lat, lng } : w)));
    }
  }
  function updateParada(id: string, patch: Partial<ParadaState>) {
    setParadas((p) => p.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  }
  function removeParada(id: string) {
    setParadas((p) => p.filter((x) => x.id !== id));
  }
  function moverPunto(id: string, lat: number, lng: number) {
    if (id === "origen") {
      setDirecciones((prev) => prev.map((d) => (String(d.id) === String(origenId) ? { ...d, lat, lng } : d)));
    } else if (id === "destino") {
      setDirecciones((prev) => prev.map((d) => (String(d.id) === String(destinoId) ? { ...d, lat, lng } : d)));
    } else {
      setParadas((prev) => prev.map((p) => (p.id === id ? { ...p, lat, lng } : p)));
    }
  }
  function addWaypoint() {
    setWaypoints((p) => [...p, { id: `_w_${Math.random().toString(36).slice(2, 8)}`, nombre: "", lat: null, lng: null, distance: 5000 }]);
  }
  function updateWaypoint(id: string, patch: Partial<WaypointState>) {
    setWaypoints((p) => p.map((x) => (x.id === id ? { ...x, ...patch } : x)));
  }
  function removeWaypoint(id: string) {
    setWaypoints((p) => p.filter((x) => x.id !== id));
  }
  // Dirección completa de una parada para el payload.
  function paradaDireccion(p: ParadaState): Record<string, any> {
    const d = p.dirId ? direcciones.find((x) => String(x.id) === p.dirId) : null;
    const ll = paradaLatLng(p);
    return {
      nombre: d?.nombre || "Parada",
      ciudad: d?.ciudad || "",
      calle: d?.calle || "",
      cp: d?.cp || "",
      pais: d?.pais || "ES",
      lat: ll?.lat ?? null,
      lng: ll?.lng ?? null,
      actividad: p.actividad,
      comentario: p.comentario,
    };
  }

  async function guardarViaje() {
    if (!clienteId || !origenId || !destinoId) {
      setMsg({ tipo: "error", texto: "Selecciona cliente, origen y destino." });
      return;
    }
    const tarifa = tarifas.find((t) => String(t.id) === tarifaId);
    const precioNum = Number(precio);
    if (!tarifaId && (!precio || isNaN(precioNum) || precioNum <= 0)) {
      setMsg({ tipo: "error", texto: "Indica el precio del viaje (€) o selecciona una tarifa." });
      return;
    }
    if (tarifa && tarifa.tipo === "kilos" && (!kilos || Number(kilos) <= 0)) {
      setMsg({ tipo: "error", texto: "Indica los kilos para la tarifa por kilos." });
      return;
    }
    if (!fechaCarga || !fechaDescarga) {
      setMsg({ tipo: "error", texto: "Indica la fecha/hora esperada de carga y descarga." });
      return;
    }
    setGuardando(true);
    setMsg(null);
    try {
      const dir = (id: string) => {
        const d = direcciones.find((x) => String(x.id) === id);
        return d
          ? { nombre: d.nombre || "", ciudad: d.ciudad || "", calle: d.calle || "", cp: d.cp || "", pais: d.pais || "ES", lat: d.lat ?? null, lng: d.lng ?? null }
          : { nombre: id, ciudad: id };
      };
      const cli = clientes.find((c) => String(c.id) === clienteId);
      const con = conductores.find((c) => String(c.id) === conductorId);
      const payload = {
        origen: { ...dir(origenId), actividad: actividadOrigen },
        destino: { ...dir(destinoId), actividad: actividadDestino },
        paradas: paradas.map(paradaDireccion),
        waypoints: waypoints.filter((w) => w.lat != null && w.lng != null).map((w) => ({
          nombre: w.nombre || "Punto de paso",
          lat: w.lat,
          lng: w.lng,
          distance: w.distance || 5000,
        })),
        tramos: partirTramos ? segmentos.map((s, i) => ({
          orden: i + 1,
          origen_nombre: s.origen.nombre,
          origen_ciudad: s.origen.ciudad,
          origen_lat: s.origen.lat,
          origen_lng: s.origen.lng,
          destino_nombre: s.destino.nombre,
          destino_ciudad: s.destino.ciudad,
          destino_lat: s.destino.lat,
          destino_lng: s.destino.lng,
          terminal: tramoAsign[i]?.terminal || "",
          conductor: tramoAsign[i]?.conductor || "",
        })) : [],
        cliente: cli?.nombre || "",
        cliente_id: cli ? Number(cli.id) : null,
        conductor: con?.nombre || "",
        conductor_id: con ? Number(con.id) : null,
        terminal: terminal || "",
        semirremolque_id: semirremolqueId || "",
        tipo_carga: "",
        precio: tarifaId ? 0 : precioNum,
        modo_tarifa: tarifa ? tarifa.tipo : "viaje",
        tarifa_id: tarifa ? Number(tarifa.id) : null,
        kilos: Number(kilos) || 0,
        subcontratado,
        proveedor_id: subcontratado && proveedorId ? Number(proveedorId) : null,
        coste: subcontratado ? Number(coste) || 0 : 0,
        fecha_esperada_carga: fechaCarga,
        fecha_esperada_descarga: fechaDescarga,
        gastos: 0,
        iva: 21,
        estado_pago: "pendiente",
        documentos,
      };
      const url = editTripId ? `/api/trips/${encodeURIComponent(editTripId)}` : CREAR_VIAJE;
      await api(url, {
        method: editTripId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      setMsg({ tipo: "ok", texto: editTripId ? "Viaje actualizado correctamente." : "Viaje creado correctamente." });
      setFormAbierto(false);
      recargar();
    } catch (err) {
      console.error(editTripId ? "Error actualizando viaje:" : "Error creando viaje:", err);
      setMsg({ tipo: "error", texto: editTripId ? "Error de red al actualizar el viaje." : "Error de red al crear el viaje." });
    } finally {
      setGuardando(false);
    }
  }

  // Carga inicial (una sola vez; de ahí en adelante todo va por transacciones).
  useEffect(() => {
    let cancelado = false;
    (async () => {
      try {
        const data = await api<any>(REST_VIAJES);
        const lista: Viaje[] = Array.isArray(data) ? data : (data?.viajes ?? []);
        if (cancelado) return;
        rowsRef.current = new Map(lista.map((v) => [v.id, v]));
        setRowData(lista);
      } catch (err) {
        console.error("Error cargando /api/viajes:", err);
      }
    })();
    return () => {
      cancelado = true;
    };
  }, []);

  // Suscripción al socket → applyTransaction (sin setRowData ⇒ sin re-renders).
  useEffect(() => {
    return subscribe((evt: ViajeEvent) => {
      const api = gridApiRef.current;
      if (!api) return;

      if (evt.tipo === "creado") {
        const existe = rowsRef.current.has(evt.viaje.id);
        rowsRef.current.set(evt.viaje.id, evt.viaje);
        api.applyTransaction(existe ? { update: [evt.viaje] } : { add: [evt.viaje] });
        if (!existe) flashRow(api, evt.viaje.id);
        return;
      }

      if (evt.tipo === "eliminado") {
        rowsRef.current.delete(evt.id);
        api.applyTransaction({ remove: [{ id: evt.id } as Viaje] });
        return;
      }

      const prev = rowsRef.current.get(evt.id);
      if (!prev) return;

      if (evt.tipo === "estado") {
        const merged: Viaje = { ...prev, estado: evt.estado };
        rowsRef.current.set(evt.id, merged);
        api.applyTransaction({ update: [merged] });
        flashRow(api, evt.id, ["estado"]);
      } else if (evt.tipo === "telemetria") {
        const merged: Viaje = {
          ...prev,
          velocidad: evt.velocidad,
          progreso: evt.progreso,
          lat: evt.lat,
          lng: evt.lng,
          ultima_actualizacion: new Date().toISOString(),
        };
        rowsRef.current.set(evt.id, merged);
        api.applyTransaction({ update: [merged] });
        flashRow(api, evt.id, ["velocidad", "progreso"]);
      }
    });
  }, [subscribe]);

  const columnDefs = useMemo<ColDef<Viaje>[]>(
    () => [
      { field: "referencia", headerName: "Ref.", width: 90, pinned: "left" },
      { field: "id", headerName: "ID", width: 140, pinned: "left" },
      { field: "matricula", headerName: "Matrícula", width: 120, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: Array.from(new Set(vehiculos.map((v) => v.matricula).filter(Boolean))) } },
      { field: "conductor", headerName: "Conductor", width: 170, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: Array.from(new Set(conductores.map((c) => c.nombre).filter(Boolean))) } },
      { field: "origen", headerName: "Origen", flex: 1, minWidth: 150, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: Array.from(new Set(direcciones.map((d) => d.nombre).filter(Boolean))) } },
      { field: "destino", headerName: "Destino", flex: 1, minWidth: 150, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: Array.from(new Set(direcciones.map((d) => d.nombre).filter(Boolean))) } },
      { field: "itinerario", headerName: "Itinerario", flex: 1.2, minWidth: 180, valueFormatter: (p) => p.value || "—", cellClass: "text-xs text-slate-500" },
      { field: "cliente", headerName: "Cliente", width: 150, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: Array.from(new Set(clientes.map((c) => c.nombre).filter(Boolean))) } },
      { field: "precio", headerName: "Precio (€)", width: 110, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor", valueFormatter: (p) => (p.value == null ? "—" : `${Number(p.value).toLocaleString("es-ES", { style: "currency", currency: "EUR" })}`) },
      { field: "km_total", headerName: "Km", width: 90, type: "rightAligned", valueFormatter: (p) => (p.value ? `${Number(p.value).toFixed(0)} km` : "—") },
      { field: "peaje_estimado", headerName: "Peaje (€)", width: 100, type: "rightAligned", valueFormatter: (p) => (p.value ? Number(p.value).toLocaleString("es-ES", { style: "currency", currency: "EUR" }) : "—") },
      { field: "tiempo_min", headerName: "Tiempo", width: 95, type: "rightAligned", valueFormatter: (p) => (p.value ? (Number(p.value) >= 60 ? `${Math.floor(Number(p.value) / 60)}h ${Math.round(Number(p.value) % 60)}m` : `${Math.round(Number(p.value))} min`) : "—") },
      { field: "fecha_esperada_carga", headerName: "Carga", width: 150, editable: true, cellClass: editableCell },
      { field: "fecha_esperada_descarga", headerName: "Descarga", width: 150, editable: true, cellClass: editableCell },
      { field: "estado_pago", headerName: "Pago", width: 110, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["pendiente", "parcial", "pagado"] } },
      {
        field: "estado",
        headerName: "Estado",
        width: 140,
        cellRenderer: (p: { value: ViajeEstado }) => <StatusBadge value={p.value} />,
      },
      {
        field: "progreso",
        headerName: "Progreso",
        width: 150,
        cellRenderer: (p: { value: number }) => <ProgresoCell value={p.value} />,
      },
      {
        field: "velocidad",
        headerName: "Vel.",
        width: 100,
        type: "rightAligned",
        valueFormatter: (p: ValueFormatterParams<Viaje, number>) =>
          p.value == null ? "—" : `${p.value} km/h`,
      },
      {
        field: "eta",
        headerName: "ETA",
        width: 110,
        valueFormatter: (p) =>
          p.value
            ? new Date(p.value).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })
            : "—",
      },
      {
        field: "n_documentos",
        headerName: "Docs",
        width: 70,
        type: "rightAligned",
        cellRenderer: (p: { value: number }) =>
          p.value ? (
            <span className="inline-flex items-center gap-1 font-medium text-slate-600"><FileText size={13} /> {p.value}</span>
          ) : (
            <span className="text-slate-300">—</span>
          ),
      },
      {
        field: "n_tramos",
        headerName: "Tramos",
        width: 84,
        type: "rightAligned",
        cellRenderer: (p: { value: number }) =>
          p.value ? (
            <span className="inline-flex items-center gap-1 font-medium text-slate-600"><Split size={13} /> {p.value}</span>
          ) : (
            <span className="text-slate-300">—</span>
          ),
      },
      {
        headerName: "",
        width: 244,
        cellRenderer: (p: { data: Viaje }) => (
          <div className="flex items-center gap-1">
            <button
              onClick={(e) => { e.stopPropagation(); enviarTrip(p.data.id); }}
              title="Enviar viaje al terminal Trimble"
              className="inline-flex items-center justify-center rounded-md bg-emerald-50 p-1.5 text-emerald-600 transition hover:bg-emerald-100"
            >
              <Send size={15} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setTripDetalle(p.data); }}
              title="Informes del chofer y archivos"
              className="inline-flex items-center justify-center rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-amber-50 hover:text-amber-600"
            >
              <Info size={15} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); abrirEditar(p.data); }}
              title="Editar viaje (cliente, ruta, asignación, documentos)"
              className="inline-flex items-center justify-center rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-blue-50 hover:text-blue-600"
            >
              <Pencil size={15} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); duplicarViaje(p.data.id); }}
              title="Duplicar viaje (sin asignar)"
              className="inline-flex items-center justify-center rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-violet-50 hover:text-violet-600"
            >
              <Copy size={15} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setChatTripId(p.data.id); }}
              title="Mensajería con el terminal"
              className="inline-flex items-center justify-center rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-blue-50 hover:text-blue-600"
            >
              <MessageCircle size={15} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); eliminarViaje(p.data); }}
              title="Eliminar viaje"
              className="inline-flex items-center justify-center rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-red-50 hover:text-red-600"
            >
              <Trash2 size={15} />
            </button>
          </div>
        ),
      },
    ],
    [vehiculos, conductores, clientes, direcciones]
  );

  const defaultColDef = useMemo<ColDef<Viaje>>(
    () => ({
      sortable: true,
      filter: true,
      resizable: true,
      suppressMovable: false,
    }),
    []
  );

  const { resetColumnState, exportToCsv, onGridReady: aplicarVistaGuardada, ...gridHandlers } = useAgGridState("tms_operaciones_grid");

  const onGridReady = useCallback((e: GridReadyEvent<Viaje>) => {
    gridApiRef.current = e.api;
    aplicarVistaGuardada(e);
  }, [aplicarVistaGuardada]);

  const getRowId = useCallback((p: { data: Viaje }) => p.data.id, []);

  // Edición en línea: PATCH del campo modificado; rollback a event.oldValue si falla.
  const onCellValueChanged = useCallback(async (event: CellValueChangedEvent<Viaje>) => {
    const { colDef, data, newValue, oldValue } = event;
    const field = colDef.field;
    if (!field || !data || revertiendoRef.current) return;
    try {
      // Matrícula: el desplegable muestra la matrícula, pero se reasigna por `terminal`.
      let body: Record<string, unknown>;
      if (field === "matricula") {
        const v = vehiculos.find((x) => x.matricula === newValue);
        if (!v) throw new Error("vehiculo no encontrado");
        body = { terminal: v.id };
      } else {
        body = { [field]: newValue };
      }
      await api(`/api/trips/${data.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
    } catch {
      revertiendoRef.current = true;
      event.node.setDataValue(field, oldValue); // rollback automático
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: `No se pudo guardar «${field}». Cambio revertido.` });
    }
  }, [vehiculos]);

  const mostrarGrid = vista === "grid" || vista === "dividido";
  const mostrarMapa = vista === "mapa" || vista === "dividido";
  const panelClass = vista === "dividido" ? "w-1/2" : "w-full";

  return (
    <div className="flex h-full w-full flex-col p-3">
      {/* Selector de vista + acción */}
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          {VISTAS.map((v) => (
            <button
              key={v.id}
              onClick={() => setVista(v.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                vista === v.id
                  ? "bg-blue-600 text-white shadow"
                  : "text-slate-600 hover:bg-slate-100"
              }`}
            >
              {v.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={resetColumnState}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <RotateCcw size={16} /> Restaurar vista
          </button>
          <button
            onClick={() => exportToCsv("operaciones.csv")}
            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
          >
            <Download size={16} /> Exportar CSV
          </button>
          <button
            onClick={abrirForm}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
          >
            <Plus size={16} /> Nuevo Viaje
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-2">
        {mostrarGrid && (
          <div className={`${panelClass} min-h-0 overflow-hidden rounded-xl border border-slate-200 bg-white`}>
            <AgGridReact<Viaje>
              theme={gridTheme}
              columnDefs={columnDefs}
              defaultColDef={defaultColDef}
              rowData={rowData}
              getRowId={getRowId}
              onGridReady={onGridReady}
              singleClickEdit
              stopEditingWhenCellsLoseFocus
              onCellValueChanged={onCellValueChanged}

              {...gridHandlers}
              rowHeight={GRID_ROW_HEIGHT}
              headerHeight={GRID_HEADER_HEIGHT}
              rowSelection="multiple"
              animateRows
              suppressRowClickSelection
            />
          </div>
        )}
        {mostrarMapa && (
          <div className={`${panelClass} min-h-0 overflow-hidden rounded-xl border border-slate-200 bg-white`}>
            <LiveMap focus={focus} />
          </div>
        )}
      </div>

      {banner && (
        <div className={`fixed bottom-4 right-4 z-[3000] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}

      {chatTripId && (
        <ChatViaje tripId={chatTripId} onClose={() => setChatTripId(null)} />
      )}

      {tripDetalle && (
        <DetalleViaje trip={tripDetalle} onClose={() => setTripDetalle(null)} />
      )}

      {formAbierto && (
        <>
          <div className="fixed inset-0 z-[2000] bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardando && setFormAbierto(false)} />
          <div className="fixed inset-y-0 right-0 z-[2001] flex w-[38%] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">{editTripId ? "Editar viaje" : "Nuevo viaje"}</h3>
              <button onClick={() => setFormAbierto(false)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              {cargandoOpciones && <div className="text-xs text-slate-400">Cargando opciones…</div>}
              <label className="block">
                <span className="text-xs text-slate-500">Cliente *</span>
                <select value={clienteId} onChange={(e) => setClienteId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {clientes.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Precio del viaje (€)</span>
                <input type="number" min="0" step="0.01" value={precio} onChange={(e) => setPrecio(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="1500.00" />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Tarifa (valoración automática)</span>
                <select value={tarifaId} onChange={(e) => setTarifaId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Precio manual —</option>
                  {tarifas.filter((t) => t.activo !== false).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.nombre} · {t.tipo === "km" ? `${t.precio} €/km` : t.tipo === "kilos" ? `${t.precio} €/kg` : `${t.precio} €/viaje`}
                    </option>
                  ))}
                </select>
              </label>
              {tarifaId && tarifas.find((t) => String(t.id) === tarifaId)?.tipo === "kilos" && (
                <label className="block">
                  <span className="text-xs text-slate-500">Kilos *</span>
                  <input type="number" min="0" step="1" value={kilos} onChange={(e) => setKilos(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="24000" />
                </label>
              )}
              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Subcontratar (vender a tercero)</span>
                  <label className="flex items-center gap-1.5 text-xs text-slate-500">
                    <input type="checkbox" checked={subcontratado} onChange={(e) => setSubcontratado(e.target.checked)} className="h-3.5 w-3.5" />
                    Activar
                  </label>
                </div>
                {subcontratado && (
                  <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-2">
                    <label className="block">
                      <span className="text-xs text-slate-500">Proveedor / Transportista</span>
                      <select value={proveedorId} onChange={(e) => setProveedorId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option value="">— Seleccionar —</option>
                        {proveedores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Coste del tercero (€)</span>
                      <input type="number" min="0" step="0.01" value={coste} onChange={(e) => setCoste(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="1200.00" />
                    </label>
                  </div>
                )}
              </div>
              <label className="block">
                <span className="text-xs text-slate-500">Fecha/hora esperada de carga *</span>
                <input type="datetime-local" value={fechaCarga} onChange={(e) => setFechaCarga(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Fecha/hora esperada de descarga *</span>
                <input type="datetime-local" value={fechaDescarga} onChange={(e) => setFechaDescarga(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Origen *</span>
                <BuscadorDireccion
                  placeholder="Buscar empresa o lugar…"
                  value={dirNombre(origenId)}
                  onSelect={(d) => { setDirecciones((p) => (p.some((x) => String(x.id) === String(d.id)) ? p : [...p, d])); setOrigenId(String(d.id)); }}
                  onClear={() => setOrigenId("")}
                />
                <span className="mt-1 block text-[11px] text-slate-400">Tipo de actividad</span>
                <select value={actividadOrigen} onChange={(e) => setActividadOrigen(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1 text-xs">
                  {actividades.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Destino *</span>
                <BuscadorDireccion
                  placeholder="Buscar empresa o lugar…"
                  value={dirNombre(destinoId)}
                  onSelect={(d) => { setDirecciones((p) => (p.some((x) => String(x.id) === String(d.id)) ? p : [...p, d])); setDestinoId(String(d.id)); }}
                  onClear={() => setDestinoId("")}
                />
                <span className="mt-1 block text-[11px] text-slate-400">Tipo de actividad</span>
                <select value={actividadDestino} onChange={(e) => setActividadDestino(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1 text-xs">
                  {actividades.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>

              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Paradas intermedias</span>
                  <button type="button" onClick={() => addParada()} className="inline-flex items-center gap-1 rounded-md border border-dashed border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-500 hover:bg-slate-50">
                    <Plus size={13} /> Añadir parada
                  </button>
                </div>
                {paradas.length === 0 && <p className="text-[11px] text-slate-400">Sin paradas. Añade una o haz clic en el mapa.</p>}
                <div className="space-y-2">
                  {paradas.map((p, i) => (
                    <div key={p.id} className="rounded-md border border-slate-200 bg-slate-50 p-2">
                      <div className="mb-1 flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-slate-500">Parada {i + 1}</span>
                        <button onClick={() => removeParada(p.id)} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
                      </div>
                      <BuscadorDireccion
                        placeholder="Buscar empresa o lugar…"
                        value={p.dirId ? dirNombre(p.dirId) : ""}
                        onSelect={(d) => { setDirecciones((prev) => (prev.some((x) => String(x.id) === String(d.id)) ? prev : [...prev, d])); updateParada(p.id, { dirId: String(d.id), lat: d.lat ?? null, lng: d.lng ?? null }); }}
                        onClear={() => updateParada(p.id, { dirId: "", lat: null, lng: null })}
                      />
                      <select value={p.actividad} onChange={(e) => updateParada(p.id, { actividad: e.target.value })} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs">
                        {actividades.map((a) => <option key={a} value={a}>{a}</option>)}
                      </select>
                      <input value={p.comentario} onChange={(e) => updateParada(p.id, { comentario: e.target.value })} placeholder="Comentarios / especificaciones…" className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs" />
                    </div>
                  ))}
                </div>
              </div>

              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Puntos de paso (via points)</span>
                  <button type="button" onClick={addWaypoint} className="inline-flex items-center gap-1 rounded-md border border-dashed border-slate-300 px-2 py-0.5 text-xs font-medium text-slate-500 hover:bg-slate-50">
                    <Plus size={13} /> Añadir punto de paso
                  </button>
                </div>
                {waypoints.length === 0 && <p className="text-[11px] text-slate-400">Sin puntos de paso. Son puntos por los que la ruta debe pasar sin detenerse (navegación Trimble).</p>}
                <div className="space-y-2">
                  {waypoints.map((w, i) => (
                    <div key={w.id} className="rounded-md border border-slate-200 bg-slate-50 p-2">
                      <div className="mb-1 flex items-center justify-between">
                        <span className="text-[11px] font-semibold text-slate-500">Punto de paso {i + 1}</span>
                        <button onClick={() => removeWaypoint(w.id)} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
                      </div>
                      <BuscadorDireccion
                        placeholder="Buscar punto de paso…"
                        value={w.nombre}
                        onSelect={(d) => updateWaypoint(w.id, { nombre: d.nombre || "", lat: d.lat ?? null, lng: d.lng ?? null })}
                        onClear={() => updateWaypoint(w.id, { nombre: "", lat: null, lng: null })}
                      />
                      <div className="mt-1 flex items-center gap-1.5">
                        <span className="text-[11px] text-slate-400">Radio (m):</span>
                        <input
                          type="number"
                          min="0"
                          step="100"
                          value={w.distance}
                          onChange={(e) => updateWaypoint(w.id, { distance: Number(e.target.value) || 0 })}
                          className="w-24 rounded-md border border-slate-200 px-2 py-1 text-xs"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 flex items-center justify-between">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Partir en tramos</span>
                  <label className="flex items-center gap-1.5 text-xs text-slate-500">
                    <input type="checkbox" checked={partirTramos} onChange={(e) => setPartirTramos(e.target.checked)} className="h-3.5 w-3.5" />
                    Activar
                  </label>
                </div>
                {partirTramos && (
                  <div className="space-y-2">
                    {segmentos.length < 2 ? (
                      <p className="text-[11px] text-slate-400">Añade paradas intermedias para dividir el viaje en tramos.</p>
                    ) : (
                      segmentos.map((s, i) => (
                        <div key={i} className="rounded-md border border-slate-200 bg-slate-50 p-2">
                          <div className="mb-1 flex items-center justify-between gap-2">
                            <span className="text-[11px] font-semibold text-slate-500">Tramo {i + 1}</span>
                            <span className="truncate text-[11px] text-slate-400">{s.origen.nombre} → {s.destino.nombre}</span>
                          </div>
                          <select value={tramoAsign[i]?.terminal || ""} onChange={(e) => updateTramoAsign(i, { terminal: e.target.value })} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs">
                            <option value="">— Vehículo —</option>
                            {vehiculos.filter((v) => v.categoria === "tractora").map((v) => (
                              <option key={v.id} value={v.id} disabled={v.disponible === false}>{v.matricula} · {v.marca} {v.modelo}{v.disponible === false ? " ⛔️" : ""}</option>
                            ))}
                          </select>
                          <select value={tramoAsign[i]?.conductor || ""} onChange={(e) => updateTramoAsign(i, { conductor: e.target.value })} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs">
                            <option value="">— Conductor —</option>
                            {conductores.map((c) => (
                              <option key={c.id} value={c.nombre} disabled={c.disponible === false}>{c.nombre}{c.disponible === false ? " ⛔️" : ""}</option>
                            ))}
                          </select>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>

              <label className="block">
                <span className="text-xs text-slate-500">Vehículo (Tractora)</span>
                <select value={terminal} onChange={(e) => setTerminal(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Sin asignar —</option>
                  {vehiculos.filter((v) => v.categoria === "tractora").map((v) => (
                    <option key={v.id} value={v.id} disabled={v.disponible === false}>
                      {v.matricula} · {v.marca} {v.modelo}{v.disponible === false ? ` — ⛔️ ${labelBloqueo(v.motivo_bloqueo)}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {dstat && dstat.ok && (
                <div className="mt-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] text-slate-600">
                  <div className="flex items-center gap-1 font-semibold text-slate-700">
                    <Gauge size={12} className={dstat.conduccion_continua_restante_min <= 0 ? "text-red-500" : "text-emerald-600"} />
                    Tacógrafo · Conductor {dstat.did}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                    <span>Continua: <b>{fmtMin(dstat.driving_coupure_min)}</b> / 4h30</span>
                    <span>Hoy: <b>{fmtMin(dstat.day_driving_min)}</b> / {fmtMin(dstat.limite_diario_min)}</span>
                    <span>Semana restante: <b>{fmtMin(dstat.remaining_week_available_min)}</b></span>
                    {dstat.next_rest_due && <span>Pausa antes de las <b>{dstat.next_rest_due}</b></span>}
                  </div>
                </div>
              )}
              {dstat && !dstat.ok && (
                <div className="mt-1 flex items-center gap-1 rounded-md bg-amber-50 px-2 py-1 text-[11px] text-amber-700">
                  <AlertTriangle size={12} /> Sin datos de tacógrafo para esta tractora (no hay conductor logueado).
                </div>
              )}
              <label className="block">
                <span className="text-xs text-slate-500">Conductor</span>
                <select value={conductorId} onChange={(e) => setConductorId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {conductores.map((c) => (
                    <option key={c.id} value={c.id} disabled={c.disponible === false}>
                      {c.nombre}{c.disponible === false ? ` — ⛔️ ${labelMotivo(c.motivo_ausencia)}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Semirremolque</span>
                <select value={semirremolqueId} onChange={(e) => setSemirremolqueId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {vehiculos.filter((v) => v.categoria === "semirremolque").map((v) => (
                    <option key={v.id} value={v.id} disabled={v.disponible === false}>
                      {v.matricula} · {v.marca} {v.modelo}{v.disponible === false ? ` — ⛔️ ${labelBloqueo(v.motivo_bloqueo)}` : ""}
                    </option>
                  ))}
                </select>
              </label>

              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Documentos (PDF) — se envían al terminal</div>
                <input
                  type="file"
                  accept="application/pdf"
                  multiple
                  onChange={onFiles}
                  className="block w-full text-xs text-slate-500 file:mr-2 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-xs file:font-semibold file:text-slate-700 hover:file:bg-slate-200"
                />
                {documentos.length > 0 && (
                  <ul className="mt-2 space-y-1">
                    {documentos.map((d, i) => (
                      <li key={i} className="flex items-center justify-between rounded-md bg-slate-50 px-2 py-1 text-xs text-slate-600">
                        <span className="truncate">{d.nombre}</span>
                        <button onClick={() => setDocumentos((p) => p.filter((_, j) => j !== i))} className="text-red-500 hover:text-red-700">
                          <Trash2 size={13} />
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Ruta (PTV)</div>
                <RutaMiniMapa
                  puntos={puntosRuta}
                  polyline={ruta.polyline}
                  onAddWaypoint={onMapClick}
                  onMovePoint={moverPunto}
                />
                <div className="mt-1.5 flex flex-wrap items-center gap-1">
                  <span className="mr-1 flex items-center gap-1 text-[11px] text-slate-400">
                    <MapPin size={12} /> Clic en el mapa → rellena:
                  </span>
                  <button className={btnObjetivo(objetivoMapa === "origen")} onClick={() => setObjetivoMapa("origen")}>Origen</button>
                  {paradas.map((p, i) => (
                    <button key={p.id} className={btnObjetivo(objetivoMapa === `parada:${p.id}`)} onClick={() => setObjetivoMapa(`parada:${p.id}`)}>Parada {i + 1}</button>
                  ))}
                  {waypoints.map((w, i) => (
                    <button key={w.id} className={btnObjetivo(objetivoMapa === `waypoint:${w.id}`)} onClick={() => setObjetivoMapa(`waypoint:${w.id}`)}>Paso {i + 1}</button>
                  ))}
                  <button className={btnObjetivo(objetivoMapa === "destino")} onClick={() => setObjetivoMapa("destino")}>Destino</button>
                  {geocodificando && <span className="text-[11px] text-blue-500">buscando dirección…</span>}
                </div>
                <div className="mt-2 grid grid-cols-3 gap-2 text-center">
                  <div className="rounded-md bg-slate-50 px-2 py-1.5">
                    <div className="text-[10px] uppercase text-slate-400">Distancia</div>
                    <div className="text-sm font-semibold text-slate-800">{ruta.calculando ? "…" : ruta.distancia ? `${ruta.distancia.toFixed(0)} km` : "—"}</div>
                    {ruta.metodo === "linea_recta" && !ruta.calculando && (
                      <div className="text-[10px] font-medium text-amber-600">aprox. línea recta</div>
                    )}
                  </div>
                  <div className="rounded-md bg-slate-50 px-2 py-1.5">
                    <div className="text-[10px] uppercase text-slate-400">Tiempo</div>
                    <div className="text-sm font-semibold text-slate-800">{ruta.calculando ? "…" : ruta.tiempo ? `${Math.round(ruta.tiempo)} min` : "—"}</div>
                  </div>
                  <div className="rounded-md bg-slate-50 px-2 py-1.5">
                    <div className="text-[10px] uppercase text-slate-400">Peajes</div>
                    <div className="text-sm font-semibold text-slate-800">{ruta.calculando ? "…" : ruta.peaje != null ? `${ruta.peaje.toFixed(2)} €` : "—"}</div>
                    {ruta.toll_km > 0 && !ruta.calculando && (
                      <div className="text-[10px] font-medium text-slate-500">{ruta.toll_km} km de peaje</div>
                    )}
                  </div>
                </div>
              </div>

              {msg && (
                <div className={`rounded-md px-3 py-2 text-xs ${msg.tipo === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                  {msg.texto}
                </div>
              )}
            </div>
            <footer className="border-t border-slate-200 p-4">
              <button onClick={guardarViaje} disabled={guardando || cargandoOpciones} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                {guardando ? "Guardando…" : editTripId ? "Guardar cambios" : "Crear viaje"}
              </button>
            </footer>
          </div>
        </>
      )}
    </div>
  );
}
