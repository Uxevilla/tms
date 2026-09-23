import { useEffect, useMemo, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef } from "ag-grid-community";
import { BarChart3, LineChart } from "lucide-react";
import { GRAFANA_URL, REST_RENTABILIDAD } from "../config";
import { getToken } from "../auth";

import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";
ModuleRegistry.registerModules([AllCommunityModule]);

const eur = (v: number) => v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

interface RentabilidadRow {
  vehiculo_id: string;
  matricula: string;
  total_ingresos: number;
  total_gastos: number;
  margen_neto: number;
  margen_porcentaje: number | null;
}

const colorMargen = (v: number | null) =>
  v == null ? "#94a3b8" : v > 15 ? "#059669" : v >= 5 ? "#d97706" : "#dc2626";

/** Barra de progreso in-line + porcentaje, coloreada por umbral de rentabilidad. */
function MargenBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-xs text-slate-300">—</span>;
  const color = colorMargen(value);
  const width = Math.min(100, Math.max(0, value));
  return (
    <div className="flex w-full items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
        <div className="h-full rounded-full transition-all" style={{ width: `${width}%`, backgroundColor: color }} />
      </div>
      <span className="w-14 text-right text-xs font-semibold tabular-nums" style={{ color }}>
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

export function KpiDashboard() {
  const [tab, setTab] = useState<"rentabilidad" | "grafana">("rentabilidad");
  const [data, setData] = useState<RentabilidadRow[]>([]);
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [rango, setRango] = useState<{ desde: string; hasta: string }>({ desde: "", hasta: "" });

  async function cargar(d = "", h = "") {
    try {
      const q = new URLSearchParams();
      if (d) q.set("desde", d);
      if (h) q.set("hasta", h);
      const qs = q.toString() ? `?${q.toString()}` : "";
      const r = await fetch(`${REST_RENTABILIDAD}${qs}`, { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
      const j = await r.json();
      setData(j.flota ?? []);
      setRango({ desde: j.desde ?? "", hasta: j.hasta ?? "" });
    } catch (e) {
      console.error("Error rentabilidad:", e);
    }
  }

  useEffect(() => {
    cargar();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const columnDefs = useMemo<ColDef<RentabilidadRow>[]>(
    () => [
      { field: "matricula", headerName: "Vehículo", width: 130, pinned: "left" },
      { field: "vehiculo_id", headerName: "ID Trimble", width: 150 },
      { field: "total_ingresos", headerName: "Ingresos", width: 130, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "total_gastos", headerName: "Gastos", width: 130, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      {
        field: "margen_neto",
        headerName: "Margen neto",
        width: 130,
        type: "rightAligned",
        valueFormatter: (p) => eur(Number(p.value) || 0),
        cellStyle: (p) => (Number(p.value) < 0 ? { color: "#b91c1c", fontWeight: 600 } : { color: "#047857", fontWeight: 600 }),
      },
      {
        field: "margen_porcentaje",
        headerName: "Margen %",
        width: 220,
        cellRenderer: (p: { value: number | null }) => <MargenBar value={p.value} />,
        cellStyle: (p) => {
          const v = p.value as number | null;
          if (v == null) return {};
          if (v > 15) return { backgroundColor: "#ecfdf5" };
          if (v >= 5) return { backgroundColor: "#fffbeb" };
          return { backgroundColor: "#fef2f2" };
        },
      },
    ],
    []
  );

  const pinnedBottom = useMemo(() => {
    const ing = data.reduce((s, r) => s + (r.total_ingresos || 0), 0);
    const gas = data.reduce((s, r) => s + (r.total_gastos || 0), 0);
    const margen = ing - gas;
    const pct = ing > 0 ? (margen / ing) * 100 : null;
    return [
      {
        vehiculo_id: "TOTAL FLOTA",
        matricula: "Flota",
        total_ingresos: ing,
        total_gastos: gas,
        margen_neto: margen,
        margen_porcentaje: pct,
      } as RentabilidadRow,
    ];
  }, [data]);

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          <button onClick={() => setTab("rentabilidad")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "rentabilidad" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <BarChart3 size={16} /> Rentabilidad Flota
          </button>
          <button onClick={() => setTab("grafana")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "grafana" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <LineChart size={16} /> Grafana
          </button>
        </div>
        {tab === "rentabilidad" && (
          <div className="ml-auto flex items-center gap-2 text-sm text-slate-500">
            <input type="date" value={desde} onChange={(e) => setDesde(e.target.value)} className="rounded-md border border-slate-200 px-2 py-1 text-xs" />
            <span>→</span>
            <input type="date" value={hasta} onChange={(e) => setHasta(e.target.value)} className="rounded-md border border-slate-200 px-2 py-1 text-xs" />
            <button onClick={() => cargar(desde, hasta)} className="rounded-md bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200">
              Aplicar
            </button>
          </div>
        )}
      </div>

      {tab === "grafana" ? (
        <iframe src={GRAFANA_URL} title="KPIs y dashboards (Grafana)" className="h-full w-full rounded-xl border border-slate-200 bg-white" allowFullScreen />
      ) : (
        <>
          {rango.desde && (
            <div className="mb-1 text-xs text-slate-400">
              Periodo: {rango.desde} → {rango.hasta}
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
            <AgGridReact<RentabilidadRow>
              theme={gridTheme}
              columnDefs={columnDefs}
              defaultColDef={{ sortable: true, resizable: true }}
              rowData={data}
              pinnedBottomRowData={pinnedBottom}
              getRowStyle={(p) =>
                p.node.rowPinned === "bottom"
                  ? { fontWeight: 700, borderTop: "2px solid #e2e8f0", backgroundColor: "#f8fafc" }
                  : undefined
              }
              rowHeight={GRID_ROW_HEIGHT}
              headerHeight={GRID_HEADER_HEIGHT}
            />
          </div>
        </>
      )}
    </div>
  );
}
