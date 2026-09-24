import { useEffect, useRef } from "react";

// Icono ↗️ que abre el panel. El listener nativo detiene la propagación para que
// el clic NO llegue a AG Grid (singleClickEdit) y abra el editor; el AppShell
// abre el panel en la fase de captura (antes de este stopPropagation).
function IconoPanel({ enlace }: { enlace: string }) {
  const ref = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const stop = (e: MouseEvent) => e.stopPropagation();
    el.addEventListener("click", stop);
    return () => el.removeEventListener("click", stop);
  }, []);
  return (
    <span
      ref={ref}
      data-panel={enlace}
      className="inline-flex shrink-0 cursor-pointer px-0.5 text-primary opacity-70 transition hover:opacity-100"
      title="Abrir panel"
    >
      ↗
    </span>
  );
}

// CellRenderer genérico para enlazar celdas de las tablas antiguas al panel de entidad.
// Dos modos:
//   - texto (default): toda la celda es el enlace [data-panel="tipo:id"].
//   - icono (opts.icono): el valor queda libre (para editar con doble clic) y se añade
//     un ↗️ al lado con el enlace; el texto NO abre el panel.
export function panelCell<T = Record<string, unknown>>(
  tipo: string,
  getId?: (data: T) => string | number | undefined,
  opts?: { icono?: boolean },
) {
  return (params: { value?: unknown; data?: T }) => {
    const val = params.value;
    const id = getId ? getId(params.data as T) : val;
    const txt = val == null || val === "" ? "" : String(val);
    if (id == null || id === "") return <span>{txt}</span>;

    if (opts?.icono) {
      return (
        <span className="flex items-center justify-between gap-1">
          <span data-celda-valor className="min-w-0 truncate">{txt}</span>
          <IconoPanel enlace={`${tipo}:${id}`} />
        </span>
      );
    }

    return (
      <span data-panel={`${tipo}:${id}`} className="cursor-pointer text-primary hover:underline">
        {txt}
      </span>
    );
  };
}
