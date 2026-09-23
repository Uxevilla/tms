import { useEffect, useState } from "react";
import { Calendar, dateFnsLocalizer, type Event, type SlotInfo } from "react-big-calendar";
import { format, parse, startOfWeek, getDay, addDays, parseISO } from "date-fns";
import { es } from "date-fns/locale";
import "react-big-calendar/lib/css/react-big-calendar.css";
import { X, Save, Trash2, Loader2, Plus } from "lucide-react";
import { REST_AUSENCIAS_PLANNING } from "../config";
import { getToken } from "../auth";

// Localizador date-fns en español.
const locales = { es };
const localizer = dateFnsLocalizer({ format, parse, startOfWeek, getDay, locales });

const COLOR_TIPO: Record<string, string> = {
  vacaciones: "#22c55e",          // bg-green-500
  baja_medica: "#ef4444",         // bg-red-500
  permiso_retribuido: "#3b82f6",  // bg-blue-500
};
const LABEL_TIPO: Record<string, string> = {
  vacaciones: "Vacaciones",
  baja_medica: "Baja Médica",
  permiso_retribuido: "Permiso Retribuido",
};

const MENSAJES = {
  allDay: "Todo el día",
  previous: "Anterior",
  next: "Siguiente",
  today: "Hoy",
  month: "Mes",
  week: "Semana",
  day: "Día",
  agenda: "Agenda",
  date: "Fecha",
  time: "Hora",
  event: "Evento",
  noEventsInRange: "Sin ausencias en este periodo.",
};

interface Empleado {
  id: string;
  nombre: string;
  apellidos?: string;
}

interface AusenciaEvento extends Event {
  id: number;
  tipo: string;
  observaciones?: string;
  nombre_completo?: string;
  fecha_inicio: string;
  fecha_fin: string;
}

