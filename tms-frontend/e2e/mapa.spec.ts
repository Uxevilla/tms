import { test, expect } from "@playwright/test";
import { Client } from "pg";

// E2E: el encuadre automático se hace SOLO la primera vez. Mover el mapa y recibir
// una posición GPS NUEVA no debe cambiar la cámara (para no pelear con el usuario).

const TILE_HOSTS = ["tile.openstreetmap.org", "server.arcgisonline.com"];
const TILE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

async function insertarPosicion(vehiculoId: string, lat: number, lng: number): Promise<void> {
  const client = new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "",
    database: process.env.DB_NAME ?? "tms",
  });
  await client.connect();
  await client.query(
    "INSERT INTO telemetria.posiciones_gps (time, vehiculo_id, lat, lng) VALUES (now(), $1, $2, $3)",
    [vehiculoId, lat, lng],
  );
  await client.end();
}

test.beforeEach(async ({ page }) => {
  for (const host of TILE_HOSTS) {
    await page.route(`**${host}/**`, (route) =>
      route.fulfill({ status: 200, contentType: "image/png", body: TILE_PNG }),
    );
  }
});

test("mover el mapa: una posición nueva no cambia el encuadre", async ({ page }) => {
  await page.goto("/torre");

  // Marcador fijo que rastreamos (0002-TST): su posición geo NO cambia durante el test.
  const marcador = () => page.locator('.maplibregl-marker button[title="0002-TST"]');
  await marcador().waitFor({ timeout: 20_000 });

  const box0 = await marcador().boundingBox();
  if (!box0) throw new Error("no se pudo medir el marcador 0002-TST");

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

  // Llega una posición GPS NUEVA (de OTRO vehículo, lejos): el encuadre NO debe cambiar.
  await insertarPosicion("E2E-VEH", 43.0, -8.0);
  await page.waitForTimeout(6000); // el backend sondea (~3s) y el WS empuja la telemetría
  const box2 = await marcador().boundingBox();
  if (!box2) throw new Error("marcador desapareció tras la telemetría");
  // Tolerancia de ~5px: el re-render del marcador tras la telemetría puede derivar
  // 2-3px sub-píxel; un reencuadre REAL mueve el marcador decenas de px.
  expect(Math.abs(box2.x - box1.x)).toBeLessThan(5);
  expect(Math.abs(box2.y - box1.y)).toBeLessThan(5);
});
