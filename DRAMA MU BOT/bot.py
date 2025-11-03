import logging
import psycopg2
import json
import os
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ==========================================================
# 🔧 KONFIGURASI DASAR
# ==========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))

BASE_URL = "https://dramamuid.netlify.app"
URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"

# ==========================================================
# 📦 DATABASE CONFIG
# ==========================================================
DB_HOST = os.environ.get("DB_HOST")
DB_PORT = os.environ.get("DB_PORT")
DB_NAME = os.environ.get("DB_NAME")
DB_USER = os.environ.get("DB_USER")
DB_PASS = os.environ.get("DB_PASS")

conn_string = None
if all([DB_NAME, DB_USER, DB_HOST, DB_PORT, DB_PASS]):
    conn_string = f"dbname='{DB_NAME}' user='{DB_USER}' host='{DB_HOST}' port='{DB_PORT}' password='{DB_PASS}'"

# ==========================================================
# 🪵 LOGGING
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger("dramamu-bot")

# ==========================================================
# 🧩 DATABASE
# ==========================================================
def get_db_connection():
    if not conn_string:
        logger.error("DB belum dikonfigurasi!")
        return None
    try:
        return psycopg2.connect(conn_string)
    except Exception as e:
        logger.error(f"Gagal konek DB: {e}")
        return None


def check_vip_status(telegram_id: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        cur.execute("SELECT is_vip FROM users WHERE telegram_id = %s;", (telegram_id,))
        user = cur.fetchone()
        if user and user[0]:
            return True
        else:
            cur.execute(
                "INSERT INTO users (telegram_id, is_vip) VALUES (%s, %s) ON CONFLICT (telegram_id) DO NOTHING;",
                (telegram_id, False),
            )
            conn.commit()
            return False
    except Exception as e:
        logger.error(f"Error cek VIP: {e}")
        return False
    finally:
        conn.close()


def get_movie_details(movie_id: int) -> dict | None:
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return None
        return {"title": row[0], "video_link": row[1]}
    except Exception as e:
        logger.error(f"Error ambil movie: {e}")
        return None
    finally:
        conn.close()


# ==========================================================
# 🚀 HANDLER /start
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("⭐️ GRUP DRAMA MU OFFICIAL ⭐️", url="https://t.me/dramamuofficial")],
        [
            InlineKeyboardButton("🎬 CARI JUDUL", web_app=WebAppInfo(url=URL_CARI_JUDUL)),
            InlineKeyboardButton("💰 CARI CUAN", web_app=WebAppInfo(url=URL_REFERRAL)),
        ],
        [
            InlineKeyboardButton("💎 BELI VIP", web_app=WebAppInfo(url=URL_BELI_VIP)),
            InlineKeyboardButton("📝 REQ DRAMA", web_app=WebAppInfo(url=URL_REQUEST)),
        ],
        [InlineKeyboardButton("💬 HUBUNGI KAMI", url="https://t.me/kot_dik")],
    ]
    caption = (
        "🎬 <b>Selamat datang di Dramamu</b>\n\n"
        "Nonton semua drama favorit cuma segelas kopi ☕\n"
        "Pilih menu di bawah, bre!"
    )

    try:
        await update.message.reply_photo(
            photo=open("poster.jpg", "rb"),
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await update.message.reply_text(
            caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
        )


# ==========================================================
# 🛰 HANDLER WEBAPP DATA VIA TELEGRAM SENDDATA
# ==========================================================
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not getattr(update.message, "web_app_data", None):
        return

    user_id = update.effective_user.id
    try:
        data = json.loads(update.message.web_app_data.data)
    except Exception:
        await context.bot.send_message(user_id, "Data WebApp tidak valid bre.")
        return

    await process_webapp_action(context, user_id, data)


# ==========================================================
# 🌐 HANDLER WEBAPP VIA HTTP POST (buat fetch() dari HTML)
# ==========================================================
async def webapp_post(request: web.Request):
    try:
        payload = await request.json()
        user_id = payload.get("user_id")
        data = payload.get("data", {})

        if not user_id:
            return web.Response(status=400, text="user_id kosong")

        app: Application = request.app["telegram_app"]
        context = type("FakeCtx", (), {"bot": app.bot})()  # bikin context dummy

        await process_webapp_action(context, user_id, data)
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"WebApp POST error: {e}")
        return web.Response(status=400, text=f"Error: {e}")


# ==========================================================
# 🧠 PROSES AKSI DARI MINI APP
# ==========================================================
async def process_webapp_action(context, user_id, data):
    action = data.get("action")

    # === Aksi Nonton Drama ===
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
            keyboard = [
                [InlineKeyboardButton("💎 Beli VIP Sekarang", web_app=WebAppInfo(url=URL_BELI_VIP))]
            ]
            await context.bot.send_message(
                chat_id=user_id,
                text="🚫 Kamu belum VIP. Klik di bawah buat upgrade 💎",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

    elif action == "request_drama":
        await context.bot.send_message(user_id, f"✅ Request '{data.get('judul')}' udah kami terima!")

    elif action == "withdraw_referral":
        await context.bot.send_message(user_id, f"✅ Penarikan Rp {data.get('jumlah')} sedang diproses, bre!")

    else:
        await context.bot.send_message(user_id, "⚠️ Aksi tidak dikenal.")


# ==========================================================
# 💬 HANDLER PESAN BIASA
# ==========================================================
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or getattr(update.message, "web_app_data", None):
        return
    await update.message.reply_text(f"🤖 Pesan lo: {update.message.text}")


# ==========================================================
# ⚙️ MAIN
# ==========================================================
async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN kosong bre!")
        return

    logger.info("🚀 Dramamu Bot aktif & siap terima data dari MiniApp")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))

    # === Tambah HTTP server untuk POST ===
    web_app = web.Application()
    web_app["telegram_app"] = app
    web_app.router.add_post("/webapp", webapp_post)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", 8080)))
    await site.start()

    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
