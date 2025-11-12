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
from database import SessionLocal, User, Movie, Favorite, Like, WatchHistory, DramaRequest, Withdrawal, Payment, init_db
from config import MIDTRANS_SERVER_KEY, MIDTRANS_CLIENT_KEY, TELEGRAM_BOT_TOKEN, URL_CARI_JUDUL, URL_BELI_VIP, ALLOWED_ORIGINS
from telebot import TeleBot, types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dramamu API")

allow_credentials = "*" not in ALLOWED_ORIGINS

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info(f"✅ CORS configured: origins={ALLOWED_ORIGINS}, credentials={allow_credentials}")

if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
    logger.info("✅ Static files mounted at /static")
else:
    logger.info("ℹ️ Static directory not found - frontend deployed separately")

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

bot = TeleBot(TELEGRAM_BOT_TOKEN)

def validate_telegram_webapp(init_data: str, bot_token: str = TELEGRAM_BOT_TOKEN) -> dict:
    """
    Cek validasi signature initData dari Telegram WebApp
    Dokumentasi: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    
    Return data user kalo valid, throw error kalo ga valid
    """
    try:
        parsed_data = dict(parse_qsl(init_data))
    except Exception as e:
        logger.error(f"Failed to parse initData: {e}")
        raise HTTPException(status_code=401, detail="Data tidak valid")
    
    if 'hash' not in parsed_data:
        logger.error("No hash found in initData")
        raise HTTPException(status_code=401, detail="Signature tidak ditemukan")
    
    received_hash = parsed_data.pop('hash')
    
    if 'auth_date' in parsed_data:
        try:
            auth_date = int(parsed_data['auth_date'])
            current_time = int(time.time())
            time_diff = current_time - auth_date
            
            if time_diff > 86400:
                logger.error(f"initData too old: {time_diff} seconds")
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
        logger.error(f"Hash mismatch! Received: {received_hash[:10]}..., Calculated: {calculated_hash[:10]}...")
        raise HTTPException(status_code=401, detail="Signature tidak valid - autentikasi gagal")
    
    logger.info("✅ Telegram WebApp signature validated successfully")
    
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
        logger.error(f"Failed to parse user data: {e}")
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

@app.head("/api")
@app.get("/api")
async def api_health():
    """Endpoint buat ngecek API masih hidup"""
    return {"status": "ok", "message": "ready"}

