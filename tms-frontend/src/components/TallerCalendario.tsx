import { useEffect, useState } from "react";
import { Calendar, dateFnsLocalizer, type Event, type SlotInfo } from "react-big-calendar";
import { format, parse, startOfWeek, getDay, addDays, parseISO } from "date-fns";
import { es } from "date-fns/locale";
import "react-big-calendar/lib/css/react-big-calendar.css";
import { X, Save, Trash2, Loader2, Plus } from "lucide-react";
import { REST_MANTENIMIENTOS } from "../config";
import { api } from "../api";

const locales = { es };
const localizer = dateFnsLocalizer({ format, parse, startOfWeek, getDay, locales });

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
  noEventsInRange: "Sin citas de taller en este periodo.",
};

// Color por tipo de mantenimiento (bg-yellow-500 / bg-red-500 / bg-blue-500).
function colorTipo(tipo: string): string {
  const s = (tipo || "").toLowerCase();
  if (s.includes("itv")) return "#eab308";                                              // amarillo
  if (s.includes("taller") || s.includes("correctiv") || s.includes("repara")) return "#ef4444"; // rojo
  if (s.includes("preventiv") || s.includes("revis") || s.includes("rutina") || s.includes("mantenim")) return "#3b82f6"; // azul
  return "#64748b"; // gris (otros)
}

interface Vehiculo {
  id: string;
  matricula?: string;
  marca?: string;
  modelo?: string;
  categoria?: string;
}

interface MantenimientoEvento extends Event {
  id: number;
  tipo: string;
  notas?: string;
  matricula?: string;
  vehiculo_id?: string;
  fecha_inicio: string;
  fecha_fin: string;
  hecho: boolean;
}

