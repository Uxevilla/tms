import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from "@tanstack/react-table";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Plus, Pencil } from "lucide-react";

import { api } from "../api";
import { REST_MANTENIMIENTOS, REST_VEHICULOS_DISPONIBLES } from "../config";
import { CaducidadRenderer } from "./CaducidadRenderer";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";

// ------------------------------------------------------------------ tipos
interface Mantenimiento {
  id: number;
  vehiculo_id: string;
  tipo: string;
  fecha: string;
  fecha_fin: string | null;
  km: number;
  coste: number;
  notas: string | null;
  hecho: boolean;
  matricula: string;
  categoria: string;
}

interface Vehiculo {
  id: string;
  matricula: string;
  categoria: string;
}

const TIPO_MANTENIMIENTO = [
  "aceite",
  "frenos",
  "neumaticos",
  "revision",
  "itv",
  "otro",
] as const;

// ------------------------------------------------------------------ formulario
const mantenimientoSchema = z.object({
  tipo: z.enum(TIPO_MANTENIMIENTO),
  fecha: z.string().min(1, "Requerido"),
  fecha_fin: z.string().optional().nullable(),
  km: z.number().int().min(0),
  coste: z.number().min(0),
  notas: z.string().optional(),
});

type MantenimientoForm = z.infer<typeof mantenimientoSchema>;

const valoresPorDefecto: MantenimientoForm = {
  tipo: "revision",
  fecha: "",
  fecha_fin: "",
  km: 0,
  coste: 0,
  notas: "",
};

const fmtEuro = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const fmtKm = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });

