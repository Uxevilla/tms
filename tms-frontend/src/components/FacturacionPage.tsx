import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { Download, FileText, CheckCircle2, Send, Loader2 } from "lucide-react";

import { api, ApiError } from "../api";
import { getToken } from "../auth";
import {
  REST_FACTURABLES,
  REST_FACTURAS,
  REST_FACTURA_AGRUPADA,
  REST_FACTURA_GENERAR,
  REST_FACTURA_COBRAR,
  REST_FACTURA_PDF,
  REST_FACTURA_ENVIAR,
} from "../config";
import { Button } from "./ui/button";

// ------------------------------------------------------------------ tipos
interface Facturable {
  id: string;
  referencia: string;
  cliente: string;
  precio: number;
  iva: number;
  origen: string;
  destino: string;
  creado: string;
  estado: string;
}

interface Factura {
  id: number;
  numero: string;
  fecha: string;
  cliente_nombre: string;
  base: number;
  iva: number;
  cuota_iva: number;
  total: number;
  estado: string;
}

// ------------------------------------------------------------------ helpers
const fmtEUR = (n: number | null | undefined) =>
  new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" }).format(Number(n ?? 0));

const fmtFecha = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
};

const ESTADO_FACTURA: Record<string, { label: string; clase: string }> = {
  emitida: { label: "Emitida", clase: "bg-blue-100 text-blue-700" },
  cobrada: { label: "Cobrada", clase: "bg-emerald-100 text-emerald-700" },
  pagada: { label: "Pagada", clase: "bg-emerald-100 text-emerald-700" },
  vencida: { label: "Vencida", clase: "bg-red-100 text-red-700" },
  anulada: { label: "Anulada", clase: "bg-slate-100 text-slate-600" },
};

