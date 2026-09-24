import { lazy, Suspense, useEffect, useState } from "react";
import { SocketProvider, useSocketStatus } from "./context/SocketContext";
import { AppShell } from "./components/AppShell";
import { Login } from "./components/Login";
import { getToken, setToken, clearToken, isTokenValid } from "./auth";
import type { Seccion } from "./types";

// Carga diferida por sección: cada dashboard se descarga solo al abrirse,
// dividiendo el bundle inicial (antes ~1.7 MB en un solo chunk).
const OperacionesDashboard = lazy(() => import("./components/OperacionesDashboard").then((m) => ({ default: m.OperacionesDashboard })));
const ContabilidadDashboard = lazy(() => import("./components/ContabilidadDashboard").then((m) => ({ default: m.ContabilidadDashboard })));
const KpiDashboard = lazy(() => import("./components/KpiDashboard").then((m) => ({ default: m.KpiDashboard })));
const VehiculosDashboard = lazy(() => import("./components/VehiculosDashboard").then((m) => ({ default: m.VehiculosDashboard })));
const RrhhDashboard = lazy(() => import("./components/RrhhDashboard").then((m) => ({ default: m.RrhhDashboard })));
const GastosDashboard = lazy(() => import("./components/GastosDashboard").then((m) => ({ default: m.GastosDashboard })));
const DocumentosDashboard = lazy(() => import("./components/DocumentosDashboard").then((m) => ({ default: m.DocumentosDashboard })));
const MensajeriaDashboard = lazy(() => import("./components/MensajeriaDashboard").then((m) => ({ default: m.MensajeriaDashboard })));
const ConfiguracionDashboard = lazy(() => import("./components/ConfiguracionDashboard").then((m) => ({ default: m.ConfiguracionDashboard })));

const TITULOS: Record<Seccion, string> = {
  operaciones: "Operaciones",
  vehiculos: "Vehículos",
  rrhh: "RRHH",
  contabilidad: "Contabilidad",
  gastos: "Gastos",
  documentos: "Documentos",
  kpi: "KPIs",
  mensajeria: "Mensajería",
  configuracion: "Configuración",
};

export default function App() {
  const [token, setTokenState] = useState<string | null>(() => {
    const t = getToken();
    return isTokenValid(t) ? t : null;
  });

  const handleLogin = (t: string) => {
    setToken(t);
    setTokenState(t);
  };

  const handleLogout = () => {
    clearToken();
    setTokenState(null);
  };

  // Sesión caducada en caliente (token expirado a mitad de uso): el WS cierra con
  // código 1008 o una llamada fetch recibe 401 → se dispara tms:sesion-caducada.
  useEffect(() => {
    const onSesionCaducada = () => {
      clearToken();
      setTokenState(null);
    };
    window.addEventListener("tms:sesion-caducada", onSesionCaducada);
    return () => window.removeEventListener("tms:sesion-caducada", onSesionCaducada);
  }, []);

  if (!token) {
    return <Login onLogin={handleLogin} />;
  }

  return (
    <SocketProvider token={token}>
      <AppInner onLogout={handleLogout} />
    </SocketProvider>
  );
}

function AppInner({ onLogout }: { onLogout: () => void }) {
  const [seccion, setSeccion] = useState<Seccion>(() => {
    const h = location.hash.replace("#", "");
    return (h in TITULOS ? h : "operaciones") as Seccion;
  });
  const wsStatus = useSocketStatus();

  // Sección → hash: permite enlaces directos (#contabilidad) y botón atrás.
  useEffect(() => {
    if (location.hash !== `#${seccion}`) location.hash = seccion;
  }, [seccion]);

  // hash → sección: botón atrás/adelante del navegador.
  useEffect(() => {
    const onHash = () => {
      const h = location.hash.replace("#", "");
      if (h in TITULOS) setSeccion(h as Seccion);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <AppShell
      seccion={seccion}
      onSeccion={setSeccion}
      wsStatus={wsStatus}
      onLogout={onLogout}
    >
      <Suspense fallback={<Cargando />}>
        {seccion === "operaciones" ? (
          <OperacionesDashboard />
        ) : seccion === "contabilidad" ? (
          <ContabilidadDashboard />
        ) : seccion === "kpi" ? (
          <KpiDashboard />
        ) : seccion === "vehiculos" ? (
          <VehiculosDashboard />
        ) : seccion === "rrhh" ? (
          <RrhhDashboard />
        ) : seccion === "gastos" ? (
          <GastosDashboard />
        ) : seccion === "documentos" ? (
          <DocumentosDashboard />
        ) : seccion === "mensajeria" ? (
          <MensajeriaDashboard />
        ) : seccion === "configuracion" ? (
          <ConfiguracionDashboard />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            {TITULOS[seccion]} — pendiente de implementar
          </div>
        )}
      </Suspense>
    </AppShell>
  );
}

function Cargando() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-slate-400">
      Cargando…
    </div>
  );
}
