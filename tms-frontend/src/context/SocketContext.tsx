import { createContext, useCallback, useContext, useRef, type ReactNode } from "react";
import { useWebSocket } from "../hooks/useWebSocket";
import type { ViajeEvent, WsStatus } from "../types";
import { WS_OPERACIONES } from "../config";

/**
 * Proveedor del socket de operaciones: UNA sola conexión WS para toda la app,
 * con patrón pub/sub. El Topbar lee el estado (useSocketStatus) y el Grid se
 * suscribe a los eventos (useSocketSubscribe) para aplicar transacciones.
 *
 * La conexión vive a nivel de app (no dentro del Grid) para que permanezca
 * activa aunque el usuario navegue a otra sección.
 */

const WS_URL = WS_OPERACIONES;

type Listener = (event: ViajeEvent) => void;

const StatusContext = createContext<WsStatus>("desconectado");
const SubscribeContext = createContext<(fn: Listener) => () => void>(() => () => {});

export function SocketProvider({ children, token }: { children: ReactNode; token: string }) {
  const listeners = useRef(new Set<Listener>());

  // Emite cada evento a todos los suscriptores (sin crear conexiones extra).
  const emit = useCallback((event: ViajeEvent) => {
    listeners.current.forEach((fn) => {
      try {
        fn(event);
      } catch (err) {
        console.error("[ws] error en listener:", err);
      }
    });
  }, []);

  const status = useWebSocket<ViajeEvent>(`${WS_OPERACIONES}?token=${encodeURIComponent(token)}`, emit);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  return (
    <StatusContext.Provider value={status}>
      <SubscribeContext.Provider value={subscribe}>{children}</SubscribeContext.Provider>
    </StatusContext.Provider>
  );
}

export const useSocketStatus = () => useContext(StatusContext);
export const useSocketSubscribe = () => useContext(SubscribeContext);
