import { test, expect, type Page } from "@playwright/test";

// E2E Fase 2 (revisión): panel desde Vehículos, bandeja con las 8 clases (con seed),
// y tiempo real (WS sin errores en /torre). Mosaicos de mapa simulados.

const TILE_HOSTS = ["tile.openstreetmap.org", "server.arcgisonline.com"];
const TILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

test.beforeEach(async ({ page }) => {
  for (const host of TILE_HOSTS) {
    await page.route(`**${host}/**`, (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: TILE_PNG }),
    );
  }
});

test("clic en matrícula de Vehículos abre el panel", async ({ page }) => {
  await page.goto("/vehiculos");
  const celda = page.locator('[data-panel^="vehiculo:"]').first();
  await celda.waitFor({ timeout: 20_000 });
  await celda.click();
  await expect(page).toHaveURL(/panel=/, { timeout: 10_000 });
});

test("bandeja muestra las 8 clases (datos de prueba)", async ({ page }, testInfo) => {
  await page.goto("/torre");
  // viaje_sin_facturar es solo admin (se filtra en el servidor).
  const soloAdmin = testInfo.project.name === "admin" ? ["Viaje E2E-FACT sin facturar"] : [];
  const titulos = [
    "Viaje E2E-TRIP-1 retrasado",
    "Conducción al límite",
    "ITV de 0001-TST caducada",
    "Gasto sin imputar: gasto suelto e2e",
    "Mensaje sin responder",
    "Mantenimiento vencido: aceite",
    "Envío a Trimble fallido: E2E-ERR",
    "Carné de Conductor Prueba caducado",
    ...soloAdmin,
  ];
  for (const t of titulos) {
    await expect(page.getByText(t, { exact: false }).first()).toBeVisible({ timeout: 20_000 });
  }
});

test("clic en aviso de conducción límite abre el panel con datos", async ({ page }) => {
  await page.goto("/torre");
  await page.getByText("Conducción al límite", { exact: false }).first().click();
  // El panel debe mostrar datos del conductor (pestaña Datos), no un error.
  await expect(page.getByText("Error al cargar.")).toHaveCount(0);
  await expect(page.getByText("Nombre", { exact: true })).toBeVisible({ timeout: 10_000 });
});

test("tiempo real: /torre con WS sin errores de consola", async ({ page }) => {
  const errores: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errores.push(m.text());
  });
  await page.goto("/torre");
  await page.getByText("Requiere atención").waitFor({ timeout: 20_000 });
  // Dejar tiempo para que lleguen eventos del WS (telemetría/estado) y se apliquen vía setQueryData.
  await page.waitForTimeout(4000);
  expect(errores, "errores de consola en /torre").toEqual([]);
});
