import { test, expect } from "@playwright/test";

// E2E: el encuadre automático se hace SOLO la primera vez. Mover el mapa y recibir
// telemetría nueva no debe cambiar la cámara (para no pelear con el usuario).

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

test("mover el mapa: la telemetría nueva no cambia el encuadre", async ({ page }) => {
  await page.goto("/torre");

  // Fijamos un vehículo concreto por su matrícula para seguirlo entre re-renders.
  const primer = page.locator(".maplibregl-marker button[title]").first();
  await primer.waitFor({ timeout: 20_000 });
  const titulo = await primer.getAttribute("title");
  if (!titulo) throw new Error("el marcador no tiene matrícula (title)");
  const marcador = () => page.locator(`.maplibregl-marker button[title="${titulo}"]`);

  const box0 = await marcador().boundingBox();
  if (!box0) throw new Error("no se pudo medir el marcador");

  // Arrastrar el mapa (pan).
  const canvas = page.locator(".maplibregl-canvas");
  const cb = await canvas.boundingBox();
  if (!cb) throw new Error("no se pudo medir el canvas");
  await page.mouse.move(cb.x + cb.width / 2, cb.y + cb.height / 2);
  await page.mouse.down();
  await page.mouse.move(cb.x + cb.width / 2 - 120, cb.y + cb.height / 2 - 70, { steps: 8 });
  await page.mouse.up();

  // El mapa se movió: el marcador cambió de posición en pantalla.
  const box1 = await marcador().boundingBox();
  if (!box1) throw new Error("marcador desapareció tras el pan");
  expect(Math.abs(box1.x - box0.x) + Math.abs(box1.y - box0.y)).toBeGreaterThan(5);

  // Llega telemetría nueva por WS; el encuadre NO debe cambiar.
  await page.waitForTimeout(5000);
  const box2 = await marcador().boundingBox();
  if (!box2) throw new Error("marcador desapareció tras la telemetría");
  expect(box2.x).toBeCloseTo(box1.x, 0);
  expect(box2.y).toBeCloseTo(box1.y, 0);
});
