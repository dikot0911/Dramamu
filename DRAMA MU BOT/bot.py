import logging
import psycopg2
import json
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==========================================================
# 🔧 KONFIGURASI DASAR
# ==========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")

BASE_URL = "https://dramamuid.netlify.app"
URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"

# ==========================================================
# 📦 DATABASE CONFIG (POSTGRESQL)
# ==========================================================
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")

conn_string = None
if DB_NAME and DB_USER and DB_HOST and DB_PORT and DB_PASS:
    conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# ==========================================================
# 🪵 LOGGING
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("dramamu-bot")


# ==========================================================
# 🧩 HELPER: DATABASE CONNECTION
# ==========================================================
def get_db_connection():
    if not conn_string:
        logger.error("DB connection string belum lengkap!")
        return None
    try:
        conn = psycopg2.connect(conn_string)
        return conn
    except Exception as e:
        logger.error(f"Gagal konek DB: {e}")
        return None


# ==========================================================
# 💎 CEK STATUS VIP USER
# ==========================================================
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
        elif not user:
            cur.execute(
                "INSERT INTO users (telegram_id, is_vip) VALUES (%s, %s)",
                (telegram_id, False),
            )
            conn.commit()
        cur.close()
    except Exception as e:
        logger.error(f"Error cek VIP: {e}")
    finally:
        if conn:
            conn.close()

    return is_vip


# ==========================================================
# 🎬 AMBIL DETAIL FILM
# ==========================================================
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


# ==========================================================
# 🚀 HANDLER /start
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐️ GRUP DRAMA MU OFFICIAL ⭐️", url="https://t.me/dramamuofficial")],
        [
            InlineKeyboardButton("🎬 CARI JUDUL [□]", web_app=WebAppInfo(url=URL_CARI_JUDUL)),
            InlineKeyboardButton("💰 CARI CUAN [□]", web_app=WebAppInfo(url=URL_REFERRAL)),
        ],
        [
            InlineKeyboardButton("💎 BELI VIP [□]", web_app=WebAppInfo(url=URL_BELI_VIP)),
            InlineKeyboardButton("📝 REQ DRAMA [□]", web_app=WebAppInfo(url=URL_REQUEST)),
        ],
        [InlineKeyboardButton("💬 HUBUNGI KAMI", url="https://t.me/kot_dik")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    caption = (
        "🎬 <b>Selamat datang di Dramamu</b>\n\n"
        "Nonton semua drama favorit cuma segelas kopi ☕\n"
        "Pilih menu di bawah, bre!"
    )

    try:
        if os.path.exists("poster.jpg"):
            with open("poster.jpg", "rb") as img:
                await update.message.reply_photo(
                    photo=img, caption=caption, reply_markup=reply_markup, parse_mode=ParseMode.HTML
                )
        else:
            await update.message.reply_text(caption, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Gagal kirim /start: {e}")
        await update.message.reply_text("Halo bre! Pilih menu di bawah 👇", reply_markup=reply_markup)


# ==========================================================
# 📡 HANDLER WEBAPP DATA
# ==========================================================
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user_id = update.effective_user.id if update.effective_user else None

    # (Logika lu udah bener)
    # cek apakah pesan mengandung web_app_data
    if not message or not getattr(message, "web_app_data", None):
        return

    data_str = message.web_app_data.data
    if not data_str:
        logger.warning("⚠️ WebApp data kosong.")
        return

    logger.info(f"📨 Data diterima dari {user_id}: {data_str}")

    # decode JSON
    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        await context.bot.send_message(chat_id=user_id, text="Data dari WebApp tidak valid, bre.")
        return

    action = data.get("action")

    # =============================
    # 1️⃣ AKSI NONTON DRAMA
    # =============================
    if action == "watch":
        movie_id = int(data.get("movie_id", 0))
        if not movie_id:
            await context.bot.send_message(chat_id=user_id, text="Film gak valid.")
            return

        if check_vip_status(user_id):
            movie = get_movie_details(movie_id)
            if not movie:
                await context.bot.send_message(chat_id=user_id, text="Film gak ditemukan di database.")
                return

            try:
                await context.bot.send_video(
                    chat_id=user_id,
                    video=movie["video_link"],
                    caption=f"🎥 <b>{movie['title']}</b>",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.error(f"Gagal kirim video: {e}")
                await context.bot.send_message(chat_id=user_id, text=f"🎬 {movie['title']}\n{movie['video_link']}")
        else:
            keyboard = [[InlineKeyboardButton("💎 Beli VIP Sekarang [□]", web_app=WebAppInfo(url=URL_BELI_VIP))]]
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 Anda belum VIP.\nGabung VIP biar bisa nonton full, bre!",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    # =============================
    # 2️⃣ AKSI REQUEST DRAMA
    # =============================
    elif action == "request_drama":
        judul = data.get("judul", "-")
        apk = data.get("apk", "-")
        logger.info(f"📝 REQUEST: {user_id} — {judul} dari {apk}")
        await context.bot.send_message(chat_id=user_id, text=f"✅ Request '{judul}' (dari {apk}) udah kami terima!")

    # =============================
    # 3️⃣ AKSI WITHDRAW REFERRAL
    # =============================
    elif action == "withdraw_referral":
        jumlah = data.get("jumlah")
        metode = data.get("metode")
        nomor = data.get("nomor_rekening")
        nama = data.get("nama_pemilik")

        logger.info(f"💸 PENARIKAN: {user_id} — Rp{jumlah} via {metode} ({nama} - {nomor})")
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Request penarikan Rp {jumlah} udah diterima.\nDiproses admin dalam 1x24 jam.",
        )

    # =============================
    # ❓AKSI TIDAK DIKENALI
    # =============================
    else:
        logger.warning(f"Aksi tidak dikenal: {action}")
        await context.bot.send_message(chat_id=user_id, text="⚠️ Aksi tidak dikenali dari WebApp.")


# ==========================================================
# 💬 HANDLER PESAN BIASA (AI AGENT)
# ==========================================================
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text or msg.web_app_data:
        return
    user_msg = msg.text
    await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🤖 AI belum aktif, bre. Pesan: {user_msg}")


# ==========================================================
# ⚠️ GLOBAL ERROR HANDLER
# ==========================================================
async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Global error: {context.error}", exc_info=context.error)
    admin_id = os.environ.get("ADMIN_ID")
    if admin_id:
        try:
            await context.bot.send_message(chat_id=int(admin_id), text=f"⚠️ Bot error: {context.error}")
        except Exception:
            pass


# ==========================================================
# 🧠 MAIN FUNCTION
# ==========================================================
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN kosong, bre! Set env-nya dulu.")
        return

    logger.info("🚀 Dramamu Bot sudah jalan...")

    app = Application.builder().token(BOT_TOKEN).build()

    # === HANDLER ===
    app.add_handler(CommandHandler("start", start))
    
    # HAPUS HANDLER YANG BIKIN CRASH (filters.StatusUpdate.WEB_APP_DATA)
    # TINGGALIN HANDLER filters.ALL (LOGIKA LU UDAH BENER)
    app.add_handler(MessageHandler(filters.ALL, handle_webapp_data), group=-1)
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler), group=1)

    app.add_error_handler(global_error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()

