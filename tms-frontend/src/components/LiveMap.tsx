import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, Marker, Polyline, useMap } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import { useSocketSubscribe } from "../context/SocketContext";
import { REST_TELEMETRIA, REST_TRAYECTORIAS, REST_TACOGRAFO } from "../config";
import { getToken } from "../auth";
import type { TelemetriaActiva, ViajeEvent } from "../types";

// ---------------------------------------------------------------- tipos

interface MapaVehiculo {
  id: string; // clave del marcador: viaje_id || vehiculo_id
  matricula: string;
  conductor: string;
  conductor_taco: string;
  disponibilidad: "Libre" | "En_Viaje";
  fecha_esperada_descarga: string;
  velocidad: number | null;
  heading: number | null;
  odometer_km: number | null;
  lat: number;
  lng: number;
}

type Filtro = "todos" | "en_ruta" | "libres" | "parados";

interface DstatInfo {
  ok: boolean;
  did?: string;
  day_driving_min?: number;
  dia_restante_min?: number;
  conduccion_continua_restante_min?: number;
  remaining_week_available_min?: number;
  next_rest_due?: string;
}

/** Objetivo de "flyTo" que el grid dispara al hacer click en una fila. */
export interface FocusMapa {
  lat: number;
  lng: number;
}

// ---------------------------------------------------------------- helpers

/** VERDE = libre · AMARILLO = en tránsito a tiempo · ROJO = retrasado (pasó la descarga). */
function colorVehiculo(v: MapaVehiculo): string {
  if (v.disponibilidad === "Libre") return "#16a34a";
  const fe = v.fecha_esperada_descarga;
  if (fe) {
    const d = new Date(fe);
    if (!isNaN(d.getTime()) && Date.now() > d.getTime()) return "#dc2626";
  }
  return "#f59e0b";
}

/** Umbral (km/h) por debajo del cual se considera parado. */
const UMBRAL_MOVIMIENTO_KMH = 3;

function estaEnMovimiento(v: MapaVehiculo): boolean {
  return (v.velocidad ?? 0) > UMBRAL_MOVIMIENTO_KMH;
}

