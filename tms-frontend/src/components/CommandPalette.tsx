import { useNavigate } from "@tanstack/react-router";

import { itemsVisibles } from "@/nav";
import { getRol } from "@/auth";
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
 * Paleta de comandos (Ctrl/⌘+K). Fase 1: solo navegación entre rutas visibles
 * para el rol. La búsqueda global (/api/buscar) llega en la Fase 2.
 */
export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const navigate = useNavigate();
  const grupos = itemsVisibles(getRol());

  return (
    <CommandDialog open={open} onOpenChange={onOpenChange}>
      <CommandInput placeholder="Ir a… (buscar llegará en la fase 2)" />
      <CommandList>
        <CommandEmpty>Sin resultados.</CommandEmpty>
        {grupos.map((grupo) => (
          <CommandGroup key={grupo.label} heading={grupo.label}>
            {grupo.items.map((item) => (
              <CommandItem
                key={item.to}
                value={item.label}
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
      </CommandList>
    </CommandDialog>
  );
}
