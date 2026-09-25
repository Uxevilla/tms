import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type FilterFn,
  type SortingState,
} from "@tanstack/react-table";
import { Copy, Download, Info, LayoutList, Map as MapIcon, MessageCircle, Pencil, Plus, Send, Trash2, X } from "lucide-react";

import { api, ApiError } from "@/api";
import { REST_VIAJES, REST_CONDUCTORES, REST_VEHICULOS_DISPONIBLES } from "@/config";
import type { Viaje, ViajeEstado } from "@/types";
import { StatusBadge } from "./StatusBadge";
import { LiveMap } from "./LiveMap";
import { ChatViaje } from "./ChatViaje";
import { DetalleViaje } from "./DetalleViaje";
import { panelCell } from "./panelCell";

// Sheet de creación/edición: importación estática para que el settle del `keyboard.press`
// no dependa del lazy chunk + Suspense (se colgaba en CI al abrir con el atajo "n").
import { NuevoViajeSheet } from "./NuevoViajeSheet";

// Filtro por rango de fecha sobre fecha_esperada_carga: compara SOLO la fecha (YYYY-MM-DD).
const filtroFecha: FilterFn<Viaje> = (row, columnId, filterValue) => {
  const { desde, hasta } = (filterValue ?? {}) as { desde?: string; hasta?: string };
  if (!desde && !hasta) return true;
  const v = ((row.getValue(columnId) as string) || "").slice(0, 10);
  if (desde && v < desde) return false;
  if (hasta && v > hasta) return false;
  return true;
};

type Vista = "lista" | "mapa" | "dividido";

// Celda editable (input/select) con commit optimista en blur/Enter/cambio.
function CeldaInput({ valor, tipo, onCommit, placeholder, className = "", dataCampo }: {
  valor: string;
  tipo?: "text" | "number" | "date";
  onCommit: (v: string) => void;
  placeholder?: string;
  className?: string;
  dataCampo?: string;
}) {
  const [v, setV] = useState(valor);
  useEffect(() => setV(valor), [valor]);
  const commit = () => { if (v !== valor) onCommit(v); };
  return (
    <input
      data-campo={dataCampo}
      value={v}
      type={tipo ?? "text"}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => { if (e.key === "Enter") (e.currentTarget as HTMLInputElement).blur(); }}
      className={`w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs hover:border-slate-200 focus:border-blue-400 focus:outline-none ${className}`}
    />
  );
}

const ESTADOS_PAGO = ["pendiente", "parcial", "pagado"];

