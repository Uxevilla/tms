import { test, expect, type Page } from "@playwright/test";

// Smoke test (Fase 1): recorrido por todas las secciones visibles del sidebar,
// 0 errores de consola y 0 respuestas HTTP >= 400; recargar conserva la ruta y
// "Atrás" funciona. El login se hace UNA vez por rol en global-setup.ts.
// Credenciales por variable de entorno, nunca hardcodeadas.

const SECCIONES: Record<string, string[]> = {
  admin: [
    "Torre de control", "Planificación", "Viajes", "Mensajes",
    "Vehículos", "Conductores", "Taller",
    "Facturación", "Gastos", "Contabilidad", "KPIs",
    "RRHH", "Documentos", "Configuración",
  ],
  dispatcher: [
    "Torre de control", "Planificación", "Viajes", "Mensajes",
    "Vehículos", "Conductores", "Taller",
    "Gastos", "KPIs", "Documentos",
  ],
};

// Mosaicos de mapa externos: se simulan para no depender de la red externa.
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
  await page.getByRole("link", { name: "Torre de control", exact: true }).waitFor({ timeout: 20_000 });

  for (const seccion of SECCIONES[rol]) {
    await page.getByRole("link", { name: seccion, exact: true }).click();
    await page.waitForTimeout(500);
  }

  expect(erroresConsola, "errores de consola").toEqual([]);
  expect(respuestas400, "respuestas HTTP >= 400").toEqual([]);
});

test("recargar conserva la ruta y Atrás funciona", async ({ page }) => {
  await page.goto("/vehiculos");
  await page.getByRole("link", { name: "Vehículos", exact: true }).waitFor({ timeout: 20_000 });

  // Recargar conserva la ruta (sin hash).
  await page.reload();
  await page.waitForTimeout(800);
  expect(new URL(page.url()).pathname).toBe("/vehiculos");

  // Navegar a Gastos y volver con "Atrás".
  await page.getByRole("link", { name: "Gastos", exact: true }).click();
  await page.waitForTimeout(500);
  expect(new URL(page.url()).pathname).toBe("/gastos");
  await page.goBack();
  await page.waitForTimeout(500);
  expect(new URL(page.url()).pathname).toBe("/vehiculos");
});
