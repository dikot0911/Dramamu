import time
import logging
import hmac
import hashlib
import os
from urllib.parse import parse_qsl
from typing import cast
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
import midtransclient
from database import SessionLocal, User, Movie, Favorite, Like, WatchHistory, DramaRequest, Withdrawal, Payment, init_db, check_and_update_vip_expiry
from config import MIDTRANS_SERVER_KEY, MIDTRANS_CLIENT_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, URL_CARI_JUDUL, URL_BELI_VIP, ALLOWED_ORIGINS, now_utc
from telebot import TeleBot, types
from admin_api import router as admin_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

logger.info(f"✅ CORS configured: origins={ALLOWED_ORIGINS}, credentials={allow_credentials}")

if os.path.exists("admin"):
    app.mount("/panel", StaticFiles(directory="admin", html=True), name="admin_panel")
    logger.info("✅ Admin panel mounted at /panel")
else:
    logger.warning("⚠️ Admin directory not found")

if os.path.exists("backend_assets"):
    app.mount("/media", StaticFiles(directory="backend_assets"), name="media")
    logger.info("✅ Backend assets mounted at /media")

@app.on_event("startup")
async def startup_event():
    """Setup database waktu app startup"""
    init_db()

try:
    midtrans_client = midtransclient.Snap(
        is_production=False,
        server_key=MIDTRANS_SERVER_KEY,
        client_key=MIDTRANS_CLIENT_KEY
    )
    logger.info("✅ Midtrans client initialized")
except Exception as e:
    logger.error(f"❌ Midtrans initialization failed: {e}")
    midtrans_client = None

bot = None
if TELEGRAM_BOT_TOKEN:
    try:
        bot = TeleBot(TELEGRAM_BOT_TOKEN)
        logger.info("✅ Telegram bot initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Telegram bot: {e}")
        bot = None
else:
    logger.warning("⚠️ Telegram bot NOT initialized - TELEGRAM_BOT_TOKEN not configured")

def validate_telegram_webapp(init_data: str, bot_token: str | None = TELEGRAM_BOT_TOKEN) -> dict:
    """
    Cek validasi signature initData dari Telegram WebApp
    Dokumentasi: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    
    Return data user kalo valid, throw error kalo ga valid
    """
    if not bot_token:
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
    """Endpoint buat ngecek kesehatan server (buat Render dan monitoring)"""
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
    midtrans_status = "configured" if midtrans_client else "not_configured"
    bot_status = "configured" if bot else "not_configured"
    
    overall_status = "ok" if db_status == "healthy" else "degraded"
    
    return {
        "status": overall_status,
        "database": db_status,
        "midtrans": midtrans_status,
        "telegram_bot": bot_status,
        "service": "dramamu-api",
        "version": "1.0.0"
    }

@app.head("/api")
@app.get("/api")
async def api_health():
    """Endpoint buat ngecek API masih hidup"""
    return {"status": "ok", "message": "ready"}

@app.get("/api/v1/config")
async def get_public_config():
    """Return public configuration untuk frontend"""
    return {
        "midtrans_client_key": MIDTRANS_CLIENT_KEY,
        "bot_username": TELEGRAM_BOT_USERNAME
    }

