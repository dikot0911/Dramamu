import logging
import psycopg2
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- BOT TOKEN (BACA DARI RENDER) ---
# INI YANG BENER: Baca NAMA variabelnya
BOT_TOKEN = os.environ.get("BOT_TOKEN") 

# --- URL NETLIFY (UDAH ONLINE) ---
BASE_URL = "https://dramamuid.netlify.app" 

URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"
# ---

# --- KONEKSI DATABASE (BACA DARI RENDER) ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")
# ---

# String koneksi database
conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# Mengatur logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- FUNGSI HELPER: KONEK KE DB ---
def get_db_connection():
    try:
        conn = psycopg2.connect(conn_string)
        return conn
    except Exception as e:
        logger.error(f"GAGAL KONEK KE DB: {e}")
        return None

# --- FUNGSI HELPER: CEK STATUS VIP (PENTING) ---
def check_vip_status(telegram_id: int) -> bool:
    """Cek ke DB apakah user VIP atau bukan."""
    conn = get_db_connection()
    if conn is None:
        return False
    
    is_vip = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()
        
        if user and user[0] == True:
            is_vip = True
        elif not user:
            cur.execute(
                "INSERT INTO users (telegram_id, is_vip) VALUES (%s, %s)",
                (telegram_id, False)
            )
            conn.commit()
            
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error pas cek VIP: {e}")
        
    return is_vip

# --- FUNGSI HELPER: AMBIL INFO FILM (PENTING) ---
def get_movie_details(movie_id: int) -> dict:
    """Ambil data 1 film dari DB pake ID-nya."""
    conn = get_db_connection()
    if conn is None:
        return None
        
    movie = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link FROM movies WHERE id = %s;", (movie_id,))
        movie_data = cur.fetchone()
        
        if movie_data:
            movie = {
                "title": movie_data[0],
                "video_link": movie_data[1]
            }
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error pas ambil film: {e}")
        
    return movie


