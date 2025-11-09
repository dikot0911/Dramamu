import logging
import psycopg2
import json
import os
import secrets
import hashlib
import hmac
import httpx
from typing import Optional
from urllib.parse import parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest, NetworkError, Forbidden

# ==========================================================
# 🔧 KONFIGURASI DASAR
# ==========================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "dramamu_bot")
ADMIN_ID = os.environ.get("ADMIN_ID")

BASE_URL = os.environ.get("FRONTEND_URL", "https://famous-semolina-e06e90.netlify.app")
URL_CARI_JUDUL = f"{BASE_URL}/index.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"

# ==========================================================
# 📦 DATABASE CONFIG
# ==========================================================
DATABASE_URL = os.environ.get("DATABASE_URL")

# ==========================================================
# 🪵 LOGGING
# ==========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger("dramamu-bot")

# ==========================================================
# 🧩 HELPER: DATABASE CONNECTION
# ==========================================================
def get_db_connection():
    if not DATABASE_URL:
        logger.error("DATABASE_URL tidak tersedia!")
        return None
    try:
        conn = psycopg2.connect(DATABASE_URL)
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
        if not init_data or not bot_token:
            return False

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
# 💎 CEK STATUS VIP USER (Dengan Race Condition Protection)
# ==========================================================
def check_vip_status(telegram_id: int) -> bool:
    conn = get_db_connection()
    if not conn:
        return False

    is_vip = False
    try:
        cur = conn.cursor()

        # Gunakan UPSERT untuk hindari race condition
        cur.execute("""
            INSERT INTO users (telegram_id, is_vip, created_at) 
            VALUES (%s, %s, NOW())
            ON CONFLICT (telegram_id) 
            DO UPDATE SET telegram_id = EXCLUDED.telegram_id
            RETURNING is_vip;
        """, (telegram_id, False))

        result = cur.fetchone()
        is_vip = result[0] if result else False
        conn.commit()

    except Exception as e:
        logger.error(f"Error cek VIP: {e}")
        try:
            conn.rollback()
        except:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

    return is_vip

# ==========================================================
# 🎬 AMBIL DETAIL FILM (Dengan Error Handling Lengkap)
# ==========================================================
def get_movie_details(movie_id: int) -> Optional[dict]:
    conn = get_db_connection()
    if not conn:
        return None

    movie: Optional[dict] = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT title, video_link, poster_url FROM movies WHERE id = %s;", (movie_id,))
        row = cur.fetchone()
        if row:
            movie = {
                "title": row[0] or "Judul Tidak Tersedia",
                "video_link": row[1] or "#",
                "poster_url": row[2] or "https://via.placeholder.com/300x450/333333/FFFFFF?text=No+Image"
            }
        cur.close()
    except Exception as e:
        logger.error(f"Error ambil movie {movie_id}: {e}")
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass
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
            "SELECT movie_id FROM pending_actions WHERE telegram_id = %s AND expires_at > NOW() AND status = 'pending';",
            (telegram_id,)
        )
        pending_actions = cur.fetchall()

        processed_movies = []
        for action in pending_actions:
            movie_id = action[0]
            movie = get_movie_details(movie_id)
            if movie:
                success = await send_movie_to_user(telegram_id, movie, context)
                if success:
                    processed_movies.append(movie_id)

        # Hapus pending actions yang sudah diproses
        if processed_movies:
            cur.execute(
                "DELETE FROM pending_actions WHERE telegram_id = %s AND movie_id = ANY(%s);",
                (telegram_id, processed_movies)
            )
            conn.commit()

        cur.close()

    except Exception as e:
        logger.error(f"Error handle pending action: {e}")
        try:
            conn.rollback()
        except:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# ==========================================================
