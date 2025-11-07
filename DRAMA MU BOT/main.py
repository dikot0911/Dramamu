import psycopg2
from psycopg2 import pool
import sys
import time
import os
import requests
import logging
import hashlib
import hmac
import json
from urllib.parse import unquote, parse_qsl
from fastapi import FastAPI, HTTPException, Request
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
MIDTRANS_SERVER_KEY = os.environ.get("MIDTRANS_SERVER_KEY", "")
MIDTRANS_CLIENT_KEY = os.environ.get("MIDTRANS_CLIENT_KEY", "")

# Inisialisasi Midtrans (hanya jika credentials tersedia)
midtrans_client = None
if MIDTRANS_SERVER_KEY and MIDTRANS_CLIENT_KEY:
    midtrans_client = midtransclient.Snap(
        is_production=False,
        server_key=MIDTRANS_SERVER_KEY,
        client_key=MIDTRANS_CLIENT_KEY
    )

# ==========================================================
# 📊 DATABASE CONNECTION POOL (OPTIMIZED)
# ==========================================================
connection_pool = None
if DB_HOST and DB_PORT and DB_NAME and DB_USER and DB_PASS:
    try:
        connection_pool = pool.ThreadedConnectionPool(
            5, 20,
            dbname=DB_NAME,
            user=DB_USER,
            host=DB_HOST,
            port=DB_PORT,
            password=DB_PASS,
            connect_timeout=10,
            options="-c statement_timeout=30000"
        )
        logger.info("✅ Database connection pool initialized (min=5, max=20) with ThreadedConnectionPool")
    except Exception as e:
        logger.error(f"❌ Failed to initialize connection pool: {e}")

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
    if not connection_pool:
        logger.error("Database connection pool tidak tersedia!")
        return None
    try:
        conn = connection_pool.getconn()
        return conn
    except Exception as e:
        logger.error(f"Gagal ambil connection dari pool: {e}")
        return None

def return_db_connection(conn):
    if conn and connection_pool:
        try:
            connection_pool.putconn(conn)
        except Exception as e:
            logger.error(f"Gagal return connection ke pool: {e}")

def check_vip_status(telegram_id: int) -> bool:
    """
    Check VIP status with race condition protection using INSERT...ON CONFLICT...RETURNING.
    This ensures atomicity and prevents race conditions when multiple requests come simultaneously.
    """
    conn = get_db_connection()
    if not conn:
        return False
    
    is_vip = False
    try:
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO users (telegram_id, is_vip) 
            VALUES (%s, %s) 
            ON CONFLICT (telegram_id) 
            DO UPDATE SET telegram_id = EXCLUDED.telegram_id
            RETURNING is_vip
            """,
            (telegram_id, False)
        )
        result = cur.fetchone()
        conn.commit()
        
        if result and result[0] is True:
            is_vip = True
        
        cur.close()
    except Exception as e:
        logger.error(f"Error cek VIP: {e}")
        conn.rollback()
    finally:
        return_db_connection(conn)
    
    return is_vip

def get_movie_details(movie_id: int) -> dict | None:
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
        return_db_connection(conn)
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
# IDEMPOTENCY & PAYMENT TRACKING FUNCTIONS
# ==========================================================

def check_and_mark_webhook_processed(event_id: str, telegram_id: int, action: str, data: dict | None = None) -> bool:
    """
    Check if webhook event already processed. If not, mark it as processed.
    Returns True if event is NEW (should be processed), False if already processed.
    """
    conn = get_db_connection()
    if not conn:
        logger.warning("⚠️ DB connection failed for idempotency check - allowing request")
        return True
    
    try:
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO webhook_events (event_id, telegram_id, action, data)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING id
            """,
            (event_id, telegram_id, action, json.dumps(data or {}))
        )
        
        result = cur.fetchone()
        conn.commit()
        cur.close()
        
        if result:
            logger.info(f"✅ New event {event_id} - akan diproses")
            return True
        else:
            logger.info(f"⚠️ Duplicate event {event_id} - sudah diproses sebelumnya")
            return False
            
    except Exception as e:
        logger.error(f"Error checking idempotency: {e}")
        conn.rollback()
        return True
    finally:
        return_db_connection(conn)

