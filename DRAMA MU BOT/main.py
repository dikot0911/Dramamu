import psycopg2
import sys
import time
import os
import secrets
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import midtransclient
import hashlib
import hmac
from urllib.parse import parse_qs

# --- DATA KONEKSI DATABASE (BACA DARI RENDER) ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")

# --- INFO MIDTRANS 
MIDTRANS_SERVER_KEY = os.environ.get("MIDTRANS_SERVER_KEY")
MIDTRANS_CLIENT_KEY = os.environ.get("MIDTRANS_CLIENT_KEY")

# --- BOT CONFIG ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "dramamu_bot")

# Inisialisasi Midtrans
midtrans_client = midtransclient.Snap(
    is_production=False,
    server_key=MIDTRANS_SERVER_KEY,
    client_key=MIDTRANS_CLIENT_KEY
)

# Buat string koneksi
conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# Buat aplikasi FastAPI
app = FastAPI()

# --- TAMBAHIN INI BUAT NGASIH IZIN (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fungsi untuk konek ke DB (Biar rapi)
def get_db_connection():
    try:
        conn = psycopg2.connect(conn_string)
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
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()

        if user and user[0] is True:
            is_vip = True
        cur.close()
    except Exception as e:
        print(f"Error cek VIP: {e}")
    finally:
        if conn:
            conn.close()

    return is_vip

# --- AMBIL DETAIL FILM ---
def get_movie_details(movie_id: int) -> dict:
    conn = get_db_connection()
    if not conn:
        return None

    movie = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link, poster_url FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        if row:
            movie = {"title": row[0], "video_link": row[1], "poster_url": row[2]}
        cur.close()
    except Exception as e:
        print(f"Error ambil movie: {e}")
    finally:
        if conn:
            conn.close()
    return movie

# --- API BARU UNTUK NGASIH DATA STATS REFERRAL ---
@app.get("/api/v1/referral_stats/{telegram_id}")
async def get_referral_stats(telegram_id: int):
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
        conn.close()
        
        if user_stats:
            return {
                "referral_code": user_stats[0],
                "commission_balance": user_stats[1],
                "total_referrals": user_stats[2]
            }
        else:
            return {"referral_code": "KODE_UNIK", "commission_balance": 0, "total_referrals": 0}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- CONTOH ENDPOINT 1: Halaman Depan ---
@app.get("/")
async def root():
    return {"message": "Halo, ini API Dramamu!"}

# --- CONTOH ENDPOINT 2: Ambil Daftar Film ---
@app.get("/api/v1/movies")
async def get_all_movies():
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, title, description, poster_url, video_link FROM movies;")
        movies_raw = cur.fetchall()
        cur.close()
        conn.close()
        
        movies_list = []
        for movie in movies_raw:
            movies_list.append({
                "id": movie[0],
                "title": movie[1],
                "description": movie[2],
                "poster_url": movie[3],
                "video_link": movie[4]
            })
            
        return {"movies": movies_list}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- CONTOH ENDPOINT 3: Cek Status User ---
