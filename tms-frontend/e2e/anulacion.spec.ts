import { test, expect, type Page } from "@playwright/test";
import { Client } from "pg";

// A2 — Anulación de viajes: borrar con motivo → anulado en "Ver anulados" → reactivar.

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

function dbClient(): Client {
  return new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "tms",
    database: process.env.DB_NAME ?? "tms",
  });
}

async function seedTripConDocs(codigo: string) {
  const c = dbClient();
  await c.connect();
  try {
    await c.query(
      "INSERT INTO operaciones.trips (codigo, estado, cliente, precio, iva, creado, origen, destino) " +
        "VALUES ($1, 'Entregado', 'E2E-Cliente-Anul', 100, 21, '2026-09-01', 'A', 'B')",
      [codigo],
    );
    // Con documento → el borrado se convierte en anulación (soft delete).
    await c.query(
      "INSERT INTO files (trip_id, name, ftype, estado_descarga) VALUES ($1, $2, 3, 'descargado')",
      [codigo, `cmr-${codigo}.pdf`],
    );
  } finally {
    await c.end();
  }
}

async function limpiar(codigo: string) {
  const c = dbClient();
  await c.connect();
  try {
    await c.query("DELETE FROM files WHERE trip_id = $1", [codigo]);
    await c.query("DELETE FROM finanzas.facturas WHERE trip_id = $1", [codigo]);
    await c.query("DELETE FROM operaciones.trips WHERE codigo = $1", [codigo]);
  } finally {
    await c.end();
  }
}

test.describe("Anulación de viajes (A2)", () => {
  test("borrar con motivo → anulado en 'Ver anulados' → reactivar", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "admin", "Reactivar es solo admin");
    const codigo = `E2E-ANUL-${Date.now()}`;
    await seedTripConDocs(codigo);
    try {
      await page.goto("/viajes");
      await expect(page.getByRole("button", { name: /Nuevo viaje/i })).toBeVisible();

      // Borrar el viaje con motivo.
      const fila = page.locator("tr", { hasText: codigo });
      await expect(fila).toHaveCount(1);
      await fila.getByTitle("Eliminar viaje").click();
      await page.getByPlaceholder("Motivo (obligatorio)").fill("Viaje de prueba anulado");
      await page.getByRole("button", { name: "Eliminar", exact: true }).click();
      await expect(page.getByText("Viaje eliminado/anulado.")).toBeVisible();

      // Ya no aparece en la lista activa.
      await expect(page.locator("tr", { hasText: codigo })).toHaveCount(0);

      // Aparece en "Ver anulados" con el motivo.
      await page.getByRole("button", { name: "Ver anulados" }).click();
      const filaAnulada = page.locator("tr", { hasText: codigo });
      await expect(filaAnulada).toHaveCount(1);
      await expect(filaAnulada).toContainText("Viaje de prueba anulado");

      // Reactivar (admin).
      await filaAnulada.getByRole("button", { name: "Reactivar" }).click();
      await expect(page.getByText("Viaje reactivado.")).toBeVisible();
      await expect(page.locator("tr", { hasText: codigo })).toHaveCount(0);

      // Vuelve a la lista activa.
      await page.getByRole("button", { name: "Ver activos" }).click();
      await expect(page.locator("tr", { hasText: codigo })).toHaveCount(1);
    } finally {
      await limpiar(codigo);
    }
  });
});