# --- FUNGSI 1: TAMPILIN MENU UTAMA (/start) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mengirim panel menu utama (LAYOUT BARU 2x2)."""
    
    keyboard = [
        # Baris 1: Link Grup (URL Eksternal)
        [InlineKeyboardButton("⭐️ GRUP DRAMA MU OFFICIAL ⭐️", url="https://t.me/dramamuofficial")], # <-- (URL INI UDAH BENER)
        
        # Baris 2: Cari Judul & Cari Cuan (2 kolom)
        [
            InlineKeyboardButton("🎬 CARI JUDUL [□]", web_app=WebAppInfo(url=URL_CARI_JUDUL)),
            InlineKeyboardButton("💰 CARI CUAN [□]", web_app=WebAppInfo(url=URL_REFERRAL))
        ],
        
        # Baris 3: Beli VIP & Req Drama (2 kolom)
        [
            InlineKeyboardButton("💎 BELI VIP [□]", web_app=WebAppInfo(url=URL_BELI_VIP)),
            InlineKeyboardButton("📝 REQ DRAMA [□]", web_app=WebAppInfo(url=URL_REQUEST))
        ],
        
        # Baris 4: Hubungi Kami (Link Eksternal)
        [InlineKeyboardButton("💬 HUBUNGI KAMI", url="https://t.me/kot_dik")] # <-- (URL INI UDAH BENER)
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # Coba kirim foto
        await update.message.reply_photo(
            photo=open("poster.jpg", "rb"), # (File poster.jpg lu udah di GitHub)
            caption="Selamat datang di Dramamu 🎬\n"
                    "Dunia drama dari semua aplikasi, cukup segelas kopi harganya ☕",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Gagal kirim foto: {e}. Coba kirim teks aja.")
        # Kalo gagal (misal file gak ada), kirim teks aja biar bot gak crash
        await update.message.reply_text(
            text="Selamat datang di Dramamu 🎬\n"
                 "Silakan pilih menu di bawah:",
            reply_markup=reply_markup
        )

# --- FUNGSI 2: NANGANIN DATA DARI MINI APP (PALING PENTING) ---
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nangkep data yang dikirim dari SEMUA Mini App."""
    
    data_str = update.effective_message.web_app_data.data
    user_id = update.effective_user.id
    
    try:
        data = json.loads(data_str)
        action = data.get("action")

        if action == "watch":
            # --- Alur Nonton Film (dari drama.html) ---
            movie_id = int(data.get("movie_id"))
            
            if check_vip_status(user_id):
                # USER UDAH VIP
                movie = get_movie_details(movie_id)
                if movie:
                    parts_keyboard = [[ InlineKeyboardButton("Part 1", callback_data=f"play:{movie_id}:1") ]]
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=movie["video_link"],
                        caption=f"🎥 **{movie['title']}**\nSilakan pilih part:",
                        reply_markup=InlineKeyboardMarkup(parts_keyboard),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await context.bot.send_message(chat_id=user_id, text="Aduh, film-nya gak ketemu di database.")
            else:
                # USER BELUM VIP
                keyboard = [[InlineKeyboardButton("💎 Beli VIP Sekarang [□]", web_app=WebAppInfo(url=URL_BELI_VIP))]]
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Anda belum VIP. 💎\nSilakan gabung VIP untuk menonton.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif action == "request_drama":
            # --- Alur Request Drama ---
            judul = data.get("judul")
            apk = data.get("apk")
            logger.info(f"REQUEST BARU DARI {user_id}: Judul: {judul}, APK: {apk}")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Request lu buat film '{judul}' (dari {apk}) udah kami terima, bre!"
            )
            
        elif action == "withdraw_referral":
            # --- Alur Penarikan ---
            data_penarikan = {
                "jumlah": data.get("jumlah"),
                "metode": data.get("metode"),
                "nomor": data.get("nomor_rekening"),
                "nama": data.get("nama_pemilik")
            }
            logger.info(f"PENARIKAN BARU DARI {user_id}: {data_penarikan}")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Request penarikan lu (Rp {data_penarikan['jumlah']}) udah kami terima. "
                     "Akan diproses admin 1x24 jam."
            )
    
    except Exception as e:
        logger.error(f"Error pas nanganin data WebApp: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"Aduh bre, ada error: {e}")

# --- FUNGSI 3: NANGANIN AI AGENT (opsional) ---
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    jawaban_ai = f"AI Agent lagi nanganin pesan lu: '{user_message}' (Ini masih dummy)"
    await update.message.reply_text(jawaban_ai)

# --- FUNGSI UTAMA (MAIN) ---
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    
    # Cek kalo TOKEN ada
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN tidak ditemukan! Matiin bot.")
        return
        
    logger.info("Bot (Versi FINAL) sedang berjalan...")
    
    application = Application.builder().token(BOT_TOKEN).build()

    # 1. Nanganin /start
    application.add_handler(CommandHandler("start", start))
    
    # 2. Nanganin Mini App
    application.add_handler(MessageHandler(filters.WEB_APP_DATA, handle_webapp_data)) # <--- INI BENER
    
    # 3. Nanganin AI Agent (Pesan teks biasa)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))
    
    # Mulai bot
    application.run_polling()

if __name__ == "__main__":
    main()

import psycopg2
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- BOT TOKEN (BACA DARI RENDER) ---
# (Kita asumsikan lu udah ganti tokennya di Render Environment)
BOT_TOKEN = os.environ.get("8480298677:AAEAAjfGYLyixnFoBoaci4GGIo_i9MIlxgo") 

# --- URL NETLIFY (UDAH ONLINE) ---
BASE_URL = "https://dramamuid.netlify.app" 

URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"
# ---

# --- KONEKSI DATABASE (BACA DARI RENDER) ---
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")
# ---

# String koneksi database
conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# Mengatur logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- FUNGSI HELPER: KONEK KE DB ---
def get_db_connection():
    try:
        conn = psycopg2.connect(conn_string)
        return conn
    except Exception as e:
        logger.error(f"GAGAL KONEK KE DB: {e}")
        return None

# --- FUNGSI HELPER: CEK STATUS VIP (PENTING) ---
def check_vip_status(telegram_id: int) -> bool:
    """Cek ke DB apakah user VIP atau bukan."""
    conn = get_db_connection()
    if conn is None:
        return False
    
    is_vip = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()
        
        if user and user[0] == True:
            is_vip = True
        elif not user:
            cur.execute(
                "INSERT INTO users (telegram_id, is_vip) VALUES (%s, %s)",
                (telegram_id, False)
            )
            conn.commit()
            
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error pas cek VIP: {e}")
        
    return is_vip

# --- FUNGSI HELPER: AMBIL INFO FILM (PENTING) ---
def get_movie_details(movie_id: int) -> dict:
    """Ambil data 1 film dari DB pake ID-nya."""
    conn = get_db_connection()
    if conn is None:
        return None
        
    movie = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link FROM movies WHERE id = %s;", (movie_id,))
        movie_data = cur.fetchone()
        
        if movie_data:
            movie = {
                "title": movie_data[0],
                "video_link": movie_data[1]
            }
        
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"Error pas ambil film: {e}")
        
    return movie


# --- FUNGSI 1: TAMPILIN MENU UTAMA (/start) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mengirim panel menu utama (LAYOUT BARU 2x2)."""
    
    keyboard = [
        # Baris 1: Link Grup (URL Eksternal)
        [InlineKeyboardButton("⭐️ GRUP DRAMA MU OFFICIAL ⭐️", url="https://t.me/dramamuofficial")], # <-- GANTI URL GRUP LU
        
        # Baris 2: Cari Judul & Cari Cuan (2 kolom)
        [
            InlineKeyboardButton("🎬 CARI JUDUL [□]", web_app=WebAppInfo(url=URL_CARI_JUDUL)),
            InlineKeyboardButton("💰 CARI CUAN [□]", web_app=WebAppInfo(url=URL_REFERRAL))
        ],
        
        # Baris 3: Beli VIP & Req Drama (2 kolom)
        [
            InlineKeyboardButton("💎 BELI VIP [□]", web_app=WebAppInfo(url=URL_BELI_VIP)),
            InlineKeyboardButton("📝 REQ DRAMA [□]", web_app=WebAppInfo(url=URL_REQUEST))
        ],
        
        # Baris 4: Hubungi Kami (Link Eksternal)
        [InlineKeyboardButton("💬 HUBUNGI KAMI", url="https://t.me/kot_dik")] # <-- GANTI URL ADMIN LU
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        # Coba kirim foto
        await update.message.reply_photo(
            photo=open("poster.jpg", "rb"), # (File poster.jpg lu udah di GitHub)
            caption="Selamat datang di Dramamu 🎬\n"
                    "Dunia drama dari semua aplikasi, cukup segelas kopi harganya ☕",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Gagal kirim foto: {e}. Coba kirim teks aja.")
        # Kalo gagal (misal file gak ada), kirim teks aja biar bot gak crash
        await update.message.reply_text(
            text="Selamat datang di Dramamu 🎬\n"
                 "Silakan pilih menu di bawah:",
            reply_markup=reply_markup
        )

# --- FUNGSI 2: NANGANIN DATA DARI MINI APP (PALING PENTING) ---
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nangkep data yang dikirim dari SEMUA Mini App."""
    
    data_str = update.effective_message.web_app_data.data
    user_id = update.effective_user.id
    
    try:
        data = json.loads(data_str)
        action = data.get("action")

        if action == "watch":
            # --- Alur Nonton Film (dari drama.html) ---
            movie_id = int(data.get("movie_id"))
            
            if check_vip_status(user_id):
                # USER UDAH VIP
                movie = get_movie_details(movie_id)
                if movie:
                    parts_keyboard = [[ InlineKeyboardButton("Part 1", callback_data=f"play:{movie_id}:1") ]]
                    await context.bot.send_video(
                        chat_id=user_id,
                        video=movie["video_link"],
                        caption=f"🎥 **{movie['title']}**\nSilakan pilih part:",
                        reply_markup=InlineKeyboardMarkup(parts_keyboard),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    await context.bot.send_message(chat_id=user_id, text="Aduh, film-nya gak ketemu di database.")
            else:
                # USER BELUM VIP
                keyboard = [[InlineKeyboardButton("💎 Beli VIP Sekarang [□]", web_app=WebAppInfo(url=URL_BELI_VIP))]]
                await context.bot.send_message(
                    chat_id=user_id,
                    text="Anda belum VIP. 💎\nSilakan gabung VIP untuk menonton.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        
        elif action == "request_drama":
            # --- Alur Request Drama ---
            judul = data.get("judul")
            apk = data.get("apk")
            logger.info(f"REQUEST BARU DARI {user_id}: Judul: {judul}, APK: {apk}")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Request lu buat film '{judul}' (dari {apk}) udah kami terima, bre!"
            )
            
        elif action == "withdraw_referral":
            # --- Alur Penarikan ---
            data_penarikan = {
                "jumlah": data.get("jumlah"),
                "metode": data.get("metode"),
                "nomor": data.get("nomor_rekening"),
                "nama": data.get("nama_pemilik")
            }
            logger.info(f"PENARIKAN BARU DARI {user_id}: {data_penarikan}")
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Request penarikan lu (Rp {data_penarikan['jumlah']}) udah kami terima. "
                     "Akan diproses admin 1x24 jam."
            )
    
    except Exception as e:
        logger.error(f"Error pas nanganin data WebApp: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"Aduh bre, ada error: {e}")

# --- FUNGSI 3: NANGANIN AI AGENT (opsional) ---
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text
    jawaban_ai = f"AI Agent lagi nanganin pesan lu: '{user_message}' (Ini masih dummy)"
    await update.message.reply_text(jawaban_ai)

# --- FUNGSI UTAMA (MAIN) ---
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    
    # Cek kalo TOKEN ada
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN tidak ditemukan! Matiin bot.")
        return
        
    logger.info("Bot (Versi FINAL) sedang berjalan...")
    
    application = Application.builder().token(BOT_TOKEN).build()

    # 1. Nanganin /start
    application.add_handler(CommandHandler("start", start))
    
    # 2. Nanganin Mini App
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    # 3. Nanganin AI Agent (Pesan teks biasa)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))
    
    # Mulai bot
    application.run_polling()

if __name__ == "__main__":
    main()




