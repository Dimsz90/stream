const CACHE_NAME = "StreamVault-v1.9";

const PRECACHE = [
  "/extractor.html",
  "/manifest.json",
  "/css/style.css",
  "/js/app.js",
  "/js/anime-fullscreen.js",
  "https://fonts.googleapis.com/css2?family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Bebas+Neue&family=DM+Sans:wght@300;400;500;600&display=swap",
  "https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.7/hls.min.js",
];

// Domain CDN yang harus bypass SW sepenuhnya.
const BYPASS_HOSTS = [
  "fizzopic.org",
  "tiktokcdn.com",
  "muscdn.com",
  "byteimg.com",
  "bytedance.com",
  "sgsnssdk.com",
  "r2.dev",
  "cloudflarestorage.com",
  "wibufile.com",
  "samehadaku.care",
  "samehadaku.how",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(PRECACHE).catch((err) =>
        console.warn("[SW] Precache partial fail:", err)
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // 1. Bypass video/audio media & range requests
  if (
    event.request.destination === "video" ||
    event.request.destination === "audio" ||
    event.request.headers.has("range") ||
    /\.(mp4|m3u8|ts|mkv|webm|mp3|m4a)(\?.*)?$/i.test(url.pathname)
  ) {
    return;
  }

  // 2. CDN & R2 bypass → jangan intercept, biarkan browser fetch langsung
  if (BYPASS_HOSTS.some((h) => url.hostname === h || url.hostname.endsWith("." + h))) {
    return;
  }

  // 3. API calls → network only
  if (url.pathname.startsWith("/api/")) return;

  if (event.request.mode === "navigate" || event.request.destination === "document") {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => cached);

      return cached || fetchPromise;
    })
  );
});
