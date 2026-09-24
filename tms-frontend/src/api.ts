// Cliente de API único: centraliza cabeceras, token y manejo de errores.
// Sustituye a las llamadas `fetch` dispersas con `Authorization` repetida a mano.
import { getToken } from "./auth";

export async function api<T = unknown>(url: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken() ?? ""}`,
      ...init.headers,
    },
  });
  if (r.status === 401) {
    window.dispatchEvent(new Event("tms:sesion-caducada"));
    throw new Error("Sesión caducada");
  }
  if (r.status === 403) throw new Error("No tienes permiso para ver esto");
  if (!r.ok) {
    const data = await r.json().catch(() => null);
    throw new Error(data?.detail?.error ?? `Error ${r.status}`);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}
