const CACHE_NAME = 'Smart-control-beton-v2';

// URLs relatives exactes selon l'arborescence de votre projet
const urlsToCache = [
  './',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './static/offline_betonnage.html',
  './static/sw-betonnage.js'
];

// Installation du Service Worker et mise en cache
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(urlsToCache);
    })
  );
  self.skipWaiting();
});

// Activation et nettoyage des anciens caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            return caches.delete(cache);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Gestion des requêtes réseau (Priorité réseau + exclusion WebSockets Streamlit)
self.addEventListener('fetch', (event) => {
  // Ne pas intercepter les requêtes internes et WebSockets de Streamlit
  if (event.request.url.includes('_stcore') || event.request.url.includes('stream')) {
    return;
  }

  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