export function TallerCalendario({ vehiculos }: { vehiculos: Vehiculo[] }) {
  const [eventos, setEventos] = useState<MantenimientoEvento[]>([]);
  const [drawer, setDrawer] = useState<{ inicio: string; fin: string } | null>(null);
  const [detalle, setDetalle] = useState<MantenimientoEvento | null>(null);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [guardando, setGuardando] = useState(false);
  const [borrando, setBorrando] = useState(false);

  const [pVehiculo, setPVehiculo] = useState("");
  const [pTipo, setPTipo] = useState("");
  const [pInicio, setPInicio] = useState("");
  const [pFin, setPFin] = useState("");
  const [pNotas, setPNotas] = useState("");

  async function cargar() {
    try {
      const d = await api<any>(REST_MANTENIMIENTOS);
      const lista: MantenimientoEvento[] = (d.mantenimientos ?? []).map((m: any) => ({
        id: m.id,
        title: `${m.matricula || m.vehiculo_id} - ${m.tipo}`,
        start: parseISO(m.fecha),
        end: addDays(parseISO(m.fecha_fin || m.fecha), 1), // fin exclusivo: +1 día
        allDay: true,
        tipo: m.tipo,
        notas: m.notas || "",
        matricula: m.matricula || m.vehiculo_id,
        vehiculo_id: m.vehiculo_id,
        fecha_inicio: m.fecha,
        fecha_fin: m.fecha_fin || m.fecha,
        hecho: !!m.hecho,
      }));
      setEventos(lista);
    } catch (err) {
      console.error("Error cargando taller:", err);
    }
  }

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function eventPropGetter(event: MantenimientoEvento) {
    return {
      style: {
        backgroundColor: colorTipo(event.tipo),
        color: "#fff",
        borderRadius: 4,
        border: "none",
        opacity: event.hecho ? 0.5 : 1,
      },
    };
  }

  function onSelectSlot(slot: SlotInfo) {
    const inicio = format(slot.start, "yyyy-MM-dd");
    const fin = format(addDays(slot.end, -1), "yyyy-MM-dd");
    setPVehiculo("");
    setPTipo("");
    setPInicio(inicio);
    setPFin(fin);
    setPNotas("");
    setDrawer({ inicio, fin });
  }

  function abrirNuevo() {
    const hoy = format(new Date(), "yyyy-MM-dd");
    setPVehiculo("");
    setPTipo("");
    setPInicio(hoy);
    setPFin(hoy);
    setPNotas("");
    setDrawer({ inicio: hoy, fin: hoy });
  }

  async function guardar() {
    if (!pVehiculo || !pTipo || !pInicio) {
      setBanner({ tipo: "error", texto: "Indica vehículo, tipo y fecha." });
      return;
    }
    setGuardando(true);
    try {
      const d = await api<any>(REST_MANTENIMIENTOS, {
        method: "POST",
        body: JSON.stringify({
          vehiculo_id: pVehiculo,
          tipo: pTipo,
          fecha: pInicio,
          fecha_fin: pFin,
          km: 0,
          coste: 0,
          notas: pNotas,
          hecho: false,
        }),
      });
      if (d.ok) {
        setBanner({ tipo: "ok", texto: "Cita de taller registrada." });
        setDrawer(null);
        cargar();
      } else {
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || "Error" });
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
      await api(`${REST_MANTENIMIENTOS}/${detalle.id}`, { method: "DELETE" });
      setBanner({ tipo: "ok", texto: "Cita eliminada." });
      setDetalle(null);
      cargar();
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
        <h2 className="text-sm font-semibold text-slate-700">Planning de Taller</h2>
        <button onClick={abrirNuevo} className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700">
          <Plus size={16} /> Nueva Cita
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white p-3">
        <Calendar<MantenimientoEvento>
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

      {drawer && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardando && setDrawer(null)} />
          <div className="fixed inset-y-0 right-0 z-50 flex w-[360px] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Nueva cita de taller</h3>
              <button onClick={() => setDrawer(null)} disabled={guardando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Vehículo *</span>
                <select value={pVehiculo} onChange={(e) => setPVehiculo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {vehiculos.map((v) => (
                    <option key={v.id} value={v.id}>[{v.categoria ? v.categoria.charAt(0).toUpperCase() + v.categoria.slice(1) : ""}] {v.matricula} — {v.marca} {v.modelo}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Tipo *</span>
                <select value={pTipo} onChange={(e) => setPTipo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  <option value="ITV">ITV</option>
                  <option value="Taller">Taller correctivo</option>
                  <option value="Preventivo">Revisión rutinaria</option>
                  <option value="Otros">Otros</option>
                </select>
              </label>
              <div className="flex gap-2">
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Desde *</span>
                  <input type="date" value={pInicio} onChange={(e) => setPInicio(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Hasta</span>
                  <input type="date" value={pFin} onChange={(e) => setPFin(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Notas</span>
                <textarea value={pNotas} onChange={(e) => setPNotas(e.target.value)} rows={3} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
              <button onClick={guardar} disabled={guardando} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                {guardando ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />} {guardando ? "Guardando…" : "Registrar cita"}
              </button>
            </div>
          </div>
        </>
      )}

      {detalle && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm" onClick={() => !borrando && setDetalle(null)} />
          <div className="fixed left-1/2 top-1/2 z-50 w-[360px] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-slate-200 bg-white p-4 shadow-2xl">
            <div className="mb-1 flex items-start justify-between">
              <h3 className="text-sm font-semibold text-slate-800">{detalle.matricula}</h3>
              <button onClick={() => setDetalle(null)} disabled={borrando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={16} />
              </button>
            </div>
            <div className="mb-3 text-xs font-semibold uppercase tracking-wide" style={{ color: colorTipo(detalle.tipo) }}>
              {detalle.tipo}
            </div>
            <div className="space-y-1 text-sm text-slate-600">
              <div><span className="text-slate-400">Inicio:</span> {detalle.fecha_inicio}</div>
              <div><span className="text-slate-400">Fin:</span> {detalle.fecha_fin}</div>
              <div><span className="text-slate-400">Notas:</span> {detalle.notas || "—"}</div>
            </div>
            <button onClick={eliminar} disabled={borrando} className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-red-600 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-60">
              {borrando ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />} {borrando ? "Eliminando…" : "Eliminar cita"}
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
