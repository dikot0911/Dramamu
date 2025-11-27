import json
import logging
import requests
import random
import string
import os
import time
import threading
from datetime import datetime, timedelta
from typing import cast
from telebot import TeleBot, types
from telebot.apihelper import ApiTelegramException
from database import (
    SessionLocal, User, Movie, Part, PendingUpload,
    check_and_update_vip_expiry,
    get_movie_by_id, get_movie_by_short_id, get_parts_by_movie_id, get_part,
    create_part, create_pending_upload, get_pending_upload,
    update_pending_upload_status, increment_part_views,
    create_conversation, get_conversation, update_conversation, delete_conversation,
    get_unique_short_id
)
from config import (
    TELEGRAM_BOT_TOKEN,
    BASE_URL,
    URL_CARI_JUDUL,
    URL_CARI_CUAN,
    URL_BELI_VIP,
    URL_REQUEST,
    URL_HUBUNGI_KAMI,
    TELEGRAM_STORAGE_CHAT_ID,
    TELEGRAM_ADMIN_IDS,
    now_utc
)
import telegram_delivery
from bot_state import bot_state

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
    """
    Generate referral code dengan format yang lebih aman dari collision.
    
    BUG FIX #5: Increased random part dari 4 ke 8 characters untuk prevent collision.
    
    Format: first 5 digits of telegram_id + 8 random alphanumeric chars
    Total combinations: 62^8 = 218 trillion (vs previous 62^4 = 14 million)
    
    Collision probability dengan 1 juta users: ~0.000002% (practically zero)
    
    Args:
        telegram_id: User's Telegram ID
        
    Returns:
        13-character referral code (5 digits + 8 random chars)
    """
    logger.info(f"Bikin ref_code buat {telegram_id}...")
    first_five = str(telegram_id)[:5]
    # BUG FIX #5: Increased from k=4 to k=8 for 15,000x more combinations
    rand_part = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    ref_code = f"{first_five}{rand_part}"
    logger.info(f"Ref_code jadi: {ref_code}")
    return ref_code

def get_or_create_user(user, referred_by_code=None):
    """
    Get or create user dengan simple, reliable approach.
    
    Args:
        user: Telegram user object dengan .id dan .username attributes
        referred_by_code: Optional referral code dari user lain
        
    Returns:
        User object (never None)
        
    Raises:
        RuntimeError: Jika gagal create user setelah max retries
    """
    from sqlalchemy.exc import IntegrityError
    
    telegram_id = str(user.id)
    username = user.username if user.username else None
    
    MAX_RETRIES = 3
    
    for attempt in range(MAX_RETRIES):
        db = SessionLocal()
        try:
            # BUG FIX #8: Check for existing non-deleted user
            existing_user = db.query(User).filter(
                User.telegram_id == telegram_id,
                User.deleted_at == None  # Exclude soft-deleted users
            ).first()
            if existing_user:
                logger.info(f"User {telegram_id} udah ada di DB")
                return existing_user
            
            # Bikin user baru
            ref_code = generate_ref_code(telegram_id)
            new_user = User(
                telegram_id=telegram_id,
                username=username,
                ref_code=ref_code,
                referred_by_code=referred_by_code,
                is_vip=False,
                commission_balance=0,
                total_referrals=0
            )
            
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            
            logger.info(f"✅ User {telegram_id} dibuat dengan ref_code: {ref_code}")
            
            # Update referrer kalau ada
            if referred_by_code and referred_by_code != ref_code:
                # BUG FIX #8: Only count non-deleted referrers
                referrer = db.query(User).filter(
                    User.ref_code == referred_by_code,
                    User.deleted_at == None  # Exclude soft-deleted users
                ).first()
                if referrer:
                    referrer.total_referrals += 1  # type: ignore
                    db.commit()
                    logger.info(f"✅ Referrer {referred_by_code} total_referrals incremented")
                else:
                    logger.warning(f"⚠️ Referrer dengan code {referred_by_code} tidak ditemukan")
            elif referred_by_code == ref_code:
                logger.warning(f"⚠️ Self-referral detected for {telegram_id}, ignoring")
            
            return new_user
        
        except IntegrityError as ie:
            db.rollback()
            # BUG FIX #8: Check for non-deleted user created by concurrent request
            existing_user = db.query(User).filter(
                User.telegram_id == telegram_id,
                User.deleted_at == None  # Exclude soft-deleted users
            ).first()
            if existing_user:
                logger.info(f"User {telegram_id} dibuat di concurrent request, gunakan yang existing")
                return existing_user
            
            # Kalau bukan duplicate telegram_id, mungkin ref_code collision
            if 'ref_code' in str(ie):
                logger.warning(f"ref_code collision on attempt {attempt + 1}/{MAX_RETRIES}, retrying...")
                if attempt < MAX_RETRIES - 1:
                    continue
                else:
                    raise RuntimeError(f"Failed to create user {telegram_id}: {ie}")
            else:
                raise RuntimeError(f"Failed to create user {telegram_id}: {ie}")
        
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Error di get_or_create_user attempt {attempt + 1}/{MAX_RETRIES}: {e}")
            logger.exception("Full error traceback:")
            if attempt == MAX_RETRIES - 1:
                raise RuntimeError(f"Failed to create user {telegram_id} after {MAX_RETRIES} attempts: {e}")
            continue
        
        finally:
            db.close()
    
    error_msg = f"Failed to create user {telegram_id} after {MAX_RETRIES} attempts"
    logger.error(f"❌ {error_msg}")
    raise RuntimeError(error_msg)

