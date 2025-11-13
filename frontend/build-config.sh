#!/bin/bash
# Build script untuk generate config.js dari environment variable
# Dipakai waktu deploy ke Netlify

# Ambil URL backend dari environment variable, atau pake placeholder kalau ga ada
BACKEND_URL="${API_BASE_URL:-https://GANTI-DENGAN-URL-RENDER-ANDA.onrender.com}"

echo "🔧 Generating config.js dengan backend URL: $BACKEND_URL"

# Generate config.js
cat > config.js << EOF
// Konfigurasi API buat deployment
// File ini auto-generated dari build-config.sh
// JANGAN edit manual - set API_BASE_URL di Netlify environment variables

const API_CONFIG = {
    // URL backend production (dari environment variable API_BASE_URL)
    PRODUCTION_API_URL: '$BACKEND_URL',
    
    // URL buat development local
    DEVELOPMENT_API_URL: window.location.origin
};

// Auto-detect environment dari hostname
function getApiBaseUrl() {
    const hostname = window.location.hostname;
    
    // Cek apakah kita lagi di Netlify (production)
    const isNetlify = hostname.includes('netlify.app') || 
                     hostname.includes('netlify.com') ||
                     hostname.includes('.app');
    
    // Cek apakah kita lagi development local
    const isLocal = hostname === 'localhost' || 
                   hostname === '127.0.0.1';
    
    if (isNetlify) {
        console.log('🌐 Mode production - pake backend Render');
        return API_CONFIG.PRODUCTION_API_URL;
    }
    
    if (isLocal) {
        console.log('🔧 Mode development - pake backend local');
        return API_CONFIG.DEVELOPMENT_API_URL;
    }
    
    // Fallback ke production kalau host tidak diketahui
    console.warn('⚠️ Host ga dikenali, pake production API');
    return API_CONFIG.PRODUCTION_API_URL;
}

// Export API_BASE_URL biar bisa dipake di semua halaman
window.API_BASE_URL = getApiBaseUrl();

// Validasi: Cek apakah URL masih placeholder
if (window.API_BASE_URL.includes('GANTI-DENGAN-URL-RENDER-ANDA')) {
    console.error('❌ ERROR: API_BASE_URL belum di-set!');
    console.error('❌ Set environment variable API_BASE_URL di Netlify dashboard!');
    console.error('❌ Netlify Dashboard → Site settings → Environment variables');
    console.error('❌ Contoh value: https://dramamu-backend-abc123.onrender.com');
    alert('⚠️ Konfigurasi Error!\\n\\nAPI_BASE_URL belum di-set di Netlify.\\n\\nSet environment variable API_BASE_URL di Netlify dashboard.');
}

console.log('✅ Config API udah loaded:', window.API_BASE_URL);
EOF

echo "✅ config.js berhasil di-generate!"