@app.get("/api/v1/movies")
async def get_all_movies(sort: str = "terbaru", init_data: str | None = None):
    db = SessionLocal()
    try:
        telegram_id = None
        if init_data:
            try:
                validated_user = validate_telegram_webapp(init_data)
                telegram_id = str(validated_user['telegram_id'])
            except:
                pass
        
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
        
    except Exception as e:
        logger.error(f"Error fetching movies: {e}")
        return {"movies": []}
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
            return {"telegram_id": telegram_id, "is_vip": user.is_vip}
        else:
            return {"telegram_id": telegram_id, "is_vip": False, "status": "user_not_found"}
    except HTTPException:
        raise        
    except Exception as e:
        logger.error(f"Error fetching user status: {e}")
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
        logger.error(f"Error fetching referral stats: {e}")
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
        logger.info(f"🎬 Movie selection received: User {telegram_id} (authenticated) → Movie {request.movie_id}")
        
        movie = db.query(Movie).filter(Movie.id == request.movie_id).first()
        if not movie:
            logger.error(f"Movie {request.movie_id} not found")
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        user = db.query(User).filter(User.telegram_id == str(telegram_id)).first()
        if not user:
            logger.warning(f"User {telegram_id} not found in database")
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        is_vip_col: bool = cast(bool, user.is_vip)
        is_vip_user = is_vip_col
        from datetime import datetime
        vip_expires_value: datetime | None = cast(datetime | None, user.vip_expires_at)
        current_time = datetime.now()
        if vip_expires_value is not None and vip_expires_value <= current_time:
            is_vip_user = False
            user.is_vip = False  # type: ignore
            user.vip_expires_at = None  # type: ignore
            db.commit()
        
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
            logger.info(f"✅ User {telegram_id} is VIP - sending movie")
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
            logger.info(f"⚠️ User {telegram_id} is NOT VIP - sending upgrade prompt")
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
            'sample-1': 'static/posters/cincin-lepas.jpg',
            'sample-2': 'static/posters/tuan-su.jpg',
            'sample-3': 'static/posters/suami-dengar.jpg',
            'sample-4': 'static/posters/jodoh-sempurna.jpg'
        }
        
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
                logger.info(f"✅ Message with poster sent successfully to user {telegram_id}")
            else:
                logger.warning(f"Poster not found for movie {movie_id}, sending text only")
                bot.send_message(
                    telegram_id,
                    caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Text message sent to user {telegram_id}")
            
            return {"status": "success", "message": "Movie sent to user"}
            
        except Exception as send_error:
            logger.error(f"❌ Failed to send photo: {send_error}")
            try:
                bot.send_message(
                    telegram_id,
                    caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Fallback message sent to user {telegram_id}")
                return {"status": "success", "message": "Movie sent to user (text only)"}
            except Exception as fallback_error:
                logger.error(f"❌ Fallback message also failed: {fallback_error}")
                raise HTTPException(status_code=500, detail=f"Gagal kirim pesan: {str(fallback_error)}")
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error in select_movie: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
    if not midtrans_client:
        raise HTTPException(status_code=500, detail="Payment gateway not available")
    
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
            logger.error(f"Error saving payment record: {db_error}")
        finally:
            db.close()
        
        return {"snap_token": snap_token, "order_id": order_id}
    
    except Exception as e:
        logger.error(f"Error creating payment: {e}")
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
            return {"status": "already_favorited", "message": "Movie already in favorites"}
        
        favorite = Favorite(
            telegram_id=str(telegram_id),
            movie_id=request.movie_id
        )
        db.add(favorite)
        db.commit()
        
        return {"status": "success", "message": "Movie added to favorites"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding favorite: {e}")
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
        
        return {"status": "success", "message": "Favorite removed"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error removing favorite: {e}")
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
        logger.error(f"Error fetching favorites: {e}")
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
                "message": "Like removed",
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
                "message": "Movie liked",
                "is_liked": True,
                "like_count": like_count
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling like: {e}")
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
        
        return {"status": "success", "message": "Watch history recorded"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding watch history: {e}")
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
        logger.error(f"Error fetching watch history: {e}")
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
        logger.error(f"Error fetching categories: {e}")
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
        logger.error(f"Error fetching movies by category: {e}")
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
        logger.error(f"Error searching movies: {e}")
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
        
        return {"status": "success", "message": "Drama request submitted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting drama request: {e}")
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
        logger.error(f"Error fetching drama requests: {e}")
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
            raise HTTPException(status_code=404, detail="User not found")
        
        commission_balance_value: int = cast(int, user.commission_balance)
        if commission_balance_value < request.amount:
            raise HTTPException(status_code=400, detail="Insufficient balance")
        
        if request.amount < 50000:
            raise HTTPException(status_code=400, detail="Minimum withdrawal is Rp 50,000")
        
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
        
        return {"status": "success", "message": "Withdrawal request submitted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting withdrawal: {e}")
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
        logger.error(f"Error fetching withdrawals: {e}")
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
            raise HTTPException(status_code=404, detail="User not found")
        
        watched_count = db.query(WatchHistory).filter(
            WatchHistory.telegram_id == str(telegram_id)
        ).count()
        
        favorites_count = db.query(Favorite).filter(
            Favorite.telegram_id == str(telegram_id)
        ).count()
        
        from datetime import datetime
        is_vip_active = False
        vip_expires_at_iso = None
        
        is_vip_col: bool = cast(bool, user.is_vip)
        if is_vip_col:
            vip_expires_value: datetime | None = cast(datetime | None, user.vip_expires_at)
            if vip_expires_value is not None and vip_expires_value > datetime.now():
                is_vip_active = True
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
        logger.error(f"Error fetching user profile: {e}")
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
        logger.error(f"Error fetching payment history: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/v1/payment_callback")
async def payment_callback(callback: PaymentCallback):
    if not callback.signature_key:
        logger.error("Missing signature_key in callback")
        raise HTTPException(status_code=401, detail="Missing signature_key")
    
    if not callback.status_code:
        logger.error("Missing status_code in callback")
        raise HTTPException(status_code=400, detail="Missing status_code")
    
    if not callback.gross_amount:
        logger.error("Missing gross_amount in callback")
        raise HTTPException(status_code=400, detail="Missing gross_amount")
    
    signature_string = f"{callback.order_id}{callback.status_code}{callback.gross_amount}{MIDTRANS_SERVER_KEY}"
    calculated_signature = hashlib.sha512(signature_string.encode()).hexdigest()
    
    if not hmac.compare_digest(calculated_signature, callback.signature_key):
        logger.error(f"Signature verification failed for order {callback.order_id}")
        logger.error(f"Calculated: {calculated_signature[:20]}..., Received: {callback.signature_key[:20]}...")
        raise HTTPException(status_code=401, detail="Invalid signature - callback authentication failed")
    
    logger.info(f"✅ Midtrans signature verified for order {callback.order_id}")
    
    db = SessionLocal()
    try:
        logger.info(f"Payment callback received: {callback.order_id}, status: {callback.transaction_status}")
        
        payment = db.query(Payment).filter(Payment.order_id == callback.order_id).first()
        
        if not payment:
            logger.error(f"Payment not found: {callback.order_id}")
            raise HTTPException(status_code=404, detail="Payment not found")
        
        if callback.transaction_status == 'settlement' or callback.transaction_status == 'capture':
            if callback.fraud_status != 'deny':
                from datetime import datetime, timedelta
                
                payment.status = 'success'  # type: ignore
                payment.paid_at = datetime.now()  # type: ignore
                
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
                    
                    if current_expiry is not None and current_expiry > datetime.now():
                        user.vip_expires_at = current_expiry + timedelta(days=days)  # type: ignore
                    else:
                        user.vip_expires_at = datetime.now() + timedelta(days=days)  # type: ignore
                    
                    logger.info(f"VIP activated for user {payment.telegram_id} for {days} days")
                
                db.commit()
                
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
                    logger.error(f"Failed to send success message: {bot_error}")
                
                return {"status": "success", "message": "Payment processed"}
        
        elif callback.transaction_status == 'pending':
            payment.status = 'pending'  # type: ignore
            db.commit()
            return {"status": "pending", "message": "Payment pending"}
        
        elif callback.transaction_status in ['deny', 'cancel', 'expire']:
            payment.status = 'failed'  # type: ignore
            db.commit()
            return {"status": "failed", "message": "Payment failed"}
        
        return {"status": "unknown", "message": "Unknown transaction status"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing payment callback: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/health")
async def health_check():
    db = SessionLocal()
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except:
        db_status = "disconnected"
    finally:
        db.close()
    
    return {
        "status": "healthy",
        "database": db_status,
        "midtrans": "initialized" if midtrans_client else "not initialized"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
