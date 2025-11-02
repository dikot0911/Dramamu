import logging
import psycopg2
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# --- BOT TOKEN (BACA DARI RENDER) ---
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

# String koneksi database (guard kalau env kosong)
conn_string = None
if DB_NAME and DB_USER and DB_HOST and DB_PORT and DB_PASS:
    conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# Mengatur logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


# --- FUNGSI HELPER: KONEK KE DB ---
def get_db_connection():
    if not conn_string:
        logger.error("Conn string DB belum lengkap (env vars mungkin belum diset).")
        return None
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

        if user and user[0] is True:
            is_vip = True
        elif not user:
            # kalau user belum ada di DB, insert record default
            try:
                cur.execute(
                    "INSERT INTO users (telegram_id, is_vip) VALUES (%s, %s)",
                    (telegram_id, False)
                )
                conn.commit()
            except Exception as e:
                logger.error(f"Gagal insert user default: {e}")

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
    """Mengirim panel menu utama (LAYOUT 2x2)."""
    keyboard = [
        # Baris 1: Link Grup (URL Eksternal)
        [InlineKeyboardButton("⭐️ GRUP DRAMA MU OFFICIAL ⭐️", url="https://t.me/dramamuofficial")],

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
        [InlineKeyboardButton("💬 HUBUNGI KAMI", url="https://t.me/kot_dik")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        # kirim foto jika file ada
        if os.path.exists("poster.jpg"):
            with open("poster.jpg", "rb") as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="Selamat datang di Dramamu 🎬\nDunia drama dari semua aplikasi, cukup segelas kopi harganya ☕",
                    reply_markup=reply_markup
                )
        else:
            # fallback teks
            await update.message.reply_text(
                text="Selamat datang di Dramamu 🎬\nSilakan pilih menu di bawah:",
                reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Gagal kirim photo/start: {e}")
        # fallback teks lagi
        try:
            await update.message.reply_text(
                text="Selamat datang di Dramamu 🎬\nSilakan pilih menu di bawah:",
                reply_markup=reply_markup
            )
        except Exception as ex:
            logger.error(f"Start handler gagal mengirim pesan: {ex}")


# --- FUNGSI 2: NANGANIN DATA DARI MINI APP (PALING PENTING) ---
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Nangkep data yang dikirim dari SEMUA Mini App.
    Kita mendaftarkan handler ke filters.ALL tapi di sini hanya memproses pesan
    yang memang berisi web_app_data.
    """
    try:
        message = update.effective_message
        user_id = update.effective_user.id if update.effective_user else None

        # cek apakah message punya web_app_data
        if not message or not getattr(message, "web_app_data", None):
            # bukan data webapp -> ignore (biarkan handler lain proses)
            return

        data_str = message.web_app_data.data
        if not data_str:
            logger.warning("WebApp data kosong.")
            return

        logger.info(f"Terima WebApp data dari {user_id}: {data_str}")

        try:
            data = json.loads(data_str)
        except Exception as e:
            logger.error(f"JSON decode error: {e} -- payload: {data_str}")
            await context.bot.send_message(chat_id=user_id, text="Maaf, data webapp tidak valid.")
            return

        action = data.get("action")

        if action == "watch":
            # --- Alur Nonton Film (dari drama.html) ---
            movie_id = int(data.get("movie_id"))
            if check_vip_status(user_id):
                movie = get_movie_details(movie_id)
                if movie:
                    parts_keyboard = [[InlineKeyboardButton("Part 1", callback_data=f"play:{movie_id}:1")]]
                    # send video: bisa pake url atau file. Pastikan link public & support streaming.
                    try:
                        await context.bot.send_video(
                            chat_id=user_id,
                            video=movie["video_link"],
                            caption=f"🎥 <b>{movie['title']}</b>\nSilakan pilih part:",
                            reply_markup=InlineKeyboardMarkup(parts_keyboard),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Gagal send_video: {e}")
                        # fallback: kirim link
                        await context.bot.send_message(chat_id=user_id, text=f"Nih link film: {movie['video_link']}")
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
                text=f"✅ Request penarikan lu (Rp {data_penarikan['jumlah']}) udah kami terima. Akan diproses admin 1x24 jam."
            )
        else:
            logger.warning(f"Webapp action unknown: {action}")
            await context.bot.send_message(chat_id=user_id, text="Aksi tidak dikenali dari WebApp.")

    except Exception as e:
        logger.exception(f"Error pas nanganin data WebApp: {e}")
        try:
            if update.effective_user:
                await context.bot.send_message(chat_id=update.effective_user.id, text=f"Aduh bre, ada error: {e}")
        except Exception:
            logger.error("Gagal kirim pesan error ke user.")


# --- FUNGSI 3: NANGANIN AI AGENT (opsional) ---
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_message = update.message.text if update.message else ""
    jawaban_ai = f"AI Agent lagi nanganin pesan lu: '{user_message}' (Ini masih dummy)"
    if update.effective_chat:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=jawaban_ai)


# --- GLOBAL ERROR HANDLER ---
async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Global error: {context.error}", exc_info=context.error)
    # optional: notify admin id (kalau ada env ADMIN_ID)
    admin_id = os.environ.get("ADMIN_ID")
    if admin_id:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=f"Bot error: {context.error}")
        except Exception:
            logger.error("Gagal kirim error ke admin.")


# --- FUNGSI UTAMA (MAIN) ---
def main() -> None:
    """Fungsi utama untuk menjalankan bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN tidak ditemukan! Matiin bot.")
        return

    logger.info("Bot (Versi FINAL) sedang berjalan...")

    application = Application.builder().token(BOT_TOKEN).build()

    # 1. Nanganin /start
    application.add_handler(CommandHandler("start", start))

    # 2. Nanganin Mini App: kita pasang ALL tapi fungsi akan cek web_app_data dulu
    application.add_handler(MessageHandler(filters.ALL, handle_webapp_data))

    # 3. Nanganin AI Agent (Pesan teks biasa)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))

    # 4. Error handler global
    application.add_error_handler(global_error_handler)

    # Mulai bot (polling)
    application.run_polling()


if __name__ == "__main__":
    main()
