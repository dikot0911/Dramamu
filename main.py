import time
import logging
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from typing import cast
from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from database import SessionLocal, User, Movie, Favorite, Like, WatchHistory, DramaRequest, Withdrawal, Payment, PaymentCommission, Broadcast, init_db, check_and_update_vip_expiry, serialize_movie
from schema_migrations import run_migrations, validate_critical_schema
from config import DOKU_CLIENT_ID, DOKU_SECRET_KEY, QRIS_PW_API_KEY, QRIS_PW_API_SECRET, QRIS_PW_API_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, URL_CARI_JUDUL, URL_BELI_VIP, ALLOWED_ORIGINS, BASE_URL, FRONTEND_URL, now_utc, is_production
from telebot import types
from admin_api import router as admin_router
import telegram_delivery
from bot_state import bot_state
import bot as bot_module
from referral_utils import process_referral_commission, send_referrer_notification, get_referral_stats as get_referral_stats_util, get_referral_program_analytics
from payment_processing import extend_vip_atomic, process_payment_success
import payment_config_service
from payment_sync import init_payment_sync, get_payment_sync_worker, stop_payment_sync

from security.config import SecurityConfig
from security.rate_limiter import RateLimitMiddleware
from security.waf import WAFMiddleware
from security.headers import SecurityHeadersMiddleware
from security.ip_blocker import IPBlocker, SSRFProtector
from security.brute_force import BruteForceProtector
from security.audit_logger import log_security_event, get_audit_logger
from security.input_validator import sanitize_html, validate_input

# PRODUCTION LOGGING: Structured logging untuk production monitoring
# Level: INFO untuk production, DEBUG untuk development
log_level = logging.INFO if is_production() else logging.DEBUG
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def query_for_update(query, use_lock=True):
    """
    Apply with_for_update() only for PostgreSQL to enable row-level locking.
    
    CRITICAL: This prevents race conditions in concurrent payment processing:
    - Prevents double VIP activation when webhook + polling run simultaneously
    - Prevents duplicate commission payment
    - Ensures atomicity of payment status changes
    
    Args:
        query: SQLAlchemy query object
        use_lock: Whether to attempt row-level locking (default: True)
    
    Returns:
        Query with or without with_for_update() based on dialect
    """
    if not use_lock:
        return query
    
    try:
        # Get the session from the query
        session = query.session
        
        # Inspect actual database dialect
        dialect_name = session.bind.dialect.name
        
        # Only apply row-level locking for PostgreSQL
        if dialect_name == 'postgresql':
            return query.with_for_update()
        
        # Skip locking for SQLite and other dialects
        return query
    
    except (AttributeError, Exception):
        # Fallback: if we can't detect dialect, skip locking (safe default)
        return query

# Log environment info pada startup with comprehensive deployment info
if is_production():
    logger.info("=" * 70)
    logger.info("🚀 PRODUCTION MODE - Dramamu Bot API Starting")
    logger.info("=" * 70)
else:
    logger.info("=" * 70)
    logger.info("🔧 DEVELOPMENT MODE - Dramamu Bot API Starting")
    logger.info("=" * 70)

# ENHANCED LOGGING: Comprehensive startup info for debugging
logger.info("📊 Environment Configuration:")
logger.info(f"   BASE_URL: {BASE_URL}")
logger.info(f"   FRONTEND_URL: {FRONTEND_URL}")
logger.info(f"   BOT_USERNAME: {TELEGRAM_BOT_USERNAME}")
logger.info(f"   BOT_TOKEN configured: {'✅' if TELEGRAM_BOT_TOKEN else '❌'}")
logger.info(f"   QRIS_PW configured: {'✅' if QRIS_PW_API_KEY and QRIS_PW_API_SECRET else '❌'}")
logger.info(f"   DOKU configured: {'✅' if DOKU_CLIENT_ID and DOKU_SECRET_KEY else '❌'}")
logger.info(f"   ALLOWED_ORIGINS: {ALLOWED_ORIGINS}")

app = FastAPI(title="Dramamu API")

app.include_router(admin_router)

security_config = SecurityConfig()
ip_blocker = IPBlocker(security_config.ip_blocker)
ssrf_protector = SSRFProtector(security_config.ssrf)
brute_force_protector = BruteForceProtector(security_config.brute_force)
audit_logger = get_audit_logger()

logger.info("🔒 Initializing security modules...")

allow_credentials = "*" not in ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(f"✅ CORS configured: origins={ALLOWED_ORIGINS}, credentials={allow_credentials}")

app.add_middleware(SecurityHeadersMiddleware, config=security_config)
logger.info("✅ Security Headers middleware loaded")

app.add_middleware(WAFMiddleware, config=security_config)
logger.info("✅ WAF (Web Application Firewall) middleware loaded")

app.add_middleware(RateLimitMiddleware, config=security_config)
logger.info("✅ Rate Limiter middleware loaded")

@app.middleware("http")
async def ip_blocking_middleware(request: Request, call_next):
    """IP blocking middleware - blocks known malicious IPs (runs first)"""
    direct_ip = request.client.host if request.client else "unknown"
    
    if ip_blocker.is_trusted_proxy(direct_ip):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        else:
            client_ip = direct_ip
    else:
        client_ip = direct_ip
    
    if ip_blocker.is_whitelisted(client_ip) or ip_blocker.is_trusted_proxy(client_ip):
        return await call_next(request)
    
    is_blocked, reason, retry_after = ip_blocker.is_blocked(client_ip)
    if is_blocked:
        log_security_event(
            event_type="ip_blocked",
            severity="warning",
            ip_address=client_ip,
            details={"path": str(request.url.path), "method": request.method, "reason": reason}
        )
        return JSONResponse(
            status_code=403,
            content={"detail": "Access denied"},
            headers={"Retry-After": str(retry_after)} if retry_after else {}
        )
    
    return await call_next(request)

logger.info("✅ IP Blocking middleware loaded")

@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if (request.url.path.endswith(".css") or 
        request.url.path.endswith(".js") or
        request.url.path.endswith(".html")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

def validate_external_url(url: str) -> bool:
    """
    Validate external URL using SSRF protector.
    Use this before making external HTTP requests to prevent SSRF attacks.
    
    Args:
        url: The URL to validate
        
    Returns:
        True if URL is safe, False if blocked
    """
    is_safe, error = ssrf_protector.validate_url(url)
    if not is_safe:
        logger.warning(f"SSRF protection blocked URL: {url[:100]}, reason: {error}")
    return is_safe

@app.get("/panel")
async def redirect_to_admin_panel():
    """Redirect /panel to /panel/ for proper static file serving"""
    return RedirectResponse(url="/panel/")

if os.path.exists("admin"):
    app.mount("/panel", StaticFiles(directory="admin", html=True), name="admin_panel")
    logger.info("✅ Admin panel mounted at /panel")
else:
    logger.warning("⚠️ Admin directory not found")

if os.path.exists("backend_assets"):
    app.mount("/media", StaticFiles(directory="backend_assets"), name="media")
    logger.info("✅ Backend assets mounted at /media")

if os.path.exists("frontend/assets/qris"):
    app.mount("/qris", StaticFiles(directory="frontend/assets/qris"), name="qris_images")
    logger.info("✅ QRIS images mounted at /qris")

# Frontend static files - HARUS di-mount SETELAH semua API routes didefinisikan
# Mounting ini ada di akhir file setelah semua routes

@app.on_event("startup")
async def startup_event():
    """Setup database waktu app startup"""
    init_db()
    
    # Run pending migrations otomatis saat startup
    logger.info("🔄 Running database migrations...")
    migration_success = run_migrations()
    if migration_success:
        logger.info("✅ Database migrations completed")
    else:
        logger.error("❌ Database migrations failed - check logs for details")
    
    # Validate schema setelah migrations
    schema_valid = validate_critical_schema()
    if not schema_valid:
        logger.error("❌ Schema validation failed - some columns may be missing")
    
    # Setup Telegram webhook untuk production
    if is_production() and TELEGRAM_BOT_TOKEN:
        logger.info("🌐 Production mode - setting up webhook...")
        try:
            from bot import setup_webhook
            webhook_success = setup_webhook()
            if webhook_success:
                logger.info("✅ Telegram webhook configured")
            else:
                logger.error("❌ Webhook setup failed - bot mungkin tidak berfungsi")
                logger.error("   Check TELEGRAM_BOT_TOKEN dan pastikan tidak ada bot lain yang pakai token ini")
        except Exception as e:
            logger.error(f"❌ Webhook setup error: {e}")
            logger.error("   Bot akan tetap jalan tapi mungkin tidak terima pesan")
    elif TELEGRAM_BOT_TOKEN:
        logger.info("🔧 Development mode - bot pakai polling")
    
    # Start Payment Sync Worker - background task untuk sync pending payments
    # Ini memastikan VIP otomatis aktif meskipun webhook gagal
    if QRIS_PW_API_KEY and QRIS_PW_API_SECRET:
        try:
            sync_worker = init_payment_sync(bot=bot)
            if sync_worker:
                logger.info("✅ Payment Sync Worker started - akan sync pending payments setiap 30 detik")
        except Exception as e:
            logger.error(f"❌ Failed to start Payment Sync Worker: {e}")
    else:
        logger.info("ℹ️ Payment Sync Worker not started - QRIS.PW not configured")

if QRIS_PW_API_KEY and QRIS_PW_API_SECRET:
    logger.info("✅ QRIS.PW payment gateway initialized")
    logger.info(f"   API URL: {QRIS_PW_API_URL}")
else:
    logger.warning("⚠️ QRIS.PW credentials belum di-set - Fitur pembayaran QRIS disabled")
    logger.warning("   Set QRIS_PW_API_KEY dan QRIS_PW_API_SECRET di environment variables")

if DOKU_CLIENT_ID and DOKU_SECRET_KEY:
    logger.info("✅ DOKU payment gateway initialized (Legacy)")
else:
    logger.info("ℹ️  DOKU credentials not set (Legacy payment method)")

# Import bot instance dari bot.py (sudah ada message handlers)
# CRITICAL: Jangan buat TeleBot baru, pakai bot yang sudah register handlers
bot = bot_module.bot
if bot:
    logger.info("✅ Telegram bot imported from bot.py (with handlers)")
else:
    logger.warning("⚠️ Telegram bot NOT initialized - TELEGRAM_BOT_TOKEN not configured")

def validate_telegram_webapp(init_data: str, bot_token: str | None = TELEGRAM_BOT_TOKEN, allow_missing_token: bool = False) -> dict | None:
    """
    Cek validasi signature initData dari Telegram WebApp
    Dokumentasi: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    
    Return data user kalo valid, throw error kalo ga valid
    
    Args:
        init_data: Data dari Telegram WebApp
        bot_token: Token bot Telegram
        allow_missing_token: Jika True, return None daripada throw error ketika bot_token tidak ada
    
    Returns:
        dict dengan user data jika valid, None jika allow_missing_token=True dan bot_token tidak ada
    """
    if not bot_token:
        if allow_missing_token:
            logger.warning("⚠️ Telegram bot token tidak tersedia - mode degraded (tanpa autentikasi user)")
            return None
        raise HTTPException(status_code=503, detail="Telegram bot tidak dikonfigurasi")
    
    try:
        parsed_data = dict(parse_qsl(init_data))
    except Exception as e:
        logger.error(f"Gagal parsing initData: {e}")
        raise HTTPException(status_code=401, detail="Data tidak valid")
    
    if 'hash' not in parsed_data:
        logger.error("Ga ada hash di initData")
        raise HTTPException(status_code=401, detail="Signature tidak ditemukan")
    
    received_hash = parsed_data.pop('hash')
    
    if 'auth_date' in parsed_data:
        try:
            auth_date = int(parsed_data['auth_date'])
            current_time = int(time.time())
            time_diff = current_time - auth_date
            
            if time_diff > 86400:
                logger.error(f"initData udah kadaluarsa: {time_diff} detik")
                raise HTTPException(status_code=401, detail="Data sudah kadaluarsa (lebih dari 24 jam)")
        except ValueError:
            raise HTTPException(status_code=401, detail="Format tanggal tidak valid")
    
    data_check_string = '\n'.join(
        f"{k}={v}" for k, v in sorted(parsed_data.items())
    )
    
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256
    ).digest()
    
    calculated_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(calculated_hash, received_hash):
        logger.error(f"Hash ga cocok! Yang diterima: {received_hash[:10]}..., Yang dihitung: {calculated_hash[:10]}...")
        raise HTTPException(status_code=401, detail="Signature tidak valid - autentikasi gagal")
    
    logger.info("✅ Signature Telegram WebApp berhasil divalidasi")
    
    if 'user' not in parsed_data:
        raise HTTPException(status_code=401, detail="Data user tidak ditemukan")
    
    try:
        import json
        user_data = json.loads(parsed_data['user'])
        return {
            'telegram_id': user_data.get('id'),
            'username': user_data.get('username'),
            'first_name': user_data.get('first_name'),
            'last_name': user_data.get('last_name'),
            'auth_date': parsed_data.get('auth_date')
        }
    except Exception as e:
        logger.error(f"Gagal parsing data user: {e}")
        raise HTTPException(status_code=401, detail="Format data user tidak valid")

