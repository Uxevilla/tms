import { test, expect, type Page } from "@playwright/test";
import { Client } from "pg";

// Auth vía storageState (global-setup). El backend corre en modo falso de Trimble.

function dbClient(): Client {
  return new Client({
    host: process.env.DB_HOST ?? "127.0.0.1",
    port: Number(process.env.DB_PORT ?? 5432),
    user: process.env.DB_USER ?? "tms",
    password: process.env.DB_PASSWORD ?? "tms",
    database: process.env.DB_NAME ?? "tms",
  });
}

async function insertarViaje(codigo: string, cliente: string, precio: number, iva = 21) {
  const c = dbClient();
  await c.connect();
  try {
    await c.query(
      "INSERT INTO operaciones.trips (codigo, estado, cliente, precio, iva, creado) VALUES ($1, 'Entregado', $2, $3, $4, '2026-09-01')",
      [codigo, cliente, precio, iva],
    );
  } finally {
    await c.end();
  }
}

async function limpiar(codigos: string[]) {
  const c = dbClient();
  await c.connect();
  try {
    for (const cod of codigos) {
      await c.query("DELETE FROM finanzas.factura_lineas WHERE trip_id = $1", [cod]);
      await c.query("DELETE FROM finanzas.facturas WHERE trip_id = $1", [cod]);
      await c.query("DELETE FROM operaciones.trips WHERE codigo = $1", [cod]);
    }
  } finally {
    await c.end();
  }
}

async function facturaNumero(codigo: string): Promise<{ numero: string; asiento_id: number | null }> {
  const c = dbClient();
  await c.connect();
  try {
    const r = await c.query(
      "SELECT numero, asiento_id FROM finanzas.facturas WHERE trip_id = $1 AND estado <> 'borrador' ORDER BY id DESC LIMIT 1",
      [codigo],
    );
    return r.rows[0] ?? { numero: "", asiento_id: null };
  } finally {
    await c.end();
  }
}

async function cuentasAsiento(asientoId: number): Promise<string[]> {
  const c = dbClient();
  await c.connect();
  try {
    const r = await c.query("SELECT cuenta FROM finanzas.apuntes WHERE asiento_id = $1", [asientoId]);
    return r.rows.map((x) => x.cuenta);
  } finally {
    await c.end();
  }
}

test("entregar viaje → Pendientes → facturar → F-<año>-NNNN con asiento 430/705/477", async ({ page }) => {
  const codigo = `E2E-FACT-${Date.now()}`;
  await insertarViaje(codigo, "E2E-Cliente-Fact", 200);

  await page.goto("/facturacion");
  await expect(page.getByText("Pendientes", { exact: false })).toBeVisible();

  // El viaje aparece en "Viajes pendientes de facturar".
  const fila = page.locator("tr", { hasText: codigo });
  await expect(fila).toBeVisible();

  // Facturar → diálogo de confirmación → Confirmar.
  await fila.getByRole("button", { name: "Facturar" }).click();
  await page.getByRole("button", { name: "Confirmar", exact: true }).click();

  // Número F-<año actual>-NNNN en el toast (autocierre 5 s).
  const anio = new Date().getFullYear();
  await expect(page.getByText(new RegExp(`F-${anio}-\\d{4}`))).toBeVisible();

  // Asiento de venta 430/705/477 creado en BD.
  const f = await facturaNumero(codigo);
  expect(f.numero).toMatch(new RegExp(`^F-${anio}-\\d{4}$`));
  expect(f.asiento_id).not.toBeNull();
  const cuentas = await cuentasAsiento(f.asiento_id!);
  expect(cuentas).toEqual(expect.arrayContaining(["430", "705", "477"]));

  await limpiar([codigo]);
});

test("cobrar factura emitida → asiento 572/430", async ({ page }) => {
  const codigo = `E2E-COB-${Date.now()}`;
  await insertarViaje(codigo, "E2E-Cliente-Cob", 150);

  await page.goto("/facturacion");
  const fila = page.locator("tr", { hasText: codigo });
  await expect(fila).toBeVisible();
  await fila.getByRole("button", { name: "Facturar" }).click();
  await page.getByRole("button", { name: "Confirmar", exact: true }).click();
  await expect(page.getByText(new RegExp(`F-${new Date().getFullYear()}-\\d{4}`))).toBeVisible();

  // Pestaña Emitidas → cobrar.
  await page.getByRole("button", { name: /Emitidas/ }).click();
  const f = await facturaNumero(codigo);
  const filaEmitida = page.locator("tr", { hasText: f.numero });
  await expect(filaEmitida).toBeVisible();
  await filaEmitida.getByRole("button", { name: "Marcar cobrada" }).click();
  await page.getByRole("button", { name: "Confirmar", exact: true }).click();
  await expect(page.getByText("Factura marcada como cobrada.")).toBeVisible();

  // Asiento de cobro 572/430.
  await expect.poll(async () => {
    const c = dbClient();
    await c.connect();
    try {
      const r = await c.query(
        "SELECT a.id FROM finanzas.asientos a WHERE a.origen = 'cobro' AND a.documento = $1",
        [f.numero],
      );
      if (r.rows.length === 0) return null;
      return cuentasAsiento(r.rows[0].id);
    } finally {
      await c.end();
    }
  }).toEqual(expect.arrayContaining(["572", "430"]));

  await limpiar([codigo]);
});

test("agrupar viajes de 2 clientes distintos → error visible", async ({ page }) => {
  const a = `E2E-AGR-A-${Date.now()}`;
  const b = `E2E-AGR-B-${Date.now()}`;
  await insertarViaje(a, "E2E-Cliente-A", 100);
  await insertarViaje(b, "E2E-Cliente-B", 100);

  await page.goto("/facturacion");
  await expect(page.getByText("Pendientes", { exact: false })).toBeVisible();

  // Seleccionar ambos viajes (checkbox de cada fila).
  await page.locator("tr", { hasText: a }).getByRole("checkbox").check();
  await page.locator("tr", { hasText: b }).getByRole("checkbox").check();

  // Facturar seleccionados → confirmar → error 409 visible en el toast.
  await page.getByRole("button", { name: /Facturar 2/ }).click();
  await page.getByRole("button", { name: "Confirmar", exact: true }).click();
  await expect(page.getByText("Viajes de clientes distintos.")).toBeVisible();

  await limpiar([a, b]);
});