function EstadoBadge({ estado }: { estado: string }) {
  const e = ESTADO_FACTURA[estado] ?? { label: estado || "—", clase: "bg-slate-100 text-slate-600" };
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${e.clase}`}>{e.label}</span>;
}

// ------------------------------------------------------------------ página
export function FacturacionPage() {
  const queryClient = useQueryClient();
  const [pestana, setPestana] = useState<"facturables" | "facturas">("facturables");
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);

  const { data: facturables = [], isLoading } = useQuery({
    queryKey: ["facturables"],
    queryFn: async () => (await api<{ viajes: Facturable[] }>(REST_FACTURABLES)).viajes ?? [],
  });

  const { data: facturas = [] } = useQuery({
    queryKey: ["facturas"],
    queryFn: async () => (await api<{ facturas: Factura[] }>(REST_FACTURAS)).facturas ?? [],
  });

  const facturar = useMutation({
    mutationFn: async (tripIds: string[]) => {
      const d = await api<{ ok: boolean; factura: string; total: number }>(REST_FACTURA_AGRUPADA, {
        method: "POST",
        body: JSON.stringify({ trip_ids: tripIds }),
      });
      return d;
    },
    onSuccess: (d) => {
      setBanner({ tipo: "ok", texto: `Factura ${d.factura} creada (${fmtEUR(d.total)}).` });
      setSel(new Set());
      queryClient.invalidateQueries({ queryKey: ["facturables"] });
      queryClient.invalidateQueries({ queryKey: ["facturas"] });
    },
    onError: (e) =>
      setBanner({ tipo: "error", texto: e instanceof ApiError ? String(e.detail ?? e.message) : "Error al facturar." }),
  });

  const cobrar = useMutation({
    mutationFn: (id: number) => api(REST_FACTURA_COBRAR(id), { method: "POST" }),
    onSuccess: () => {
      setBanner({ tipo: "ok", texto: "Factura marcada como cobrada." });
      queryClient.invalidateQueries({ queryKey: ["facturas"] });
    },
    onError: (e) =>
      setBanner({ tipo: "error", texto: e instanceof ApiError ? String(e.detail ?? e.message) : "Error al cobrar." }),
  });

  const enviar = useMutation({
    mutationFn: (id: number) => api(REST_FACTURA_ENVIAR(id), { method: "POST" }),
    onSuccess: () => setBanner({ tipo: "ok", texto: "Factura enviada." }),
    onError: (e) =>
      setBanner({ tipo: "error", texto: e instanceof ApiError ? String(e.detail ?? e.message) : "Error al enviar." }),
  });

  async function descargarPdf(id: number) {
    try {
      const r = await fetch(REST_FACTURA_PDF(id), { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
      if (!r.ok) {
        setBanner({ tipo: "error", texto: "Error al generar el PDF." });
        return;
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `factura-${id}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setBanner({ tipo: "error", texto: "Error al descargar el PDF." });
    }
  }

  function toggle(id: string) {
    setSel((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  const facturablesCols = useMemo<ColumnDef<Facturable>[]>(() => [
    {
      id: "sel",
      header: () => (
        <input
          type="checkbox"
          checked={facturables.length > 0 && sel.size === facturables.length}
          onChange={(e) => setSel(e.target.checked ? new Set(facturables.map((f) => f.id)) : new Set())}
          className="h-3.5 w-3.5"
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={sel.has(row.original.id)}
          onChange={() => toggle(row.original.id)}
          className="h-3.5 w-3.5"
        />
      ),
    },
    { accessorKey: "referencia", header: "Viaje", cell: ({ row }) => <span className="font-medium text-slate-700">{row.original.referencia || row.original.id}</span> },
    { accessorKey: "cliente", header: "Cliente", cell: ({ getValue }) => <span className="text-slate-600">{String(getValue() ?? "—")}</span> },
    {
      id: "ruta",
      header: "Ruta",
      cell: ({ row }) => (
        <span className="text-xs text-slate-500">
          {row.original.origen || "—"} → {row.original.destino || "—"}
        </span>
      ),
    },
    { accessorKey: "precio", header: "Precio", cell: ({ getValue }) => <span className="tabular-nums text-slate-800">{fmtEUR(getValue() as number)}</span> },
    {
      id: "accion",
      header: "",
      cell: ({ row }) => (
        <Button
          size="sm"
          variant="outline"
          onClick={() => facturar.mutate([row.original.id])}
          disabled={facturar.isPending}
        >
          <FileText size={14} className="mr-1" />
          Facturar
        </Button>
      ),
    },
  ], [sel, facturables, facturar]);

  const facturasCols = useMemo<ColumnDef<Factura>[]>(() => [
    { accessorKey: "numero", header: "Número", cell: ({ getValue }) => <span className="font-medium text-slate-700">{String(getValue() ?? "")}</span> },
    { accessorKey: "fecha", header: "Fecha", cell: ({ getValue }) => <span className="text-slate-600">{fmtFecha(getValue() as string)}</span> },
    { accessorKey: "cliente_nombre", header: "Cliente", cell: ({ getValue }) => <span className="text-slate-600">{String(getValue() ?? "—")}</span> },
    { accessorKey: "total", header: "Total", cell: ({ getValue }) => <span className="tabular-nums font-semibold text-slate-800">{fmtEUR(getValue() as number)}</span> },
    { accessorKey: "estado", header: "Estado", cell: ({ getValue }) => <EstadoBadge estado={String(getValue() ?? "")} /> },
    {
      id: "acciones",
      header: "",
      cell: ({ row }) => {
        const f = row.original;
        return (
          <div className="flex items-center justify-end gap-1">
            <Button size="sm" variant="ghost" onClick={() => descargarPdf(f.id)} title="PDF">
              <Download size={14} />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => enviar.mutate(f.id)} disabled={enviar.isPending} title="Enviar">
              <Send size={14} />
            </Button>
            {f.estado !== "cobrada" && f.estado !== "pagada" && (
              <Button size="sm" variant="ghost" onClick={() => cobrar.mutate(f.id)} disabled={cobrar.isPending} title="Marcar cobrada">
                <CheckCircle2 size={14} />
              </Button>
            )}
          </div>
        );
      },
    },
  ], [enviar, cobrar]);

  const tablaFacturables = useReactTable({
    data: facturables,
    columns: facturablesCols,
    getCoreRowModel: getCoreRowModel(),
  });
  const tablaFacturas = useReactTable({
    data: facturas,
    columns: facturasCols,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      {/* Cabecera + tabs */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center rounded-md border border-input p-0.5">
          <button
            type="button"
            onClick={() => setPestana("facturables")}
            className={`rounded px-2.5 py-1 text-xs font-medium transition ${pestana === "facturables" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Facturables ({facturables.length})
          </button>
          <button
            type="button"
            onClick={() => setPestana("facturas")}
            className={`rounded px-2.5 py-1 text-xs font-medium transition ${pestana === "facturas" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Facturas ({facturas.length})
          </button>
        </div>
        {pestana === "facturables" && sel.size > 0 && (
          <Button onClick={() => facturar.mutate([...sel])} disabled={facturar.isPending}>
            {facturar.isPending ? <Loader2 size={16} className="mr-1.5 animate-spin" /> : <FileText size={16} className="mr-1.5" />}
            Facturar {sel.size} viaje{sel.size === 1 ? "" : "s"}
          </Button>
        )}
      </div>

      {/* Contenido */}
      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-200 bg-white">
        {pestana === "facturables" ? (
          <div className="h-full overflow-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 bg-slate-50">
                {tablaFacturables.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((h) => (
                      <th key={h.id} className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {isLoading ? (
                  <tr><td colSpan={7} className="px-3 py-8 text-center text-sm text-slate-400">Cargando…</td></tr>
                ) : facturables.length === 0 ? (
                  <tr><td colSpan={7} className="px-3 py-8 text-center text-sm text-slate-400">No hay viajes pendientes de facturar.</td></tr>
                ) : (
                  tablaFacturables.getRowModel().rows.map((row) => (
                    <tr key={row.id} className="border-b border-slate-100 hover:bg-slate-50">
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-2">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="h-full overflow-auto">
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 bg-slate-50">
                {tablaFacturas.getHeaderGroups().map((hg) => (
                  <tr key={hg.id}>
                    {hg.headers.map((h) => (
                      <th key={h.id} className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </th>
                    ))}
                  </tr>
                ))}
              </thead>
              <tbody>
                {facturas.length === 0 ? (
                  <tr><td colSpan={6} className="px-3 py-8 text-center text-sm text-slate-400">Sin facturas todavía.</td></tr>
                ) : (
                  tablaFacturas.getRowModel().rows.map((row) => (
                    <tr key={row.id} className="border-b border-slate-100 hover:bg-slate-50">
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-3 py-2">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {banner && (
        <div
          className={`fixed right-4 top-20 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${
            banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"
          }`}
        >
          {banner.texto}
        </div>
      )}
    </div>
  );
}
