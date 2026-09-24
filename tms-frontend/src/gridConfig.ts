// Configuración global de AG Grid — ultra-alta densidad (estilo desktop legacy).
// Todos los grids deben importar `gridTheme` y las constantes de altura desde aquí
// para mantener una densidad uniforme en toda la app.
import { themeQuartz } from "ag-grid-community";

// Tema ultra-denso: tipografía 11px (text-xs), spacing mínimo.
export const gridTheme = themeQuartz.withParams({
  spacing: 2,
  fontSize: 11,
  fontFamily: "inherit",
});

// Alturas mínimas aceptables para máxima información en pantalla
// (emulan la densidad de una app de escritorio nativa de los 2000).
export const GRID_ROW_HEIGHT = 26;
export const GRID_HEADER_HEIGHT = 26;
