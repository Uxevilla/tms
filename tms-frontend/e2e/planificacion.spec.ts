import { test, expect, type Locator, type Page } from "@playwright/test";
import { Client } from "pg";

// E2E Fase 3: arrastre en el tablero de planificación.
// El validar va contra el backend REAL; solo se mockea el despacho SOAP (POST /api/trips/*/asignar),
// que en e2e no tiene credenciales Trimble. La asignación es optimista: el POST /asignar se difiere
// 10 s (ventana de deshacer), así que deshacer/reasignar/desasignar no tocan el servidor.

async function arrastrar(page: Page, origen: Locator, destino: Locator) {
  await origen.waitFor({ state: "attached", timeout: 5000 });
  await page.waitForTimeout(150);
  await origen.scrollIntoViewIfNeeded();
  await destino.scrollIntoViewIfNeeded();
  const ob = await origen.boundingBox();
  const db = await destino.boundingBox();
  if (!ob || !db) throw new Error("no se pudo medir el origen o destino");
  await page.mouse.move(ob.x + ob.width / 2, ob.y + ob.height / 2);
  await page.mouse.down();
  await page.mouse.move(db.x + db.width / 2, db.y + db.height / 2, { steps: 15 });
}

async function mockAsignar(page: Page, llamadas?: number[]) {
  await page.route("**/api/trips/*/asignar", (route) => {
    llamadas?.push(1);
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
  });
}

async function cambiarEstadoTrip(codigo: string, estado: string): Promise<void> {
  const client = new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "",
    database: process.env.DB_NAME ?? "tms",
  });
  await client.connect();
  await client.query("UPDATE operaciones.trips SET estado = $1 WHERE codigo = $2", [estado, codigo]);
  await client.end();
}

test("arrastre ok → verde → asignar → deshacer (sin llamar a /asignar)", async ({ page }) => {
  const llamadas: number[] = [];
  await mockAsignar(page, llamadas);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor({ timeout: 20_000 });

  await arrastrar(page, pendiente, fila);
  await expect(fila).toHaveClass(/ring-green/, { timeout: 10_000 });
  await page.mouse.up();

  // Popover → Asignar (espera a que termine la re-validación y se habilite).
  const asignar = page.getByRole("button", { name: "Asignar", exact: true });
  await expect(asignar).toBeEnabled({ timeout: 10_000 });
  await asignar.click();

  // El viaje aparece como bloque en el timeline + toast de deshacer.
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });

  // Deshacer → vuelve a "Pendientes" y /asignar NO se ha llamado.
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toHaveCount(0);
  expect(llamadas.length).toBe(0);
});

test("bloqueo (tractora solapada) → rojo → soltar → toast con motivo, sin popover", async ({ page }) => {
  await mockAsignar(page);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const filaOcupada = page.locator('[data-tractora="E2E-TRAC2"]');
  await filaOcupada.waitFor({ timeout: 20_000 });

  await arrastrar(page, pendiente, filaOcupada);
  await expect(filaOcupada).toHaveClass(/ring-red/, { timeout: 10_000 });
  await page.mouse.up();

  // Sin popover + toast con el motivo del bloqueo.
  await expect(page.getByRole("button", { name: "Asignar", exact: true })).toHaveCount(0);
  await expect(page.getByText(/solapado/)).toBeVisible({ timeout: 5000 });
});

test("aviso (ITV caducada) → ámbar → confirmación → popover", async ({ page }) => {
  await mockAsignar(page);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-AVISO"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor({ timeout: 20_000 });

  await arrastrar(page, pendiente, fila);
  await expect(fila).toHaveClass(/ring-amber/, { timeout: 10_000 });
  await page.mouse.up();

  // Confirmación de avisos dentro de la página → Continuar → popover.
  await expect(page.getByText("Asignar con avisos")).toBeVisible({ timeout: 5000 });
  await page.getByRole("button", { name: "Continuar", exact: true }).click();
  await expect(page.getByRole("button", { name: "Asignar", exact: true })).toBeVisible({ timeout: 5000 });
});

