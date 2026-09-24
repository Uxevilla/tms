import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useSocketSubscribe } from "../context/SocketContext";
import type { ViajeEvent, Viaje, TelemetriaActiva } from "../types";

/**
 * Adaptador del WebSocket → TanStack Query. Cada evento creado/estado/telemetria/
 * eliminado actualiza las queries ['viajes'], ['viaje', codigo] y ['telemetria']
 * con setQueryData (sin re-fetch). Las pantallas migradas a Query (Fase 4+) lo
 * consumen; por ahora es infraestructura lista, sin consumidores.
 */
export function WsQueryAdapter() {
  const qc = useQueryClient();
  const subscribe = useSocketSubscribe();

  useEffect(() => {
    return subscribe((evt: ViajeEvent) => {
      if (evt.tipo === "creado") {
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old ? (old.some((v) => v.id === evt.viaje.id) ? old : [...old, evt.viaje]) : [evt.viaje],
        );
        qc.setQueryData(["viaje", evt.viaje.id], evt.viaje);
      } else if (evt.tipo === "estado") {
        qc.setQueryData(["viajes"], (old: Viaje[] | undefined) =>
          old?.map((v) => (v.id === evt.id ? { ...v, estado: evt.estado } : v)),
        );
        qc.setQueryData(["viaje", evt.id], (old: Viaje | undefined) =>
          old ? { ...old, estado: evt.estado } : old,
        );
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
      }
    });
  }, [qc, subscribe]);

  return null;
}
