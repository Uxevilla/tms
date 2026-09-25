import { test, expect } from "@playwright/test";
import { Client } from "pg";

// Cliente pg reutilizable (misma configuración que viajes.spec.ts).
function dbClient(): Client {
  return new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "tms",
    database: process.env.DB_NAME ?? "tms",
  });
}

// Conteo de gastos del vehículo con cuenta 622 (verificación por BD, no por texto).
async function contarGastos622(codigo: string): Promise<number> {
  const c = dbClient();
  await c.connect();
  try {
    const r = await c.query(
      "SELECT count(*)::int AS n FROM finanzas.gastos_vehiculos WHERE vehiculo_id = $1 AND cuenta_contable_gasto = '622'",
      [codigo],
    );
    return r.rows[0].n;
  } finally {
    await c.end();
  }
}

test("completar un mantenimiento con 'generar gasto' → gasto con cuenta 622", async ({ page }) => {
  await page.goto("/taller");
  await expect(page.getByRole("button", { name: /Nuevo mantenimiento/i })).toBeVisible();

  // Filtra por el vehículo de prueba del seed (E2E-VEH2, tractora).
  await page.getByLabel("Vehículo", { exact: true }).selectOption("E2E-VEH2");

  await page.getByRole("button", { name: /Nuevo mantenimiento/i }).click();

  // Tipo ya viene "revision" por defecto; rellena fecha + marca Completado y Generar gasto.
  await page.getByLabel("Fecha", { exact: true }).fill("2026-09-25");
  await page.getByLabel("Completado", { exact: true }).check();
  await page.getByLabel("Generar gasto", { exact: true }).check();
  await page.getByLabel("Base imponible (€)", { exact: true }).fill("100");

  await page.getByRole("button", { name: "Guardar", exact: true }).click();

  // Espera a que el sheet se cierre (el gasto se crea en BD al guardar).
  await expect(page.getByRole("button", { name: /Nuevo mantenimiento/i })).toBeVisible();

  // Verifica en BD: aparece el gasto con cuenta 622 para el vehículo filtrado.
  await expect.poll(() => contarGastos622("E2E-VEH2")).toBeGreaterThanOrEqual(1);
});
