const HUGCIVI_CACHE = "hugcivi-static-v1";
const HUGCIVI_STATIC_ASSETS = [
  "/manifest.webmanifest",
  "/static/style.css",
  "/static/icons/hugcivi.svg",
  "/static/icons/hugcivi-180.png",
  "/static/icons/hugcivi-192.png",
  "/static/icons/hugcivi-512.png",
  "/static/icons/hugcivi-maskable-512.png",
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(HUGCIVI_CACHE).then(cache => cache.addAll(HUGCIVI_STATIC_ASSETS)).catch(() => undefined)
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(key => key !== HUGCIVI_CACHE).map(key => caches.delete(key))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const cacheable =
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/static/style.css" ||
    url.pathname.startsWith("/static/icons/");

  if (!cacheable) return;

  event.respondWith(
    caches.match(request).then(cached => {
      if (cached) return cached;
      return fetch(request).then(response => {
        const copy = response.clone();
        caches.open(HUGCIVI_CACHE).then(cache => cache.put(request, copy)).catch(() => undefined);
        return response;
      });
    })
  );
});
