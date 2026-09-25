import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
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
import { REST_VEHICULOS, PATCH_VEHICULO } from "../config";
import { CaducidadRenderer } from "./CaducidadRenderer";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";

// ------------------------------------------------------------------ tipos
interface Vehiculo {
  id: string;
  terminal_trimble?: string;
  app_terminal?: string;
  categoria: string;
  matricula: string;
  marca: string;
  modelo: string;
  anno: number;
  itv: string;
  seguro: string;
  clase_euro: string;
  capacidad_peso: number;
  capacidad_palets: number;
  coste_adquisicion: number;
  valor_residual: number;
  vida_util: number;
  disponible: boolean;
  ejes?: number;
  mma?: number;
  fecha_caducidad_itv?: string;
  seguro_compania?: string;
  fecha_caducidad_seguro?: string;
  tipo_tenencia?: string;
  proveedor_id?: number | null;
  proveedor_nombre?: string;
  fecha_alta?: string;
  cuota_mensual?: number;
  km_actuales?: number;
}

const CATEGORIAS = ["tractora", "semirremolque", "frigorifico", "furgon", "ligero", "cisterna"] as const;
const TENENCIAS = ["Propiedad", "Renting", "Leasing"] as const;

// ------------------------------------------------------------------ formulario
const vehiculoSchema = z.object({
  id: z.string().trim().min(1, "Requerido (referencia interna)"),
  terminal_trimble: z.string().trim(),
  app_terminal: z.string().trim(),
  matricula: z.string().trim().min(1, "Requerido"),
  categoria: z.enum(CATEGORIAS),
  marca: z.string().trim(),
  modelo: z.string().trim(),
  anno: z.number().int().min(1950).max(2100),
  ejes: z.number().int().min(0),
  clase_euro: z.string().trim(),
  capacidad_peso: z.number().min(0),
  capacidad_palets: z.number().int().min(0),
  fecha_caducidad_itv: z.string(),
  seguro_compania: z.string().trim(),
  fecha_caducidad_seguro: z.string(),
  tipo_tenencia: z.enum(TENENCIAS),
  proveedor_id: z.string(),
  fecha_alta: z.string(),
  cuota_mensual: z.number().min(0),
});

type VehiculoForm = z.infer<typeof vehiculoSchema>;

const valoresPorDefecto: VehiculoForm = {
  id: "",
  terminal_trimble: "",
  app_terminal: "",
  matricula: "",
  categoria: "tractora",
  marca: "",
  modelo: "",
  anno: new Date().getFullYear(),
  ejes: 0,
  clase_euro: "EURO 6",
  capacidad_peso: 0,
  capacidad_palets: 0,
  fecha_caducidad_itv: "",
  seguro_compania: "",
  fecha_caducidad_seguro: "",
  tipo_tenencia: "Propiedad",
  proveedor_id: "",
  fecha_alta: "",
  cuota_mensual: 0,
};

const fmtEuro = new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" });
const fmtKm = new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 });

