import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "leaflet/dist/leaflet.css";
import "./index.css";

// Pantalla en blanco tras un despliegue: quien tenga la app abierta pedirá un
// chunk que ya no existe → recargar en vez de desmontar toda la app.
window.addEventListener("vite:preloadError", () => window.location.reload());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