# 📤 FUNGSI KIRIM FILM KE USER (Dengan Comprehensive Error Handling)
# ==========================================================
async def send_movie_to_user(chat_id: int, movie: dict, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Kirim film ke user dengan fallback ke link"""
    try:
        # Validasi data movie
        if not movie or not movie.get("video_link"):
            logger.error(f"Movie data invalid for user {chat_id}")
            return False

        video_link = movie["video_link"]
        title = movie.get("title", "Film")
        poster_url = movie.get("poster_url")

        # Cek jika video_link adalah URL valid
        if not video_link.startswith(('http://', 'https://')):
            logger.error(f"Invalid video link for user {chat_id}: {video_link}")
            return False

        if poster_url and poster_url.startswith(('http://', 'https://')):
            try:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=poster_url,
                    caption=f"🎥 <b>{title}</b>\n\nKlik tombol di bawah untuk menonton:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🎬 Tonton Sekarang", url=video_link)
                    ]])
                )
                return True
            except (BadRequest, NetworkError) as e:
                logger.warning(f"Gagal kirim photo, fallback ke text: {e}")
                # Fallback ke text message

        # Kirim sebagai text message
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎥 <b>{title}</b>\n\n{video_link}",
            parse_mode=ParseMode.HTML
        )
        return True

    except Forbidden as e:
        logger.error(f"Bot blocked by user {chat_id}: {e}")
        return False
    except BadRequest as e:
        logger.error(f"BadRequest sending to {chat_id}: {e}")
        return False
    except NetworkError as e:
        logger.error(f"NetworkError sending to {chat_id}: {e}")
        return False
    except TelegramError as e:
        logger.error(f"TelegramError sending to {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending to {chat_id}: {e}")
        return False

# ==========================================================
# 🚀 HANDLER /start DENGAN TOKEN SUPPORT
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    args = context.args

    logger.info(f"User {user_id} memulai bot dengan args: {args}")

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
                    photo=img, 
                    caption=caption, 
                    reply_markup=reply_markup, 
                    parse_mode=ParseMode.HTML
                )
        else:
            await update.message.reply_text(
                caption, 
                reply_markup=reply_markup, 
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Gagal kirim /start: {e}")
        await update.message.reply_text(
            "Halo bre! Pilih menu di bawah 👇", 
            reply_markup=reply_markup
        )

    # Cek pending actions untuk user ini
    await handle_pending_action(user_id, context)

async def handle_start_token(user_id: int, token: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle start token - SISTEM PELANTARA BARU
    1. Bot terima /start dengan token
    2. Panggil endpoint pelantara untuk ambil data yang ditahan
    3. Kirim film ke user
    """
    try:
        # Ambil backend URL dari environment
        backend_url = os.environ.get("BACKEND_URL")
        if not backend_url:
            # Fallback ke RAILWAY_PUBLIC_DOMAIN (auto-set oleh Railway)
            railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
            if railway_domain:
                backend_url = f"https://{railway_domain}"
            else:
                logger.error("BACKEND_URL atau RAILWAY_PUBLIC_DOMAIN tidak tersedia!")
                return

        # Panggil endpoint pelantara untuk release data
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{backend_url}/api/v1/release_movie_data/{token}"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("valid"):
                    # Data valid, kirim film ke user
                    movie_data = data.get("movie_data")
                    telegram_id = data.get("telegram_id")
                    
                    if movie_data and telegram_id == user_id:
                        success = await send_movie_to_user(user_id, movie_data, context)
                        
                        if success:
                            logger.info(f"✅ Film berhasil dikirim dari pelantara ke user {user_id}")
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="✅ Film berhasil dikirim! Selamat menonton! 🍿"
                            )
                        else:
                            logger.error(f"❌ Gagal kirim film ke user {user_id}")
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="❌ Maaf, terjadi kesalahan saat mengirim film. Silakan coba lagi."
                            )
                    else:
                        logger.warning(f"Data tidak sesuai untuk user {user_id}")
                else:
                    logger.warning(f"Token tidak valid atau expired: {token}")
                    # Fallback ke sistem lama (pending_actions)
                    await handle_start_token_legacy(user_id, token, context)
            else:
                logger.error(f"Error response from backend: {response.status_code}")
                # Fallback ke sistem lama
                await handle_start_token_legacy(user_id, token, context)
                
    except Exception as e:
        logger.error(f"Error handle start token pelantara: {e}")
        # Fallback ke sistem lama jika ada error
        await handle_start_token_legacy(user_id, token, context)

async def handle_start_token_legacy(user_id: int, token: str, context: ContextTypes.DEFAULT_TYPE):
    """Handle start token dari sistem lama (pending_actions) - Fallback"""
    conn = get_db_connection()
    if not conn:
        return

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT movie_id FROM pending_actions WHERE telegram_id = %s AND start_token = %s AND expires_at > NOW() AND status = 'pending';",
            (user_id, token)
        )
        result = cur.fetchone()

        if result:
            movie_id = result[0]
            movie = get_movie_details(movie_id)
            if movie:
                success = await send_movie_to_user(user_id, movie, context)
                if success:
                    # Update status jadi processed
                    cur.execute(
                        "UPDATE pending_actions SET status = 'processed' WHERE telegram_id = %s AND start_token = %s;",
                        (user_id, token)
                    )
                    conn.commit()
                    logger.info(f"Successfully processed legacy pending action for user {user_id}, movie {movie_id}")

        cur.close()
    except Exception as e:
        logger.error(f"Error handle legacy start token: {e}")
        try:
            conn.rollback()
        except:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except:
            pass

