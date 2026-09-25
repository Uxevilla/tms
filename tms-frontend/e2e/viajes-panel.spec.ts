import { test, expect } from "@playwright/test";

// Regresión en Viajes (Fase 4): la edición en línea de matrícula y conductor es un
// <select> de un clic (ya no doble clic de AG Grid), y el panel de entidad se abre
// desde el icono ↗️, no desde el valor.

test("matrícula y conductor se editan con select en línea; ↗️ abre el panel", async ({ page }) => {
  await page.goto("/viajes");

  // 1. La matrícula es un select editable (edición en línea).
  const matricula = page.locator('[data-campo="matricula"]').first();
  await matricula.waitFor({ timeout: 20_000 });
  await expect(matricula).toBeVisible();

  // 2. El conductor también es un select editable.
  const conductor = page.locator('[data-campo="conductor"]').first();
  await expect(conductor).toBeVisible();

  // 3. El icono ↗️ abre el panel del vehículo (no el select).
  const icono = page.locator('[data-panel^="vehiculo:"]').first();
  await icono.waitFor({ timeout: 10_000 });
  await icono.click();
  await expect(page).toHaveURL(/panel=vehiculo/, { timeout: 10_000 });
});
