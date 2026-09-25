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
import { REST_CONDUCTORES, PATCH_EMPLEADO } from "../config";
import { CaducidadRenderer } from "./CaducidadRenderer";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "./ui/sheet";

// ------------------------------------------------------------------ tipos
interface Conductor {
  id: string;
  empleado_id: string;
  nombre: string;
  dni: string;
  telefono: string;
  email: string;
  did?: string;
  caducidad_carnet?: string | null;
  caducidad_cap?: string | null;
  caducidad_medica?: string | null;
  disponible: boolean;
  motivo_ausencia?: string | null;
}

// ------------------------------------------------------------------ formulario alta/edición conductor
const conductorSchema = z.object({
  nombre: z.string().trim().min(1, "Requerido"),
  dni: z.string().trim().min(1, "Requerido"),
  telefono: z.string().trim().min(1, "Requerido"),
  email: z.string().trim().email("Email inválido").or(z.string().trim().length(0)),
  did: z.string().trim(),
});

type ConductorForm = z.infer<typeof conductorSchema>;

const valoresPorDefecto: ConductorForm = {
  nombre: "",
  dni: "",
  telefono: "",
  email: "",
  did: "",
};

// ------------------------------------------------------------------ formulario caducidades (edita empleado)
const caducidadesSchema = z.object({
  caducidad_carnet: z.string().optional().nullable(),
  caducidad_cap: z.string().optional().nullable(),
  caducidad_medica: z.string().optional().nullable(),
});

type CaducidadesForm = z.infer<typeof caducidadesSchema>;

