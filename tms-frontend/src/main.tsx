import React from "react";
import ReactDOM from "react-dom/client";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import App from "./App";
import "leaflet/dist/leaflet.css";
import "./index.css";

// Registro ÚNICO de módulos de AG Grid (antes repetido en 8 componentes).
ModuleRegistry.registerModules([AllCommunityModule]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
