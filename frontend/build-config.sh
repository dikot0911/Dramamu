#!/bin/bash
# Build script untuk generate config.js dari environment variable
# CRITICAL: Script ini HARUS dijalankan oleh Netlify build process
# Dipakai waktu deploy ke Netlify
# 
# PRODUCTION OPTIMIZATION: Fail-fast validation untuk prevent broken deployments

set -e  # Exit on error
set -u  # Exit on undefined variable

echo "=================================================="
echo "🏗️  NETLIFY BUILD: Generating Frontend Config"
echo "=================================================="

# Ambil URL backend dari environment variable
BACKEND_URL="${API_BASE_URL:-}"

# VALIDATION 1: Environment variable MUST be set
if [ -z "$BACKEND_URL" ]; then
    echo ""
    echo "❌ CRITICAL ERROR: API_BASE_URL environment variable is NOT SET!"
    echo ""
    echo "Deployment will FAIL without this variable."
    echo ""
    echo "🔧 To fix:"
    echo "1. Go to Netlify Dashboard → Site settings → Environment variables"
    echo "2. Add: API_BASE_URL=https://your-backend.onrender.com"
    echo "   (Replace with your actual Render backend URL)"
    echo "3. Trigger re-deploy"
    echo ""
    echo "Example values:"
    echo "  Production: https://dramamu-api.onrender.com"
    echo "  Staging: https://dramamu-api-staging.onrender.com"
    echo ""
    exit 1
fi

# VALIDATION 2: URL should not be placeholder
if [[ "$BACKEND_URL" == *"GANTI-DENGAN-URL"* ]] || [[ "$BACKEND_URL" == *"PLACEHOLDER"* ]] || [[ "$BACKEND_URL" == *"localhost"* ]]; then
    echo ""
    echo "❌ CRITICAL ERROR: API_BASE_URL contains invalid value!"
    echo "Current value: $BACKEND_URL"
    echo ""
    echo "This looks like a placeholder or localhost URL."
    echo "Set actual production backend URL in Netlify environment variables."
    echo ""
    exit 1
fi

# VALIDATION 3: URL should be HTTPS for production
if [[ "$BACKEND_URL" != https://* ]]; then
    echo ""
    echo "⚠️  WARNING: API_BASE_URL is not HTTPS!"
    echo "Current value: $BACKEND_URL"
    echo ""
    echo "Telegram WebApp requires HTTPS for production."
    echo "Consider using HTTPS URL for security."
    echo ""
    # Don't exit, just warn (untuk flexibility)
fi

echo ""
echo "✅ Environment variable validated"
echo "🔧 Backend URL: $BACKEND_URL"
echo ""
echo "Generating config.js..."

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
