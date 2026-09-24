import { useEffect, useRef, useState } from "react";
import type { WsStatus } from "../types";

/**
 * Hook genérico de WebSocket con reconexión automática (backoff fijo de 3s).
 *
 * - `onMessage` se guarda en una ref, de modo que el handler pueda cambiar
 *   (p. ej. cerrarse sobre el gridApi una vez montado el grid) sin re-suscribir
 *   la conexión.
 * - Devuelve el estado de conexión para la UI (Topbar).
 */
export function useWebSocket<T>(url: string, onMessage: (data: T) => void): WsStatus {
  const [status, setStatus] = useState<WsStatus>("conectando");
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: number | undefined;
    let disposed = false;

    const connect = () => {
      setStatus("conectando");
      ws = new WebSocket(url);

      ws.onopen = () => setStatus("conectado");

      ws.onmessage = (ev) => {
        try {
          handlerRef.current(JSON.parse(ev.data) as T);
        } catch {
          // Mensaje no-JSON: se ignora silenciosamente.
        }
      };

      ws.onclose = (ev) => {
        if (disposed) return;
        // Código 1008 (policy violation): el servidor cerró por token inválido/caducado.
        // No reintentar en bucle: notificar la caducidad de sesión y parar.
        if (ev.code === 1008) {
          window.dispatchEvent(new Event("tms:sesion-caducada"));
          return;
        }
        setStatus("desconectado");
        retryTimer = window.setTimeout(connect, 3000);
      };

      ws.onerror = () => ws?.close();
    };

    connect();

    return () => {
      disposed = true;
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      ws?.close();
    };
  }, [url]);

  return status;
}
