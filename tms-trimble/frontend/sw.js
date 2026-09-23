/* Service worker del TMS — habilita la instalación como app y el trabajo offline */
const CACHE = "tms-v5";

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // API y estado: red únicamente, no se cachea
  if (url.pathname.startsWith("/api/")) return;

  // Navegación (HTML): red primero para servir siempre la última versión,
  // con el documento cacheado como respaldo offline.
  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp.status === 200) {
            const clone = resp.clone();
            caches.open(CACHE).then((c) => c.put("/", clone));
          }
          return resp;
        })
        .catch(() => caches.match("/"))
    );
    return;
  }

  // Estáticos (app.js, style.css, iconos): red primero, cache solo como respaldo offline.
  e.respondWith(
    fetch(req)
      .then((resp) => {
        if (resp && resp.status === 200) {
          const clone = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, clone));
        }
        return resp;
      })
      .catch(() => caches.match(req))
  );
});
