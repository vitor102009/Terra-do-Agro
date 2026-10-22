/* Service Worker do Terra do Agro — versão {{ versao }}
   Estratégia:
   - Arquivos estáticos (CSS, JS, ícones, fontes): cache primeiro, versionado.
   - Páginas (navegação): sempre da rede. Páginas privadas NUNCA são guardadas em cache.
     Sem conexão, mostra /offline (genérica) ou /app-offline (casca do modo offline).
   - API e mídia: somente rede. Os dados offline ficam no IndexedDB, isolados por usuário. */
"use strict";

const VERSAO = "{{ versao }}";
const CACHE_ESTATICO = "terra-estatico-" + VERSAO;
const ARQUIVOS = {{ arquivos|safe }};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_ESTATICO)
      .then((cache) => cache.addAll(ARQUIVOS.map((u) => new Request(u, { cache: "reload", credentials: "same-origin" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((nomes) => Promise.all(nomes.filter((n) => n.startsWith("terra-") && n !== CACHE_ESTATICO).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

function ehEstatico(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (ehEstatico(url)) {
    event.respondWith(
      caches.match(req, { ignoreSearch: true }).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((resp) => {
          if (resp.ok && resp.type === "basic") {
            const copia = resp.clone();
            caches.open(CACHE_ESTATICO).then((c) => c.put(req, copia));
          }
          return resp;
        });
      })
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => {
        if (url.pathname === "/app-offline") return caches.match("/app-offline");
        return caches.match("/offline");
      })
    );
    return;
  }

  if (url.pathname === "/manifest.webmanifest") {
    event.respondWith(fetch(req).catch(() => caches.match(req)));
  }
  // Demais requisições (API, mídia, JSON): somente rede, sem cache.
});

self.addEventListener("message", (event) => {
  if (event.data === "skipWaiting") self.skipWaiting();
});
