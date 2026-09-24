import { test, expect } from "@playwright/test";

// E2E Fase 3: arrastre en el tablero de planificación.
// validar (real) → la fila se tiñe de verde → soltar → popover → asignado (optimista) → deshacer.
// El despacho SOAP (POST /api/trips/*/asignar) se mockea: en e2e no hay credenciales Trimble.
// El validar y el PATCH de desasignar van contra el backend real.

test("arrastre: validar → color → soltar → asignado → deshacer", async ({ page }) => {
  await page.route("**/api/trips/*/asignar", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) }),
  );

  await page.goto("/planificacion");

  const pendiente = page.locator('[data-viaje-pendiente="E2E-TRIP-1"]');
  await pendiente.waitFor({ timeout: 20_000 });
  const fila = page.locator('[data-tractora="E2E-TRAC"]');
  await fila.waitFor({ timeout: 20_000 });

  // Asegurar que ambos quedan visibles (columnas con scroll independiente).
  await pendiente.scrollIntoViewIfNeeded();
  await fila.scrollIntoViewIfNeeded();

  const pb = await pendiente.boundingBox();
  const fb = await fila.boundingBox();
  if (!pb || !fb) throw new Error("no se pudo medir el pendiente o la tractora");

  // Arrastrar el pendiente sobre la tractora (pointer events).
  await page.mouse.move(pb.x + pb.width / 2, pb.y + pb.height / 2);
  await page.mouse.down();
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2, { steps: 12 });

  // Validación ok (sin bloqueos ni avisos) → la fila se tiñe de verde.
  await expect(fila).toHaveClass(/ring-green/, { timeout: 5000 });

  await page.mouse.up();

  // Popover de asignación → confirmar.
  await page.getByRole("button", { name: "Asignar", exact: true }).click();

  // El viaje aparece en el timeline (bloque) + toast de deshacer.
  await expect(page.locator('[data-viaje-bloque="E2E-TRIP-1"]')).toBeVisible({ timeout: 5000 });
  await expect(page.getByRole("button", { name: "Deshacer" })).toBeVisible();

  // Deshacer → el viaje vuelve a "Pendientes de asignar".
  await page.getByRole("button", { name: "Deshacer" }).click();
  await expect(page.locator('[data-viaje-pendiente="E2E-TRIP-1"]')).toBeVisible({ timeout: 5000 });
});
