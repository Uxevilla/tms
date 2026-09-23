import { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef, CellValueChangedEvent } from "ag-grid-community";
import { Search, Eye, X, Download, RotateCcw } from "lucide-react";
import { REST_DOCUMENTOS } from "../config";
import { getToken } from "../auth";
import { useAgGridState } from "../hooks/useAgGridState";
import { VisorDocumentos } from "./VisorDocumentos";

import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";
ModuleRegistry.registerModules([AllCommunityModule]);
const editableCell = "cursor-text hover:bg-slate-100";

interface Documento {
  id: string;
  nombre: string;
  modulo_origen: string;
  referencia: string;
  formato: string;
  fecha: string;
  content_b64: string;
  source: string;
}

const COLOR_MODULO: Record<string, string> = {
  Operaciones: "bg-blue-100 text-blue-700",
  Gastos: "bg-amber-100 text-amber-700",
  "Vehículos": "bg-emerald-100 text-emerald-700",
  RRHH: "bg-purple-100 text-purple-700",
  Otros: "bg-slate-100 text-slate-600",
};

export function DocumentosDashboard() {
  const [docs, setDocs] = useState<Documento[]>([]);
  const [busqueda, setBusqueda] = useState("");
  const [visor, setVisor] = useState<Documento | null>(null);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const revertiendoRef = useRef(false);
  const { resetColumnState, exportToCsv, ...gridHandlers } = useAgGridState("tms_documentos_grid");

  const headers = () => ({ Authorization: `Bearer ${getToken() ?? ""}` });

  async function cargar() {
    try {
      const r = await fetch(REST_DOCUMENTOS, { headers: headers() });
      if (r.ok) setDocs((await r.json()).documentos ?? []);
    } catch (err) {
      console.error("Error cargando documentos:", err);
    }
  }

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  async function onCellValueChanged(e: CellValueChangedEvent<Documento>) {
    const field = e.colDef.field;
    if (field !== "nombre" || !e.data || revertiendoRef.current) return;
    try {
      const r = await fetch(`${REST_DOCUMENTOS}/${e.data.id}/renombrar`, {
        method: "PATCH",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ nombre: e.newValue }),
      });
      if (!r.ok) throw new Error(String(r.status));
    } catch {
      revertiendoRef.current = true;
      e.node.setDataValue(field, e.oldValue);
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: "No se pudo renombrar el archivo." });
    }
  }

  function descargar(d: Documento) {
    if (!d.content_b64) return;
    const dataUri = d.content_b64.startsWith("data:")
      ? d.content_b64
      : `data:application/pdf;base64,${d.content_b64}`;
    const a = document.createElement("a");
    a.href = dataUri;
    a.download = d.nombre;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  const columnDefs = useMemo<ColDef<Documento>[]>(
    () => [
      { field: "fecha", headerName: "Fecha", width: 110, sort: "desc" },
      { field: "nombre", headerName: "Archivo", flex: 1, minWidth: 200, editable: true, cellClass: editableCell },
      {
        field: "modulo_origen",
        headerName: "Módulo",
        width: 130,
        cellRenderer: (p: { value: string }) => (
          <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${COLOR_MODULO[p.value] || "bg-slate-100 text-slate-600"}`}>
            {p.value}
          </span>
        ),
      },
      { field: "referencia", headerName: "Referencia", width: 150 },
      { field: "formato", headerName: "Formato", width: 90 },
      {
        headerName: "Acción",
        width: 150,
        cellRenderer: (p: { data: Documento }) => (
          <div className="flex items-center gap-1">
            <button
              onClick={() => setVisor(p.data)}
              className="inline-flex items-center gap-1 rounded-md bg-blue-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-blue-700"
            >
              <Eye size={12} /> Ver
            </button>
            <button onClick={() => descargar(p.data)} title="Descargar" className="rounded-md bg-slate-100 p-1 text-slate-600 hover:bg-slate-200">
              <Download size={13} />
            </button>
          </div>
        ),
      },
    ],
    []
  );

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Documentos</h2>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={resetColumnState} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
            <RotateCcw size={16} /> Restaurar vista
          </button>
          <button onClick={() => exportToCsv("documentos.csv")} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
            <Download size={16} /> Exportar CSV
          </button>
        </div>
      </div>

      <div className="mb-2 shrink-0">
        <div className="relative">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={busqueda}
            onChange={(e) => setBusqueda(e.target.value)}
            placeholder="Buscar por matrícula, viaje, proveedor, tipo de documento..."
            className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm shadow-sm focus:border-blue-400 focus:outline-none"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <AgGridReact<Documento>
          theme={gridTheme}
          columnDefs={columnDefs}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          rowData={docs}
          rowHeight={GRID_ROW_HEIGHT}
          headerHeight={GRID_HEADER_HEIGHT}
          quickFilterText={busqueda}
          singleClickEdit
          stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged}
          sideBar={{ toolPanels: ["columns"] }}
          {...gridHandlers}
        />
      </div>

      {visor && (
        <>
          <div className="fixed inset-0 z-[2000] bg-slate-900/40 backdrop-blur-sm" onClick={() => setVisor(null)} />
          <div className="fixed inset-y-0 right-0 z-[2001] flex w-[55%] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">{visor.nombre}</h3>
              <button onClick={() => setVisor(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="min-h-0 flex-1 p-3">
              <VisorDocumentos base64={visor.content_b64} nombre={visor.nombre} className="h-full" />
            </div>
          </div>
        </>
      )}

      {banner && (
        <div className={`fixed bottom-4 right-4 z-[3000] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}
    </div>
  );
}