# ==========================================================
# 📡 HANDLER WEBAPP DATA YANG DIPERBAIKI
# ==========================================================
async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.web_app_data or not update.effective_user:
        return

    user_id = update.effective_user.id
    data_str = update.message.web_app_data.data

    logger.info(f"Received webapp data from user {user_id}: {data_str[:100]}...")

    try:
        data = json.loads(data_str)
        action = data.get("action")

        if action == "watch":
            await handle_watch_action(user_id, data, context, update)
        elif action == "request_drama":
            await handle_request_action(user_id, data, context)
        elif action == "withdraw_referral":
            await handle_withdraw_action(user_id, data, context)
        else:
            await context.bot.send_message(
                chat_id=user_id, 
                text="⚠️ Aksi tidak dikenali."
            )

    except json.JSONDecodeError:
        logger.info(f"Received plain string (transaction_id) from user {user_id}")
        await handle_transaction_id(user_id, data_str, context)
        
    except Exception as e:
        logger.error(f"Unexpected error in webapp handler for user {user_id}: {e}")
        await context.bot.send_message(
            chat_id=user_id, 
            text="❌ Terjadi kesalahan sistem."
        )

async def handle_transaction_id(user_id: int, transaction_id: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle transaction_id dari Mini App sendData()
    AUTO-TRIGGER /start command tanpa klik manual
    
    Alur:
    1. Terima transaction_id dari sendData()
    2. Panggil backend untuk release data film
    3. Kirim film ke user
    """
    try:
        transaction_id = transaction_id.strip()
        
        if not transaction_id:
            logger.warning(f"Empty transaction_id from user {user_id}")
            return
            
        logger.info(f"Processing transaction_id {transaction_id} for user {user_id}")
        
        backend_url = os.environ.get("BACKEND_URL")
        if not backend_url:
            # Fallback ke RAILWAY_PUBLIC_DOMAIN (auto-set oleh Railway)
            railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
            if railway_domain:
                backend_url = f"https://{railway_domain}"
            else:
                logger.error("BACKEND_URL atau RAILWAY_PUBLIC_DOMAIN tidak tersedia!")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Konfigurasi server error. Silakan hubungi admin."
                )
                return
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{backend_url}/api/v1/release_movie_data/{transaction_id}"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get("valid"):
                    movie_data = data.get("movie_data")
                    telegram_id = data.get("telegram_id")
                    
                    if movie_data and telegram_id == user_id:
                        success = await send_movie_to_user(user_id, movie_data, context)
                        
                        if success:
                            logger.info(f"✅ Film berhasil dikirim via sendData ke user {user_id}")
                        else:
                            logger.error(f"❌ Gagal kirim film ke user {user_id}")
                            await context.bot.send_message(
                                chat_id=user_id,
                                text="❌ Maaf, terjadi kesalahan saat mengirim film. Silakan coba lagi."
                            )
                    else:
                        logger.warning(f"Data tidak sesuai untuk user {user_id}")
                        await context.bot.send_message(
                            chat_id=user_id,
                            text="❌ Data tidak valid. Silakan coba lagi."
                        )
                else:
                    logger.warning(f"Transaction ID tidak valid atau expired: {transaction_id}")
                    await context.bot.send_message(
                        chat_id=user_id,
                        text="❌ Link sudah expired atau tidak valid. Silakan pilih film lagi."
                    )
            else:
                logger.error(f"Error response from backend: {response.status_code}")
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Terjadi kesalahan server. Silakan coba lagi."
                )
                
    except Exception as e:
        logger.error(f"Error handle transaction_id: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Terjadi kesalahan sistem. Silakan coba lagi."
        )

async def handle_watch_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE, update: Optional[Update] = None):
    """Handle aksi nonton film dengan fallback mechanism"""
    try:
        movie_id = int(data.get("movie_id", 0))
        init_data = data.get("init_data", "")

        if not movie_id or movie_id <= 0:
            await context.bot.send_message(
                chat_id=user_id, 
                text="❌ Film tidak valid."
            )
            return

        # Verifikasi init_data jika ada
        if init_data and BOT_TOKEN and not verify_telegram_init_data(init_data, BOT_TOKEN):
            logger.warning(f"Invalid init_data from user {user_id}")
            await context.bot.send_message(
                chat_id=user_id, 
                text="❌ Akses tidak sah."
            )
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
            await context.bot.send_message(
                chat_id=user_id, 
                text="❌ Film tidak ditemukan."
            )
            return

        # Coba kirim film langsung
        success = await send_movie_to_user(user_id, movie, context)

        if not success:
            # Fallback: buat start token dan simpan pending action
            start_token = secrets.token_urlsafe(32)
            conn = get_db_connection()
            if conn:
                try:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO pending_actions (telegram_id, movie_id, start_token, expires_at, status) VALUES (%s, %s, %s, NOW() + INTERVAL '15 minutes', 'pending');",
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
                    logger.info(f"Created fallback for user {user_id}, movie {movie_id}")
                except Exception as e:
                    logger.error(f"Error create fallback for user {user_id}: {e}")
                finally:
                    try:
                        conn.close()
                    except:
                        pass
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Gagal membuat fallback. Coba lagi nanti."
                )
        else:
            # Jika berhasil, kirim konfirmasi
            try:
                if update and update.effective_message:
                    await update.effective_message.reply_text(
                        "✅ Film berhasil dikirim! Cek chat Telegram Anda.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔙 Kembali ke Menu", web_app=WebAppInfo(url=URL_CARI_JUDUL))
                        ]])
                    )
                logger.info(f"Successfully sent movie {movie_id} to user {user_id}")
            except Exception as e:
                logger.error(f"Error sending success message to user {user_id}: {e}")

    except Exception as e:
        logger.error(f"Unexpected error in watch action for user {user_id}: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Terjadi kesalahan sistem."
        )

async def handle_request_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Handle aksi request drama"""
    try:
        judul = data.get("judul", "-")[:500]  # Limit length
        apk = data.get("apk", "-")[:100]

        logger.info(f"📝 REQUEST: {user_id} — {judul} dari {apk}")

        # Log ke database jika perlu
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO requests (telegram_id, judul, aplikasi, created_at) VALUES (%s, %s, %s, NOW());",
                    (user_id, judul, apk)
                )
                conn.commit()
                cur.close()
            except Exception as e:
                logger.error(f"Error logging request: {e}")
            finally:
                try:
                    conn.close()
                except:
                    pass

        await context.bot.send_message(
            chat_id=user_id, 
            text=f"✅ Request '{judul}' (dari {apk}) udah kami terima!"
        )

    except Exception as e:
        logger.error(f"Error handling request for user {user_id}: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Gagal memproses request."
        )

