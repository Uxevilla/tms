import { AlertTriangle, Clock, CheckCircle2 } from "lucide-react";
import { differenceInCalendarDays, format, isValid, parseISO } from "date-fns";

interface Props {
  value: string | null | undefined;
}

/**
 * Semáforo de caducidad de documentación (Carnet / CAP / Reconocimiento Médico).
 * - Rojo (crítico/caducado): < 30 días o ya vencido.
 * - Ámbar (aviso): entre 30 y 90 días.
 * - Verde (válido): > 90 días.
 * - Sin fecha: gris neutro.
 */
export function CaducidadRenderer({ value }: Props) {
  if (!value) {
    return <span className="whitespace-nowrap text-xs text-slate-300">Sin fecha</span>;
  }
  const fecha = parseISO(value);
  if (!isValid(fecha)) {
    return <span className="whitespace-nowrap text-xs text-slate-500">{value}</span>;
  }
  const dias = differenceInCalendarDays(fecha, new Date());
  const texto = format(fecha, "dd/MM/yyyy");

  if (dias < 30) {
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold text-red-600">
        <AlertTriangle size={14} /> {texto}
      </span>
    );
  }
  if (dias <= 90) {
    return (
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium text-amber-600">
        <Clock size={14} /> {texto}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-emerald-600">
      <CheckCircle2 size={14} /> {texto}
    </span>
  );
}
