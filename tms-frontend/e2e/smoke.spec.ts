import { test, expect, type Page } from "@playwright/test";

// Smoke test de Fase 0: login como admin y como dispatcher, recorrido por todas
// las secciones visibles, 0 errores de consola y 0 respuestas HTTP >= 400.
// Credenciales por variable de entorno (nunca hardcodeadas): E2E_ADMIN_USER,
// E2E_ADMIN_PASSWORD, E2E_DISPATCHER_USER, E2E_DISPATCHER_PASSWORD.

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";

const ADMIN = {
  usuario: process.env.E2E_ADMIN_USER ?? "admin",
  contrasena: process.env.E2E_ADMIN_PASSWORD ?? "",
};

const DISPATCHER = {
  usuario: process.env.E2E_DISPATCHER_USER ?? "e2e_dispatcher",
  contrasena: process.env.E2E_DISPATCHER_PASSWORD ?? "e2e-dispatcher-1",
};

// Etiquetas visibles del ribbon por rol (antes del rediseño "Torre de control").
const SECCIONES: Record<string, string[]> = {
  admin: [
    "Operaciones", "Vehículos", "RRHH", "Contabilidad", "Gastos",
    "Documentos", "KPIs", "Mensajería", "Configuración",
  ],
  dispatcher: ["Operaciones", "Vehículos", "Gastos", "Documentos", "KPIs", "Mensajería"],
};

function track(page: Page) {
  const erroresConsola: string[] = [];
  const respuestas400: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") erroresConsola.push(m.text());
  });
  page.on("response", (r) => {
    if (r.status() >= 400) respuestas400.push(`${r.status()} ${r.url()}`);
  });
  return { erroresConsola, respuestas400 };
}

async function login(page: Page, usuario: string, contrasena: string) {
  await page.goto("/");
  await page.locator("#username").fill(usuario);
  await page.locator("#password").fill(contrasena);
  await page.locator('button[type="submit"]').click();
  await page.getByRole("button", { name: "Operaciones", exact: true }).waitFor({ timeout: 20_000 });
}

test.beforeAll(async ({ request }) => {
  // Asegura un usuario dispatcher para la prueba (idempotente) usando el admin.
  const login = await request.post(`${BASE}/api/auth/login`, {
    data: { usuario: ADMIN.usuario, contrasena: ADMIN.contrasena },
  });
  expect(login.ok(), `login admin para preparar el dispatcher (${login.status()})`).toBeTruthy();
  const { token } = await login.json();
  const crear = await request.post(`${BASE}/api/configuracion/usuarios`, {
    headers: { Authorization: `Bearer ${token}` },
    data: {
      usuario: DISPATCHER.usuario,
      password: DISPATCHER.contrasena,
      rol: "dispatcher",
      nombre: "E2E Dispatcher",
    },
  });
  expect([200, 409], "crear/obtener dispatcher").toContain(crear.status());
});

test("recorrido admin sin errores", async ({ page }) => {
  const { erroresConsola, respuestas400 } = track(page);
  await login(page, ADMIN.usuario, ADMIN.contrasena);
  for (const seccion of SECCIONES.admin) {
    await page.getByRole("button", { name: seccion, exact: true }).click();
    await page.waitForTimeout(600);
  }
  expect(erroresConsola, "errores de consola").toEqual([]);
  expect(respuestas400, "respuestas HTTP >= 400").toEqual([]);
});

test("recorrido dispatcher sin errores", async ({ page }) => {
  const { erroresConsola, respuestas400 } = track(page);
  await login(page, DISPATCHER.usuario, DISPATCHER.contrasena);
  for (const seccion of SECCIONES.dispatcher) {
    await page.getByRole("button", { name: seccion, exact: true }).click();
    await page.waitForTimeout(600);
  }
  expect(erroresConsola, "errores de consola").toEqual([]);
  expect(respuestas400, "respuestas HTTP >= 400").toEqual([]);
});