def is_vip(user_id):
    db = SessionLocal()
    try:
        user_id_str = str(user_id)
        # BUG FIX #8: Exclude soft-deleted users from VIP check
        db_user = db.query(User).filter(
            User.telegram_id == user_id_str,
            User.deleted_at == None  # Exclude soft-deleted users
        ).first()
        
        if not db_user:
            logger.warning(f"Cek VIP: User {user_id_str} gak ada")
            return False
        
        return check_and_update_vip_expiry(db_user, db)
        
    except Exception as e:
        logger.error(f"❌ Error di is_vip: {e}")
        return False
    finally:
        db.close()

def send_welcome_message(bot_instance, chat_id):
    """
    Kirim pesan welcome dengan poster dan tombol menu.
    Fungsi ini dipakai oleh /start dan menu_utama callback.
    
    Args:
        bot_instance: TeleBot instance
        chat_id: Telegram chat ID
    """
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
                bot_instance.send_photo(
                    chat_id,
                    photo,
                    caption=welcome_text,
                    reply_markup=keyboard_markup,
                    parse_mode='HTML'
                )
            logger.info(f"✅ Pesan welcome dengan poster dikirim ke {chat_id}")
        else:
            bot_instance.send_message(
                chat_id,
                welcome_text,
                reply_markup=keyboard_markup,
                parse_mode='HTML'
            )
            logger.info(f"✅ Pesan welcome dikirim ke {chat_id} (tanpa poster)")
    except Exception as e:
        logger.error(f"❌ Error waktu kirim pesan welcome: {e}")
        bot_instance.send_message(
            chat_id,
            welcome_text,
            reply_markup=keyboard_markup,
            parse_mode='HTML'
        )
        logger.info(f"✅ Pesan welcome fallback dikirim ke {chat_id}")