class PaymentRequest(BaseModel):
    telegram_id: int
    paket_id: int
    gross_amount: int
    nama_paket: str
    screenshot_file: str | None = None

class MovieSelectionRequest(BaseModel):
    init_data: str
    movie_id: str

class FavoriteRequest(BaseModel):
    init_data: str
    movie_id: str

class RemoveFavoriteRequest(BaseModel):
    init_data: str
    movie_id: str

class LikeRequest(BaseModel):
    init_data: str
    movie_id: str

class WatchHistoryRequest(BaseModel):
    init_data: str
    movie_id: str

class DramaRequestSubmit(BaseModel):
    init_data: str
    judul: str
    apk_source: str

class WithdrawalRequest(BaseModel):
    init_data: str
    amount: int = Field(..., gt=0, description="Jumlah withdrawal harus lebih dari 0")
    payment_method: str = Field(..., min_length=1, description="Metode pembayaran")
    account_number: str = Field(..., min_length=1, description="Nomor rekening")
    account_name: str = Field(..., min_length=1, description="Nama pemilik rekening")

class UserDataRequest(BaseModel):
    init_data: str

class PaymentCallback(BaseModel):
    order_id: str
    transaction_status: str
    fraud_status: str | None = None
    signature_key: str | None = None
    status_code: str | None = None
    gross_amount: str | None = None

@app.get("/api/config")
async def get_config(request: Request):
    """
    Get dynamic configuration dari current request.
    This helps frontend auto-detect backend URL even jika URL berubah.
    """
    # Detect hostname dari request headers (akurat untuk development)
    host_header = request.headers.get('host', '')
    protocol = 'https' if request.url.scheme == 'https' else 'http'
    
    # Build dynamic API URL dari current request
    if host_header:
        base_url = f"{protocol}://{host_header}"
    else:
        base_url = BASE_URL
    
    return {
        "status": "ok",
        "API_BASE_URL": base_url.rstrip('/'),
        "FRONTEND_URL": FRONTEND_URL.rstrip('/'),
        "environment": "production" if is_production() else "development",
        "version": "2.0",
        "auto_detected": bool(host_header)
    }

@app.get("/health")
async def health_check():
    """
    Endpoint buat ngecek kesehatan server (buat Render dan monitoring).
    PRODUCTION CRITICAL: Reports actual health including bot status.
    """
    db = SessionLocal()
    db_status = "healthy"
    
    try:
        # Tes koneksi database
        db.execute(text("SELECT 1"))
    except Exception as e:
        logger.error(f"Pengecekan kesehatan database gagal: {e}")
        db_status = "unhealthy"
    finally:
        # Selalu tutup session biar ga ada connection leak
        db.close()
    
    # Cek status service lainnya
    doku_status = "configured" if (DOKU_CLIENT_ID and DOKU_SECRET_KEY) else "not_configured"
    bot_configured = "configured" if bot else "not_configured"
    
    # PRODUCTION: Check bot health dari shared bot_state module
    # CRITICAL: Prioritize is_healthy() over started flag for accurate liveness
    bot_health = "not_started"
    try:
        # Priority 1: Check if bot is actually healthy (thread alive + started + not failed)
        if bot_state.is_healthy():
            bot_health = "healthy"
        # Priority 2: Check if bot failed explicitly
        elif bot_state.failed.is_set():
            bot_health = "failed"
        # Priority 3: Check if bot shutdown
        elif bot_state.shutdown.is_set():
            bot_health = "stopped"
        # Priority 4: Check if bot started but thread died (degraded state)
        elif bot_state.started.is_set() and bot_state.thread and not bot_state.thread.is_alive():
            bot_health = "died"  # Thread exited unexpectedly
        # Priority 5: Bot is starting (token configured but not started yet)
        elif TELEGRAM_BOT_TOKEN and not bot_state.started.is_set():
            bot_health = "starting"
        # Priority 6: Bot not configured (no token)
        elif not TELEGRAM_BOT_TOKEN:
            bot_health = "not_configured"
        # Fallback: Unknown state
        else:
            bot_health = "unknown"
    except Exception as e:
        # Unexpected error saat check health
        logger.warning(f"Error checking bot health: {e}")
        bot_health = "error"
    
    # Overall status:
    # - ok: database healthy, bot healthy or not configured
    # - degraded: database healthy but bot failed (optional service)
    # - unhealthy: database down (critical service)
    if db_status != "healthy":
        overall_status = "unhealthy"
    elif bot_health == "failed" and TELEGRAM_BOT_TOKEN:
        overall_status = "degraded"
    else:
        overall_status = "ok"
    
    return {
        "status": overall_status,
        "database": db_status,
        "doku": doku_status,
        "telegram_bot": {
            "configured": bot_configured,
            "health": bot_health
        },
        "service": "dramamu-api",
        "version": "1.0.0"
    }