test("reasignar un viaje no enviado a otra tractora", async ({ page }) => {
  await mockAsignar(page);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila1 = page.locator('[data-tractora="E2E-TRAC"]');
  const fila3 = page.locator('[data-tractora="E2E-TRAC3"]');
  await fila1.waitFor();
  await fila3.waitFor();

  // Asignar a E2E-TRAC.
  await arrastrar(page, pendiente, fila1);
  await expect(fila1).toHaveClass(/ring-green/, { timeout: 10_000 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Asignar", exact: true }).click();
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });

  // Reasignar: arrastrar el bloque a E2E-TRAC3.
  const bloque = page.locator('[data-viaje-bloque="E2E-PLAN-OK"]');
  await arrastrar(page, bloque, fila3);
  await expect(fila3).toHaveClass(/ring-green/, { timeout: 10_000 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Asignar", exact: true }).click();

  // El bloque ahora está dentro de E2E-TRAC3.
  await expect(fila3.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });
  await expect(fila1.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toHaveCount(0);
});

test("desasignar soltando en 'Pendientes'", async ({ page }) => {
  await mockAsignar(page);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor();

  // Asignar a E2E-TRAC.
  await arrastrar(page, pendiente, fila);
  await expect(fila).toHaveClass(/ring-green/, { timeout: 10_000 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Asignar", exact: true }).click();
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });

  // Desasignar: arrastrar el bloque y soltarlo en la columna "Pendientes".
  const bloque = page.locator('[data-viaje-bloque="E2E-PLAN-OK"]');
  await arrastrar(page, bloque, page.locator("[data-zona-pendientes]"));
  await page.mouse.up();

  await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toHaveCount(0);
});

test("un viaje de otra fecha no aparece en la vista del día actual", async ({ page }) => {
  await page.goto("/planificacion");

  // El viaje de hoy SÍ aparece (sanity de carga); el de ayer, no.
  await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible({ timeout: 20_000 });
  await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OTRO"]')).toHaveCount(0);
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OTRO"]')).toHaveCount(0);
});

test("la asignación pendiente sobrevive a un refetch y /asignar se llama una sola vez a los 10 s", async ({ page }) => {
  const llamadas: number[] = [];
  await mockAsignar(page, llamadas);
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor();

  // Asignar (optimista → bloque + toast + temporizador de 10 s).
  await arrastrar(page, pendiente, fila);
  await expect(fila).toHaveClass(/ring-green/, { timeout: 10_000 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Asignar", exact: true }).click();
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });

  // Cambiar el estado de OTRO viaje en la BD → el WS (poll ~3 s) invalida y refetchea.
  await cambiarEstadoTrip("E2E-PLAN-AVISO", "En_Transito");

  // A los ~4 s, tras el refetch, el bloque SIGUE en la tractora (enCurso lo mantiene; no vuelve a Pendientes).
  await page.waitForTimeout(4000);
  await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toHaveCount(0);

  // A los 10 s, /asignar se llama exactamente una vez.
  await expect.poll(() => llamadas.length, { timeout: 12000 }).toBe(1);
  await page.waitForTimeout(1000);
  expect(llamadas.length).toBe(1);
});

test("validar del popover con 500 → Asignar desactivado", async ({ page }) => {
  await mockAsignar(page);
  let fallaValidar = false;
  await page.route("**/api/planificacion/validar", (route) => {
    if (fallaValidar) {
      return route.fulfill({ status: 500, contentType: "application/json", body: "{}" });
    }
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, bloqueos: [], avisos: [] }) });
  });
  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor();

  // Arrastre: el validar del arrastre responde ok (200) → verde.
  await arrastrar(page, pendiente, fila);
  await expect(fila).toHaveClass(/ring-green/, { timeout: 10_000 });

  // A partir de ahora el validar falla (500) → la re-validación del popover falla.
  fallaValidar = true;
  await page.mouse.up();

  await expect(page.getByText("⛔ No se pudo validar")).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole("button", { name: "Asignar", exact: true })).toBeDisabled();
});
