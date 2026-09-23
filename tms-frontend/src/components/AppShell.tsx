import type { ReactNode } from "react";
import type { Seccion, WsStatus } from "../types";
import { TopToolbar } from "./TopToolbar";

interface AppShellProps {
  seccion: Seccion;
  onSeccion: (s: Seccion) => void;
  wsStatus: WsStatus;
  onLogout: () => void;
  children: ReactNode;
}

/**
 * Layout principal — Ribbon + "efecto ventana".
 *
 * - Fondo oscuro sólido (bg-slate-900) en la raíz.
 * - Ribbon de navegación (TopToolbar) fijo arriba, sin sidebar.
 * - El contenido se renderiza dentro de una "ventana" blanca flotante
 *   (bg-white rounded-md shadow-xl p-2) que simula una app de escritorio.
 * - La ventana recibe `min-h-0 flex-1` para que AG Grid ocupe todo el alto
 *   restante sin scroll de página (clave en monitores ultrawide).
 */
export function AppShell({ seccion, onSeccion, wsStatus, onLogout, children }: AppShellProps) {
  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-slate-900 text-slate-100 antialiased">
      <TopToolbar
        seccion={seccion}
        onSeccion={onSeccion}
        wsStatus={wsStatus}
        onLogout={onLogout}
      />
      <main className="min-h-0 flex-1 p-2">
        <div className="flex h-full flex-col overflow-hidden rounded-md bg-white p-2 text-slate-900 shadow-xl">
          {children}
        </div>
      </main>
    </div>
  );
}
