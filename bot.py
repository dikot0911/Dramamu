import json
import logging
import requests
import random
import string
import os
from datetime import datetime, timedelta
from typing import cast
from telebot import TeleBot, types
from telebot.apihelper import ApiTelegramException
from database import SessionLocal, User, Movie, check_and_update_vip_expiry
from config import (
    TELEGRAM_BOT_TOKEN,
    BASE_URL,
    URL_CARI_JUDUL,
    URL_CARI_CUAN,
    URL_BELI_VIP,
    URL_REQUEST,
    URL_HUBUNGI_KAMI,
    now_utc
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

if TELEGRAM_BOT_TOKEN:
    bot = TeleBot(TELEGRAM_BOT_TOKEN)
else:
    bot = None  # type: ignore

def escape_html(text):
    if not text:
        return text
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))

def generate_ref_code(telegram_id):
    logger.info(f"Bikin ref_code buat {telegram_id}...")
    first_five = str(telegram_id)[:5]
    rand_part = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
    ref_code = f"{first_five}{rand_part}"
    logger.info(f"Ref_code jadi: {ref_code}")
    return ref_code

def get_or_create_user(user, referred_by_code=None):
    db = SessionLocal()
    try:
        telegram_id = str(user.id)
        db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
        
        if db_user:
            logger.info(f"User {telegram_id} udah ada di DB")
            return db_user
        else:
            logger.info(f"User {telegram_id} belum ada, bikin baru...")
            ref_code = generate_ref_code(telegram_id)
            
            new_user = User(
                telegram_id=telegram_id,
                username=user.username,
                ref_code=ref_code,
                referred_by_code=referred_by_code,
                is_vip=False,
                commission_balance=0,
                total_referrals=0
            )
            
            db.add(new_user)
            
            if referred_by_code:
                referrer = db.query(User).filter(User.ref_code == referred_by_code).first()
                if referrer:
                    referrer.total_referrals += 1  # type: ignore
                    logger.info(f"✅ User {telegram_id} akan dibuat dengan ref_code: {ref_code}, referred_by: {referred_by_code} - Referrer total_referrals akan diupdate")
                else:
                    logger.warning(f"⚠️ Referrer dengan code {referred_by_code} tidak ditemukan")
                    logger.info(f"✅ User {telegram_id} akan dibuat dengan ref_code: {ref_code}, referred_by: {referred_by_code}")
            else:
                logger.info(f"✅ User {telegram_id} akan dibuat dengan ref_code: {ref_code}")
            
            db.commit()
            db.refresh(new_user)
            
            return new_user
    except Exception as e:
        logger.error(f"❌ Error di get_or_create_user: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def is_vip(user_id):
    db = SessionLocal()
    try:
        user_id_str = str(user_id)
        db_user = db.query(User).filter(User.telegram_id == user_id_str).first()
        
        if not db_user:
            logger.warning(f"Cek VIP: User {user_id_str} gak ada")
            return False
        
        return check_and_update_vip_expiry(db_user, db)
        
    except Exception as e:
        logger.error(f"❌ Error di is_vip: {e}")
        return False
    finally:
        db.close()

def get_movie_by_id(movie_id):
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if movie:
            return {
                'id': movie.id,
                'title': movie.title,
                'description': movie.description,
                'poster_url': movie.poster_url,
                'video_link': movie.video_link
            }
        return None
    except Exception as e:
        logger.error(f"Error waktu ambil film: {e}")
        return None
    finally:
        db.close()

if bot is not None:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        logger.info(f"User {message.from_user.id} mulai pake bot")
        
        try:
            ref_code = None
            if message.text and len(message.text.split()) > 1:
                ref_code = message.text.split()[1].strip()
                logger.info(f"User join pake kode referral: {ref_code}")
            
            user_db = get_or_create_user(message.from_user, referred_by_code=ref_code)
            if not user_db:
                logger.error(f"Gagal bikin/ambil data user {message.from_user.id}")
                bot.send_message(message.chat.id, "Terjadi kesalahan. Coba lagi nanti.")
                return
        except Exception as e:
            logger.error(f"Error gede di /start waktu get_or_create_user: {e}")
            bot.send_message(message.chat.id, "Bot sedang mengalami gangguan. Tunggu sebentar ya.")
            return

        welcome_text = (
            "🎬 <b>Selamat datang di Dramamu Bot!</b>\n\n"
            "Nonton drama favorit kamu dengan harga terjangkau.\n\n"
            "Pilih menu di bawah:"
        )
        
        keyboard_markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn_cari_judul = types.InlineKeyboardButton("🎬 CARI JUDUL", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
        btn_cari_cuan = types.InlineKeyboardButton("💰 CARI CUAN", web_app=types.WebAppInfo(url=URL_CARI_CUAN))
        btn_beli_vip = types.InlineKeyboardButton("💎 BELI VIP", web_app=types.WebAppInfo(url=URL_BELI_VIP))
        btn_req_drama = types.InlineKeyboardButton("📽 REQ DRAMA", web_app=types.WebAppInfo(url=URL_REQUEST))
        btn_hubungi_kami = types.InlineKeyboardButton("💬 HUBUNGI KAMI", web_app=types.WebAppInfo(url=URL_HUBUNGI_KAMI))
        btn_join_grup = types.InlineKeyboardButton("⭐ Join GRUP DRAMA MU OFFICIAL ⭐", url="https://t.me/dramamuofficial")
        
        keyboard_markup.add(btn_join_grup)
        keyboard_markup.add(btn_cari_judul, btn_cari_cuan)
        keyboard_markup.add(btn_beli_vip, btn_req_drama)
        keyboard_markup.add(btn_hubungi_kami)
        
        poster_path = 'backend_assets/poster.jpg'
        try:
            if os.path.exists(poster_path):
                with open(poster_path, 'rb') as photo:
                    bot.send_photo(
                        message.chat.id,
                        photo,
                        caption=welcome_text,
                        reply_markup=keyboard_markup,
                        parse_mode='HTML'
                    )
                logger.info(f"✅ Pesan welcome dengan poster dikirim ke user {message.from_user.id}")
            else:
                bot.send_message(
                    message.chat.id,
                    welcome_text,
                    reply_markup=keyboard_markup,
                    parse_mode='HTML'
                )
                logger.info(f"✅ Pesan welcome dikirim ke user {message.from_user.id} (tanpa poster)")
        except Exception as e:
            logger.error(f"❌ Error waktu kirim pesan welcome: {e}")
            bot.send_message(
                message.chat.id,
                welcome_text,
                reply_markup=keyboard_markup,
                parse_mode='HTML'
            )
            logger.info(f"✅ Pesan welcome fallback dikirim ke user {message.from_user.id}")

    @bot.message_handler(content_types=['web_app_data'])
    def handle_web_app_data(message):
        try:
            data = json.loads(message.web_app_data.data)
            logger.info(f"📥 Data diterima dari Mini App: {data}")
            
            action = data.get('action')
            user_id = message.from_user.id
            
            if action == 'watch':
                movie_id = data.get('movie_id')
                logger.info(f"User {user_id} mau nonton film ID: {movie_id}")
                
                movie = get_movie_by_id(movie_id)
                
                if not movie:
                    bot.send_message(message.chat.id, "Film tidak ditemukan.")
                    return
                
                if is_vip(user_id):
                    send_movie_to_vip(message.chat.id, movie)
                else:
                    send_non_vip_message(message.chat.id, movie)
                    
            elif action == 'request_drama':
                judul = data.get('judul')
                apk = data.get('apk')
                logger.info(f"User {user_id} minta drama: {judul} dari {apk}")
                
                response_text = (
                    f"Request kamu sudah diterima.\n\n"
                    f"<b>{escape_html(judul)}</b> ({escape_html(apk)})\n\n"
                    f"Tim kami akan cek secepatnya. Terima kasih!"
                )
                bot.send_message(message.chat.id, response_text, parse_mode='HTML')
                
            elif action == 'withdraw_referral':
                jumlah = data.get('jumlah')
                metode = data.get('metode')
                nomor_rekening = data.get('nomor_rekening')
                nama_pemilik = data.get('nama_pemilik')
                
                logger.info(f"User {user_id} mau tarik dana Rp {jumlah} via {metode}")
                
                response_text = (
                    f"<b>Request Penarikan Dana</b>\n\n"
                    f"Jumlah: Rp {jumlah}\n"
                    f"Metode: {metode}\n"
                    f"Rekening: {nomor_rekening}\n"
                    f"Nama: {nama_pemilik}\n\n"
                    f"Kami akan proses maksimal 1x24 jam. Terima kasih!"
                )
                bot.send_message(message.chat.id, response_text, parse_mode='HTML')
            else:
                bot.send_message(message.chat.id, "Data tidak valid.")
                
        except json.JSONDecodeError as e:
            logger.error(f"Error waktu parsing JSON: {e}")
            bot.send_message(message.chat.id, "Data tidak valid.")
        except Exception as e:
            logger.error(f"Error waktu handle data web app: {e}")
            bot.send_message(message.chat.id, "Terjadi kesalahan.")

    def send_movie_to_vip(chat_id, movie):
        logger.info(f"✅ Kirim film ke user VIP: {chat_id}")
        
        safe_title = escape_html(movie.get('title', 'Unknown'))
        safe_description = escape_html(movie.get('description', ''))
        
        caption = (
            f"🎬 <b>{safe_title}</b>\n\n"
            f"{safe_description}\n\n"
            f"Selamat menonton!"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_tonton = types.InlineKeyboardButton("▶️ Tonton Sekarang", url=movie['video_link'])
        btn_download = types.InlineKeyboardButton("📥 Download", url=movie['video_link'])
        btn_menu = types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
        
        markup.add(btn_tonton, btn_download)
        markup.add(btn_menu)
        
        poster_map = {
            'sample-1': 'backend_assets/posters/cincin-lepas.jpg',
            'sample-2': 'backend_assets/posters/tuan-su.jpg',
            'sample-3': 'backend_assets/posters/suami-dengar.jpg',
            'sample-4': 'backend_assets/posters/jodoh-sempurna.jpg'
        }
        
        try:
            movie_id = movie.get('id')
            poster_path = poster_map.get(movie_id)
            
            if poster_path and os.path.exists(poster_path):
                with open(poster_path, 'rb') as photo:
                    bot.send_photo(
                        chat_id,    
                        photo,    
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            else:
                logger.warning(f"Poster ga ketemu buat film {movie_id}, kirim pesan aja")
                bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            logger.error(f"❌ Error waktu kirim foto: {e}")
            try:
                bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
            except Exception as fallback_error:
                logger.error(f"❌ Fallback message gagal: {fallback_error}")

    def send_non_vip_message(chat_id, movie):
        logger.info(f"⚠️ User {chat_id} belum VIP, kirim pesan ajakan")
        
        safe_title = escape_html(movie.get('title', 'Unknown'))
        
        caption = (
            f"🔒 <b>{safe_title}</b>\n\n"
            f"Konten ini khusus untuk member VIP.\n\n"
            f"Kamu belum menjadi member VIP.\n"
            f"Upgrade ke VIP untuk menonton film ini."
        )
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        btn_join_vip = types.InlineKeyboardButton("⭐ Join VIP Sekarang", web_app=types.WebAppInfo(url=URL_BELI_VIP))
        btn_info_vip = types.InlineKeyboardButton("ℹ️ Info VIP", callback_data="info_vip")
        btn_pilih_film = types.InlineKeyboardButton("🎬 Pilih Film Lain", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
        
        markup.add(btn_join_vip)
        markup.add(btn_info_vip, btn_pilih_film)
        
        poster_map = {
            'sample-1': 'backend_assets/posters/cincin-lepas.jpg',
            'sample-2': 'backend_assets/posters/tuan-su.jpg',
            'sample-3': 'backend_assets/posters/suami-dengar.jpg',
            'sample-4': 'backend_assets/posters/jodoh-sempurna.jpg'
        }
        
        try:
            movie_id = movie.get('id')
            poster_path = poster_map.get(movie_id)
            
            if poster_path and os.path.exists(poster_path):
                with open(poster_path, 'rb') as photo:
                    bot.send_photo(
                        chat_id,
                        photo,
                        caption=caption,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            else:
                logger.warning(f"Poster ga ketemu buat film {movie_id}, kirim pesan aja")
                bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
        except Exception as e:
            logger.error(f"❌ Error waktu kirim foto: {e}")
            try:
                bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
            except Exception as fallback_error:
                logger.error(f"❌ Fallback message gagal: {fallback_error}")

    @bot.callback_query_handler(func=lambda call: call.data == "info_vip")
    def handle_info_vip_callback(call):
        logger.info(f"User {call.from_user.id} klik Info VIP")
        
        text = (
            "<b>Info VIP</b>\n\n"
            "Keuntungan menjadi member VIP:\n"
            "• Nonton semua film tanpa batas\n"
            "• Kualitas HD\n"
            "• Download sepuasnya\n"
            "• Tanpa iklan\n"
            "• Akses film baru lebih dulu\n\n"
            "Harga mulai dari Rp 2.000/hari\n"
        )
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        btn_join_vip = types.InlineKeyboardButton("⭐ Join VIP", web_app=types.WebAppInfo(url=URL_BELI_VIP))
        btn_menu = types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
        
        markup.add(btn_join_vip)
        markup.add(btn_menu)
        
        bot.edit_message_text(
            text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode='HTML',
            reply_markup=markup
        )
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda call: call.data == "menu_utama")
    def handle_menu_utama_callback(call):
        logger.info(f"User {call.from_user.id} balik ke menu utama")
        
        welcome_text = (
            "🎬 <b>Selamat datang di Dramamu Bot!</b>\n\n"
            "Nonton drama favorit kamu dengan harga terjangkau.\n\n"
            "⭐ Join <a href='https://t.me/dramamuofficial'>GRUP DRAMA MU OFFICIAL</a> ⭐\n\n"
            "Pilih menu di bawah:"
        )
        
        keyboard_markup = types.InlineKeyboardMarkup(row_width=2)
        
        btn_cari_judul = types.InlineKeyboardButton("🎬 CARI JUDUL", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
        btn_cari_cuan = types.InlineKeyboardButton("💰 CARI CUAN", web_app=types.WebAppInfo(url=URL_CARI_CUAN))
        btn_beli_vip = types.InlineKeyboardButton("💎 BELI VIP", web_app=types.WebAppInfo(url=URL_BELI_VIP))
        btn_req_drama = types.InlineKeyboardButton("📽 REQ DRAMA", web_app=types.WebAppInfo(url=URL_REQUEST))
        btn_hubungi_kami = types.InlineKeyboardButton("💬 HUBUNGI KAMI", web_app=types.WebAppInfo(url=URL_HUBUNGI_KAMI))
        
        keyboard_markup.add(btn_cari_judul, btn_cari_cuan)
        keyboard_markup.add(btn_beli_vip, btn_req_drama)
        keyboard_markup.add(btn_hubungi_kami)
        
        bot.edit_message_text(
            welcome_text,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode='HTML',
            reply_markup=keyboard_markup
        )
        bot.answer_callback_query(call.id)

    @bot.message_handler(commands=['addvip'])
    def add_vip_command(message):
        user = get_or_create_user(message.from_user)
        if not user:
            bot.reply_to(message, "Gagal memproses user.")
            return

        db = SessionLocal()
        try:
            telegram_id = str(message.from_user.id)
            db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
            
            if db_user:
                db_user.is_vip = True  # type: ignore
                db_user.vip_expires_at = now_utc() + timedelta(days=30)  # type: ignore
                db.commit()
                
                logger.info(f"✅ User {telegram_id} ditambahin ke VIP (30 hari)")
                bot.reply_to(message, "Kamu sekarang sudah menjadi member VIP selama 30 hari!")
            else:
                bot.reply_to(message, "User tidak ditemukan.")
        except Exception as e:
            logger.error(f"❌ Error di addvip: {e}")
            db.rollback()
            bot.reply_to(message, "Terjadi kesalahan saat update VIP.")
        finally:
            db.close()

    @bot.message_handler(commands=['removevip'])
    def remove_vip_command(message):
        db = SessionLocal()
        try:
            telegram_id = str(message.from_user.id)
            db_user = db.query(User).filter(User.telegram_id == telegram_id).first()
            
            if db_user:
                db_user.is_vip = False  # type: ignore
                db_user.vip_expires_at = None  # type: ignore
                db.commit()
                
                logger.info(f"VIP status {telegram_id} udah dihapus")
                bot.reply_to(message, "Status VIP kamu telah dihapus.")
            else:
                bot.reply_to(message, "User tidak ditemukan.")
        except Exception as e:
            logger.error(f"❌ Error di removevip: {e}")
            db.rollback()
            bot.reply_to(message, "Terjadi kesalahan saat menghapus VIP.")
        finally:
            db.close()

    @bot.message_handler(commands=['checkvip'])
    def check_vip_command(message):
        user_id = message.from_user.id
        get_or_create_user(message.from_user) 
        
        if is_vip(user_id):
            bot.reply_to(message, "Kamu sudah menjadi member VIP.")
        else:
            bot.reply_to(message, "Kamu belum menjadi member VIP.")

    @bot.message_handler(func=lambda message: True)
    def log_other_messages(message):
        logger.info(f"📨 Pesan lain dari {message.from_user.id}: {message.text if message.text else 'non-text'}")

def run_bot():
    import time
    
    if not TELEGRAM_BOT_TOKEN or bot is None:
        logger.error("❌ TELEGRAM_BOT_TOKEN ga dikonfigurasi - bot ga bisa jalan")
        logger.error("Set TELEGRAM_BOT_TOKEN di environment variables buat aktifin Telegram bot")
        return
    
    assert bot is not None
    
    logger.info("🤖 Bot dimulai...")
    logger.info(f"📱 BASE URL: {BASE_URL}")
    logger.info(f"🎬 Cari Judul: {URL_CARI_JUDUL}")
    logger.info("✅ Bot siap menerima perintah.")
    
    try:
        bot.remove_webhook()
        logger.info("✅ Webhook yang ada udah dihapus")
    except Exception as e:
        logger.warning(f"Ga bisa hapus webhook: {e}")
    
    retry_count = 0
    max_retries = 2
    
    while retry_count < max_retries:
        try:
            me = bot.get_me()
            logger.info(f"✅ Tes koneksi bot berhasil - @{me.username} (ID: {me.id})")
            logger.info("🔄 Mulai polling loop...")
            
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True, allowed_updates=['message', 'callback_query'])
            break
        except ApiTelegramException as e:
            if "409" in str(e) or "Conflict" in str(e):
                logger.error("=" * 80)
                logger.error("❌ CONFLICT ERROR 409: Ada instance bot lain yang masih aktif!")
                logger.error("=" * 80)
                logger.error("📌 SOLUSI:")
                logger.error("   1. Stop semua instance bot yang lain")
                logger.error("   2. Tunggu 2-3 menit")
                logger.error("   3. Coba deploy ulang")
                logger.error("")
                logger.error(f"Detail error: {e}")
                logger.error("=" * 80)
                # Langsung exit tanpa retry
                return
            else:
                logger.error(f"❌ Error API Telegram: {e}")
                raise
        except KeyboardInterrupt:
            logger.info("⚠️ Bot stopped by user")
            bot.stop_polling()
            break
        except Exception as e:
            logger.error(f"❌ Error ga terduga: {e}")
            retry_count += 1
            if retry_count >= max_retries:
                raise
            time.sleep(30)

if __name__ == '__main__':
    run_bot()