export function PlanningCalendario({ empleados }: { empleados: Empleado[] }) {
  const [eventos, setEventos] = useState<AusenciaEvento[]>([]);
  const [drawer, setDrawer] = useState<{ inicio: string; fin: string } | null>(null);
  const [detalle, setDetalle] = useState<AusenciaEvento | null>(null);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [borrando, setBorrando] = useState(false);

  const [pEmpleado, setPEmpleado] = useState("");
  const [pTipo, setPTipo] = useState("vacaciones");
  const [pInicio, setPInicio] = useState("");
  const [pFin, setPFin] = useState("");
  const [pObs, setPObs] = useState("");

  const headers = () => ({ Authorization: `Bearer ${getToken() ?? ""}` });

  async function cargar() {
    try {
      const r = await fetch(REST_AUSENCIAS_PLANNING, { headers: headers() });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      const lista: AusenciaEvento[] = (d.ausencias ?? []).map((a: any) => ({
        id: a.id,
        title: `${`${a.nombre ?? ""} ${a.apellidos ?? ""}`.trim()} - ${LABEL_TIPO[a.tipo] || a.tipo}`,
        start: parseISO(a.fecha_inicio),
        end: addDays(parseISO(a.fecha_fin), 1), // react-big-calendar usa fin exclusivo: +1 día para incluir el último.
        allDay: true,
        tipo: a.tipo,
        observaciones: a.observaciones || "",
        nombre_completo: `${`${a.nombre ?? ""} ${a.apellidos ?? ""}`.trim()}`,
        fecha_inicio: a.fecha_inicio,
        fecha_fin: a.fecha_fin,
      }));
      setEventos(lista);
    } catch (err) {
      console.error("Error cargando planning:", err);
    }
  }

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function eventPropGetter(event: AusenciaEvento) {
    const bg = COLOR_TIPO[event.tipo] || "#64748b";
    return { style: { backgroundColor: bg, color: "#fff", borderRadius: 4, border: "none" } };
  }

  function onSelectSlot(slot: SlotInfo) {
    const inicio = format(slot.start, "yyyy-MM-dd");
    const fin = format(addDays(slot.end, -1), "yyyy-MM-dd"); // fin exclusivo → restar 1 día
    setPEmpleado("");
    setPTipo("vacaciones");
    setPInicio(inicio);
    setPFin(fin);
    setPObs("");
    setDrawer({ inicio, fin });
  }

  function abrirNuevo() {
    const hoy = format(new Date(), "yyyy-MM-dd");
    setPEmpleado("");
    setPTipo("vacaciones");
    setPInicio(hoy);
    setPFin(hoy);
    setPObs("");
    setDrawer({ inicio: hoy, fin: hoy });
  }

  async function guardar() {
    if (!pEmpleado || !pInicio || !pFin) {
      setBanner({ tipo: "error", texto: "Selecciona empleado y fechas." });
      return;
    }
    setGuardando(true);
    try {
      const r = await fetch(REST_AUSENCIAS_PLANNING, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ empleado_id: pEmpleado, fecha_inicio: pInicio, fecha_fin: pFin, tipo: pTipo, observaciones: pObs }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        setBanner({ tipo: "ok", texto: "Ausencia registrada." });
        setDrawer(null);
        cargar();
      } else {
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || `Error ${r.status}` });
      }
    } catch {
      setBanner({ tipo: "error", texto: "Error de red al registrar." });
    } finally {
      setGuardando(false);
    }
  }

  async function eliminar() {
    if (!detalle) return;
    setBorrando(true);
    try {
      const r = await fetch(`${REST_AUSENCIAS_PLANNING}/${detalle.id}`, { method: "DELETE", headers: headers() });
      if (r.ok) {
        setBanner({ tipo: "ok", texto: "Ausencia eliminada." });
        setDetalle(null);
        cargar();
      } else {
        setBanner({ tipo: "error", texto: `No se pudo eliminar (${r.status}).` });
      }
    } catch {
      setBanner({ tipo: "error", texto: "Error de red al eliminar." });
    } finally {
      setBorrando(false);
    }
  }

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  return (
    <div className="flex h-full w-full flex-col">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Planning de ausencias</h2>
        <button onClick={abrirNuevo} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700">
          <Plus size={16} /> Nueva Ausencia
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white p-3">
        <Calendar<AusenciaEvento>
          localizer={localizer}
          events={eventos}
          startAccessor="start"
          endAccessor="end"
          selectable
          onSelectSlot={onSelectSlot}
          onSelectEvent={(e) => setDetalle(e)}
          eventPropGetter={eventPropGetter}
          messages={MENSAJES}
          culture="es"
          defaultView="month"
          views={["month", "week", "day", "agenda"]}
          style={{ height: "100%" }}
        />
      </div>

      {/* Drawer Nueva Ausencia (abierto por clic/arrastre en un hueco del calendario) */}
      {drawer && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardando && setDrawer(null)} />
          <div className="fixed inset-y-0 right-0 z-50 flex w-[360px] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Nueva ausencia</h3>
              <button onClick={() => setDrawer(null)} disabled={guardando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Empleado *</span>
                <select value={pEmpleado} onChange={(e) => setPEmpleado(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {empleados.map((em) => (
                    <option key={em.id} value={em.id}>{em.nombre} {em.apellidos ?? ""}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Tipo *</span>
                <select value={pTipo} onChange={(e) => setPTipo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="vacaciones">Vacaciones</option>
                  <option value="baja_medica">Baja Médica</option>
                  <option value="permiso_retribuido">Permiso Retribuido</option>
                </select>
              </label>
              <div className="flex gap-2">
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Desde *</span>
                  <input type="date" value={pInicio} onChange={(e) => setPInicio(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Hasta *</span>
                  <input type="date" value={pFin} onChange={(e) => setPFin(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Observaciones</span>
                <textarea value={pObs} onChange={(e) => setPObs(e.target.value)} rows={3} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
              <button onClick={guardar} disabled={guardando} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                {guardando ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} {guardando ? "Guardando…" : "Registrar ausencia"}
              </button>
            </div>
          </div>
        </>
      )}

      {/* Modal detalle de ausencia (clic sobre un evento) */}
      {detalle && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm" onClick={() => !borrando && setDetalle(null)} />
          <div className="fixed left-1/2 top-1/2 z-50 w-[360px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl">
            <div className="mb-1 flex items-start justify-between">
              <h3 className="text-sm font-semibold text-slate-800">{detalle.nombre_completo}</h3>
              <button onClick={() => setDetalle(null)} disabled={borrando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={16} />
              </button>
            </div>
            <div className="mb-3 text-xs font-semibold uppercase tracking-wide" style={{ color: COLOR_TIPO[detalle.tipo] || "#64748b" }}>
              {LABEL_TIPO[detalle.tipo] || detalle.tipo}
            </div>
            <div className="space-y-1 text-sm text-slate-600">
              <div><span className="text-slate-400">Inicio:</span> {detalle.fecha_inicio}</div>
              <div><span className="text-slate-400">Fin:</span> {detalle.fecha_fin}</div>
              <div><span className="text-slate-400">Observaciones:</span> {detalle.observaciones || "—"}</div>
            </div>
            <button onClick={eliminar} disabled={borrando} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60">
              {borrando ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />} {borrando ? "Eliminando…" : "Eliminar ausencia"}
            </button>
          </div>
        </>
      )}

      {banner && (
        <div className={`fixed bottom-4 right-4 z-[3000] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}
    </div>
  );
}