// ------------------------------------------------------------------ página
export function TallerPage() {
  const queryClient = useQueryClient();
  const [abierto, setAbierto] = useState(false);
  const [editando, setEditando] = useState<Mantenimiento | null>(null);
  const [filtroVehiculo, setFiltroVehiculo] = useState("");

  const { data: mantenimientos = [], isLoading } = useQuery({
    queryKey: ["mantenimientos", filtroVehiculo],
    queryFn: async () =>
      (
        await api<{ mantenimientos?: Mantenimiento[] }>(
          `${REST_MANTENIMIENTOS}${filtroVehiculo ? `?vehiculo_id=${encodeURIComponent(filtroVehiculo)}` : ""}`
        )
      ).mantenimientos ?? [],
  });

  const { data: vehiculos = [] } = useQuery({
    queryKey: ["vehiculos-taller"],
    queryFn: async () =>
      (await api<{ vehiculos?: Vehiculo[] }>(REST_VEHICULOS_DISPONIBLES)).vehiculos ?? [],
  });

  const form = useForm<MantenimientoForm>({
    resolver: zodResolver(mantenimientoSchema),
    defaultValues: valoresPorDefecto,
  });

  const guardar = useMutation({
    mutationFn: async ({ editar, valores }: { editar: Mantenimiento | null; valores: MantenimientoForm }) => {
      if (editar) {
        return api(`${REST_MANTENIMIENTOS}/${editar.id}/campos`, {
          method: "PATCH",
          body: JSON.stringify(valores),
        });
      }
      return api(REST_MANTENIMIENTOS, {
        method: "POST",
        body: JSON.stringify({ ...valores, vehiculo_id: filtroVehiculo || "" }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mantenimientos"] });
      setAbierto(false);
      setEditando(null);
      form.reset(valoresPorDefecto);
    },
  });

  const toggleHecho = useMutation({
    mutationFn: async (id: number) => {
      return api(`${REST_MANTENIMIENTOS}/${id}`, { method: "PATCH" });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mantenimientos"] });
    },
  });

  const columns = useMemo<ColumnDef<Mantenimiento>[]>(
    () => [
      {
        accessorKey: "matricula",
        header: "Vehículo",
        cell: ({ row }) => (
          <span className="font-medium text-slate-900">{row.original.matricula}</span>
        ),
      },
      {
        accessorKey: "tipo",
        header: "Tipo",
        cell: ({ row }) => <span className="capitalize">{row.original.tipo}</span>,
      },
      {
        accessorKey: "fecha",
        header: "Fecha",
        cell: ({ row }) => (
          <CaducidadRenderer value={row.original.fecha ?? null} />
        ),
      },
      {
        accessorKey: "km",
        header: "Km",
        cell: ({ row }) => {
          const km = row.original.km;
          return km == null || km === 0 ? (
            <span className="text-slate-300">—</span>
          ) : (
            <span>{fmtKm.format(km)}</span>
          );
        },
      },
      {
        accessorKey: "coste",
        header: "Coste (€)",
        cell: ({ row }) => (
          <span className="tabular-nums">
            {fmtEuro.format(row.original.coste ?? 0)}
          </span>
        ),
      },
      {
        accessorKey: "hecho",
        header: "Estado",
        cell: ({ row }) => (
          <input
            type="checkbox"
            checked={row.original.hecho}
            onChange={(e) => {
              e.stopPropagation();
              toggleHecho.mutate(row.original.id);
            }}
            className="h-4 w-4 rounded border-slate-300 text-primary focus:ring-primary"
          />
        ),
      },
      {
        accessorKey: "notas",
        header: "Notas",
        cell: ({ row }) => (
          <span className="text-slate-600 max-w-xs truncate block">
            {row.original.notas ?? "—"}
          </span>
        ),
      },
    ],
    [form]
  );

  const table = useReactTable({
    data: mantenimientos,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  function abrirAlta() {
    setEditando(null);
    form.reset(valoresPorDefecto);
    setAbierto(true);
  }

  const onSubmit = (valores: MantenimientoForm) => {
    guardar.mutate({ editar: editando, valores });
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Taller</h1>
          <p className="text-sm text-slate-500">
            {mantenimientos.length} mantenimientos · click en fila para editar
          </p>
        </div>
        <div className="flex flex-col sm:flex-row gap-3 w-full sm:w-auto">
          <div className="flex items-center gap-2">
            <Label htmlFor="filtro-vehiculo" className="text-sm text-slate-600">
              Vehículo
            </Label>
            <select
              id="filtro-vehiculo"
              value={filtroVehiculo}
              onChange={(e) => setFiltroVehiculo(e.target.value)}
              className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
            >
              <option value="">Todos los vehículos</option>
              {vehiculos.map((v) => (
                <option key={v.matricula} value={v.matricula}>
                  {v.matricula} ({v.categoria})
                </option>
              ))}
            </select>
          </div>
          <Button onClick={abrirAlta}>
            <Plus size={16} className="mr-1.5" />
            Nuevo mantenimiento
          </Button>
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-slate-200">
        <table className="w-full border-collapse text-sm">
          <thead className="bg-slate-50">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th key={h.id} className="px-3 py-2 text-left text-xs font-semibold text-slate-500">
                    {flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={table.getAllColumns().length} className="px-3 py-10 text-center text-slate-400">
                  Cargando mantenimientos…
                </td>
              </tr>
            ) : table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={table.getAllColumns().length} className="px-3 py-10 text-center text-slate-400">
                  Sin mantenimientos. Crea el primero con «Nuevo mantenimiento».
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => {
                    setEditando(row.original);
                    form.reset({
                      ...valoresPorDefecto,
                      tipo: (row.original.tipo as MantenimientoForm["tipo"]) ?? "revision",
                      fecha: row.original.fecha,
                      fecha_fin: row.original.fecha_fin ?? "",
                      km: row.original.km,
                      coste: row.original.coste,
                      notas: row.original.notas ?? "",
                    });
                    setAbierto(true);
                  }}
                  className="cursor-pointer border-t border-slate-100 transition hover:bg-slate-50"
                >
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

      <Sheet open={abierto} onOpenChange={setAbierto}>
        <SheetContent side="right" className="w-full max-w-md overflow-y-auto">
          <SheetHeader>
            <SheetTitle>{editando ? "Editar mantenimiento" : "Nuevo mantenimiento"}</SheetTitle>
          </SheetHeader>
          <form onSubmit={form.handleSubmit(onSubmit)} className="mt-4 flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="tipo">Tipo</Label>
                <select
                  id="tipo"
                  value={form.watch("tipo")}
                  onChange={(e) => form.setValue("tipo", e.target.value as MantenimientoForm["tipo"])}
                  className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  <option value="">Seleccionar tipo</option>
                  {TIPO_MANTENIMIENTO.map((t) => (
                    <option key={t} value={t}>
                      {t.charAt(0).toUpperCase() + t.slice(1)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fecha">Fecha</Label>
                <Input
                  id="fecha"
                  type="date"
                  {...form.register("fecha")}
                />
                {form.formState.errors.fecha && (
                  <span className="text-xs text-red-600">{form.formState.errors.fecha.message}</span>
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fecha_fin">Fecha fin (opcional)</Label>
                <Input
                  id="fecha_fin"
                  type="date"
                  {...form.register("fecha_fin", { valueAsDate: false })}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="km">Km</Label>
                <Input
                  id="km"
                  type="number"
                  min="0"
                  step="1"
                  {...form.register("km", { valueAsNumber: true })}
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="coste">Coste (€)</Label>
                <Input
                  id="coste"
                  type="number"
                  min="0"
                  step="0.01"
                  {...form.register("coste", { valueAsNumber: true })}
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="notas">Notas</Label>
              <textarea
                id="notas"
                rows={3}
                className="h-24 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm"
                {...form.register("notas")}
              />
            </div>

            {guardar.isError && (
              <p className="text-sm text-red-600">
                {guardar.error instanceof Error ? guardar.error.message : "Error al guardar el mantenimiento."}
              </p>
            )}

            <div className="mt-2 flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setAbierto(false)}>
                Cancelar
              </Button>
              <Button type="submit" disabled={guardar.isPending}>
                {guardar.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}