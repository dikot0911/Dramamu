import logging
import psycopg2
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- ⚠️ GANTI INI ⚠️ ---
BOT_TOKEN = "8480298677:AAEAAjfGYLyixnFoBoaci4GGIo_i9MIlxgo"

# --- NANTI INI DIISI URL PUBLIK (NETLIFY/RENDER) SEMUA ---
BASE_URL = "https://dramamuid.netlify.app" # <-- URL Netlify lu

URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"  # (File ini belum kita bikin)
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html
# ---

# --- KONEKSI DATABASE (SAMA KAYAK main.py) ---
DB_HOST = "aws-1-ap-southeast-2.pooler.supabase.com"
DB_PORT = "6543"
DB_NAME = "postgres"
DB_USER = "postgres.geczfycekxkeiubbajz" # (Yang udah bener)
DB_PASS = "Kk02199542527"
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
            # Kalo user belum ada, daftarin
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
        # Ambil kolom yang kita perluin
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
    """Mengirim panel menu utama (SEMUA TOMBOL MINI APP)."""
    
    keyboard = [
        # Baris 1: Cari Judul (Mini App)
        [InlineKeyboardButton("🎬 Cari Judul v1 [□]", web_app=WebAppInfo(url=URL_CARI_JUDUL))],
        
        # Baris 2: Beli VIP & Profile
        [
            InlineKeyboardButton("💎 Beli VIP [□]", web_app=WebAppInfo(url=URL_BELI_VIP)),
            InlineKeyboardButton("👤 Profile [□]", web_app=WebAppInfo(url=URL_PROFILE))
        ],
        
        # Baris 3: Request & Referral
        [
            InlineKeyboardButton("📝 Request Drama [□]", web_app=WebAppInfo(url=URL_REQUEST)),
            InlineKeyboardButton("💰 Cari Cuan (Referral)", callback_data="coming_soon") # (Contoh tombol biasa)
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_photo(
        photo=open("poster.jpg", "rb"), # (Pastiin ada file poster.jpg di folder lu)
        caption="Selamat datang di Dramamu 🎬\n"
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
            # --- Alur Request Drama (BARU) ---
            judul = data.get("judul")
            apk = data.get("apk")
            
            # (Nanti lu bisa simpen ini ke DB atau kirim ke chat admin)
            logger.info(f"REQUEST BARU DARI {user_id}: Judul: {judul}, APK: {apk}")
            
            # Kasih konfirmasi ke user
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Request lu buat film '{judul}' (dari {apk}) udah kami terima, bre!"
            )
            
        # (Nanti kita tambahin 'elif action == "withdraw_referral"' di sini)
    
    except Exception as e:
        logger.error(f"Error pas nanganin data WebApp: {e}")
        await context.bot.send_message(chat_id=user_id, text=f"Aduh bre, ada error: {e}")


# --- FUNGSI UTAMA (MAIN) ---
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    print("Bot (Arsitektur BARU) sedang berjalan... (Tekan Ctrl+C untuk berhenti)")
    
    application = Application.builder().token(BOT_TOKEN).build()

    # Perintah /start
    application.add_handler(CommandHandler("start", start))
    
    # INI HANDLER BARU: Nangkep data yg dikirim dari Mini App
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    
    # (Nanti kita tambahin CallbackQueryHandler buat nanganin tombol 'Part 1', 'Part 2', dll)
    
    # Mulai bot
    application.run_polling()

if __name__ == "__main__":

    main()
