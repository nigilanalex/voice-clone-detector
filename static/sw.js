const CACHE_NAME = "voiceguard-shell-v6";
const APP_SHELL = [
    "/",
    "/static/styles.css",
    "/static/app.js",
    "/static/pcm-worklet.js",
    "/static/manifest.webmanifest",
    "/static/icon.svg",
    "/static/icon-192.png",
    "/static/icon-512.png",
    "/static/apple-touch-icon.png",
    "/static/legal.css",
    "/privacy",
    "/consent",
    "/disclaimer"
];

self.addEventListener("install", (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    const requestUrl = new URL(event.request.url);
    if (
        event.request.method !== "GET"
        || requestUrl.origin !== self.location.origin
        || requestUrl.pathname.startsWith("/api/")
        || requestUrl.pathname.startsWith("/ws/")
    ) {
        return;
    }

    if (event.request.mode === "navigate") {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put("/", copy));
                    return response;
                })
                .catch(() => caches.match("/"))
        );
        return;
    }

    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) return cached;
            return fetch(event.request).then((response) => {
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
                }
                return response;
            });
        })
    );
});
