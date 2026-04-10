/* Weekend Getaway Flight Scanner - service worker.
 *
 * Strategy:
 *   - Static assets (html / css / js / manifest / icon): cache-first.
 *     The app shell is tiny and stable, so we can load it instantly
 *     offline.
 *   - deals.json: network-first, fall back to cache. Always try to
 *     get the freshest scan when the user opens the app; if offline,
 *     render whatever we last stored.
 *   - Wikipedia thumbnails: stale-while-revalidate, so photos appear
 *     immediately from cache and refresh in the background.
 *
 * On install: pre-cache the shell. On activate: clean up old caches.
 * No push notifications or background sync (Discord alerts still
 * come through normally since those run server-side).
 */
// Bump this whenever the dashboard shell (html/css/js) changes so
// users pick up the new files on next reload. Old caches get pruned
// in the activate hook.
const CACHE_VERSION = "v2-bhx";
const SHELL_CACHE = `shell-${CACHE_VERSION}`;
const DATA_CACHE = `data-${CACHE_VERSION}`;
const PHOTO_CACHE = `photos-${CACHE_VERSION}`;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./styles.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icon.svg",
  // Leaflet CDN assets are cached opportunistically on first network
  // hit rather than pre-cached, because they're versioned and we
  // don't want to burn install time on them.
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_FILES))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => ![SHELL_CACHE, DATA_CACHE, PHOTO_CACHE].includes(k))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // deals.json: network-first with cache fallback.
  if (url.pathname.endsWith("/deals.json")) {
    event.respondWith(
      fetch(request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(DATA_CACHE).then((cache) => cache.put(request, copy));
          return resp;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Wikipedia photos: stale-while-revalidate.
  if (url.hostname.endsWith("wikimedia.org")) {
    event.respondWith(
      caches.open(PHOTO_CACHE).then((cache) =>
        cache.match(request).then((cached) => {
          const fetchPromise = fetch(request)
            .then((resp) => {
              cache.put(request, resp.clone());
              return resp;
            })
            .catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // Shell files and everything else: cache-first.
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((resp) => {
        // Opportunistically cache same-origin GETs so a second visit
        // to styles.css or app.js is instant.
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return resp;
      });
    })
  );
});
