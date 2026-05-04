// SSG Operations Center — Service Worker
// ⚠️ Incrementar CACHE_VERSION a cada deploy para forçar atualização nos clientes
const CACHE_VERSION = '20260504-2';
const CACHE = 'ssg-' + CACHE_VERSION;

// Apenas assets imutáveis vão pro precache (libs, ícones).
// HTML/JSON ficam fora — sempre buscam da rede pra evitar versões presas.
const PRECACHE = [
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './vendor/chart.min.js',
  './vendor/xlsx.full.min.js'
];

self.addEventListener('install', function(e) {
  e.waitUntil(
    caches.open(CACHE).then(function(c) { return c.addAll(PRECACHE); })
  );
  self.skipWaiting();
});

self.addEventListener('activate', function(e) {
  e.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.filter(function(k){ return k!==CACHE; }).map(function(k){ return caches.delete(k); }));
    })
  );
  self.clients.claim();
});

// Mensagem do client pode forçar skipWaiting (útil pra atualização instantânea)
self.addEventListener('message', function(e){
  if(e.data && e.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', function(e) {
  var url = e.request.url;

  // 1) HTML e JSON dinâmicos: NETWORK-FIRST
  //    (index.html, sw.js, qualquer .json — sempre busca rede primeiro;
  //    cache só serve como fallback se rede falhar)
  var isHtmlOrJson = url.endsWith('/') ||
                     url.endsWith('.html') ||
                     url.endsWith('.json') ||
                     url.endsWith('/sw.js') ||
                     e.request.mode === 'navigate' ||
                     (e.request.headers.get('accept') || '').indexOf('text/html') !== -1;

  if(isHtmlOrJson){
    e.respondWith(
      fetch(e.request).then(function(r){
        // Atualiza cache em background (apenas se response OK)
        if(r && r.ok){
          var rc = r.clone();
          caches.open(CACHE).then(function(c){ c.put(e.request, rc); });
        }
        return r;
      }).catch(function(){
        return caches.match(e.request);
      })
    );
    return;
  }

  // 2) Assets imutáveis (libs, ícones, fontes): CACHE-FIRST
  //    (chart.min.js, xlsx.full.min.js, ícones — versionados no PRECACHE)
  e.respondWith(
    caches.match(e.request).then(function(r){
      return r || fetch(e.request).then(function(nr){
        if(nr && nr.ok){
          var rc2 = nr.clone();
          caches.open(CACHE).then(function(c){ c.put(e.request, rc2); });
        }
        return nr;
      });
    })
  );
});
