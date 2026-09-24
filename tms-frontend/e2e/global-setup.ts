import { request } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// Login ÚNICO por rol en globalSetup: evita el límite de 8 logins/5 min del backend.
// Guarda el JWT en storageState (localStorage['tms_jwt']) que reutilizan los tests.

const BASE = process.env.E2E_BASE_URL ?? "http://localhost:8080";
const AUTH_DIR = join(process.cwd(), ".auth");

function storageState(token: string): unknown {
  return {
    cookies: [],
    origins: [
      {
        origin: new URL(BASE).origin,
        localStorage: [{ name: "tms_jwt", value: token }],
      },
    ],
  };
}

export default async function globalSetup(): Promise<void> {
  const admin = {
    usuario: process.env.E2E_ADMIN_USER ?? "admin",
    contrasena: process.env.E2E_ADMIN_PASSWORD ?? "",
  };
  const dispatcher = {
    usuario: process.env.E2E_DISPATCHER_USER ?? "e2e_dispatcher",
    contrasena: process.env.E2E_DISPATCHER_PASSWORD ?? "e2e-dispatcher-1",
  };

  const ctx = await request.newContext({ baseURL: BASE });

  // 1. Login admin (única vez).
  const adminLogin = await ctx.post("/api/auth/login", { data: admin });
  if (!adminLogin.ok()) throw new Error(`login admin: ${adminLogin.status()}`);
  const { token: adminToken } = await adminLogin.json();

  // 2. Crear dispatcher (idempotente: 200 o 409).
  await ctx.post("/api/configuracion/usuarios", {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: {
      usuario: dispatcher.usuario,
      password: dispatcher.contrasena,
      rol: "dispatcher",
      nombre: "E2E Dispatcher",
    },
  });

  // 3. Login dispatcher (única vez).
  const dispLogin = await ctx.post("/api/auth/login", { data: dispatcher });
  if (!dispLogin.ok()) throw new Error(`login dispatcher: ${dispLogin.status()}`);
  const { token: dispToken } = await dispLogin.json();

  // 4. Usuario con cambio de contraseña obligatorio (idempotente: se resetea en cada run).
  const cambia = {
    usuario: process.env.E2E_CAMBIA_USER ?? "e2e_cambia",
    contrasena: process.env.E2E_CAMBIA_PASSWORD ?? "e2e-cambia-1",
  };
  await ctx.post("/api/configuracion/usuarios", {
    headers: { Authorization: `Bearer ${adminToken}` },
    data: {
      usuario: cambia.usuario,
      password: cambia.contrasena,
      rol: "dispatcher",
      nombre: "E2E Cambia Clave",
      debe_cambiar_clave: true,
    },
  });
  const lista = await ctx.get("/api/configuracion/usuarios", {
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  const { usuarios } = await lista.json();
  const uid = usuarios.find((u: { usuario: string }) => u.usuario === cambia.usuario)?.id;
  if (uid) {
    await ctx.put(`/api/configuracion/usuarios/${uid}`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: { password: cambia.contrasena, debe_cambiar_clave: true },
    });
  }

  mkdirSync(AUTH_DIR, { recursive: true });
  writeFileSync(join(AUTH_DIR, "admin.json"), JSON.stringify(storageState(adminToken)));
  writeFileSync(join(AUTH_DIR, "dispatcher.json"), JSON.stringify(storageState(dispToken)));

  await ctx.dispose();
}
