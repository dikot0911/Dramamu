// Konfigurasi API buat deployment
// File ini AUTO-GENERATED oleh build-config.sh saat deploy ke Netlify
// JANGAN edit manual - set API_BASE_URL di Netlify environment variables

const API_CONFIG = {
    // URL backend production (dari environment variable API_BASE_URL)
    // Penting: file ini harus di-generate oleh build-config.sh
    // Netlify akan run build-config.sh yang akan overwrite file ini
    PRODUCTION_API_URL: 'https://dramamu-backend-680p.onrender.com',
    
    // URL buat development local
    DEVELOPMENT_API_URL: 'http://localhost:5000'
};

function getApiBaseUrl() {
    const hostname = window.location.hostname;
    const protocol = window.location.protocol;
    
    const isDev = hostname.includes('vercel.app') ||
                 hostname.includes('railway.app') ||
                 hostname.includes('netlify.app') ||
                 hostname.endsWith('.dev');
    
    if (isDev) {
        console.log('✅ Auto-detected Development environment:', hostname);
        const apiUrl = `${protocol}//${hostname}`;
        console.log('   Backend URL:', apiUrl);
        return apiUrl;
    }
    
    const isLocal = hostname === 'localhost' || 
                   hostname === '127.0.0.1' ||
                   hostname === '0.0.0.0';
    
    if (isLocal) {
        console.log('🔧 Mode development local - pake backend local');
        return API_CONFIG.DEVELOPMENT_API_URL;
    }
    
    console.log('🌐 Mode production - pake backend dari config');
    return API_CONFIG.PRODUCTION_API_URL;
}

// Export API_BASE_URL biar bisa dipake di semua halaman
window.API_BASE_URL = getApiBaseUrl();

// Validasi: Cek apakah URL masih placeholder (CRITICAL ERROR jika iya)
if (window.API_BASE_URL.includes('PLACEHOLDER_WILL_BE_REPLACED') || 
    window.API_BASE_URL.includes('GANTI-DENGAN-URL-RENDER-ANDA')) {
    
    console.error('❌ CRITICAL ERROR: Build script tidak dijalankan dengan benar!');
    console.error('❌ config.js masih berisi placeholder URL');
    console.error('');
    console.error('Solusi:');
    console.error('1. Set environment variable API_BASE_URL di Netlify dashboard');
    console.error('2. Netlify Dashboard → Site settings → Environment variables');
    console.error('3. Trigger re-deploy di Netlify');
    console.error('4. Verify netlify.toml configured dengan build command');
    console.error('');
    console.error('Contoh value: https://dramamu-backend-abc123.onrender.com');
    
    // Show prominent error to user
    alert('🚨 CRITICAL DEPLOYMENT ERROR!\n\n' +
          'Build script tidak berjalan.\n' +
          'config.js tidak di-generate dengan benar.\n\n' +
          'Hubungi admin atau check Netlify build logs.');
}

console.log('✅ Config API udah loaded:', window.API_BASE_URL);
