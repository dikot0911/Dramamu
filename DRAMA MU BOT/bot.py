import logging
import psycopg2
import json
import os
import secrets
import hashlib
import hmac
from urllib.parse import parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode

# ==========================================================
# 🔧 KONFIGURASI DASAR
# ==========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "dramamu_bot")

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
# 🔐 VALIDASI INIT_DATA TELEGRAM
# ==========================================================
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
        logger.error(f"Error validating init_data: {e}")
        return False

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
        cur.execute("SELECT title, video_link, poster_url FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        if row:
            movie = {"title": row[0], "video_link": row[1], "poster_url": row[2]}
        cur.close()
    except Exception as e:
        logger.error(f"Error ambil movie: {e}")
    finally:
        if conn:
            conn.close()
    return movie

# ==========================================================
# 🔄 HANDLE PENDING ACTIONS DARI START TOKEN
# ==========================================================
async def handle_pending_action(telegram_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Cek dan eksekusi pending actions setelah user start bot"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT movie_id FROM pending_actions WHERE telegram_id = %s AND expires_at > NOW();",
            (telegram_id,)
        )
        pending_actions = cur.fetchall()
        
        for action in pending_actions:
            movie_id = action[0]
            movie = get_movie_details(movie_id)
            if movie:
                await send_movie_to_user(telegram_id, movie, context)
        
        # Hapus pending actions yang sudah diproses
        cur.execute("DELETE FROM pending_actions WHERE telegram_id = %s;", (telegram_id,))
        conn.commit()
        cur.close()
        
    except Exception as e:
        logger.error(f"Error handle pending action: {e}")
    finally:
        if conn:
            conn.close()

# ==========================================================
# 📤 FUNGSI KIRIM FILM KE USER
# ==========================================================
async def send_movie_to_user(chat_id: int, movie: dict, context: ContextTypes.DEFAULT_TYPE):
    """Kirim film ke user dengan fallback ke link"""
    try:
        if movie.get("poster_url"):
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=movie["poster_url"],
                caption=f"🎥 <b>{movie['title']}</b>\n\nKlik tombol di bawah untuk menonton:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎬 Tonton Sekarang", url=movie["video_link"])
                ]])
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎥 <b>{movie['title']}</b>\n\n{movie['video_link']}",
                parse_mode=ParseMode.HTML
            )
        return True
    except Exception as e:
        logger.error(f"Gagal kirim film ke {chat_id}: {e}")
        return False

# ==========================================================
# 🚀 HANDLER /start DENGAN TOKEN SUPPORT
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    
    # Handle start dengan token
    if args and len(args) > 0:
        start_token = args[0]
        await handle_start_token(user_id, start_token, context)
    
    # Tampilkan menu utama
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
    
    # Cek pending actions untuk user ini
    await handle_pending_action(user_id, context)

async def handle_start_token(user_id: int, token: str, context: ContextTypes.DEFAULT_TYPE):
    """Handle start token dari fallback link"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT movie_id FROM pending_actions WHERE telegram_id = %s AND start_token = %s AND expires_at > NOW();",
            (user_id, token)
        )
        result = cur.fetchone()
        
        if result:
            movie_id = result[0]
            movie = get_movie_details(movie_id)
            if movie:
                await send_movie_to_user(user_id, movie, context)
            
            # Hapus token yang sudah digunakan
            cur.execute("DELETE FROM pending_actions WHERE telegram_id = %s AND start_token = %s;", 
                       (user_id, token))
            conn.commit()
        
        cur.close()
    except Exception as e:
        logger.error(f"Error handle start token: {e}")
    finally:
        if conn:
            conn.close()

# ==========================================================
# 📡 HANDLER WEBAPP DATA YANG DIPERBAIKI
# ==========================================================
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.web_app_data:
        return

    user_id = update.effective_user.id
    data_str = update.message.web_app_data.data
    
    try:
        data = json.loads(data_str)
        action = data.get("action")
        
        if action == "watch":
            await handle_watch_action(user_id, data, context)
        elif action == "request_drama":
            await handle_request_action(user_id, data, context)
        elif action == "withdraw_referral":
            await handle_withdraw_action(user_id, data, context)
        else:
            await context.bot.send_message(chat_id=user_id, text="⚠️ Aksi tidak dikenali.")
            
    except json.JSONDecodeError:
        await context.bot.send_message(chat_id=user_id, text="❌ Data tidak valid.")

async def handle_watch_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Handle aksi nonton film dengan fallback mechanism"""
    movie_id = int(data.get("movie_id", 0))
    init_data = data.get("init_data")  # Data dari Telegram WebApp
    
    if not movie_id:
        await context.bot.send_message(chat_id=user_id, text="❌ Film tidak valid.")
        return

    # Verifikasi init_data jika ada
    if init_data and not verify_telegram_init_data(init_data, BOT_TOKEN):
        await context.bot.send_message(chat_id=user_id, text="❌ Akses tidak sah.")
        return

    # Cek status VIP
    if not check_vip_status(user_id):
        keyboard = [[InlineKeyboardButton("💎 Beli VIP Sekarang", web_app=WebAppInfo(url=URL_BELI_VIP))]]
        await context.bot.send_message(
            chat_id=user_id,
            text="🚫 Anda belum VIP.\nGabung VIP biar bisa nonton full, bre!",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    movie = get_movie_details(movie_id)
    if not movie:
        await context.bot.send_message(chat_id=user_id, text="❌ Film tidak ditemukan.")
        return

    # Coba kirim film langsung
    success = await send_movie_to_user(user_id, movie, context)
    
    if not success:
        # Fallback: buat start token dan simpan pending action
        start_token = secrets.token_urlsafe(16)
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO pending_actions (telegram_id, movie_id, start_token, expires_at) VALUES (%s, %s, %s, NOW() + INTERVAL '15 minutes');",
                    (user_id, movie_id, start_token)
                )
                conn.commit()
                cur.close()
                
                # Kirim fallback link
                fallback_link = f"https://t.me/{BOT_USERNAME}?start={start_token}"
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📨 Gagal mengirim film secara langsung. Klik link berikut untuk menonton:\n{fallback_link}"
                )
            except Exception as e:
                logger.error(f"Error create fallback: {e}")
            finally:
                conn.close()
    else:
        # Jika berhasil, tutup webapp
        try:
            if update.effective_message and hasattr(update.effective_message, 'reply_text'):
                await update.effective_message.reply_text(
                    "✅ Film berhasil dikirim! Cek chat Telegram Anda.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Kembali ke Menu", web_app=WebAppInfo(url=URL_CARI_JUDUL))
                    ]])
                )
        except Exception as e:
            logger.error(f"Error sending success message: {e}")

async def handle_request_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Handle aksi request drama"""
    judul = data.get("judul", "-")
    apk = data.get("apk", "-")
    logger.info(f"📝 REQUEST: {user_id} — {judul} dari {apk}")
    await context.bot.send_message(chat_id=user_id, text=f"✅ Request '{judul}' (dari {apk}) udah kami terima!")

async def handle_withdraw_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Handle aksi withdraw referral"""
    jumlah = data.get("jumlah")
    metode = data.get("metode")
    nomor = data.get("nomor_rekening")
    nama = data.get("nama_pemilik")

    logger.info(f"💸 PENARIKAN: {user_id} — Rp{jumlah} via {metode} ({nama} - {nomor})")
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ Request penarikan Rp {jumlah} udah diterima.\nDiproses admin dalam 1x24 jam.",
    )

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
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))

    app.add_error_handler(global_error_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
