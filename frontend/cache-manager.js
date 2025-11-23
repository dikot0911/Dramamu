/**
 * Automatic Cache Manager
 * Handles cache versioning and invalidation
 */

const CACHE_VERSION = 'dramamu-v1.0.0';
const CACHE_ASSETS = `${CACHE_VERSION}-assets`;

class CacheManager {
  constructor() {
    this.version = this.getVersion();
    this.storageKey = 'dramamu-cache-version';
    this.updateInterval = null;
    this.init();
  }

  getVersion() {
    // Version based on build timestamp
    const timestamp = new Date().getTime();
    const hash = Math.floor(Math.random() * 1000000).toString(36).toUpperCase();
    return `${timestamp}-${hash}`;
  }

  init() {
    // Register Service Worker
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
        .then(registration => {
          console.log('✅ Service Worker registered:', registration);
          
          // Check for updates every 24 hours
          this.updateInterval = setInterval(() => {
            registration.update();
          }, 24 * 60 * 60 * 1000);
        })
        .catch(error => {
          console.log('⚠️ Service Worker registration failed:', error);
        });
    }

    // Handle cache version changes
    this.checkCacheVersion();
  }
  
  cleanup() {
    if (this.updateInterval) {
      clearInterval(this.updateInterval);
      this.updateInterval = null;
    }
  }

  checkCacheVersion() {
    const storedVersion = localStorage.getItem(this.storageKey);
    
    if (storedVersion !== this.version) {
      console.log('🔄 Cache version changed, clearing old caches...');
      this.clearOldCaches();
      localStorage.setItem(this.storageKey, this.version);
    }
  }

  clearOldCaches() {
    if ('caches' in window) {
      caches.keys().then(cacheNames => {
        cacheNames.forEach(cacheName => {
          if (!cacheName.includes('dramamu')) {
            caches.delete(cacheName);
            console.log('🗑️ Deleted old cache:', cacheName);
          }
        });
      });
    }
  }

  // Force refresh all assets
  static forceRefresh() {
    if ('caches' in window) {
      caches.keys().then(cacheNames => {
        cacheNames.forEach(cacheName => {
          caches.delete(cacheName);
        });
      });
    }
    location.reload(true); // Hard refresh
  }

  // Pre-cache important assets
  static precacheAssets(urls = []) {
    if ('serviceWorker' in navigator && 'caches' in window) {
      caches.open(CACHE_ASSETS).then(cache => {
        urls.forEach(url => {
          cache.add(url).catch(err => {
            console.log('⚠️ Failed to precache:', url);
          });
        });
      });
    }
  }
}

// Initialize on page load
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    window.cacheManager = new CacheManager();
  });
} else {
  window.cacheManager = new CacheManager();
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
  if (window.cacheManager) {
    window.cacheManager.cleanup();
  }
});

window.addEventListener('pagehide', () => {
  if (window.cacheManager) {
    window.cacheManager.cleanup();
  }
});
