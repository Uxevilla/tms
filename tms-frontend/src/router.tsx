import { lazy, useEffect } from "react";
import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useNavigate,
  useParams,
  useSearch,
} from "@tanstack/react-router";

import { AppShell } from "./components/AppShell";
import { Login } from "./components/Login";
import { setToken, getToken, getRol, isTokenValid } from "./auth";

// Carga diferida por sección: cada dashboard antiguo se descarga solo al abrirse.
const ViajesDashboard = lazy(() => import("./components/ViajesDashboard").then((m) => ({ default: m.ViajesDashboard })));
const ContabilidadDashboard = lazy(() => import("./components/ContabilidadDashboard").then((m) => ({ default: m.ContabilidadDashboard })));
const KpiDashboard = lazy(() => import("./components/KpiDashboard").then((m) => ({ default: m.KpiDashboard })));
const VehiculosDashboard = lazy(() => import("./components/VehiculosDashboard").then((m) => ({ default: m.VehiculosDashboard })));
const VehiculosPage = lazy(() => import("./components/VehiculosPage").then((m) => ({ default: m.VehiculosPage })));
const ConductoresPage = lazy(() => import("./components/ConductoresPage").then((m) => ({ default: m.ConductoresPage })));
const RrhhDashboard = lazy(() => import("./components/RrhhDashboard").then((m) => ({ default: m.RrhhDashboard })));
const GastosDashboard = lazy(() => import("./components/GastosDashboard").then((m) => ({ default: m.GastosDashboard })));
const DocumentosDashboard = lazy(() => import("./components/DocumentosDashboard").then((m) => ({ default: m.DocumentosDashboard })));
const MensajeriaDashboard = lazy(() => import("./components/MensajeriaDashboard").then((m) => ({ default: m.MensajeriaDashboard })));
const ConfiguracionDashboard = lazy(() => import("./components/ConfiguracionDashboard").then((m) => ({ default: m.ConfiguracionDashboard })));
const TorreDashboard = lazy(() => import("./components/TorreDashboard").then((m) => ({ default: m.TorreDashboard })));
const PlanificacionDashboard = lazy(() => import("./components/PlanificacionDashboard").then((m) => ({ default: m.PlanificacionDashboard })));
const TallerPage = lazy(() => import("./components/TallerPage").then((m) => ({ default: m.TallerPage })));
import { NotFound } from "./components/NotFound";

function Placeholder({ titulo, fase }: { titulo: string; fase: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <div className="text-lg font-semibold">{titulo}</div>
      <div className="text-sm">En construcción — {fase}</div>
    </div>
  );
}

// /viajes/$codigo → abre el panel de entidad del viaje en la lista (no placeholder).
function ViajeDetalleRedirect() {
  const { codigo } = useParams({ from: "/app/viajes/$codigo" });
  const navigate = useNavigate();
  useEffect(() => {
    navigate({ to: "/viajes", search: { panel: `viaje:${codigo}` }, replace: true });
  }, [codigo, navigate]);
  return null;
}

// Guards
function authGuard({ location }: { location: { pathname: string; searchStr: string } }) {
  if (!isTokenValid(getToken())) {
    throw redirect({ to: "/login", search: { redirect: location.pathname + location.searchStr } });
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

// /login (público). Acepta ?redirect= solo si es una ruta relativa (empieza por "/" y no por "//").
function validateLoginSearch(search: Record<string, unknown>): { redirect?: string } {
  const redirect = search.redirect;
  if (typeof redirect === "string" && redirect.startsWith("/") && !redirect.startsWith("//")) {
    return { redirect };
  }
  return {};
}

function LoginPage() {
  const navigate = useNavigate();
  const search = useSearch({ from: "/login" });
  return (
    <Login
      onLogin={(token) => {
        setToken(token);
        navigate({ to: (search.redirect ?? "/torre") as never });
      }}
    />
  );
}

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginPage,
  validateSearch: validateLoginSearch,
});

// Layout autenticado (pathless)
function validateAppSearch(search: Record<string, unknown>): { panel?: string } {
  const panel = search.panel;
  if (typeof panel === "string" && /^(vehiculo|viaje|conductor):.+$/.test(panel)) {
    return { panel };
  }
  return {};
}

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  component: AppShell,
  beforeLoad: authGuard,
  validateSearch: validateAppSearch,
});

// Secciones
const torreRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/torre",
  component: () => <TorreDashboard />,
});
const planificacionRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/planificacion",
  component: () => <PlanificacionDashboard />,
  validateSearch: (search: Record<string, unknown>): { panel?: string; viaje?: string } => ({
    panel: typeof search.panel === "string" && /^(vehiculo|viaje|conductor):.+$/.test(search.panel) ? search.panel : undefined,
    viaje: typeof search.viaje === "string" && search.viaje ? search.viaje : undefined,
  }),
});
// Filtros de /viajes en la URL (se comparten y sobreviven a recargar).
function validateViajesSearch(search: Record<string, unknown>): { estado?: string; cliente?: string; vehiculo?: string; desde?: string; hasta?: string } {
  const s = (v: unknown) => (typeof v === "string" && v ? v : undefined);
  return {
    estado: s(search.estado),
    cliente: s(search.cliente),
    vehiculo: s(search.vehiculo),
    desde: s(search.desde),
    hasta: s(search.hasta),
  };
}

const viajesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/viajes",
  component: () => <ViajesDashboard />,
  validateSearch: validateViajesSearch,
});
const viajeDetalleRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/viajes/$codigo",
  component: () => <ViajeDetalleRedirect />,
});
const mensajesRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/mensajes",
  component: () => <MensajeriaDashboard />,
});
const vehiculosRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/vehiculos",
  component: () => <VehiculosPage />,
});
const conductoresRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/conductores",
  component: () => <ConductoresPage />,
});
const tallerRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/taller",
  component: () => <TallerPage />,
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

export const router = createRouter({
  routeTree,
  // strict: los search params no devueltos por validateSearch se DESCARTAN
  // (si no, un ?redirect=//evil.com rechazado se conservaría como "desconocido").
  search: { strict: true },
  // 404 propia para rutas que no coinciden con ninguna de las del árbol.
  defaultNotFoundComponent: () => <NotFound />,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
