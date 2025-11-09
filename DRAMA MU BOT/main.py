import psycopg2
import time
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Depends
from pydantic import BaseModel, validator
from fastapi.middleware.cors import CORSMiddleware
import midtransclient
import hashlib
import hmac
from urllib.parse import parse_qs
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import httpx

# --- DATA KONEKSI DATABASE ---
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- INFO MIDTRANS ---
MIDTRANS_SERVER_KEY = os.environ.get("MIDTRANS_SERVER_KEY")
MIDTRANS_CLIENT_KEY = os.environ.get("MIDTRANS_CLIENT_KEY")

# --- BOT CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "dramamu_bot")

# Inisialisasi Midtrans
midtrans_client = midtransclient.Snap(
    is_production=False,
    server_key=MIDTRANS_SERVER_KEY or "",
    client_key=MIDTRANS_CLIENT_KEY or ""
)

# Buat aplikasi FastAPI dengan rate limiting
app = FastAPI(title="Dramamu API", version="1.0.0")

# Rate Limiter
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fungsi untuk konek ke DB
def get_db_connection():
    try:
        if not DATABASE_URL:
            print("DATABASE_URL tidak tersedia!")
            return None
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"GAGAL KONEK KE DB: {e}")
        return None

# --- VALIDASI INIT_DATA TELEGRAM ---
def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """
    Validasi init_data dari Telegram WebApp menggunakan HMAC-SHA256
    """
    try:
        if not init_data or not bot_token:
            return False

        # Parse query string
        parsed = parse_qs(init_data)

        # Extract hash
        received_hash = parsed.get('hash', [''])[0]
        if not received_hash:
            return False

        # Remove hash dan sort keys
        keys = [k for k in parsed.keys() if k != 'hash']
        keys.sort()

        # Buat data_check_string
        data_check_string = "\n".join([f"{k}={parsed[k][0]}" for k in keys])

        # Calculate secret key
        secret_key = hmac.new(
            key=b"WebAppData", 
            msg=bot_token.encode(), 
            digestmod=hashlib.sha256
        ).digest()

        # Calculate HMAC
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        return received_hash == calculated_hash

    except Exception as e:
        print(f"Error validating init_data: {e}")
        return False

