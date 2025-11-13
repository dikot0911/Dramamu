import os
import sys
from datetime import datetime, timezone

def now_utc():
    """
    Buat dapetin waktu UTC sekarang (tanpa timezone).
    Dipakai biar konsisten, soalnya datetime.utcnow() udah deprecated di Python 3.12+.
    Returns waktu UTC tanpa info timezone biar kompatibel sama database lama.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

_telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
if not _telegram_bot_token:
    print("⚠️ WARNING: TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables")
    print("Bot Telegram tidak akan berjalan, tapi FastAPI server dan Admin Panel tetap bisa diakses")
    print("Set TELEGRAM_BOT_TOKEN di .env untuk mengaktifkan bot Telegram")
    TELEGRAM_BOT_TOKEN: str | None = None
elif not _telegram_bot_token.strip():
    print("⚠️ WARNING: TELEGRAM_BOT_TOKEN kosong")
    print("Bot Telegram tidak akan berjalan. Cek token bot dari @BotFather")
    TELEGRAM_BOT_TOKEN = None
else:
    TELEGRAM_BOT_TOKEN = _telegram_bot_token.strip()
    print(f"✅ TELEGRAM_BOT_TOKEN configured")

_midtrans_server = os.getenv('MIDTRANS_SERVER_KEY', '').strip()
_midtrans_client = os.getenv('MIDTRANS_CLIENT_KEY', '').strip()

if not _midtrans_server or not _midtrans_client:
    print("⚠️ WARNING: MIDTRANS_SERVER_KEY atau MIDTRANS_CLIENT_KEY belum di-set")
    print("Fitur pembayaran bakal terbatas. Set ini di .env atau environment variable buat aktifin semua fitur")
    MIDTRANS_SERVER_KEY = _midtrans_server or ''
    MIDTRANS_CLIENT_KEY = _midtrans_client or ''
else:
    MIDTRANS_SERVER_KEY = _midtrans_server
    MIDTRANS_CLIENT_KEY = _midtrans_client
    print("✅ Midtrans keys configured")

_database_url = os.getenv('DATABASE_URL', '').strip()
if not _database_url:
    DATABASE_URL = 'sqlite:///dramamu.db'
    print("Pake SQLite database (default)")
else:
    DATABASE_URL = _database_url
    if DATABASE_URL.startswith('postgresql'):
        print(f"Pake PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'}")
    else:
        print(f"Pake custom database")

# BASE_URL = URL backend API (Render buat production, localhost buat development)
_api_base_url = os.getenv('API_BASE_URL', '').strip()
_render_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()

if _api_base_url:
    BASE_URL = _api_base_url.rstrip('/')
    print(f"API_BASE_URL: {BASE_URL}")
elif _render_url:
    # Fallback ke RENDER_EXTERNAL_URL kalau deploy di Render tapi lupa set API_BASE_URL
    BASE_URL = _render_url.rstrip('/')
    print(f"⚠️ API_BASE_URL ga di-set, pake RENDER_EXTERNAL_URL: {BASE_URL}")
    print(f"💡 Tip: Set API_BASE_URL di environment variables buat lebih jelas")
else:
    BASE_URL = "http://localhost:5000"
    print(f"Pake localhost (development mode)")

# FRONTEND_URL = URL mini app frontend (Netlify buat production, sama dengan BASE_URL buat dev)
_frontend_url = os.getenv('FRONTEND_URL', '').strip()
if _frontend_url:
    FRONTEND_URL = _frontend_url
    print(f"FRONTEND_URL: {FRONTEND_URL}")
else:
    FRONTEND_URL = BASE_URL
    print(f"FRONTEND_URL defaulting to BASE_URL")

# ALLOWED_ORIGINS buat CORS
_allowed_origins = os.getenv('ALLOWED_ORIGINS', '').strip()
_is_production = bool(os.getenv('RENDER')) or bool(os.getenv('RAILWAY_ENVIRONMENT'))

if _allowed_origins:
    # Kalau ALLOWED_ORIGINS udah di-set manual
    ALLOWED_ORIGINS = [origin.strip().rstrip('/') for origin in _allowed_origins.split(',') if origin.strip()]
    print(f"✅ CORS Origins: {ALLOWED_ORIGINS}")
elif _frontend_url and _frontend_url.rstrip('/') != BASE_URL.rstrip('/'):
    # Auto-configure dari FRONTEND_URL buat production
    ALLOWED_ORIGINS = [FRONTEND_URL.rstrip('/')]
    print(f"✅ CORS otomatis dikonfigurasi dari FRONTEND_URL: {ALLOWED_ORIGINS}")
elif _is_production:
    # Production deployment tanpa CORS config yang bener - ERROR KRITIS!
    print("=" * 80)
    print("❌ ERROR KRITIS: Deployment production tanpa konfigurasi CORS!")
    print("=" * 80)
    print("")
    print("KEBUTUHAN KEAMANAN: ALLOWED_ORIGINS HARUS di-set di production!")
    print("")
    print("Set environment variable:")
    print("  ALLOWED_ORIGINS=https://your-frontend.netlify.app")
    print("")
    print("Atau set keduanya:")
    print("  FRONTEND_URL=https://your-frontend.netlify.app")
    print("  ALLOWED_ORIGINS=https://your-frontend.netlify.app")
    print("")
    print("=" * 80)
    sys.exit(1)  # Gabisa jalan di production tanpa CORS yang bener
else:
    # Development mode - pake wildcard (OK buat development)
    ALLOWED_ORIGINS = ["*"]
    print("🔧 CORS pake wildcard (*) - Development mode")

TELEGRAM_BOT_USERNAME = os.getenv('TELEGRAM_BOT_USERNAME', 'dramamu_bot').strip()
if TELEGRAM_BOT_USERNAME:
    print(f"Bot username: @{TELEGRAM_BOT_USERNAME}")

URL_CARI_JUDUL = f"{FRONTEND_URL}/drama.html"
URL_CARI_CUAN = f"{FRONTEND_URL}/referal.html"
URL_BELI_VIP = f"{FRONTEND_URL}/payment.html"
URL_REQUEST = f"{FRONTEND_URL}/request.html"
URL_HUBUNGI_KAMI = f"{FRONTEND_URL}/contact.html"
