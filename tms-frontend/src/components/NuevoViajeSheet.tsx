import { useEffect, useMemo, useRef, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { GripVertical, Plus, Trash2, Copy, Paperclip } from "lucide-react";
import { MapContainer, TileLayer, Marker, Polyline, useMapEvents } from "react-leaflet";
import L from "leaflet";

import { api, ApiError } from "@/api";
import {
  REST_CLIENTES, REST_DIRECCIONES, REST_TARIFAS, REST_CONDUCTORES,
  REST_VEHICULOS_DISPONIBLES, REST_PROVEEDORES, REST_ACTIVITY_TYPES,
  REST_REVERSE_GEOCODE, RUTA, CREAR_VIAJE, REST_TRIP,
} from "@/config";
import { BuscadorDireccion, type Sugerencia } from "./BuscadorDireccion";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";

// ---- Esquema zod (validación en vivo) ----
const paradaSchema = z.object({
  id: z.string(),
  dirId: z.string(),
  actividad: z.string(),
  comentario: z.string(),
});

const esquema = z
  .object({
    cliente_id: z.string().min(1, "Selecciona un cliente"),
    cliente_nombre: z.string(),
    origen_id: z.string().min(1, "Selecciona el origen"),
    destino_id: z.string().min(1, "Selecciona el destino"),
    actividad_origen: z.string(),
    actividad_destino: z.string(),
    fecha_carga: z.string().min(1, "Indica la fecha de carga"),
    fecha_descarga: z.string().min(1, "Indica la fecha de descarga"),
    tarifa_id: z.string(),
    precio: z.string(),
    kilos: z.string(),
    subcontratado: z.boolean(),
    proveedor_id: z.string(),
    coste: z.string(),
    terminal: z.string(),
    conductor_id: z.string(),
    semirremolque_id: z.string(),
    paradas: z.array(paradaSchema),
  })
  .superRefine((v, ctx) => {
    if (v.fecha_carga && v.fecha_descarga && v.fecha_descarga < v.fecha_carga) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["fecha_descarga"],
        message: "La descarga no puede ser anterior a la carga",
      });
    }
  });

type FormViaje = z.infer<typeof esquema>;

interface Cliente { id: number; nombre: string; }
interface Direccion { id: string; nombre?: string; empresa?: string; ciudad?: string; lat: number | null; lng: number | null; }
interface Tarifa { id: number; nombre: string; tipo: string; precio: number; cliente_id: number | null; activo: boolean; }
interface Proveedor { id: number; nombre: string; }

interface RespuestaRuta {
  ptv?: { polyline?: string; distance_km?: number; travel_time_min?: number; toll?: number | null };
  total_km?: number;
}

interface ParadaPayload { nombre?: string; ciudad?: string; calle?: string; cp?: string; lat?: number | null; lng?: number | null; actividad?: string; comentario?: string; }
interface DireccionPayload { nombre?: string; ciudad?: string; calle?: string; cp?: string; lat?: number | null; lng?: number | null; actividad?: string; }
interface PayloadTrip {
  origen?: DireccionPayload;
  destino?: DireccionPayload;
  paradas?: ParadaPayload[];
  cliente?: string;
  cliente_id?: number;
  conductor_id?: number | null;
  terminal?: string;
  semirremolque_id?: string;
  fecha_esperada_carga?: string;
  fecha_esperada_descarga?: string;
  tarifa_id?: number | null;
  precio?: number;
  kilos?: number;
  subcontratado?: boolean;
  proveedor_id?: number | null;
  coste?: number;
}

// Convierte la Sugerencia del BuscadorDireccion a la Direccion que manejamos (id string).
const aDireccion = (d: Sugerencia & { id: number }): Direccion => ({
  id: String(d.id), nombre: d.nombre, empresa: d.empresa, ciudad: d.ciudad, lat: d.lat, lng: d.lng,
});

/** Decodifica la polyline comprimida de Google (formato PTV). */
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

function MapaClick({ onAdd }: { onAdd: (lat: number, lng: number) => void }) {
  useMapEvents({ click: (e) => onAdd(e.latlng.lat, e.latlng.lng) });
  return null;
}

