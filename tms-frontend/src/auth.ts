// Gestión del JWT en localStorage (mínimo, sin librerías externas).

const TOKEN_KEY = "tms_jwt";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

// Decodifica el payload del JWT (base64url) y comprueba la expiración (exp).
export function isTokenValid(token: string | null): boolean {
  if (!token) return false;
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return typeof payload.exp === "number" && payload.exp * 1000 > Date.now();
  } catch {
    return false;
  }
}

// Devuelve el rol del JWT (para gating de UI), o null si no hay token válido.
export function getRol(): string | null {
  const token = getToken();
  if (!token) return null;
  try {
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return payload.rol ?? payload.usuario ?? null;
  } catch {
    return null;
  }
}
