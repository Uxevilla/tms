import { useRouterState } from "@tanstack/react-router";
import { Search } from "lucide-react";

import { tituloPara } from "@/nav";
import { Button } from "@/components/ui/button";

export function Header({ onOpenPalette }: { onOpenPalette: () => void }) {
  const router = useRouterState();
  const titulo = tituloPara(router.location.pathname);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b bg-card px-4">
      <h1 className="text-sm font-semibold tracking-tight">{titulo}</h1>
      <Button
        variant="outline"
        size="sm"
        className="ml-auto h-7 gap-2 text-muted-foreground"
        onClick={onOpenPalette}
      >
        <Search className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">Buscar</span>
        <kbd className="pointer-events-none hidden select-none rounded border px-1.5 text-[10px] font-normal sm:inline">
          Ctrl K
        </kbd>
      </Button>
    </header>
  );
}
