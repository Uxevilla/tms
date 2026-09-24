import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Truck, CalendarDays, CalendarRange, X, Undo2 } from "lucide-react";

import { api } from "@/api";
import { REST_ASIGNAR, REST_CONDUCTORES, REST_PLANIFICACION, REST_PLANIFICACION_VALIDAR, REST_VEHICULOS_DISPONIBLES } from "@/config";
import type { ValidacionResultado, VehiculoPlanificacion, ViajePlanificacion } from "@/types";

type Vista = "dia" | "semana";

interface Semirremolque {
  id: string;
  matricula: string;
  disponible: boolean;
  motivo_bloqueo: string | null;
}
interface ConductorOpcion {
  id: number;
  nombre: string;
  disponible: boolean;
  motivo_ausencia: string | null;
}

// Píxeles por unidad de tiempo.
const HORA_PX = 48;
const DIA_PX = 150;
const ROW_H = 56;

function aMinutos(iso: string): number {
  if (!iso) return 0;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 0;
  return d.getHours() * 60 + d.getMinutes();
}
function aDiaSemana(iso: string): number {
  if (!iso) return 0;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return 0;
  return (d.getDay() + 6) % 7; // lunes = 0
}

/** Posición/anchura de un viaje dentro del eje de tiempo (px). */
function ventanaPx(inicio: string, fin: string, vista: Vista): { left: number; width: number } {
  if (vista === "dia") {
    const i = aMinutos(inicio);
    const f = aMinutos(fin);
    const dur = Math.max(30, f - i); // mínimo 30 min visibles
    return { left: (i / 60) * HORA_PX, width: (dur / 60) * HORA_PX };
  }
  const d0 = aDiaSemana(inicio);
  const d1 = aDiaSemana(fin);
  const dur = Math.max(1, (fin ? d1 - d0 + 1 : 1));
  return { left: d0 * DIA_PX, width: dur * DIA_PX };
}