# --- CEK STATUS VIP USER ---
def check_vip_status(telegram_id: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False

    is_vip = False
    try:
        cur = conn.cursor()

        # Gunakan UPSERT untuk hindari race condition
        cur.execute("""
            INSERT INTO users (telegram_id, is_vip, created_at) 
            VALUES (%s, %s, NOW())
            ON CONFLICT (telegram_id) 
            DO UPDATE SET telegram_id = EXCLUDED.telegram_id
            RETURNING is_vip;
        """, (telegram_id, False))

        result = cur.fetchone()
        is_vip = result[0] if result else False
        conn.commit()

    except Exception as e:
        print(f"Error cek VIP: {e}")
        try:
            conn.rollback()
        except:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

    return is_vip

# --- AMBIL DETAIL FILM ---
def get_movie_details(movie_id: int) -> Optional[dict]:
    conn = get_db_connection()
    if not conn:
        return None

    movie: Optional[dict] = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link, poster_url FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        if row:
            movie = {
                "title": row[0] or "Judul Tidak Tersedia",
                "video_link": row[1] or "#",
                "poster_url": row[2] or "https://via.placeholder.com/300x450/333333/FFFFFF?text=No+Image"
            }
        cur.close()
    except Exception as e:
        print(f"Error ambil movie: {e}")
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass
    return movie

# --- MODEL VALIDATION ---
class PaymentRequest(BaseModel):
    telegram_id: int
    paket_id: int
    gross_amount: int
    nama_paket: str

    @validator('gross_amount')
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be positive')
        return v

    @validator('telegram_id')
    def validate_telegram_id(cls, v):
        if v <= 0:
            raise ValueError('Invalid Telegram ID')
        return v

# --- HEALTH CHECK ---
@app.get("/")
async def root():
    return {
        "message": "Halo, ini API Dramamu!",
        "status": "active",
        "timestamp": datetime.now().isoformat()
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    db_status = "healthy"
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            db_status = "unhealthy"
    except:
        db_status = "unhealthy"
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

    return {
        "status": "healthy",
        "database": db_status,
        "timestamp": datetime.now().isoformat()
    }

# --- API BARU UNTUK NGASIH DATA STATS REFERRAL ---
@app.get("/api/v1/referral_stats/{telegram_id}")
@limiter.limit("30/minute")
async def get_referral_stats(request: Request, telegram_id: int):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT referral_code, commission_balance, total_referrals FROM users WHERE telegram_id = %s;",
            (telegram_id,)
        )
        user_stats = cur.fetchone()
        cur.close()

        if user_stats:
            return {
                "referral_code": user_stats[0] or f"DRAMA{telegram_id}",
                "commission_balance": float(user_stats[1] or 0),
                "total_referrals": user_stats[2] or 0
            }
        else:
            return {
                "referral_code": f"DRAMA{telegram_id}",
                "commission_balance": 0,
                "total_referrals": 0
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# --- AMBIL DAFTAR FILM ---
@app.get("/api/v1/movies")
@limiter.limit("60/minute")
async def get_all_movies(request: Request):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, description, poster_url, video_link FROM movies WHERE active = true ORDER BY created_at DESC;")
        movies_raw = cur.fetchall()
        cur.close()

        movies_list = []
        for movie in movies_raw:
            movies_list.append({
                "id": movie[0],
                "title": movie[1] or "Judul Tidak Tersedia",
                "description": movie[2] or "Deskripsi tidak tersedia",
                "poster_url": movie[3] or "https://via.placeholder.com/300x450/333333/FFFFFF?text=No+Image",
                "video_link": movie[4] or "#"
            })

        return {"movies": movies_list}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# --- CEK STATUS USER ---
@app.get("/api/v1/user_status/{telegram_id}")
@limiter.limit("30/minute")
async def get_user_status(request: Request, telegram_id: int):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")

    try:
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()
        cur.close()

        if user:
            return {
                "telegram_id": telegram_id, 
                "is_vip": user[0],
                "status": "user_found"
            }
        else:
            return {
                "telegram_id": telegram_id, 
                "is_vip": False, 
                "status": "user_not_found"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# --- CREATE PAYMENT LINK ---
@app.post("/api/v1/create_payment")
@limiter.limit("20/minute")
async def create_payment_link(request: Request, payment_data: PaymentRequest):
    # Validasi additional
    if payment_data.gross_amount < 1000:
        raise HTTPException(status_code=400, detail="Amount too small")

    # Buat ID order unik
    order_id = f"DRAMAMU-{payment_data.telegram_id}-{int(time.time())}"

    # Siapin detail transaksi
    transaction_details = {
        "order_id": order_id,
        "gross_amount": payment_data.gross_amount,
    }

    # Info user
    customer_details = {
        "first_name": "User",
        "last_name": str(payment_data.telegram_id),
        "email": f"{payment_data.telegram_id}@dramamu.com",
        "phone": "08123456789", 
    }

    # Info barang
    item_details = [
        {
            "id": f"VIP-{payment_data.paket_id}",
            "price": payment_data.gross_amount,
            "quantity": 1,
            "name": payment_data.nama_paket,
        }
    ]

    # Gabungin semua
    transaction_data = {
        "transaction_details": transaction_details,
        "customer_details": customer_details,
        "item_details": item_details,
    }

    try:
        snap_response = midtrans_client.create_transaction(transaction_data)
        snap_token = snap_response['token']

        # Log payment attempt
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO payments (telegram_id, order_id, amount, package_name, status, created_at) VALUES (%s, %s, %s, %s, 'pending', NOW());",
                    (payment_data.telegram_id, order_id, payment_data.gross_amount, payment_data.nama_paket)
                )
                conn.commit()
                cur.close()
            except Exception as e:
                print(f"Error logging payment: {e}")
            finally:
                try:
                    conn.close()
                except:
                    pass

        return {"snap_token": snap_token}

    except Exception as e:
        print(f"Error pas bikin token Snap: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- SISTEM PELANTARA: TAHAN DATA FILM SAMPAI BOT TERIMA /START ---
@app.post("/api/v1/hold_movie_data")
@limiter.limit("30/minute")
async def hold_movie_data(request: Request, data: dict):
    """
    ENDPOINT PELANTARA - SISTEM SENDDATA AUTO-TRIGGER:
    1. Terima data film dari mini app
    2. Validasi VIP status
    3. Tahan data di intermediary_queue
    4. Return transaction_id ke mini app
    5. Mini app kirim via sendData() → auto-trigger bot
    6. Bot terima via web_app_data → fetch data → kirim film
    """
    try:
        telegram_id = data.get("chat_id")
        movie_id = data.get("movie_id")
        init_data = data.get("init_data")

        # Validasi input
        if not telegram_id or not movie_id or not init_data:
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Validasi tipe data
        try:
            telegram_id = int(telegram_id)
            movie_id = int(movie_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid ID format")

        # Verifikasi init_data Telegram
        if not BOT_TOKEN or not verify_telegram_init_data(init_data, BOT_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid init_data")

        # Cek status VIP user
        if not check_vip_status(telegram_id):
            return {
                "status": "vip_required",
                "message": "User is not VIP"
            }

        # Ambil detail film
        movie = get_movie_details(movie_id)
        if not movie:
            return {
                "status": "movie_not_found",
                "message": "Movie not found"
            }

        # Generate token unik
        start_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=15)

        # Simpan data film di pelantara (intermediary_queue)
        conn = get_db_connection()
        if not conn:
            return {
                "status": "error",
                "message": "Database connection failed"
            }

        try:
            cur = conn.cursor()
            
            # Simpan data film dalam format JSONB
            import json
            movie_data_json = json.dumps(movie)
            
            start_link = f"https://t.me/{BOT_USERNAME}?start={start_token}"
            
            cur.execute(
                """INSERT INTO intermediary_queue 
                   (telegram_id, movie_id, start_token, movie_data, status, start_link, expires_at) 
                   VALUES (%s, %s, %s, %s, 'waiting_start', %s, %s);""",
                (telegram_id, movie_id, start_token, movie_data_json, start_link, expires_at)
            )
            conn.commit()
            cur.close()

            # Log aktivitas
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO activity_logs (telegram_id, action, movie_id, status, created_at) VALUES (%s, %s, %s, %s, NOW());",
                    (telegram_id, "movie_held_in_queue", movie_id, "waiting_senddata")
                )
                conn.commit()
                cur.close()
            except Exception as e:
                print(f"Error logging activity: {e}")

            print(f"✅ Data film ditahan untuk user {telegram_id}, token: {start_token}")
            print(f"⏳ Menunggu Mini App kirim via sendData()...")

            return {
                "status": "success",
                "message": "Data film ditahan, akan otomatis terkirim via sendData",
                "token": start_token
            }

        except Exception as e:
            print(f"Error holding movie data: {e}")
            try:
                conn.rollback()
            except:
                pass
            return {
                "status": "error",
                "message": "Failed to hold movie data"
            }
        finally:
            try:
                conn.close()
            except:
                pass

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in hold_movie_data: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.post("/api/v1/release_movie_data/{token}")
@limiter.limit("60/minute")
async def release_movie_data(request: Request, token: str):
    """
    ENDPOINT UNTUK BOT:
    Dipanggil setelah bot terima /start
    Mengembalikan data film yang ditahan dan update status
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")

    try:
        cur = conn.cursor()
        
        # Ambil data dari intermediary_queue
        cur.execute(
            """SELECT telegram_id, movie_id, movie_data, status 
               FROM intermediary_queue 
               WHERE start_token = %s 
               AND expires_at > NOW() 
               AND status = 'waiting_start';""",
            (token,)
        )
        result = cur.fetchone()

        if result:
            telegram_id, movie_id, movie_data_json, status = result
            
            # Update status: bot sudah terima /start
            cur.execute(
                """UPDATE intermediary_queue 
                   SET status = 'released_to_bot', 
                       bot_received_start_at = NOW() 
                   WHERE start_token = %s;""",
                (token,)
            )
            conn.commit()
            
            # Log aktivitas
            try:
                cur.execute(
                    "INSERT INTO activity_logs (telegram_id, action, movie_id, status, created_at) VALUES (%s, %s, %s, %s, NOW());",
                    (telegram_id, "movie_released_to_bot", movie_id, "released")
                )
                conn.commit()
            except Exception as e:
                print(f"Error logging activity: {e}")

            cur.close()
            
            import json
            movie_data = json.loads(movie_data_json)
            
            return {
                "valid": True,
                "telegram_id": telegram_id,
                "movie_id": movie_id,
                "movie_data": movie_data,
                "message": "Data film berhasil dilepas dari pelantara"
            }
        else:
            cur.close()
            return {
                "valid": False,
                "message": "Token tidak valid, expired, atau sudah diproses"
            }

    except Exception as e:
        print(f"Error releasing movie data: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# --- LEGACY ENDPOINT (untuk backward compatibility) ---
@app.post("/api/v1/handle_movie_request")
@limiter.limit("30/minute")
async def handle_movie_request(request: Request, data: dict):
    """
    Legacy endpoint - redirect ke hold_movie_data
    """
    return await hold_movie_data(request, data)

@app.get("/api/v1/pending/{token}")
@limiter.limit("30/minute")
async def get_pending_action(request: Request, token: str):
    """
    Ambil pending action berdasarkan token (untuk bot)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT telegram_id, movie_id FROM pending_actions WHERE start_token = %s AND expires_at > NOW() AND status = 'pending';",
            (token,)
        )
        pending = cur.fetchone()

        if pending:
            return {
                "telegram_id": pending[0],
                "movie_id": pending[1],
                "valid": True
            }
        else:
            return {
                "valid": False,
                "message": "Token expired or invalid"
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

async def simulate_bot_send(telegram_id: int, movie_data: dict) -> bool:
    """
    Kirim film ke user via Telegram Bot API
    """
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        
        message_text = f"""
🎬 <b>{movie_data.get('title', 'Film')}</b>

Selamat menonton! 🍿

🔗 Link: {movie_data.get('video_link', '#')}
"""
        
        payload = {
            "chat_id": telegram_id,
            "text": message_text,
            "parse_mode": "HTML"
        }
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200:
                print(f"✅ Film berhasil dikirim ke user {telegram_id}")
                return True
            else:
                print(f"❌ Gagal kirim film ke user {telegram_id}: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ Error saat kirim film: {e}")
        return False

# --- ENDPOINT UNTUK MENGURUS PENARIKAN REFERRAL ---
@app.post("/api/v1/withdraw_referral")
@limiter.limit("10/minute")
async def withdraw_referral(request: Request, data: dict):
    """
    Handle penarikan dana referral
    """
    try:
        telegram_id = data.get("telegram_id")
        jumlah = data.get("jumlah")
        metode = data.get("metode")
        nomor_rekening = data.get("nomor_rekening")
        nama_pemilik = data.get("nama_pemilik")

        # Validasi
        if not all([telegram_id, jumlah, metode, nomor_rekening, nama_pemilik]):
            raise HTTPException(status_code=400, detail="Missing required fields")

        # Validasi tipe data
        try:
            telegram_id = int(telegram_id) if telegram_id is not None else 0
            jumlah = float(jumlah) if jumlah is not None else 0.0
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid data format")

        if jumlah <= 0:
            raise HTTPException(status_code=400, detail="Invalid amount")

        # Simpan ke database
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO withdrawal_requests (telegram_id, amount, method, account_number, account_name, status, created_at) VALUES (%s, %s, %s, %s, %s, 'pending', NOW());",
                    (telegram_id, jumlah, metode, nomor_rekening, nama_pemilik)
                )
                conn.commit()
                cur.close()

                return {
                    "status": "success",
                    "message": "Withdrawal request submitted"
                }
            except Exception as e:
                try:
                    conn.rollback()
                except:
                    pass
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                try:
                    conn.close()
                except:
                    pass
        else:
            raise HTTPException(status_code=500, detail="Database connection failed")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