if bot is not None:
    @bot.message_handler(commands=['start'])
    def send_welcome(message):
        logger.info(f"User {message.from_user.id} mulai pake bot di chat type: {message.chat.type}")
        
        is_group = message.chat.type in ['group', 'supergroup']
        
        if is_group:
            welcome_text = (
                "🎬 <b>Halo! Ini adalah Dramamu Bot.</b>\n\n"
                "Bot ini untuk nonton drama favorit kamu.\n\n"
                "💡 <b>Cara pakai:</b>\n"
                "Silakan chat bot secara <b>private</b> untuk akses semua fitur!\n\n"
                "Klik tombol di bawah untuk mulai:"
            )
            
            keyboard_markup = types.InlineKeyboardMarkup()
            btn_start_private = types.InlineKeyboardButton(
                "💬 Chat Bot Secara Private", 
                url=f"https://t.me/{bot.get_me().username}"
            )
            btn_join_grup = types.InlineKeyboardButton(
                "⭐ Join GRUP DRAMA MU OFFICIAL ⭐", 
                url="https://t.me/dramamuofficial"
            )
            keyboard_markup.add(btn_start_private)
            keyboard_markup.add(btn_join_grup)
            
            bot.send_message(
                message.chat.id,
                welcome_text,
                reply_markup=keyboard_markup,
                parse_mode='HTML'
            )
            logger.info(f"✅ Pesan welcome untuk grup dikirim")
            return
        
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
            logger.error(f"❌ Error di /start waktu get_or_create_user: {e}")
            logger.exception("Full traceback:")
            bot.send_message(message.chat.id, "Bot sedang mengalami gangguan. Tunggu sebentar ya.")
            return
        
        send_welcome_message(bot, message.chat.id)

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
                    telegram_delivery.send_movie_to_vip(bot, message.chat.id, movie)
                else:
                    telegram_delivery.send_non_vip_message(bot, message.chat.id, movie)
                    
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
    
    @bot.message_handler(content_types=['video'])
    def handle_video_upload(message):
        if not TELEGRAM_STORAGE_CHAT_ID:
            return
        
        # Normalize chat ID comparison (handle both positive and negative IDs)
        if abs(message.chat.id) != abs(TELEGRAM_STORAGE_CHAT_ID):
            return
        
        uploader_id = message.from_user.id
        if TELEGRAM_ADMIN_IDS and uploader_id not in TELEGRAM_ADMIN_IDS:
            logger.warning(f"⚠️ Non-admin {uploader_id} mencoba upload video di storage group")
            bot.reply_to(message, "❌ Hanya admin yang bisa upload video.")
            return
        
        logger.info(f"📹 Video baru terdeteksi dari admin {uploader_id} di storage group")
        
        try:
            existing = get_pending_upload(str(message.message_id))
            if existing:
                logger.info(f"Video message {message.message_id} sudah diproses sebelumnya")
                bot.reply_to(message, "ℹ️ Video ini sudah diproses sebelumnya.")
                return
            
            video = message.video
            file_id = video.file_id
            duration = video.duration if video.duration else None
            file_size = video.file_size if video.file_size else None
            
            thumbnail_file_id = None
            if video.thumb:
                thumbnail_file_id = video.thumb.file_id
            
            pending_upload = create_pending_upload(
                telegram_file_id=file_id,
                telegram_chat_id=str(message.chat.id),
                telegram_message_id=str(message.message_id),
                uploader_id=str(uploader_id),
                duration=duration,
                file_size=file_size,
                thumbnail_url=thumbnail_file_id
            )
            
            if not pending_upload:
                bot.reply_to(message, "❌ Gagal menyimpan video. Coba lagi.")
                return
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton(
                    "🎬 Film Baru",
                    callback_data=f"new_movie_{message.message_id}"
                ),
                types.InlineKeyboardButton(
                    "📺 Part Baru",
                    callback_data=f"new_part_{message.message_id}"
                )
            )
            
            duration_text = f"• Duration: {duration}s\n" if duration else ""
            size_text = f"• Size: {file_size / 1024 / 1024:.2f} MB\n" if file_size else ""
            
            info_text = (
                f"✅ <b>Video terdeteksi!</b>\n\n"
                f"🔑 <b>File ID:</b>\n"
                f"<code>{file_id}</code>\n\n"
                f"📊 <b>Info:</b>\n"
                f"{duration_text}"
                f"{size_text}"
                f"\n"
                f"💡 <b>Cara pakai:</b>\n"
                f"1. Copy file_id di atas (tap untuk copy)\n"
                f"2. Buka Admin Panel\n"
                f"3. Paste di field 'Telegram File ID'\n\n"
                f"<b>Atau klik button di bawah untuk panduan detail:</b>"
            )
            
            bot.reply_to(message, info_text, reply_markup=markup, parse_mode='HTML')
            logger.info(f"✅ Pending upload created dengan file_id: {file_id}")
            
        except Exception as e:
            logger.error(f"❌ Error handle video upload: {e}")
            bot.reply_to(message, f"❌ Error: {str(e)}")
    
    @bot.message_handler(content_types=['photo'])
    def handle_photo_upload(message):
        """
        Handler untuk upload poster (photo).
        - Jika upload di grup storage: simpan otomatis ke pending_uploads
        - Jika upload di tempat lain: kirim File ID untuk copy manual
        """
        uploader_id = message.from_user.id
        
        # Cek apakah yang upload adalah admin
        if TELEGRAM_ADMIN_IDS and uploader_id not in TELEGRAM_ADMIN_IDS:
            # Silent ignore untuk non-admin, biar ga spam
            logger.info(f"ℹ️ Non-admin {uploader_id} kirim foto, diabaikan")
            return
        
        # Ambil photo dengan quality terbaik (index terakhir = largest)
        photo = message.photo[-1]
        file_id = photo.file_id
        file_size = photo.file_size if photo.file_size else None
        width = photo.width if photo.width else None
        height = photo.height if photo.height else None
        
        # CEK APAKAH INI DI GRUP STORAGE (otomatis) atau bukan (manual)
        # Normalize chat ID comparison (handle both positive and negative IDs)
        if TELEGRAM_STORAGE_CHAT_ID and abs(message.chat.id) == abs(TELEGRAM_STORAGE_CHAT_ID):
            # MODE OTOMATIS: Upload di grup storage
            logger.info(f"🖼 Poster baru terdeteksi dari admin {uploader_id} di storage group")
            
            try:
                existing = get_pending_upload(str(message.message_id))
                if existing:
                    logger.info(f"Poster message {message.message_id} sudah diproses sebelumnya")
                    bot.reply_to(message, "ℹ️ Poster ini sudah diproses sebelumnya.")
                    return
                
                # Simpan ke pending_uploads dengan content_type='poster'
                pending_upload = create_pending_upload(
                    telegram_file_id=file_id,
                    telegram_chat_id=str(message.chat.id),
                    telegram_message_id=str(message.message_id),
                    uploader_id=str(uploader_id),
                    content_type='poster',
                    file_size=file_size,
                    poster_width=width,
                    poster_height=height
                )
                
                if not pending_upload:
                    bot.reply_to(message, "❌ Gagal menyimpan poster. Coba lagi.")
                    return
                
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton(
                        "🖼️ Poster untuk Film Existing",
                        callback_data=f"poster_existing_{message.message_id}"
                    )
                )
                markup.add(types.InlineKeyboardButton("❌ Batal", callback_data="cancel_conv"))
                
                size_text = f"• Size: {file_size / 1024:.2f} KB\n" if file_size else ""
                dimension_text = f"• Dimension: {width}x{height}px\n" if width and height else ""
                
                info_text = (
                    f"✅ <b>Poster terdeteksi!</b>\n\n"
                    f"🖼 <b>File ID:</b>\n"
                    f"<code>{file_id}</code>\n\n"
                    f"📊 <b>Info:</b>\n"
                    f"{dimension_text}"
                    f"{size_text}"
                    f"\n"
                    f"💡 <b>Pilih opsi di bawah:</b>"
                )
                
                bot.reply_to(message, info_text, reply_markup=markup, parse_mode='HTML')
                logger.info(f"✅ Pending poster upload created dengan file_id: {file_id}")
                
            except Exception as e:
                logger.error(f"❌ Error handle poster upload di storage group: {e}")
                bot.reply_to(message, f"❌ Error: {str(e)}")
        
        else:
            # MODE MANUAL: Upload di chat biasa (fallback untuk backward compatibility)
            logger.info(f"🖼 Poster (photo) baru terdeteksi dari admin {uploader_id}")
            
            try:
                # Markup dengan inline button untuk copy
                markup = types.InlineKeyboardMarkup(row_width=1)
                markup.add(
                    types.InlineKeyboardButton(
                        "📋 Copy File ID",
                        callback_data=f"copy_poster_{message.message_id}"
                    )
                )
                
                # Format info text
                size_text = f"• Size: {file_size / 1024:.2f} KB\n" if file_size else ""
                dimension_text = f"• Dimension: {width}x{height}px\n" if width and height else ""
                
                info_text = (
                    f"✅ <b>Poster terdeteksi!</b>\n\n"
                    f"🖼 <b>File ID Poster:</b>\n"
                    f"<code>{file_id}</code>\n\n"
                    f"📊 <b>Info:</b>\n"
                    f"{dimension_text}"
                    f"{size_text}"
                    f"\n"
                    f"💡 <b>Cara pakai:</b>\n"
                    f"1. Copy file_id di atas (tap untuk copy)\n"
                    f"2. Buka Admin Panel\n"
                    f"3. Saat tambah/edit movie, paste di field 'Telegram File ID Poster'\n\n"
                    f"ℹ️ Poster ini akan otomatis muncul di:\n"
                    f"• Pesan bot ke user\n"
                    f"• Mini App (drama list, favorit)\n"
                    f"• Semua tempat yang butuh poster film"
                )
                
                bot.reply_to(message, info_text, reply_markup=markup, parse_mode='HTML')
                logger.info(f"✅ Poster File ID dikirim: {file_id}")
                
            except Exception as e:
                logger.error(f"❌ Error handle photo upload: {e}")
                bot.reply_to(message, f"❌ Error: {str(e)}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('copy_poster_'))
    def handle_copy_poster_callback(call):
        """
        Handler untuk button Copy File ID poster.
        """
        logger.info(f"📋 Admin {call.from_user.id} klik Copy Poster File ID")
        
        try:
            # Ambil file_id dari original message
            if call.message.reply_to_message and call.message.reply_to_message.photo:
                photo = call.message.reply_to_message.photo[-1]
                file_id = photo.file_id
                
                # Answer callback query dengan file_id
                bot.answer_callback_query(
                    call.id,
                    f"File ID copied! Paste ke Admin Panel.",
                    show_alert=False
                )
                
                # Edit message untuk kasih konfirmasi
                bot.edit_message_text(
                    f"✅ <b>File ID Poster:</b>\n"
                    f"<code>{file_id}</code>\n\n"
                    f"📋 Silakan copy file_id di atas dan paste ke Admin Panel saat tambah/edit movie.\n\n"
                    f"💡 Gunakan field <b>'Telegram File ID Poster'</b> di form movie.",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='HTML'
                )
            else:
                bot.answer_callback_query(call.id, "Photo tidak ditemukan", show_alert=True)
                
        except Exception as e:
            logger.error(f"❌ Error handle copy poster callback: {e}")
            bot.answer_callback_query(call.id, f"Error: {str(e)}", show_alert=True)
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('new_movie_'))
    def handle_new_movie_callback(call):
        logger.info(f"🎬 Admin {call.from_user.id} pilih Film Baru: {call.data}")
        
        try:
            message_id = call.data.split('_')[2]
            
            pending = get_pending_upload(message_id)
            if not pending:
                bot.answer_callback_query(call.id, "Pending upload tidak ditemukan")
                return
            
            admin_id = call.from_user.id
            create_conversation(admin_id, 'new_movie', 'select_category', {
                'message_id': message_id,
                'telegram_file_id': pending.get('telegram_file_id'),
                'duration': pending.get('duration'),
                'file_size': pending.get('file_size'),
                'thumbnail_url': pending.get('thumbnail_url')
            })
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            categories = ['Romance', 'Action', 'Comedy', 'Drama', 'Fantasy', 'Horror', 'Thriller']
            for cat in categories:
                markup.add(types.InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
            markup.add(types.InlineKeyboardButton("❌ Batal", callback_data="cancel_conv"))
            
            bot.edit_message_text(
                f"✅ <b>Video tersimpan!</b>\n\n"
                f"📁 Pilih kategori untuk film baru:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=markup
            )
            
            bot.answer_callback_query(call.id, "Pilih kategori film")
            
        except Exception as e:
            logger.error(f"❌ Error handle new movie: {e}")
            bot.answer_callback_query(call.id, f"Error: {str(e)}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('new_part_'))
    def handle_new_part_callback(call):
        logger.info(f"📺 Admin {call.from_user.id} pilih Part Baru: {call.data}")
        
        try:
            message_id = call.data.split('_')[2]
            
            pending = get_pending_upload(message_id)
            if not pending:
                bot.answer_callback_query(call.id, "Pending upload tidak ditemukan")
                return
            
            admin_id = call.from_user.id
            
            db = SessionLocal()
            try:
                movies = db.query(Movie).filter(
                    Movie.deleted_at == None
                ).order_by(Movie.created_at.desc()).limit(20).all()
                
                if not movies:
                    bot.answer_callback_query(call.id, "Belum ada film. Buat film baru dulu!", show_alert=True)
                    return
                
                create_conversation(admin_id, 'new_part', 'select_movie', {
                    'message_id': message_id,
                    'telegram_file_id': pending.get('telegram_file_id'),
                    'duration': pending.get('duration'),
                    'file_size': pending.get('file_size'),
                    'thumbnail_url': pending.get('thumbnail_url')
                })
                
                markup = types.InlineKeyboardMarkup(row_width=1)
                for movie in movies:
                    title = movie.title[:40] + '...' if len(movie.title) > 40 else movie.title  # type: ignore[arg-type]
                    markup.add(types.InlineKeyboardButton(
                        f"🎬 {title}", 
                        callback_data=f"movie_{movie.id}"
                    ))
                markup.add(types.InlineKeyboardButton("❌ Batal", callback_data="cancel_conv"))
                
                bot.edit_message_text(
                    f"✅ <b>Video tersimpan!</b>\n\n"
                    f"🎬 Pilih film untuk ditambah partnya:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                
                bot.answer_callback_query(call.id, "Pilih film")
                
            finally:
                db.close()
            
        except Exception as e:
            logger.error(f"❌ Error handle new part: {e}")
            bot.answer_callback_query(call.id, f"Error: {str(e)}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('poster_existing_'))
    def handle_poster_existing_callback(call):
        logger.info(f"🖼️ Admin {call.from_user.id} pilih Poster untuk Film Existing: {call.data}")
        
        if TELEGRAM_ADMIN_IDS and call.from_user.id not in TELEGRAM_ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized. Admin only.", show_alert=True)
            logger.warning(f"⚠️ Non-admin {call.from_user.id} mencoba assign poster - BLOCKED")
            return
        
        try:
            message_id = call.data.split('_')[2]
            
            pending = get_pending_upload(message_id)
            if not pending:
                bot.answer_callback_query(call.id, "Pending upload tidak ditemukan")
                return
            
            admin_id = call.from_user.id
            
            create_conversation(admin_id, 'assign_poster', 'input_movie_code', {
                'message_id': message_id,
                'poster_file_id': pending.get('telegram_file_id')
            })
            
            markup = types.InlineKeyboardMarkup(row_width=1)
            markup.add(types.InlineKeyboardButton("❌ Batal", callback_data="cancel_conv"))
            
            bot.edit_message_text(
                f"✅ <b>Poster tersimpan!</b>\n\n"
                f"🔑 Sekarang kirim <b>kode film</b> untuk assign poster ini:\n\n"
                f"💡 <b>Format kode yang diterima:</b>\n"
                f"• Movie ID: <code>movie-12345</code>\n"
                f"• Short ID: <code>SHORT-ABC</code>\n\n"
                f"📝 Kirim kode film sekarang:",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML',
                reply_markup=markup
            )
            
            bot.answer_callback_query(call.id, "Kirim kode film")
            
        except Exception as e:
            logger.error(f"❌ Error handle poster existing: {e}")
            bot.answer_callback_query(call.id, f"Error: {str(e)}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
    def handle_category_selection(call):
        """Handler untuk pilihan kategori film"""
        admin_id = call.from_user.id
        conv = get_conversation(admin_id)
        
        if not conv or conv['conversation_type'] != 'new_movie':
            bot.answer_callback_query(call.id, "Session expired, coba lagi", show_alert=True)
            return
        
        try:
            category = call.data.split('_')[1]
            update_conversation(admin_id, 'input_title', {'category': category})
            
            bot.edit_message_text(
                f"📁 Kategori: <b>{category}</b>\n\n"
                f"✏️ Sekarang kirim <b>judul film</b> (ketik di chat):",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML'
            )
            
            bot.answer_callback_query(call.id, f"Kategori: {category}")
            
        except Exception as e:
            logger.error(f"❌ Error handle category: {e}")
            bot.answer_callback_query(call.id, "Error", show_alert=True)
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('movie_'))
    def handle_movie_selection(call):
        """Handler untuk pilihan film (untuk new part)"""
        admin_id = call.from_user.id
        conv = get_conversation(admin_id)
        
        if not conv or conv['conversation_type'] != 'new_part':
            bot.answer_callback_query(call.id, "Session expired, coba lagi", show_alert=True)
            return
        
        try:
            movie_id = call.data.replace('movie_', '')
            movie = get_movie_by_id(movie_id)
            
            if not movie:
                bot.answer_callback_query(call.id, "Film tidak ditemukan", show_alert=True)
                return
            
            update_conversation(admin_id, 'input_part_number', {
                'movie_id': movie_id,
                'movie_title': movie.get('title')
            })
            
            safe_title = escape_html(movie.get('title'))
            bot.edit_message_text(
                f"🎬 Film: <b>{safe_title}</b>\n\n"
                f"🔢 Sekarang kirim <b>part number</b> (contoh: 1, 2, 3, dst):",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                parse_mode='HTML'
            )
            
            bot.answer_callback_query(call.id, f"Film dipilih")
            
        except Exception as e:
            logger.error(f"❌ Error handle movie selection: {e}")
            bot.answer_callback_query(call.id, "Error", show_alert=True)
    
    @bot.callback_query_handler(func=lambda call: call.data == 'cancel_conv')
    def handle_cancel_conversation(call):
        """Handler untuk cancel conversation"""
        admin_id = call.from_user.id
        delete_conversation(admin_id)
        
        bot.edit_message_text(
            "❌ <b>Dibatalkan</b>\n\n"
            "Upload video lagi untuk memulai dari awal.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode='HTML'
        )
        
        bot.answer_callback_query(call.id, "Dibatalkan")
    
    @bot.message_handler(func=lambda msg: TELEGRAM_STORAGE_CHAT_ID and abs(msg.chat.id) == abs(TELEGRAM_STORAGE_CHAT_ID) and msg.text and not msg.text.startswith('/'))
    def handle_admin_text_input(message):
        """Handler untuk text input dari admin di storage group"""
        admin_id = message.from_user.id
        
        if TELEGRAM_ADMIN_IDS and admin_id not in TELEGRAM_ADMIN_IDS:
            return
        
        conv = get_conversation(admin_id)
        if not conv:
            return
        
        try:
            text = message.text.strip()
            conv_type = conv['conversation_type']
            step = conv['step']
            data = conv['data']
            
            if conv_type == 'new_movie':
                if step == 'input_title':
                    update_conversation(admin_id, 'input_description', {'title': text})
                    bot.send_message(
                        message.chat.id,
                        f"✏️ Judul: <b>{escape_html(text)}</b>\n\n"
                        f"📝 Sekarang kirim <b>deskripsi film</b>:",
                        parse_mode='HTML'
                    )
                
                elif step == 'input_description':
                    description = text
                    title = data.get('title')
                    category = data.get('category')
                    telegram_file_id = data.get('telegram_file_id')
                    duration = data.get('duration')
                    file_size = data.get('file_size')
                    
                    db = SessionLocal()
                    try:
                        movie_id = f"movie-{random.randint(10000, 99999)}"
                        short_id = get_unique_short_id()
                        
                        new_movie = Movie(
                            id=movie_id,
                            short_id=short_id,
                            title=title,
                            description=description,
                            category=category,
                            telegram_file_id=telegram_file_id,
                            is_series=False,
                            total_parts=0
                        )
                        db.add(new_movie)
                        db.commit()
                        
                        update_pending_upload_status(data.get('message_id'), 'used')
                        delete_conversation(admin_id)
                        
                        bot.send_message(
                            message.chat.id,
                            f"✅ <b>Film berhasil dibuat!</b>\n\n"
                            f"🎬 Judul: <b>{escape_html(title)}</b>\n"
                            f"📁 Kategori: {category}\n"
                            f"🆔 ID: <code>{movie_id}</code>\n\n"
                            f"Film sudah bisa ditonton user!",
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Film baru dibuat: {movie_id} - {title}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error creating movie: {e}")
                        db.rollback()
                        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
                    finally:
                        db.close()
            
            elif conv_type == 'new_part':
                if step == 'input_part_number':
                    try:
                        part_number = int(text)
                        update_conversation(admin_id, 'input_part_title', {'part_number': part_number})
                        bot.send_message(
                            message.chat.id,
                            f"🔢 Part: <b>{part_number}</b>\n\n"
                            f"✏️ Sekarang kirim <b>judul part</b> (contoh: Episode 1, Part 1, dll):",
                            parse_mode='HTML'
                        )
                    except ValueError:
                        bot.send_message(message.chat.id, "❌ Part number harus angka. Coba lagi:")
                
                elif step == 'input_part_title':
                    part_title = text
                    movie_id = data.get('movie_id')
                    movie_title = data.get('movie_title')
                    part_number = data.get('part_number')
                    telegram_file_id = data.get('telegram_file_id')
                    duration = data.get('duration')
                    file_size = data.get('file_size')
                    
                    db = SessionLocal()
                    try:
                        # BUG FIX #8: Exclude soft-deleted movies
                        movie = db.query(Movie).filter(
                            Movie.id == movie_id,
                            Movie.deleted_at == None
                        ).first()
                        if not movie:
                            bot.send_message(message.chat.id, "❌ Film tidak ditemukan")
                            delete_conversation(admin_id)
                            return
                        
                        new_part = Part(
                            movie_id=movie_id,
                            part_number=part_number,
                            title=part_title,
                            telegram_file_id=telegram_file_id,
                            duration=duration,
                            file_size=file_size
                        )
                        db.add(new_part)
                        
                        if not movie.is_series:  # type: ignore[truthy-bool]
                            movie.is_series = True  # type: ignore[assignment]
                        
                        current_max = db.query(Part).filter(Part.movie_id == movie_id).count()
                        movie.total_parts = max(movie.total_parts, current_max + 1)  # type: ignore[arg-type,assignment]
                        
                        db.commit()
                        
                        update_pending_upload_status(data.get('message_id'), 'used')
                        delete_conversation(admin_id)
                        
                        bot.send_message(
                            message.chat.id,
                            f"✅ <b>Part berhasil ditambahkan!</b>\n\n"
                            f"🎬 Film: <b>{escape_html(movie_title)}</b>\n"
                            f"📺 Part {part_number}: <b>{escape_html(part_title)}</b>\n"
                            f"📊 Total parts: {movie.total_parts}\n\n"
                            f"Part sudah bisa ditonton user!",
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Part baru ditambahkan: {movie_id} - Part {part_number}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error creating part: {e}")
                        db.rollback()
                        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
                    finally:
                        db.close()
            
            elif conv_type == 'assign_poster':
                if step == 'input_movie_code':
                    movie_code = text
                    poster_file_id = data.get('poster_file_id')
                    message_id = data.get('message_id')
                    
                    db = SessionLocal()
                    try:
                        movie = db.query(Movie).filter(
                            (Movie.id == movie_code) | (Movie.short_id == movie_code),
                            Movie.deleted_at == None
                        ).first()
                        
                        if not movie:
                            # Truncate kode kalo kepanjangan (misalnya user salah kirim File ID)
                            display_code = movie_code[:30] + '...' if len(movie_code) > 30 else movie_code
                            bot.send_message(
                                message.chat.id,
                                f"❌ Film dengan kode <code>{escape_html(display_code)}</code> tidak ditemukan.\n\n"
                                f"💡 <b>Format kode yang benar:</b>\n"
                                f"• Movie ID: <code>movie-12345</code>\n"
                                f"• Short ID: <code>SHORT-ABC</code>\n\n"
                                f"Kirim kode film yang benar:",
                                parse_mode='HTML'
                            )
                            return
                        
                        movie.poster_file_id = poster_file_id
                        db.commit()
                        
                        update_pending_upload_status(message_id, 'used')
                        delete_conversation(admin_id)
                        
                        safe_title = escape_html(movie.title)
                        bot.send_message(
                            message.chat.id,
                            f"✅ <b>Poster berhasil di-assign!</b>\n\n"
                            f"🎬 Film: <b>{safe_title}</b>\n"
                            f"🆔 Kode: <code>{movie.id}</code>\n"
                            f"🖼️ Poster ID: <code>{poster_file_id[:20]}...</code>\n\n"
                            f"Poster sudah bisa dilihat user!",
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Poster assigned ke film: {movie.id}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error assign poster: {e}")
                        db.rollback()
                        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
                    finally:
                        db.close()
            
        except Exception as e:
            logger.error(f"❌ Error handling admin text input: {e}")
            bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('watch_part_'))
    def handle_watch_part_callback(call):
        logger.info(f"📺 CALLBACK RECEIVED: User {call.from_user.id} mau nonton part: {call.data}")
        
        try:
            parts = call.data.split('_')
            logger.info(f"📺 Parsed parts: {parts}, length: {len(parts)}")
            if len(parts) < 4:
                logger.warning(f"❌ Format callback tidak valid: {call.data}")
                bot.answer_callback_query(call.id, "Format callback tidak valid")
                return
            
            callback_id = parts[2]
            part_number = int(parts[3])
            
            user_id = call.from_user.id
            
            # Dual parsing: coba resolve short_id dulu, kalau ga ada pake full movie_id
            movie = get_movie_by_short_id(callback_id)
            if not movie:
                movie = get_movie_by_id(callback_id)
            
            if not movie:
                bot.answer_callback_query(call.id, "Film tidak ditemukan")
                logger.warning(f"Movie tidak ditemukan untuk callback_id: {callback_id}")
                return
            
            movie_id = movie.get('id')
            short_id = movie.get('short_id', callback_id)
            
            if not is_vip(user_id):
                telegram_delivery.send_non_vip_message(bot, call.message.chat.id, movie)
                return
            
            part = get_part(movie_id, part_number)
            if not part:
                bot.answer_callback_query(call.id, "Part tidak ditemukan")
                return
            
            total_parts = movie.get('total_parts', 1)
            part_title = escape_html(part.get('title', f'Part {part_number}'))
            
            success = telegram_delivery.send_series_part(
                bot, 
                call.message.chat.id, 
                movie, 
                part, 
                part_number, 
                total_parts, 
                short_id
            )
            
            if success:
                bot.answer_callback_query(call.id, f"▶️ {part_title}")
            else:
                bot.answer_callback_query(call.id, "Part tidak memiliki video")
                
        except ValueError as ve:
            logger.error(f"❌ Error parsing callback data: {ve}")
            bot.answer_callback_query(call.id, "Data tidak valid")
        except Exception as e:
            logger.error(f"❌ Error handle watch part: {e}")
            bot.answer_callback_query(call.id, "Terjadi kesalahan")
    
    @bot.callback_query_handler(func=lambda call: call.data.startswith('list_part_'))
    def handle_list_parts_callback(call):
        logger.info(f"📋 User {call.from_user.id} mau lihat list part: {call.data}")
        
        movie_id = None
        movie = None
        
        try:
            callback_id = call.data.split('_')[2]
            
            user_id = call.from_user.id
            if not is_vip(user_id):
                bot.answer_callback_query(call.id, "Hanya untuk member VIP")
                return
            
            movie = get_movie_by_short_id(callback_id)
            if not movie:
                movie = get_movie_by_id(callback_id)
            
            if not movie:
                bot.answer_callback_query(call.id, "Film tidak ditemukan")
                logger.warning(f"Movie tidak ditemukan untuk callback_id: {callback_id}")
                return
            
            movie_id = movie.get('id')
            
            success = telegram_delivery.edit_parts_list(bot, call.message, movie_id, movie)
            
            if success:
                bot.answer_callback_query(call.id, "📋 List Parts")
            else:
                logger.warning(f"⚠️ Gagal edit message, fallback ke send_parts_list")
                telegram_delivery.send_parts_list(bot, call.message.chat.id, movie_id, movie)
                bot.answer_callback_query(call.id, "📋 List Parts")
            
        except ApiTelegramException as api_err:
            logger.error(f"❌ Telegram API error: {api_err}")
            if movie_id and movie:
                try:
                    telegram_delivery.send_parts_list(bot, call.message.chat.id, movie_id, movie)
                    bot.answer_callback_query(call.id, "📋 List Parts")
                except Exception as fallback_err:
                    logger.error(f"❌ Fallback juga gagal: {fallback_err}")
                    bot.answer_callback_query(call.id, "Terjadi kesalahan")
            else:
                bot.answer_callback_query(call.id, "Terjadi kesalahan")
        except Exception as e:
            logger.error(f"❌ Error handle list parts: {e}")
            bot.answer_callback_query(call.id, "Terjadi kesalahan")
    
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
        
        send_welcome_message(bot, call.message.chat.id)
        bot.answer_callback_query(call.id, "🏠 Menu Utama")

    @bot.message_handler(commands=['addvip'])
    def add_vip_command(message):
        user = get_or_create_user(message.from_user)
        if not user:
            bot.reply_to(message, "Gagal memproses user.")
            return

        db = SessionLocal()
        try:
            telegram_id = str(message.from_user.id)
            # BUG FIX #8: Exclude soft-deleted users
            db_user = db.query(User).filter(
                User.telegram_id == telegram_id,
                User.deleted_at == None
            ).first()
            
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
            # BUG FIX #8: Exclude soft-deleted users
            db_user = db.query(User).filter(
                User.telegram_id == telegram_id,
                User.deleted_at == None
            ).first()
            
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

def shutdown_monitor():
    """
    Monitor bot_state.shutdown flag and stop polling when shutdown requested.
    This runs in a separate daemon thread and enables graceful shutdown.
    """
    while not bot_state.should_shutdown():
        time.sleep(1)
    
    logger.info("🛑 Shutdown requested, stopping bot polling...")
    if bot is not None:
        try:
            bot.stop_polling()
            logger.info("✅ Bot polling stopped gracefully")
        except Exception as e:
            logger.error(f"❌ Error stopping bot polling: {e}")

def setup_webhook():
    """
    Setup webhook untuk production (Render/cloud hosting).
    Digunakan sebagai pengganti infinity_polling yang tidak stabil di cloud.
    """
    if not TELEGRAM_BOT_TOKEN or bot is None:
        logger.error("❌ TELEGRAM_BOT_TOKEN ga dikonfigurasi - bot ga bisa jalan")
        bot_state.signal_failed("TELEGRAM_BOT_TOKEN not configured")
        return False
    
    assert bot is not None
    
    try:
        # Test koneksi bot
        me = bot.get_me()
        logger.info(f"✅ Tes koneksi bot berhasil - @{me.username} (ID: {me.id})")
        
        # Hapus webhook lama kalau ada
        bot.remove_webhook()
        logger.info("✅ Webhook lama dihapus")
        
        # Set webhook baru
        webhook_url = f"{BASE_URL}/webhook/telegram"
        success = bot.set_webhook(url=webhook_url, allowed_updates=['message', 'callback_query'])
        
        if success:
            logger.info(f"✅ Webhook berhasil di-set ke: {webhook_url}")
            bot_state.signal_started()
            logger.info("✅ Bot marked as started - health checks will now pass")
            return True
        else:
            logger.error("❌ Gagal set webhook")
            bot_state.signal_failed("Failed to set webhook")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error setup webhook: {e}")
        bot_state.signal_failed(f"Webhook setup error: {e}")
        return False

def run_bot():
    """
    LEGACY MODE: Run bot dengan polling (untuk development/localhost).
    Untuk production di Render, gunakan webhook mode (setup_webhook).
    """
    if not TELEGRAM_BOT_TOKEN or bot is None:
        logger.error("❌ TELEGRAM_BOT_TOKEN ga dikonfigurasi - bot ga bisa jalan")
        logger.error("Set TELEGRAM_BOT_TOKEN di environment variables buat aktifin Telegram bot")
        bot_state.signal_failed("TELEGRAM_BOT_TOKEN not configured")
        return
    
    assert bot is not None
    
    if bot_state.should_shutdown():
        logger.info("🛑 Shutdown already requested, not starting bot")
        return
    
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
            
            monitor_thread = threading.Thread(target=shutdown_monitor, daemon=True, name="ShutdownMonitor")
            monitor_thread.start()
            logger.info("✅ Shutdown monitor thread started")
            
            bot_state.signal_started()
            logger.info("✅ Bot marked as started - health checks will now pass")
            
            logger.info("🔄 Mulai polling loop...")
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True, allowed_updates=['message', 'callback_query'])
            
            logger.info("🛑 Polling loop exited")
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
                bot_state.signal_failed(f"Conflict error 409: {e}")
                return
            else:
                logger.error(f"❌ Error API Telegram: {e}")
                bot_state.signal_failed(f"Telegram API error: {e}")
                raise
        except KeyboardInterrupt:
            logger.info("⚠️ Bot stopped by user")
            bot.stop_polling()
            break
        except Exception as e:
            logger.error(f"❌ Error ga terduga: {e}")
            retry_count += 1
            if retry_count >= max_retries:
                bot_state.signal_failed(f"Max retries exceeded: {e}")
                raise
            logger.info(f"Retrying in 30 seconds... (attempt {retry_count}/{max_retries})")
            time.sleep(30)

if __name__ == '__main__':
    run_bot()
