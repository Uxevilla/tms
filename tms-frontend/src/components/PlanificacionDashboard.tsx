import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearch } from "@tanstack/react-router";
import { CalendarDays, CalendarRange, ChevronLeft, ChevronRight, Undo2, X, AlertTriangle } from "lucide-react";

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
interface Toast {
  id: number;
  texto: string;
  tipo: "error" | "info";
}
interface EnvioPendiente {
  timer: number;
  v: ViajePlanificacion;
  tractora: string;
  semi: string;
  cond: number | null;
  conductorNombre: string;
}

const ROW_H = 56;
const DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

function aTs(iso: string): number {
  if (!iso) return NaN;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? NaN : d.getTime();
}
function fmtDiaLocal(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${dd}`;
}
function fmtHora(iso: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function PlanificacionDashboard() {
  const qc = useQueryClient();
  const { viaje: viajeSeleccionado } = useSearch({ from: "/app/planificacion" });
  const [vista, setVista] = useState<Vista>("dia");
  const [pxHora, setPxHora] = useState(48);
  const [anchor, setAnchor] = useState(() => {
    const h = new Date();
    return new Date(h.getFullYear(), h.getMonth(), h.getDate());
  });

  // Rango visible: día completo o semana (lunes→domingo), según la vista.
  const rango = useMemo(() => {
    if (vista === "dia") {
      return { desde: anchor, hasta: new Date(anchor.getTime() + 86400000) };
    }
    const dow = (anchor.getDay() + 6) % 7; // lunes = 0
    const desde = new Date(anchor.getTime() - dow * 86400000);
    return { desde, hasta: new Date(desde.getTime() + 7 * 86400000) };
  }, [anchor, vista]);
  const desdeISO = fmtDiaLocal(rango.desde);
  const hastaISO = fmtDiaLocal(rango.hasta);

  // UNA sola query para vehículos y viajes (el WS invalida esta misma clave).
  const plan = useQuery({
    queryKey: ["planificacion", desdeISO, hastaISO],
    queryFn: () =>
      api<{ vehiculos: VehiculoPlanificacion[]; viajes: ViajePlanificacion[] }>(
        `${REST_PLANIFICACION}?desde=${encodeURIComponent(desdeISO)}&hasta=${encodeURIComponent(hastaISO)}`,
      ),
    refetchInterval: 30000,
  });
  const tractoras = plan.data?.vehiculos ?? [];
  const listaBase = plan.data?.viajes ?? [];

  // Asignaciones pendientes (ventana de deshacer / envío en curso): estado APARTE que se aplica
  // ENCIMA de plan.data con useMemo. Así un refetch (WS o refetchInterval) no devuelve el viaje
  // a Pendientes mientras el envío está pendiente (no se toca la caché).
  const [enCurso, setEnCurso] = useState<Record<string, { matricula: string; semirremolque_id: string; conductor_id: number | null }>>({});

  // Aplicar las asignaciones en curso ENCIMA de los datos del servidor (sin tocar la caché).
  const lista = useMemo(() => {
    if (Object.keys(enCurso).length === 0) return listaBase;
    return listaBase.map((v) => {
      const pend = enCurso[v.id];
      return pend ? { ...v, matricula: pend.matricula, semirremolque_id: pend.semirremolque_id, conductor_id: pend.conductor_id } : v;
    });
  }, [listaBase, enCurso]);
  const pendientes = useMemo(() => lista.filter((v) => !v.matricula), [lista]);
  const asignados = useMemo(() => lista.filter((v) => !!v.matricula), [lista]);

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

  // ---- Eje de tiempo: posición relativa al inicio del rango visible + recorte ----
  const desdeTs = rango.desde.getTime();
  const hastaTs = rango.hasta.getTime();
  const span = hastaTs - desdeTs;
  const pxDia = pxHora * 3;
  const totalWidth = vista === "dia" ? 24 * pxHora : 7 * pxDia;
  const posBloque = (v: ViajePlanificacion): { left: number; width: number } | null => {
    const i = aTs(v.inicio);
    const f = aTs(v.fin);
    if (isNaN(i) || isNaN(f)) return null;
    if (f <= desdeTs || i >= hastaTs) return null; // fuera de rango: no se pinta
    const s = Math.max(i, desdeTs);
    const e = Math.min(f, hastaTs);
    return { left: ((s - desdeTs) / span) * totalWidth, width: Math.max(2, ((e - s) / span) * totalWidth) };
  };

  // ---- Toasts (en lugar de window.alert) ----
  const toastIdRef = useRef(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((texto: string, tipo: "error" | "info" = "info") => {
    const id = ++toastIdRef.current;
    setToasts((t) => [...t, { id, texto, tipo }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  // ---- Arrastre ----
  const [arrastre, setArrastre] = useState<ViajePlanificacion | null>(null);
  const [sobreTractora, setSobreTractora] = useState<string | null>(null);
  const [validacion, setValidacion] = useState<ValidacionResultado | null>(null);
  const [validacionError, setValidacionError] = useState(false);
  const [popover, setPopover] = useState<{ viaje: ViajePlanificacion; tractora: string; x: number; y: number } | null>(null);
  const [confirmacion, setConfirmacion] = useState<{ viaje: ViajePlanificacion; tractora: string; x: number; y: number; avisos: ValidacionResultado["avisos"] } | null>(null);
  const [toastUndo, setToastUndo] = useState<{ viaje: ViajePlanificacion; tractora: string } | null>(null);

  const validacionRef = useRef<ValidacionResultado | null>(null);
  const validacionErrorRef = useRef(false);
  const validacionGen = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const pendientesEnvioRef = useRef<Map<string, EnvioPendiente>>(new Map());
  const arrastreRef = useRef<ViajePlanificacion | null>(null);
  const sobreTractoraRef = useRef<string | null>(null);

  const validar = useCallback(
    async (v: ViajePlanificacion, tractora: string, semi: string, cond: number | null, signal?: AbortSignal): Promise<ValidacionResultado | null> => {
      try {
        return await api<ValidacionResultado>(REST_PLANIFICACION_VALIDAR, {
          method: "POST",
          body: JSON.stringify({
            trip_id: v.id, matricula: tractora,
            semirremolque_id: semi, remolque_id: v.remolque_id,
            conductor_id: cond, inicio: v.inicio, fin: v.fin,
            kilos: v.kilos, palets: v.palets,
          }),
          signal,
        });
      } catch (e) {
        if ((e as Error).name === "AbortError") return null;
        console.error("[planificacion] error validando asignación:", e);
        return null;
      }
    },
    [],
  );

  // Validar (debounce + cancelación) cuando el puntero está sobre una tractora.
  useEffect(() => {
    validacionGen.current += 1;
    validacionRef.current = null;
    validacionErrorRef.current = false;
    setValidacion(null);
    setValidacionError(false);
    if (!arrastre || !sobreTractora) return;
    const gen = validacionGen.current;
    const ac = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ac;
    const timer = window.setTimeout(() => {
      validar(arrastre, sobreTractora, arrastre.semirremolque_id, arrastre.conductor_id, ac.signal).then((r) => {
        if (gen === validacionGen.current) {
          validacionRef.current = r;
          validacionErrorRef.current = r === null;
          setValidacion(r);
          setValidacionError(r === null);
        }
      });
    }, 150);
    return () => {
      window.clearTimeout(timer);
      ac.abort();
    };
  }, [arrastre, sobreTractora, validar]);

  // Envío real a Trimble (POST /asignar): se llama al terminar los 10 s de deshacer.
  const enviar = useCallback(
    async (p: EnvioPendiente) => {
      pendientesEnvioRef.current.delete(p.v.id);
      setToastUndo((t) => (t && t.viaje.id === p.v.id ? null : t));
      try {
        await api(REST_ASIGNAR(p.v.id), {
          method: "POST",
          body: JSON.stringify({
            matricula: p.tractora, semirremolque_id: p.semi, remolque_id: "",
            conductor: p.conductorNombre, conductor_id: p.cond,
          }),
        });
        // Éxito: invalidar (el refetch ya devuelve el viaje asignado) y luego quitar de enCurso.
        await qc.invalidateQueries({ queryKey: ["planificacion"] });
        setEnCurso((cur) => {
          const n = { ...cur };
          delete n[p.v.id];
          return n;
        });
      } catch (e) {
        console.error("[planificacion] fallo al asignar:", e);
        setEnCurso((cur) => {
          const n = { ...cur };
          delete n[p.v.id];
          return n;
        });
        toast("No se pudo asignar el viaje a Trimble. Se ha revertido.", "error");
      }
    },
    [qc, toast],
  );

  // Al salir de la pantalla con un envío pendiente, se envía de inmediato.
  const enviarRef = useRef(enviar);
  enviarRef.current = enviar;
  useEffect(() => {
    return () => {
      pendientesEnvioRef.current.forEach((p) => {
        window.clearTimeout(p.timer);
        enviarRef.current(p);
      });
      pendientesEnvioRef.current.clear();
    };
  }, []);

  const confirmarAsignacion = useCallback(
    (v: ViajePlanificacion, tractora: string, semi: string, cond: number | null) => {
      // Cancelar un envío pendiente anterior (reasignación).
      const prev = pendientesEnvioRef.current.get(v.id);
      if (prev) {
        window.clearTimeout(prev.timer);
        pendientesEnvioRef.current.delete(v.id);
      }
      const conductorNombre = conductores.data?.find((c) => c.id === cond)?.nombre ?? "";
      // Optimista en estado aparte (no en la caché): el viaje pasa a la tractora al instante.
      setEnCurso((cur) => ({ ...cur, [v.id]: { matricula: tractora, semirremolque_id: semi, conductor_id: cond } }));
      setPopover(null);
      const pendiente: EnvioPendiente = { timer: 0, v, tractora, semi, cond, conductorNombre };
      pendiente.timer = window.setTimeout(() => enviar(pendiente), 10000);
      pendientesEnvioRef.current.set(v.id, pendiente);
      setToastUndo({ viaje: v, tractora });
    },
    [conductores.data, enviar],
  );

  // Deshacer (o soltar en "Pendientes"): cancelar el temporizador y quitar de enCurso,
  // SIN llamadas al servidor (el viaje aún no se ha despachado a Trimble).
  const desasignar = useCallback(
    (v: ViajePlanificacion) => {
      const p = pendientesEnvioRef.current.get(v.id);
      if (p) {
        window.clearTimeout(p.timer);
        pendientesEnvioRef.current.delete(v.id);
      }
      setEnCurso((cur) => {
        const n = { ...cur };
        delete n[v.id];
        return n;
      });
      setToastUndo(null);
    },
    [],
  );

  const decidirSoltar = useCallback(
    (v: ViajePlanificacion, tractora: string, x: number, y: number) => {
      if (validacionErrorRef.current) {
        toast("No se pudo validar la asignación", "error");
        return;
      }
      const r = validacionRef.current;
      if (!r) {
        toast("Aún validando la asignación, espera un instante…", "info");
      } else if (r.bloqueos.length > 0) {
        toast("No se puede asignar: " + r.bloqueos.map((b) => b.mensaje).join(" · "), "error");
      } else if (r.avisos.length > 0) {
        setConfirmacion({ viaje: v, tractora, x, y, avisos: r.avisos });
      } else {
        setPopover({ viaje: v, tractora, x, y });
      }
    },
    [toast],
  );

  // Iniciar arrastre (estado + ref para que los listeners lean siempre el valor vivo).
  const iniciarArrastre = useCallback((v: ViajePlanificacion) => {
    arrastreRef.current = v;
    setArrastre(v);
  }, []);

  // Seguimiento del puntero durante el arrastre. Registrado UNA vez (deps estables);
  // el estado vive en refs para no perder eventos pointermove al re-registrar el listener
  // en cada cambio de tractora (causa de la flakiness del arrastre en CI).
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (tid !== sobreTractoraRef.current) {
        sobreTractoraRef.current = tid;
        setSobreTractora(tid);
      }
    };
    const onUp = (e: PointerEvent) => {
      const v = arrastreRef.current;
      const enPendientes = !!document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-zona-pendientes]");
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (v) {
        if (enPendientes && v.matricula) desasignar(v);
        else if (tid) decidirSoltar(v, tid, e.clientX, e.clientY);
      }
      arrastreRef.current = null;
      sobreTractoraRef.current = null;
      setArrastre(null);
      setSobreTractora(null);
      setValidacion(null);
      setValidacionError(false);
      validacionRef.current = null;
      validacionErrorRef.current = false;
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
    };
  }, [decidirSoltar, desasignar]);

  // Zoom con Ctrl+rueda (cambia px/hora); rueda sola = scroll. Necesita listener no pasivo.
  const ejeRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ejeRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setPxHora((p) => Math.min(120, Math.max(20, Math.round(p * factor))));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const noEnviado = (v: ViajePlanificacion) => !v.estado || v.estado === "sin_asignar";

  const colorValidacion = !sobreTractora
    ? ""
    : validacionError
      ? "ring-2 ring-inset ring-red-500/70 bg-red-500/10"
      : !validacion
        ? "ring-1 ring-inset ring-sky-400/60 bg-sky-400/5"
        : !validacion.ok
          ? "ring-2 ring-inset ring-red-500/70 bg-red-500/10"
          : (validacion.avisos?.length ?? 0) > 0
            ? "ring-2 ring-inset ring-amber-500/70 bg-amber-500/10"
            : "ring-2 ring-inset ring-green-500/70 bg-green-500/10";

  const tooltipValidacion = validacionError
    ? "⛔ No se pudo validar"
    : validacion
      ? [...validacion.bloqueos.map((b) => `⛔ ${b.mensaje}`), ...validacion.avisos.map((a) => `⚠️ ${a.mensaje}`)].join("\n")
      : "";

  const etiquetaRango = useMemo(() => {
    if (vista === "dia") {
      return new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(rango.desde);
    }
    const f = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
    return `${f.format(rango.desde)} – ${f.format(new Date(rango.hasta.getTime() - 86400000))}`;
  }, [vista, rango.desde, rango.hasta]);

  const dias = useMemo(
    () =>
      Array.from({ length: 7 }, (_, i) => {
        const d = new Date(rango.desde.getTime() + i * 86400000);
        return { nombre: DIA_NOMBRES[i], num: d.getDate() };
      }),
    [rango.desde],
  );

  const mover = (dir: -1 | 1) => {
    const paso = vista === "dia" ? 86400000 : 7 * 86400000;
    setAnchor((a) => new Date(a.getTime() + dir * paso));
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-2">
        <h1 className="text-lg font-semibold">Planificación</h1>
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          <button onClick={() => setVista("dia")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "dia" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
            <CalendarDays size={14} /> Día
          </button>
          <button onClick={() => setVista("semana")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "semana" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>
            <CalendarRange size={14} /> Semana
          </button>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => mover(-1)} className="rounded border p-1 hover:bg-muted" aria-label="Anterior"><ChevronLeft size={14} /></button>
          <span className="min-w-[180px] text-center text-xs font-medium">{etiquetaRango}</span>
          <button onClick={() => mover(1)} className="rounded border p-1 hover:bg-muted" aria-label="Siguiente"><ChevronRight size={14} /></button>
        </div>
        <span className="ml-auto text-xs text-muted-foreground">Ctrl + rueda = zoom</span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Columna "Pendientes de asignar" (también zona de soltar para desasignar) */}
        <aside data-zona-pendientes className="flex w-60 shrink-0 flex-col border-r">
          <div className="border-b px-3 py-2 text-xs font-semibold text-muted-foreground">Pendientes de asignar</div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {pendientes.length === 0 && <div className="px-1 text-xs text-muted-foreground">Sin viajes pendientes.</div>}
            {pendientes.map((v) => (
              <div
                key={v.id}
                data-viaje-pendiente={v.id}
                onPointerDown={(e) => { e.preventDefault(); iniciarArrastre(v); }}
                className={`mb-1.5 cursor-grab select-none rounded-md border bg-card p-2 shadow-sm active:cursor-grabbing ${v.id === viajeSeleccionado ? "ring-2 ring-primary" : ""}`}
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
        <div ref={ejeRef} className="min-w-0 flex-1 overflow-auto">
          <div style={{ width: totalWidth + 160, minWidth: "100%" }}>
            {/* Cabecera temporal */}
            <div className="sticky top-0 z-10 flex border-b bg-background">
              <div className="w-40 shrink-0" />
              <div className="relative flex-1" style={{ height: 28 }}>
                {vista === "dia"
                  ? Array.from({ length: 24 }, (_, h) => (
                      <span key={h} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: h * pxHora }}>{String(h).padStart(2, "0")}</span>
                    ))
                  : dias.map((d, i) => (
                      <span key={i} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: i * pxDia }}>{d.nombre} {d.num}</span>
                    ))}
              </div>
            </div>

            {tractoras.map((t) => {
              const viajesDeTractora = asignados.filter((v) => v.matricula === t.matricula);
              return (
                <div key={t.matricula} data-tractora={t.matricula} className={`flex border-b transition-colors ${sobreTractora === t.matricula ? colorValidacion : ""}`} title={sobreTractora === t.matricula ? tooltipValidacion : undefined}>
                  <div className="flex w-40 shrink-0 flex-col justify-center px-3">
                    <div className="truncate text-xs font-semibold">{t.matricula || t.id}</div>
                    <div className="text-[10px] text-muted-foreground">{viajesDeTractora.length} viaje(s)</div>
                  </div>
                  <div className="relative flex-1" style={{ height: ROW_H }}>
                    {viajesDeTractora.map((v) => {
                      const pos = posBloque(v);
                      if (!pos) return null;
                      const enviado = !noEnviado(v);
                      return (
                        <div
                          key={v.id}
                          data-viaje-bloque={v.id}
                          onPointerDown={enviado ? undefined : (e) => { e.preventDefault(); iniciarArrastre(v); }}
                          className={`absolute top-1 overflow-hidden rounded border px-1.5 py-0.5 text-[10px] ${enviado ? "cursor-not-allowed border-slate-500/40 bg-slate-500/15" : "cursor-grab border-sky-600/40 bg-sky-500/20 active:cursor-grabbing"}`}
                          style={{ left: pos.left, width: pos.width, height: ROW_H - 8, pointerEvents: arrastre?.id === v.id ? "none" : undefined }}
                          title={enviado ? "Enviado a Trimble: cancélalo desde Viajes" : `${v.id} · ${v.origen} → ${v.destino} · ${fmtHora(v.inicio)}–${fmtHora(v.fin)}`}
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

      {/* Confirmación de avisos (dentro de la página) */}
      {confirmacion && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setConfirmacion(null)}>
          <div className="w-96 rounded-lg border bg-card p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <AlertTriangle size={16} className="text-amber-500" /> Asignar con avisos
            </div>
            <div className="mb-3 space-y-1 text-xs text-muted-foreground">
              {confirmacion.avisos.map((a, i) => <div key={i}>• {a.mensaje}</div>)}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmacion(null)} className="rounded border px-2 py-1 text-xs hover:bg-muted">Cancelar</button>
              <button
                onClick={() => { setPopover({ viaje: confirmacion.viaje, tractora: confirmacion.tractora, x: confirmacion.x, y: confirmacion.y }); setConfirmacion(null); }}
                className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90"
              >
                Continuar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Popover de asignación (semirremolque + conductor, re-valida y desactiva Asignar si hay bloqueos) */}
      {popover && (
        <PopoverAsignacion
          popover={popover}
          semirremolques={semirremolques.data ?? []}
          conductores={conductores.data ?? []}
          viajes={lista}
          validar={validar}
          onCancelar={() => setPopover(null)}
          onConfirmar={(semi, cond) => confirmarAsignacion(popover.viaje, popover.tractora, semi, cond)}
        />
      )}

      {/* Toast de deshacer (se cierra solo a los 10 s) */}
      {toastUndo && (
        <div className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-3 rounded-lg border bg-card px-4 py-2 text-sm shadow-lg">
          <span>Asignado a {toastUndo.tractora}</span>
          <button onClick={() => desasignar(toastUndo.viaje)} className="flex items-center gap-1 rounded border px-2 py-1 text-xs font-medium hover:bg-muted">
            <Undo2 size={14} /> Deshacer
          </button>
          <button onClick={() => setToastUndo(null)} className="text-muted-foreground hover:text-foreground" aria-label="Cerrar"><X size={14} /></button>
        </div>
      )}

      {/* Toasts genéricos (errores/aviso) */}
      <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className={`max-w-sm rounded-lg border px-3 py-2 text-xs shadow-lg ${t.tipo === "error" ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400" : "border-sky-500/40 bg-sky-500/10"}`}>
            {t.texto}
          </div>
        ))}
      </div>
    </div>
  );
}

