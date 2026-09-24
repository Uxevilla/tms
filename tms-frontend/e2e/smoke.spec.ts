import { test, expect, type Page } from "@playwright/test";

// Smoke test de Fase 0: recorrido por todas las secciones visibles por rol,
// 0 errores de consola y 0 respuestas HTTP >= 400.
// El login se hace UNA sola vez por rol en global-setup.ts (storageState),
// para no chocar con el límite de 8 logins/5 min del backend.
// Credenciales por variable de entorno (nunca hardcodeadas): E2E_ADMIN_USER,
// E2E_ADMIN_PASSWORD, E2E_DISPATCHER_USER, E2E_DISPATCHER_PASSWORD.

// Etiquetas visibles del ribbon por rol (antes del rediseño "Torre de control").
const SECCIONES: Record<string, string[]> = {
  admin: [
    "Operaciones", "Vehículos", "RRHH", "Contabilidad", "Gastos",
    "Documentos", "KPIs", "Mensajería", "Configuración",
  ],
  dispatcher: ["Operaciones", "Vehículos", "Gastos", "Documentos", "KPIs", "Mensajería"],
};

// Mosaicos de mapa externos (única excepción de recursos de terceros): se simulan
// con page.route para que el test no dependa de la red externa.
const TILE_HOSTS = ["tile.openstreetmap.org", "server.arcgisonline.com"];
const TILE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"></svg>';

function track(page: Page) {
  const erroresConsola: string[] = [];
  const respuestas400: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") erroresConsola.push(m.text());
  });
  page.on("response", (r) => {
    if (r.status() >= 400) respuestas400.push(`${r.status()} ${r.url()}`);
  });
  return { erroresConsola, respuestas400 };
}

test.beforeEach(async ({ page }) => {
  for (const host of TILE_HOSTS) {
    await page.route(`**${host}/**`, (route) =>
      route.fulfill({ status: 200, contentType: "image/svg+xml", body: TILE_SVG }),
    );
  }
});

test("recorrido sin errores", async ({ page }, testInfo) => {
  const rol = testInfo.project.name;
  const { erroresConsola, respuestas400 } = track(page);

  await page.goto("/");
  await page.getByRole("button", { name: "Operaciones", exact: true }).waitFor({ timeout: 20_000 });

  for (const seccion of SECCIONES[rol]) {
    await page.getByRole("button", { name: seccion, exact: true }).click();
    await page.waitForTimeout(600);
  }

  expect(erroresConsola, "errores de consola").toEqual([]);
  expect(respuestas400, "respuestas HTTP >= 400").toEqual([]);
});