// ---- Parada reordenable ----
function FilaParada({ id, index, onQuitar, children }: { id: string; index: number; onQuitar: () => void; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });
  return (
    <div ref={setNodeRef} style={{ transform: CSS.Transform.toString(transform), transition }} className={`flex items-start gap-1.5 rounded-md border bg-muted/40 p-2 ${isDragging ? "opacity-60" : ""}`}>
      <button type="button" className="mt-1 cursor-grab text-muted-foreground" {...attributes} {...listeners}><GripVertical size={15} /></button>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] font-semibold text-muted-foreground">Parada {index + 1}</span>
          <button type="button" onClick={onQuitar} className="text-red-500 hover:text-red-700"><Trash2 size={13} /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function NuevoViajeSheet({ onClose, onGuardado, editTripId = null }: {
  onClose: () => void;
  onGuardado: (id: string | null, abrirPlanificacion: boolean) => void;
  editTripId?: string | null;
}) {
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [direcciones, setDirecciones] = useState<Direccion[]>([]);
  const [tarifas, setTarifas] = useState<Tarifa[]>([]);
  const [conductores, setConductores] = useState<{ id: number; nombre: string }[]>([]);
  const [vehiculos, setVehiculos] = useState<{ id: string; matricula: string; categoria: string }[]>([]);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [actividades, setActividades] = useState<Record<string, string>>({});
  const [documentos, setDocumentos] = useState<{ nombre: string; b64: string }[]>([]);
  const [buscandoCliente, setBuscandoCliente] = useState("");
  const [clienteAbierto, setClienteAbierto] = useState(false);
  const [servidor, setServidor] = useState<Record<string, string>>({});
  const [ruta, setRuta] = useState<{ polyline: [number, number][]; km: number; min: number; peaje: number | null; calculando: boolean }>({ polyline: [], km: 0, min: 0, peaje: null, calculando: false });
  const [cargandoEdicion, setCargandoEdicion] = useState(!!editTripId);

  const { register, control, handleSubmit, watch, setValue, getValues, reset, clearErrors, trigger, formState: { errors, isValid } } = useForm<FormViaje>({
    resolver: zodResolver(esquema),
    mode: "onChange",
    defaultValues: {
      cliente_id: "", cliente_nombre: "", origen_id: "", destino_id: "",
      actividad_origen: "CARGA", actividad_destino: "DESCARGA",
      fecha_carga: "", fecha_descarga: "",
      tarifa_id: "", precio: "", kilos: "", subcontratado: false, proveedor_id: "", coste: "",
      terminal: "", conductor_id: "", semirremolque_id: "", paradas: [],
    },
  });
  const { fields, append, remove, move } = useFieldArray({ control, name: "paradas" });

  const sensores = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));
  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (over && active.id !== over.id) {
      const oldIndex = fields.findIndex((f) => f.id === active.id);
      const newIndex = fields.findIndex((f) => f.id === over.id);
      move(oldIndex, newIndex);
    }
  };

  // Carga de opciones (cada una independiente).
  useEffect(() => {
    (async () => {
      const [cli, dir, tar, con, veh, prov, act] = await Promise.allSettled([
        api<{ clientes: Cliente[] }>(REST_CLIENTES),
        api<{ direcciones: Direccion[] }>(REST_DIRECCIONES),
        api<{ tarifas: Tarifa[] }>(REST_TARIFAS),
        api<{ conductores: { id: number; nombre: string }[] }>(REST_CONDUCTORES),
        api<{ vehiculos: { id: string; matricula: string; categoria: string }[] }>(REST_VEHICULOS_DISPONIBLES),
        api<{ proveedores: Proveedor[] }>(REST_PROVEEDORES),
        api<{ actividades: Record<string, string> }>(REST_ACTIVITY_TYPES),
      ]);
      if (cli.status === "fulfilled") setClientes(cli.value.clientes);
      if (dir.status === "fulfilled") setDirecciones(dir.value.direcciones);
      if (tar.status === "fulfilled") setTarifas(tar.value.tarifas.filter((t) => t.activo !== false));
      if (con.status === "fulfilled") setConductores(con.value.conductores);
      if (veh.status === "fulfilled") setVehiculos(veh.value.vehiculos);
      if (prov.status === "fulfilled") setProveedores(prov.value.proveedores);
      if (act.status === "fulfilled") setActividades(act.value.actividades);
      const fallos = [["clientes", cli], ["direcciones", dir], ["tarifas", tar], ["conductores", con], ["vehiculos", veh], ["proveedores", prov], ["actividades", act]] as const;
      for (const [nombre, r] of fallos) {
        if (r.status === "rejected") console.error(`[viajes] error cargando ${nombre}:`, r.reason);
      }
    })();
  }, []);

  // Precarga en modo edición desde GET /api/trips/{id}.
  useEffect(() => {
    if (!editTripId) { setCargandoEdicion(false); return; }
    (async () => {
      try {
        const r = await api<{ payload?: PayloadTrip }>(REST_TRIP(editTripId));
        const p = r.payload ?? {};
        const synth = (d?: DireccionPayload): string => {
          if (!d?.nombre) return "";
          const id = `_edit_${Math.random().toString(36).slice(2, 8)}`;
          setDirecciones((prev) => (prev.some((x) => String(x.id) === id) ? prev : [...prev, { id, nombre: d.nombre, ciudad: d.ciudad, lat: d.lat ?? null, lng: d.lng ?? null }]));
          return id;
        };
        reset({
          cliente_id: p.cliente_id ? String(p.cliente_id) : "",
          cliente_nombre: p.cliente || "",
          origen_id: synth(p.origen),
          destino_id: synth(p.destino),
          actividad_origen: p.origen?.actividad || "CARGA",
          actividad_destino: p.destino?.actividad || "DESCARGA",
          fecha_carga: p.fecha_esperada_carga || "",
          fecha_descarga: p.fecha_esperada_descarga || "",
          tarifa_id: p.tarifa_id ? String(p.tarifa_id) : "",
          precio: p.precio ? String(p.precio) : "",
          kilos: p.kilos ? String(p.kilos) : "",
          subcontratado: !!p.subcontratado,
          proveedor_id: p.proveedor_id ? String(p.proveedor_id) : "",
          coste: p.coste ? String(p.coste) : "",
          terminal: p.terminal || "",
          conductor_id: p.conductor_id ? String(p.conductor_id) : "",
          semirremolque_id: p.semirremolque_id || "",
          paradas: (p.paradas ?? []).map((par) => ({
            id: `_edit_p_${Math.random().toString(36).slice(2, 8)}`,
            dirId: synth(par),
            actividad: par.actividad || "DESCARGA",
            comentario: par.comentario || "",
          })),
        });
        if (p.cliente) setBuscandoCliente(p.cliente);
      } catch (e) {
        console.error("[viajes] error precargando viaje:", e);
      } finally {
        setCargandoEdicion(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editTripId]);

  const clienteSeleccionado = watch("cliente_id");
  const origenId = watch("origen_id");
  const destinoId = watch("destino_id");
  const paradas = watch("paradas");
  const tarifaId = watch("tarifa_id");
  const kilos = watch("kilos");
  const subcontratado = watch("subcontratado");

  // Puntos de la ruta: origen + paradas + destino (para el minimapa + recalculo).
  const puntosRuta = useMemo(() => {
    const pts: { id: string; lat: number; lng: number }[] = [];
    const o = direcciones.find((d) => String(d.id) === origenId);
    if (o?.lat != null && o.lng != null) pts.push({ id: "origen", lat: o.lat, lng: o.lng });
    for (const p of paradas) {
      const d = direcciones.find((x) => String(x.id) === p.dirId);
      if (d?.lat != null && d.lng != null) pts.push({ id: p.id, lat: d.lat, lng: d.lng });
    }
    const de = direcciones.find((d) => String(d.id) === destinoId);
    if (de?.lat != null && de.lng != null) pts.push({ id: "destino", lat: de.lat, lng: de.lng });
    return pts;
  }, [origenId, destinoId, paradas, direcciones]);

  // Recalcula la ruta (debounce + cancelación de la petición anterior).
  useEffect(() => {
    if (puntosRuta.length < 2) {
      setRuta((p) => ({ ...p, polyline: [], km: 0, min: 0, peaje: null, calculando: false }));
      return;
    }
    let cancel = false;
    setRuta((p) => ({ ...p, calculando: true }));
    const t = window.setTimeout(() => {
      api<RespuestaRuta>(RUTA, { method: "POST", body: JSON.stringify({ puntos: puntosRuta, terminal: watch("terminal") || "" }) })
        .then((d) => {
          if (cancel) return;
          const ptv = d.ptv;
          setRuta({
            polyline: ptv?.polyline ? decodePolyline(ptv.polyline) : [],
            km: ptv?.distance_km ?? d.total_km ?? 0,
            min: ptv?.travel_time_min ?? 0,
            peaje: ptv?.toll ?? null,
            calculando: false,
          });
        })
        .catch(() => { if (!cancel) setRuta((p) => ({ ...p, calculando: false })); });
    }, 400);
    return () => { cancel = true; window.clearTimeout(t); };
  }, [puntosRuta, watch("terminal")]);

  // Precargar tarifa + direcciones habituales al elegir cliente.
  const elegirCliente = (c: Cliente) => {
    setValue("cliente_id", String(c.id));
    setValue("cliente_nombre", c.nombre);
    setBuscandoCliente(c.nombre);
    setClienteAbierto(false);
    const tarifa = tarifas.find((t) => t.cliente_id === c.id);
    if (tarifa) setValue("tarifa_id", String(tarifa.id));
    const hab = direcciones.filter((d) => d.empresa === c.nombre);
    if (hab.length >= 2) {
      setValue("origen_id", String(hab[0].id));
      setValue("destino_id", String(hab[1].id));
    }
    trigger();
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
  };

  // "Duplicar último viaje de este cliente": el más reciente por fecha de descarga.
  async function duplicarUltimo() {
    if (!clienteSeleccionado) return;
    try {
      const viajes = await api<{ viajes: { id: string; cliente: string; fecha_esperada_descarga?: string }[] }>(`/api/viajes`);
      const delCliente = viajes.viajes.filter((v) => v.cliente === getValues("cliente_nombre"));
      delCliente.sort((a, b) => (b.fecha_esperada_descarga || "").localeCompare(a.fecha_esperada_descarga || ""));
      const ultimo = delCliente[0];
      if (!ultimo) return;
      const t = await api<{ payload?: PayloadTrip }>(REST_TRIP(ultimo.id));
      const p = t.payload ?? {};
      const synth = (nombre: string, lat: number | null, lng: number | null, ciudad = "") => {
        const id = `_dup_${Math.random().toString(36).slice(2, 8)}`;
        setDirecciones((prev) => [...prev, { id, nombre, ciudad, lat, lng }]);
        return id;
      };
      if (p.origen?.nombre) setValue("origen_id", synth(p.origen.nombre, p.origen.lat ?? null, p.origen.lng ?? null, p.origen.ciudad || ""));
      if (p.destino?.nombre) setValue("destino_id", synth(p.destino.nombre, p.destino.lat ?? null, p.destino.lng ?? null, p.destino.ciudad || ""));
      reset((f) => ({
        ...f,
        paradas: (p.paradas ?? []).map((par: ParadaPayload) => ({
          id: `_dup_p_${Math.random().toString(36).slice(2, 8)}`,
          dirId: par.nombre ? synth(par.nombre, par.lat ?? null, par.lng ?? null, par.ciudad || "") : "",
          actividad: par.actividad || "DESCARGA",
          comentario: par.comentario || "",
        })),
      }));
    } catch (e) {
      console.error("[viajes] error duplicando último viaje:", e);
    }
  }

  const addParada = (dirId = "", actividad = "DESCARGA", comentario = "") =>
    append({ id: `_p_${Math.random().toString(36).slice(2, 8)}`, dirId, actividad, comentario });

  // Clic en el minimapa → nueva parada con geocodificación inversa.
  async function onMapClick(lat: number, lng: number) {
    try {
      const r = await api<{ resultado?: { nombre?: string; calle?: string; ciudad?: string; lat?: number; lng?: number } }>(
        `${REST_REVERSE_GEOCODE}?lat=${lat}&lng=${lng}`,
      );
      const res = r.resultado;
      const nombre = res?.nombre || res?.calle || `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
      const id = `_map_${Math.random().toString(36).slice(2, 8)}`;
      setDirecciones((prev) => [...prev, { id, nombre, ciudad: res?.ciudad || "", lat, lng }]);
      addParada(id, "DESCARGA", "");
    } catch (e) {
      console.error("[viajes] error geocodificando:", e);
    }
  }

  function onFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    for (const file of files) {
      const reader = new FileReader();
      reader.onload = () => setDocumentos((p) => [...p, { nombre: file.name, b64: String(reader.result) }]);
      reader.readAsDataURL(file);
    }
  }

  // Precio estimado según tarifa (km o kilos o viaje).
  const precioEstimado = useMemo(() => {
    const t = tarifas.find((x) => String(x.id) === tarifaId);
    if (!t) return null;
    if (t.tipo === "km" && ruta.km > 0) return t.precio * ruta.km;
    if (t.tipo === "kilos") return t.precio * (Number(kilos) || 0);
    if (t.tipo === "viaje") return t.precio;
    return null;
  }, [tarifas, tarifaId, ruta.km, kilos]);

  const dirNombre = (id: string) => direcciones.find((d) => String(d.id) === id)?.nombre || "";

  async function guardar(abrirPlanificacion: boolean) {
    clearErrors();
    setServidor({});
    const ok = await new Promise<boolean>((resolve) => {
      handleSubmit(() => resolve(true), () => resolve(false))();
    });
    if (!ok) return;
    const f = getValues();
    const dir = (id: string) => {
      const d = direcciones.find((x) => String(x.id) === id);
      return d ? { nombre: d.nombre || "", ciudad: d.ciudad || "", calle: "", cp: "", pais: "ES", lat: d.lat ?? null, lng: d.lng ?? null } : { nombre: id, ciudad: id };
    };
    const tarifa = tarifas.find((x) => String(x.id) === f.tarifa_id);
    const payload = {
      origen: { ...dir(f.origen_id), actividad: f.actividad_origen || "CARGA" },
      destino: { ...dir(f.destino_id), actividad: f.actividad_destino || "DESCARGA" },
      paradas: f.paradas.map((p) => {
        const d = direcciones.find((x) => String(x.id) === p.dirId);
        return { nombre: d?.nombre || "Parada", ciudad: d?.ciudad || "", calle: "", cp: "", pais: "ES", lat: d?.lat ?? null, lng: d?.lng ?? null, actividad: p.actividad, comentario: p.comentario };
      }),
      waypoints: [],
      tramos: [],
      cliente: f.cliente_nombre,
      cliente_id: Number(f.cliente_id),
      conductor: conductores.find((c) => String(c.id) === f.conductor_id)?.nombre || "",
      conductor_id: f.conductor_id ? Number(f.conductor_id) : null,
      terminal: f.terminal || "",
      semirremolque_id: f.semirremolque_id || "",
      tipo_carga: "",
      precio: tarifa ? 0 : Number(f.precio) || 0,
      modo_tarifa: tarifa ? tarifa.tipo : "viaje",
      tarifa_id: tarifa ? Number(tarifa.id) : null,
      kilos: Number(f.kilos) || 0,
      subcontratado: f.subcontratado,
      proveedor_id: f.subcontratado && f.proveedor_id ? Number(f.proveedor_id) : null,
      coste: f.subcontratado ? Number(f.coste) || 0 : 0,
      fecha_esperada_carga: f.fecha_carga,
      fecha_esperada_descarga: f.fecha_descarga,
      gastos: 0,
      iva: 21,
      estado_pago: "pendiente",
      documentos,
    };
    try {
      const url = editTripId ? REST_TRIP(editTripId) : CREAR_VIAJE;
      const method = editTripId ? "PUT" : "POST";
      const r = await api<{ trip_id?: string }>(url, { method, body: JSON.stringify(payload) });
      onGuardado(editTripId ?? r.trip_id ?? null, abrirPlanificacion);
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        const det = (e.detail as { detail?: unknown })?.detail;
        if (Array.isArray(det)) {
          const mapa: Record<string, string> = {};
          for (const d of det as { loc?: string[]; msg?: string }[]) {
            const campo = d.loc?.slice(-1)[0];
            if (campo) mapa[campo] = d.msg || "inválido";
          }
          setServidor(mapa);
          return;
        }
      }
      console.error("[viajes] error guardando viaje:", e);
      setServidor({ general: e instanceof ApiError ? e.message : "Error de red al guardar el viaje." });
    }
  }

  // Atajos: Ctrl+Enter guarda; Ctrl+Shift+Enter guarda y abre /planificacion; "p" añade parada.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const enInput = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        guardar(e.shiftKey);
      } else if (!enInput && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        addParada();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const clientesFiltrados = clientes.filter((c) => c.nombre.toLowerCase().includes(buscandoCliente.toLowerCase())).slice(0, 8);
  const listaActividades = Object.entries(actividades);

  return (
    <Sheet open onOpenChange={(o) => { if (!o) onClose(); }}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="border-b px-4 py-3">
          <SheetTitle className="text-sm font-semibold">{editTripId ? "Editar viaje" : "Nuevo viaje"}</SheetTitle>
        </SheetHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-sm">
          {cargandoEdicion && <div className="text-xs text-muted-foreground">Cargando viaje…</div>}

          {/* Cliente con autocompletado */}
          <div className="relative">
            <label className="mb-0.5 block text-xs text-muted-foreground">Cliente *</label>
            <input value={buscandoCliente} onChange={(e) => { setBuscandoCliente(e.target.value); setClienteAbierto(true); }} onFocus={() => setClienteAbierto(true)} onKeyDown={(e) => { if (e.key === "Enter" && clienteAbierto && clientesFiltrados.length > 0) { e.preventDefault(); elegirCliente(clientesFiltrados[0]); } }} placeholder="Buscar cliente…" className="w-full rounded-md border px-2 py-1.5 text-sm" />
            {servidor.cliente_id && <div className="text-[11px] text-red-500">{servidor.cliente_id}</div>}
            {errors.cliente_id?.message && <div className="text-[11px] text-red-500">{errors.cliente_id.message}</div>}
            {clienteAbierto && clientesFiltrados.length > 0 && (
              <ul className="absolute z-10 mt-1 w-full rounded-md border bg-card shadow-lg">
                {clientesFiltrados.map((c) => (
                  <li key={c.id}><button type="button" onClick={() => elegirCliente(c)} className="w-full px-2 py-1.5 text-left text-sm hover:bg-muted">{c.nombre}</button></li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex items-center justify-between">
            <label className="text-xs text-muted-foreground">Tarifa</label>
            <button type="button" onClick={duplicarUltimo} className="flex items-center gap-1 rounded border px-2 py-1 text-[11px] hover:bg-muted"><Copy size={12} /> Duplicar último viaje de este cliente</button>
          </div>
          <select {...register("tarifa_id")} className="w-full rounded-md border px-2 py-1.5 text-sm">
            <option value="">— Precio manual —</option>
            {tarifas.map((t) => <option key={t.id} value={t.id}>{t.nombre} · {t.tipo === "km" ? `${t.precio} €/km` : t.tipo === "kilos" ? `${t.precio} €/kg` : `${t.precio} €/viaje`}</option>)}
          </select>
          {tarifas.find((x) => String(x.id) === tarifaId)?.tipo === "kilos" && (
            <input {...register("kilos")} type="number" placeholder="Kilos" className="w-full rounded-md border px-2 py-1.5 text-sm" />
          )}
          {!tarifaId && <input {...register("precio")} type="number" step="0.01" placeholder="Precio (€)" className="w-full rounded-md border px-2 py-1.5 text-sm" />}

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Carga *</label>
              <input type="datetime-local" {...register("fecha_carga")} className="w-full rounded-md border px-2 py-1.5 text-sm" />
              {errors.fecha_carga?.message && <div className="text-[11px] text-red-500">{errors.fecha_carga.message}</div>}
            </div>
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Descarga *</label>
              <input type="datetime-local" {...register("fecha_descarga")} className="w-full rounded-md border px-2 py-1.5 text-sm" />
              {errors.fecha_descarga?.message && <div className="text-[11px] text-red-500">{errors.fecha_descarga.message}</div>}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Origen *</label>
              <BuscadorDireccion placeholder="Buscar origen…" value={dirNombre(origenId)} onSelect={(d) => { setDirecciones((p) => (p.some((x) => String(x.id) === String(d.id)) ? p : [...p, aDireccion(d)])); setValue("origen_id", String(d.id), { shouldValidate: true }); }} onClear={() => setValue("origen_id", "")} />
              {errors.origen_id?.message && <div className="text-[11px] text-red-500">{errors.origen_id.message}</div>}
            </div>
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Actividad</label>
              <select {...register("actividad_origen")} className="w-full rounded-md border px-2 py-1.5 text-sm">
                {listaActividades.map(([nombre]) => <option key={nombre} value={nombre}>{nombre}</option>)}
              </select>
            </div>
          </div>

          {/* Paradas reordenables */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold uppercase text-muted-foreground">Paradas</span>
              <button type="button" onClick={() => addParada()} className="flex items-center gap-1 rounded border border-dashed px-2 py-0.5 text-[11px] hover:bg-muted"><Plus size={12} /> Añadir parada (P)</button>
            </div>
            <DndContext sensors={sensores} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={fields.map((f) => f.id)} strategy={verticalListSortingStrategy}>
                {fields.map((f, i) => (
                  <FilaParada key={f.id} id={f.id} index={i} onQuitar={() => remove(i)}>
                    <BuscadorDireccion placeholder="Buscar parada…" value={dirNombre(f.dirId)} onSelect={(d) => { setDirecciones((p) => (p.some((x) => String(x.id) === String(d.id)) ? p : [...p, aDireccion(d)])); setValue(`paradas.${i}.dirId`, String(d.id)); }} onClear={() => setValue(`paradas.${i}.dirId`, "")} />
                    <div className="mt-1 flex gap-1">
                      <select {...register(`paradas.${i}.actividad`)} className="w-full rounded-md border px-2 py-1 text-xs">
                        {listaActividades.map(([nombre]) => <option key={nombre} value={nombre}>{nombre}</option>)}
                      </select>
                      <input {...register(`paradas.${i}.comentario`)} placeholder="Comentarios…" className="w-full rounded-md border px-2 py-1 text-xs" />
                    </div>
                  </FilaParada>
                ))}
              </SortableContext>
            </DndContext>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Destino *</label>
              <BuscadorDireccion placeholder="Buscar destino…" value={dirNombre(destinoId)} onSelect={(d) => { setDirecciones((p) => (p.some((x) => String(x.id) === String(d.id)) ? p : [...p, aDireccion(d)])); setValue("destino_id", String(d.id), { shouldValidate: true }); }} onClear={() => setValue("destino_id", "")} />
              {errors.destino_id?.message && <div className="text-[11px] text-red-500">{errors.destino_id.message}</div>}
            </div>
            <div>
              <label className="mb-0.5 block text-xs text-muted-foreground">Actividad</label>
              <select {...register("actividad_destino")} className="w-full rounded-md border px-2 py-1.5 text-sm">
                {listaActividades.map(([nombre]) => <option key={nombre} value={nombre}>{nombre}</option>)}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <select {...register("terminal")} className="rounded-md border px-2 py-1.5 text-sm">
              <option value="">— Sin tractora —</option>
              {vehiculos.filter((v) => v.categoria === "tractora").map((v) => <option key={v.id} value={v.id}>{v.matricula}</option>)}
            </select>
            <select {...register("semirremolque_id")} className="rounded-md border px-2 py-1.5 text-sm">
              <option value="">— Sin semirremolque —</option>
              {vehiculos.filter((v) => v.categoria === "semirremolque").map((v) => <option key={v.id} value={v.id}>{v.matricula}</option>)}
            </select>
          </div>
          <select {...register("conductor_id")} className="w-full rounded-md border px-2 py-1.5 text-sm">
            <option value="">— Sin conductor —</option>
            {conductores.map((c) => <option key={c.id} value={c.id}>{c.nombre}</option>)}
          </select>

          {/* Subcontratado */}
          <div className="rounded-md border p-2">
            <label className="flex items-center gap-2 text-xs font-medium">
              <input type="checkbox" {...register("subcontratado")} className="h-4 w-4" /> Subcontratado
            </label>
            {subcontratado && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <select {...register("proveedor_id")} className="rounded-md border px-2 py-1.5 text-sm">
                  <option value="">— Proveedor —</option>
                  {proveedores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                </select>
                <input {...register("coste")} type="number" step="0.01" placeholder="Coste (€)" className="w-full rounded-md border px-2 py-1.5 text-sm" />
              </div>
            )}
          </div>

          {/* Documentos */}
          <div>
            <label className="mb-0.5 flex items-center gap-1.5 text-xs text-muted-foreground"><Paperclip size={13} /> Documentos</label>
            <input type="file" multiple accept=".pdf,image/*" onChange={onFiles} className="w-full text-xs" />
            {documentos.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {documentos.map((d, i) => <li key={i} className="flex items-center justify-between text-xs text-muted-foreground"><span className="truncate">{d.nombre}</span><button type="button" onClick={() => setDocumentos((p) => p.filter((_, j) => j !== i))} className="text-red-500"><Trash2 size={12} /></button></li>)}
              </ul>
            )}
          </div>

          {/* Minimapa + métricas */}
          <div className="rounded-lg border p-2">
            <div className="h-48 w-full overflow-hidden rounded-md">
              <MapContainer center={puntosRuta[0] ? [puntosRuta[0].lat, puntosRuta[0].lng] : [40, -3]} zoom={6} className="h-full w-full" fadeAnimation={false} zoomAnimation={false} markerZoomAnimation={false}>
                <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <MapaClick onAdd={onMapClick} />
                {puntosRuta.map((p, i) => <Marker key={p.id} position={[p.lat, p.lng]} icon={L.divIcon({ className: "bg-transparent", html: `<svg width="16" height="22" viewBox="0 0 24 32"><path d="M12 0C5.4 0 0 5.4 0 12c0 8 12 20 12 20s12-12 12-20C24 5.4 18.6 0 12 0z" fill="${i === 0 ? "#10b981" : i === puntosRuta.length - 1 ? "#ef4444" : "#2563eb"}"/></svg>`, iconSize: [16, 22], iconAnchor: [8, 22] })} />)}
                {ruta.polyline.length > 1 && <Polyline positions={ruta.polyline} pathOptions={{ color: "#2563eb", weight: 4 }} />}
              </MapContainer>
            </div>
            <div className="mt-2 grid grid-cols-4 gap-1 text-center text-[11px]">
              <div className="rounded bg-muted/60 px-1 py-1.5"><div className="text-[10px] uppercase text-muted-foreground">Km</div><div className="font-semibold">{ruta.calculando ? "…" : ruta.km ? `${ruta.km.toFixed(0)} km` : "—"}</div></div>
              <div className="rounded bg-muted/60 px-1 py-1.5"><div className="text-[10px] uppercase text-muted-foreground">Duración</div><div className="font-semibold">{ruta.calculando ? "…" : ruta.min ? `${Math.round(ruta.min)} min` : "—"}</div></div>
              <div className="rounded bg-muted/60 px-1 py-1.5"><div className="text-[10px] uppercase text-muted-foreground">Peajes</div><div className="font-semibold">{ruta.calculando ? "…" : ruta.peaje != null ? `${ruta.peaje.toFixed(2)} €` : "—"}</div></div>
              <div className="rounded bg-muted/60 px-1 py-1.5"><div className="text-[10px] uppercase text-muted-foreground">Precio</div><div className="font-semibold">{precioEstimado != null ? `${precioEstimado.toLocaleString("es-ES", { style: "currency", currency: "EUR" })}` : "—"}</div></div>
            </div>
          </div>

          {servidor.general && <div className="rounded-md bg-red-50 px-3 py-2 text-xs text-red-700">{servidor.general}</div>}
        </div>

        <footer className="flex items-center justify-between border-t p-4">
          <span className="text-[11px] text-muted-foreground">Ctrl+Enter guarda · Ctrl+Shift+Enter guarda y abre Planificación</span>
          <div className="flex gap-2">
            <button onClick={onClose} className="rounded border px-3 py-2 text-xs hover:bg-muted">Cancelar</button>
            <button onClick={() => guardar(false)} disabled={!isValid} className="rounded bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">{editTripId ? "Guardar" : "Crear viaje"}</button>
          </div>
        </footer>
      </SheetContent>
    </Sheet>
  );
}
