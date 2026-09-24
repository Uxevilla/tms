import "maplibre-gl/dist/maplibre-gl.css";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { setWorkerUrl } from "maplibre-gl";
import Map, { Marker, NavigationControl } from "react-map-gl/maplibre";
import type { MapRef } from "react-map-gl/maplibre";
import type { StyleSpecification } from "maplibre-gl";

// Vite no bundlea el worker de maplibre por defecto; lo resolvemos explícitamente.
setWorkerUrl(maplibreWorkerUrl);

import { MAP_STYLE_URL } from "@/config";
import type { TelemetriaActiva } from "@/types";

// Estilo raster por defecto (OSM + red de transporte) cuando no hay VITE_MAP_STYLE_URL.
const OSM_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap",
    },
    transporte: {
      type: "raster",
      tiles: [
        "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
    },
  },
  layers: [
    { id: "osm", type: "raster", source: "osm" },
    { id: "transporte", type: "raster", source: "transporte" },
  ],
};

/** VERDE = libre · AMARILLO = en ruta a tiempo · ROJO = retrasado (pasó la descarga). */
function colorVehiculo(v: TelemetriaActiva): string {
  if (v.disponibilidad === "Libre") return "#16a34a";
  const fe = v.fecha_esperada_descarga;
  if (fe) {
    const d = new Date(fe);
    if (!isNaN(d.getTime()) && Date.now() > d.getTime()) return "#dc2626";
  }
  return "#f59e0b";
}

const UMBRAL_MOVIMIENTO_KMH = 3;
const enMovimiento = (v: TelemetriaActiva) => (v.velocidad ?? 0) > UMBRAL_MOVIMIENTO_KMH;

function Marcador({ v, onSelect }: { v: TelemetriaActiva; onSelect: (v: TelemetriaActiva) => void }) {
  const mov = enMovimiento(v);
  const color = colorVehiculo(v);
  const angulo = Number(v.heading) || 0;
  const inner = mov ? (
    <svg
      width="30"
      height="30"
      viewBox="0 0 24 24"
      style={{ transform: `rotate(${angulo}deg)`, display: "block" }}
    >
      <path d="M12 3 L20 20 L12 15.5 L4 20 Z" fill={color} stroke="#ffffff" strokeWidth="1.5" />
    </svg>
  ) : (
    <svg width="16" height="16" viewBox="0 0 24 24" style={{ display: "block" }}>
      <circle cx="12" cy="12" r="7" fill="#111827" stroke="#ffffff" strokeWidth="2" />
    </svg>
  );
  return (
    <Marker
      longitude={v.lng}
      latitude={v.lat}
      anchor="center"
      onClick={(e) => {
        e.originalEvent.stopPropagation();
        onSelect(v);
      }}
    >
      <button type="button" className="cursor-pointer border-0 bg-transparent p-0" title={v.matricula}>
        {inner}
      </button>
    </Marker>
  );
}

interface MapaLibreProps {
  vehiculos: TelemetriaActiva[];
  onSelect: (v: TelemetriaActiva) => void;
}

/** Mapa en vivo (MapLibre) con los camiones coloreados por estado y orientados según heading. */
export function MapaLibre({ vehiculos, onSelect }: MapaLibreProps) {
  const mapRef = useRef<MapRef>(null);
  const [listo, setListo] = useState(false);
  // El encuadre automático se hace SOLO la primera vez; las actualizaciones de
  // telemetría no deben mover el mapa (para no pelear con el usuario que explora).
  const encuadrado = useRef(false);

  const encuadrarFlota = useCallback(() => {
    const conPos = vehiculos.filter((v) => Number.isFinite(v.lng) && Number.isFinite(v.lat));
    if (conPos.length === 0) return;
    const lngs = conPos.map((v) => v.lng);
    const lats = conPos.map((v) => v.lat);
    const bounds: [[number, number], [number, number]] = [
      [Math.min(...lngs), Math.min(...lats)],
      [Math.max(...lngs), Math.max(...lats)],
    ];
    // Un único punto: subir el zoom máximo para no quedarnos pegados al suelo.
    const opts = conPos.length === 1 ? { padding: 60, maxZoom: 12 } : { padding: 60 };
    mapRef.current?.fitBounds(bounds, { ...opts, duration: 800 });
  }, [vehiculos]);

  useEffect(() => {
    if (!listo || encuadrado.current) return;
    encuadrarFlota();
    encuadrado.current = true;
  }, [listo, encuadrarFlota]);

  return (
    <div className="relative h-full w-full">
      <Map
        ref={mapRef}
        onLoad={() => setListo(true)}
        mapStyle={MAP_STYLE_URL || (OSM_STYLE as unknown as string)}
        initialViewState={{ longitude: -3.0, latitude: 40.0, zoom: 6 }}
        style={{ width: "100%", height: "100%" }}
        reuseMaps
      >
        <NavigationControl position="bottom-right" />
        <button
          type="button"
          onClick={encuadrarFlota}
          className="absolute left-3 top-3 z-10 rounded-md border bg-card/90 px-2.5 py-1.5 text-xs font-medium text-foreground shadow backdrop-blur hover:bg-card"
        >
          Encuadrar flota
        </button>
        {vehiculos.map((v) => (
          <Marcador key={v.vehiculo_id} v={v} onSelect={onSelect} />
        ))}
      </Map>
      <Leyenda />
    </div>
  );
}

function Leyenda() {
  const item = "flex items-center gap-2";
  const dot = "inline-block h-3 w-3 rounded-full";
  return (
    <div className="absolute bottom-4 left-4 z-10 rounded-lg bg-slate-900/85 px-3 py-2 text-[11px] text-slate-200 shadow-lg backdrop-blur">
      <div className="mb-1 font-semibold text-white">Leyenda</div>
      <div className={item}>
        <span className={dot} style={{ background: "#16a34a" }} /> Libre
      </div>
      <div className={item}>
        <span className={dot} style={{ background: "#f59e0b" }} /> En ruta
      </div>
      <div className={item}>
        <span className={dot} style={{ background: "#dc2626" }} /> Retrasado
      </div>
      <div className={item}>
        <span className={`${dot} border border-white`} style={{ background: "#111827" }} /> Parado
      </div>
    </div>
  );
}
