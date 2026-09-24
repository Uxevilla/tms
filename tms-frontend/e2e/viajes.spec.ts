import { test, expect, type Page } from "@playwright/test";

// Auth via storageState (global-setup); solo navegar.
async function abrirViajes(page: Page) {
  await page.goto("/viajes");
  await expect(page).toHaveURL(/\/viajes/);
  await expect(page.getByRole("button", { name: /Nuevo viaje/i })).toBeVisible();
}

test.describe("Viajes (Fase 4)", () => {
  // Rellena un autocompletado y elige la primera sugerencia con Enter (solo teclado).
  async function elegir(page: Page, placeholder: string, texto: string) {
    const input = page.getByPlaceholder(placeholder).last(); // .last() = la parada recién añadida
    await input.fill(texto);
    // Espera a que la sugerencia esté disponible (la búsqueda terminó) antes de pulsar Enter.
    await expect(page.getByRole("button", { name: new RegExp(texto.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).first()).toBeVisible({ timeout: 8000 });
    await input.press("Enter");
  }

  test("crear un viaje de 2 paradas solo con teclado en < 30 s", async ({ page }) => {
    await abrirViajes(page);
    const t0 = Date.now();

    await page.getByRole("button", { name: /Nuevo viaje/i }).click();
    await expect(page.getByPlaceholder("Buscar cliente…")).toBeVisible();

    // 1. Cliente (autocompletado → Enter).
    await elegir(page, "Buscar cliente…", "CLI-E2E");
    await expect(page.getByPlaceholder("Buscar cliente…")).toHaveValue(/CLI-E2E/);

    // 2. Fechas (carga / descarga).
    await page.locator('input[type="datetime-local"]').nth(0).fill("2026-09-25T08:00");
    await page.locator('input[type="datetime-local"]').nth(1).fill("2026-09-25T18:00");

    // 3. Origen.
    await elegir(page, "Buscar origen…", "DIR-E2E-ORIGEN");

    // 4. Dos paradas.
    await page.getByRole("button", { name: /Añadir parada|parada/i }).click({ force: true });
    await elegir(page, "Buscar parada…", "DIR-E2E-PARADA1");

    await page.getByRole("button", { name: /Añadir parada|parada/i }).click({ force: true });
    await elegir(page, "Buscar parada…", "DIR-E2E-PARADA2");

    // 5. Destino.
    await elegir(page, "Buscar destino…", "DIR-E2E-DESTINO");

    // 6. Guardar con atajo (Ctrl+Enter) — sin tocar el botón.
    await page.keyboard.press("Control+Enter");

    // El viaje aparece en la lista (destino E2EDESTINO) sin recargar.
    await expect(page.getByText("E2EDESTINO", { exact: false }).first()).toBeVisible({ timeout: 15_000 });
    const elapsed = (Date.now() - t0) / 1000;
    expect(elapsed).toBeLessThan(30);
  });

  test("validación en vivo: el botón se habilita solo con los obligatorios", async ({ page }) => {
    await abrirViajes(page);
    await page.getByRole("button", { name: /Nuevo viaje/i }).click();
    await expect(page.getByPlaceholder("Buscar cliente…")).toBeVisible();

    // Sin rellenar nada, el botón de guardar debe estar deshabilitado.
    const guardar = page.getByRole("button", { name: /Crear viaje/i });
    await expect(guardar).toBeDisabled();

    // Al elegir cliente se precargan origen/destino (direcciones habituales); faltan las fechas.
    await elegir(page, "Buscar cliente…", "CLI-E2E");
    await expect(guardar).toBeDisabled();
    await expect(page.getByText(/Indica la fecha de carga/i)).toBeVisible();

    // Al rellenar las fechas, el botón se habilita (cliente + origen + destino + fechas listos).
    await page.locator('input[type="datetime-local"]').nth(0).fill("2026-09-25T08:00");
    await page.locator('input[type="datetime-local"]').nth(1).fill("2026-09-25T18:00");
    await expect(guardar).toBeEnabled();
  });
});
