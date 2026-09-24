import { test, expect, type Page } from "@playwright/test";
import { Client } from "pg";

// Auth vía storageState (global-setup). TMS_TRIMBLE_FAKE=1 lo pone la CI/entorno.

function dbClient(): Client {
  return new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "tms",
    database: process.env.DB_NAME ?? "tms",
  });
}

async function tripRow(codigo: string) {
  const c = dbClient();
  await c.connect();
  const r = await c.query(
    "SELECT terminal, semirremolque_id, conductor_id, estado, fecha_esperada_carga, fecha_esperada_descarga FROM trips WHERE id = $1",
    [codigo],
  );
  await c.end();
  return r.rows[0];
}

// Reset determinista de los viajes que mutan los tests (el seed corre una sola vez).
test.beforeEach(async () => {
  const c = dbClient();
  await c.connect();
  const hoy = new Date().toISOString().slice(0, 10);
  await c.query(
    "UPDATE operaciones.trips SET terminal=NULL, semirremolque_id=NULL, remolque_id=NULL, conductor_id=NULL, estado='sin_asignar', fecha_esperada_carga=$1, fecha_esperada_descarga=$2 WHERE codigo='E2E-PLAN-OK'",
    [hoy + "T10:00", hoy + "T12:00"],
  );
  await c.query(
    "UPDATE operaciones.trips SET terminal='E2E-TRAC', estado='enviado', fecha_esperada_carga=$1, fecha_esperada_descarga=$2 WHERE codigo='E2E-PLAN-ENVIADO'",
    [hoy + "T16:00", hoy + "T18:00"],
  );
  await c.query(
    "UPDATE operaciones.trips SET terminal='E2E-TRAC', estado='enviado', fecha_esperada_carga=$1, fecha_esperada_descarga=$2 WHERE codigo='E2E-FAIL-1'",
    [hoy + "T18:00", hoy + "T20:00"],
  );
  await c.end();
});

// Llamadas SOAP registradas por el modo falso (vía el navegador, con el JWT del storageState).
async function fakeCalls(page: Page): Promise<{ op: string; ids: string[] }[]> {
  return page.evaluate(async () => {
    const t = localStorage.getItem("tms_jwt");
    const r = await fetch("/api/trimble/fake-calls", { headers: { Authorization: `Bearer ${t}` } });
    return (await r.json()).calls;
  });
}
async function fakeReset(page: Page): Promise<void> {
  await page.evaluate(async () => {
    const t = localStorage.getItem("tms_jwt");
    await fetch("/api/trimble/fake-reset", { method: "POST", headers: { Authorization: `Bearer ${t}` } });
  });
}

async function abrirPlanificacion(page: Page) {
  await page.goto("/planificacion");
  await expect(page.locator('[data-tractora="E2E-TRAC"]')).toBeVisible();
}

// Arrastre con eventos de puntero (el tablero usa onPointerDown + pointermove/up globales).
async function arrastrar(page: Page, source: string, target: string) {
  await page.locator(source).hover();
  await page.mouse.down();
  await page.locator(target).hover();
  await page.mouse.up();
}

// Posición X (viewport) de una hora concreta del eje (pxHora=48 por defecto, columna matrícula 160px).
async function xDeHora(page: Page, hora: number): Promise<number> {
  return page.evaluate((h) => {
    const eje = document.querySelector("[data-eje]") as HTMLElement;
    const rect = eje.getBoundingClientRect();
    return rect.left - eje.scrollLeft + 160 + h * 48;
  }, hora);
}

// Arrastrar y soltar en un X concreto (para fijar la hora de destino).
async function soltarEn(page: Page, source: string, target: string, clientX: number) {
  await page.locator(source).hover();
  await page.mouse.down();
  const box = await page.locator(target).boundingBox();
  await page.mouse.move(clientX, box!.y + box!.height / 2, { steps: 4 });
  await page.mouse.up();
}