/** Minutos → "Xh Ym" (o "Ym" si < 1h). */
function fmtMin(v: number): string {
  const total = Math.round(v);
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/** Marcador según estado: punto negro si está parado; flecha orientada al rumbo si se mueve. */
function iconoVehiculo(v: MapaVehiculo): L.DivIcon {
  if (!estaEnMovimiento(v)) {
    const svg =
      `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24">` +
      `<circle cx="12" cy="12" r="7" fill="#111827" stroke="#ffffff" stroke-width="2"/></svg>`;
    return L.divIcon({ className: "tms-vehiculo-icon", html: svg, iconSize: [16, 16], iconAnchor: [8, 8] });
  }
  const color = colorVehiculo(v);
  const angulo = Number(v.heading) || 0; // 0 = norte, 90 = este…
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="30" height="30" viewBox="0 0 24 24">` +
    `<g transform="rotate(${angulo} 12 12)">` +
    `<path d="M12 3 L20 20 L12 15.5 L4 20 Z" fill="${color}" stroke="#ffffff" stroke-width="1.5"/>` +
    `</g></svg>`;
  return L.divIcon({ className: "tms-vehiculo-icon", html: svg, iconSize: [30, 30], iconAnchor: [15, 15] });
}

/** Icono de cluster (agrupación) con tema oscuro: círculo con el nº de vehículos. */
function iconoCluster(cluster: any): L.DivIcon {
  const count = cluster.getChildCount();
  const size = count < 10 ? 30 : count < 100 ? 38 : 46;
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24">` +
    `<circle cx="12" cy="12" r="11" fill="#1e293b" fill-opacity="0.92" stroke="#64748b" stroke-width="2"/>` +
    `<text x="12" y="16" text-anchor="middle" font-size="10" font-weight="700" fill="#e2e8f0">${count}</text></svg>`;
  return L.divIcon({ className: "tms-cluster-icon", html: svg, iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
}

/** Vuela al objetivo cuando cambia (click sobre un viaje en el AG Grid). */
function FlyTo({ target }: { target: FocusMapa | null }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo([target.lat, target.lng], 13, { duration: 0.8 });
  }, [target, map]);
  return null;
}

/** Un marcador. Sin popup: al pinchar abre el panel lateral. */
function VehiculoMarker({ v, onSelect }: { v: MapaVehiculo; onSelect: (v: MapaVehiculo) => void }) {
  const icon = useMemo(
    () => iconoVehiculo(v),
    [v.velocidad, v.heading, v.disponibilidad, v.fecha_esperada_descarga],
  );
  return (
    <Marker
      position={[v.lat, v.lng]}
      icon={icon}
      eventHandlers={{ click: () => onSelect(v) }}
    />
  );
}

// ---------------------------------------------------------------- overlays

function Leyenda() {
  const item = "flex items-center gap-2";
  const dot = "inline-block h-3 w-3 rounded-full";
  return (
    <div className="absolute bottom-4 left-4 z-[1000] rounded-lg bg-slate-900/85 px-3 py-2 text-[11px] text-slate-200 shadow-lg backdrop-blur">
      <div className="mb-1 font-semibold text-white">Leyenda</div>
      <div className={item}><span className={dot} style={{ background: "#16a34a" }} /> Libre</div>
      <div className={item}><span className={dot} style={{ background: "#f59e0b" }} /> En ruta</div>
      <div className={item}><span className={dot} style={{ background: "#dc2626" }} /> Retrasado</div>
      <div className={item}><span className={`${dot} border border-white`} style={{ background: "#111827" }} /> Parado</div>
    </div>
  );
}

function Filtros({
  filtro,
  setFiltro,
  mostrarTrayectorias,
  setMostrarTrayectorias,
}: {
  filtro: Filtro;
  setFiltro: (f: Filtro) => void;
  mostrarTrayectorias: boolean;
  setMostrarTrayectorias: (b: boolean) => void;
}) {
  const opciones: { id: Filtro; label: string }[] = [
    { id: "todos", label: "Todos" },
    { id: "en_ruta", label: "En ruta" },
    { id: "libres", label: "Libres" },
    { id: "parados", label: "Parados" },
  ];
  return (
    <div className="absolute left-4 top-4 z-[1000] flex flex-col items-start gap-2">
      <div className="flex gap-1 rounded-lg bg-slate-900/85 p-1 shadow-lg backdrop-blur">
        {opciones.map((o) => (
          <button
            key={o.id}
            onClick={() => setFiltro(o.id)}
            className={`rounded px-2 py-1 text-[11px] font-medium transition ${
              filtro === o.id ? "bg-slate-100 text-slate-900" : "text-slate-300 hover:text-white"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <button
        onClick={() => setMostrarTrayectorias((x) => !x)}
        className={`rounded-lg px-2 py-1 text-[11px] font-medium shadow-lg backdrop-blur transition ${
          mostrarTrayectorias ? "bg-slate-100 text-slate-900" : "bg-slate-900/85 text-slate-300 hover:text-white"
        }`}
      >
        Trayectorias
      </button>
    </div>
  );
}

/** Panel lateral con el detalle del vehículo seleccionado + tacógrafo (DSTAT). */
function PanelVehiculo({ v, onClose }: { v: MapaVehiculo; onClose: () => void }) {
  const [dstat, setDstat] = useState<DstatInfo | null>(null);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const res = await fetch(REST_TACOGRAFO(encodeURIComponent(v.matricula)), {
          headers: { Authorization: `Bearer ${getToken() ?? ""}` },
        });
        if (!res.ok || cancel) return;
        const d = await res.json();
        if (d.ok && !cancel) setDstat(d);
      } catch {
        /* sin tacógrafo: se deja vacío */
      }
    })();
    return () => {
      cancel = true;
    };
  }, [v.matricula]);

  const enMovimiento = estaEnMovimiento(v);
  const conductor = v.conductor_taco || v.conductor;
  const fila = "flex justify-between gap-3";

  return (
    <div className="absolute right-2 top-2 z-[1000] w-72 rounded-xl border border-slate-200 bg-white p-3 text-sm shadow-xl">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-base font-semibold text-slate-800">{v.matricula}</div>
          <div className="text-xs text-slate-500">{conductor || "—"}</div>
        </div>
        <button
          onClick={onClose}
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          aria-label="Cerrar"
        >
          ✕
        </button>
      </div>

      <div className="mt-2 space-y-1 text-slate-700">
        <div className={fila}>
          <span className="text-slate-500">Estado</span>
          <span>{v.disponibilidad === "Libre" ? "Libre" : enMovimiento ? "En ruta" : "Parado"}</span>
        </div>
        {v.velocidad != null && (
          <div className={fila}>
            <span className="text-slate-500">Velocidad</span>
            <span>{Math.round(v.velocidad)} km/h</span>
          </div>
        )}
        {v.odometer_km != null && (
          <div className={fila}>
            <span className="text-slate-500">Odómetro</span>
            <span>{Math.round(v.odometer_km).toLocaleString("es-ES")} km</span>
          </div>
        )}
        {enMovimiento && v.heading != null && (
          <div className={fila}>
            <span className="text-slate-500">Rumbo</span>
            <span>{Math.round(v.heading)}°</span>
          </div>
        )}
      </div>

      {dstat && (
        <div className="mt-2 border-t border-slate-100 pt-2">
          <div className="mb-1 text-xs font-semibold text-slate-500">Tacógrafo</div>
          <div className="space-y-0.5 text-xs text-slate-600">
            {dstat.day_driving_min != null && (
              <div className={fila}>
                <span>Conducción hoy</span>
                <span>{fmtMin(dstat.day_driving_min)}</span>
              </div>
            )}
            {dstat.dia_restante_min != null && (
              <div className={fila}>
                <span>Restante hoy</span>
                <span>{fmtMin(dstat.dia_restante_min)}</span>
              </div>
            )}
            {dstat.conduccion_continua_restante_min != null && (
              <div className={fila}>
                <span>Continua restante</span>
                <span>{fmtMin(dstat.conduccion_continua_restante_min)}</span>
              </div>
            )}
            {dstat.remaining_week_available_min != null && (
              <div className={fila}>
                <span>Semana restante</span>
                <span>{fmtMin(dstat.remaining_week_available_min)}</span>
              </div>
            )}
            {dstat.next_rest_due && (
              <div className={fila}>
                <span>Descanso a las</span>
                <span>{dstat.next_rest_due}</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- componente

export function LiveMap({ focus }: { focus: FocusMapa | null }) {
  const [vehiculos, setVehiculos] = useState<Map<string, MapaVehiculo>>(new Map());
  const [trayectorias, setTrayectorias] = useState<Map<string, [number, number][]>>(new Map());
  const [filtro, setFiltro] = useState<Filtro>("todos");
  const [mostrarTrayectorias, setMostrarTrayectorias] = useState(false);
  const [seleccionado, setSeleccionado] = useState<MapaVehiculo | null>(null);
  const subscribe = useSocketSubscribe();

  // Carga inicial + refresco periódico (heading/odómetro/conductor de tacógrafo).
  useEffect(() => {
    let cancelado = false;
    const cargar = async () => {
      try {
        const res = await fetch(REST_TELEMETRIA, {
          headers: { Authorization: `Bearer ${getToken() ?? ""}` },
        });
        if (!res.ok || cancelado) return;
        const data = await res.json();
        const lista: TelemetriaActiva[] = data?.telemetria ?? [];
        const mapa = new Map<string, MapaVehiculo>();
        for (const t of lista) {
          const lat = Number(t.lat);
          const lng = Number(t.lng);
          if (!isFinite(lat) || !isFinite(lng)) continue;
          const clave = t.viaje_id || t.vehiculo_id;
          mapa.set(clave, {
            id: clave,
            matricula: t.matricula,
            conductor: t.conductor,
            conductor_taco: t.conductor_taco ?? "",
            disponibilidad: t.disponibilidad,
            fecha_esperada_descarga: t.fecha_esperada_descarga,
            velocidad: t.velocidad,
            heading: t.heading ?? null,
            odometer_km: t.odometer_km ?? null,
            lat,
            lng,
          });
        }
        if (!cancelado) setVehiculos(mapa);
      } catch (err) {
        console.error("Error cargando telemetría activa:", err);
      }
    };
    cargar();
    const t = setInterval(cargar, 30000);
    return () => {
      cancelado = true;
      clearInterval(t);
    };
  }, []);

  // Trayectorias: se cargan solo cuando el usuario activa el toggle.
  useEffect(() => {
    if (!mostrarTrayectorias) return;
    let cancelado = false;
    (async () => {
      try {
        const res = await fetch(REST_TRAYECTORIAS, {
          headers: { Authorization: `Bearer ${getToken() ?? ""}` },
        });
        if (!res.ok || cancelado) return;
        const data = await res.json();
        const mapa = new Map<string, [number, number][]>();
        for (const [k, pts] of Object.entries(data?.trayectorias ?? {})) {
          const arr = (pts as unknown[])
            .map((p) => [Number((p as number[])[0]), Number((p as number[])[1])] as [number, number])
            .filter(([a, b]) => isFinite(a) && isFinite(b));
          if (arr.length >= 2) mapa.set(k, arr);
        }
        if (!cancelado) setTrayectorias(mapa);
      } catch (err) {
        console.error("Error cargando trayectorias:", err);
      }
    })();
    return () => {
      cancelado = true;
    };
  }, [mostrarTrayectorias]);

  // WS → mueve el marcador y actualiza heading/odómetro en tiempo real.
  useEffect(() => {
    return subscribe((evt: ViajeEvent) => {
      if (evt.tipo !== "telemetria") return;
      const lat = Number(evt.lat);
      const lng = Number(evt.lng);
      if (!isFinite(lat) || !isFinite(lng)) return;
      setVehiculos((prev) => {
        const v = prev.get(evt.id);
        if (!v) return prev;
        const next = new Map(prev);
        next.set(evt.id, {
          ...v,
          lat,
          lng,
          velocidad: evt.velocidad,
          heading: evt.heading ?? v.heading,
          odometer_km: evt.odometer_km ?? v.odometer_km,
          fecha_esperada_descarga: evt.fecha_esperada_descarga ?? v.fecha_esperada_descarga,
          disponibilidad: evt.disponibilidad ?? v.disponibilidad,
        });
        return next;
      });
      // Mantén el panel lateral sincronizado si es el vehículo seleccionado.
      setSeleccionado((sel) => {
        if (!sel || sel.id !== evt.id) return sel;
        return {
          ...sel,
          lat,
          lng,
          velocidad: evt.velocidad,
          heading: evt.heading ?? sel.heading,
          odometer_km: evt.odometer_km ?? sel.odometer_km,
          disponibilidad: evt.disponibilidad ?? sel.disponibilidad,
        };
      });
    });
  }, [subscribe]);

  const marcadores = useMemo(() => {
    const lista = Array.from(vehiculos.values());
    if (filtro === "todos") return lista;
    return lista.filter((v) => {
      if (filtro === "libres") return v.disponibilidad === "Libre";
      if (filtro === "parados") return v.disponibilidad === "En_Viaje" && !estaEnMovimiento(v);
      if (filtro === "en_ruta") return v.disponibilidad === "En_Viaje" && estaEnMovimiento(v);
      return true;
    });
  }, [vehiculos, filtro]);

  const polilineas = useMemo(() => {
    const out: { id: string; pts: [number, number][] }[] = [];
    for (const v of marcadores) {
      const pts = trayectorias.get(v.matricula);
      if (pts && pts.length >= 2) out.push({ id: v.id, pts });
    }
    return out;
  }, [marcadores, trayectorias]);

  return (
    <div className="relative h-full w-full">
      <MapContainer center={[40.0, -3.0]} zoom={6} className="h-full w-full" zoomControl>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <TileLayer
          attribution='Transporte &copy; Esri'
          url="https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}"
        />
        <FlyTo target={focus} />
        <MarkerClusterGroup iconCreateFunction={iconoCluster} chunkedLoading maxClusterRadius={60}>
          {marcadores.map((v) => (
            <VehiculoMarker key={v.id} v={v} onSelect={setSeleccionado} />
          ))}
        </MarkerClusterGroup>
        {mostrarTrayectorias &&
          polilineas.map((p) => (
            <Polyline
              key={p.id}
              positions={p.pts}
              pathOptions={{ color: "#38bdf8", weight: 2, opacity: 0.5 }}
            />
          ))}
      </MapContainer>
      <Filtros
        filtro={filtro}
        setFiltro={setFiltro}
        mostrarTrayectorias={mostrarTrayectorias}
        setMostrarTrayectorias={setMostrarTrayectorias}
      />
      <Leyenda />
      {seleccionado && <PanelVehiculo v={seleccionado} onClose={() => setSeleccionado(null)} />}
    </div>
  );
}
