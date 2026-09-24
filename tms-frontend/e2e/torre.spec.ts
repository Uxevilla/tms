import { test, expect, type Page } from "@playwright/test";

// E2E Fase 2: 404 propia, panel de entidad (?panel=…), paleta de comandos con
// búsqueda y cambio de contraseña obligatorio. Mosaicos de mapa externos simulados.

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

test("404 propia dentro del AppShell", async ({ page }) => {
  await page.goto("/ruta-inexistente");
  await expect(page.getByText("Página no encontrada")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("link", { name: "Volver a la torre de control" }).click();
  await expect(page).toHaveURL(/\/torre/);
});

test("panel de entidad se abre desde ?panel=… y se cierra con Esc", async ({ page }) => {
  await page.goto("/torre?panel=vehiculo:V-99999");
  // El panel fetchea la entidad (404 aquí) y muestra el estado vacío.
  await expect(page.getByText("No encontrado.")).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
  await expect(page.getByText("No encontrado.")).toHaveCount(0);
});

test("paleta de comandos: acciones + búsqueda global", async ({ page }) => {
  await page.goto("/torre");
  await expect(page.getByRole("link", { name: "Torre de control", exact: true })).toBeVisible({
    timeout: 20_000,
  });

  await page.keyboard.press("Control+k");
  await expect(page.getByText("Nuevo viaje")).toBeVisible();

  // Búsqueda sin coincidencias (≥2 caracteres).
  const input = page.getByPlaceholder("Buscar viajes, vehículos, conductores…");
  await input.fill("zzzz");
  await expect(page.getByText("Sin resultados.")).toBeVisible({ timeout: 10_000 });
});

test("cambio de contraseña obligatorio del admin", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "admin", "se ejecuta una sola vez (proyecto admin)");
  const usuario = process.env.E2E_CAMBIA_USER ?? "e2e_cambia";
  const contrasena = process.env.E2E_CAMBIA_PASSWORD ?? "e2e-cambia-1";
  const nueva = "e2e-cambia-nueva-1";

  await page.goto("/login");
  await page.evaluate(() => localStorage.removeItem("tms_jwt"));
  await page.reload();
  await page.locator("#username").fill(usuario);
  await page.locator("#password").fill(contrasena);
  await page.locator('button[type="submit"]').click();

  // Pantalla de cambio obligatorio.
  await expect(page.getByText("Cambio de contraseña obligatorio")).toBeVisible({ timeout: 15_000 });
  await page.getByLabel("Nueva contraseña").fill(nueva);
  await page.getByLabel("Confirmar contraseña").fill(nueva);
  await page.getByRole("button", { name: "Guardar y continuar" }).click();

  await expect(page).toHaveURL(/\/torre/, { timeout: 15_000 });
});
