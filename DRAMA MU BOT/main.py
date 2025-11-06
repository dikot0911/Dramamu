import psycopg2
import sys
import time
import os
import requests
import logging
import hashlib
import hmac
import json
from urllib.parse import unquote, parse_qsl
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import midtransclient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dramamu-api")

# --- DATA KONEKSI DATABASE ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")

# --- BOT TOKEN (UNTUK KIRIM MESSAGE VIA API) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else None

# --- MINI APP BASE URL ---
BASE_URL = "https://dramamuid.netlify.app"
URL_BELI_VIP = f"{BASE_URL}/payment.html"

# --- INFO MIDTRANS ---
MIDTRANS_SERVER_KEY = os.environ.get("MIDTRANS_SERVER_KEY")
MIDTRANS_CLIENT_KEY = os.environ.get("MIDTRANS_CLIENT_KEY")

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

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# SECURITY: TELEGRAM SIGNATURE VERIFICATION
# ==========================================================

def verify_telegram_signature(init_data: str, max_age_seconds: int = 300) -> dict | None:
    """
    Verifikasi signature Telegram initData dan return parsed data jika valid.
    Juga memverifikasi auth_date untuk mencegah replay attack.
    
    Args:
        init_data: String initData dari Telegram WebApp
        max_age_seconds: Maksimal umur initData yang diterima (default 5 menit)
    
    Return:
        dict: Parsed data jika valid
        None: Jika signature tidak valid atau data terlalu lama
    """
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN tidak tersedia untuk verifikasi!")
        return None
    
    try:
        parsed_data = dict(parse_qsl(init_data))
        
        if 'hash' not in parsed_data:
            logger.error("Hash tidak ditemukan di initData")
            return None
        
        if 'auth_date' not in parsed_data:
            logger.error("❌ auth_date tidak ditemukan - request ditolak")
            return None
        
        received_hash = parsed_data.pop('hash')
        
        data_check_arr = sorted([f"{k}={v}" for k, v in parsed_data.items()])
        data_check_string = '\n'.join(data_check_arr)
        
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=BOT_TOKEN.encode(),
            digestmod=hashlib.sha256
        ).digest()
        
        calculated_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        if calculated_hash != received_hash:
            logger.error("❌ Signature tidak valid! Kemungkinan data palsu.")
            return None
        
        try:
            auth_date = int(parsed_data['auth_date'])
            current_time = int(time.time())
            age_seconds = current_time - auth_date
            
            if age_seconds < 0:
                logger.error(f"❌ auth_date di masa depan! Kemungkinan clock skew atau data palsu.")
                return None
            
            if age_seconds > max_age_seconds:
                logger.error(f"❌ initData terlalu lama ({age_seconds}s) - kemungkinan replay attack! Max: {max_age_seconds}s")
                return None
            
            logger.info(f"✅ Signature valid - data terverifikasi dari Telegram (umur: {age_seconds}s)")
            
        except (ValueError, KeyError) as e:
            logger.error(f"❌ auth_date tidak valid: {e}")
            return None
        
        if 'user' not in parsed_data:
            logger.error("❌ Field 'user' tidak ditemukan di initData")
            return None
        
        return parsed_data
        
    except Exception as e:
        logger.error(f"Error saat verifikasi signature: {e}")
        return None

def extract_user_id_from_verified_data(verified_data: dict) -> int | None:
    """
    Extract user_id dari verified initData.
    Return None jika gagal.
    """
    try:
        if 'user' in verified_data:
            user_json = json.loads(verified_data['user'])
            return user_json.get('id')
        return None
    except Exception as e:
        logger.error(f"Error extract user_id: {e}")
        return None

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def get_db_connection():
    try:
        conn = psycopg2.connect(conn_string)
        return conn
    except Exception as e:
        logger.error(f"GAGAL KONEK KE DB: {e}")
        return None

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
        logger.error(f"Error cek VIP: {e}")
    finally:
        if conn:
            conn.close()
    
    return is_vip

def get_movie_details(movie_id: int) -> dict:
    conn = get_db_connection()
    if not conn:
        return None
    
    movie = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        if row:
            movie = {"title": row[0], "video_link": row[1]}
        cur.close()
    except Exception as e:
        logger.error(f"Error ambil movie: {e}")
    finally:
        if conn:
            conn.close()
    return movie

def send_telegram_message(chat_id: int, text: str, reply_markup=None):
    if not TELEGRAM_API_URL:
        logger.error("BOT_TOKEN tidak tersedia!")
        return False
    
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        
        response = requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)
        response.raise_for_status()
        logger.info(f"✅ Pesan terkirim ke {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error kirim message: {e}")
        return False

def send_telegram_video(chat_id: int, video_url: str, caption: str = ""):
    if not TELEGRAM_API_URL:
        logger.error("BOT_TOKEN tidak tersedia!")
        return False
    
    try:
        payload = {
            "chat_id": chat_id,
            "video": video_url,
            "caption": caption,
            "parse_mode": "HTML"
        }
        
        response = requests.post(f"{TELEGRAM_API_URL}/sendVideo", json=payload)
        response.raise_for_status()
        logger.info(f"✅ Video terkirim ke {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error kirim video: {e}")
        send_telegram_message(chat_id, f"🎬 {caption}\n{video_url}")
        return False

# ==========================================================
# PYDANTIC MODELS
# ==========================================================

class WebhookRequest(BaseModel):
    init_data: str
    action: str
    movie_id: int | None = None
    judul: str | None = None
    apk: str | None = None
    jumlah: str | None = None
    metode: str | None = None
    nomor_rekening: str | None = None
    nama_pemilik: str | None = None

