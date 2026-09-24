import { lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { api } from "@/api";
import { getRol } from "@/auth";
import { REST_ATENCION, REST_TELEMETRIA, REST_VIAJES } from "@/config";
import type { AtencionItem, AtencionRespuesta, TelemetriaActiva, Viaje } from "@/types";

// MapLibre carga diferida: se descarga después del primer pintado (no entra en el JS inicial).
const MapaLibre = lazy(() => import("./MapaLibre").then((m) => ({ default: m.MapaLibre })));

const SEVERIDAD_ETIQUETA: Record<string, string> = { critico: "Crítico", aviso: "Aviso", info: "Info" };
const SEVERIDAD_COLOR: Record<string, string> = {
  critico: "bg-red-500/15 text-red-500 border-red-500/30",
  aviso: "bg-amber-500/15 text-amber-500 border-amber-500/30",
  info: "bg-sky-500/15 text-sky-500 border-sky-500/30",
};

function Kpi({ label, valor, sub, soloAdmin }: { label: string; valor: string; sub?: string; soloAdmin?: boolean }) {
  if (soloAdmin && getRol() !== "admin") return null;
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{valor}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export function TorreDashboard() {
  const navigate = useNavigate();

  const telemetria = useQuery({
    queryKey: ["telemetria"],
    queryFn: () => api<{ telemetria: TelemetriaActiva[] }>(REST_TELEMETRIA).then((r) => r.telemetria),
    refetchInterval: 30000,
  });
  const atencion = useQuery({
    queryKey: ["atencion"],
    queryFn: () => api<AtencionRespuesta>(REST_ATENCION),
    refetchInterval: 60000,
  });
  const viajes = useQuery({
    queryKey: ["viajes"],
    queryFn: () => api<{ viajes: Viaje[] }>(REST_VIAJES).then((r) => r.viajes),
    refetchInterval: 60000,
  });

  const vehiculos = telemetria.data ?? [];
  const enRuta = vehiculos.filter((v) => v.disponibilidad === "En_Viaje" && (v.velocidad ?? 0) > 3).length;
  const libres = vehiculos.filter((v) => v.disponibilidad === "Libre").length;
  const parados = vehiculos.filter((v) => v.disponibilidad === "En_Viaje" && (v.velocidad ?? 0) <= 3).length;

  // Viajes de hoy: carga o descarga prevista hoy (fecha LOCAL, no UTC); "hechos" = entregados.
  const now = new Date();
  const hoy = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const viajesHoy = (viajes.data ?? []).filter(
    (v) => (v.fecha_esperada_carga ?? "").startsWith(hoy) || (v.fecha_esperada_descarga ?? "").startsWith(hoy),
  );
  const viajesHechos = viajesHoy.filter((v) => v.estado === "Entregado").length;

  const resumen = atencion.data?.resumen;
  const esAdmin = getRol() === "admin";

  const abrirAtencion = (item: AtencionItem) => {
    const t = item.entidad.tipo;
    const id = item.entidad.codigo || item.entidad.id;
    if (t === "vehiculo" || t === "viaje" || t === "conductor") {
      navigate({ search: { panel: `${t}:${id}` } as never });
    } else if (t === "factura") {
      navigate({ to: "/facturacion" });
    } else {
      navigate({ to: "/gastos" });
    }
  };

  const accionAtencion = (item: AtencionItem, acc: { id: string; label: string }) => {
    if (acc.id === "facturar") navigate({ to: "/facturacion" });
    else abrirAtencion(item);
  };

  const abrirVehiculo = (v: TelemetriaActiva) => {
    navigate({ search: { panel: `vehiculo:${v.vehiculo_id}` } as never });
  };

  const items = atencion.data?.items ?? [];
  const grupos: { severidad: string; items: AtencionItem[] }[] = ["critico", "aviso", "info"]
    .map((s) => ({ severidad: s, items: items.filter((i) => i.severidad === s) }))
    .filter((g) => g.items.length > 0);

  return (
    <div className="flex h-full flex-col gap-3 overflow-hidden p-3">
      {/* KPIs */}
      <div className="grid shrink-0 grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi
          label="Flota en vivo"
          valor={String(vehiculos.length)}
          sub={`${enRuta} en ruta · ${libres} libres · ${parados} parados`}
        />
        <Kpi label="Viajes de hoy" valor={`${viajesHechos}/${viajesHoy.length}`} sub="hechos / total" />
        <Kpi label="Alertas críticas" valor={String(resumen?.critico ?? 0)} sub={`${resumen?.aviso ?? 0} avisos`} />
        <Kpi
          label="Facturación pendiente"
          valor={(resumen?.facturacion_pendiente ?? 0).toLocaleString("es-ES", { style: "currency", currency: "EUR" })}
          soloAdmin
        />
      </div>

      {/* Bandeja (60%) + mapa (40%) */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-5">
        <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border lg:col-span-3">
          <h2 className="shrink-0 border-b px-3 py-2 text-sm font-semibold">Requiere atención</h2>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {atencion.isLoading && <div className="p-3 text-sm text-muted-foreground">Cargando…</div>}
            {!atencion.isLoading && grupos.length === 0 && (
              <div className="p-3 text-sm text-muted-foreground">Sin avisos pendientes.</div>
            )}
            {grupos.map((g) => (
              <div key={g.severidad} className="mb-2">
                <div className="flex items-center gap-2 px-1 py-1 text-xs font-semibold text-muted-foreground">
                  <span className={`rounded border px-1.5 py-0.5 ${SEVERIDAD_COLOR[g.severidad]}`}>
                    {SEVERIDAD_ETIQUETA[g.severidad]}
                  </span>
                  <span>{g.items.length}</span>
                </div>
                {g.items.map((it) => (
                  <div key={it.id} className="mb-1 rounded-md border px-2 py-1.5">
                    <button onClick={() => abrirAtencion(it)} className="w-full text-left">
                      <div className="text-sm font-medium">{it.titulo}</div>
                      <div className="text-xs text-muted-foreground">{it.detalle}</div>
                    </button>
                    {it.acciones.length > 0 && (
                      <div className="mt-1 flex gap-1">
                        {it.acciones.map((acc) => (
                          <button
                            key={acc.id}
                            onClick={() => accionAtencion(it, acc)}
                            className="rounded border px-2 py-0.5 text-xs transition hover:bg-secondary"
                          >
                            {acc.label}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </section>

        <section className="relative min-h-[300px] overflow-hidden rounded-lg border lg:col-span-2">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                Cargando mapa…
              </div>
            }
          >
            <MapaLibre vehiculos={vehiculos} onSelect={abrirVehiculo} />
          </Suspense>
        </section>
      </div>
    </div>
  );
}
