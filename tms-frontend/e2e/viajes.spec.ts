import { test, expect, type Page } from "@playwright/test";
import { Client } from "pg";

// Auth vía storageState (global-setup); solo navegar.
async function abrirViajes(page: Page) {
  await page.goto("/viajes");
  await expect(page.getByRole("button", { name: /Nuevo viaje/i })).toBeVisible();
}

// Teselas del minimapa (OSM/ArcGIS): se simulan con un PNG 1×1 para que el settle de
// Playwright no se quede esperando peticiones externas (mismas que smoke/torre).
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

// Cliente pg reutilizable (misma configuración para countTrips y getTripFecha).
function dbClient(): Client {
  return new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "tms",
    database: process.env.DB_NAME ?? "tms",
  });
}

// Conteo de viajes en BD (verificación del "crear viaje" por total, no por texto).
async function countTrips(): Promise<number> {
  const c = dbClient();
  await c.connect();
  const r = await c.query("SELECT count(*)::int AS n FROM operaciones.trips");
  await c.end();
  return r.rows[0].n;
}

// fecha_esperada_carga en BD de un viaje (para verificar que la hora se conserva).
async function getTripFecha(codigo: string): Promise<string> {
  const c = dbClient();
  await c.connect();
  const r = await c.query("SELECT fecha_esperada_carga FROM operaciones.trips WHERE codigo = $1", [codigo]);
  await c.end();
  return r.rows[0]?.fecha_esperada_carga ?? "";
}

// Restaura la fecha (el test la muta y debe dejar el seed intacto para otros tests).
async function restoreTripFecha(codigo: string, fecha: string): Promise<void> {
  const c = dbClient();
  await c.connect();
  await c.query("UPDATE operaciones.trips SET fecha_esperada_carga = $2 WHERE codigo = $1", [codigo, fecha]);
  await c.end();
}