// ------------------------------------------------------------------ página
export function ConductoresPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [abierto, setAbierto] = useState(false);
  const [editando, setEditando] = useState<Conductor | null>(null);
  const [editandoCaducidades, setEditandoCaducidades] = useState<Conductor | null>(null);

  const { data: conductores = [], isLoading } = useQuery({
    queryKey: ["conductores"],
    queryFn: async () => (await api<{ conductores?: Conductor[] }>(REST_CONDUCTORES)).conductores ?? [],
  });

  const form = useForm<ConductorForm>({
    resolver: zodResolver(conductorSchema),
    defaultValues: valoresPorDefecto,
  });

  const formCaducidades = useForm<CaducidadesForm>({
    resolver: zodResolver(caducidadesSchema),
    defaultValues: { caducidad_carnet: "", caducidad_cap: "", caducidad_medica: "" },
  });

  const guardar = useMutation({
    mutationFn: async ({ editar, valores }: { editar: Conductor | null; valores: ConductorForm }) => {
      if (editar) {
        // Solo edita campos básicos del conductor (no caducidades)
        const body = {
          nombre: valores.nombre,
          dni: valores.dni,
          telefono: valores.telefono,
          email: valores.email,
          did: valores.did,
        };
        return api(`/api/conductores/${editar.id}`, { method: "PATCH", body: JSON.stringify(body) });
      }
      const body = {
        nombre: valores.nombre,
        dni: valores.dni,
        telefono: valores.telefono,
        email: valores.email,
        did: valores.did,
      };
      return api(REST_CONDUCTORES, { method: "POST", body: JSON.stringify(body) });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conductores"] });
      setAbierto(false);
      setEditando(null);
      form.reset(valoresPorDefecto);
    },
  });

  const guardarCaducidades = useMutation({
    mutationFn: async ({ conductor, valores }: { conductor: Conductor; valores: CaducidadesForm }) => {
      const body = {
        caducidad_carnet: valores.caducidad_carnet || null,
        caducidad_cap: valores.caducidad_cap || null,
        caducidad_medica: valores.caducidad_medica || null,
      };
      return api(PATCH_EMPLEADO(conductor.empleado_id), { method: "PATCH", body: JSON.stringify(body) });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conductores"] });
      setEditandoCaducidades(null);
      formCaducidades.reset({ caducidad_carnet: "", caducidad_cap: "", caducidad_medica: "" });
    },
  });

  const columns = useMemo<ColumnDef<Conductor>[]>(
    () => [
      {
        accessorKey: "nombre",
        header: "Nombre",
        cell: ({ row }) => (
          <span
            data-panel={`conductor:${row.original.id}`}
            className="cursor-pointer font-medium text-primary hover:underline"
          >
            {row.original.nombre}
          </span>
        ),
      },
      {
        accessorKey: "dni",
        header: "DNI",
      },
      {
        accessorKey: "telefono",
        header: "Teléfono",
      },
      {
        accessorKey: "did",
        header: "ID Trimble",
        cell: ({ row }) => <span className="font-mono text-sm">{row.original.did ?? "—"}</span>,
      },
      {
        accessorKey: "caducidad_carnet",
        header: "Carnet",
        cell: ({ row }) => <CaducidadRenderer value={row.original.caducidad_carnet ?? null} />,
      },
      {
        accessorKey: "caducidad_cap",
        header: "CAP",
        cell: ({ row }) => <CaducidadRenderer value={row.original.caducidad_cap ?? null} />,
      },
      {
        accessorKey: "caducidad_medica",
        header: "Médica",
        cell: ({ row }) => <CaducidadRenderer value={row.original.caducidad_medica ?? null} />,
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
            {row.original.motivo_ausencia && (
              <span className="ml-1 text-xs text-slate-500">({row.original.motivo_ausencia})</span>
            )}
          </span>
        ),
      },
      {
        id: "acciones",
        header: "",
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setEditando(row.original);
                form.reset({
                  ...valoresPorDefecto,
                  nombre: row.original.nombre,
                  dni: row.original.dni,
                  telefono: row.original.telefono,
                  email: row.original.email,
                  did: row.original.did ?? "",
                });
                setAbierto(true);
              }}
              className="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              title="Editar conductor"
            >
              <Pencil size={15} />
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setEditandoCaducidades(row.original);
                formCaducidades.reset({
                  caducidad_carnet: row.original.caducidad_carnet ?? "",
                  caducidad_cap: row.original.caducidad_cap ?? "",
                  caducidad_medica: row.original.caducidad_medica ?? "",
                });
              }}
              className="rounded p-1 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
              title="Editar caducidades"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width={15}
                height={15}
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 22V12" />
                <path d="M4 8l8 8 8-8" />
                <path d="M8 14h8" />
              </svg>
            </button>
          </div>
        ),
      },
    ],
    [form, formCaducidades],
  );

  const table = useReactTable({
    data: conductores,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  function abrirAlta() {
    setEditando(null);
    form.reset(valoresPorDefecto);
    setAbierto(true);
  }

  function abrirCaducidades(conductor: Conductor) {
    setEditandoCaducidades(conductor);
    formCaducidades.reset({
      caducidad_carnet: conductor.caducidad_carnet ?? "",
      caducidad_cap: conductor.caducidad_cap ?? "",
      caducidad_medica: conductor.caducidad_medica ?? "",
    });
  }

  const onSubmit = (valores: ConductorForm) => {
    guardar.mutate({ editar: editando, valores });
  };

  const onSubmitCaducidades = (valores: CaducidadesForm) => {
    if (editandoCaducidades) {
      guardarCaducidades.mutate({ conductor: editandoCaducidades, valores });
    }
  };

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Conductores</h1>
          <p className="text-sm text-slate-500">
            {conductores.length} conductores · clic en el nombre para abrir el panel
          </p>
        </div>
        <Button onClick={abrirAlta}>
          <Plus size={16} className="mr-1.5" />
          Nuevo conductor
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
                  Cargando conductores…
                </td>
              </tr>
            ) : table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={table.getAllColumns().length} className="px-3 py-10 text-center text-slate-400">
                  Sin conductores. Crea el primero con «Nuevo conductor».
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => navigate({ search: { panel: `conductor:${row.original.id}` } as never })}
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

      {/* Sheet alta/edición conductor */}
      <Sheet open={abierto} onOpenChange={setAbierto}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[480px]">
          <SheetHeader className="shrink-0">
            <SheetTitle>{editando ? "Editar conductor" : "Nuevo conductor"}</SheetTitle>
          </SheetHeader>
          <form onSubmit={form.handleSubmit(onSubmit)} className="mt-4 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 pb-6">
            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="nombre">Nombre</Label>
                <Input id="nombre" placeholder="Juan Pérez" {...form.register("nombre")} />
                {form.formState.errors.nombre && (
                  <span className="text-xs text-red-600">{form.formState.errors.nombre.message}</span>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="dni">DNI</Label>
                <Input id="dni" placeholder="12345678A" {...form.register("dni")} />
                {form.formState.errors.dni && (
                  <span className="text-xs text-red-600">{form.formState.errors.dni.message}</span>
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="telefono">Teléfono</Label>
                <Input id="telefono" placeholder="600 000 000" {...form.register("telefono")} />
                {form.formState.errors.telefono && (
                  <span className="text-xs text-red-600">{form.formState.errors.telefono.message}</span>
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input id="email" type="email" placeholder="juan@ejemplo.com" {...form.register("email")} />
                {form.formState.errors.email && (
                  <span className="text-xs text-red-600">{form.formState.errors.email.message}</span>
                )}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="did">ID Trimble (DID)</Label>
              <Input id="did" placeholder="001" {...form.register("did")} />
              <span className="text-xs text-slate-400">
                ID del conductor en Trimble. Los datos del tacógrafo (T4U) quedan asociados a este ID.
              </span>
            </div>

            {guardar.isError && (
              <p className="text-sm text-red-600">
                {guardar.error instanceof Error ? guardar.error.message : "Error al guardar el conductor."}
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

      {/* Sheet editar caducidades */}
      <Sheet open={!!editandoCaducidades} onOpenChange={(v) => !v && setEditandoCaducidades(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[480px]">
          <SheetHeader className="shrink-0">
            <SheetTitle>Editar caducidades — {editandoCaducidades?.nombre}</SheetTitle>
          </SheetHeader>
          <form onSubmit={formCaducidades.handleSubmit(onSubmitCaducidades)} className="mt-4 flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 pb-6">
            <p className="text-sm text-slate-500">
              Las caducidades se guardan en el empleado vinculado (PATCH /api/empleados/&#123;id&#125;).
            </p>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="caducidad_carnet">Carnet (caducidad)</Label>
                <Input id="caducidad_carnet" type="date" {...formCaducidades.register("caducidad_carnet")} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="caducidad_cap">CAP (caducidad)</Label>
                <Input id="caducidad_cap" type="date" {...formCaducidades.register("caducidad_cap")} />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="caducidad_medica">Médica (caducidad)</Label>
              <Input id="caducidad_medica" type="date" {...formCaducidades.register("caducidad_medica")} />
            </div>

            {guardarCaducidades.isError && (
              <p className="text-sm text-red-600">
                {guardarCaducidades.error instanceof Error ? guardarCaducidades.error.message : "Error al guardar caducidades."}
              </p>
            )}

            <div className="mt-2 flex justify-end gap-2">
              <Button type="button" variant="outline" onClick={() => setEditandoCaducidades(null)}>
                Cancelar
              </Button>
              <Button type="submit" disabled={guardarCaducidades.isPending}>
                {guardarCaducidades.isPending ? "Guardando…" : "Guardar"}
              </Button>
            </div>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}