@app.get("/api/v1/movies")
async def get_all_movies(sort: str = "terbaru", init_data: str | None = None):
    db = SessionLocal()
    try:
        telegram_id = None
        if init_data:
            try:
                validated_user = validate_telegram_webapp(init_data)
                telegram_id = str(validated_user['telegram_id'])
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
        
        def escape_html(text):
            if not text:
                return text
            return (text
                    .replace('&', '&amp;')
                    .replace('<', '&lt;')
                    .replace('>', '&gt;')
                    .replace('"', '&quot;')
                    .replace("'", '&#39;'))
        
        safe_title = escape_html(movie.title)
        safe_description = escape_html(movie.description)
        
        is_vip_check = bool(is_vip_user)
        if is_vip_check:
            logger.info(f"✅ User {telegram_id} adalah VIP - kirim film")
            caption = (
                f"🎬 <b>{safe_title}</b>\n\n"
                f"{safe_description}\n\n"
                f"Selamat menonton!"
            )
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            btn_tonton = types.InlineKeyboardButton("▶️ Tonton Sekarang", url=str(movie.video_link))
            btn_download = types.InlineKeyboardButton("📥 Download", url=str(movie.video_link))
            btn_pilih_lagi = types.InlineKeyboardButton("🎬 Pilih Film Lain", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
            
            markup.add(btn_tonton, btn_download)
            markup.add(btn_pilih_lagi)
            
        else:
            logger.info(f"⚠️ User {telegram_id} belum VIP - kirim ajakan upgrade")
            caption = (
                f"🔒 <b>{safe_title}</b>\n\n"
                f"Konten ini khusus untuk member VIP.\n\n"
                f"Kamu belum menjadi member VIP.\n"
                f"Upgrade ke VIP untuk menonton film ini."
            )
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            btn_join_vip = types.InlineKeyboardButton("⭐ Join VIP Sekarang", web_app=types.WebAppInfo(url=URL_BELI_VIP))
            btn_pilih_lagi = types.InlineKeyboardButton("🎬 Pilih Film Lain", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
            
            markup.add(btn_join_vip)
            markup.add(btn_pilih_lagi)
        
        poster_map = {
            'sample-1': 'backend_assets/posters/cincin-lepas.jpg',
            'sample-2': 'backend_assets/posters/tuan-su.jpg',
            'sample-3': 'backend_assets/posters/suami-dengar.jpg',
            'sample-4': 'backend_assets/posters/jodoh-sempurna.jpg'
        }
        
        if not bot:
            raise HTTPException(status_code=503, detail="Telegram bot tidak dikonfigurasi")
        
        try:
            movie_id = str(movie.id)
            poster_path = poster_map.get(movie_id)
            
            if poster_path and os.path.exists(poster_path):
                with open(poster_path, 'rb') as photo:
                    bot.send_photo(
                        telegram_id,
                        photo,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
                logger.info(f"✅ Pesan dengan poster berhasil dikirim ke user {telegram_id}")
            else:
                logger.warning(f"Poster ga ketemu buat film {movie_id}, kirim teks aja")
                bot.send_message(
                    telegram_id,
                    caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Pesan teks dikirim ke user {telegram_id}")
            
            return {"status": "success", "message": "Film berhasil dikirim ke user"}
            
        except Exception as send_error:
            logger.error(f"❌ Gagal kirim foto: {send_error}")
            try:
                bot.send_message(
                    telegram_id,
                    caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Pesan fallback dikirim ke user {telegram_id}")
                return {"status": "success", "message": "Film berhasil dikirim (teks saja)"}
            except Exception as fallback_error:
                logger.error(f"❌ Pesan fallback juga gagal: {fallback_error}")
                raise HTTPException(status_code=500, detail=f"Gagal kirim pesan: {str(fallback_error)}")
                
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
    if not midtrans_client:
        raise HTTPException(status_code=500, detail="Payment gateway ga tersedia")
    
    order_id = f"DRAMAMU-{request.telegram_id}-{int(time.time())}"
    
    transaction_details = {
        "order_id": order_id,
        "gross_amount": request.gross_amount,
    }
    
    customer_details = {
        "first_name": "User",
        "last_name": str(request.telegram_id),
        "email": f"{request.telegram_id}@dramamu.com",
        "phone": "08123456789", 
    }

    item_details = [
        {
            "id": f"VIP-{request.paket_id}",
            "price": request.gross_amount,
            "quantity": 1,
            "name": request.nama_paket,
        }
    ]

    transaction_data = {
        "transaction_details": transaction_details,
        "customer_details": customer_details,
        "item_details": item_details,
    }

    try:
        snap_response = midtrans_client.create_transaction(transaction_data)
        snap_token = snap_response['token']
        
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
        
        return {"snap_token": snap_token, "order_id": order_id}
    
    except Exception as e:
        logger.error(f"Error waktu bikin pembayaran: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/favorites")
async def add_favorite(request: FavoriteRequest):
    validated_user = validate_telegram_webapp(request.init_data)
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
                    "video_link": movie.video_link,
                    "category": movie.category if movie.category is not None else "",
                    "views": movie.views if movie.views is not None else 0,
                    "favorited_at": fav.created_at.isoformat() if fav.created_at is not None else None
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
                    "watched_at": entry.watched_at.isoformat() if entry.watched_at is not None else None
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
                "created_at": req.created_at.isoformat() if req.created_at is not None else None
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
                "created_at": wd.created_at.isoformat() if wd.created_at is not None else None,
                "processed_at": wd.processed_at.isoformat() if wd.processed_at is not None else None
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
                vip_expires_at_iso = vip_expires_value.isoformat()
        
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
            "created_at": user.created_at.isoformat() if user.created_at is not None else None
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
                "created_at": payment.created_at.isoformat() if payment.created_at is not None else None,
                "paid_at": payment.paid_at.isoformat() if payment.paid_at is not None else None
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
    
    signature_string = f"{callback.order_id}{callback.status_code}{callback.gross_amount}{MIDTRANS_SERVER_KEY}"
    calculated_signature = hashlib.sha512(signature_string.encode()).hexdigest()
    
    if not hmac.compare_digest(calculated_signature, callback.signature_key):
        logger.error(f"Verifikasi signature gagal buat order {callback.order_id}")
        logger.error(f"Yang dihitung: {calculated_signature[:20]}..., Yang diterima: {callback.signature_key[:20]}...")
        raise HTTPException(status_code=401, detail="Signature ga valid - autentikasi callback gagal")
    
    logger.info(f"✅ Signature Midtrans berhasil diverifikasi buat order {callback.order_id}")
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
