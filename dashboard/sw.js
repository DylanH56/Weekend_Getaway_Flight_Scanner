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
 * Update flow:
 *   * Every SW version bump triggers the browser's SW update check
 *     on the next navigation. We call skipWaiting() during install
 *     so the new SW activates immediately, then clients.claim() in
 *     activate so every open tab is controlled by the new worker.
 *   * app.js listens for the "controllerchange" event and auto-
 *     reloads the page once, so the user sees new HTML/CSS/JS
 *     without having to manually hard-refresh.
 *
 * On install: pre-cache the shell. On activate: clean up old caches.
 * No push notifications or background sync (Discord alerts still
 * come through normally since those run server-side).
 */
// Bump this whenever the dashboard shell (html/css/js) changes so
// users pick up the new files on next reload. Old caches get pruned
// in the activate hook.
const CACHE_VERSION = "v13-cap200-horizon39";
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
  // cache.addAll with `{cache: "reload"}` Request options bypasses
  // the HTTP cache so the install step ALWAYS gets fresh copies of
  // the shell files from the origin server. Without this, an
  // intermediate CDN or browser HTTP cache could serve stale files
  // into the new SW's shell cache, defeating the whole point of
  // bumping CACHE_VERSION.
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => {
      const freshRequests = SHELL_FILES.map(
        (url) => new Request(url, { cache: "reload" })
      );
      return cache.addAll(freshRequests);
    }).then(() => self.skipWaiting())
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

// Allow the page to ask the SW to skip waiting immediately if, for
// whatever reason, it got stuck in the `waiting` state instead of
// activating. The page sends {type: "SKIP_WAITING"} in app.js's
// SW update handler.
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // deals.json and history.json: network-first with cache fallback.
  // Both are updated on every scan and we always want the freshest
  // version if the network is up.
  if (
    url.pathname.endsWith("/deals.json") ||
    url.pathname.endsWith("/history.json")
  ) {
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

  // Shell files: network-first so the newest HTML/CSS/JS always
  // wins when the user reloads. Cache is the fallback for offline
  // use only. This is the OPPOSITE of the old cache-first strategy,
  // which was the reason stale Phase 1/2 features weren't reaching
  // users after deploy -- the cache kept serving ancient app.js
  // until the user manually hard-refreshed.
  event.respondWith(
    fetch(request)
      .then((resp) => {
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(SHELL_CACHE).then((cache) => cache.put(request, copy));
        }
        return resp;
      })
      .catch(() => caches.match(request))
  );
});
