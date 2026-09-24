import { useState } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { ChevronLeft, ChevronRight, LogOut, Moon, Sun, Wifi, WifiOff, Loader2, LayoutGrid } from "lucide-react";

import { cn } from "@/lib/utils";
import { itemsVisibles } from "@/nav";
import { getRol, getUsuario } from "@/auth";
import { useTheme } from "@/theme";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { WsStatus } from "@/types";

interface SidebarProps {
  wsStatus: WsStatus;
  onLogout: () => void;
}

const WS_ETIQUETA: Record<WsStatus, string> = {
  conectando: "Conectando…",
  conectado: "Conectado",
  desconectado: "Desconectado",
};

export function Sidebar({ wsStatus, onLogout }: SidebarProps) {
  const [plegada, setPlegada] = useState(false);
  const rol = getRol();
  const usuario = getUsuario();
  const grupos = itemsVisibles(rol);
  const router = useRouterState();
  const pathname = router.location.pathname;
  const { theme, toggle } = useTheme();

  return (
    <aside
      className={cn(
        "flex h-full shrink-0 flex-col border-r bg-card transition-[width] duration-200",
        plegada ? "w-14" : "w-[232px]",
      )}
    >
      {/* Marca + plegar */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b px-2">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <LayoutGrid size={16} />
        </span>
        {!plegada && <span className="text-sm font-semibold tracking-tight">TMS Flota</span>}
        <Button
          variant="ghost"
          size="icon"
          className="ml-auto h-7 w-7"
          onClick={() => setPlegada((v) => !v)}
          aria-label={plegada ? "Desplegar barra lateral" : "Plegar barra lateral"}
        >
          {plegada ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </Button>
      </div>

      {/* Navegación por grupos */}
      <nav className="min-h-0 flex-1 overflow-y-auto p-2">
        {grupos.map((grupo) => (
          <div key={grupo.label} className="mb-3">
            {!plegada && (
              <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                {grupo.label}
              </div>
            )}
            <div className="space-y-0.5">
              {grupo.items.map((item) => {
                const activo = pathname === item.to || pathname.startsWith(item.to + "/");
                const enlace = (
                  <Link
                    key={item.to}
                    to={item.to}
                    className={cn(
                      "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium transition-colors",
                      activo
                        ? "bg-primary text-primary-foreground"
                        : "text-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    <item.icon className="h-4 w-4 shrink-0" />
                    {!plegada && <span className="truncate">{item.label}</span>}
                  </Link>
                );
                if (plegada) {
                  return (
                    <Tooltip key={item.to}>
                      <TooltipTrigger asChild>{enlace}</TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  );
                }
                return enlace;
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Pie: WS · usuario · tema · salir */}
      <div className="shrink-0 space-y-1 border-t p-2">
        <div className={cn("flex items-center gap-2 rounded-md px-2 py-1 text-xs text-muted-foreground", plegada && "justify-center")}>
          {wsStatus === "conectado" ? (
            <Wifi className="h-3.5 w-3.5 text-estado-libre" />
          ) : wsStatus === "conectando" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <WifiOff className="h-3.5 w-3.5 text-estado-critico" />
          )}
          {!plegada && <span>{WS_ETIQUETA[wsStatus]}</span>}
        </div>
        {!plegada && usuario && (
          <div className="truncate px-2 py-1 text-xs font-medium text-foreground">{usuario}</div>
        )}
        <div className={cn("flex items-center gap-1", plegada && "flex-col")}>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={toggle} aria-label="Cambiar tema">
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Tema claro/oscuro</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onLogout} aria-label="Cerrar sesión">
                <LogOut className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">Cerrar sesión</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </aside>
  );
}
