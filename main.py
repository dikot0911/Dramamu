import time
import logging
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from typing import cast
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from sqlalchemy import text
from database import SessionLocal, User, Movie, Favorite, Like, WatchHistory, DramaRequest, Withdrawal, Payment, init_db, check_and_update_vip_expiry, serialize_movie
from config import DOKU_CLIENT_ID, DOKU_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, URL_CARI_JUDUL, URL_BELI_VIP, ALLOWED_ORIGINS, now_utc, is_production
from telebot import types
from admin_api import router as admin_router
import telegram_delivery
from bot_state import bot_state
import bot as bot_module

# PRODUCTION LOGGING: Structured logging untuk production monitoring
# Level: INFO untuk production, DEBUG untuk development
log_level = logging.INFO if is_production() else logging.DEBUG
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Log environment info pada startup
if is_production():
    logger.info("=" * 60)
    logger.info("🚀 PRODUCTION MODE - Dramamu Bot API Starting")
    logger.info("=" * 60)
else:
    logger.info("=" * 60)
    logger.info("🔧 DEVELOPMENT MODE - Dramamu Bot API Starting")
    logger.info("=" * 60)

app = FastAPI(title="Dramamu API")

app.include_router(admin_router)

allow_credentials = "*" not in ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    # Disable cache untuk semua file HTML, CSS, JS (frontend dan admin panel)
    if (request.url.path.endswith(".css") or 
        request.url.path.endswith(".js") or
        request.url.path.endswith(".html")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

logger.info(f"✅ CORS configured: origins={ALLOWED_ORIGINS}, credentials={allow_credentials}")

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

# Frontend static files - HARUS di-mount SETELAH semua API routes didefinisikan
# Mounting ini ada di akhir file setelah semua routes

@app.on_event("startup")
async def startup_event():
    """Setup database waktu app startup"""
    init_db()
    
    from admin_auth import ensure_admin_exists
    result = ensure_admin_exists()
    
    if result['status'] == 'success':
        logger.info(f"✅ Admin user ready: {result.get('message', 'Admin verified')}")
    elif result['status'] == 'missing_secrets':
        logger.warning("⚠️  Admin panel belum dikonfigurasi - menunggu secrets")
        logger.warning(f"   Kurang: {', '.join(result.get('missing_secrets', []))}")
    else:
        logger.error(f"❌ Admin setup error: {result.get('message', 'Unknown error')}")
    
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
        logger.info("🔧 Development mode - bot pakai polling (dijalankan oleh runner.py)")

if DOKU_CLIENT_ID and DOKU_SECRET_KEY:
    logger.info("✅ DOKU payment gateway initialized")
else:
    logger.warning("⚠️ DOKU credentials belum di-set - Fitur pembayaran terbatas")
    logger.warning("   Set DOKU_CLIENT_ID dan DOKU_SECRET_KEY di environment variables")

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
    amount: int
    payment_method: str
    account_number: str
    account_name: str

class UserDataRequest(BaseModel):
    init_data: str

class PaymentCallback(BaseModel):
    order_id: str
    transaction_status: str
    fraud_status: str | None = None
    signature_key: str | None = None
    status_code: str | None = None
    gross_amount: str | None = None

@app.get("/")
async def root():
    return {"message": "Dramamu Bot API", "status": "ok"}

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
        
        # Download file dari Telegram servers
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
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
        
        query = db.query(Movie)
        
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
            
            like_count = like_counts.get(movie.id, 0)
            favorite_count = favorite_counts.get(movie.id, 0)
            
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
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        
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
async def get_referral_stats(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        
        if user:
            return {
                "ref_code": user.ref_code,
                "commission_balance": user.commission_balance,
                "total_referrals": user.total_referrals
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
        
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            logger.error(f"Film {request.movie_id} ga ketemu")
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
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
    if not (DOKU_CLIENT_ID and DOKU_SECRET_KEY):
        raise HTTPException(status_code=500, detail="DOKU payment gateway belum dikonfigurasi")
    
    import json
    import uuid
    from datetime import datetime, timezone
    import base64
    import requests
    from config import DOKU_API_URL
    
    order_id = f"DRAMAMU-{request.telegram_id}-{int(time.time())}"
    
    try:
        db = SessionLocal()
        try:
            payment = Payment(
                telegram_id=str(request.telegram_id),
                order_id=order_id,
                package_name=request.nama_paket,
                amount=request.gross_amount,
                status='pending'
            )
            db.add(payment)
            db.commit()
        except Exception as db_error:
            logger.error(f"Error waktu simpan record pembayaran: {db_error}")
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Gagal menyimpan data pembayaran: {str(db_error)}")
        finally:
            db.close()
        
        # Call DOKU Jokul Checkout API - Try QRIS first, fallback to VA banks
        request_id = str(uuid.uuid4())
        request_timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        target_path = "/checkout/v1/payment"
        
        # Try payment method options in order of preference
        payment_methods_to_try = [
            ["QRIS"],  # Primary: QRIS
            ["VA_BCA", "VA_MANDIRI", "VA_BNI"],  # Fallback: VA banks
        ]
        
        payment_url = None
        last_error = None
        
        for payment_methods in payment_methods_to_try:
            try:
                # Request body (ensure consistent key ordering for signature)
                request_body = {
                    "order": {
                        "amount": request.gross_amount,
                        "invoice_number": order_id
                    },
                    "payment": {
                        "payment_due_date": 60,
                        "payment_method_types": payment_methods
                    }
                }
                
                # Generate digest (use separators for consistent JSON formatting)
                body_json = json.dumps(request_body, separators=(',', ':'), sort_keys=True)
                digest_value = base64.b64encode(hashlib.sha256(body_json.encode()).digest()).decode()
                
                # Build signature string
                component_signature = (
                    f"Client-Id:{DOKU_CLIENT_ID}\n"
                    f"Request-Id:{request_id}\n"
                    f"Request-Timestamp:{request_timestamp}\n"
                    f"Request-Target:{target_path}\n"
                    f"Digest:{digest_value}"
                )
                
                # Generate HMAC-SHA256 signature
                signature_bytes = hmac.new(
                    DOKU_SECRET_KEY.encode(),
                    component_signature.encode(),
                    hashlib.sha256
                ).digest()
                signature = "HMACSHA256=" + base64.b64encode(signature_bytes).decode()
                
                # Call DOKU API
                headers = {
                    "Client-Id": DOKU_CLIENT_ID,
                    "Request-Id": request_id,
                    "Request-Timestamp": request_timestamp,
                    "Signature": signature,
                    "Content-Type": "application/json"
                }
                
                logger.info(f"📤 Calling DOKU API for order {order_id} with methods: {payment_methods}")
                
                response = requests.post(
                    f"{DOKU_API_URL}{target_path}",
                    data=body_json,
                    headers=headers,
                    timeout=30
                )
                
                logger.info(f"DOKU API response status: {response.status_code} for methods: {payment_methods}")
                
                if response.status_code == 200:
                    result = response.json()
                    payment_url = result.get("response", {}).get("payment", {}).get("url")
                    if payment_url:
                        logger.info(f"✅ DOKU payment URL created with methods: {payment_methods}")
                        break
                else:
                    last_error = f"Status {response.status_code}: {response.text}"
                    logger.warning(f"⚠️ Failed with {payment_methods}: {last_error}")
                    continue
                    
            except Exception as e:
                last_error = str(e)
                logger.warning(f"⚠️ Error trying {payment_methods}: {last_error}")
                continue
        
        if not payment_url:
            logger.error(f"❌ All payment methods failed. Last error: {last_error}")
            if last_error and ("PAYMENT CHANNEL IS INACTIVE" in last_error or "CHANNEL IS INACTIVE" in last_error):
                error_detail = "Tidak ada payment method yang aktif di DOKU Dashboard Anda. Silakan enable minimal satu metode pembayaran di https://dashboard.doku.com → Settings → Payment Methods"
            else:
                error_detail = f"Gagal membuat payment link. Error: {last_error}"
            raise HTTPException(status_code=500, detail=error_detail)
        
        result = response.json()
        payment_url = result.get("response", {}).get("payment", {}).get("url")
        
        if not payment_url:
            logger.error(f"❌ No payment URL in DOKU response: {result}")
            raise HTTPException(status_code=500, detail="DOKU tidak mengembalikan payment URL")
        
        logger.info(f"✅ DOKU payment URL created: {payment_url}")
        
        return {
            "order_id": order_id,
            "payment_url": payment_url,
            "amount": request.gross_amount
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu bikin pembayaran: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/create_qris_payment")
async def create_qris_payment(request: PaymentRequest):
    """
    Temporary QRIS payment endpoint - creates payment record and returns QRIS path
    User akan scan QRIS dan kirim bukti pembayaran ke admin untuk manual verification
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
            # Create payment record with status 'qris_pending'
            payment = Payment(
                telegram_id=str(request.telegram_id),
                order_id=order_id,
                package_name=request.nama_paket,
                amount=request.gross_amount,
                status='qris_pending'
            )
            db.add(payment)
            db.commit()
            logger.info(f"✅ QRIS payment record created: {order_id} for user {request.telegram_id}")
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
        existing = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id),
            Favorite.movie_id == request.movie_id
        ).first()
        
        if existing:
            return {"status": "already_favorited", "message": "Film udah ada di favorit"}
        
        favorite = Favorite(
            telegram_id=str(telegram_id),
            movie_id=request.movie_id
        )
        db.add(favorite)
        db.commit()
        
        return {"status": "success", "message": "Film berhasil ditambahin ke favorit"}
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
        favorite = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id),
            Favorite.movie_id == request.movie_id
        ).first()
        
        if not favorite:
            raise HTTPException(status_code=404, detail="Favorit tidak ditemukan")
        
        db.delete(favorite)
        db.commit()
        
        from sqlalchemy import func
        favorite_count = db.query(func.count(Favorite.id)).filter(
            Favorite.movie_id == request.movie_id
        ).scalar() or 0
        
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
            movie = db.query(Movie).filter(Movie.id == fav.movie_id).first()
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
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        existing = db.query(Like).filter(
            Like.telegram_id == str(telegram_id),
            Like.movie_id == request.movie_id
        ).first()
        
        if existing:
            db.delete(existing)
            db.commit()
            
            like_count = db.query(Like).filter(Like.movie_id == request.movie_id).count()
            
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
            
            like_count = db.query(Like).filter(Like.movie_id == request.movie_id).count()
            
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
        
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
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
            movie = db.query(Movie).filter(Movie.id == entry.movie_id).first()
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
        categories = db.query(distinct(Movie.category)).filter(Movie.category.isnot(None)).all()
        
        category_list = []
        for cat_tuple in categories:
            cat_name = cat_tuple[0]
            if cat_name:
                count = db.query(Movie).filter(Movie.category == cat_name).count()
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
async def get_movies_by_category(category: str):
    db = SessionLocal()
    try:
        movies = db.query(Movie).filter(Movie.category == category).all()
        
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
        query = db.query(Movie)
        
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
        requests_list = db.query(DramaRequest).filter(
            DramaRequest.telegram_id == str(telegram_id)
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

@app.post("/api/v1/withdrawal")
async def submit_withdrawal(request: WithdrawalRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        
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
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        
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

@app.post("/api/v1/payment_history")
async def get_payment_history(request: UserDataRequest):
    validated_user = validate_telegram_webapp(request.init_data)
    assert validated_user is not None, "validate_telegram_webapp should raise HTTPException if validation fails"
    telegram_id = validated_user['telegram_id']
    
    db = SessionLocal()
    try:
        payments = db.query(Payment).filter(
            Payment.telegram_id == str(telegram_id)
        ).order_by(Payment.created_at.desc()).all()
        
        result = []
        for payment in payments:
            result.append({
                "id": payment.id,
                "order_id": payment.order_id,
                "package_name": payment.package_name,
                "amount": payment.amount,
                "status": payment.status,
                "created_at": payment.created_at.isoformat() + 'Z' if payment.created_at is not None else None,
                "paid_at": payment.paid_at.isoformat() + 'Z' if payment.paid_at is not None else None
            })
        
        return {"payments": result}
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
                
                user = db.query(User).filter(User.telegram_id == payment.telegram_id).first()
                if user:
                    days_map = {
                        "VIP 1 Hari": 1,
                        "VIP 3 Hari": 3,
                        "VIP 7 Hari": 7
                    }
                    package_name_str = str(payment.package_name)
                    days = days_map.get(package_name_str, 1)
                    
                    user.is_vip = True  # type: ignore
                    current_expiry_col = user.vip_expires_at
                    current_expiry: datetime | None = cast(datetime | None, current_expiry_col)
                    
                    if current_expiry is not None and current_expiry > now_utc():
                        user.vip_expires_at = current_expiry + timedelta(days=days)  # type: ignore
                    else:
                        user.vip_expires_at = now_utc() + timedelta(days=days)  # type: ignore
                    
                    logger.info(f"VIP diaktifkan buat user {payment.telegram_id} selama {days} hari")
                    
                    referred_by_code_value = cast(str | None, user.referred_by_code)
                    if referred_by_code_value:
                        is_first_payment = db.query(Payment).filter(
                            Payment.telegram_id == payment.telegram_id,
                            Payment.status == 'success',
                            Payment.id != payment.id
                        ).first() is None
                        
                        if is_first_payment:
                            referrer = db.query(User).filter(User.ref_code == referred_by_code_value).first()
                            if referrer:
                                payment_amount = cast(int, payment.amount)
                                commission = int(payment_amount * 0.25)
                                
                                from sqlalchemy import update
                                db.execute(
                                    update(User)
                                    .where(User.id == referrer.id)
                                    .values(commission_balance=User.commission_balance + commission)
                                )
                                logger.info(f"💰 Komisi dibayar: Rp {commission} ke user {referrer.telegram_id} (referrer dari {payment.telegram_id}) - PEMBAYARAN PERTAMA")
                            else:
                                logger.warning(f"Referrer dengan kode {referred_by_code_value} ga ketemu buat user {payment.telegram_id}")
                        else:
                            logger.info(f"⏭️ Skip komisi - bukan pembayaran pertama buat user {payment.telegram_id}")
                
                db.commit()
                
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