async def handle_withdraw_action(user_id: int, data: dict, context: ContextTypes.DEFAULT_TYPE):
    """Handle aksi withdraw referral"""
    try:
        jumlah = data.get("jumlah", "0")
        metode = data.get("metode", "-")[:50]
        nomor = data.get("nomor_rekening", "-")[:100]
        nama = data.get("nama_pemilik", "-")[:100]

        logger.info(f"💸 PENARIKAN: {user_id} — Rp{jumlah} via {metode} ({nama} - {nomor})")

        # Simpan ke database
        conn = get_db_connection()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO withdrawal_requests (telegram_id, amount, method, account_number, account_name, status, created_at) VALUES (%s, %s, %s, %s, %s, 'pending', NOW());",
                    (user_id, jumlah, metode, nomor, nama)
                )
                conn.commit()
                cur.close()
            except Exception as e:
                logger.error(f"Error logging withdrawal: {e}")
            finally:
                try:
                    conn.close()
                except:
                    pass

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ Request penarikan Rp {jumlah} udah diterima.\nDiproses admin dalam 1x24 jam."
        )

    except Exception as e:
        logger.error(f"Error handling withdrawal for user {user_id}: {e}")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Gagal memproses penarikan."
        )

# ==========================================================
# 💬 HANDLER PESAN BIASA (AI AGENT)
# ==========================================================
async def ai_agent_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.effective_message
        if not msg or not msg.text or msg.web_app_data or not update.effective_user or not update.effective_chat:
            return

        user_msg = msg.text.strip()
        user_id = update.effective_user.id

        logger.info(f"AI Agent received message from {user_id}: {user_msg}")

        # Simple response untuk sekarang
        responses = [
            "Halo bre! Gunakan menu di bawah untuk akses fitur.",
            "Cari drama lewat menu 'CARI JUDUL' ya bre!",
            "Mau nonton? Langsung cari judulnya di menu!",
            "Fitur AI masih dalam pengembangan, bre!",
        ]

        import random
        response = random.choice(responses)

        await context.bot.send_message(
            chat_id=update.effective_chat.id, 
            text=response
        )

    except Exception as e:
        logger.error(f"Error in AI agent handler: {e}")

# ==========================================================
# ⚠️ GLOBAL ERROR HANDLER
# ==========================================================
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error_msg = f"Global error: {context.error}"
    logger.error(error_msg, exc_info=context.error)

    if ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=int(ADMIN_ID), 
                text=f"⚠️ Bot error: {context.error}"
            )
        except Exception as e:
            logger.error(f"Failed to send error to admin: {e}")

# ==========================================================
# 🧠 MAIN FUNCTION
# ==========================================================
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN kosong, bre! Set env-nya dulu.")
        return

    logger.info("🚀 Dramamu Bot sudah jalan...")

    try:
        app = Application.builder().token(BOT_TOKEN).build()

        # === HANDLER ===
        app.add_handler(CommandHandler("start", start))
        app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_agent_handler))

        app.add_error_handler(global_error_handler)

        logger.info("✅ Bot started successfully")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )

    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        raise

if __name__ == "__main__":
    main()
