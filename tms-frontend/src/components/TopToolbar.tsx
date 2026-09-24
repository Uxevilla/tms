import {
  LayoutGrid,
  Truck,
  Users,
  Calculator,
  Fuel,
  FolderOpen,
  BarChart3,
  Wifi,
  WifiOff,
  Loader2,
  LogOut,
  MessageCircle,
  Settings,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { Seccion, WsStatus } from "../types";
import { getRol } from "../auth";

const ITEMS: { id: Seccion; label: string; icon: LucideIcon }[] = [
  { id: "operaciones", label: "Operaciones", icon: LayoutGrid },
  { id: "vehiculos", label: "Vehículos", icon: Truck },
  { id: "rrhh", label: "RRHH", icon: Users },
  { id: "contabilidad", label: "Contabilidad", icon: Calculator },
  { id: "gastos", label: "Gastos", icon: Fuel },
  { id: "documentos", label: "Documentos", icon: FolderOpen },
  { id: "kpi", label: "KPIs", icon: BarChart3 },
  { id: "mensajeria", label: "Mensajería", icon: MessageCircle },
  { id: "configuracion", label: "Configuración", icon: Settings },
];

// Secciones restringidas a admin (el backend responde 403 para el resto de roles).
const SOLO_ADMIN: Seccion[] = ["contabilidad", "rrhh", "configuracion"];

interface TopToolbarProps {
  seccion: Seccion;
  onSeccion: (s: Seccion) => void;
  wsStatus: WsStatus;
  onLogout: () => void;
}

/**
 * Navegación tipo Ribbon: barra horizontal fija en la parte superior.
 * Sustituye al sidebar lateral. Botones anchos con icono grande (28px) y
 * el nombre del módulo debajo en texto pequeño y negrita.
 */
export function TopToolbar({ seccion, onSeccion, wsStatus, onLogout }: TopToolbarProps) {
  const rol = getRol();
  const visibles = ITEMS.filter((i) => rol === "admin" || !SOLO_ADMIN.includes(i.id));
  return (
    <header className="flex h-[72px] shrink-0 items-stretch gap-1 border-b border-slate-950 bg-slate-800 px-2 text-slate-300">
      {/* Marca */}
      <div className="flex items-center gap-2 border-r border-slate-700 pr-3">
        <span className="flex h-10 w-10 items-center justify-center rounded-md bg-blue-600 text-white shadow">
          <LayoutGrid size={22} />
        </span>
        <div className="hidden leading-tight lg:block">
          <div className="text-sm font-black tracking-tight text-white">TMS</div>
          <div className="text-[10px] font-medium text-slate-400">Flota</div>
        </div>
      </div>

      {/* Ribbon de módulos */}
      <nav className="flex flex-1 items-stretch gap-1 overflow-x-auto">
        {visibles.map(({ id, label, icon: Icon }) => {
          const activo = seccion === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => onSeccion(id)}
              aria-pressed={activo}
              className={`flex min-w-[76px] flex-col items-center justify-center gap-1 rounded-md px-3 transition-colors ${
                activo
                  ? "bg-blue-600 text-white shadow"
                  : "text-slate-300 hover:bg-slate-700 hover:text-white"
              }`}
            >
              <Icon size={28} strokeWidth={activo ? 2.2 : 1.8} />
              <span className="text-[11px] font-bold leading-none tracking-tight">{label}</span>
            </button>
          );
        })}
      </nav>

      {/* Estado WS + cerrar sesión */}
      <div className="flex items-center gap-2 border-l border-slate-700 pl-3">
        <IndicadorConexion status={wsStatus} />
        <button
          type="button"
          onClick={onLogout}
          title="Cerrar sesión"
          aria-label="Cerrar sesión"
          className="rounded-md p-2 text-slate-400 hover:bg-slate-700 hover:text-white"
        >
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}

function IndicadorConexion({ status }: { status: WsStatus }) {
  if (status === "conectado") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-[11px] font-semibold text-emerald-300 ring-1 ring-inset ring-emerald-500/30">
        <Wifi size={13} />
        Conectado
      </span>
    );
  }
  if (status === "conectando") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-700 px-2.5 py-1 text-[11px] font-semibold text-slate-300">
        <Loader2 size={13} className="animate-spin" />
        Conectando…
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-red-500/15 px-2.5 py-1 text-[11px] font-semibold text-red-300 ring-1 ring-inset ring-red-500/30">
      <WifiOff size={13} />
      Desconectado
    </span>
  );
}