@app.head("/api")
@app.get("/api")
async def api_health():
    """Endpoint buat ngecek API masih hidup"""
    return {"status": "ok", "message": "ready"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """
    Webhook endpoint untuk menerima update dari Telegram.
    Digunakan di production (Render) sebagai pengganti polling.
    """
    if not bot:
        logger.error("❌ Bot tidak dikonfigurasi - webhook tidak bisa diproses")
        raise HTTPException(status_code=503, detail="Bot tidak tersedia")
    
    try:
        # Ambil update dari Telegram
        update_dict = await request.json()
        
        # Process update
        from telebot import types
        update = types.Update.de_json(update_dict)
        
        if update:
            # Extract message details untuk logging
            message_info = ""
            if update.message:
                chat_id = update.message.chat.id
                user_id = update.message.from_user.id if update.message.from_user else "unknown"
                username = update.message.from_user.username if update.message.from_user and update.message.from_user.username else "no_username"
                text = update.message.text or update.message.caption or "<no_text>"
                message_info = f"chat_id={chat_id}, user={user_id}(@{username}), text='{text[:50]}'"
            elif update.callback_query:
                user_id = update.callback_query.from_user.id if update.callback_query.from_user else "unknown"
                username = update.callback_query.from_user.username if update.callback_query.from_user and update.callback_query.from_user.username else "no_username"
                callback_data = update.callback_query.data or "<no_data>"
                message_info = f"callback from user={user_id}(@{username}), data='{callback_data}'"
            
            logger.info(f"📨 Webhook received: update_id={update.update_id}, {message_info}")
            
            # Process update with error handling
            try:
                bot.process_new_updates([update])
                logger.info(f"✅ Webhook processed successfully: update_id={update.update_id}")
            except Exception as process_error:
                logger.error(f"❌ Error in bot.process_new_updates(): {process_error}")
                logger.exception("Bot processing error details:")
                # Return OK to Telegram agar tidak retry
                return {"status": "error_logged", "message": "Processing failed but acknowledged"}
        else:
            logger.warning("⚠️ Received null update from Telegram")
        
        return {"status": "ok"}
        
    except Exception as e:
        logger.error(f"❌ Error processing webhook: {e}")
        logger.exception("Webhook error details:")
        return {"status": "error", "message": str(e)}

@app.get("/api/v1/config")
async def get_public_config():
    """Return public configuration untuk frontend"""
    return {
        "doku_client_id": DOKU_CLIENT_ID,
        "bot_username": TELEGRAM_BOT_USERNAME
    }

@app.get("/api/poster/{file_id}")
async def get_poster_by_file_id(file_id: str):
    """
    Proxy poster dari Telegram File ID.
    Download file dari Telegram dan serve ke client (Mini App).
    """
    if not bot:
        raise HTTPException(status_code=503, detail="Telegram bot tidak tersedia")
    
    try:
        import requests
        
        # Ambil file info dari Telegram
        file_info = bot.get_file(file_id)
        file_path = file_info.file_path
        
        if not file_path:
            raise HTTPException(status_code=404, detail="File path tidak ditemukan")
        
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
        if not validate_external_url(file_url):
            logger.error(f"❌ SSRF protection blocked URL: {file_url[:50]}...")
            raise HTTPException(status_code=500, detail="URL tidak valid")
        
        response = requests.get(file_url, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(status_code=404, detail="Poster tidak ditemukan")
        
        # Tentukan content type berdasarkan extension
        content_type = "image/jpeg"  # default
        if file_path.lower().endswith('.png'):
            content_type = "image/png"
        elif file_path.lower().endswith('.webp'):
            content_type = "image/webp"
        
        # Return file sebagai response
        return Response(
            content=response.content,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",  # Cache 24 jam
                "Content-Disposition": "inline"
            }
        )
        
    except Exception as e:
        logger.error(f"❌ Error download poster {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Gagal mengambil poster: {str(e)}")

@app.get("/api/v1/movies")
async def get_all_movies(sort: str = "terbaru", init_data: str | None = None):
    db = SessionLocal()
    try:
        telegram_id = None
        if init_data:
            try:
                validated_user = validate_telegram_webapp(init_data, allow_missing_token=True)
                if validated_user:
                    telegram_id = str(validated_user['telegram_id'])
                else:
                    logger.info("📋 Fetching movies tanpa user context (bot token tidak tersedia)")
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error ga terduga waktu validasi init_data: {e}")
                raise HTTPException(status_code=500, detail="Error waktu validasi data autentikasi")
        
        query = db.query(Movie).filter(Movie.deleted_at == None)
        
        if sort == "populer":
            query = query.order_by(Movie.views.desc())
        else:
            query = query.order_by(Movie.created_at.desc())
        
        movies = query.all()
        movies_list = []
        
        user_likes = set()
        user_favorites = set()
        
        if telegram_id:
            user_likes_query = db.query(Like.movie_id).filter(Like.telegram_id == telegram_id).all()
            user_likes = {like[0] for like in user_likes_query}
            
            user_fav_query = db.query(Favorite.movie_id).filter(Favorite.telegram_id == telegram_id).all()
            user_favorites = {fav[0] for fav in user_fav_query}
        
        from sqlalchemy import func
        like_counts_query = db.query(
            Like.movie_id,
            func.count(Like.id).label('count')
        ).group_by(Like.movie_id).all()
        like_counts = {movie_id: count for movie_id, count in like_counts_query}
        
        favorite_counts_query = db.query(
            Favorite.movie_id,
            func.count(Favorite.id).label('count')
        ).group_by(Favorite.movie_id).all()
        favorite_counts = {movie_id: count for movie_id, count in favorite_counts_query}
        
        for movie in movies:
            desc_value: str | None = cast(str | None, movie.description)
            description_text = desc_value if (desc_value is not None and desc_value != '') else ''
            
            user_like_count = like_counts.get(movie.id, 0)
            user_favorite_count = favorite_counts.get(movie.id, 0)
            
            base_like = movie.base_like_count if movie.base_like_count is not None else 0
            base_favorite = movie.base_favorite_count if movie.base_favorite_count is not None else 0
            
            like_count = base_like + user_like_count
            favorite_count = base_favorite + user_favorite_count
            
            movies_list.append({
                "id": movie.id,
                "title": movie.title,
                "description": description_text,
                "poster_url": movie.poster_url,
                "poster_file_id": movie.poster_file_id,
                "video_link": movie.video_link,
                "category": movie.category if movie.category is not None else "",
                "views": movie.views if movie.views is not None else 0,
                "like_count": like_count,
                "favorite_count": favorite_count,
                "is_liked": movie.id in user_likes if telegram_id else False,
                "is_favorited": movie.id in user_favorites if telegram_id else False
            })
            
        return {"movies": movies_list}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil daftar film: {e}")
        raise HTTPException(status_code=500, detail="Error waktu ambil daftar film")
    finally:
        db.close()

@app.post("/api/v1/user_status")
async def get_user_status(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        user = db.query(User).filter(
            User.telegram_id == str(telegram_id),
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        ).first()
        
        if user:
            is_vip_active = check_and_update_vip_expiry(user, db)
            return {"telegram_id": telegram_id, "is_vip": is_vip_active}
        else:
            return {"telegram_id": telegram_id, "is_vip": False, "status": "user_not_found"}
    except HTTPException:
        raise        
    except Exception as e:
        logger.error(f"Error waktu ambil status user: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/referral_stats")
async def get_referral_stats_endpoint(request: UserDataRequest):
    """Get referral stats untuk user - menggunakan centralized utility function"""
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        user = db.query(User).filter(
            User.telegram_id == str(telegram_id),
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        ).first()
        
        if user:
            # Use centralized utility function
            stats = get_referral_stats_util(db, user)
            return {
                "ref_code": stats["ref_code"],
                "commission_balance": stats["commission_balance"],
                "total_referrals": stats["total_referrals"]
            }
        else:
            return {"ref_code": "UNKNOWN", "commission_balance": 0, "total_referrals": 0}
    except HTTPException:
        raise        
    except Exception as e:
        logger.error(f"Error waktu ambil stats referral: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/v1/referral_analytics")
async def get_referral_analytics_endpoint():
    """
    Get comprehensive referral program analytics (admin/public endpoint).
    
    Returns:
    - Total active referrers
    - Total program earnings
    - Average commission per referrer
    - Top 10 referrers by earnings
    - Recent commissions
    - Program health metrics
    """
    db = SessionLocal()
    try:
        analytics = get_referral_program_analytics(db)
        return analytics
    except Exception as e:
        logger.error(f"Error waktu ambil referral analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/select_movie")
async def select_movie(request: MovieSelectionRequest):
    """
    Endpoint buat nerima pilihan film dari Mini App
    Backend langsung trigger bot kirim response ke user
    Ada validasi signature Telegram buat keamanan
    """
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        logger.info(f"🎬 Pilihan film diterima: User {telegram_id} (terautentikasi) → Film {request.movie_id}")
        
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == request.movie_id,
            Movie.deleted_at == None
        ).first()
        if not movie:
            logger.error(f"Film {request.movie_id} ga ketemu")
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        # BUG FIX #8: Exclude soft-deleted users
        user = db.query(User).filter(
            User.telegram_id == str(telegram_id),
            User.deleted_at == None
        ).first()
        if not user:
            logger.warning(f"User {telegram_id} ga ada di database")
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        is_vip_user = check_and_update_vip_expiry(user, db)
        
        if not bot:
            raise HTTPException(status_code=503, detail="Telegram bot tidak dikonfigurasi")
        
        movie_data = serialize_movie(movie)
        if not movie_data:
            raise HTTPException(status_code=500, detail="Gagal serialize movie data")
        
        try:
            is_vip_check = bool(is_vip_user)
            if is_vip_check:
                logger.info(f"✅ User {telegram_id} adalah VIP - kirim film via telegram_delivery")
                telegram_delivery.send_movie_to_vip(bot, telegram_id, movie_data)
            else:
                logger.info(f"⚠️ User {telegram_id} belum VIP - kirim ajakan upgrade via telegram_delivery")
                telegram_delivery.send_non_vip_message(bot, telegram_id, movie_data)
            
            return {"status": "success", "message": "Film berhasil dikirim ke user"}
            
        except Exception as delivery_error:
            logger.error(f"❌ Error saat mengirim film: {delivery_error}")
            raise HTTPException(status_code=500, detail=f"Gagal mengirim film: {str(delivery_error)}")
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error di select_movie: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
    """
    Create payment based on active payment gateway configuration.
    
    Supports multiple gateways:
    - qrispw: QRIS.PW dynamic QRIS (auto-verified)
    - qris-interactive: Static QRIS images (manual verification by admin)
    - doku: DOKU payment gateway (belum tersedia)
    - midtrans: Midtrans payment gateway (belum tersedia)
    """
    import requests
    import json
    from datetime import datetime, timedelta
    
    active_gateway = payment_config_service.get_active_gateway()
    is_ready, error_msg = payment_config_service.is_gateway_ready(active_gateway)
    
    logger.info(f"💳 Create payment request: gateway={active_gateway}, amount=Rp{request.gross_amount}, user={request.telegram_id}")
    
    if not is_ready:
        logger.error(f"❌ Gateway {active_gateway} not ready: {error_msg}")
        raise HTTPException(status_code=503, detail=error_msg)
    
    order_id = f"DRAMAMU-{request.telegram_id}-{int(time.time())}"
    
    if active_gateway == "qrispw":
        return await _create_qrispw_payment(request, order_id)
    
    elif active_gateway == "qris-interactive":
        return await _create_qris_interactive_payment(request, order_id)
    
    elif active_gateway in ["doku", "midtrans"]:
        logger.info(f"⚠️ Gateway {active_gateway} belum tersedia")
        raise HTTPException(status_code=503, detail="Gateway pembayaran belum tersedia. Silakan hubungi admin.")
    
    else:
        logger.error(f"❌ Unknown gateway: {active_gateway}")
        raise HTTPException(status_code=500, detail="Konfigurasi pembayaran tidak valid. Silakan hubungi admin.")


async def _create_qrispw_payment(request: PaymentRequest, order_id: str):
    """
    Create payment via QRIS.PW API (existing logic, refactored).
    """
    import requests
    import json
    from datetime import datetime
    
    if not (QRIS_PW_API_KEY and QRIS_PW_API_SECRET):
        raise HTTPException(status_code=503, detail="QRIS.PW belum dikonfigurasi. Hubungi admin.")
    
    try:
        callback_url = f"{BASE_URL}/api/v1/qris_callback"
        
        payload = {
            "amount": request.gross_amount,
            "order_id": order_id,
            "customer_name": f"User {request.telegram_id}",
            "customer_phone": "",
            "callback_url": callback_url
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": QRIS_PW_API_KEY,
            "X-API-Secret": QRIS_PW_API_SECRET
        }
        
        logger.info(f"📤 Calling QRIS.PW API for order {order_id}, amount: Rp {request.gross_amount}")
        logger.debug(f"   Callback URL: {callback_url}")
        
        api_url = f"{QRIS_PW_API_URL}/create-payment.php"
        if not validate_external_url(api_url):
            logger.error(f"❌ SSRF protection blocked URL: {api_url}")
            raise HTTPException(status_code=500, detail="Konfigurasi payment gateway tidak valid")
        
        response = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=30
        )
        
        logger.info(f"QRIS.PW API response status: {response.status_code}")
        
        if response.status_code != 200:
            error_text = response.text[:200]
            logger.error(f"❌ QRIS.PW API error (status {response.status_code}): {error_text}")
            raise HTTPException(status_code=500, detail="Gagal membuat pembayaran, silakan coba lagi")
        
        try:
            result = response.json()
        except json.JSONDecodeError:
            logger.error(f"❌ Invalid JSON response from QRIS.PW: {response.text[:200]}")
            raise HTTPException(status_code=500, detail="Gagal membuat pembayaran, silakan coba lagi")
        
        if not result.get("success"):
            error_msg = str(result.get("error", "Unknown error"))[:100]
            logger.error(f"❌ QRIS.PW returned error: {error_msg}")
            raise HTTPException(status_code=500, detail="Gagal membuat pembayaran, silakan coba lagi")
        
        transaction_id = result.get("transaction_id")
        qris_url = result.get("qris_url")
        qris_string = result.get("qris_string") or result.get("qris_content")
        expires_at_str = result.get("expires_at")
        
        if not transaction_id:
            logger.error(f"❌ QRIS.PW response missing transaction_id: {result}")
            raise HTTPException(status_code=500, detail="Gagal membuat pembayaran, silakan coba lagi")
        
        if not qris_string:
            logger.error(f"❌ QRIS.PW response missing qris_string")
            raise HTTPException(status_code=500, detail="Gagal membuat pembayaran, silakan coba lagi")
        
        logger.info(f"✅ QRIS.PW payment created successfully")
        logger.info(f"   Transaction ID: {transaction_id}")
        logger.info(f"   QR Code URL: {qris_url[:50] if qris_url else 'N/A'}...")
        logger.info(f"   Expires at: {expires_at_str}")
        
        expires_at_datetime = None
        if expires_at_str:
            try:
                if isinstance(expires_at_str, (int, float)):
                    expires_at_datetime = datetime.fromtimestamp(expires_at_str)
                elif isinstance(expires_at_str, str):
                    try:
                        expires_at_datetime = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                    except ValueError:
                        try:
                            expires_at_datetime = datetime.fromtimestamp(float(expires_at_str))
                        except (ValueError, TypeError):
                            logger.warning(f"⚠️ Could not parse expires_at: {expires_at_str}")
            except Exception as parse_error:
                logger.warning(f"⚠️ Failed to parse expires_at: {parse_error}")
        
        db = SessionLocal()
        try:
            payment = Payment(
                telegram_id=str(request.telegram_id),
                order_id=order_id,
                transaction_id=transaction_id,
                package_name=request.nama_paket,
                amount=request.gross_amount,
                status='pending',
                qris_url=qris_url,
                qris_string=qris_string,
                expires_at=expires_at_datetime
            )
            db.add(payment)
            db.commit()
            db.refresh(payment)
            logger.info(f"✅ Payment record saved: {order_id}")
        except Exception as db_error:
            logger.error(f"❌ Database error while saving payment: {db_error}")
            db.rollback()
            raise HTTPException(status_code=500, detail="Gagal menyimpan data pembayaran, silakan coba lagi")
        finally:
            db.close()
        
        return {
            "success": True,
            "order_id": order_id,
            "transaction_id": transaction_id,
            "qris_url": qris_url,
            "qris_string": qris_string,
            "amount": request.gross_amount,
            "expires_at": expires_at_str,
            "gateway": "qrispw",
            "message": "QRIS payment berhasil dibuat"
        }
    
    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout calling QRIS.PW API")
        raise HTTPException(status_code=504, detail="Koneksi timeout, silakan coba lagi")
    except requests.exceptions.ConnectionError:
        logger.error(f"❌ Connection error to QRIS.PW API")
        raise HTTPException(status_code=503, detail="Tidak dapat terhubung ke server pembayaran, silakan coba lagi")
    except Exception as e:
        logger.error(f"❌ Unexpected error in QRIS.PW payment: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail="Terjadi kesalahan, silakan coba lagi")


async def _create_qris_interactive_payment(request: PaymentRequest, order_id: str):
    """
    Create manual QRIS payment with static QRIS image.
    
    This gateway uses pre-uploaded QRIS images that must be manually verified by admin.
    Payment status is set to 'pending_manual' until admin approves/rejects.
    """
    from datetime import datetime, timedelta
    
    amount = request.gross_amount
    
    qris_image_url = payment_config_service.get_qris_image_url(amount)
    
    if not qris_image_url:
        available_amounts = payment_config_service.get_available_qris_amounts()
        if available_amounts:
            amounts_str = ", ".join([f"Rp{a:,}" for a in available_amounts])
            logger.warning(f"⚠️ QRIS image not found for amount Rp{amount}. Available: {amounts_str}")
            raise HTTPException(
                status_code=400, 
                detail=f"Nominal Rp{amount:,} tidak tersedia. Pilih salah satu: {amounts_str}"
            )
        else:
            logger.error("❌ No QRIS images available")
            raise HTTPException(status_code=503, detail="Tidak ada nominal QRIS yang tersedia. Hubungi admin.")
    
    expires_at = now_utc() + timedelta(hours=24)
    
    db = SessionLocal()
    try:
        payment = Payment(
            telegram_id=str(request.telegram_id),
            order_id=order_id,
            transaction_id=None,
            package_name=request.nama_paket,
            amount=amount,
            status='pending_manual',
            qris_url=qris_image_url,
            qris_string=None,
            expires_at=expires_at
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)
        
        logger.info(f"✅ QRIS Interactive payment created")
        logger.info(f"   Order ID: {order_id}")
        logger.info(f"   Amount: Rp{amount:,}")
        logger.info(f"   QRIS Image: {qris_image_url}")
        logger.info(f"   Status: pending_manual")
        
    except Exception as db_error:
        logger.error(f"❌ Database error while saving QRIS Interactive payment: {db_error}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Gagal menyimpan data pembayaran, silakan coba lagi")
    finally:
        db.close()
    
    return {
        "success": True,
        "order_id": order_id,
        "transaction_id": None,
        "qris_url": qris_image_url,
        "qris_string": None,
        "amount": amount,
        "expires_at": expires_at.isoformat() + 'Z',
        "gateway": "qris-interactive",
        "requires_manual_verification": True,
        "message": "Pembayaran QRIS berhasil dibuat. Silakan transfer dan upload bukti pembayaran."
    }


@app.get("/api/v1/payment-config")
async def get_public_payment_config():
    """
    Public endpoint for frontend to know active gateway and available options.
    
    Returns:
        - active_gateway: Currently active payment gateway
        - is_ready: Whether the gateway is properly configured
        - error: Error message if not ready (optional)
        - qris_amounts: Available QRIS amounts (for qris-interactive only)
        - qris_images: Available QRIS images with URLs (for qris-interactive only)
    """
    try:
        config = payment_config_service.get_public_config()
        return config
    except Exception as e:
        logger.error(f"❌ Error getting public payment config: {e}")
        return {
            "active_gateway": "unknown",
            "is_ready": False,
            "error": "Gagal memuat konfigurasi pembayaran"
        }

@app.post("/api/v1/qris_callback")
async def qris_payment_callback(request: Request):
    """
    Webhook callback dari QRIS.PW ketika pembayaran berhasil
    """
    try:
        from datetime import datetime, timedelta
        import json
        
        # Get raw body untuk signature verification
        body_bytes = await request.body()
        body_str = body_bytes.decode('utf-8')
        
        # Parse JSON payload
        payload = json.loads(body_str)
        
        logger.info(f"📥 QRIS.PW webhook received for transaction: {payload.get('transaction_id')}")
        
        # Verify signature
        if not QRIS_PW_API_SECRET:
            logger.error("❌ QRIS_PW_API_SECRET not configured")
            raise HTTPException(status_code=500, detail="Payment gateway not configured")
        
        signature = payload.get('signature')
        if not signature:
            logger.error("❌ No signature in webhook payload")
            raise HTTPException(status_code=401, detail="Missing signature")
        
        # Remove signature from payload for verification
        payload_copy = payload.copy()
        payload_copy.pop('signature', None)
        
        # Calculate expected signature
        payload_json = json.dumps(payload_copy, separators=(',', ':'), sort_keys=True)
        expected_signature = hmac.new(
            QRIS_PW_API_SECRET.encode('utf-8'),
            payload_json.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            logger.error(f"❌ Signature verification failed")
            logger.error(f"   Expected: {expected_signature[:20]}...")
            logger.error(f"   Received: {signature[:20]}...")
            raise HTTPException(status_code=401, detail="Invalid signature")
        
        logger.info(f"✅ Signature verified for transaction: {payload.get('transaction_id')}")
        
        # Extract data from webhook
        transaction_id = payload.get('transaction_id')
        order_id = payload.get('order_id')
        status = payload.get('status')
        amount = payload.get('amount')
        paid_at_str = payload.get('paid_at')
        
        # Order ID fallback: Try transaction_id if order_id missing
        if not order_id and transaction_id:
            logger.warning(f"⚠️ order_id missing in webhook, using transaction_id as fallback")
            order_id = transaction_id
        
        if not order_id:
            logger.error(f"❌ Both order_id and transaction_id missing in webhook: {payload}")
            raise HTTPException(status_code=400, detail="Missing order_id and transaction_id")
        
        # Find payment in database
        db = SessionLocal()
        try:
            # CRITICAL: Use row-level locking to prevent race conditions
            # This prevents double VIP activation if webhook and polling run simultaneously
            payment = query_for_update(
                db.query(Payment).filter(Payment.order_id == order_id)
            ).first()
            
            if not payment and transaction_id:
                logger.info(f"Payment not found by order_id, trying transaction_id: {transaction_id}")
                payment = query_for_update(
                    db.query(Payment).filter(Payment.transaction_id == transaction_id)
                ).first()
            
            if not payment:
                logger.error(f"❌ Payment not found: order_id={order_id}, transaction_id={transaction_id}")
                raise HTTPException(status_code=404, detail="Payment not found")
            
            logger.info(f"Payment status update: {payment.order_id} -> {status}")
            
            # Handle payment status
            if status == 'paid':
                # IDEMPOTENCY CHECK: Only process if payment is still pending
                # Combined with row-level lock above, this ensures exactly-once processing
                if str(payment.status) != 'pending':
                    logger.info(f"⏭️ Payment already processed (status: {payment.status}), skipping webhook")
                    return {"status": "already_processed", "message": f"Payment already in {payment.status} status"}
                
                # BUG FIX #6: Validate package BEFORE setting status to success
                # This prevents brief moment where payment is marked success with invalid package
                from vip_packages import validate_package_name
                
                package_name_str = str(payment.package_name)
                valid, days, error = validate_package_name(package_name_str)
                
                if not valid or days is None:
                    logger.error(
                        f"❌ CRITICAL: Invalid package name '{package_name_str}' "
                        f"for payment {payment.order_id}. Payment marked as pending manual review."
                    )
                    payment.status = 'manual_review'  # type: ignore
                    db.commit()
                    raise HTTPException(
                        status_code=422,
                        detail=f"Package tidak valid: {error}"
                    )
                
                # Type ignore untuk SQLAlchemy column assignments
                payment.status = 'success'  # type: ignore
                payment.paid_at = now_utc()  # type: ignore
                
                # Activate VIP with row-level lock on user record
                user = query_for_update(
                    db.query(User).filter(
                        User.telegram_id == payment.telegram_id,
                        User.deleted_at == None
                    )
                ).first()
                if user:
                    
                    # Instead of manual calculation, use atomic function
                    success, error = extend_vip_atomic(db, user, days)
                    if not success:
                        logger.error(f"Failed to extend VIP: {error}")
                        db.rollback()
                        raise HTTPException(status_code=500, detail="Failed to activate VIP")
                    
                    logger.info(f"✅ VIP activated for user {payment.telegram_id} for {days} days (package: {package_name_str})")
                    
                    # Process referral commission using centralized utility
                    commission_paid, commission_amount, referrer_id = process_referral_commission(db, payment, user)
                    if commission_paid:
                        logger.info(f"💰 Commission paid via webhook: Rp {commission_amount} to {referrer_id}")
                
                db.commit()
                
                # Send referrer notification (after commit to ensure data consistency)
                if commission_paid and referrer_id and bot:
                    send_referrer_notification(bot, referrer_id, str(payment.telegram_id), commission_amount)
                
                # Send Telegram notification
                if bot:
                    try:
                        telegram_id_str = str(payment.telegram_id)
                        bot.send_message(
                            int(telegram_id_str),
                            f"✅ <b>Pembayaran Berhasil!</b>\n\n"
                            f"Paket: {payment.package_name}\n"
                            f"Status VIP kamu sudah aktif!\n\n"
                            f"Selamat menonton! 🎬",
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Success notification sent to user {payment.telegram_id}")
                    except Exception as bot_error:
                        logger.error(f"❌ Failed to send Telegram notification: {bot_error}")
                else:
                    logger.warning("⚠️ Bot not configured - cannot send payment success notification")
                
                return {"status": "success", "message": "Payment processed successfully"}
            
            elif status == 'pending':
                payment.status = 'pending'  # type: ignore
                db.commit()
                return {"status": "pending", "message": "Payment still pending"}
            
            elif status in ['expired', 'failed', 'cancelled']:
                payment.status = 'failed'  # type: ignore
                db.commit()
                logger.info(f"Payment marked as failed: {payment.order_id} (status: {status})")
                return {"status": "failed", "message": f"Payment {status}"}
            
            else:
                logger.warning(f"Unknown payment status: {status} for order {order_id}")
                return {"status": "unknown", "message": f"Unknown status: {status}"}
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            db.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in webhook handler: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/check_payment_status")
async def check_payment_status(transaction_id: str):
    """
    Check payment status from QRIS.PW API and sync with local database
    This ensures VIP activation even if webhook fails or is delayed
    
    PRIORITY CHECK ORDER:
    1. Check if local payment already marked as success/paid (admin manual activation)
    2. Query QRIS.PW API for actual payment status
    
    IMPORTANT: We do NOT check if user already has active VIP because:
    - User could have VIP from a PREVIOUS transaction
    - Checking VIP status would incorrectly mark NEW transactions as "paid"
    - This was a critical bug that gave users free VIP!
    
    Admin manual activation flow:
    - Admin uses /admin/payments/qris/approve or /admin/manual-vip-activation
    - These endpoints update payment.status to 'success'/'paid' DIRECTLY
    - PRIORITY 1 will then detect this and return "paid" status
    
    RACE CONDITION PROTECTION:
    - Uses SELECT FOR UPDATE to lock payment row during processing
    - Checks payment.status == 'pending' for idempotency
    - Atomic database updates with proper commit/rollback
    """
    if not (QRIS_PW_API_KEY and QRIS_PW_API_SECRET):
        raise HTTPException(status_code=500, detail="QRIS.PW not configured")
    
    db = SessionLocal()
    try:
        import requests
        from datetime import datetime, timedelta
        
        logger.info(f"🔍 Checking payment status for transaction: {transaction_id}")
        
        # PRIORITY 1: Check local payment status first (for manual admin activations)
        # Admin approval updates payment.status to 'success'/'paid', which we detect here
        payment = db.query(Payment).filter(Payment.transaction_id == transaction_id).first()
        
        if payment:
            local_status = str(payment.status)
            
            # If already success/paid, check if VIP is actually active
            # BUG FIX: Previously, if payment was success but VIP activation failed (due to error/rollback),
            # user would see "Payment confirmed" but not have VIP access. This ensures VIP is activated.
            if local_status in ['success', 'paid']:
                logger.info(f"✅ Payment already confirmed locally (status: {local_status})")
                
                # BUG FIX: Verify and repair VIP status if payment is success but VIP not active
                try:
                    user = db.query(User).filter(
                        User.telegram_id == payment.telegram_id,
                        User.deleted_at == None
                    ).first()
                    
                    if user:
                        now = now_utc()
                        is_vip_active = user.is_vip and user.vip_expires_at and user.vip_expires_at > now
                        
                        if not is_vip_active:
                            # VIP not active but payment is success - this is a bug, fix it!
                            logger.warning(
                                f"⚠️ VIP RECOVERY: Payment {payment.order_id} is {local_status} but "
                                f"user {payment.telegram_id} VIP is not active. Activating VIP now..."
                            )
                            
                            from vip_packages import validate_package_name
                            package_name_str = str(payment.package_name)
                            valid, days, error = validate_package_name(package_name_str)
                            
                            if valid and days:
                                success, vip_error = extend_vip_atomic(db, user, days)
                                if success:
                                    db.commit()
                                    logger.info(
                                        f"✅ VIP RECOVERED: User {payment.telegram_id} now has VIP for {days} days "
                                        f"(payment {payment.order_id})"
                                    )
                                else:
                                    logger.error(f"❌ VIP recovery failed: {vip_error}")
                            else:
                                logger.error(f"❌ VIP recovery skipped - invalid package: {package_name_str}")
                        else:
                            logger.info(f"✓ User {payment.telegram_id} VIP already active (expires: {user.vip_expires_at})")
                    else:
                        logger.warning(f"⚠️ User not found for payment {payment.order_id}")
                except Exception as vip_check_error:
                    logger.error(f"❌ Error during VIP status check/recovery: {vip_check_error}")
                
                return {
                    "success": True,
                    "status": "paid",
                    "message": "Pembayaran sudah dikonfirmasi",
                    "local_status": local_status
                }
            
            # NOTE: We intentionally DO NOT check user's VIP status here for PENDING payments!
            # BUG FIX: Previously, if user had active VIP from PREVIOUS transaction,
            # this check would incorrectly mark NEW transactions as "paid" giving free VIP.
            # Admin manual activation should ONLY go through:
            # - /admin/payments/qris/approve -> updates payment.status to 'paid'
            # - /admin/manual-vip-activation -> updates payment.status to 'success'
            # Both will be caught by PRIORITY 1 above.
        
        # PRIORITY 2: Query QRIS.PW API for payment status
        headers = {
            "X-API-Key": QRIS_PW_API_KEY,
            "X-API-Secret": QRIS_PW_API_SECRET
        }
        
        try:
            api_url = f"{QRIS_PW_API_URL}/check-payment.php"
            if not validate_external_url(api_url):
                logger.error(f"❌ SSRF protection blocked URL: {api_url}")
                raise HTTPException(status_code=500, detail="Konfigurasi payment gateway tidak valid")
            
            response = requests.get(
                api_url,
                params={"transaction_id": transaction_id},
                headers=headers,
                timeout=10
            )
        except requests.RequestException as req_error:
            logger.error(f"❌ QRIS.PW API request failed: {req_error}")
            raise HTTPException(status_code=503, detail="Payment gateway unavailable")
        
        if response.status_code != 200:
            logger.error(f"QRIS.PW check status error: {response.text}")
            raise HTTPException(status_code=500, detail="Failed to check payment status")
        
        try:
            result = response.json()
        except ValueError as json_error:
            logger.error(f"❌ Invalid JSON response from QRIS.PW: {json_error}")
            raise HTTPException(status_code=500, detail="Invalid payment gateway response")
        
        if not result.get("success"):
            error_msg = result.get("error", "Unknown error")
            logger.error(f"❌ QRIS.PW API error: {error_msg}")
            raise HTTPException(status_code=500, detail=f"QRIS.PW error: {error_msg}")
        
        payment_status = result.get("status")
        if not payment_status:
            logger.error(f"❌ Missing status in QRIS.PW response: {result}")
            raise HTTPException(status_code=500, detail="Invalid payment status response")
        
        logger.info(f"📊 QRIS.PW payment status: {payment_status}")
        
        # CRITICAL: Lock payment row to prevent race condition with webhook
        # This ensures only one process (webhook OR polling) processes the payment
        payment = query_for_update(
            db.query(Payment).filter(Payment.transaction_id == transaction_id)
        ).first()
        
        if not payment:
            logger.warning(f"⚠️ Payment record not found for transaction: {transaction_id}")
            return result
        
        # IDEMPOTENCY CHECK: Only process if payment is still pending
        # This prevents duplicate VIP activation if both webhook and polling run
        current_status = str(payment.status)
        
        if payment_status == 'paid':
            if current_status != 'pending':
                logger.info(f"⏭️ Payment already processed (status: {current_status}), skipping")
                return result
            
            logger.info(f"💳 Processing paid payment from polling: {transaction_id}")
            
            try:
                # BUG FIX #6: Validate package BEFORE setting status to success
                # This prevents brief moment where payment is marked success with invalid package
                from vip_packages import validate_package_name
                
                package_name_str = str(payment.package_name)
                valid, days, error = validate_package_name(package_name_str)
                
                if not valid or days is None:
                    logger.error(
                        f"❌ CRITICAL: Invalid package name '{package_name_str}' "
                        f"for payment {payment.order_id}. Payment marked as pending manual review."
                    )
                    payment.status = 'manual_review'  # type: ignore
                    db.commit()
                    raise HTTPException(
                        status_code=422,
                        detail=f"Package tidak valid: {error}"
                    )
                
                # Update payment status atomically
                payment.status = 'success'  # type: ignore
                payment.paid_at = now_utc()  # type: ignore
                
                # Activate VIP with row-level lock on user record (same logic as webhook for consistency)
                user = query_for_update(
                    db.query(User).filter(
                        User.telegram_id == payment.telegram_id,
                        User.deleted_at == None
                    )
                ).first()
                if not user:
                    logger.error(f"❌ User not found for payment: telegram_id={payment.telegram_id}")
                    db.rollback()
                    raise HTTPException(status_code=404, detail="User not found")
                
                # Instead of manual calculation, use atomic function
                success, error = extend_vip_atomic(db, user, days)
                if not success:
                    logger.error(f"Failed to extend VIP: {error}")
                    db.rollback()
                    raise HTTPException(status_code=500, detail="Failed to activate VIP")
                
                logger.info(f"✅ VIP activated via polling for user {payment.telegram_id} for {days} days (package: {package_name_str})")
                
                # Process referral commission using centralized utility
                commission_paid, commission_amount, referrer_id = process_referral_commission(db, payment, user)
                if commission_paid:
                    logger.info(f"💰 Commission paid via polling: Rp {commission_amount} to {referrer_id}")
                
                # Commit all changes atomically
                db.commit()
                logger.info(f"✅ Payment processing completed successfully for {transaction_id}")
                
                # Send referrer notification (after commit to ensure data consistency)
                if commission_paid and referrer_id and bot:
                    send_referrer_notification(bot, referrer_id, str(payment.telegram_id), commission_amount)
                
                # Send Telegram notification (outside transaction to avoid blocking)
                if bot:
                    try:
                        telegram_id_str = str(payment.telegram_id)
                        bot.send_message(
                            int(telegram_id_str),
                            f"✅ <b>Pembayaran Berhasil!</b>\n\n"
                            f"Paket: {payment.package_name}\n"
                            f"Status VIP kamu sudah aktif!\n\n"
                            f"Selamat menonton! 🎬",
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Success notification sent to user {payment.telegram_id}")
                    except Exception as bot_error:
                        logger.error(f"❌ Failed to send Telegram notification: {bot_error}")
                else:
                    logger.warning("⚠️ Bot not configured - cannot send payment success notification")
                    
            except HTTPException:
                db.rollback()
                raise
            except Exception as process_error:
                logger.error(f"❌ Error processing payment success: {process_error}")
                logger.exception("Payment processing error details:")
                db.rollback()
                raise HTTPException(status_code=500, detail="Failed to process payment")
        
        elif payment_status == 'expired':
            if current_status == 'pending':
                payment.status = 'expired'  # type: ignore
                db.commit()
                logger.info(f"⏰ Payment expired: {transaction_id}")
            else:
                logger.info(f"⏭️ Payment status already updated to {current_status}, skipping")
        
        elif payment_status in ['failed', 'cancelled']:
            if current_status == 'pending':
                payment.status = 'failed'  # type: ignore
                db.commit()
                logger.info(f"❌ Payment failed/cancelled: {transaction_id}")
            else:
                logger.info(f"⏭️ Payment status already updated to {current_status}, skipping")
        
        elif payment_status == 'pending':
            logger.info(f"⏳ Payment still pending: {transaction_id}")
        
        else:
            logger.warning(f"⚠️ Unknown payment status from QRIS.PW: {payment_status}")
        
        return result
        
    except HTTPException:
        # HTTPException already has proper status code and message
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error in check_payment_status: {e}")
        logger.exception("Full error traceback:")
        try:
            db.rollback()
        except Exception as rollback_error:
            logger.error(f"❌ Rollback failed: {rollback_error}")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        try:
            db.close()
        except Exception as close_error:
            logger.error(f"❌ DB session close failed: {close_error}")

@app.post("/api/v1/upload_screenshot")
async def upload_screenshot(
    transaction_id: str = Form(...),
    screenshot: UploadFile = File(...)
):
    """
    Upload screenshot bukti pembayaran untuk order tertentu.
    Screenshot disimpan di backend_assets/screenshots/ dan URL-nya disimpan ke database.
    
    BUG FIX #1: Secure file upload validation dengan:
    - File size limit (5 MB)
    - Extension whitelist (jpg, jpeg, png, webp)
    - MIME type verification
    - Secure random filename generation
    """
    from file_validation import validate_and_save_upload, delete_file_safe
    
    db = SessionLocal()
    try:
        # Find payment record
        payment = db.query(Payment).filter(Payment.transaction_id == transaction_id).first()
        if not payment:
            raise HTTPException(
                status_code=404,
                detail=f"Payment dengan transaction_id {transaction_id} tidak ditemukan"
            )
        
        # Secure file validation and upload
        screenshots_dir = "backend_assets/screenshots"
        filename_prefix = f"payment_{transaction_id}"
        
        success, file_path, error = await validate_and_save_upload(
            screenshot,
            screenshots_dir,
            filename_prefix
        )
        
        if not success:
            raise HTTPException(status_code=400, detail=error)
        
        if not file_path:
            raise HTTPException(status_code=500, detail="Gagal menyimpan file")
        
        # Delete old screenshot if exists
        if payment.screenshot_url:
            try:
                old_screenshot_path = payment.screenshot_url.replace("/media/", "backend_assets/")
                delete_file_safe(old_screenshot_path)
            except Exception as delete_error:
                logger.warning(f"⚠️ Failed to delete old screenshot: {delete_error}")
        
        # Update payment record with screenshot URL
        filename = os.path.basename(file_path)
        screenshot_url = f"/media/screenshots/{filename}"
        payment.screenshot_url = screenshot_url  # type: ignore
        db.commit()
        
        logger.info(f"✅ Secure screenshot uploaded for payment {transaction_id}: {screenshot_url}")
        
        return {
            "success": True,
            "screenshot_url": screenshot_url,
            "message": "Screenshot berhasil diupload"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error uploading screenshot: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail="Gagal mengupload screenshot")
    finally:
        db.close()

@app.post("/api/v1/create_qris_payment")
async def create_qris_payment(request: PaymentRequest):
    """
    DEPRECATED: Legacy QRIS payment endpoint with static QR codes
    Use /api/v1/create_payment instead for dynamic QRIS via QRIS.PW
    """
    order_id = f"QRIS-{request.telegram_id}-{int(time.time())}"
    
    # QRIS mapping by amount
    qris_mapping = {
        2000: "assets/qris/2000.png",
        5000: "assets/qris/5000.png",
        10000: "assets/qris/10000.png",
        30000: "assets/qris/30000.png",
        150000: "assets/qris/150000.png"
    }
    
    qris_path = qris_mapping.get(request.gross_amount)
    if not qris_path:
        raise HTTPException(status_code=400, detail=f"Paket dengan harga Rp {request.gross_amount} tidak tersedia")
    
    try:
        db = SessionLocal()
        try:
            # Create payment record with status 'qris_pending' and optional screenshot
            payment = Payment(
                telegram_id=str(request.telegram_id),
                order_id=order_id,
                package_name=request.nama_paket,
                amount=request.gross_amount,
                status='qris_pending',
                screenshot_url=request.screenshot_file if request.screenshot_file else None
            )
            db.add(payment)
            db.commit()
            logger.info(f"✅ QRIS payment record created: {order_id} for user {request.telegram_id}")
            if request.screenshot_file:
                logger.info(f"📸 Screenshot uploaded with order {order_id}")
        except Exception as db_error:
            logger.error(f"Error waktu simpan QRIS payment record: {db_error}")
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan data pembayaran: {str(db_error)}")
        finally:
            db.close()
        
        return {
            "order_id": order_id,
            "qris_path": qris_path,
            "amount": request.gross_amount,
            "package_name": request.nama_paket,
            "status": "qris_pending",
            "screenshot_url": request.screenshot_file if request.screenshot_file else None,
            "message": "Scan QRIS dan kirim bukti pembayaran ke admin untuk aktivasi VIP"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu bikin QRIS payment: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/favorites")
async def add_favorite(request: FavoriteRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(
            Movie.id == request.movie_id,
            Movie.deleted_at == None
        ).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        existing = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id),
            Favorite.movie_id == request.movie_id
        ).first()
        
        base_favorite = movie.base_favorite_count if movie.base_favorite_count is not None else 0
        
        if existing:
            from sqlalchemy import func
            user_favorite_count = db.query(func.count(Favorite.id)).filter(
                Favorite.movie_id == request.movie_id
            ).scalar() or 0
            favorite_count = base_favorite + user_favorite_count
            return {"status": "already_favorited", "message": "Film udah ada di favorit", "favorite_count": favorite_count}
        
        favorite = Favorite(
            telegram_id=str(telegram_id),
            movie_id=request.movie_id
        )
        db.add(favorite)
        db.commit()
        
        from sqlalchemy import func
        user_favorite_count = db.query(func.count(Favorite.id)).filter(
            Favorite.movie_id == request.movie_id
        ).scalar() or 0
        favorite_count = base_favorite + user_favorite_count
        
        return {"status": "success", "message": "Film berhasil ditambahin ke favorit", "is_favorited": True, "favorite_count": favorite_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu tambahin favorit: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/favorites/remove")
async def remove_favorite(request: RemoveFavoriteRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(
            Movie.id == request.movie_id,
            Movie.deleted_at == None
        ).first()
        
        favorite = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id),
            Favorite.movie_id == request.movie_id
        ).first()
        
        if not favorite:
            raise HTTPException(status_code=404, detail="Favorit tidak ditemukan")
        
        db.delete(favorite)
        db.commit()
        
        base_favorite = movie.base_favorite_count if (movie and movie.base_favorite_count is not None) else 0
        
        from sqlalchemy import func
        user_favorite_count = db.query(func.count(Favorite.id)).filter(
            Favorite.movie_id == request.movie_id
        ).scalar() or 0
        favorite_count = base_favorite + user_favorite_count
        
        return {
            "status": "success",
            "message": "Favorit berhasil dihapus",
            "is_favorited": False,
            "favorite_count": favorite_count
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu hapus favorit: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/favorites/list")
async def get_favorites(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        favorites = db.query(Favorite).filter(Favorite.telegram_id == str(telegram_id)).all()
        
        favorite_movies = []
        for fav in favorites:
            # BUG FIX #8: Exclude soft-deleted movies
            movie = db.query(Movie).filter(
                Movie.id == fav.movie_id,
                Movie.deleted_at == None
            ).first()
            if movie:
                favorite_movies.append({
                    "id": movie.id,
                    "title": movie.title,
                    "description": movie.description if movie.description is not None else "",
                    "poster_url": movie.poster_url,
                    "poster_file_id": movie.poster_file_id,
                    "video_link": movie.video_link,
                    "category": movie.category if movie.category is not None else "",
                    "views": movie.views if movie.views is not None else 0,
                    "favorited_at": fav.created_at.isoformat() + 'Z' if fav.created_at is not None else None
                })
        
        return {"favorites": favorite_movies}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil daftar favorit: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/likes/toggle")
async def toggle_like(request: LikeRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == request.movie_id,
            Movie.deleted_at == None
        ).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        existing = db.query(Like).filter(
            Like.telegram_id == str(telegram_id),
            Like.movie_id == request.movie_id
        ).first()
        
        base_like = movie.base_like_count if movie.base_like_count is not None else 0
        
        if existing:
            db.delete(existing)
            db.commit()
            
            user_like_count = db.query(Like).filter(Like.movie_id == request.movie_id).count()
            like_count = base_like + user_like_count
            
            return {
                "status": "unliked",
                "message": "Like dihapus",
                "is_liked": False,
                "like_count": like_count
            }
        else:
            like = Like(
                telegram_id=str(telegram_id),
                movie_id=request.movie_id
            )
            db.add(like)
            db.commit()
            
            user_like_count = db.query(Like).filter(Like.movie_id == request.movie_id).count()
            like_count = base_like + user_like_count
            
            return {
                "status": "liked",
                "message": "Film di-like",
                "is_liked": True,
                "like_count": like_count
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu toggle like: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/broadcasts-v2/active")
async def get_active_broadcasts_v2():
    """
    PUBLIC ENDPOINT: Get active v2 broadcasts for mini app (no authentication required).
    This endpoint is accessible from frontend without auth.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import desc
        # BUG FIX #8: Exclude soft-deleted broadcasts
        broadcasts = db.query(Broadcast).filter(
            Broadcast.is_active == True,
            Broadcast.broadcast_type == 'v2',
            Broadcast.deleted_at == None
        ).order_by(desc(Broadcast.created_at)).all()
        
        result = []
        for broadcast in broadcasts:
            result.append({
                "id": broadcast.id,
                "message": broadcast.message,
                "target": broadcast.target,
                "created_at": broadcast.created_at.isoformat() + 'Z' if broadcast.created_at else None
            })
        
        return {"broadcasts": result}
    except Exception as e:
        logger.error(f"Error getting active v2 broadcasts: {e}")
        return {"broadcasts": []}
    finally:
        db.close()

@app.post("/api/v1/watch_history")
async def add_watch_history(request: WatchHistoryRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        watch_entry = WatchHistory(
            telegram_id=str(telegram_id),
            movie_id=request.movie_id
        )
        db.add(watch_entry)
        
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == request.movie_id,
            Movie.deleted_at == None
        ).first()
        if movie:
            current_views = movie.views if movie.views is not None else 0
            movie.views = current_views + 1  # type: ignore
        
        db.commit()
        
        return {"status": "success", "message": "Riwayat tontonan berhasil dicatat"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu tambahin riwayat tontonan: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/watch_history/list")
async def get_watch_history(request: UserDataRequest, limit: int = 20):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        history = db.query(WatchHistory).filter(
            WatchHistory.telegram_id == str(telegram_id)
        ).order_by(WatchHistory.watched_at.desc()).limit(limit).all()
        
        watched_movies = []
        for entry in history:
            # BUG FIX #8: Exclude soft-deleted movies
            movie = db.query(Movie).filter(
                Movie.id == entry.movie_id,
                Movie.deleted_at == None
            ).first()
            if movie:
                watched_movies.append({
                    "id": movie.id,
                    "title": movie.title,
                    "description": movie.description if movie.description is not None else "",
                    "poster_url": movie.poster_url,
                    "video_link": movie.video_link,
                    "category": movie.category if movie.category is not None else "",
                    "views": movie.views if movie.views is not None else 0,
                    "watched_at": entry.watched_at.isoformat() + 'Z' if entry.watched_at is not None else None
                })
        
        return {"watch_history": watched_movies}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil riwayat tontonan: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/v1/categories")
async def get_categories():
    db = SessionLocal()
    try:
        from sqlalchemy import func, distinct
        categories = db.query(distinct(Movie.category)).filter(
            Movie.category.isnot(None),
            Movie.deleted_at == None
        ).all()
        
        category_list = []
        for cat_tuple in categories:
            cat_name = cat_tuple[0]
            if cat_name:
                count = db.query(Movie).filter(
                    Movie.category == cat_name,
                    Movie.deleted_at == None
                ).count()
                category_list.append({
                    "name": cat_name,
                    "count": count
                })
        
        return {"categories": category_list}
    except Exception as e:
        logger.error(f"Error waktu ambil kategori: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/v1/movies/category/{category}")
async def get_movies_by_category(category: str, init_data: str | None = None):
    db = SessionLocal()
    try:
        telegram_id = None
        if init_data:
            try:
                validated_user = validate_telegram_webapp(init_data, allow_missing_token=True)
                if validated_user:
                    telegram_id = str(validated_user['telegram_id'])
            except Exception as e:
                logger.warning(f"Could not validate init_data for category: {e}")
        
        movies = db.query(Movie).filter(
            Movie.category == category,
            Movie.deleted_at == None
        ).order_by(Movie.created_at.desc()).all()
        
        user_likes = set()
        user_favorites = set()
        
        if telegram_id:
            user_likes_query = db.query(Like.movie_id).filter(Like.telegram_id == telegram_id).all()
            user_likes = {like[0] for like in user_likes_query}
            
            user_fav_query = db.query(Favorite.movie_id).filter(Favorite.telegram_id == telegram_id).all()
            user_favorites = {fav[0] for fav in user_fav_query}
        
        from sqlalchemy import func
        like_counts_query = db.query(
            Like.movie_id,
            func.count(Like.id).label('count')
        ).group_by(Like.movie_id).all()
        like_counts = {movie_id: count for movie_id, count in like_counts_query}
        
        favorite_counts_query = db.query(
            Favorite.movie_id,
            func.count(Favorite.id).label('count')
        ).group_by(Favorite.movie_id).all()
        favorite_counts = {movie_id: count for movie_id, count in favorite_counts_query}
        
        movies_list = []
        for movie in movies:
            user_like_count = like_counts.get(movie.id, 0)
            user_favorite_count = favorite_counts.get(movie.id, 0)
            
            base_like = movie.base_like_count if movie.base_like_count is not None else 0
            base_favorite = movie.base_favorite_count if movie.base_favorite_count is not None else 0
            
            like_count = base_like + user_like_count
            favorite_count = base_favorite + user_favorite_count
            
            movies_list.append({
                "id": movie.id,
                "title": movie.title,
                "description": movie.description if movie.description is not None else "",
                "poster_url": movie.poster_url,
                "poster_file_id": movie.poster_file_id,
                "video_link": movie.video_link,
                "category": movie.category if movie.category is not None else "",
                "views": movie.views if movie.views is not None else 0,
                "like_count": like_count,
                "favorite_count": favorite_count,
                "is_liked": movie.id in user_likes if telegram_id else False,
                "is_favorited": movie.id in user_favorites if telegram_id else False
            })
        
        return {"movies": movies_list, "category": category}
    except Exception as e:
        logger.error(f"Error waktu ambil film berdasarkan kategori: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/v1/movies/search")
async def search_movies(q: str = "", sort: str = "terbaru"):
    db = SessionLocal()
    try:
        query = db.query(Movie).filter(Movie.deleted_at == None)
        
        if q:
            search_term = f"%{q}%"
            query = query.filter(
                (Movie.title.like(search_term)) | 
                (Movie.description.like(search_term))
            )
        
        if sort == "populer":
            query = query.order_by(Movie.views.desc())
        else:
            query = query.order_by(Movie.created_at.desc())
        
        movies = query.all()
        
        movies_list = []
        for movie in movies:
            movies_list.append({
                "id": movie.id,
                "title": movie.title,
                "description": movie.description if movie.description is not None else "",
                "poster_url": movie.poster_url,
                "video_link": movie.video_link,
                "category": movie.category if movie.category is not None else "",
                "views": movie.views if movie.views is not None else 0
            })
        
        return {"movies": movies_list, "query": q, "sort": sort}
    except Exception as e:
        logger.error(f"Error waktu pencarian film: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/drama_request")
async def submit_drama_request(request: DramaRequestSubmit):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        drama_req = DramaRequest(
            telegram_id=str(telegram_id),
            judul=request.judul,
            apk_source=request.apk_source,
            status='pending'
        )
        db.add(drama_req)
        db.commit()
        
        return {"status": "success", "message": "Request drama berhasil dikirim"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu kirim request drama: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/drama_requests/list")
async def get_drama_requests(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted drama requests
        requests_list = db.query(DramaRequest).filter(
            DramaRequest.telegram_id == str(telegram_id),
            DramaRequest.deleted_at == None
        ).order_by(DramaRequest.created_at.desc()).all()
        
        result = []
        for req in requests_list:
            result.append({
                "id": req.id,
                "judul": req.judul,
                "apk_source": req.apk_source if req.apk_source is not None else "",
                "status": req.status,
                "admin_notes": req.admin_notes,
                "created_at": req.created_at.isoformat() + 'Z' if req.created_at is not None else None,
                "updated_at": req.updated_at.isoformat() + 'Z' if req.updated_at is not None else None
            })
        
        return {"requests": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil daftar request drama: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/broadcasts/active")
async def get_active_broadcasts():
    """Get all active broadcasts for frontend display"""
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted broadcasts
        broadcasts = db.query(Broadcast).filter(
            Broadcast.is_active == True,
            Broadcast.deleted_at == None
        ).order_by(Broadcast.created_at.desc()).all()
        
        result = []
        for broadcast in broadcasts:
            result.append({
                "id": broadcast.id,
                "message": broadcast.message,
                "target": broadcast.target,
                "created_at": broadcast.created_at.isoformat() + 'Z' if broadcast.created_at is not None else None
            })
        
        return {"broadcasts": result}
    except Exception as e:
        logger.error(f"Error getting active broadcasts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/withdrawal")
async def submit_withdrawal(request: WithdrawalRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted users
        user = db.query(User).filter(
            User.telegram_id == str(telegram_id),
            User.deleted_at == None
        ).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="User ga ketemu")
        
        commission_balance_value: int = cast(int, user.commission_balance)
        if commission_balance_value < request.amount:
            raise HTTPException(
                status_code=400, 
                detail=f"Saldo tidak cukup. Saldo Anda: Rp {commission_balance_value}, Withdraw: Rp {request.amount}"
            )
        
        if request.amount < 50000:
            raise HTTPException(status_code=400, detail="Minimum withdrawal Rp 50.000")
        
        pending_withdrawal = db.query(Withdrawal).filter(
            Withdrawal.telegram_id == str(telegram_id),
            Withdrawal.status == 'pending'
        ).first()
        
        if pending_withdrawal:
            raise HTTPException(status_code=400, detail="Kamu udah punya request withdrawal yang masih pending. Tunggu dulu ya sampe diproses.")
        
        withdrawal = Withdrawal(
            telegram_id=str(telegram_id),
            amount=request.amount,
            payment_method=request.payment_method,
            account_number=request.account_number,
            account_name=request.account_name,
            status='pending'
        )
        db.add(withdrawal)
        db.commit()
        
        return {"status": "success", "message": "Request withdrawal berhasil dikirim"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu kirim request withdrawal: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/withdrawals/list")
async def get_withdrawals(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        withdrawals = db.query(Withdrawal).filter(
            Withdrawal.telegram_id == str(telegram_id)
        ).order_by(Withdrawal.created_at.desc()).all()
        
        result = []
        for wd in withdrawals:
            result.append({
                "id": wd.id,
                "amount": wd.amount,
                "payment_method": wd.payment_method,
                "account_number": wd.account_number,
                "account_name": wd.account_name,
                "status": wd.status,
                "created_at": wd.created_at.isoformat() + 'Z' if wd.created_at is not None else None,
                "processed_at": wd.processed_at.isoformat() + 'Z' if wd.processed_at is not None else None
            })
        
        return {"withdrawals": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil daftar withdrawal: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/user_profile")
async def get_user_profile(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted users
        user = db.query(User).filter(
            User.telegram_id == str(telegram_id),
            User.deleted_at == None
        ).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="User ga ketemu")
        
        watched_count = db.query(WatchHistory).filter(
            WatchHistory.telegram_id == str(telegram_id)
        ).count()
        
        favorites_count = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id)
        ).count()
        
        from datetime import datetime
        is_vip_active = check_and_update_vip_expiry(user, db)
        vip_expires_at_iso = None
        
        if is_vip_active and user.vip_expires_at is not None:
            vip_expires_value: datetime | None = cast(datetime | None, user.vip_expires_at)
            if vip_expires_value is not None:
                vip_expires_at_iso = vip_expires_value.isoformat() + 'Z'
        
        return {
            "telegram_id": telegram_id,
            "username": user.username if user.username is not None else "",
            "ref_code": user.ref_code,
            "is_vip": is_vip_active,
            "vip_expires_at": vip_expires_at_iso,
            "commission_balance": user.commission_balance,
            "total_referrals": user.total_referrals,
            "total_watched": watched_count,
            "total_favorites": favorites_count,
            "created_at": user.created_at.isoformat() + 'Z' if user.created_at is not None else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil profil user: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/pending_payments")
async def get_pending_payments(request: UserDataRequest):
    """
    Endpoint untuk mendapatkan riwayat pembayaran yang belum sukses (pending/failed/expired)
    untuk user tertentu. Digunakan untuk menampilkan history di halaman payment.
    """
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        # Get payments with status: pending, failed, expired, cancelled (not success)
        payments = db.query(Payment).filter(
            Payment.telegram_id == str(telegram_id),
            Payment.status.in_(['pending', 'failed', 'expired', 'cancelled'])
        ).order_by(Payment.created_at.desc()).all()
        
        result = []
        for payment in payments:
            result.append({
                "id": payment.id,
                "order_id": payment.order_id,
                "transaction_id": payment.transaction_id,
                "package_name": payment.package_name,
                "amount": payment.amount,
                "status": payment.status,
                "qris_url": payment.qris_url,
                "expires_at": payment.expires_at.isoformat() + 'Z' if payment.expires_at is not None else None,
                "created_at": payment.created_at.isoformat() + 'Z' if payment.created_at is not None else None,
                "paid_at": payment.paid_at.isoformat() + 'Z' if payment.paid_at is not None else None
            })
        
        return {"payments": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil riwayat pembayaran pending: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/payment_history")
async def get_payment_history(request: UserDataRequest):
    """
    Endpoint untuk mendapatkan SEMUA riwayat pembayaran user,
    dipisahkan menjadi 2 kategori:
    - ongoing: pending (transaksi yang masih berlangsung/menunggu pembayaran)
    - completed: success, failed, expired, cancelled (transaksi yang sudah selesai)
    """
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        all_payments = db.query(Payment).filter(
            Payment.telegram_id == str(telegram_id)
        ).order_by(Payment.created_at.desc()).all()
        
        ongoing = []
        completed = []
        
        for payment in all_payments:
            payment_data = {
                "id": payment.id,
                "order_id": payment.order_id,
                "transaction_id": payment.transaction_id,
                "package_name": payment.package_name,
                "amount": payment.amount,
                "status": payment.status,
                "qris_url": payment.qris_url,
                "expires_at": payment.expires_at.isoformat() + 'Z' if payment.expires_at is not None else None,
                "created_at": payment.created_at.isoformat() + 'Z' if payment.created_at is not None else None,
                "paid_at": payment.paid_at.isoformat() + 'Z' if payment.paid_at is not None else None
            }
            
            if payment.status == 'pending':
                ongoing.append(payment_data)
            else:
                completed.append(payment_data)
        
        return {
            "ongoing": ongoing,
            "completed": completed
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu ambil riwayat pembayaran: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/payment_callback")
async def payment_callback(callback: PaymentCallback):
    if not callback.signature_key:
        logger.error("Ga ada signature_key di callback")
        raise HTTPException(status_code=401, detail="Signature key ga ada")
    
    if not callback.status_code:
        logger.error("Ga ada status_code di callback")
        raise HTTPException(status_code=400, detail="Status code ga ada")
    
    if not callback.gross_amount:
        logger.error("Ga ada gross_amount di callback")
        raise HTTPException(status_code=400, detail="Gross amount ga ada")
    
    # Verifikasi DOKU signature
    signature_string = f"{callback.order_id}{callback.status_code}{callback.gross_amount}{DOKU_SECRET_KEY}"
    calculated_signature = hashlib.sha512(signature_string.encode()).hexdigest()
    
    if not hmac.compare_digest(calculated_signature, callback.signature_key):
        logger.error(f"Verifikasi signature DOKU gagal buat order {callback.order_id}")
        logger.error(f"Yang dihitung: {calculated_signature[:20]}..., Yang diterima: {callback.signature_key[:20]}...")
        raise HTTPException(status_code=401, detail="Signature ga valid - autentikasi callback DOKU gagal")
    
    logger.info(f"✅ Signature DOKU berhasil diverifikasi buat order {callback.order_id}")
    
    db = SessionLocal()
    try:
        logger.info(f"Callback pembayaran diterima: {callback.order_id}, status: {callback.transaction_status}")
        
        payment = db.query(Payment).filter(Payment.order_id == callback.order_id).first()
        
        if not payment:
            logger.error(f"Pembayaran ga ketemu: {callback.order_id}")
            raise HTTPException(status_code=404, detail="Pembayaran ga ketemu")
        
        if callback.transaction_status == 'settlement' or callback.transaction_status == 'capture':
            if callback.fraud_status != 'deny':
                from datetime import datetime, timedelta
                
                payment.status = 'success'  # type: ignore
                payment.paid_at = now_utc()  # type: ignore
                
                # BUG FIX #8: Exclude soft-deleted users
                user = db.query(User).filter(
                    User.telegram_id == payment.telegram_id,
                    User.deleted_at == None
                ).first()
                if user:
                    days_map = {
                        "VIP 1 Hari": 1,
                        "VIP 3 Hari": 3,
                        "VIP 7 Hari": 7,
                        "VIP 30 Hari": 30,
                        "VIP 180 Hari": 180
                    }
                    days = days_map.get(str(payment.package_name), 1)
                    
                    user.is_vip = True  # type: ignore
                    current_expiry_col = user.vip_expires_at
                    current_expiry: datetime | None = cast(datetime | None, current_expiry_col)
                    
                    if current_expiry is not None and current_expiry > now_utc():
                        user.vip_expires_at = current_expiry + timedelta(days=days)  # type: ignore
                    else:
                        user.vip_expires_at = now_utc() + timedelta(days=days)  # type: ignore
                    
                    logger.info(f"VIP diaktifkan buat user {payment.telegram_id} selama {days} hari")
                    
                    # Process referral commission using centralized utility
                    commission_paid, commission_amount, referrer_id = process_referral_commission(db, payment, user)
                    if commission_paid:
                        logger.info(f"💰 Komisi dibayar: Rp {commission_amount} ke user {referrer_id} (referrer dari {payment.telegram_id}) - PEMBAYARAN PERTAMA")
                
                db.commit()
                
                # Send referrer notification (after commit to ensure data consistency)
                if commission_paid and referrer_id and bot:
                    send_referrer_notification(bot, referrer_id, str(payment.telegram_id), commission_amount)
                
                if bot:
                    try:
                        telegram_id_str = str(payment.telegram_id)
                        bot.send_message(
                            int(telegram_id_str),
                            f"✅ <b>Pembayaran Berhasil!</b>\n\n"
                            f"Paket: {payment.package_name}\n"
                            f"Status VIP kamu sudah aktif!\n\n"
                            f"Selamat menonton! 🎬",
                            parse_mode='HTML'
                        )
                    except Exception as bot_error:
                        logger.error(f"Gagal kirim pesan sukses: {bot_error}")
                else:
                    logger.warning("Bot ga dikonfigurasi - ga bisa kirim pesan sukses pembayaran")
                
                return {"status": "success", "message": "Pembayaran berhasil diproses"}
        
        elif callback.transaction_status == 'pending':
            payment.status = 'pending'  # type: ignore
            db.commit()
            return {"status": "pending", "message": "Pembayaran masih pending"}
        
        elif callback.transaction_status in ['deny', 'cancel', 'expire']:
            payment.status = 'failed'  # type: ignore
            db.commit()
            return {"status": "failed", "message": "Pembayaran gagal"}
        
        logger.warning(f"Status transaksi ga dikenal: {callback.transaction_status} buat order {callback.order_id}")
        raise HTTPException(status_code=400, detail=f"Status transaksi ga dikenal: {callback.transaction_status}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu proses callback pembayaran: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# Mount frontend static files (di akhir setelah semua API routes didefinisikan)
if os.path.exists("frontend"):
    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
    logger.info("✅ Frontend mounted at / (fallback for undefined routes)")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