test.describe("Planificación — envío manual", () => {
  test("a) pendiente → tractora: sin popover, borde discontinuo, 0 llamadas a Trimble", async ({ page }) => {
    await abrirPlanificacion(page);
    await fakeReset(page);

    await arrastrar(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]');
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
    expect(await fakeCalls(page)).toHaveLength(0);
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toHaveClass(/border-dashed/);
  });

  test("b) mover en horizontal conserva la hora (BD guarda el nuevo inicio)", async ({ page }) => {
    await abrirPlanificacion(page);
    // El viaje está pendiente → asígnalo primero.
    await arrastrar(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]');
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
    const antes = await tripRow("E2E-PLAN-OK");

    const bloque = page.locator('[data-viaje-bloque="E2E-PLAN-OK"]');
    const box = await bloque.boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.move(box!.x + 10, box!.y + box!.height / 2);
    await page.mouse.down();
    await page.mouse.move(box!.x + 10 + 48, box!.y + box!.height / 2, { steps: 4 });
    await page.mouse.up();

    await expect.poll(async () => (await tripRow("E2E-PLAN-OK")).fecha_esperada_carga, { timeout: 10000 }).not.toBe(antes.fecha_esperada_carga);
  });

  test("c) clic derecho → enviar: create+assign+deploy 1 vez → icono de camión", async ({ page }) => {
    await abrirPlanificacion(page);
    await fakeReset(page);
    await arrastrar(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]');
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });

    await page.locator('[data-viaje-bloque="E2E-PLAN-OK"]').click({ button: "right" });
    await page.getByText("Enviar viaje al terminal").click();

    await expect.poll(async () => (await fakeCalls(page)).map((c) => c.op), { timeout: 10000 }).toEqual(["createTrips", "assignTrips", "deployTrips"]);
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toHaveClass(/border-solid/, { timeout: 10000 });
  });

  test("d) enviado → Pendientes: aviso exacto; Esc no cambia; Enter quita del terminal (unassign 1 vez)", async ({ page }) => {
    await abrirPlanificacion(page);
    await fakeReset(page);
    const bloque = page.locator('[data-viaje-bloque="E2E-PLAN-ENVIADO"]');
    await expect(bloque).toBeVisible({ timeout: 10000 });

    await arrastrar(page, '[data-viaje-bloque="E2E-PLAN-ENVIADO"]', '[data-zona-pendientes]');
    await expect(page.getByText("El viaje será eliminado de la pantalla del terminal.")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByText("El viaje será eliminado de la pantalla del terminal.")).toBeHidden();
    expect(await fakeCalls(page)).toHaveLength(0);

    await arrastrar(page, '[data-viaje-bloque="E2E-PLAN-ENVIADO"]', '[data-zona-pendientes]');
    await expect(page.getByText("El viaje será eliminado de la pantalla del terminal.")).toBeVisible();
    await page.keyboard.press("Enter");
    await expect.poll(async () => (await fakeCalls(page)).filter((c) => c.op === "unAssignTrips").length, { timeout: 10000 }).toBe(1);
    await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-ENVIADO"]')).toBeVisible({ timeout: 10000 });
  });

  test("e) enviado → otra tractora: aviso → unassign 1 vez, planificado (discontinuo) y NO se reenvía", async ({ page }) => {
    await abrirPlanificacion(page);
    await fakeReset(page);
    const bloque = page.locator('[data-viaje-bloque="E2E-PLAN-ENVIADO"]');
    await expect(bloque).toBeVisible({ timeout: 10000 });

    await arrastrar(page, '[data-viaje-bloque="E2E-PLAN-ENVIADO"]', '[data-tractora="E2E-TRAC3"]');
    await expect(page.getByText("El viaje será eliminado de la pantalla del terminal.")).toBeVisible();
    await page.keyboard.press("Enter");

    await expect.poll(async () => (await fakeCalls(page)).map((c) => c.op), { timeout: 10000 }).toEqual(["unAssignTrips"]);
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-ENVIADO"]')).toHaveClass(/border-dashed/, { timeout: 10000 });
    const row = await tripRow("E2E-PLAN-ENVIADO");
    expect(row.terminal).toBe("E2E-TRAC3");
    expect(row.estado).toBe("sin_asignar");
  });

  test("f) bloqueo (tractora solapada): no se deja soltar + toast", async ({ page }) => {
    await abrirPlanificacion(page);
    // Soltar a las 10:00 (solapa con E2E-PLAN-SOLAP 09:00–11:00 en E2E-TRAC2).
    const x10 = await xDeHora(page, 10);
    await soltarEn(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC2"]', x10);
    await expect(page.getByText(/No se puede asignar|No se pudo mover/)).toBeVisible({ timeout: 5000 });
    await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible();
  });

  test("g) fault de Trimble al quitar: el bloque vuelve a su sitio + toast", async ({ page }) => {
    await abrirPlanificacion(page);
    await fakeReset(page);
    const bloque = page.locator('[data-viaje-bloque="E2E-FAIL-1"]');
    await expect(bloque).toBeVisible({ timeout: 10000 });
    await arrastrar(page, '[data-viaje-bloque="E2E-FAIL-1"]', '[data-zona-pendientes]');
    await expect(page.getByText("El viaje será eliminado de la pantalla del terminal.")).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(page.getByText(/No se pudo mover/)).toBeVisible({ timeout: 10000 });
    const row = await tripRow("E2E-FAIL-1");
    expect(row.terminal).toBe("E2E-TRAC");
  });

  test("h) Ctrl+Z tras mover un no enviado: vuelve a su posición anterior", async ({ page }) => {
    await abrirPlanificacion(page);
    await arrastrar(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]');
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(500);
    await page.keyboard.press("Control+z");
    await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
  });

  test("i) rendimiento: mover y devolver 5 veces, reflejo optimista <1s por movimiento", async ({ page }) => {
    await abrirPlanificacion(page);
    for (let i = 0; i < 5; i++) {
      const t0 = Date.now();
      await arrastrar(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]');
      await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 3000 });
      expect(Date.now() - t0).toBeLessThan(1000);
      await arrastrar(page, '[data-viaje-bloque="E2E-PLAN-OK"]', '[data-zona-pendientes]');
      await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible({ timeout: 3000 });
      await page.waitForTimeout(200);
    }
  });

  test("j) diagonal: otra tractora +3 h → BD con la nueva tractora y la hora nueva", async ({ page }) => {
    await abrirPlanificacion(page);
    const x13 = await xDeHora(page, 13);
    await soltarEn(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC3"]', x13);
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
    const row = await tripRow("E2E-PLAN-OK");
    expect(row.terminal).toBe("E2E-TRAC3");
    expect((row.fecha_esperada_carga as string).slice(11, 16)).toMatch(/^13:/);
  });

  test("k) pendiente soltado a las 12:00 → inicio 12:00", async ({ page }) => {
    await abrirPlanificacion(page);
    const x12 = await xDeHora(page, 12);
    await soltarEn(page, '[data-viaje-pendiente="E2E-PLAN-OK"]', '[data-tractora="E2E-TRAC"]', x12);
    await expect(page.locator('[data-viaje-bloque="E2E-PLAN-OK"]')).toBeVisible({ timeout: 10000 });
    const row = await tripRow("E2E-PLAN-OK");
    expect((row.fecha_esperada_carga as string).slice(11, 16)).toMatch(/^12:/);
  });

  test("l) Esc cancela el arrastre (el viaje vuelve a su sitio)", async ({ page }) => {
    await abrirPlanificacion(page);
    await page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]').hover();
    await page.mouse.down();
    await page.locator('[data-tractora="E2E-TRAC"]').hover();
    await page.keyboard.press("Escape");
    await page.mouse.up();
    await expect(page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]')).toBeVisible();
    const row = await tripRow("E2E-PLAN-OK");
    expect(row.terminal).toBeNull();
  });

  test("m) auto-scroll: arrastrar al borde inferior baja el tablero", async ({ page }) => {
    await abrirPlanificacion(page);
    await page.locator('[data-viaje-pendiente="E2E-PLAN-OK"]').hover();
    await page.mouse.down();
    const eje = page.locator("[data-eje]");
    const box = await eje.boundingBox();
    // Acercarse al borde inferior (auto-scroll) y mantener para que avance.
    await page.mouse.move(box!.x + 200, box!.y + box!.height - 5);
    await page.waitForTimeout(700);
    const scrollTop = await page.evaluate(() => (document.querySelector("[data-eje]") as HTMLElement).scrollTop);
    expect(scrollTop).toBeGreaterThan(0);
    await page.keyboard.press("Escape");
    await page.mouse.up();
  });
});
