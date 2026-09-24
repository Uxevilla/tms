import { useState } from "react";
import { SocketProvider, useSocketStatus } from "./context/SocketContext";
import { AppShell } from "./components/AppShell";
import { OperacionesDashboard } from "./components/OperacionesDashboard";
import { ContabilidadDashboard } from "./components/ContabilidadDashboard";
import { KpiDashboard } from "./components/KpiDashboard";
import { VehiculosDashboard } from "./components/VehiculosDashboard";
import { RrhhDashboard } from "./components/RrhhDashboard";
import { GastosDashboard } from "./components/GastosDashboard";
import { DocumentosDashboard } from "./components/DocumentosDashboard";
import { MensajeriaDashboard } from "./components/MensajeriaDashboard";
import { ConfiguracionDashboard } from "./components/ConfiguracionDashboard";
import { Login } from "./components/Login";
import { getToken, setToken, clearToken, isTokenValid } from "./auth";
import type { Seccion } from "./types";

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
  const [seccion, setSeccion] = useState<Seccion>("operaciones");
  const wsStatus = useSocketStatus();

  return (
    <AppShell
      seccion={seccion}
      onSeccion={setSeccion}
      wsStatus={wsStatus}
      onLogout={onLogout}
    >
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
    </AppShell>
  );
}
