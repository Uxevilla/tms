import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";

import { api } from "@/api";
import { itemsVisibles } from "@/nav";
import { getRol } from "@/auth";
import { REST_BUSCAR } from "@/config";
import type { BuscarResultado } from "@/types";
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Paleta de comandos (Ctrl/⌘+K). Con búsqueda global (/api/buscar, debounce 150 ms)
 * + acciones ("Nuevo viaje"). Los resultados de entidad abren el panel lateral.
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const grupos = itemsVisibles(getRol());
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");

  // Debounce de 150 ms para no disparar una petición por tecla.
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 150);
    return () => window.clearTimeout(t);
  }, [q]);

  const busqueda = useQuery({
    queryKey: ["buscar", debounced],
    queryFn: () => api<{ resultados: BuscarResultado[] }>(`${REST_BUSCAR}?q=${encodeURIComponent(debounced)}`),
    enabled: debounced.length >= 2,
  });

  const buscando = debounced.length >= 2;

  const abrirResultado = (r: BuscarResultado) => {
    onOpenChange(false);
    if (r.tipo === "vehiculo" || r.tipo === "viaje" || r.tipo === "conductor") {
      navigate({ search: { panel: `${r.tipo}:${r.id}` } as never });
    } else if (r.tipo === "factura") {
      navigate({ to: "/facturacion" });
    } else if (r.tipo === "cliente") {
      navigate({ to: "/contabilidad" });
    } else {
      navigate({ to: "/gastos" });
    }
  };

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange} shouldFilter={false}>
      <CommandInput
        placeholder="Buscar viajes, vehículos, conductores…"
        value={q}
        onValueChange={setQ}
      />
      <CommandList>
        {buscando ? (
          <>
            <CommandEmpty>
              {busqueda.isLoading ? "Buscando…" : "Sin resultados."}
            </CommandEmpty>
            <CommandGroup heading="Resultados">
              {(busqueda.data?.resultados ?? []).map((r) => (
                <CommandItem
                  key={`${r.tipo}:${r.id}`}
                  value={`${r.tipo}:${r.id}`}
                  onSelect={() => abrirResultado(r)}
                >
                  <span className="truncate">{r.titulo}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted-foreground">{r.subtitulo}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          </>
        ) : (
          <>
            <CommandGroup heading="Acciones">
              <CommandItem
                value="accion:nuevo-viaje"
                onSelect={() => {
                  onOpenChange(false);
                  navigate({ to: "/planificacion" });
                }}
              >
                <Plus />
                <span>Nuevo viaje</span>
              </CommandItem>
            </CommandGroup>
            {grupos.map((grupo) => (
              <CommandGroup key={grupo.label} heading={grupo.label}>
                {grupo.items.map((item) => (
                  <CommandItem
                    key={item.to}
                    value={`nav:${item.to}`}
                    onSelect={() => {
                      navigate({ to: item.to });
                      onOpenChange(false);
                    }}
                  >
                    <item.icon />
                    <span>{item.label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </>
        )}
      </CommandList>
    </CommandDialog>
  );
}
