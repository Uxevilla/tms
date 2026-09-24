import { lazy, Suspense, useEffect, useState } from "react";
import { Outlet, useNavigate, useRouterState, useSearch } from "@tanstack/react-router";

import { SocketProvider, useSocketStatus } from "../context/SocketContext";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { CommandPalette } from "./CommandPalette";
import { WsQueryAdapter } from "./WsQueryAdapter";
import { TooltipProvider } from "./ui/tooltip";
import { clearToken, getToken } from "../auth";

// El panel de entidad carga en diferido: solo se descarga al abrir ?panel=….
const EntityPanel = lazy(() => import("./EntityPanel").then((m) => ({ default: m.EntityPanel })));

/**
 * Layout autenticado (esqueleto nuevo): barra lateral plegable + cabecera +
 * contenido. Sustituye al ribbon + "efecto ventana" anterior.
 */
export function AppShell() {
  const token = getToken();
  if (!token) return null; // no debería ocurrir (guard), por seguridad
  return (
    <SocketProvider token={token}>
      <AppShellInner />
    </SocketProvider>
  );
}

function AppShellInner() {
  const wsStatus = useSocketStatus();
  const navigate = useNavigate();
  const [paleta, setPaleta] = useState(false);
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [mostrarAviso, setMostrarAviso] = useState(false);
  const { panel } = useSearch({ from: "/app" });
  const cerrarPanel = () => navigate({ search: { panel: undefined } as never });

  // Ctrl/⌘+K abre/cierra la paleta de comandos.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaleta((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Aviso de ruta sin permiso (redirigida desde un guard de rol).
  useEffect(() => {
    const aviso = sessionStorage.getItem("tms_aviso");
    if (aviso) {
      sessionStorage.removeItem("tms_aviso");
      setMostrarAviso(true);
      const t = window.setTimeout(() => setMostrarAviso(false), 4000);
      return () => window.clearTimeout(t);
    }
  }, [pathname]);

  // Sesión caducada en caliente (WS 1008 o fetch 401) → logout + login conservando destino.
  useEffect(() => {
    const onCaducada = () => {
      clearToken();
      const redirect = window.location.pathname + window.location.search;
      navigate({ to: "/login", search: { redirect } });
    };
    window.addEventListener("tms:sesion-caducada", onCaducada);
    return () => window.removeEventListener("tms:sesion-caducada", onCaducada);
  }, [navigate]);

  // Panel de entidad desde cualquier pantalla: cualquier elemento con [data-panel="tipo:id"]
  // (celdas de las tablas antiguas, marcadores, avisos…) abre el panel sin cambiar de ruta.
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.("[data-panel]");
      const panel = el?.getAttribute("data-panel");
      if (panel) navigate({ search: { panel } as never });
    };
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [navigate]);

  const logout = () => {
    clearToken();
    navigate({ to: "/login" });
  };

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground antialiased">
        <Sidebar wsStatus={wsStatus} onLogout={logout} />
        <div className="flex min-w-0 flex-1 flex-col">
          <Header onOpenPalette={() => setPaleta(true)} />
          <main className="relative min-h-0 flex-1 overflow-hidden">
            {mostrarAviso && (
              <div className="absolute left-1/2 top-3 z-40 -translate-x-1/2 rounded-md border border-estado-aviso/40 bg-estado-aviso/10 px-4 py-2 text-sm text-estado-aviso">
                No tienes permiso para esa sección.
              </div>
            )}
            <Suspense fallback={<Cargando />}>
              <Outlet />
            </Suspense>
          </main>
        </div>
        <CommandPalette open={paleta} onOpenChange={setPaleta} />
        <WsQueryAdapter />
        {panel && (
          <Suspense fallback={null}>
            <EntityPanel panel={panel} onClose={cerrarPanel} />
          </Suspense>
        )}
      </div>
    </TooltipProvider>
  );
}

function Cargando() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      Cargando…
    </div>
  );
}
