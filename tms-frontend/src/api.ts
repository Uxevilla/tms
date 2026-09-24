// Cliente de API único: centraliza cabeceras, token y manejo de errores.
// Sustituye a las llamadas `fetch` dispersas con `Authorization` repetida a mano.
import { getToken } from "./auth";

/** Error de API con el código de estado y el cuerpo (para ramificar por 400/403/409 o leer `detail`). */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = unknown>(url: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getToken() ?? ""}`,
      ...init.headers,
    },
  });
  const data = r.status === 204 ? undefined : await r.json().catch(() => null);
  if (r.status === 401) {
    window.dispatchEvent(new Event("tms:sesion-caducada"));
    throw new ApiError("Sesión caducada", 401, data);
  }
  if (r.status === 403) throw new ApiError("No tienes permiso para ver esto", 403, data);
  if (!r.ok) {
    const detail = (data as { detail?: { error?: string } } | null)?.detail?.error;
    throw new ApiError(detail ?? `Error ${r.status}`, r.status, data);
  }
  return data as T;
}