function fmtHora(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

export function PlanificacionDashboard() {
  const qc = useQueryClient();
  const [vista, setVista] = useState<Vista>("dia");

  // LISTAS (no objetos {viajes:[…]}): clave para que setQueryData del WS actualice sin refetch.
  const viajes = useQuery({
    queryKey: ["planificacion"],
    queryFn: () => api<{ viajes: ViajePlanificacion[] }>(REST_PLANIFICACION).then((r) => r.viajes),
    refetchInterval: 30000,
  });
  const tractoras = useQuery({
    queryKey: ["planificacion-vehiculos"],
    queryFn: () => api<{ vehiculos: VehiculoPlanificacion[] }>(REST_PLANIFICACION).then((r) => r.vehiculos),
    refetchInterval: 60000,
  });
  const semirremolques = useQuery({
    queryKey: ["planificacion-semirremolques"],
    queryFn: () =>
      api<{ vehiculos: Semirremolque[] }>(`${REST_VEHICULOS_DISPONIBLES}?categoria=semirremolque`).then((r) => r.vehiculos),
    refetchInterval: 60000,
  });
  const conductores = useQuery({
    queryKey: ["planificacion-conductores"],
    queryFn: () => api<{ conductores: ConductorOpcion[] }>(REST_CONDUCTORES).then((r) => r.conductores),
    refetchInterval: 60000,
  });

  const lista = viajes.data ?? [];
  const pendientes = useMemo(() => lista.filter((v) => !v.terminal), [lista]);
  const asignados = useMemo(() => lista.filter((v) => !!v.terminal), [lista]);
  const tractoraList = tractoras.data ?? [];

  // ---- Arrastre (pointer events; testeable con page.mouse) ----
  const [arrastre, setArrastre] = useState<ViajePlanificacion | null>(null);
  const [sobreTractora, setSobreTractora] = useState<string | null>(null);
  const [validacion, setValidacion] = useState<ValidacionResultado | null>(null);
  const [popover, setPopover] = useState<{ viaje: ViajePlanificacion; tractora: string; x: number; y: number } | null>(null);
  const [toastUndo, setToastUndo] = useState<{ viaje: ViajePlanificacion; tractora: string } | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const validar = useCallback(async (v: ViajePlanificacion, tractora: string): Promise<ValidacionResultado | null> => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      return await api<ValidacionResultado>(REST_PLANIFICACION_VALIDAR, {
        method: "POST",
        body: JSON.stringify({
          trip_id: v.id, terminal: tractora,
          semirremolque_id: v.semirremolque_id, remolque_id: v.remolque_id,
          conductor_id: v.conductor_id, inicio: v.inicio, fin: v.fin,
          kilos: v.kilos, palets: v.palets,
        }),
        signal: ac.signal,
      });
    } catch (e) {
      if ((e as Error).name === "AbortError") return null;
      console.error("[planificacion] error validando asignación:", e);
      return null;
    }
  }, []);

  // Validar (debounce + cancelación) cuando el puntero está sobre una tractora.
  useEffect(() => {
    if (!arrastre || !sobreTractora) {
      setValidacion(null);
      return;
    }
    const timer = setTimeout(() => {
      validar(arrastre, sobreTractora).then((r) => setValidacion(r));
    }, 150);
    return () => clearTimeout(timer);
  }, [arrastre, sobreTractora, validar]);

  // Seguimiento del puntero durante el arrastre.
  useEffect(() => {
    if (!arrastre) return;
    const onMove = (e: PointerEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (tid !== sobreTractora) setSobreTractora(tid);
    };
    const onUp = (e: PointerEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (tid && arrastre) setPopover({ viaje: arrastre, tractora: tid, x: e.clientX, y: e.clientY });
      setArrastre(null);
      setSobreTractora(null);
      setValidacion(null);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
  }, [arrastre, sobreTractora]);

  const confirmarAsignacion = useCallback(async (v: ViajePlanificacion, tractora: string, semi: string, cond: number | null) => {
    const qcViejos = qc.getQueryData<ViajePlanificacion[]>(["planificacion"]) ?? [];
    const anterior = qcViejos.find((x) => x.id === v.id) ?? v;
    // Optimista: mover el viaje a la tractora inmediatamente.
    qc.setQueryData<ViajePlanificacion[]>(["planificacion"], (old) =>
      (old ?? []).map((x) => (x.id === v.id ? { ...x, terminal: tractora, semirremolque_id: semi, conductor_id: cond } : x)),
    );
    setPopover(null);
    try {
      const conductor = conductores.data?.find((c) => c.id === cond);
      await api(REST_ASIGNAR(v.id), {
        method: "POST",
        body: JSON.stringify({
          terminal: tractora, semirremolque_id: semi, remolque_id: "",
          conductor: conductor?.nombre ?? "", conductor_id: cond,
        }),
      });
      setToastUndo({ viaje: v, tractora });
    } catch (e) {
      // Reversión + toast de error si el servidor falla.
      qc.setQueryData<ViajePlanificacion[]>(["planificacion"], (old) =>
        (old ?? []).map((x) => (x.id === v.id ? anterior : x)),
      );
      console.error("[planificacion] fallo al asignar:", e);
      window.alert("No se pudo asignar el viaje. Se ha revertido.");
    }
  }, [qc, conductores.data]);

  const deshacer = useCallback(async (v: ViajePlanificacion) => {
    // Desasignar: PATCH terminal="" (se revierte la asignación).
    qc.setQueryData<ViajePlanificacion[]>(["planificacion"], (old) =>
      (old ?? []).map((x) => (x.id === v.id ? { ...x, terminal: "" } : x)),
    );
    setToastUndo(null);
    try {
      await api(`/api/trips/${encodeURIComponent(v.id)}`, { method: "PATCH", body: JSON.stringify({ terminal: "" }) });
    } catch (e) {
      console.error("[planificacion] fallo al desasignar:", e);
    }
  }, [qc]);

  const anchoEje = vista === "dia" ? 24 * HORA_PX : 7 * DIA_PX;

  const colorValidacion = !sobreTractora
    ? ""
    : !validacion
      ? "ring-1 ring-inset ring-sky-400/60 bg-sky-400/5"
      : !validacion.ok
        ? "ring-2 ring-inset ring-red-500/70 bg-red-500/10"
        : (validacion.avisos?.length ?? 0) > 0
          ? "ring-2 ring-inset ring-amber-500/70 bg-amber-500/10"
          : "ring-2 ring-inset ring-green-500/70 bg-green-500/10";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-2">
        <h1 className="text-lg font-semibold">Planificación</h1>
        <div className="ml-auto flex items-center gap-1 rounded-md border p-0.5">
          <button onClick={() => setVista("dia")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "dia" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
            <CalendarDays size={14} /> Día
          </button>
          <button onClick={() => setVista("semana")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "semana" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
            <CalendarRange size={14} /> Semana
          </button>
        </div>
        <span className="text-xs text-muted-foreground">Ctrl + rueda para hacer zoom (próximamente)</span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Columna "Pendientes de asignar" */}
        <aside className="flex w-60 shrink-0 flex-col border-r">
          <div className="border-b px-3 py-2 text-xs font-semibold text-muted-foreground">Pendientes de asignar</div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {pendientes.length === 0 && <div className="px-1 text-xs text-muted-foreground">Sin viajes pendientes.</div>}
            {pendientes.map((v) => (
              <div
                key={v.id}
                data-viaje-pendiente={v.id}
                onPointerDown={(e) => { e.preventDefault(); setArrastre(v); }}
                className="mb-1.5 cursor-grab select-none rounded-md border bg-card p-2 shadow-sm active:cursor-grabbing"
                style={{ pointerEvents: arrastre?.id === v.id ? "none" : undefined }}
              >
                <div className="truncate text-xs font-medium">{v.id}</div>
                <div className="truncate text-[11px] text-muted-foreground">{v.origen} → {v.destino}</div>
                <div className="mt-0.5 text-[11px] text-muted-foreground">{v.cliente || "—"} · {v.kilos > 0 ? `${v.kilos} kg` : "—"}</div>
              </div>
            ))}
          </div>
        </aside>

        {/* Eje de tiempo */}
        <div className="min-w-0 flex-1 overflow-auto">
          <div style={{ width: anchoEje + 160, minWidth: "100%" }}>
            {/* Cabecera temporal */}
            <div className="sticky top-0 z-10 flex border-b bg-background">
              <div className="w-40 shrink-0" />
              <div className="relative flex-1" style={{ height: 28 }}>
                {vista === "dia"
                  ? Array.from({ length: 24 }, (_, h) => (
                      <span key={h} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: h * HORA_PX }}>{String(h).padStart(2, "0")}</span>
                    ))
                  : Array.from({ length: 7 }, (_, d) => (
                      <span key={d} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: d * DIA_PX }}>{DIA_NOMBRES[d]}</span>
                    ))}
              </div>
            </div>

            {tractoraList.map((t) => {
              const viajesDeTractora = asignados.filter((v) => v.terminal === t.id);
              return (
                <div key={t.id} data-tractora={t.id} className={`flex border-b transition-colors ${sobreTractora === t.id ? colorValidacion : ""}`}>
                  <div className="flex w-40 shrink-0 flex-col justify-center px-3">
                    <div className="truncate text-xs font-semibold">{t.matricula || t.id}</div>
                    <div className="text-[10px] text-muted-foreground">{viajesDeTractora.length} viaje(s)</div>
                  </div>
                  <div className="relative flex-1" style={{ height: ROW_H }}>
                    {viajesDeTractora.map((v) => {
                      const { left, width } = ventanaPx(v.inicio, v.fin, vista);
                      return (
                        <div
                          key={v.id}
                          data-viaje-bloque={v.id}
                          onPointerDown={(e) => { e.preventDefault(); setArrastre(v); }}
                          className="absolute top-1 cursor-grab overflow-hidden rounded border border-sky-600/40 bg-sky-500/20 px-1.5 py-0.5 text-[10px] active:cursor-grabbing"
                          style={{ left, width, height: ROW_H - 8, pointerEvents: arrastre?.id === v.id ? "none" : undefined }}
                          title={`${v.id} · ${v.origen} → ${v.destino} · ${fmtHora(v.inicio)}–${fmtHora(v.fin)}`}
                        >
                          <div className="truncate font-medium">{v.id}</div>
                          <div className="truncate text-muted-foreground">{fmtHora(v.inicio)}–{fmtHora(v.fin)}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Popover de asignación (semirremolque + conductor) */}
      {popover && <PopoverAsignacion popover={popover} semirremolques={semirremolques.data ?? []} conductores={conductores.data ?? []} viajes={lista} onCancelar={() => setPopover(null)} onConfirmar={(semi, cond) => confirmarAsignacion(popover.viaje, popover.tractora, semi, cond)} />}

      {/* Toast de deshacer (10 s) */}
      {toastUndo && (
        <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg border bg-card px-4 py-2 text-sm shadow-lg">
          <span>Asignado a {toastUndo.tractora}</span>
          <button onClick={() => deshacer(toastUndo.viaje)} className="flex items-center gap-1 rounded border px-2 py-1 text-xs font-medium hover:bg-muted">
            <Undo2 size={14} /> Deshacer
          </button>
          <button onClick={() => setToastUndo(null)} className="text-muted-foreground hover:text-foreground"><X size={14} /></button>
        </div>
      )}
    </div>
  );
}

function PopoverAsignacion({ popover, semirremolques, conductores, viajes, onCancelar, onConfirmar }: {
  popover: { viaje: ViajePlanificacion; tractora: string; x: number; y: number };
  semirremolques: Semirremolque[];
  conductores: ConductorOpcion[];
  viajes: ViajePlanificacion[];
  onCancelar: () => void;
  onConfirmar: (semi: string, cond: number | null) => void;
}) {
  // Preseleccionar el último semirremolque/conductor usado con esa tractora.
  const ultimo = useMemo(() => {
    const usados = viajes
      .filter((v) => v.terminal === popover.tractora)
      .sort((a, b) => (b.inicio || "").localeCompare(a.inicio || ""));
    return { semi: usados.find((v) => v.semirremolque_id)?.semirremolque_id ?? "", cond: usados.find((v) => v.conductor_id)?.conductor_id ?? null };
  }, [viajes, popover.tractora]);

  const [semi, setSemi] = useState(ultimo.semi);
  const [cond, setCond] = useState<number | null>(ultimo.cond);

  return (
    <div className="fixed z-50 w-72 rounded-lg border bg-card p-3 shadow-xl" style={{ left: Math.min(popover.x, window.innerWidth - 300), top: Math.min(popover.y, window.innerHeight - 260) }}>
      <div className="mb-2 text-sm font-semibold">Asignar {popover.viaje.id} a {popover.tractora}</div>
      <label className="mb-1 block text-xs text-muted-foreground">Semirremolque</label>
      <select value={semi} onChange={(e) => setSemi(e.target.value)} className="mb-2 w-full rounded border bg-background px-2 py-1 text-sm">
        <option value="">— Sin semirremolque —</option>
        {semirremolques.map((s) => (
          <option key={s.id} value={s.id} disabled={s.disponible === false}>{s.matricula}{s.disponible === false ? ` (⛔ ${s.motivo_bloqueo})` : ""}</option>
        ))}
      </select>
      <label className="mb-1 block text-xs text-muted-foreground">Conductor</label>
      <select value={cond ?? ""} onChange={(e) => setCond(e.target.value ? Number(e.target.value) : null)} className="mb-3 w-full rounded border bg-background px-2 py-1 text-sm">
        <option value="">— Sin conductor —</option>
        {conductores.map((c) => (
          <option key={c.id} value={c.id} disabled={c.disponible === false}>{c.nombre}{c.disponible === false ? ` (⛔ ${c.motivo_ausencia})` : ""}</option>
        ))}
      </select>
      <div className="flex justify-end gap-2">
        <button onClick={onCancelar} className="rounded border px-2 py-1 text-xs hover:bg-muted">Cancelar</button>
        <button onClick={() => onConfirmar(semi, cond)} className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90">Asignar</button>
      </div>
    </div>
  );
}
