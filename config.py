import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

def now_utc():
    """
    Buat dapetin waktu UTC sekarang (tanpa timezone).
    Dipakai biar konsisten, soalnya datetime.utcnow() udah deprecated di Python 3.12+.
    Returns waktu UTC tanpa info timezone biar kompatibel sama database lama.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

def now_wib():
    """
    Buat dapetin waktu WIB (Western Indonesian Time / Asia/Jakarta) sekarang.
    WIB = UTC+7
    Returns waktu WIB dengan timezone info.
    """
    return datetime.now(ZoneInfo("Asia/Jakarta"))

def utc_to_wib(utc_dt, strict=False):
    """
    Konversi waktu UTC ke WIB (Asia/Jakarta).
    
    PENTING: Function ini mengasumsikan datetime naive adalah UTC.
    Jangan pass datetime naive yang sudah dalam WIB!
    
    Args:
        utc_dt: datetime object (harus UTC, bisa dengan atau tanpa timezone info)
        strict: Jika True, raise ValueError untuk non-UTC tz-aware datetimes
                Jika False (default), convert non-UTC ke UTC dulu dengan warning
    
    Returns:
        datetime object dengan timezone WIB, atau None jika input None
    
    Raises:
        ValueError: Jika strict=True dan timezone input bukan UTC
    """
    if utc_dt is None:
        return None
    
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    elif utc_dt.tzinfo != timezone.utc and utc_dt.tzinfo.utcoffset(utc_dt) != timedelta(0):
        if strict:
            raise ValueError(
                f"utc_to_wib expects UTC datetime but received {utc_dt.tzinfo}. "
                f"Got: {utc_dt}. Use strict=False to auto-convert or convert to UTC first."
            )
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            f"utc_to_wib received non-UTC datetime: {utc_dt} (tzinfo={utc_dt.tzinfo}). "
            f"Auto-converting to UTC. Set strict=True to raise error instead."
        )
        utc_dt = utc_dt.astimezone(timezone.utc)
    
    return utc_dt.astimezone(ZoneInfo("Asia/Jakarta"))

def format_wib(dt, include_seconds=False):
    """
    Format datetime ke string format Indonesia (WIB).
    
    Args:
        dt: datetime object (UTC atau WIB)
        include_seconds: Apakah include detik dalam format (default False)
    
    Returns:
        String formatted datetime dalam bahasa Indonesia
        Contoh: "19 Nov 2025, 14:30 WIB"
    """
    if dt is None:
        return "-"
    
    wib_dt = utc_to_wib(dt) if dt.tzinfo is None or dt.tzinfo != ZoneInfo("Asia/Jakarta") else dt
    
    if wib_dt is None:
        return "-"
    
    bulan_indonesia = {
        1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "Mei", 6: "Jun",
        7: "Jul", 8: "Agu", 9: "Sep", 10: "Okt", 11: "Nov", 12: "Des"
    }
    
    if include_seconds:
        time_format = f"{wib_dt.hour:02d}:{wib_dt.minute:02d}:{wib_dt.second:02d}"
    else:
        time_format = f"{wib_dt.hour:02d}:{wib_dt.minute:02d}"
    
    return f"{wib_dt.day} {bulan_indonesia[wib_dt.month]} {wib_dt.year}, {time_format} WIB"

# ============================================================================
# Helper functions buat parse environment variables (simple & DRY)
# ============================================================================

def get_env(key: str, default: str = '') -> str | None:
    """
    Ambil env var, return None kalau kosong.
    Auto-strip whitespace.
    """
    value = os.getenv(key, default).strip()
    return value if value else None

def require_env(key: str, error_msg: str | None = None) -> str:
    """
    Ambil env var yang wajib ada.
    Kalau ga ada atau kosong, exit dengan error message.
    """
    value = get_env(key)
    if not value:
        msg = error_msg or f"❌ {key} wajib di-set di environment variables!"
        print("=" * 80)
        print(msg)
        print("=" * 80)
        sys.exit(1)
    return value

def is_production() -> bool:
    """Check apakah environment production"""
    return bool(os.getenv('RENDER')) or bool(os.getenv('RAILWAY_ENVIRONMENT'))

# ============================================================================
# Configuration Variables
# ============================================================================

# Telegram Bot
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
if not TELEGRAM_BOT_TOKEN:
    print("⚠️  TELEGRAM_BOT_TOKEN ga di-set - Bot tidak akan jalan")
    print("   FastAPI server dan Admin Panel tetap bisa diakses")
else:
    print("✅ TELEGRAM_BOT_TOKEN configured")

# Telegram Storage Group (untuk upload film via Telegram)
TELEGRAM_STORAGE_CHAT_ID = get_env('TELEGRAM_STORAGE_CHAT_ID', '')
if TELEGRAM_STORAGE_CHAT_ID:
    try:
        TELEGRAM_STORAGE_CHAT_ID = int(TELEGRAM_STORAGE_CHAT_ID)
        # Normalize to negative ID (Telegram supergroups always use negative IDs)
        # This ensures consistency regardless of how the env var is set
        if TELEGRAM_STORAGE_CHAT_ID > 0:
            TELEGRAM_STORAGE_CHAT_ID = -TELEGRAM_STORAGE_CHAT_ID
            print(f"✅ Telegram Storage Group configured: {TELEGRAM_STORAGE_CHAT_ID} (normalized to negative)")
        else:
            print(f"✅ Telegram Storage Group configured: {TELEGRAM_STORAGE_CHAT_ID}")
    except ValueError:
        print("⚠️  TELEGRAM_STORAGE_CHAT_ID harus berupa angka (chat ID)")
        TELEGRAM_STORAGE_CHAT_ID = None
else:
    TELEGRAM_STORAGE_CHAT_ID = None
    print("⚠️  TELEGRAM_STORAGE_CHAT_ID belum di-set - Upload via Telegram disabled")

# Telegram Admin IDs (untuk akses admin via bot)
TELEGRAM_ADMIN_IDS = get_env('TELEGRAM_ADMIN_IDS', '')
if TELEGRAM_ADMIN_IDS:
    try:
        TELEGRAM_ADMIN_IDS = [int(id.strip()) for id in TELEGRAM_ADMIN_IDS.split(',') if id.strip()]
        print(f"✅ {len(TELEGRAM_ADMIN_IDS)} Telegram Admin(s) configured")
    except ValueError:
        print("⚠️  TELEGRAM_ADMIN_IDS format salah - harus angka dipisah koma")
        TELEGRAM_ADMIN_IDS = []
else:
    TELEGRAM_ADMIN_IDS = []
    print("⚠️  TELEGRAM_ADMIN_IDS belum di-set - Semua user bisa upload")

# DOKU Payment Gateway
DOKU_CLIENT_ID = get_env('DOKU_CLIENT_ID', '')
DOKU_SECRET_KEY = get_env('DOKU_SECRET_KEY', '')

# DOKU API URLs (Sandbox untuk testing, Production untuk live)
DOKU_API_URL = get_env('DOKU_API_URL', 'https://api-sandbox.doku.com')  # Default sandbox

if not DOKU_CLIENT_ID or not DOKU_SECRET_KEY:
    print("⚠️  DOKU credentials belum di-set - Fitur pembayaran terbatas")
    print("   Set DOKU_CLIENT_ID dan DOKU_SECRET_KEY di environment variables")
else:
    print("✅ DOKU payment gateway configured")
    api_env = "SANDBOX" if DOKU_API_URL and "sandbox" in DOKU_API_URL.lower() else "PRODUCTION"
    print(f"   Environment: {api_env}")

# Database
DATABASE_URL = get_env('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = 'sqlite:///dramamu.db'
    print("Pake SQLite database (default)")
else:
    if DATABASE_URL.startswith('postgresql'):
        # Hide password dari log
        safe_url = DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'configured'
        print(f"Pake PostgreSQL: {safe_url}")
    else:
        print("Pake custom database")

# Backend URL (API)
# Auto-detect production environment atau manual configuration
# Priority: API_BASE_URL > RENDER_EXTERNAL_URL > DEV_DOMAIN > localhost
dev_domain = get_env('DEV_DOMAIN')
BASE_URL = (
    get_env('API_BASE_URL') or 
    get_env('RENDER_EXTERNAL_URL') or 
    (f"https://{dev_domain}" if dev_domain else None) or 
    "http://localhost:5000"
)

if os.getenv('API_BASE_URL'):
    print(f"API_BASE_URL: {BASE_URL}")
elif os.getenv('RENDER_EXTERNAL_URL'):
    print(f"✅ Auto-detected Render URL: {BASE_URL}")
elif dev_domain:
    print(f"✅ Auto-detected Development URL: {BASE_URL}")
else:
    print("⚠️  Pake localhost (development mode) - Telegram Mini App buttons TIDAK akan jalan!")
    print("   Telegram requires HTTPS for Web App buttons")

BASE_URL = BASE_URL.rstrip('/')

# Frontend URL (Mini App)
FRONTEND_URL = get_env('FRONTEND_URL') or BASE_URL
FRONTEND_URL = FRONTEND_URL.rstrip('/')

if get_env('FRONTEND_URL'):
    print(f"FRONTEND_URL: {FRONTEND_URL}")
else:
    print("FRONTEND_URL defaulting to BASE_URL")

# CORS Configuration
allowed_origins_str = get_env('ALLOWED_ORIGINS')

if allowed_origins_str:
    # Manual config
    ALLOWED_ORIGINS = [o.strip().rstrip('/') for o in allowed_origins_str.split(',') if o.strip()]
    print(f"✅ CORS Origins: {ALLOWED_ORIGINS}")
elif get_env('FRONTEND_URL') and FRONTEND_URL != BASE_URL:
    # Auto-config dari FRONTEND_URL
    ALLOWED_ORIGINS = [FRONTEND_URL]
    print(f"✅ CORS auto-configured: {ALLOWED_ORIGINS}")
elif is_production():
    # Production HARUS set CORS untuk security
    # Tapi jangan crash server - fallback ke FRONTEND_URL sebagai satu-satunya origin
    if FRONTEND_URL != BASE_URL:
        # Fallback ke FRONTEND_URL sebagai default origin
        ALLOWED_ORIGINS = [FRONTEND_URL]
        print("=" * 80)
        print("⚠️  WARNING: Production CORS auto-configured dari FRONTEND_URL")
        print(f"   ALLOWED_ORIGINS=['{FRONTEND_URL}']")
        print("")
        print("Untuk production yang lebih aman, set ALLOWED_ORIGINS secara eksplisit:")
        print("  ALLOWED_ORIGINS=https://your-frontend.netlify.app,https://another-domain.com")
        print("=" * 80)
    else:
        # Ga ada FRONTEND_URL dan ga ada ALLOWED_ORIGINS - ini error konfigurasi
        # Fallback ke BASE_URL biar minimal API bisa diakses dari domain sendiri
        ALLOWED_ORIGINS = [BASE_URL]
        print("=" * 80)
        print("⚠️  PENTING: Production tanpa CORS config yang benar!")
        print(f"   Fallback ke BASE_URL: {BASE_URL}")
        print("")
        print("SEGERA set ALLOWED_ORIGINS atau FRONTEND_URL di environment variables!")
        print("  ALLOWED_ORIGINS=https://your-frontend.netlify.app")
        print("  FRONTEND_URL=https://your-frontend.netlify.app")
        print("=" * 80)
else:
    # Development mode - wildcard OK
    ALLOWED_ORIGINS = ["*"]
    print("🔧 CORS pake wildcard (*) - Development mode")

# Bot username
TELEGRAM_BOT_USERNAME = get_env('TELEGRAM_BOT_USERNAME', 'dramamu_bot')
print(f"Bot username: @{TELEGRAM_BOT_USERNAME}")

# Mini App URLs
URL_CARI_JUDUL = f"{FRONTEND_URL}/drama.html"
URL_CARI_CUAN = f"{FRONTEND_URL}/referal.html"
URL_BELI_VIP = f"{FRONTEND_URL}/payment.html"
URL_REQUEST = f"{FRONTEND_URL}/request.html"
URL_HUBUNGI_KAMI = f"{FRONTEND_URL}/contact.html"