// Fecha de HOY en local (misma lógica que `datetime.date.today()` del seed).
function hoyLocal(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

test.describe("Viajes (Fase 4)", () => {
  // Rellena un autocompletado con teclado: foco + type + Enter (selecciona la primera sugerencia).
  async function elegirTeclado(page: Page, placeholder: string, texto: string) {
    const input = page.getByPlaceholder(placeholder).last();
    await input.focus();
    await page.keyboard.press("Control+A"); // selecciona el valor precargado (si lo hay)
    await page.keyboard.type(texto);
    // Espera a que la sugerencia esté disponible (la búsqueda terminó) antes de pulsar Enter.
    await expect(page.getByRole("button", { name: new RegExp(texto.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first()).toBeVisible({ timeout: 8000 });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(300); // deja que el blur + el estado de React se apliquen
  }

  // Abre el Sheet con el atajo "n" (keyboard.press real; si el CI se cuelga, se adjunta la traza).
  async function abrirSheetConN(page: Page) {
    await page.waitForTimeout(400); // deja asentar la página tras el goto (evita el hang de keyboard.press)
    await page.keyboard.press("n");
    await expect(page.getByPlaceholder("Buscar cliente…")).toBeVisible({ timeout: 15000 });
  }

  test("crear un viaje de 2 paradas solo con teclado en < 30 s", async ({ page }) => {
    const antes = await countTrips();
    await abrirViajes(page);
    const t0 = Date.now();

    // 1. Abrir el Sheet con el atajo "n".
    await abrirSheetConN(page);

    // 2. Cliente → focus + type + Enter.
    await page.getByPlaceholder("Buscar cliente…").focus();
    await page.keyboard.type("CLI-E2E");
    await expect(page.getByRole("button", { name: /CLI-E2E/ }).first()).toBeVisible({ timeout: 8000 });
    await page.keyboard.press("Enter");

    // 3. Fechas (datetime-local no se puede "teclear" con fiabilidad: se rellena el valor).
    await page.locator('input[type="datetime-local"]').nth(0).fill("2026-09-25T08:00");
    await page.locator('input[type="datetime-local"]').nth(1).fill("2026-09-25T18:00");

    // 4. Origen → foco + type + Enter.
    await elegirTeclado(page, "Buscar origen…", "DIR-E2E-ORIGEN");

    // 5. Dos paradas con el atajo "p" (fuera de inputs, tras el blur de la selección).
    await page.keyboard.press("p");
    await expect(page.locator('[placeholder="Buscar parada…"]')).toHaveCount(1);
    await elegirTeclado(page, "Buscar parada…", "DIR-E2E-PARADA1");

    await page.keyboard.press("p");
    await expect(page.locator('[placeholder="Buscar parada…"]')).toHaveCount(2);
    await elegirTeclado(page, "Buscar parada…", "DIR-E2E-PARADA2");

    // 6. Destino.
    await elegirTeclado(page, "Buscar destino…", "DIR-E2E-DESTINO");

    // 7. Guardar con Ctrl+Enter.
    await page.keyboard.press("Control+Enter");

    // El total de viajes aumenta en 1 (verificado por BD, no por texto).
    await expect.poll(() => countTrips(), { timeout: 15_000 }).toBe(antes + 1);
    const elapsed = (Date.now() - t0) / 1000;
    expect(elapsed).toBeLessThan(30);
  });

  test("edición en línea de fecha: conserva la hora al cambiar el día", async ({ page }) => {
    const antes = await getTripFecha("E2E-PLAN-OK"); // p. ej. "2026-09-24T10:00"
    const hora = antes.slice(10); // "T10:00" (o "" si no había hora)
    try {
      await abrirViajes(page);

      // E2E-PLAN-OK carga HOY; filtramos por hoy para traerlo a la vista (la lista pagina).
      const hoy = hoyLocal();
      await page.locator('input[type="date"]').nth(0).fill(hoy);
      await page.locator('input[type="date"]').nth(1).fill(hoy);
      await expect(page.getByText("E2E-PLAN-OK")).toBeVisible();

      const fila = page.getByRole("row", { name: /E2E-PLAN-OK/ }).first();
      const carga = fila.locator('[data-campo="fecha_carga"]');
      await carga.waitFor({ timeout: 20_000 });
      await carga.fill("2026-10-01");
      await carga.press("Enter"); // blur → commit optimista

      // En BD queda el nuevo día CON la hora original conservada.
      await expect.poll(() => getTripFecha("E2E-PLAN-OK"), { timeout: 15_000 }).toBe("2026-10-01" + hora);
    } finally {
      await restoreTripFecha("E2E-PLAN-OK", antes);
    }
  });

  test("edición en línea de matrícula: optimista y persiste al recargar", async ({ page }) => {
    await abrirViajes(page);

    const matricula = page.locator('[data-campo="matricula"]').first();
    await matricula.waitFor({ timeout: 20_000 });

    await matricula.selectOption({ label: "0003-TST" });
    // Optimista: el select muestra la nueva matrícula sin recargar.
    await expect(matricula).toHaveValue("0003-TST");

    // Recargar: la edición persiste (el PATCH llegó al servidor).
    await page.reload();
    await expect(page.locator('[data-campo="matricula"]').first()).toHaveValue("0003-TST", { timeout: 15_000 });
  });

  test("filtro de texto con debounce: no añade más de 1 entrada al historial", async ({ page }) => {
    await abrirViajes(page);
    await page.waitForTimeout(500);
    const antes = await page.evaluate(() => history.length);

    const filtro = page.getByPlaceholder("Cliente…");
    await filtro.pressSequentially("CLI-E2E", { delay: 40 });
    await page.waitForTimeout(600); // deja pasar el debounce

    const despues = await page.evaluate(() => history.length);
    expect(despues - antes).toBeLessThanOrEqual(1);
  });

  test("filtro de fechas desde=hasta=día de carga → el viaje aparece", async ({ page }) => {
    const hoy = hoyLocal();
    await abrirViajes(page);

    await page.locator('input[type="date"]').nth(0).fill(hoy);
    await page.locator('input[type="date"]').nth(1).fill(hoy);

    // E2E-PLAN-OK carga HOY → aparece; E2E-PLAN-OTRO carga ayer → no aparece.
    await expect(page.getByText("E2E-PLAN-OK")).toBeVisible();
    await expect(page.getByText("E2E-PLAN-OTRO")).toBeHidden();
  });

  test("validación en vivo: el botón se habilita solo con los obligatorios", async ({ page }) => {
    await abrirViajes(page);
    await page.waitForTimeout(400); // deja asentar la página tras el goto (evita el click flaky en CI)
    // Abre el Sheet con el botón (más robusto que el atajo "n"; la validación no es el test de teclado).
    await page.getByRole("button", { name: /Nuevo viaje/i }).click();
    await expect(page.getByPlaceholder("Buscar cliente…")).toBeVisible({ timeout: 15000 });

    // Sin rellenar nada, el botón de guardar está deshabilitado.
    const guardar = page.getByRole("button", { name: /Crear viaje/i });
    await expect(guardar).toBeDisabled();

    // Al elegir cliente se precargan origen/destino; faltan las fechas.
    await page.getByPlaceholder("Buscar cliente…").focus();
    await page.keyboard.type("CLI-E2E");
    await expect(page.getByRole("button", { name: /CLI-E2E/ }).first()).toBeVisible({ timeout: 8000 });
    await page.keyboard.press("Enter");
    await expect(guardar).toBeDisabled();
    await expect(page.getByText(/Indica la fecha de carga/i)).toBeVisible();

    // Al rellenar las fechas, el botón se habilita.
    await page.locator('input[type="datetime-local"]').nth(0).fill("2026-09-25T08:00");
    await page.locator('input[type="datetime-local"]').nth(1).fill("2026-09-25T18:00");
    await expect(guardar).toBeEnabled();
  });
});
