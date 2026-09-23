import { Download, Maximize } from "lucide-react";

/**
 * Visor de PDF reutilizable. Recibe el contenido en Base64 (con o sin el prefijo
 * `data:application/pdf;base64,`) y lo renderiza en un <object> con una barra de
 * herramientas minimalista (Descargar / Expandir).
 */
export function VisorDocumentos({
  base64,
  nombre = "documento.pdf",
  className = "",
}: {
  base64: string;
  nombre?: string;
  className?: string;
}) {
  const dataUri = base64
    ? base64.startsWith("data:")
      ? base64
      : `data:application/pdf;base64,${base64}`
    : "";

  function descargar() {
    const a = document.createElement("a");
    a.href = dataUri;
    a.download = nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function expandir() {
    window.open(dataUri, "_blank", "noopener");
  }

  if (!base64) {
    return (
      <div
        className={`flex items-center justify-center rounded-lg border border-dashed border-slate-300 bg-slate-50 text-sm text-slate-400 ${className}`}
      >
        Sin documento
      </div>
    );
  }

  return (
    <div className={`flex flex-col overflow-hidden rounded-lg border border-slate-200 bg-white ${className}`}>
      <div className="flex shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-3 py-1.5">
        <span className="truncate text-xs font-medium text-slate-600">{nombre}</span>
        <div className="flex items-center gap-1">
          <button
            onClick={descargar}
            title="Descargar"
            className="rounded p-1 text-slate-500 hover:bg-slate-200 hover:text-slate-700"
          >
            <Download size={16} />
          </button>
          <button
            onClick={expandir}
            title="Expandir"
            className="rounded p-1 text-slate-500 hover:bg-slate-200 hover:text-slate-700"
          >
            <Maximize size={16} />
          </button>
        </div>
      </div>
      <object data={dataUri} type="application/pdf" className="min-h-0 w-full flex-1 bg-slate-100">
        <p className="p-4 text-sm text-slate-500">
          Tu navegador no puede mostrar el PDF.{" "}
          <a href={dataUri} target="_blank" rel="noreferrer" className="text-blue-600 underline">
            Abrir en otra pestaña
          </a>
          .
        </p>
      </object>
    </div>
  );
}
