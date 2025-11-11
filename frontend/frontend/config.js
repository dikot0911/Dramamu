// Config API buat deployment
// Otomatis detect environment dan pake URL yang sesuai

const API_CONFIG = {
    // URL backend production (Render) - ganti sama URL Render lo abis deploy
    PRODUCTION_API_URL: 'https://your-app-name.onrender.com',
    
    // URL buat development local
    DEVELOPMENT_API_URL: window.location.origin
};

// Auto-detect environment dari hostname
function getApiBaseUrl() {
    const hostname = window.location.hostname;
    
    // Kalo di Netlify (production)
    if (hostname.includes('netlify.app') || hostname.includes('your-custom-domain.com')) {
        return API_CONFIG.PRODUCTION_API_URL;
    }
    
    // Kalo di development (localhost)
    return API_CONFIG.DEVELOPMENT_API_URL;
}

// Export API_BASE_URL biar bisa dipake di semua halaman
window.API_BASE_URL = getApiBaseUrl();

console.log('🔧 Config API loaded:', window.API_BASE_URL);