function PopoverAsignacion({ popover, semirremolques, conductores, viajes, validar, onCancelar, onConfirmar }: {
  popover: { viaje: ViajePlanificacion; tractora: string; x: number; y: number };
  semirremolques: Semirremolque[];
  conductores: ConductorOpcion[];
  viajes: ViajePlanificacion[];
  validar: (v: ViajePlanificacion, tractora: string, semi: string, cond: number | null, signal?: AbortSignal) => Promise<ValidacionResultado | null>;
  onCancelar: () => void;
  onConfirmar: (semi: string, cond: number | null) => void;
}) {
  // Preseleccionar el último semirremolque/conductor usado con esa tractora.
  const ultimo = useMemo(() => {
    const usados = viajes
      .filter((v) => v.matricula === popover.tractora)
      .sort((a, b) => (b.inicio || "").localeCompare(a.inicio || ""));
    return { semi: usados.find((v) => v.semirremolque_id)?.semirremolque_id ?? "", cond: usados.find((v) => v.conductor_id)?.conductor_id ?? null };
  }, [viajes, popover.tractora]);

  const [semi, setSemi] = useState(ultimo.semi);
  const [cond, setCond] = useState<number | null>(ultimo.cond);
  const [res, setRes] = useState<ValidacionResultado | null>(null);
  const [validando, setValidando] = useState(true);

  // Re-valida con el semi y el conductor elegidos (debounce + cancelación).
  useEffect(() => {
    setValidando(true);
    setRes(null);
    const ac = new AbortController();
    const timer = window.setTimeout(() => {
      validar(popover.viaje, popover.tractora, semi, cond, ac.signal).then((r) => {
        if (ac.signal.aborted) return;
        setRes(r);
        setValidando(false);
      });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      ac.abort();
    };
  }, [semi, cond, popover, validar]);

  // null tras validar = el validar no respondió (error); Asignar queda desactivado.
  const fallo = !validando && res === null;
  const bloqueado = fallo || (!!res && res.bloqueos.length > 0);

  return (
    <div className="fixed z-50 w-80 rounded-lg border bg-card p-3 shadow-xl" style={{ left: Math.min(popover.x, window.innerWidth - 320), top: Math.min(popover.y, window.innerHeight - 320) }}>
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

      {validando && <div className="mb-2 text-[11px] text-muted-foreground">Validando…</div>}
      {fallo && <div className="mb-2 text-[11px] text-red-600 dark:text-red-400">⛔ No se pudo validar</div>}
      {res && !validando && (
        <div className="mb-2 space-y-1 text-[11px]">
          {res.bloqueos.map((b, i) => <div key={i} className="text-red-600 dark:text-red-400">⛔ {b.mensaje}</div>)}
          {res.avisos.map((a, i) => <div key={i} className="text-amber-600 dark:text-amber-400">⚠️ {a.mensaje}</div>)}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button onClick={onCancelar} className="rounded border px-2 py-1 text-xs hover:bg-muted">Cancelar</button>
        <button
          onClick={() => onConfirmar(semi, cond)}
          disabled={bloqueado || validando}
          className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Asignar
        </button>
      </div>
    </div>
  );
}
