const CACHE_VERSION = 'dramamu-v1.0.0';
const CACHE_ASSETS = `${CACHE_VERSION}-assets`;
const CACHE_PAGES = `${CACHE_VERSION}-pages`;

const ASSETS_TO_CACHE = [
  '/premium-styles.css',
  '/premium-icons.js',
  '/premium-init.js',
  '/navbar.js',
  '/config.js',
  '/cache-manager.js'
];

const PAGES_TO_CACHE = [
  '/',
  '/home.html',
  '/drama.html',
  '/profil.html',
  '/favorit.html',
  '/request.html'
];

// Install event - cache assets
self.addEventListener('install', event => {
  console.log('🔧 Service Worker installing...');
  event.waitUntil(
    caches.open(CACHE_ASSETS).then(cache => {
      console.log('📦 Caching assets...');
      return cache.addAll(ASSETS_TO_CACHE).catch(err => {
        console.log('⚠️ Some assets could not be cached:', err);
      });
    })
  );
  self.skipWaiting();
});

// Activate event - clean old caches
self.addEventListener('activate', event => {
  console.log('✨ Service Worker activating...');
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_ASSETS && cacheName !== CACHE_PAGES && !cacheName.includes('dramamu')) {
            console.log('🗑️ Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch event - smart caching strategy
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip external domains
  if (url.origin !== location.origin) {
    return;
  }

  // Strategy 1: HTML Pages - Network First
  if (request.headers.get('accept')?.includes('text/html') || 
      url.pathname === '/' || 
      url.pathname.endsWith('.html')) {
    event.respondWith(networkFirstStrategy(request));
    return;
  }

  // Strategy 2: Assets (CSS, JS) - Cache First
  if (url.pathname.endsWith('.css') || 
      url.pathname.endsWith('.js')) {
    event.respondWith(cacheFirstStrategy(request));
    return;
  }

  // Strategy 3: Images - Cache First with network fallback
  if (url.pathname.match(/\.(png|jpg|jpeg|gif|svg|webp)$/i)) {
    event.respondWith(cacheFirstStrategy(request));
    return;
  }

  // Default: Network First
  event.respondWith(networkFirstStrategy(request));
});

// Network First Strategy
async function networkFirstStrategy(request) {
  try {
    const response = await fetch(request);
    if (response && response.status === 200) {
      const cache = await caches.open(CACHE_PAGES);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.log('📡 Network failed, using cache:', request.url);
    const cached = await caches.match(request);
    return cached || new Response('Offline - page not available', { status: 503 });
  }
}

// Cache First Strategy
async function cacheFirstStrategy(request) {
  const cached = await caches.match(request);
  if (cached) {
    return cached;
  }

  try {
    const response = await fetch(request);
    if (response && response.status === 200) {
      const cache = await caches.open(CACHE_ASSETS);
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    console.log('📡 Network failed for:', request.url);
    return new Response('Resource not available offline', { status: 503 });
  }
}

console.log('✅ Service Worker loaded successfully');
