import type { ViajeEstado } from "../types";

// Colores semánticos de Tailwind por estado (badge redondeado).
const ESTILOS: Record<ViajeEstado, string> = {
  "Llegada_Origen": "bg-cyan-100 text-cyan-800 ring-cyan-200",
  "Llegada_Destino": "bg-teal-100 text-teal-800 ring-teal-200",
  "Cargando": "bg-amber-100 text-amber-800 ring-amber-300",
  "Descargando": "bg-orange-100 text-orange-800 ring-orange-300",
  "En_Transito": "bg-blue-100 text-blue-700 ring-blue-200",
  "Entregado": "bg-emerald-50 text-emerald-700 ring-emerald-200",
  "En Tránsito": "bg-blue-100 text-blue-700 ring-blue-200",
  "Incidencia": "bg-red-50 text-red-700 ring-red-200",
  "Pendiente": "bg-slate-50 text-slate-600 ring-slate-200",
  "Cancelado": "bg-slate-100 text-slate-500 ring-slate-300",
};

// Etiquetas legibles para los estados con guion bajo.
const LABELS: Partial<Record<ViajeEstado, string>> = {
  "Llegada_Origen": "Llegada a origen",
  "Llegada_Destino": "Llegada a destino",
  "En_Transito": "En tránsito",
  "En Tránsito": "En tránsito",
};

/**
 * Badge de estado usado como cellRenderer de la columna "Estado".
 * AG Grid React pasa `value` como prop (los params del renderer se spreadan).
 */
export function StatusBadge({ value }: { value: ViajeEstado }) {
  const clase = ESTILOS[value] ?? ESTILOS["Pendiente"];
  const label = LABELS[value] ?? value;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${clase}`}
    >
      {label}
    </span>
  );
}
