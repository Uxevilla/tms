import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  CalendarDays, CalendarRange, ChevronLeft, ChevronRight, X, AlertTriangle,
  Truck, Loader2, Send, Undo2, UserRound, Container, DoorOpen, Info, CornerUpLeft,
} from "lucide-react";

import { api, ApiError } from "@/api";
import {
  REST_CONDUCTORES, REST_ENVIAR, REST_PLANIFICACION, REST_PLANIFICACION_MOVER,
  REST_PLANIFICACION_VALIDAR, REST_QUITAR_TERMINAL, REST_VEHICULOS_DISPONIBLES,
} from "@/config";
import {
  ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuSeparator,
  ContextMenuSub, ContextMenuSubContent, ContextMenuSubTrigger, ContextMenuTrigger,
} from "@/components/ui/context-menu";
import type { ValidacionMotivo, VehiculoPlanificacion, ViajePlanificacion } from "@/types";

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
interface Asignacion {
  terminal: string;
  semirremolque_id: string;
  conductor_id: number | null;
  inicio: string;
  fin: string;
}
interface Confirmacion {
  viaje: ViajePlanificacion;
  destino: Asignacion;
  enCurso: boolean;
}

const ROW_H = 56;
const DIA_NOMBRES = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];
const ESTADOS_EN_CURSO = ["Llegada_Origen", "Cargando", "En_Transito", "Llegada_Destino", "Descargando"];

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
function fmtLocalISO(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${dd}T${hh}:${mm}`;
}

function enTrimble(v: ViajePlanificacion): boolean {
  return v.estado === "enviado" || ESTADOS_EN_CURSO.includes(v.estado);
}
function enCursoEstado(v: ViajePlanificacion): boolean {
  return ESTADOS_EN_CURSO.includes(v.estado);
}
// Duración del viaje: fin−inicio si la tiene; si no, tiempo_min de la ruta; si no, 2 h.
function duracionDe(v: ViajePlanificacion): number {
  const d = aTs(v.fin) - aTs(v.inicio);
  if (!isNaN(d) && d > 0) return d;
  if (v.tiempo_min > 0) return v.tiempo_min * 60000;
  return 2 * 3600000;
}

export function PlanificacionDashboard() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [vista, setVista] = useState<Vista>("dia");
  const [pxHora, setPxHora] = useState(48);
  const [anchor, setAnchor] = useState(() => {
    const h = new Date();
    return new Date(h.getFullYear(), h.getMonth(), h.getDate());
  });

  const rango = useMemo(() => {
    if (vista === "dia") return { desde: anchor, hasta: new Date(anchor.getTime() + 86400000) };
    const dow = (anchor.getDay() + 6) % 7;
    const desde = new Date(anchor.getTime() - dow * 86400000);
    return { desde, hasta: new Date(desde.getTime() + 7 * 86400000) };
  }, [anchor, vista]);
  const desdeISO = fmtDiaLocal(rango.desde);
  const hastaISO = fmtDiaLocal(rango.hasta);

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

  // ---- Estado de movimientos en curso: APARTE de la caché (un refetch/WS no lo pisa) ----
  const [enCurso, setEnCurso] = useState<Record<string, Asignacion>>({});
  const [avisosViaje, setAvisosViaje] = useState<Record<string, ValidacionMotivo[]>>({});
  const [erroresViaje, setErroresViaje] = useState<Record<string, string>>({});
  const [guardando, setGuardando] = useState<Set<string>>(new Set());
  const [historial, setHistorial] = useState<{ id: string; destino: Asignacion }[]>([]);

  const lista = useMemo(() => {
    if (Object.keys(enCurso).length === 0) return listaBase;
    return listaBase.map((v) => {
      const pend = enCurso[v.id];
      return pend ? { ...v, terminal: pend.terminal, semirremolque_id: pend.semirremolque_id, conductor_id: pend.conductor_id, inicio: pend.inicio, fin: pend.fin } : v;
    });
  }, [listaBase, enCurso]);
  const pendientes = useMemo(() => lista.filter((v) => !v.terminal), [lista]);
  const asignados = useMemo(() => lista.filter((v) => !!v.terminal), [lista]);

  const semirremolques = useQuery({
    queryKey: ["planificacion-semirremolques"],
    queryFn: () => api<{ vehiculos: Semirremolque[] }>(`${REST_VEHICULOS_DISPONIBLES}?categoria=semirremolque`).then((r) => r.vehiculos),
    refetchInterval: 60000,
  });
  const conductores = useQuery({
    queryKey: ["planificacion-conductores"],
    queryFn: () => api<{ conductores: ConductorOpcion[] }>(REST_CONDUCTORES).then((r) => r.conductores),
    refetchInterval: 60000,
  });

  const desdeTs = rango.desde.getTime();
  const hastaTs = rango.hasta.getTime();
  const span = hastaTs - desdeTs;
  const pxDia = pxHora * 3;
  const totalWidth = vista === "dia" ? 24 * pxHora : 7 * pxDia;
  const posBloque = (v: ViajePlanificacion): { left: number; width: number } | null => {
    const i = aTs(v.inicio);
    const f = aTs(v.fin);
    if (isNaN(i) || isNaN(f)) return null;
    if (f <= desdeTs || i >= hastaTs) return null;
    const s = Math.max(i, desdeTs);
    const e = Math.min(f, hastaTs);
    return { left: ((s - desdeTs) / span) * totalWidth, width: Math.max(2, ((e - s) / span) * totalWidth) };
  };

  // ---- Toasts ----
  const toastIdRef = useRef(0);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toast = useCallback((texto: string, tipo: "error" | "info" = "info") => {
    const id = ++toastIdRef.current;
    setToasts((t) => [...t, { id, texto, tipo }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4500);
  }, []);

  // ---- Preselección: último semirremolque/conductor usado con una tractora ----
  const ultimoUsado = useCallback(
    (tractora: string): { semi: string; cond: number | null } => {
      const usados = lista
        .filter((v) => v.terminal === tractora)
        .sort((a, b) => (b.inicio || "").localeCompare(a.inicio || ""));
      return {
        semi: usados.find((v) => v.semirremolque_id)?.semirremolque_id ?? "",
        cond: usados.find((v) => v.conductor_id)?.conductor_id ?? null,
      };
    },
    [lista],
  );

  // ---- x → tiempo (saltos de 15 min). Día: hora; semana: día + hora. ----
  const ejeRef = useRef<HTMLDivElement>(null);
  const xATiempo = useCallback((clientX: number): string => {
    const eje = ejeRef.current;
    if (!eje) return "";
    const rect = eje.getBoundingClientRect();
    const x = clientX - rect.left + eje.scrollLeft - 160; // - columna de matrícula
    if (x < 0) return "";
    if (vista === "dia") {
      const ms = desdeTs + (x / pxHora) * 3600000;
      return fmtLocalISO(new Date(Math.round(ms / 900000) * 900000));
    }
    const diaIdx = Math.min(6, Math.floor(x / pxDia));
    const horaFrac = (x - diaIdx * pxDia) / pxDia; // 0..1 dentro del día
    const ms = desdeTs + diaIdx * 86400000 + Math.round(horaFrac * 96) * 900000;
    return fmtLocalISO(new Date(ms));
  }, [desdeTs, pxHora, pxDia, vista]);

  // ---- mover (optimista + enCurso + avisos + historial + reversión) ----
  const moverViaje = useCallback(
    async (v: ViajePlanificacion, destino: Asignacion, force = false) => {
      const anterior = { terminal: v.terminal, semirremolque_id: v.semirremolque_id, conductor_id: v.conductor_id, inicio: v.inicio, fin: v.fin };
      setEnCurso((cur) => ({ ...cur, [v.id]: destino }));
      setGuardando((prev) => new Set(prev).add(v.id));
      try {
        const r = await api<{ ok: boolean; avisos: ValidacionMotivo[] }>(REST_PLANIFICACION_MOVER, {
          method: "POST",
          body: JSON.stringify({ trip_id: v.id, ...destino, force }),
        });
        if (r.avisos && r.avisos.length > 0) setAvisosViaje((cur) => ({ ...cur, [v.id]: r.avisos }));
        else setAvisosViaje((cur) => { const n = { ...cur }; delete n[v.id]; return n; });
        // Historial para Ctrl+Z solo si NO tocó Trimble (movimiento local).
        if (!enTrimble(v)) setHistorial((h) => [...h, { id: v.id, destino: anterior }]);
      } catch (e) {
        setEnCurso((cur) => { const n = { ...cur }; delete n[v.id]; return n; });
        const err = e as ApiError;
        const inner = (err.detail as { bloqueos?: ValidacionMotivo[]; error?: string; detail?: { error?: string } }) ?? {};
        const motivo = inner.bloqueos?.map((b) => b.mensaje).join(" · ") || inner.detail?.error || inner.error || err.message;
        toast(`No se pudo mover: ${motivo}`, "error");
      } finally {
        setGuardando((prev) => { const n = new Set(prev); n.delete(v.id); return n; });
      }
    },
    [],
  );

  // ---- Enviar al terminal (Safe-Dispatching con "Enviar igualmente") ----
  const [forzarEnvio, setForzarEnvio] = useState<{ viaje: ViajePlanificacion; motivo: string } | null>(null);
  const enviarViaje = useCallback(
    async (v: ViajePlanificacion, force = false) => {
      setGuardando((prev) => new Set(prev).add(v.id));
      try {
        await api(REST_ENVIAR(v.id) + (force ? "?force=true" : ""), { method: "POST" });
        await qc.invalidateQueries({ queryKey: ["planificacion"] });
        setErroresViaje((cur) => { const n = { ...cur }; delete n[v.id]; return n; });
        toast("Viaje enviado al terminal.");
      } catch (e) {
        const err = e as ApiError;
        const inner = (err.detail as { error?: string; detail?: { forzar?: boolean; error?: string } }) ?? {};
        const motivo = inner.detail?.error || inner.error || err.message;
        if (inner.detail?.forzar) setForzarEnvio({ viaje: v, motivo });
        else { setErroresViaje((cur) => ({ ...cur, [v.id]: motivo })); toast(motivo, "error"); }
      } finally {
        setGuardando((prev) => { const n = new Set(prev); n.delete(v.id); return n; });
      }
    },
    [qc, toast],
  );

  // ---- Quitar del terminal (conserva la tractora, sin enviar) ----
  const quitarTerminal = useCallback(
    async (v: ViajePlanificacion) => {
      setGuardando((prev) => new Set(prev).add(v.id));
      try {
        await api(REST_QUITAR_TERMINAL(v.id), { method: "POST" });
        await qc.invalidateQueries({ queryKey: ["planificacion"] });
        toast("Viaje quitado del terminal (queda planificado).");
      } catch (e) {
        const err = e as ApiError;
        toast(err.message, "error");
      } finally {
        setGuardando((prev) => { const n = new Set(prev); n.delete(v.id); return n; });
      }
    },
    [qc, toast],
  );

  // ---- Confirmación de aviso (enviado/en curso) ----
  const [confirmacion, setConfirmacion] = useState<Confirmacion | null>(null);
  const confirmarRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirmacion) confirmarRef.current?.focus();
  }, [confirmacion]);
  useEffect(() => {
    if (!confirmacion) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter") { e.preventDefault(); confirmarRef.current?.click(); }
      if (e.key === "Escape") { e.preventDefault(); setConfirmacion(null); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [confirmacion]);

  const aceptarConfirmacion = () => {
    if (!confirmacion) return;
    const { viaje, destino, enCurso: ec } = confirmacion;
    setConfirmacion(null);
    moverViaje(viaje, destino, ec);
  };

  // ---- Arrastre visual (copia semitransparente + sombra + auto-scroll + Esc) ----
  const [arrastre, setArrastre] = useState<ViajePlanificacion | null>(null);
  const [sobreTractora, setSobreTractora] = useState<string | null>(null);
  const [horaArrastre, setHoraArrastre] = useState<string>("");
  const [validacion, setValidacion] = useState<{ ok: boolean; bloqueos: ValidacionMotivo[]; avisos: ValidacionMotivo[] } | null>(null);
  const [validacionError, setValidacionError] = useState(false);
  const arrastreRef = useRef<ViajePlanificacion | null>(null);
  const sobreTractoraRef = useRef<string | null>(null);
  const horaArrastreRef = useRef<string>("");
  const validacionRef = useRef<{ ok: boolean; bloqueos: ValidacionMotivo[]; avisos: ValidacionMotivo[] } | null>(null);
  const validacionErrorRef = useRef(false);
  const validacionGen = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const copiaRef = useRef<HTMLDivElement>(null);
  const sombraRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);

  // ---- Redimensionar (estirar el borde derecho = ajustar "fin", F) ----
  const [redimensionando, setRedimensionando] = useState<{ viaje: ViajePlanificacion; fin: string } | null>(null);
  const redimensionandoRef = useRef<{ viaje: ViajePlanificacion; fin: string } | null>(null);

  const validar = useCallback(
    async (v: ViajePlanificacion, tractora: string, semi: string, cond: number | null, inicio: string, fin: string, signal?: AbortSignal) => {
      try {
        return await api<{ ok: boolean; bloqueos: ValidacionMotivo[]; avisos: ValidacionMotivo[] }>(REST_PLANIFICACION_VALIDAR, {
          method: "POST",
          body: JSON.stringify({
            trip_id: v.id, terminal: tractora, semirremolque_id: semi, remolque_id: v.remolque_id,
            conductor_id: cond, inicio, fin, kilos: v.kilos, palets: v.palets,
          }),
          signal,
        });
      } catch (e) {
        if ((e as Error).name === "AbortError") return null;
        console.error("[planificacion] error validando:", e);
        return null;
      }
    },
    [],
  );

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
    const semi = ultimoUsado(sobreTractora).semi;
    const cond = ultimoUsado(sobreTractora).cond;
    const inicio = horaArrastre || arrastre.inicio;
    const fin = fmtLocalISO(new Date(aTs(inicio) + duracionDe(arrastre)));
    const timer = window.setTimeout(() => {
      validar(arrastre, sobreTractora, semi, cond, inicio, fin, ac.signal).then((r) => {
        if (ac.signal.aborted) return;
        if (gen === validacionGen.current) {
          validacionRef.current = r;
          validacionErrorRef.current = r === null;
          setValidacion(r);
          setValidacionError(r === null);
        }
      });
    }, 150);
    return () => { window.clearTimeout(timer); ac.abort(); };
  }, [arrastre, sobreTractora, horaArrastre, validar, ultimoUsado]);

  const iniciarArrastre = useCallback((v: ViajePlanificacion) => {
    arrastreRef.current = v;
    horaArrastreRef.current = "";
    setArrastre(v);
    setHoraArrastre("");
  }, []);

  const soltar = useCallback(
    (v: ViajePlanificacion, tractora: string | null, clientX: number) => {
      // B/C: la fila decide la tractora; la posición horizontal decide la hora (siempre).
      const inicio = xATiempo(clientX) || v.inicio;
      const fin = fmtLocalISO(new Date(aTs(inicio) + duracionDe(v)));
      const cambia = tractora !== v.terminal;
      // Reasignación → preseleccionar el último semi/conductor; mismo tractora → conservar.
      const semi = cambia ? (tractora ? ultimoUsado(tractora).semi : "") : v.semirremolque_id;
      const cond = cambia ? (tractora ? ultimoUsado(tractora).cond : null) : v.conductor_id;
      const destino: Asignacion = { terminal: tractora ?? "", semirremolque_id: semi, conductor_id: cond, inicio, fin };

      // Bloqueos → no soltar + toast (el servidor re-valida, pero el color ya lo anticipa).
      if (validacionErrorRef.current) { toast("No se pudo validar la asignación", "error"); return; }
      if (validacionRef.current && validacionRef.current.bloqueos.length > 0) {
        toast("No se puede asignar: " + validacionRef.current.bloqueos.map((b) => b.mensaje).join(" · "), "error");
        return;
      }
      if (cambia && enTrimble(v)) {
        setConfirmacion({ viaje: v, destino, enCurso: enCursoEstado(v) });
      } else {
        moverViaje(v, destino);
      }
    },
    [xATiempo, ultimoUsado, moverViaje, toast],
  );

  useEffect(() => {
    const limpiarArrastre = () => {
      arrastreRef.current = null;
      sobreTractoraRef.current = null;
      horaArrastreRef.current = "";
      validacionRef.current = null;
      validacionErrorRef.current = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      setArrastre(null);
      setSobreTractora(null);
      setHoraArrastre("");
      setValidacion(null);
      setValidacionError(false);
    };

    const onMove = (e: PointerEvent) => {
      if (!arrastreRef.current) return;
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (tid !== sobreTractoraRef.current) {
        sobreTractoraRef.current = tid;
        setSobreTractora(tid);
      }
      // Hora (snap 15 min) — re-valida solo cuando cambia el snap.
      const hora = xATiempo(e.clientX);
      if (hora && hora !== horaArrastreRef.current) {
        horaArrastreRef.current = hora;
        setHoraArrastre(hora);
      }
      // D: auto-scroll al acercarse a los bordes (más rápido cuanto más cerca).
      const eje = ejeRef.current;
      if (eje) {
        const r = eje.getBoundingClientRect();
        const margen = 40;
        let dx = 0; let dy = 0;
        if (e.clientX < r.left + margen) dx = -Math.max(4, (r.left + margen - e.clientX) / 2);
        else if (e.clientX > r.right - margen) dx = Math.max(4, (e.clientX - (r.right - margen)) / 2);
        if (e.clientY < r.top + margen) dy = -Math.max(4, (r.top + margen - e.clientY) / 2);
        else if (e.clientY > r.bottom - margen) dy = Math.max(4, (e.clientY - (r.bottom - margen)) / 2);
        if (dx || dy) { eje.scrollLeft += dx; eje.scrollTop += dy; }
      }
      // A/H: copia + sombra con transform vía rAF (sin re-render del tablero).
      cancelAnimationFrame(rafRef.current);
      rafRef.current = requestAnimationFrame(() => {
        if (copiaRef.current) copiaRef.current.style.transform = `translate(${e.clientX}px, ${e.clientY}px) translate(-50%, -50%)`;
        if (sombraRef.current) {
          const eje2 = ejeRef.current;
          if (!eje2 || !el || !hora) { sombraRef.current.style.display = "none"; return; }
          const ejeRect = eje2.getBoundingClientRect();
          const inicioMs = aTs(hora);
          const dur = duracionDe(arrastreRef.current!);
          const leftInAxis = ((inicioMs - desdeTs) / span) * totalWidth;
          const width = Math.max(2, (dur / span) * totalWidth);
          const rowRect = el.getBoundingClientRect();
          sombraRef.current.style.display = "block";
          sombraRef.current.style.transform = `translate(${ejeRect.left + 160 - eje2.scrollLeft + leftInAxis}px, ${rowRect.top + 4}px)`;
          sombraRef.current.style.width = `${width}px`;
          sombraRef.current.style.height = `${ROW_H - 8}px`;
        }
      });
    };

    const onUp = (e: PointerEvent) => {
      const v = arrastreRef.current;
      const enPendientes = !!document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-zona-pendientes]");
      const el = document.elementFromPoint(e.clientX, e.clientY)?.closest?.("[data-tractora]") as HTMLElement | null;
      const tid = el?.getAttribute("data-tractora") ?? null;
      if (v) {
        if (enPendientes) soltar(v, null, e.clientX);
        else if (tid) soltar(v, tid, e.clientX);
        // G: soltar fuera del tablero y de Pendientes = cancelar (sin mover).
      }
      limpiarArrastre();
    };

    const onKey = (e: KeyboardEvent) => {
      // G: Esc cancela el arrastre.
      if (e.key === "Escape" && arrastreRef.current) { e.preventDefault(); limpiarArrastre(); }
    };

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerup", onUp);
      window.removeEventListener("keydown", onKey);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [soltar, xATiempo, desdeTs, span, totalWidth]);

  // ---- Redimensionar (F): estirar el borde derecho ajusta "fin" en saltos de 15 min ----
  useEffect(() => {
    if (!redimensionando) return;
    const onMove = (e: PointerEvent) => {
      const fin = xATiempo(e.clientX);
      if (fin) redimensionandoRef.current = { viaje: redimensionando.viaje, fin };
    };
    const onUp = () => {
      const r = redimensionandoRef.current;
      if (r && r.fin !== r.viaje.fin) {
        moverViaje(r.viaje, { terminal: r.viaje.terminal, semirremolque_id: r.viaje.semirremolque_id, conductor_id: r.viaje.conductor_id, inicio: r.viaje.inicio, fin: r.fin });
      }
      redimensionandoRef.current = null;
      setRedimensionando(null);
    };
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    return () => { document.removeEventListener("pointermove", onMove); document.removeEventListener("pointerup", onUp); };
  }, [redimensionando, xATiempo, moverViaje]);

  // ---- Ctrl+Z: deshacer el último movimiento que no tocó Trimble ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!e.ctrlKey || e.key.toLowerCase() !== "z") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
      e.preventDefault();
      if (historial.length === 0) return;
      const ult = historial[historial.length - 1];
      const viajeActual = lista.find((x) => x.id === ult.id);
      if (viajeActual) moverViaje(viajeActual, ult.destino);
      setHistorial((h) => h.slice(0, -1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moverViaje, lista, historial]);

  // ---- Zoom Ctrl+rueda ----
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

  const sombraCls = !sobreTractora
    ? "border-sky-500/50 bg-sky-500/10"
    : validacionError
      ? "border-red-500/70 bg-red-500/20"
      : !validacion
        ? "border-sky-400/60 bg-sky-400/15"
        : !validacion.ok
          ? "border-red-500/70 bg-red-500/20"
          : (validacion.avisos?.length ?? 0) > 0
            ? "border-amber-500/70 bg-amber-500/20"
            : "border-green-500/70 bg-green-500/20";

  const etiquetaRango = useMemo(() => {
    if (vista === "dia") return new Intl.DateTimeFormat("es-ES", { weekday: "long", day: "numeric", month: "long", year: "numeric" }).format(rango.desde);
    const f = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short" });
    return `${f.format(rango.desde)} – ${f.format(new Date(rango.hasta.getTime() - 86400000))}`;
  }, [vista, rango.desde, rango.hasta]);

  const dias = useMemo(
    () => Array.from({ length: 7 }, (_, i) => {
      const d = new Date(rango.desde.getTime() + i * 86400000);
      return { nombre: DIA_NOMBRES[i], num: d.getDate() };
    }),
    [rango.desde],
  );

  const mover = (dir: -1 | 1) => {
    const paso = vista === "dia" ? 86400000 : 7 * 86400000;
    setAnchor((a) => new Date(a.getTime() + dir * paso));
  };

  // Enviar todos los viajes planificados (sin enviar) de una tractora.
  const enviarTodos = async (t: VehiculoPlanificacion) => {
    const planificados = asignados.filter((v) => v.terminal === t.id && !enTrimble(v));
    if (planificados.length === 0) { toast("No hay viajes planificados sin enviar en esta tractora."); return; }
    for (const v of planificados) await enviarViaje(v);
  };

  const bloqueCls = (v: ViajePlanificacion): string => {
    const guardandoV = guardando.has(v.id);
    const error = erroresViaje[v.id];
    if (guardandoV) return "border-slate-400/50 bg-slate-400/20";
    if (error) return "border-red-500/60 bg-red-500/20";
    if (enTrimble(v)) return "border-solid border-slate-500/60 bg-slate-500/25";
    return "border-dashed border-sky-600/40 bg-sky-500/15";
  };

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b px-4 py-2">
        <h1 className="text-lg font-semibold">Planificación</h1>
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          <button onClick={() => setVista("dia")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "dia" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}><CalendarDays size={14} /> Día</button>
          <button onClick={() => setVista("semana")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "semana" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}><CalendarRange size={14} /> Semana</button>
        </div>
        <div className="flex items-center gap-1">
          <button onClick={() => mover(-1)} className="rounded border p-1 hover:bg-muted" aria-label="Anterior"><ChevronLeft size={14} /></button>
          <span className="min-w-[180px] text-center text-xs font-medium">{etiquetaRango}</span>
          <button onClick={() => mover(1)} className="rounded border p-1 hover:bg-muted" aria-label="Siguiente"><ChevronRight size={14} /></button>
        </div>
        <span className="ml-auto text-xs text-muted-foreground">Arrastra para planificar · clic derecho = menú · Ctrl+Z deshace</span>
      </div>

      <div className="flex min-h-0 flex-1">
        <aside data-zona-pendientes className="flex w-60 shrink-0 flex-col border-r">
          <div className="border-b px-3 py-2 text-xs font-semibold text-muted-foreground">Pendientes de asignar</div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {pendientes.length === 0 && <div className="px-1 text-xs text-muted-foreground">Sin viajes pendientes.</div>}
            {pendientes.map((v) => (
              <ContextMenu key={v.id}>
                <ContextMenuTrigger asChild>
                  <div
                    data-viaje-pendiente={v.id}
                    onPointerDown={(e) => { if (e.button !== 0) return; e.preventDefault(); iniciarArrastre(v); }}
                    className={`mb-1.5 cursor-grab select-none rounded-md border bg-card p-2 shadow-sm active:cursor-grabbing ${bloqueCls(v)}`}
                    style={{ pointerEvents: arrastre?.id === v.id ? "none" : undefined, opacity: arrastre?.id === v.id ? 0.4 : undefined }}
                  >
                    <div className="flex items-center gap-1 truncate text-xs font-medium">
                      {guardando.has(v.id) && <Loader2 size={11} className="animate-spin" />}
                      {enTrimble(v) && <Truck size={11} className="text-muted-foreground" />}
                      {(avisosViaje[v.id]?.length ?? 0) > 0 && <AlertTriangle size={11} className="text-amber-500" />}
                      {v.id}
                    </div>
                    <div className="truncate text-[11px] text-muted-foreground">{v.origen} → {v.destino}</div>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">{v.cliente || "—"} · {v.kilos > 0 ? `${v.kilos} kg` : "—"}</div>
                  </div>
                </ContextMenuTrigger>
                <MenuViaje v={v} enviarViaje={enviarViaje} quitarTerminal={quitarTerminal} moverViaje={moverViaje} ultimoUsado={ultimoUsado} abrirFicha={(id) => navigate({ search: { panel: `viaje:${id}` } as never })} semirremolques={semirremolques.data ?? []} conductores={conductores.data ?? []} />
              </ContextMenu>
            ))}
          </div>
        </aside>

        <div ref={ejeRef} data-eje className="min-w-0 flex-1 overflow-auto">
          <div style={{ width: totalWidth + 160, minWidth: "100%" }}>
            <div className="sticky top-0 z-10 flex border-b bg-background">
              <div className="w-40 shrink-0" />
              <div className="relative flex-1" style={{ height: 28 }}>
                {vista === "dia"
                  ? Array.from({ length: 24 }, (_, h) => <span key={h} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: h * pxHora }}>{String(h).padStart(2, "0")}</span>)
                  : dias.map((d, i) => <span key={i} className="absolute top-0 text-[10px] text-muted-foreground" style={{ left: i * pxDia }}>{d.nombre} {d.num}</span>)}
              </div>
            </div>

            {tractoras.map((t) => {
              const viajesDeTractora = asignados.filter((v) => v.terminal === t.id);
              const planificadosSinEnviar = viajesDeTractora.filter((v) => !enTrimble(v));
              return (
                <div key={t.id} data-tractora={t.id} className={`flex border-b transition-colors ${sobreTractora === t.id ? colorValidacion : ""}`}>
                  <ContextMenu>
                    <ContextMenuTrigger asChild>
                      <div className="flex w-40 shrink-0 flex-col justify-center px-3">
                        <div className="truncate text-xs font-semibold">{t.matricula || t.id}</div>
                        <div className="text-[10px] text-muted-foreground">{viajesDeTractora.length} viaje(s)</div>
                      </div>
                    </ContextMenuTrigger>
                    <ContextMenuContent>
                      <ContextMenuItem onSelect={() => enviarTodos(t)} disabled={planificadosSinEnviar.length === 0}>
                        <Send size={13} /> Enviar todos los del día ({planificadosSinEnviar.length})
                      </ContextMenuItem>
                    </ContextMenuContent>
                  </ContextMenu>
                  <div className="relative flex-1" style={{ height: ROW_H }}>
                    {viajesDeTractora.map((v) => {
                      const pos = posBloque(v);
                      if (!pos) return null;
                      const enviado = enTrimble(v);
                      return (
                        <ContextMenu key={v.id}>
                          <ContextMenuTrigger asChild>
                            <div
                              data-viaje-bloque={v.id}
                              onPointerDown={(e) => { if (e.button !== 0) return; e.preventDefault(); iniciarArrastre(v); }}
                              className={`absolute top-1 overflow-hidden rounded border px-1.5 py-0.5 text-[10px] cursor-grab active:cursor-grabbing ${bloqueCls(v)}`}
                              style={{ left: pos.left, width: pos.width, height: ROW_H - 8, pointerEvents: arrastre?.id === v.id ? "none" : undefined, opacity: arrastre?.id === v.id ? 0.4 : undefined }}
                              title={`${v.id} · ${v.origen} → ${v.destino} · ${fmtHora(v.inicio)}–${fmtHora(v.fin)}${enviado ? " · Enviado" : ""}${avisosViaje[v.id]?.map((a) => " · ⚠️ " + a.mensaje).join("") ?? ""}`}
                            >
                              <div className="flex items-center gap-1 truncate font-medium">
                                {guardando.has(v.id) && <Loader2 size={10} className="animate-spin" />}
                                {enviado && <Truck size={10} className="text-muted-foreground" />}
                                {(avisosViaje[v.id]?.length ?? 0) > 0 && <AlertTriangle size={10} className="text-amber-500" />}
                                {v.id}
                              </div>
                              <div className="truncate text-muted-foreground">{fmtHora(v.inicio)}–{fmtHora(v.fin)}</div>
                              {/* F: estirar el borde derecho = ajustar "fin" */}
                              <div
                                data-redimensionar={v.id}
                                className="absolute inset-y-0 right-0 w-2 cursor-ew-resize hover:bg-sky-400/40"
                                onPointerDown={(e) => { if (e.button !== 0) return; e.preventDefault(); e.stopPropagation(); redimensionandoRef.current = { viaje: v, fin: v.fin }; setRedimensionando({ viaje: v, fin: v.fin }); }}
                              />
                            </div>
                          </ContextMenuTrigger>
                          <MenuViaje v={v} enviarViaje={enviarViaje} quitarTerminal={quitarTerminal} moverViaje={moverViaje} ultimoUsado={ultimoUsado} abrirFicha={(id) => navigate({ search: { panel: `viaje:${id}` } as never })} semirremolques={semirremolques.data ?? []} conductores={conductores.data ?? []} />
                        </ContextMenu>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* Confirmación de aviso (enviado/en curso) */}
      {confirmacion && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-96 rounded-lg border bg-card p-4 shadow-xl">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <AlertTriangle size={16} className="text-amber-500" />
              {confirmacion.enCurso ? "El conductor ya ha empezado" : "El viaje está en el terminal"}
            </div>
            <div className="mb-3 text-xs text-muted-foreground">
              El viaje será eliminado de la pantalla del terminal.
              {confirmacion.enCurso && <div className="mt-1 font-medium text-amber-600 dark:text-amber-400">El conductor ya ha empezado este viaje ({confirmacion.viaje.estado}).</div>}
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmacion(null)} className="rounded border px-3 py-1 text-xs hover:bg-muted">Cancelar</button>
              <button ref={confirmarRef} onClick={aceptarConfirmacion} className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90">Aceptar</button>
            </div>
          </div>
        </div>
      )}

      {/* "Enviar igualmente" (Safe-Dispatching) */}
      {forzarEnvio && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-96 rounded-lg border bg-card p-4 shadow-xl">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold"><AlertTriangle size={16} className="text-amber-500" /> Conducción legal</div>
            <div className="mb-3 text-xs text-muted-foreground">{forzarEnvio.motivo}</div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setForzarEnvio(null)} className="rounded border px-3 py-1 text-xs hover:bg-muted">Cancelar</button>
              <button onClick={() => { const v = forzarEnvio.viaje; setForzarEnvio(null); enviarViaje(v, true); }} className="rounded bg-primary px-3 py-1 text-xs text-primary-foreground hover:opacity-90">Enviar igualmente</button>
            </div>
          </div>
        </div>
      )}

      {/* Copia semitransparente que sigue al cursor + sombra de destino (A) */}
      {arrastre && (
        <div
          ref={copiaRef}
          className="pointer-events-none fixed left-0 top-0 z-[70] w-52 rounded-md border border-sky-500/60 bg-card px-2 py-1 text-[10px] opacity-80 shadow-lg"
          style={{ transform: "translate(-9999px, -9999px)" }}
        >
          <div className="flex items-center gap-1 truncate font-medium">
            {enTrimble(arrastre) && <Truck size={10} className="text-muted-foreground" />}
            {arrastre.id}
          </div>
          <div className="truncate text-muted-foreground">{arrastre.origen} → {arrastre.destino}</div>
        </div>
      )}
      {arrastre && (
        <div
          ref={sombraRef}
          className={`pointer-events-none fixed left-0 top-0 z-[69] rounded border-2 border-dashed ${sombraCls}`}
          style={{ display: "none", transform: "translate(-9999px, -9999px)" }}
        />
      )}

      {/* Toasts */}
      <div className="fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} className={`max-w-sm rounded-lg border px-3 py-2 text-xs shadow-lg ${t.tipo === "error" ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400" : "border-sky-500/40 bg-sky-500/10"}`}>{t.texto}</div>
        ))}
      </div>
    </div>
  );
}

