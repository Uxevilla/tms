import { useCallback, useMemo, useState } from "react";
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
import { Copy, Download, Info, LayoutList, Map as MapIcon, MessageCircle, Plus, Send, Trash2 } from "lucide-react";

import { api, ApiError } from "@/api";
import { REST_VIAJES, REST_CONDUCTORES, REST_VEHICULOS_DISPONIBLES } from "@/config";
import type { Viaje, ViajeEstado } from "@/types";
import { StatusBadge } from "./StatusBadge";
import { LiveMap } from "./LiveMap";
import { ChatViaje } from "./ChatViaje";
import { DetalleViaje } from "./DetalleViaje";
import { NuevoViajeSheet } from "./NuevoViajeSheet";
import { panelCell } from "./panelCell";

// Filtro por rango de fecha sobre fecha_esperada_carga (valor compuesto {desde, hasta}).
const filtroFecha: FilterFn<Viaje> = (row, columnId, filterValue) => {
  const { desde, hasta } = (filterValue ?? {}) as { desde?: string; hasta?: string };
  if (!desde && !hasta) return true;
  const v = (row.getValue(columnId) as string) || "";
  if (desde && v < desde) return false;
  if (hasta && v > hasta) return false;
  return true;
};

type Vista = "lista" | "mapa" | "dividido";

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
  const [sheetAbierto, setSheetAbierto] = useState(false);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);

  const setFilter = (key: "estado" | "cliente" | "vehiculo" | "desde" | "hasta", value: string) => {
    const next = { ...search };
    if (value) next[key] = value;
    else delete next[key];
    navigate({ to: "/viajes", search: next });
  };

  // Filtros de columna derivados de la URL.
  const columnFilters = useMemo(() => {
    const f: { id: string; value: unknown }[] = [];
    if (search.estado) f.push({ id: "estado", value: search.estado });
    if (search.cliente) f.push({ id: "cliente", value: search.cliente });
    if (search.vehiculo) f.push({ id: "matricula", value: search.vehiculo });
    if (search.desde || search.hasta) f.push({ id: "fecha_esperada_carga", value: { desde: search.desde, hasta: search.hasta } });
    return f;
  }, [search]);

  const onCellPATCH = useCallback(
    async (id: string, body: Record<string, unknown>, fieldLabel: string) => {
      try {
        await api(`/api/trips/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(body) });
      } catch (e) {
        setBanner({ tipo: "error", texto: e instanceof ApiError ? `No se pudo guardar «${fieldLabel}»: ${e.message}` : `No se pudo guardar «${fieldLabel}».` });
      }
    },
    [],
  );

  const columnas = useMemo<ColumnDef<Viaje>[]>(
    () => [
      { accessorKey: "referencia", header: "Ref.", size: 90 },
      { accessorKey: "id", header: "ID", size: 150, cell: (c) => panelCell<Viaje>("viaje")({ value: c.getValue(), data: c.row.original }) },
      {
        accessorKey: "matricula",
        header: "Matrícula",
        size: 140,
        cell: (c) => {
          const v = c.row.original;
          const tractoras = (vehiculos.data ?? []).filter((x) => x.categoria === "tractora");
          return (
            <span className="flex items-center gap-1">
              <select
                data-campo="matricula"
                value={v.matricula ?? ""}
                onChange={(e) => {
                  const veh = tractoras.find((x) => x.matricula === e.target.value);
                  if (veh) onCellPATCH(v.id, { terminal: veh.id }, "matrícula");
                }}
                className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs hover:border-slate-200"
              >
                <option value="">—</option>
                {tractoras.map((x) => (
                  <option key={x.id} value={x.matricula}>{x.matricula}</option>
                ))}
              </select>
              {v.matricula ? panelCell<Viaje>("vehiculo", () => tractoras.find((x) => x.matricula === v.matricula)?.id, { icono: true })({ value: "", data: v }) : null}
            </span>
          );
        },
      },
      {
        accessorKey: "conductor",
        header: "Conductor",
        size: 160,
        cell: (c) => {
          const v = c.row.original;
          const conds = conductores.data ?? [];
          const conductorId = conds.find((x) => x.nombre === v.conductor)?.id;
          return (
            <span className="flex items-center gap-1">
              <select
                data-campo="conductor"
                value={v.conductor ?? ""}
                onChange={(e) => onCellPATCH(v.id, { conductor: e.target.value }, "conductor")}
                className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-xs hover:border-slate-200"
              >
                <option value="">—</option>
                {conds.map((x) => (
                  <option key={x.id} value={x.nombre}>{x.nombre}</option>
                ))}
              </select>
              {conductorId != null ? panelCell<Viaje>("conductor", () => conductorId, { icono: true })({ value: "", data: v }) : null}
            </span>
          );
        },
      },
      { accessorKey: "origen", header: "Origen", size: 150 },
      { accessorKey: "destino", header: "Destino", size: 150 },
      { accessorKey: "cliente", header: "Cliente", size: 140 },
      { accessorKey: "precio", header: "Precio (€)", size: 100, cell: (c) => (c.getValue() == null ? "—" : Number(c.getValue()).toLocaleString("es-ES", { style: "currency", currency: "EUR" })) },
      { accessorKey: "km_total", header: "Km", size: 80, cell: (c) => (c.getValue() ? `${Number(c.getValue()).toFixed(0)} km` : "—") },
      { accessorKey: "peaje_estimado", header: "Peaje (€)", size: 95, cell: (c) => (c.getValue() ? Number(c.getValue()).toLocaleString("es-ES", { style: "currency", currency: "EUR" }) : "—") },
      { accessorKey: "tiempo_min", header: "Tiempo", size: 90, cell: (c) => (c.getValue() ? `${Math.round(Number(c.getValue()))} min` : "—") },
      { accessorKey: "fecha_esperada_carga", header: "Carga", size: 140, filterFn: filtroFecha },
      { accessorKey: "fecha_esperada_descarga", header: "Descarga", size: 140 },
      { accessorKey: "estado", header: "Estado", size: 130, cell: (c) => <StatusBadge value={c.getValue() as ViajeEstado} /> },
      { accessorKey: "progreso", header: "Progreso", size: 90, cell: (c) => `${Number(c.getValue() || 0)}%` },
      {
        id: "acciones",
        header: "",
        size: 200,
        cell: (c) => {
          const v = c.row.original;
          const boton = "inline-flex items-center justify-center rounded-md p-1.5 transition";
          return (
            <div className="flex items-center gap-0.5">
              <button title="Enviar viaje al terminal Trimble" onClick={() => enviarTrip(v)} className={`${boton} bg-emerald-50 text-emerald-600 hover:bg-emerald-100`}><Send size={14} /></button>
              <button title="Informes del chofer y archivos" onClick={() => setTripDetalle(v)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-amber-50 hover:text-amber-600`}><Info size={14} /></button>
              <button title="Duplicar viaje (sin asignar)" onClick={() => duplicarViaje(v.id)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-violet-50 hover:text-violet-600`}><Copy size={14} /></button>
              <button title="Mensajería con el terminal" onClick={() => setChatTripId(v.id)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-blue-50 hover:text-blue-600`}><MessageCircle size={14} /></button>
              <button title="Eliminar viaje" onClick={() => eliminarViaje(v)} className={`${boton} bg-slate-100 text-slate-600 hover:bg-red-50 hover:text-red-600`}><Trash2 size={14} /></button>
            </div>
          );
        },
      },
    ],
    [vehiculos.data, conductores.data, onCellPATCH],
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

  async function eliminarViaje(v: Viaje) {
    if (!window.confirm(`¿Eliminar el viaje ${v.referencia || v.id}? No se puede deshacer.`)) return;
    try {
      await api(`/api/trips/${encodeURIComponent(v.id)}`, { method: "DELETE" });
      notificar("Viaje eliminado.");
    } catch (e) {
      notificar(e instanceof ApiError ? e.message : "Error al eliminar.", "error");
    }
  }

  // Exportación CSV de las filas filtradas + ordenadas (sin paginación).
  function exportarCsv() {
    const filas = table.getFilteredRowModel().rows.map((r) => r.original);
    const encabezados = ["ref", "id", "matricula", "conductor", "origen", "destino", "cliente", "precio", "km", "peaje", "tiempo_min", "carga", "descarga", "estado"];
    const lineas = [encabezados.join(",")];
    for (const v of filas) {
      lineas.push(
        [v.referencia, v.id, v.matricula, v.conductor, v.origen, v.destino, v.cliente, v.precio, v.km_total, v.peaje_estimado, v.tiempo_min, v.fecha_esperada_carga, v.fecha_esperada_descarga, v.estado]
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

        <input value={search.cliente ?? ""} onChange={(e) => setFilter("cliente", e.target.value)} placeholder="Cliente…" className="h-8 w-36 rounded border px-2 text-xs" />
        <input value={search.vehiculo ?? ""} onChange={(e) => setFilter("vehiculo", e.target.value)} placeholder="Vehículo…" className="h-8 w-32 rounded border px-2 text-xs" />
        <select value={search.estado ?? ""} onChange={(e) => setFilter("estado", e.target.value)} className="h-8 rounded border px-2 text-xs">
          <option value="">Todos los estados</option>
          {["sin_asignar", "enviado", "Llegada_Origen", "Cargando", "En_Transito", "Llegada_Destino", "Descargando", "Entregado", "error"].map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input type="date" value={search.desde ?? ""} onChange={(e) => setFilter("desde", e.target.value)} className="h-8 rounded border px-2 text-xs" title="Desde (carga)" />
        <input type="date" value={search.hasta ?? ""} onChange={(e) => setFilter("hasta", e.target.value)} className="h-8 rounded border px-2 text-xs" title="Hasta (carga)" />

        <div className="ml-auto flex items-center gap-2">
          <button onClick={exportarCsv} className="flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"><Download size={15} /> Exportar CSV</button>
          <button onClick={() => setSheetAbierto(true)} className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-semibold text-primary-foreground hover:opacity-90"><Plus size={15} /> Nuevo viaje</button>
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
      {chatTripId && <ChatViaje tripId={chatTripId} onClose={() => setChatTripId(null)} />}
      {tripDetalle && <DetalleViaje trip={tripDetalle} onClose={() => setTripDetalle(null)} />}
      {sheetAbierto && <NuevoViajeSheet onClose={() => setSheetAbierto(false)} onCreado={(id, abrirPlanificacion) => { setSheetAbierto(false); qc.invalidateQueries({ queryKey: ["viajes"] }); if (id && abrirPlanificacion) navigate({ to: "/planificacion", search: { viaje: id } }); }} />}
    </div>
  );
}
