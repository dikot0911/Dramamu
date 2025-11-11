import os
import sys

_telegram_bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
if not _telegram_bot_token:
    print("ERROR: TELEGRAM_BOT_TOKEN tidak ditemukan di environment variables")
    print("Set TELEGRAM_BOT_TOKEN di .env atau environment variables")
    sys.exit(1)

if not _telegram_bot_token.strip():
    print("ERROR: TELEGRAM_BOT_TOKEN kosong")
    print("Cek token bot dari @BotFather")
    sys.exit(1)

TELEGRAM_BOT_TOKEN: str = _telegram_bot_token.strip()

MIDTRANS_SERVER_KEY = os.getenv('MIDTRANS_SERVER_KEY', '').strip() or 'SB-Mid-server-6RjBd0tyvuIWcSoiMwpJIOSf'
MIDTRANS_CLIENT_KEY = os.getenv('MIDTRANS_CLIENT_KEY', '').strip() or 'SB-Mid-client-W0pWBfx-U5OaLIvQ'

_database_url = os.getenv('DATABASE_URL', '').strip()
if not _database_url:
    DATABASE_URL = 'sqlite:///dramamu.db'
    print("Using SQLite database (default)")
else:
    DATABASE_URL = _database_url
    if DATABASE_URL.startswith('postgresql'):
        print(f"Using PostgreSQL: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'}")
    else:
        print(f"Using custom database")

# BASE_URL = URL backend API (Render untuk production, localhost untuk dev)
_api_base_url = os.getenv('API_BASE_URL', '').strip()
if _api_base_url:
    BASE_URL = _api_base_url
    print(f"API_BASE_URL: {BASE_URL}")
else:
    BASE_URL = "http://localhost:8000"
    print(f"Using localhost (development mode)")

# FRONTEND_URL = URL mini app frontend (Netlify untuk production, sama dengan BASE_URL untuk dev)
_frontend_url = os.getenv('FRONTEND_URL', '').strip()
if _frontend_url:
    FRONTEND_URL = _frontend_url
    print(f"FRONTEND_URL: {FRONTEND_URL}")
else:
    FRONTEND_URL = BASE_URL
    print(f"FRONTEND_URL defaulting to BASE_URL")

# ALLOWED_ORIGINS untuk CORS
_allowed_origins = os.getenv('ALLOWED_ORIGINS', '').strip()
if _allowed_origins:
    ALLOWED_ORIGINS = [origin.strip().rstrip('/') for origin in _allowed_origins.split(',') if origin.strip()]
    print(f"CORS Origins: {ALLOWED_ORIGINS}")
else:
    # Kalau FRONTEND_URL ada dan berbeda dengan BASE_URL, gunakan untuk CORS
    if _frontend_url and _frontend_url.rstrip('/') != BASE_URL.rstrip('/'):
        ALLOWED_ORIGINS = [FRONTEND_URL.rstrip('/')]
        print(f"CORS auto-configured from FRONTEND_URL: {ALLOWED_ORIGINS}")
    else:
        # Development mode - gunakan wildcard
        ALLOWED_ORIGINS = ["*"]
        print("CORS using wildcard (*) - OK for development, set ALLOWED_ORIGINS for production")

URL_CARI_JUDUL = f"{FRONTEND_URL}/drama.html"
URL_CARI_CUAN = f"{FRONTEND_URL}/referal.html"
URL_BELI_VIP = f"{FRONTEND_URL}/payment.html"
URL_REQUEST = f"{FRONTEND_URL}/request.html"
URL_HUBUNGI_KAMI = f"{FRONTEND_URL}/contact.html"
