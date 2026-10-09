// Service Worker dédié au formulaire hors-ligne "Suivi de Bétonnage".
// Objectif unique : permettre à la page offline_betonnage.html de se
// recharger même quand l'appareil n'a AUCUN réseau (pas de 4G/wifi).
// Il ne touche jamais aux requêtes POST envoyées vers Supabase — celles-ci
// passent directement, sans interception ni mise en cache.

const CACHE_NAME = "betonnage-offline-v1";
const FICHIERS_A_METTRE_EN_CACHE = [
  "offline_betonnage.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(FICHIERS_A_METTRE_EN_CACHE))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((noms) =>
      Promise.all(
        noms
          .filter((nom) => nom !== CACHE_NAME)
          .map((nom) => caches.delete(nom))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  // Ne JAMAIS intercepter les envois vers Supabase (POST/PUT/PATCH/DELETE) :
  // seule la page elle-même (chargement GET) doit être mise en cache.
  if (event.request.method !== "GET") return;

  // Ne pas non plus intercepter les appels vers un autre domaine (Supabase,
  // CDN, etc.) — uniquement la page hors-ligne elle-même.
  if (new URL(event.request.url).origin !== self.location.origin) return;

  event.respondWith(
    fetch(event.request)
      .then((reponse) => {
        const copie = reponse.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copie));
        return reponse;
      })
      .catch(() => caches.match(event.request))
  );
});
