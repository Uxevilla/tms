import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/inter";
import "leaflet/dist/leaflet.css";
import "./index.css";

import { router } from "./router";
import { queryClient } from "./queryClient";
import { ThemeProvider } from "./theme";

// Pantalla en blanco tras un despliegue: quien tenga la app abierta pedirá un
// chunk que ya no existe → recargar en vez de desmontar toda la app.
window.addEventListener("vite:preloadError", () => window.location.reload());

// Redirección de los hash antiguos (#vehiculos → /vehiculos) para no romper marcadores.
const HASH_A_RUTA: Record<string, string> = {
  operaciones: "/viajes",
  vehiculos: "/vehiculos",
  rrhh: "/rrhh",
  contabilidad: "/contabilidad",
  gastos: "/gastos",
  documentos: "/documentos",
  kpi: "/kpis",
  mensajeria: "/mensajes",
  configuracion: "/configuracion",
};
const hashAntiguo = window.location.hash.replace("#", "");
if (hashAntiguo in HASH_A_RUTA) {
  window.history.replaceState(null, "", HASH_A_RUTA[hashAntiguo] + window.location.search);
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
