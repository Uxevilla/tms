import { useState, type FormEvent } from "react";
import { LogIn, KeyRound } from "lucide-react";
import { AUTH_LOGIN, CHANGE_PASSWORD } from "../config";

interface LoginProps {
  onLogin: (token: string) => void;
}

export function Login({ onLogin }: LoginProps) {
  const [usuario, setUsuario] = useState("");
  const [contrasena, setContrasena] = useState("");
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);
  // Cambio de contraseña forzado (cuando el backend marca debe_cambiar_clave).
  const [tokenPendiente, setTokenPendiente] = useState("");
  const [nuevaClave, setNuevaClave] = useState("");
  const [confirmarClave, setConfirmarClave] = useState("");

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setCargando(true);
    try {
      const res = await fetch(AUTH_LOGIN, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ usuario, contrasena }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.token) {
        if (data.debe_cambiar_clave) {
          setTokenPendiente(data.token);
          setNuevaClave("");
          setConfirmarClave("");
        } else {
          onLogin(data.token);
        }
      } else {
        setError(data?.detail?.error || data?.error || "Credenciales incorrectas");
      }
    } catch {
      setError("No se pudo conectar con el servidor");
    } finally {
      setCargando(false);
    }
  }

  async function onChangePassword(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (nuevaClave.length < 12) {
      setError("La nueva contraseña debe tener al menos 12 caracteres.");
      return;
    }
    if (nuevaClave !== confirmarClave) {
      setError("Las contraseñas no coinciden.");
      return;
    }
    setCargando(true);
    try {
      const res = await fetch(CHANGE_PASSWORD, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokenPendiente}` },
        body: JSON.stringify({ old_password: contrasena, new_password: nuevaClave }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) {
        onLogin(tokenPendiente);
      } else {
        setError(data?.detail?.error || data?.error || "No se pudo cambiar la contraseña");
      }
    } catch {
      setError("No se pudo conectar con el servidor");
    } finally {
      setCargando(false);
    }
  }

  if (tokenPendiente) {
    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-100">
        <form
          onSubmit={onChangePassword}
          className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm"
        >
          <div className="mb-6 flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-500 text-white">
              <KeyRound size={20} />
            </span>
            <div>
              <h1 className="text-lg font-semibold text-slate-800">Cambio de contraseña obligatorio</h1>
              <p className="text-xs text-slate-500">Por seguridad, define una nueva contraseña (mín. 12 caracteres).</p>
            </div>
          </div>

          <label className="mb-3 block text-sm font-medium text-slate-700">
            Nueva contraseña
            <input
              type="password"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              value={nuevaClave}
              onChange={(e) => setNuevaClave(e.target.value)}
              autoFocus
            />
          </label>

          <label className="mb-4 block text-sm font-medium text-slate-700">
            Confirmar contraseña
            <input
              type="password"
              className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              value={confirmarClave}
              onChange={(e) => setConfirmarClave(e.target.value)}
            />
          </label>

          {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={cargando || !nuevaClave || !confirmarClave}
            className="w-full rounded-lg bg-amber-500 py-2 text-sm font-semibold text-white hover:bg-amber-600 disabled:opacity-50"
          >
            {cargando ? "Guardando…" : "Guardar y continuar"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen items-center justify-center bg-slate-100">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-8 shadow-sm"
      >
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white">
            <LogIn size={20} />
          </span>
          <div>
            <h1 className="text-lg font-semibold text-slate-800">TMS — Operaciones</h1>
            <p className="text-xs text-slate-500">Inicia sesión para continuar</p>
          </div>
        </div>

        <label className="mb-3 block text-sm font-medium text-slate-700">
          Usuario
          <input
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            value={usuario}
            onChange={(e) => setUsuario(e.target.value)}
            autoFocus
          />
        </label>

        <label className="mb-4 block text-sm font-medium text-slate-700">
          Contraseña
          <input
            type="password"
            className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
            value={contrasena}
            onChange={(e) => setContrasena(e.target.value)}
          />
        </label>

        {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={cargando || !usuario || !contrasena}
          className="w-full rounded-lg bg-blue-600 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {cargando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}
