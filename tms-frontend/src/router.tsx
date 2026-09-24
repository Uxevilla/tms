import { lazy } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useNavigate,
} from "@tanstack/react-router";

import { AppShell } from "./components/AppShell";
import { Login } from "./components/Login";
import { setToken, getToken, getRol, isTokenValid } from "./auth";

// Carga diferida por sección: cada dashboard antiguo se descarga solo al abrirse.
const OperacionesDashboard = lazy(() => import("./components/OperacionesDashboard").then((m) => ({ default: m.OperacionesDashboard })));
const ContabilidadDashboard = lazy(() => import("./components/ContabilidadDashboard").then((m) => ({ default: m.ContabilidadDashboard })));
const KpiDashboard = lazy(() => import("./components/KpiDashboard").then((m) => ({ default: m.KpiDashboard })));
const VehiculosDashboard = lazy(() => import("./components/VehiculosDashboard").then((m) => ({ default: m.VehiculosDashboard })));
const RrhhDashboard = lazy(() => import("./components/RrhhDashboard").then((m) => ({ default: m.RrhhDashboard })));
const GastosDashboard = lazy(() => import("./components/GastosDashboard").then((m) => ({ default: m.GastosDashboard })));
const DocumentosDashboard = lazy(() => import("./components/DocumentosDashboard").then((m) => ({ default: m.DocumentosDashboard })));
const MensajeriaDashboard = lazy(() => import("./components/MensajeriaDashboard").then((m) => ({ default: m.MensajeriaDashboard })));
const ConfiguracionDashboard = lazy(() => import("./components/ConfiguracionDashboard").then((m) => ({ default: m.ConfiguracionDashboard })));

function Placeholder({ titulo, fase }: { titulo: string; fase: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <div className="text-lg font-semibold">{titulo}</div>
      <div className="text-sm">En construcción — {fase}</div>
    </div>
  );
}

// Guards
function authGuard() {
  if (!isTokenValid(getToken())) {
    throw redirect({ to: "/login" });
  }
}

function adminGuard() {
  if (getRol() !== "admin") {
    sessionStorage.setItem("tms_aviso", "no-permiso");
    throw redirect({ to: "/torre" });
  }
}

// Root
const rootRoute = createRootRoute({ component: () => <Outlet /> });

// / → /torre o /login
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: isTokenValid(getToken()) ? "/torre" : "/login" });
  },
});

// /login (público)
function LoginPage() {
  const navigate = useNavigate();
  return (
    <Login
      onLogin={(token) => {
        setToken(token);
        navigate({ to: "/torre" });
      }}
    />
  );
}

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
});

// Layout autenticado (pathless)
const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  component: AppShell,
  beforeLoad: authGuard,
});

// Secciones
const torreRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/torre",
  component: () => <Placeholder titulo="Torre de control" fase="Fase 2" />,
});
const planificacionRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/planificacion",
  component: () => <Placeholder titulo="Planificación" fase="Fase 3" />,
});
const viajesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/viajes",
  component: () => <OperacionesDashboard />,
});
const viajeDetalleRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/viajes/$codigo",
  component: () => <Placeholder titulo="Detalle de viaje" fase="Fase 4" />,
});
const mensajesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/mensajes",
  component: () => <MensajeriaDashboard />,
});
const vehiculosRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/vehiculos",
  component: () => <VehiculosDashboard />,
});
const conductoresRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/conductores",
  component: () => <Placeholder titulo="Conductores" fase="Fase 5" />,
});
const tallerRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/taller",
  component: () => <Placeholder titulo="Taller" fase="Fase 5" />,
});
const facturacionRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/facturacion",
  beforeLoad: adminGuard,
  component: () => <Placeholder titulo="Facturación" fase="Fase 6" />,
});
const gastosRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/gastos",
  component: () => <GastosDashboard />,
});
const contabilidadRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/contabilidad",
  beforeLoad: adminGuard,
  component: () => <ContabilidadDashboard />,
});
const rrhhRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/rrhh",
  beforeLoad: adminGuard,
  component: () => <RrhhDashboard />,
});
const documentosRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/documentos",
  component: () => <DocumentosDashboard />,
});
const configuracionRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/configuracion",
  beforeLoad: adminGuard,
  component: () => <ConfiguracionDashboard />,
});
const kpisRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/kpis",
  component: () => <KpiDashboard />,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  loginRoute,
  appRoute.addChildren([
    torreRoute,
    planificacionRoute,
    viajesRoute,
    viajeDetalleRoute,
    mensajesRoute,
    vehiculosRoute,
    conductoresRoute,
    tallerRoute,
    facturacionRoute,
    gastosRoute,
    contabilidadRoute,
    rrhhRoute,
    documentosRoute,
    configuracionRoute,
    kpisRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
