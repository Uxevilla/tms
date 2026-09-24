import { Link } from "@tanstack/react-router";

/** Página 404 propia dentro del AppShell (Fase 2), con enlace a la torre. */
export function NotFound() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
      <div className="text-5xl font-bold text-muted-foreground">404</div>
      <div className="text-lg font-semibold">Página no encontrada</div>
      <p className="text-sm text-muted-foreground">La ruta que buscas no existe o ya no está disponible.</p>
      <Link
        to="/torre"
        className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
      >
        Volver a la torre de control
      </Link>
    </div>
  );
}
