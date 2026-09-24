import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useSocketSubscribe } from "../context/SocketContext";
import type { ViajeEvent, Viaje, TelemetriaActiva } from "../types";

/**
 * Adaptador del WebSocket → TanStack Query. Los eventos de viajes/telemetría
 * actualizan ['viajes'], ['viaje', codigo] y ['telemetria'] con setQueryData
 * (sin re-fetch). Para el tablero de planificación NO se puede reconstruir el
 * viaje desde el evento (no trae terminal ni fechas), así que 'creado'/'estado'/
 * 'eliminado' invalidan ['planificacion'] con debounce de 1 s (una única petición
 * por ráfaga de eventos).
 */
export function WsQueryAdapter() {
  const qc = useQueryClient();
  const subscribe = useSocketSubscribe();
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    return subscribe((evt: ViajeEvent) => {
      const invalidarPlanificacion = () => {
        if (debounceRef.current !== null) window.clearTimeout(debounceRef.current);
        debounceRef.current = window.setTimeout(() => {
          debounceRef.current = null;
          qc.invalidateQueries({ queryKey: ["planificacion"] });
        }, 1000);
      };

      if (evt.tipo === "creado") {
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old ? (old.some((v) => v.id === evt.viaje.id) ? old : [...old, evt.viaje]) : [evt.viaje],
        );
        qc.setQueryData(["viaje", evt.viaje.id], evt.viaje);
        invalidarPlanificacion();
      } else if (evt.tipo === "estado") {
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old?.map((v) => (v.id === evt.id ? { ...v, estado: evt.estado } : v)),
        );
        qc.setQueryData(["viaje", evt.id], (old: Viaje | undefined) =>
          old ? { ...old, estado: evt.estado } : old,
        );
        invalidarPlanificacion();
      } else if (evt.tipo === "telemetria") {
        const patch = { velocidad: evt.velocidad, progreso: evt.progreso, lat: evt.lat, lng: evt.lng };
        // Lista de viajes + viaje individual: posición y velocidad SIN re-fetch.
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old?.map((v) => (v.id === evt.id ? { ...v, ...patch } : v)),
        );
        qc.setQueryData(["viaje", evt.id], (old: Viaje | undefined) =>
          old ? { ...old, ...patch } : old,
        );
        // Lista de telemetría (posiciones por vehículo): actualiza sin re-fetch
        // (ninguna petición HTTP por cada evento de telemetría).
        qc.setQueryData(["telemetria"], (old: TelemetriaActiva[] | undefined) =>
          old?.map((v) =>
            v.viaje_id === evt.id
              ? { ...v, velocidad: evt.velocidad, lat: evt.lat, lng: evt.lng }
              : v,
          ),
        );
      } else if (evt.tipo === "eliminado") {
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old?.filter((v) => v.id !== evt.id),
        );
        qc.removeQueries({ queryKey: ["viaje", evt.id] });
        invalidarPlanificacion();
      }
    });
  }, [qc, subscribe]);

  return null;
}