@app.get("/api/v1/user_status/{telegram_id}")
async def get_user_status(telegram_id: int):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        
        if user:
            return {"telegram_id": telegram_id, "is_vip": user[0]}
        else:
            return {"telegram_id": telegram_id, "is_vip": False, "status": "user_not_found"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------------------------------------
# ⬇️⬇️ INI API BARU UNTUK PEMBAYARAN ⬇️⬇️
# ----------------------------------------------

# Model untuk nerima data dari frontend
class PaymentRequest(BaseModel):
    telegram_id: int
    paket_id: int
    gross_amount: int
    nama_paket: str

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
    # Buat ID order unik
    order_id = f"DRAMAMU-{request.telegram_id}-{int(time.time())}"
    
    # Siapin detail transaksi
    transaction_details = {
        "order_id": order_id,
        "gross_amount": request.gross_amount,
    }
    
    # Info user
    customer_details = {
        "first_name": "User",
        "last_name": str(request.telegram_id),
        "email": f"{request.telegram_id}@dramamu.com",
        "phone": "08123456789", 
    }

    # Info barang
    item_details = [
        {
            "id": f"VIP-{request.paket_id}",
            "price": request.gross_amount,
            "quantity": 1,
            "name": request.nama_paket,
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
        return {"snap_token": snap_token}
    
    except Exception as e:
        print(f"Error pas bikin token Snap: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- API BARU: Handle Movie Request dari Mini App ---
@app.post("/api/v1/handle_movie_request")
async def handle_movie_request(request: dict):
    """
    Endpoint untuk menangani request film dari Mini App dengan fallback mechanism
    """
    try:
        telegram_id = request.get("chat_id")
        movie_id = request.get("movie_id")
        init_data = request.get("init_data")
        
        # Validasi input
        if not telegram_id or not movie_id or not init_data:
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Verifikasi init_data Telegram
        if not verify_telegram_init_data(init_data, BOT_TOKEN):
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
        
        # Simulasikan pengiriman via bot
        send_success = await simulate_bot_send(telegram_id, movie)
        
        if send_success:
            # Log aktivitas sukses
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO activity_logs (telegram_id, action, movie_id, status) VALUES (%s, %s, %s, %s);",
                        (telegram_id, "watch", movie_id, "success")
                    )
                    conn.commit()
                    cur.close()
                except Exception as e:
                    print(f"Error logging activity: {e}")
                finally:
                    conn.close()
            
            return {
                "status": "success",
                "message": "Movie sent successfully"
            }
        else:
            # Fallback: buat pending action
            start_token = secrets.token_urlsafe(16)
            expires_at = datetime.now() + timedelta(minutes=15)
            
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO pending_actions (telegram_id, movie_id, start_token, expires_at) VALUES (%s, %s, %s, %s);",
                        (telegram_id, movie_id, start_token, expires_at)
                    )
                    conn.commit()
                    cur.close()
                    
                    fallback_link = f"https://t.me/{BOT_USERNAME}?start={start_token}"
                    
                    return {
                        "status": "need_start",
                        "link": fallback_link,
                        "message": "Requires bot start"
                    }
                except Exception as e:
                    print(f"Error creating pending action: {e}")
                    conn.rollback()
                    return {
                        "status": "error",
                        "message": "Failed to create fallback"
                    }
                finally:
                    conn.close()
            else:
                return {
                    "status": "error", 
                    "message": "Database connection failed"
                }
                
    except Exception as e:
        print(f"Error in handle_movie_request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/pending/{token}")
async def get_pending_action(token: str):
    """
    Ambil pending action berdasarkan token (untuk bot)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database error")
    
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT telegram_id, movie_id FROM pending_actions WHERE start_token = %s AND expires_at > NOW();",
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
        if conn:
            conn.close()

async def simulate_bot_send(telegram_id: int, movie_data: dict) -> bool:
    """
    Simulasi pengiriman pesan via Bot API
    Dalam implementasi real, panggil https://api.telegram.org/bot<token>/sendMessage
    """
    try:
        # Di sini Anda akan memanggil Telegram Bot API secara langsung
        # Untuk sekarang kita return True asumsi berhasil
        # Jika gagal (misal bot belum di-start), return False
        return True
    except Exception:
        return False

# --- ENDPOINT UNTUK MENGURUS PENARIKAN REFERRAL ---
@app.post("/api/v1/withdraw_referral")
async def withdraw_referral(request: dict):
    """
    Handle penarikan dana referral
    """
    try:
        telegram_id = request.get("telegram_id")
        jumlah = request.get("jumlah")
        metode = request.get("metode")
        nomor_rekening = request.get("nomor_rekening")
        nama_pemilik = request.get("nama_pemilik")
        
        # Validasi
        if not all([telegram_id, jumlah, metode, nomor_rekening, nama_pemilik]):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Simpan ke database
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO withdrawal_requests (telegram_id, amount, method, account_number, account_name, status) VALUES (%s, %s, %s, %s, %s, %s);",
                    (telegram_id, jumlah, metode, nomor_rekening, nama_pemilik, "pending")
                )
                conn.commit()
                cur.close()
                
                return {
                    "status": "success",
                    "message": "Withdrawal request submitted"
                }
            except Exception as e:
                conn.rollback()
                raise HTTPException(status_code=500, detail=str(e))
            finally:
                conn.close()
        else:
            raise HTTPException(status_code=500, detail="Database connection failed")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
