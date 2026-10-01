/**
 * Service Worker — minimal implementation required for PWA installability
 * Handles install/activate lifecycle. No offline caching (the app needs live network).
 */

const VERSION = 'v2';

self.addEventListener('install', (event) => {
    console.log('[SW] Installing', VERSION);
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('[SW] Activated', VERSION);
    event.waitUntil(self.clients.claim());
});

// Pass GET requests through — no caching, app requires live network.
//
// Everything that is not a GET (uploads above all) returns WITHOUT respondWith, so the
// browser sends it on its own network path. Re-issuing a multipart POST through
// fetch(event.request) loses the body on Safari/iOS 26.5+: the request arrives with the
// multipart Content-Type and Content-Length: 0 (WebKit bug 319396, duplicate of 319985).
// Measured on ica 2026-09-30: 19 iPhone uploads (Safari 26.6.2), every one empty, 400.
self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    event.respondWith(fetch(event.request));
});