def save_payment_transaction(order_id: str, telegram_id: int, notification_data: dict) -> bool:
    """
    Save or update payment transaction from Midtrans notification.
    """
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute(
            """
            INSERT INTO payment_transactions 
            (order_id, telegram_id, gross_amount, payment_type, transaction_status, 
             transaction_id, fraud_status, midtrans_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (order_id) 
            DO UPDATE SET
                transaction_status = EXCLUDED.transaction_status,
                fraud_status = EXCLUDED.fraud_status,
                payment_type = EXCLUDED.payment_type,
                transaction_id = EXCLUDED.transaction_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                order_id,
                telegram_id,
                int(float(notification_data.get('gross_amount', 0))),
                notification_data.get('payment_type'),
                notification_data.get('transaction_status'),
                notification_data.get('transaction_id'),
                notification_data.get('fraud_status'),
                json.dumps(notification_data)
            )
        )
        
        conn.commit()
        cur.close()
        logger.info(f"✅ Payment transaction {order_id} saved/updated")
        return True
        
    except Exception as e:
        logger.error(f"Error saving payment transaction: {e}")
        conn.rollback()
        return False
    finally:
        return_db_connection(conn)

def activate_vip_status(telegram_id: int, order_id: str | None = None) -> bool:
    """
    Activate VIP status for user and log to VIP history.
    """
    conn = get_db_connection()
    if not conn:
        return False
    
    try:
        cur = conn.cursor()
        
        cur.execute(
            """
            UPDATE users 
            SET is_vip = TRUE 
            WHERE telegram_id = %s
            """,
            (telegram_id,)
        )
        
        if order_id:
            cur.execute(
                """
                INSERT INTO vip_history (telegram_id, order_id)
                VALUES (%s, %s)
                """,
                (telegram_id, order_id)
            )
        
        conn.commit()
        cur.close()
        logger.info(f"✅ VIP activated for user {telegram_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error activating VIP: {e}")
        conn.rollback()
        return False
    finally:
        return_db_connection(conn)

def verify_midtrans_signature(notification_data: dict) -> bool:
    """
    Verify Midtrans notification signature to prevent fraud.
    """
    if not MIDTRANS_SERVER_KEY:
        logger.error("MIDTRANS_SERVER_KEY tidak tersedia!")
        return False
    
    try:
        order_id = notification_data.get('order_id')
        status_code = notification_data.get('status_code')
        gross_amount = notification_data.get('gross_amount')
        signature_key = notification_data.get('signature_key')
        
        if not all([order_id, status_code, gross_amount, signature_key]):
            logger.error("Missing required fields in notification")
            return False
        
        string_to_hash = f"{order_id}{status_code}{gross_amount}{MIDTRANS_SERVER_KEY}"
        calculated_signature = hashlib.sha512(string_to_hash.encode()).hexdigest()
        
        if calculated_signature == signature_key:
            logger.info(f"✅ Midtrans signature valid for {order_id}")
            return True
        else:
            logger.error(f"❌ Midtrans signature INVALID for {order_id}")
            return False
            
    except Exception as e:
        logger.error(f"Error verifying Midtrans signature: {e}")
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
    finally:
        return_db_connection(conn)

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
        
        if user:
            return {"telegram_id": telegram_id, "is_vip": user[0]}
        else:
            return {"telegram_id": telegram_id, "is_vip": False, "status": "user_not_found"}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        return_db_connection(conn)

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
    finally:
        return_db_connection(conn)

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
    if not midtrans_client:
        raise HTTPException(status_code=500, detail="Midtrans belum dikonfigurasi. Set MIDTRANS_SERVER_KEY dan MIDTRANS_CLIENT_KEY.")
    
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
# 💳 MIDTRANS PAYMENT NOTIFICATION CALLBACK
# ==========================================================

@app.post("/api/v1/midtrans/notification")
async def midtrans_payment_notification(request: Request):
    """
    Endpoint untuk menerima notifikasi pembayaran dari Midtrans.
    Otomatis mengaktifkan VIP status setelah pembayaran berhasil.
    """
    try:
        notification_data = await request.json()
        logger.info(f"📬 Midtrans notification received: {notification_data.get('order_id')}")
        
        if not verify_midtrans_signature(notification_data):
            logger.error("❌ Invalid Midtrans signature - request rejected")
            raise HTTPException(status_code=403, detail="Invalid signature")
        
        order_id = notification_data.get('order_id', '')
        transaction_status = notification_data.get('transaction_status')
        fraud_status = notification_data.get('fraud_status')
        
        parts = order_id.split('-')
        if len(parts) < 2 or not parts[1].isdigit():
            logger.error(f"❌ Invalid order_id format: {order_id}")
            raise HTTPException(status_code=400, detail="Invalid order_id format")
        
        telegram_id = int(parts[1])
        
        save_payment_transaction(order_id, telegram_id, notification_data)
        
        if transaction_status == 'capture':
            if fraud_status == 'accept':
                activate_vip_status(telegram_id, order_id)
                send_telegram_message(
                    telegram_id,
                    "🎉 <b>Pembayaran Berhasil!</b>\n\n"
                    "✅ Status VIP kamu sudah aktif!\n"
                    "Sekarang kamu bisa nonton semua drama favorit tanpa batas.\n\n"
                    "Selamat menikmati! 🍿"
                )
                logger.info(f"✅ Payment SUCCESS - VIP activated for {telegram_id}")
            else:
                logger.warning(f"⚠️ Payment captured but fraud_status={fraud_status}")
        
        elif transaction_status == 'settlement':
            activate_vip_status(telegram_id, order_id)
            send_telegram_message(
                telegram_id,
                "🎉 <b>Pembayaran Berhasil!</b>\n\n"
                "✅ Status VIP kamu sudah aktif!\n"
                "Sekarang kamu bisa nonton semua drama favorit tanpa batas.\n\n"
                "Selamat menikmati! 🍿"
            )
            logger.info(f"✅ Payment SETTLED - VIP activated for {telegram_id}")
        
        elif transaction_status == 'pending':
            send_telegram_message(
                telegram_id,
                "⏳ Pembayaran kamu sedang diproses.\n"
                "Kami akan konfirmasi setelah pembayaran selesai."
            )
            logger.info(f"⏳ Payment PENDING for {telegram_id}")
        
        elif transaction_status in ['deny', 'expire', 'cancel']:
            send_telegram_message(
                telegram_id,
                f"❌ Pembayaran {transaction_status}.\n"
                "Silakan coba lagi atau hubungi admin jika ada masalah."
            )
            logger.info(f"❌ Payment {transaction_status.upper()} for {telegram_id}")
        
        return {"status": "ok"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error processing Midtrans notification: {e}", exc_info=True)
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
    
    # ==========================================================
    # 🔒 STEP 3: IDEMPOTENCY CHECK (PREVENT DUPLICATE PROCESSING)
    # ==========================================================
    event_id = hashlib.sha256(f"{telegram_id}-{action}-{request.init_data}".encode()).hexdigest()
    
    if not check_and_mark_webhook_processed(event_id, telegram_id, action, {
        "movie_id": request.movie_id,
        "judul": request.judul,
        "apk": request.apk
    }):
        logger.info(f"⚠️ Duplicate request ignored - already processed: {event_id}")
        return {"status": "success", "message": "Request already processed"}
    
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