// ------------------------------------------------------------------ página
export function VehiculosPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [abierto, setAbierto] = useState(false);
  const [editando, setEditando] = useState<Vehiculo | null>(null);

  const { data: vehiculos = [], isLoading } = useQuery({
    queryKey: ["vehiculos"],
    queryFn: async () => (await api<{ vehiculos?: Vehiculo[] }>(REST_VEHICULOS)).vehiculos ?? [],
  });

  const form = useForm<VehiculoForm>({
    resolver: zodResolver(vehiculoSchema),
    defaultValues: valoresPorDefecto,
  });

  const guardar = useMutation({
    mutationFn: async ({ editar, valores }: { editar: Vehiculo | null; valores: VehiculoForm }) => {
      if (editar) {
        const body = {
          terminal_trimble: valores.terminal_trimble,
          fecha_caducidad_itv: valores.fecha_caducidad_itv,
          seguro_compania: valores.seguro_compania,
          fecha_caducidad_seguro: valores.fecha_caducidad_seguro,
          tipo_tenencia: valores.tipo_tenencia,
          proveedor_id: valores.proveedor_id ? Number(valores.proveedor_id) : null,
          fecha_alta: valores.fecha_alta,
          cuota_mensual: valores.cuota_mensual,
          clase_euro: valores.clase_euro,
          capacidad_peso: valores.capacidad_peso,
          capacidad_palets: valores.capacidad_palets,
          ejes: valores.ejes,
        };
        return api(PATCH_VEHICULO(editar.id), { method: "PATCH", body: JSON.stringify(body) });
      }
      const body = {
        id: valores.id,
        terminal_trimble: valores.terminal_trimble,
        app_terminal: valores.app_terminal,
        matricula: valores.matricula,
        categoria: valores.categoria,
        marca: valores.marca,
        modelo: valores.modelo,
        anno: valores.anno,
        ejes: valores.ejes,
        clase_euro: valores.clase_euro,
        capacidad_peso: valores.capacidad_peso,
        capacidad_palets: valores.capacidad_palets,
        fecha_caducidad_itv: valores.fecha_caducidad_itv,
        seguro_compania: valores.seguro_compania,
        fecha_caducidad_seguro: valores.fecha_caducidad_seguro,
        tipo_tenencia: valores.tipo_tenencia,
        proveedor_id: valores.proveedor_id ? Number(valores.proveedor_id) : null,
        fecha_alta: valores.fecha_alta,
        cuota_mensual: valores.cuota_mensual,
      };
      return api(REST_VEHICULOS, { method: "POST", body: JSON.stringify(body) });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["vehiculos"] });
      setAbierto(false);
      setEditando(null);
      form.reset(valoresPorDefecto);
    },
  });

  const columns = useMemo<ColumnDef<Vehiculo>[]>(
    () => [
      {
        accessorKey: "matricula",
        header: "Matrícula",
        cell: ({ row }) => (
          <span
            data-panel={`vehiculo:${row.original.id}`}
            className="cursor-pointer font-medium text-primary hover:underline"
          >
            {row.original.matricula}
          </span>
        ),
      },
      {
        accessorKey: "categoria",
        header: "Categoría",
        cell: ({ row }) => <span className="capitalize">{row.original.categoria}</span>,
      },
      {
        accessorKey: "marca",
        header: "Marca / Modelo",
        cell: ({ row }) => {
          const { marca, modelo } = row.original;
          return <span className="text-slate-600">{[marca, modelo].filter(Boolean).join(" ") || "—"}</span>;
        },
      },
      {
        accessorKey: "fecha_caducidad_itv",
        header: "ITV",
        cell: ({ row }) => <CaducidadRenderer value={row.original.fecha_caducidad_itv ?? null} />,
      },
      {
        accessorKey: "fecha_caducidad_seguro",
        header: "Seguro",
        cell: ({ row }) => <CaducidadRenderer value={row.original.fecha_caducidad_seguro ?? null} />,
      },
      {
        accessorKey: "km_actuales",
        header: "Km",
        cell: ({ row }) => {
          const km = row.original.km_actuales;
          return km == null ? <span className="text-slate-300">—</span> : <span>{fmtKm.format(km)}</span>;
        },
      },
      {
        accessorKey: "coste_adquisicion",
        header: "Coste",
        cell: ({ row }) => <span>{fmtEuro.format(row.original.coste_adquisicion ?? 0)}</span>,
      },
      {
        accessorKey: "disponible",
        header: "Disponible",
        cell: ({ row }) => (
          <span
            className={
              row.original.disponible
                ? "inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700"
                : "inline-flex rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
            }
          >
            {row.original.disponible ? "Sí" : "No"}
          </span>
        ),
      },
      {
        id: "acciones",
        header: "",
        cell: ({ row }) => (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setEditando(row.original);
              form.reset({
                ...valoresPorDefecto,
                id: row.original.id,
                terminal_trimble: row.original.terminal_trimble ?? "",
                matricula: row.original.matricula,
                categoria: (row.original.categoria as VehiculoForm["categoria"]) ?? "tractora",
                marca: row.original.marca,
                modelo: row.original.modelo,
                anno: row.original.anno,
                ejes: row.original.ejes ?? 0,
                clase_euro: row.original.clase_euro,
                capacidad_peso: row.original.capacidad_peso,
                capacidad_palets: row.original.capacidad_palets,
                fecha_caducidad_itv: row.original.fecha_caducidad_itv ?? "",
                seguro_compania: row.original.seguro_compania ?? "",
                fecha_caducidad_seguro: row.original.fecha_caducidad_seguro ?? "",
                tipo_tenencia: (row.original.tipo_tenencia as VehiculoForm["tipo_tenencia"]) ?? "Propiedad",
                proveedor_id: row.original.proveedor_id != null ? String(row.original.proveedor_id) : "",
                fecha_alta: row.original.fecha_alta ?? "",
                cuota_mensual: row.original.cuota_mensual ?? 0,
              });
              setAbierto(true);
            }}
            className="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
            title="Editar vehículo"
          >
            <Pencil size={15} />
          </button>
        ),
      },
    ],
    [form],
  );

  const table = useReactTable({
    data: vehiculos,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  function abrirAlta() {
    setEditando(null);
    form.reset(valoresPorDefecto);
    setAbierto(true);
  }

  const onSubmit = (valores: VehiculoForm) => {
    guardar.mutate({ editar: editando, valores });
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Vehículos</h1>
          <p className="text-sm text-slate-500">
            {vehiculos.length} vehículos · clic en la matrícula para abrir el panel
          </p>
        </div>
        <Button onClick={abrirAlta}>
          <Plus size={16} className="mr-1.5" />
          Nuevo vehículo
        </Button>
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
                  Cargando vehículos…
                </td>
              </tr>
            ) : table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={table.getAllColumns().length} className="px-3 py-10 text-center text-slate-400">
                  Sin vehículos. Crea el primero con «Nuevo vehículo».
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => navigate({ search: { panel: `vehiculo:${row.original.id}` } as never })}
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
            <SheetTitle>{editando ? "Editar vehículo" : "Nuevo vehículo"}</SheetTitle>
          </SheetHeader>
          <form onSubmit={form.handleSubmit(onSubmit)} className="mt-4 flex flex-col gap-4">
            {!editando && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="id">Código interno</Label>
                  <Input id="id" placeholder="Ej: VH-001" {...form.register("id")} />
                  {form.formState.errors.id && (
                    <span className="text-xs text-red-600">{form.formState.errors.id.message}</span>
                  )}
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="matricula">Matrícula</Label>
                  <Input id="matricula" placeholder="0000 ABC" {...form.register("matricula")} />
                  {form.formState.errors.matricula && (
                    <span className="text-xs text-red-600">{form.formState.errors.matricula.message}</span>
                  )}
                </div>
              </div>
            )}

            {!editando && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="categoria">Categoría</Label>
                  <select
                    id="categoria"
                    {...form.register("categoria")}
                    className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                  >
                    {CATEGORIAS.map((c) => (
                      <option key={c} value={c} className="capitalize">
                        {c}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="anno">Año</Label>
                  <Input id="anno" type="number" {...form.register("anno", { valueAsNumber: true })} />
                </div>
              </div>
            )}

            {!editando && (
              <div className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="marca">Marca</Label>
                  <Input id="marca" {...form.register("marca")} />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="modelo">Modelo</Label>
                  <Input id="modelo" {...form.register("modelo")} />
                </div>
              </div>
            )}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="terminal_trimble">ID de telemetría (Trimble)</Label>
              <Input
                id="terminal_trimble"
                placeholder="ID del terminal en el proveedor de telemetría"
                {...form.register("terminal_trimble")}
              />
              <span className="text-xs text-slate-400">
                Identifica el vehículo en el proveedor de telemetría (hoy Trimble). Distinto de la
                matrícula, que es la referencia del vehículo en el TMS.
              </span>
            </div>

            {!editando && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="app_terminal">Terminal APP (Fleet XPS)</Label>
                <Input
                  id="app_terminal"
                  placeholder="ID de la app del conductor en Trimble"
                  {...form.register("app_terminal")}
                />
                <span className="text-xs text-slate-400">
                  Terminal donde el conductor recibe el viaje y los formularios. Si se deja vacío se
                  usa el terminal por defecto.
                </span>
              </div>
            )}

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="clase_euro">Clase Euro</Label>
                <Input id="clase_euro" {...form.register("clase_euro")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="ejes">Ejes</Label>
                <Input id="ejes" type="number" {...form.register("ejes", { valueAsNumber: true })} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="capacidad_peso">Capacidad (kg)</Label>
                <Input id="capacidad_peso" type="number" {...form.register("capacidad_peso", { valueAsNumber: true })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="capacidad_palets">Capacidad (palets)</Label>
                <Input id="capacidad_palets" type="number" {...form.register("capacidad_palets", { valueAsNumber: true })} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fecha_caducidad_itv">ITV (caducidad)</Label>
                <Input id="fecha_caducidad_itv" type="date" {...form.register("fecha_caducidad_itv")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fecha_caducidad_seguro">Seguro (caducidad)</Label>
                <Input id="fecha_caducidad_seguro" type="date" {...form.register("fecha_caducidad_seguro")} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="seguro_compania">Compañía de seguro</Label>
                <Input id="seguro_compania" {...form.register("seguro_compania")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="tipo_tenencia">Tenencia</Label>
                <select
                  id="tipo_tenencia"
                  {...form.register("tipo_tenencia")}
                  className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                >
                  {TENENCIAS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fecha_alta">Fecha de alta</Label>
                <Input id="fecha_alta" type="date" {...form.register("fecha_alta")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="cuota_mensual">Cuota mensual (€)</Label>
                <Input id="cuota_mensual" type="number" step="0.01" {...form.register("cuota_mensual", { valueAsNumber: true })} />
              </div>
            </div>

            {guardar.isError && (
              <p className="text-sm text-red-600">
                {guardar.error instanceof Error ? guardar.error.message : "Error al guardar el vehículo."}
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
