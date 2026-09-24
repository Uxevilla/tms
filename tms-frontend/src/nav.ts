import type { LucideIcon } from "lucide-react";
import {
  Gauge,
  CalendarClock,
  Truck,
  MessageCircle,
  Car,
  Users,
  Wrench,
  FileText,
  Fuel,
  Calculator,
  Briefcase,
  FolderOpen,
  Settings,
  BarChart3,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Solo visible para admin (el dispatcher no ve secciones que le darían 403). */
  soloAdmin?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operar",
    items: [
      { to: "/torre", label: "Torre de control", icon: Gauge },
      { to: "/planificacion", label: "Planificación", icon: CalendarClock },
      { to: "/viajes", label: "Viajes", icon: Truck },
      { to: "/mensajes", label: "Mensajes", icon: MessageCircle },
    ],
  },
  {
    label: "Flota",
    items: [
      { to: "/vehiculos", label: "Vehículos", icon: Car },
      { to: "/conductores", label: "Conductores", icon: Users },
      { to: "/taller", label: "Taller", icon: Wrench },
    ],
  },
  {
    label: "Finanzas",
    items: [
      { to: "/facturacion", label: "Facturación", icon: FileText, soloAdmin: true },
      { to: "/gastos", label: "Gastos", icon: Fuel },
      { to: "/contabilidad", label: "Contabilidad", icon: Calculator, soloAdmin: true },
      // Temporal: KPIs se absorbe en Contabilidad (Fase 6); mientras tanto, ruta propia.
      { to: "/kpis", label: "KPIs", icon: BarChart3 },
    ],
  },
  {
    label: "Empresa",
    items: [
      { to: "/rrhh", label: "RRHH", icon: Briefcase, soloAdmin: true },
      { to: "/documentos", label: "Documentos", icon: FolderOpen },
      { to: "/configuracion", label: "Configuración", icon: Settings, soloAdmin: true },
    ],
  },
];

export function tituloPara(pathname: string): string {
  for (const g of NAV_GROUPS) {
    for (const it of g.items) {
      if (pathname === it.to || pathname.startsWith(it.to + "/")) return it.label;
    }
  }
  return "TMS";
}

export function itemsVisibles(rol: string | null): NavGroup[] {
  return NAV_GROUPS.map((g) => ({
    ...g,
    items: g.items.filter((i) => !i.soloAdmin || rol === "admin"),
  })).filter((g) => g.items.length > 0);
}
