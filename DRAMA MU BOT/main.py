import psycopg2
import sys
import time
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import midtransclient

# --- DATA KONEKSI DATABASE (BACA DARI RENDER) ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")
# ---
# ---

# --- INFO MIDTRANS 
MIDTRANS_SERVER_KEY = os.environ.get("MIDTRANS_SERVER_KEY")
MIDTRANS_CLIENT_KEY = os.environ.get("MIDTRANS_CLIENT_KEY")
# ---
# ---

# Inisialisasi Midtrans
midtrans_client = midtransclient.Snap(
    is_production=False,  # <-- KARENA MASIH TES, PAKE 'False'
    server_key=MIDTRANS_SERVER_KEY,
    client_key=MIDTRANS_CLIENT_KEY
)
# ---

# Buat string koneksi
conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# Buat aplikasi FastAPI
app = FastAPI()

# --- TAMBAHIN INI BUAT NGASIH IZIN (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Ngasih izin ke semua 'asal' (termasuk file://)
    allow_credentials=True,
    allow_methods=["*"],  # Ngasih izin semua metode (GET, POST, dll)
    allow_headers=["*"],  # Ngasih izin semua header
)

# Fungsi untuk konek ke DB (Biar rapi)
def get_db_connection():
    try:
        conn = psycopg2.connect(conn_string)
        return conn
    except Exception as e:
        print(f"GAGAL KONEK KE DB: {e}")
        return None
# --- (Taruh di main.py) ---

# API BARU UNTUK NGASIH DATA STATS REFERRAL
@app.get("/api/v1/referral_stats/{telegram_id}")
async def get_referral_stats(telegram_id: int):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        cur = conn.cursor()
        # AMBIL DATA DARI KOLOM BARU YANG LU BIKIN
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
            # Kalo user gak ada, return data kosong
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
    paket_id: int       # (BARU)
    gross_amount: int   # (BARU)
    nama_paket: str     # (BARU)

@app.post("/api/v1/create_payment")
async def create_payment_link(request: PaymentRequest):
    # Buat ID order unik
    order_id = f"DRAMAMU-{request.telegram_id}-{int(time.time())}"
    
    # Siapin detail transaksi (ambil dari request)
    transaction_details = {
        "order_id": order_id,
        "gross_amount": request.gross_amount,  # <-- PAKE HARGA DARI FRONTEND
    }
    
    # Info user
    customer_details = {
        "first_name": "User",
        "last_name": str(request.telegram_id),
        "email": f"{request.telegram_id}@dramamu.com",
        "phone": "08123456789", 
    }

    # Info barang (ambil dari request)
    item_details = [
        {
            "id": f"VIP-{request.paket_id}",
            "price": request.gross_amount,
            "quantity": 1,
            "name": request.nama_paket, # <-- PAKE NAMA PAKET DARI FRONTEND
        }
    ]

    # Gabungin semua
    transaction_data = {
        "transaction_details": transaction_details,
        "customer_details": customer_details,
        "item_details": item_details,
    }

    try:
        # Minta token ke Midtrans pake SERVER KEY
        snap_response = midtrans_client.create_transaction(transaction_data)
        snap_token = snap_response['token']
        return {"snap_token": snap_token}
    
    except Exception as e:
        print(f"Error pas bikin token Snap: {e}")
        raise HTTPException(status_code=500, detail=str(e))