class PaymentRequest(BaseModel):
    telegram_id: int
    paket_id: int
    gross_amount: int
    nama_paket: str

# ==========================================================
# ENDPOINTS
# ==========================================================

@app.get("/")
async def root():
    return {"message": "Halo, ini API Dramamu!"}

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

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
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
        return {"snap_token": snap_token}
    
    except Exception as e:
        logger.error(f"Error pas bikin token Snap: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================================
# 🚀 WEBHOOK ENDPOINT - INI YANG BARU!
# ==========================================================

@app.post("/api/v1/webhook")
async def handle_webapp_webhook(request: WebhookRequest):
    action = request.action
    
    # ==========================================================
    # 🔒 STEP 1: VERIFY TELEGRAM SIGNATURE
    # ==========================================================
    verified_data = verify_telegram_signature(request.init_data)
    if not verified_data:
        logger.error("❌ Signature verification GAGAL - request ditolak")
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid signature")
    
    # ==========================================================
    # 🔒 STEP 2: EXTRACT USER ID DARI VERIFIED DATA
    # ==========================================================
    telegram_id = extract_user_id_from_verified_data(verified_data)
    if not telegram_id:
        logger.error("❌ User ID tidak ditemukan di verified data")
        raise HTTPException(status_code=400, detail="Bad Request: User ID not found")
    
    logger.info(f"📨 Webhook diterima dari VERIFIED user {telegram_id}: {action}")
    
    # =============================
    # 1️⃣ AKSI NONTON DRAMA
    # =============================
    if action == "watch":
        movie_id = request.movie_id
        if not movie_id:
            error_msg = "Film gak valid."
            send_telegram_message(telegram_id, error_msg)
            return {"status": "error", "message": error_msg}
        
        try:
            if check_vip_status(telegram_id):
                movie = get_movie_details(movie_id)
                if not movie:
                    error_msg = "Film gak ditemukan di database."
                    send_telegram_message(telegram_id, error_msg)
                    return {"status": "error", "message": error_msg}
                
                success = send_telegram_video(
                    telegram_id,
                    movie["video_link"],
                    f"🎥 <b>{movie['title']}</b>"
                )
                
                if success:
                    logger.info(f"✅ Video {movie['title']} berhasil dikirim ke {telegram_id}")
                    return {"status": "success", "message": "Video terkirim"}
                else:
                    logger.error(f"❌ Gagal kirim video ke {telegram_id}")
                    return {"status": "error", "message": "Gagal kirim video, tapi link sudah dikirim"}
            else:
                reply_markup = {
                    "inline_keyboard": [[
                        {"text": "💎 Beli VIP Sekarang", "web_app": {"url": URL_BELI_VIP}}
                    ]]
                }
                send_telegram_message(
                    telegram_id,
                    "🚫 Anda belum VIP.\nGabung VIP biar bisa nonton full, bre!",
                    reply_markup
                )
                logger.info(f"ℹ️ User {telegram_id} belum VIP - diarahkan ke halaman pembayaran")
                return {"status": "not_vip", "message": "User belum VIP"}
        except Exception as e:
            logger.error(f"❌ Error saat proses watch: {e}", exc_info=True)
            send_telegram_message(telegram_id, "⚠️ Terjadi kesalahan. Silakan coba lagi nanti.")
            return {"status": "error", "message": f"Server error: {str(e)}"}
    
    # =============================
    # 2️⃣ AKSI REQUEST DRAMA
    # =============================
    elif action == "request_drama":
        try:
            judul = request.judul or "-"
            apk = request.apk or "-"
            logger.info(f"📝 REQUEST: {telegram_id} — {judul} dari {apk}")
            
            success = send_telegram_message(
                telegram_id,
                f"✅ Request '{judul}' (dari {apk}) udah kami terima!"
            )
            
            if success:
                return {"status": "success", "message": "Request diterima"}
            else:
                return {"status": "error", "message": "Gagal mengirim konfirmasi"}
        except Exception as e:
            logger.error(f"❌ Error saat proses request_drama: {e}", exc_info=True)
            return {"status": "error", "message": f"Server error: {str(e)}"}
    
    # =============================
    # 3️⃣ AKSI WITHDRAW REFERRAL
    # =============================
    elif action == "withdraw_referral":
        try:
            jumlah = request.jumlah
            metode = request.metode
            nomor = request.nomor_rekening
            nama = request.nama_pemilik
            
            logger.info(f"💸 PENARIKAN: {telegram_id} — Rp{jumlah} via {metode} ({nama} - {nomor})")
            
            success = send_telegram_message(
                telegram_id,
                f"✅ Request penarikan Rp {jumlah} udah diterima.\nDiproses admin dalam 1x24 jam."
            )
            
            if success:
                return {"status": "success", "message": "Withdraw diterima"}
            else:
                return {"status": "error", "message": "Gagal mengirim konfirmasi"}
        except Exception as e:
            logger.error(f"❌ Error saat proses withdraw: {e}", exc_info=True)
            return {"status": "error", "message": f"Server error: {str(e)}"}
    
    # =============================
    # ❓ AKSI TIDAK DIKENALI
    # =============================
    else:
        logger.warning(f"Aksi tidak dikenal: {action}")
        send_telegram_message(telegram_id, "⚠️ Aksi tidak dikenali dari WebApp.")
        return {"status": "error", "message": "Aksi tidak dikenali"}
