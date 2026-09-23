import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  titulo: string;
  children: ReactNode;
  /** Clase de ancho máximo (Tailwind), p. ej. "max-w-lg", "max-w-2xl". */
  ancho?: string;
  /** Barra inferior opcional (botones de acción). */
  footer?: ReactNode;
}

/**
 * Modal centrado (base del flujo basado en Modales). Sustituye a los Drawers
 * laterales: el usuario nunca pierde de vista la tabla principal.
 *
 * Uso:
 *   <Modal open={!!viaje} onClose={() => setViaje(null)} titulo="Editar viaje">
 *     ...formulario...
 *   </Modal>
 */
export function Modal({ open, onClose, titulo, children, ancho = "max-w-2xl", footer }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-[2200] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={titulo}
        className={`relative flex max-h-[90vh] w-full ${ancho} flex-col overflow-hidden rounded-lg bg-white text-slate-900 shadow-2xl ring-1 ring-slate-900/10`}
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-4">
          <h2 className="text-sm font-bold tracking-tight text-slate-800">{titulo}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"
            aria-label="Cerrar"
          >
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        {footer && <div className="shrink-0 border-t border-slate-200 bg-slate-50 px-4 py-3">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
