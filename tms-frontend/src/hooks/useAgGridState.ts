import { useCallback, useRef } from "react";
import type { GridApi, GridReadyEvent } from "ag-grid-community";

/**
 * Persistencia de la vista de un AG Grid (orden, visibilidad, ancho y ordenación
 * de columnas) en localStorage, con restauración al montar.
 *
 * Uso:
 *   const { resetColumnState, exportToCsv, ...gridHandlers } = useAgGridState("tms_empleados_grid");
 *   <AgGridReact sideBar={{ toolPanels: ["columns"] }} {...gridHandlers} />
 */
export interface AgGridStateHandlers {
  onGridReady: (e: GridReadyEvent<any>) => void;
  onColumnMoved: (e: { api: GridApi }) => void;
  onColumnVisible: (e: { api: GridApi }) => void;
  onColumnResized: (e: { api: GridApi }) => void;
  onSortChanged: (e: { api: GridApi }) => void;
  resetColumnState: () => void;
  exportToCsv: (fileName?: string) => void;
}

export function useAgGridState(gridKey: string): AgGridStateHandlers {
  const gridApiRef = useRef<GridApi | null>(null);

  const guardar = useCallback(
    (api: GridApi) => {
      try {
        localStorage.setItem(gridKey, JSON.stringify(api.getColumnState()));
      } catch {
        /* almacenamiento no disponible */
      }
    },
    [gridKey]
  );

  const onGridReady = useCallback(
    (e: GridReadyEvent<any>) => {
      gridApiRef.current = e.api;
      try {
        const saved = localStorage.getItem(gridKey);
        if (saved) {
          e.api.applyColumnState({ state: JSON.parse(saved), applyOrder: true });
        }
      } catch {
        /* estado inválido: se ignora */
      }
    },
    [gridKey]
  );

  const onColumnChange = useCallback(
    (e: { api: GridApi }) => guardar(e.api),
    [guardar]
  );

  const resetColumnState = useCallback(() => {
    try {
      localStorage.removeItem(gridKey);
    } catch {
      /* noop */
    }
    gridApiRef.current?.resetColumnState();
  }, [gridKey]);

  const exportToCsv = useCallback(
    (fileName?: string) => {
      gridApiRef.current?.exportDataAsCsv({ fileName: fileName || "tms_export.csv" });
    },
    []
  );

  return {
    onGridReady,
    onColumnMoved: onColumnChange,
    onColumnVisible: onColumnChange,
    onColumnResized: onColumnChange,
    onSortChanged: onColumnChange,
    resetColumnState,
    exportToCsv,
  };
}