function MenuViaje({ v, enviarViaje, quitarTerminal, moverViaje, ultimoUsado, abrirFicha, semirremolques, conductores }: {
  v: ViajePlanificacion;
  enviarViaje: (v: ViajePlanificacion, force?: boolean) => void;
  quitarTerminal: (v: ViajePlanificacion) => void;
  moverViaje: (v: ViajePlanificacion, destino: Asignacion, force?: boolean) => void;
  ultimoUsado: (t: string) => { semi: string; cond: number | null };
  abrirFicha: (id: string) => void;
  semirremolques: Semirremolque[];
  conductores: ConductorOpcion[];
}) {
  const enviado = enTrimble(v);
  const cambiaSemi = (semi: string) => moverViaje(v, { terminal: v.terminal, semirremolque_id: semi, conductor_id: v.conductor_id, inicio: v.inicio, fin: v.fin });
  const cambiaConductor = (cond: number | null) => moverViaje(v, { terminal: v.terminal, semirremolque_id: v.semirremolque_id, conductor_id: cond, inicio: v.inicio, fin: v.fin });

  return (
    <ContextMenuContent>
      {!enviado
        ? <ContextMenuItem onSelect={() => enviarViaje(v)}><Send size={13} /> Enviar viaje al terminal</ContextMenuItem>
        : <ContextMenuItem onSelect={() => enviarViaje(v)}><Send size={13} /> Reenviar al terminal</ContextMenuItem>}
      {enviado && <ContextMenuItem onSelect={() => quitarTerminal(v)}><DoorOpen size={13} /> Quitar del terminal</ContextMenuItem>}
      <ContextMenuSeparator />
      <ContextMenuSub>
        <ContextMenuSubTrigger><Container size={13} /> Cambiar semirremolque…</ContextMenuSubTrigger>
        <ContextMenuSubContent>
          {semirremolques.map((s) => (
            <ContextMenuItem key={s.id} disabled={s.disponible === false} onSelect={() => cambiaSemi(s.id)}>
              {s.matricula}{s.disponible === false ? ` (⛔ ${s.motivo_bloqueo})` : ""}
            </ContextMenuItem>
          ))}
        </ContextMenuSubContent>
      </ContextMenuSub>
      <ContextMenuSub>
        <ContextMenuSubTrigger><UserRound size={13} /> Cambiar conductor…</ContextMenuSubTrigger>
        <ContextMenuSubContent>
          {conductores.map((c) => (
            <ContextMenuItem key={c.id} disabled={c.disponible === false} onSelect={() => cambiaConductor(c.id)}>
              {c.nombre}{c.disponible === false ? ` (⛔ ${c.motivo_ausencia})` : ""}
            </ContextMenuItem>
          ))}
        </ContextMenuSubContent>
      </ContextMenuSub>
      <ContextMenuItem onSelect={() => moverViaje(v, { terminal: "", semirremolque_id: "", conductor_id: null, inicio: v.inicio, fin: v.fin })}>
        <CornerUpLeft size={13} /> Devolver a Pendientes
      </ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem onSelect={() => abrirFicha(v.id)}><Info size={13} /> Abrir ficha</ContextMenuItem>
    </ContextMenuContent>
  );
}
