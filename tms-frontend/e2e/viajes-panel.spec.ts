import { test, expect } from "@playwright/test";

// Regresión en Viajes: las columnas editables (matrícula, conductor) vuelven a
// editarse con doble clic, y el panel se abre desde el icono ↗️ (no desde el texto).

test("doble clic en matrícula abre el editor; ↗️ abre el panel", async ({ page }) => {
  await page.goto("/viajes");

  // Celda de matrícula con icono ↗️ (viaje con matrícula del seed).
  const celda = page
    .locator('[role="gridcell"][col-id="matricula"]', { has: page.locator('[data-panel^="vehiculo:"]') })
    .first();
  await celda.waitFor({ timeout: 20_000 });

  // 1. Doble clic en el VALOR → aparece el editor (y NO el panel).
  await celda.locator("[data-celda-valor]").dblclick();
  await expect(page.locator(".ag-cell-inline-editing").first()).toBeVisible({ timeout: 5_000 });
  await expect(page).not.toHaveURL(/panel=/);

  // Cerrar el editor (una segunda Escape por si el desplegable del select abrió).
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");

  // 2. Clic en el ↗️ → se abre el panel del vehículo.
  const icono = celda.locator('[data-panel^="vehiculo:"]');
  await icono.waitFor({ timeout: 5_000 });
  await icono.click();
  await expect(page).toHaveURL(/panel=vehiculo/, { timeout: 10_000 });
});