export function ViajesDashboard() {
  const search = useSearch({ from: "/app/viajes" });
  const navigate = useNavigate();
  const qc = useQueryClient();

  const viajes = useQuery({
    queryKey: ["viajes"],
    queryFn: () => api<{ viajes: Viaje[] }>(REST_VIAJES).then((r) => r.viajes),
  });
  const lista = viajes.data ?? [];

  const conductores = useQuery({
    queryKey: ["viajes-conductores"],
    queryFn: () => api<{ conductores: { id: number; nombre: string }[] }>(REST_CONDUCTORES).then((r) => r.conductores),
  });
  const vehiculos = useQuery({
    queryKey: ["viajes-vehiculos"],
    queryFn: () => api<{ vehiculos: { id: string; matricula: string; categoria: string }[] }>(REST_VEHICULOS_DISPONIBLES).then((r) => r.vehiculos),
  });

  const [vista, setVista] = useState<Vista>(() => (window.innerWidth < 768 ? "mapa" : "lista"));
  const [sorting, setSorting] = useState<SortingState>([]);
  const [chatTripId, setChatTripId] = useState<string | null>(null);
  const [tripDetalle, setTripDetalle] = useState<Viaje | null>(null);
  const [sheet, setSheet] = useState<{ abierto: boolean; editTripId: string | null }>({ abierto: false, editTripId: null });
  const [borrarViaje, setBorrarViaje] = useState<Viaje | null>(null);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const debounceRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});

  // Filtros: replace:true (no ensucia el historial) + debounce en los de texto.
  const setFilter = useCallback(
    (key: "estado" | "cliente" | "vehiculo" | "desde" | "hasta", value: string) => {
      const next: Record<string, string> = { ...search };
      if (value) next[key] = value;
      else delete next[key];
      navigate({ to: "/viajes", search: next, replace: true });
    },
    [search, navigate],
  );
  const setFilterDebounced = useCallback(
    (key: "cliente" | "vehiculo", value: string) => {
      if (debounceRef.current[key]) clearTimeout(debounceRef.current[key]);
      debounceRef.current[key] = setTimeout(() => setFilter(key, value), 300);
    },
    [setFilter],
  );

  // Filtros de columna derivados de la URL.
  const columnFilters = useMemo(() => {
    const f: { id: string; value: unknown }[] = [];
    if (search.estado) f.push({ id: "estado", value: search.estado });
    if (search.cliente) f.push({ id: "cliente", value: search.cliente });
    if (search.vehiculo) f.push({ id: "matricula", value: search.vehiculo });
    if (search.desde || search.hasta) f.push({ id: "fecha_esperada_carga", value: { desde: search.desde, hasta: search.hasta } });
    return f;
  }, [search]);

  // Edición optimista con reversión: actualiza ['viajes'] y revierte si el PATCH falla.
  const editarCelda = useCallback(
    async (id: string, campo: keyof Viaje, valor: unknown, body: Record<string, unknown>, label: string) => {
      const key = ["viajes"];
      const prev = qc.getQueryData<Viaje[]>(key);
      if (prev) qc.setQueryData<Viaje[]>(key, prev.map((v) => (v.id === id ? { ...v, [campo]: valor } : v)));
      try {
        await api(`/api/trips/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
      } catch (e) {
        if (prev) qc.setQueryData<Viaje[]>(key, prev);
        setBanner({ tipo: "error", texto: e instanceof ApiError ? `No se pudo guardar «${label}»: ${e.message}` : `No se pudo guardar «${label}».` });
      }
    },
    [qc],
  );

  const columnas = useMemo<ColumnDef<Viaje>[]>(
    () => {
      const tractoras = (vehiculos.data ?? []).filter((x) => x.categoria === "tractora");
      const conds = conductores.data ?? [];
      const inputCls = "w-full rounded border border-transparent bg-transparent px-1 py-0.5 text-xs hover:border-slate-200 focus:border-blue-400 focus:outline-none";
      return [
        { accessorKey: "referencia", header: "Ref.", size: 90 },
        { accessorKey: "id", header: "ID", size: 140, cell: (c) => panelCell<Viaje>("viaje")({ value: c.getValue(), data: c.row.original }) },
        {
          accessorKey: "matricula",
          header: "Matrícula",
          size: 130,
          cell: (c) => {
            const v = c.row.original;
            return (
              <span className="flex items-center gap-1">
                <select
                  data-campo="matricula"
                  value={v.matricula ?? ""}
                  onChange={(e) => {
                    const veh = tractoras.find((x) => x.matricula === e.target.value);
                    if (veh) editarCelda(v.id, "matricula", e.target.value, { matricula: e.target.value }, "matrícula");
                  }}
                  className={inputCls}
                >
                  <option value="">—</option>
                  {tractoras.map((x) => <option key={x.id} value={x.matricula}>{x.matricula}</option>)}
                </select>
                {v.matricula ? panelCell<Viaje>("vehiculo", () => tractoras.find((x) => x.matricula === v.matricula)?.id, { icono: true })({ value: "", data: v }) : null}
              </span>
            );
          },
        },
        {
          accessorKey: "conductor",
          header: "Conductor",
          size: 150,
          cell: (c) => {
            const v = c.row.original;
            const conductorId = conds.find((x) => x.nombre === v.conductor)?.id;
            return (
              <span className="flex items-center gap-1">
                <select data-campo="conductor" value={v.conductor ?? ""} onChange={(e) => editarCelda(v.id, "conductor", e.target.value, { conductor: e.target.value }, "conductor")} className={inputCls}>
                  <option value="">—</option>
                  {conds.map((x) => <option key={x.id} value={x.nombre}>{x.nombre}</option>)}
                </select>
                {conductorId != null ? panelCell<Viaje>("conductor", () => conductorId, { icono: true })({ value: "", data: v }) : null}
              </span>
            );
          },
        },
        { accessorKey: "origen", header: "Origen", size: 140, cell: (c) => <CeldaInput valor={c.row.original.origen ?? ""} onCommit={(v) => editarCelda(c.row.original.id, "origen", v, { origen: v }, "origen")} /> },
        { accessorKey: "destino", header: "Destino", size: 140, cell: (c) => <CeldaInput valor={c.row.original.destino ?? ""} onCommit={(v) => editarCelda(c.row.original.id, "destino", v, { destino: v }, "destino")} /> },
        { accessorKey: "cliente", header: "Cliente", size: 130, cell: (c) => <CeldaInput valor={c.row.original.cliente ?? ""} onCommit={(v) => editarCelda(c.row.original.id, "cliente", v, { cliente: v }, "cliente")} /> },
        { accessorKey: "precio", header: "Precio (€)", size: 95, cell: (c) => <CeldaInput tipo="number" valor={c.row.original.precio != null ? String(c.row.original.precio) : ""} onCommit={(v) => editarCelda(c.row.original.id, "precio", Number(v), { precio: Number(v) }, "precio")} /> },
        { accessorKey: "km_total", header: "Km", size: 75, cell: (c) => (c.getValue() ? `${Number(c.getValue()).toFixed(0)} km` : "—") },
        { accessorKey: "peaje_estimado", header: "Peaje (€)", size: 90, cell: (c) => (c.getValue() ? Number(c.getValue()).toLocaleString("es-ES", { style: "currency", currency: "EUR" }) : "—") },
        { accessorKey: "tiempo_min", header: "Tiempo", size: 85, cell: (c) => (c.getValue() ? `${Math.round(Number(c.getValue()))} min` : "—") },
        { accessorKey: "fecha_esperada_carga", header: "Carga", size: 130, filterFn: filtroFecha, cell: (c) => <CeldaInput dataCampo="fecha_carga" tipo="date" valor={(c.row.original.fecha_esperada_carga ?? "").slice(0, 10)} onCommit={(dia) => editarCelda(c.row.original.id, "fecha_esperada_carga", dia + (c.row.original.fecha_esperada_carga ?? "").slice(10), { fecha_esperada_carga: dia + (c.row.original.fecha_esperada_carga ?? "").slice(10) }, "fecha de carga")} /> },
        { accessorKey: "fecha_esperada_descarga", header: "Descarga", size: 130, cell: (c) => <CeldaInput dataCampo="fecha_descarga" tipo="date" valor={(c.row.original.fecha_esperada_descarga ?? "").slice(0, 10)} onCommit={(dia) => editarCelda(c.row.original.id, "fecha_esperada_descarga", dia + (c.row.original.fecha_esperada_descarga ?? "").slice(10), { fecha_esperada_descarga: dia + (c.row.original.fecha_esperada_descarga ?? "").slice(10) }, "fecha de descarga")} /> },
        {
          accessorKey: "estado_pago",
          header: "Pago",
          size: 110,
          cell: (c) => {
            const v = c.row.original;
            return (
              <select data-campo="estado_pago" value={v.estado_pago ?? "pendiente"} onChange={(e) => editarCelda(v.id, "estado_pago", e.target.value, { estado_pago: e.target.value }, "estado de pago")} className={inputCls}>
                {ESTADOS_PAGO.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            );
          },
        },
        { accessorKey: "estado", header: "Estado", size: 120, cell: (c) => <StatusBadge value={c.getValue() as ViajeEstado} /> },
        { accessorKey: "progreso", header: "Prog.", size: 70, cell: (c) => `${Number(c.getValue() || 0)}%` },
        { accessorKey: "velocidad", header: "Km/h", size: 65, cell: (c) => (c.getValue() == null ? "—" : `${Math.round(Number(c.getValue()))}`) },
        { accessorKey: "eta", header: "ETA", size: 120, cell: (c) => (c.getValue() ? String(c.getValue()).slice(0, 16) : "—") },
        { accessorKey: "itinerario", header: "Itinerario", size: 180, cell: (c) => c.getValue() ? String(c.getValue()) : "—" },
        { accessorKey: "n_documentos", header: "Docs", size: 60, cell: (c) => (c.getValue() ?? "—") },
        {
          id: "acciones",
          header: "",
          size: 230,
          cell: (c) => {
            const v = c.row.original;
            const boton = "inline-flex items-center justify-center rounded-md p-1.5 transition";
            return (
              <div className="flex items-center gap-0.5">
                <button title="Enviar viaje al terminal Trimble" onClick={() => enviarTrip(v)} className={`${boton} bg-emerald-50 text-emerald-600 hover:bg-emerald-100`}><Send size={14} /></button>
                <button title="Editar viaje" onClick={() => setSheet({ abierto: true, editTripId: v.id })} className={`${boton} bg-slate-100 text-slate-600 hover:bg-amber-50 hover:text-amber-600`}><Pencil size={14} /></button>
                <button title="Informes del chofer y archivos" onClick={() => setTripDetalle(v)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-amber-50 hover:text-amber-600`}><Info size={14} /></button>
                <button title="Duplicar viaje (sin asignar)" onClick={() => duplicarViaje(v.id)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-violet-50 hover:text-violet-600`}><Copy size={14} /></button>
                <button title="Mensajería con el terminal" onClick={() => setChatTripId(v.id)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-blue-50 hover:text-blue-600`}><MessageCircle size={14} /></button>
                <button title="Eliminar viaje" onClick={() => setBorrarViaje(v)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-red-50 hover:text-red-600`}><Trash2 size={14} /></button>
              </div>
            );
          },
        },
      ];
    },
    [vehiculos.data, conductores.data, editarCelda],
  );

  const table = useReactTable({
    data: lista,
    columns: columnas,
    state: { sorting, columnFilters },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 50 } },
  });

  function notificar(texto: string, tipo: "ok" | "error" = "ok") {
    setBanner({ tipo, texto });
    window.setTimeout(() => setBanner(null), 4000);
  }

  async function duplicarViaje(id: string) {
    try {
      await api(`/api/trips/${encodeURIComponent(id)}/duplicar`, { method: "POST" });
      notificar("Viaje duplicado (sin asignar).");
      qc.invalidateQueries({ queryKey: ["viajes"] });
    } catch (e) {
      notificar(e instanceof ApiError ? e.message : "Error al duplicar.", "error");
    }
  }

  async function enviarTrip(v: Viaje) {
    try {
      await api(`/api/trips/${encodeURIComponent(v.id)}/enviar`, { method: "POST" });
      notificar("Viaje enviado al terminal Trimble.");
    } catch (e) {
      notificar(e instanceof ApiError ? e.message : "Error al enviar.", "error");
    }
  }

  async function confirmarBorrado() {
    if (!borrarViaje) return;
    const id = borrarViaje.id;
    setBorrarViaje(null);
    try {
      await api(`/api/trips/${encodeURIComponent(id)}`, { method: "DELETE" });
      notificar("Viaje eliminado.");
      qc.invalidateQueries({ queryKey: ["viajes"] });
    } catch (e) {
      notificar(e instanceof ApiError ? e.message : "Error al eliminar.", "error");
    }
  }

  // Atajo "n" (fuera de inputs) → nuevo viaje.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const enInput = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
      if (!enInput && !e.ctrlKey && !e.metaKey && !e.altKey && e.key.toLowerCase() === "n") {
        e.preventDefault();
        setSheet({ abierto: true, editTripId: null });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Exportación CSV de las filas filtradas + ordenadas (sin paginación).
  function exportarCsv() {
    const filas = table.getFilteredRowModel().rows.map((r) => r.original);
    const encabezados = ["ref", "id", "matricula", "conductor", "origen", "destino", "cliente", "precio", "km", "peaje", "tiempo_min", "carga", "descarga", "estado_pago", "estado", "velocidad", "eta", "itinerario", "docs"];
    const lineas = [encabezados.join(",")];
    for (const v of filas) {
      lineas.push(
        [v.referencia, v.id, v.matricula, v.conductor, v.origen, v.destino, v.cliente, v.precio, v.km_total, v.peaje_estimado, v.tiempo_min, v.fecha_esperada_carga, v.fecha_esperada_descarga, v.estado_pago, v.estado, v.velocidad, v.eta, v.itinerario, v.n_documentos]
          .map((x) => (x == null ? "" : `"${String(x).replace(/"/g, '""')}"`))
          .join(","),
      );
    }
    const blob = new Blob(["\uFEFF" + lineas.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "viajes.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  const mostrarLista = vista === "lista" || vista === "dividido";
  const mostrarMapa = vista === "mapa" || vista === "dividido";
  const ancho = vista === "dividido" ? "w-1/2" : "w-full";

  return (
    <div className="flex h-full flex-col gap-2 p-3">
      {/* Barra superior: vista + filtros + acciones */}
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border p-0.5">
          <button onClick={() => setVista("lista")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "lista" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}><LayoutList size={14} /> Lista</button>
          <button onClick={() => setVista("dividido")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "dividido" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}>Dividido</button>
          <button onClick={() => setVista("mapa")} className={`flex items-center gap-1 rounded px-2 py-1 text-xs ${vista === "mapa" ? "bg-primary text-primary-foreground" : "text-muted-foreground"}`}><MapIcon size={14} /> Mapa</button>
        </div>

        <input value={search.cliente ?? ""} onChange={(e) => setFilterDebounced("cliente", e.target.value)} placeholder="Cliente…" className="h-8 w-36 rounded border px-2 text-xs" />
        <input value={search.vehiculo ?? ""} onChange={(e) => setFilterDebounced("vehiculo", e.target.value)} placeholder="Vehículo…" className="h-8 w-32 rounded border px-2 text-xs" />
        <select value={search.estado ?? ""} onChange={(e) => setFilter("estado", e.target.value)} className="h-8 rounded border px-2 text-xs">
          <option value="">Todos los estados</option>
          {["sin_asignar", "enviado", "Llegada_Origen", "Cargando", "En_Transito", "Llegada_Destino", "Descargando", "Entregado", "error"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input type="date" value={search.desde ?? ""} onChange={(e) => setFilter("desde", e.target.value)} className="h-8 rounded border px-2 text-xs" title="Desde (carga)" />
        <input type="date" value={search.hasta ?? ""} onChange={(e) => setFilter("hasta", e.target.value)} className="h-8 rounded border px-2 text-xs" title="Hasta (carga)" />

        <div className="ml-auto flex items-center gap-2">
          <button onClick={exportarCsv} className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"><Download size={15} /> Exportar CSV</button>
          <button onClick={() => setSheet({ abierto: true, editTripId: null })} className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground hover:opacity-90"><Plus size={15} /> Nuevo viaje (N)</button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-2">
        {mostrarLista && (
          <div className={`${ancho} min-h-0 overflow-auto rounded-lg border bg-card`}>
            <table className="w-full border-collapse text-xs">
              <thead className="sticky top-0 z-10 bg-muted">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((h) => (
                      <th key={h.id} className="cursor-pointer select-none whitespace-nowrap border-b px-2 py-1.5 text-left font-semibold text-muted-foreground" onClick={h.column.getToggleSortingHandler()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {table.getRowModel().rows.map((row) => (
                  <tr key={row.id} className="border-b hover:bg-muted/40">
                    {row.getVisibleCells().map((cell) => (
                      <td key={cell.id} className="whitespace-nowrap px-2 py-1.5 text-muted-foreground">
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                ))}
                {table.getRowModel().rows.length === 0 && (
                  <tr><td colSpan={columnas.length} className="px-2 py-6 text-center text-muted-foreground">Sin viajes.</td></tr>
                )}
              </tbody>
            </table>
            {/* Paginación */}
            <div className="flex items-center justify-between border-t px-3 py-2 text-xs text-muted-foreground">
              <span>{table.getFilteredRowModel().rows.length} viajes</span>
              <div className="flex items-center gap-2">
                <button disabled={!table.getCanPreviousPage()} onClick={() => table.previousPage()} className="rounded border px-2 py-1 disabled:opacity-40">←</button>
                <span>{table.getState().pagination.pageIndex + 1} / {Math.max(1, table.getPageCount())}</span>
                <button disabled={!table.getCanNextPage()} onClick={() => table.nextPage()} className="rounded border px-2 py-1 disabled:opacity-40">→</button>
              </div>
            </div>
          </div>
        )}
        {mostrarMapa && <div className={`${ancho} min-h-0 overflow-hidden rounded-lg border bg-card`}><LiveMap focus={null} /></div>}
      </div>

      {banner && (
        <div className={`fixed bottom-4 right-4 z-[3000] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>{banner.texto}</div>
      )}

      {/* Confirmación de borrado dentro de la página */}
      {borrarViaje && (
        <div className="fixed inset-0 z-[2100] flex items-center justify-center bg-black/40" onClick={() => setBorrarViaje(null)}>
          <div className="w-full max-w-sm rounded-lg border bg-card p-4 shadow-xl" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold">Eliminar viaje</h3>
            <p className="mt-2 text-sm text-muted-foreground">¿Eliminar el viaje {borrarViaje.referencia || borrarViaje.id}? No se puede deshacer.</p>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setBorrarViaje(null)} className="rounded border px-3 py-2 text-xs hover:bg-muted">Cancelar</button>
              <button onClick={confirmarBorrado} className="rounded bg-red-600 px-3 py-2 text-xs font-semibold text-white hover:bg-red-700">Eliminar</button>
            </div>
          </div>
        </div>
      )}

      {chatTripId && <ChatViaje tripId={chatTripId} onClose={() => setChatTripId(null)} />}
      {tripDetalle && <DetalleViaje trip={tripDetalle} onClose={() => setTripDetalle(null)} />}
      {sheet.abierto && (
        <NuevoViajeSheet
          editTripId={sheet.editTripId}
            onClose={() => setSheet({ abierto: false, editTripId: null })}
            onGuardado={(id, abrirPlanificacion) => {
              setSheet({ abierto: false, editTripId: null });
              qc.invalidateQueries({ queryKey: ["viajes"] });
              if (id && abrirPlanificacion) navigate({ to: "/planificacion", search: { viaje: id } });
            }}
            />
            )}
    </div>
  );
}
