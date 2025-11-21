import logging
import os
from telebot import types
from database import get_parts_by_movie_id
from config import URL_CARI_JUDUL, URL_BELI_VIP

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def escape_html(text):
    if not text:
        return text
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))

def send_movie_to_vip(bot, chat_id, movie):
    """
    Kirim film ke user VIP.
    Otomatis cek apakah film adalah series atau single movie.
    
    Args:
        bot: TeleBot instance
        chat_id: Telegram chat ID
        movie: Movie dictionary dengan semua field
    """
    logger.info(f"✅ Kirim film ke user VIP: {chat_id}")
    
    is_series = movie.get('is_series', False)
    
    if is_series:
        movie_id = movie.get('id')
        short_id = movie.get('short_id', movie_id)
        total_parts = movie.get('total_parts', 1)
        logger.info(f"📺 Series detected, sending Part 1 directly")
        
        from database import get_part
        part_1 = get_part(movie_id, 1)
        
        if part_1 and (part_1.get('telegram_file_id') or part_1.get('video_link')):
            send_series_part(bot, chat_id, movie, part_1, 1, total_parts, short_id, use_list_buttons=True)
        else:
            if not part_1:
                logger.warning(f"⚠️ Part 1 tidak ditemukan untuk film {movie_id}, fallback ke list parts")
            else:
                logger.warning(f"⚠️ Part 1 tidak memiliki media untuk film {movie_id}, fallback ke list parts")
            send_parts_list(bot, chat_id, movie_id, movie)
    else:
        send_single_movie(bot, chat_id, movie)

def send_single_movie(bot, chat_id, movie):
    """
    Kirim single movie (bukan series) ke user.
    
    Args:
        bot: TeleBot instance
        chat_id: Telegram chat ID
        movie: Movie dictionary
    """
    logger.info(f"📹 Kirim film single ke {chat_id}: {movie.get('title')}")
    
    safe_title = escape_html(movie.get('title', 'Unknown'))
    safe_description = escape_html(movie.get('description', ''))
    
    caption = (
        f"🎬 <b>{safe_title}</b>\n\n"
        f"{safe_description}\n\n"
        f"Selamat menonton!"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    telegram_file_id = movie.get('telegram_file_id')
    video_link = movie.get('video_link')
    
    if telegram_file_id:
        try:
            bot.send_video(
                chat_id,
                telegram_file_id,
                caption=caption,
                parse_mode='HTML',
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
                )
            )
            logger.info(f"✅ Video terkirim via telegram_file_id ke {chat_id}")
            return
        except Exception as e:
            logger.error(f"❌ Gagal kirim via telegram_file_id: {e}, fallback ke link")
    
    if video_link:
        btn_tonton = types.InlineKeyboardButton("▶️ Tonton Sekarang", url=video_link)
        btn_download = types.InlineKeyboardButton("📥 Download", url=video_link)
        btn_menu = types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
        
        markup.add(btn_tonton, btn_download)
        markup.add(btn_menu)
    else:
        btn_menu = types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
        markup.add(btn_menu)
    
    try:
        movie_id = movie.get('id', 'unknown')
        
        # Prioritas 1: Pakai Telegram File ID jika ada
        poster_file_id = movie.get('poster_file_id')
        if poster_file_id:
            try:
                bot.send_photo(
                    chat_id,
                    poster_file_id,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Poster dari Telegram File ID terkirim untuk film {movie_id}")
                return
            except Exception as e:
                logger.warning(f"⚠️ Gagal kirim poster via file_id: {e}, fallback ke poster_url")
        
        # Prioritas 2: Fallback ke poster_url (file lokal)
        poster_path = None
        poster_url = movie.get('poster_url')
        if poster_url and '/media/' in poster_url:
            poster_path = poster_url.split('/media/')[-1]
            poster_path = os.path.join('backend_assets', poster_path)
        
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

def build_parts_list_view(movie, parts):
    """
    Build caption dan markup untuk parts list.
    Helper function untuk reuse logic antara send dan edit.
    
    Args:
        movie: Movie dictionary
        parts: List of parts
    
    Returns:
        tuple: (caption, markup)
    """
    safe_title = escape_html(movie.get('title', 'Unknown'))
    total_parts = movie.get('total_parts', len(parts))
    short_id = movie.get('short_id', movie.get('id'))
    
    caption = (
        f"🎬 <b>{safe_title}</b>\n\n"
        f"📺 Total Part: {total_parts}\n\n"
        f"Silakan pilih part yang ingin Anda tonton:"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for part in parts:
        part_number = part.get('part_number')
        part_title = part.get('title', f'Part {part_number}')
        btn = types.InlineKeyboardButton(
            part_title,
            callback_data=f"watch_part_{short_id}_{part_number}"
        )
        markup.add(btn)
    
    markup.row(
        types.InlineKeyboardButton("🔍 Search Via Bot", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
    )
    markup.row(
        types.InlineKeyboardButton("🏠 Home", callback_data="menu_utama")
    )
    
    return caption, markup

def edit_parts_list(bot, origin_message, movie_id, movie):
    """
    Edit pesan yang ada untuk menampilkan list parts.
    Tombol di pesan yang sama diganti dengan list parts, bukan kirim pesan baru.
    
    Args:
        bot: TeleBot instance
        origin_message: Message object dari callback query
        movie_id: Movie ID (full ID)
        movie: Movie dictionary
    
    Returns:
        bool: True jika berhasil edit, False jika gagal
    """
    logger.info(f"✏️ Edit message ke list parts untuk film {movie_id}")
    
    try:
        parts = get_parts_by_movie_id(movie_id)
        
        if not parts:
            logger.warning(f"⚠️ Tidak ada parts untuk film {movie_id}")
            return False
        
        caption, markup = build_parts_list_view(movie, parts)
        
        content_type = origin_message.content_type
        logger.info(f"📝 Content type pesan: {content_type}")
        
        if content_type in ['photo', 'video']:
            bot.edit_message_caption(
                caption=caption,
                chat_id=origin_message.chat.id,
                message_id=origin_message.message_id,
                parse_mode='HTML',
                reply_markup=markup
            )
            logger.info(f"✅ Berhasil edit caption dengan list parts")
        else:
            bot.edit_message_text(
                text=caption,
                chat_id=origin_message.chat.id,
                message_id=origin_message.message_id,
                parse_mode='HTML',
                reply_markup=markup
            )
            logger.info(f"✅ Berhasil edit text dengan list parts")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error edit parts list: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return False

def send_parts_list(bot, chat_id, movie_id, movie):
    """
    Kirim list parts untuk series ke user.
    Menampilkan semua part sebagai tombol yang bisa diklik.
    
    Args:
        bot: TeleBot instance
        chat_id: Telegram chat ID
        movie_id: Movie ID (full ID)
        movie: Movie dictionary
    """
    logger.info(f"📺 Kirim list parts untuk film {movie_id} ke {chat_id}")
    
    parts = get_parts_by_movie_id(movie_id)
    
    if not parts:
        bot.send_message(
            chat_id, 
            "Film ini belum memiliki part. Silakan hubungi admin.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")
            )
        )
        return
    
    caption, markup = build_parts_list_view(movie, parts)
    
    try:
        # Prioritas 1: Pakai Telegram File ID jika ada
        poster_file_id = movie.get('poster_file_id')
        if poster_file_id:
            try:
                result = bot.send_photo(
                    chat_id,
                    poster_file_id,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Part list dengan poster (File ID) berhasil dikirim! Message ID: {result.message_id}")
                return
            except Exception as e:
                logger.warning(f"⚠️ Gagal kirim poster via file_id: {e}, fallback ke poster_url")
        
        # Prioritas 2: Fallback ke poster_url (file lokal)
        poster_path = None
        poster_url = movie.get('poster_url')
        if poster_url and '/media/' in poster_url:
            poster_path = poster_url.split('/media/')[-1]
            poster_path = os.path.join('backend_assets', poster_path)
        
        logger.info(f"🖼️ Poster path: {poster_path}, exists: {os.path.exists(poster_path) if poster_path else False}")
        
        if poster_path and os.path.exists(poster_path):
            with open(poster_path, 'rb') as photo:
                result = bot.send_photo(
                    chat_id,
                    photo,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Part list dengan poster berhasil dikirim! Message ID: {result.message_id}")
        else:
            logger.warning(f"⚠️ Poster tidak ditemukan, kirim tanpa foto")
            result = bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
            logger.info(f"✅ Part list tanpa poster berhasil dikirim! Message ID: {result.message_id}")
    except Exception as e:
        logger.error(f"❌ Error kirim part list: {e}")
        logger.error(f"❌ Error type: {type(e).__name__}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        try:
            bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
            logger.info("✅ Fallback message berhasil dikirim")
        except Exception as fallback_err:
            logger.error(f"❌ Fallback juga gagal: {fallback_err}")

def send_non_vip_message(bot, chat_id, movie):
    """
    Kirim pesan ke user non-VIP dengan ajakan untuk upgrade.
    
    Args:
        bot: TeleBot instance
        chat_id: Telegram chat ID
        movie: Movie dictionary
    """
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
    
    try:
        movie_id = movie.get('id', 'unknown')
        
        # Prioritas 1: Pakai Telegram File ID jika ada
        poster_file_id = movie.get('poster_file_id')
        if poster_file_id:
            try:
                bot.send_photo(
                    chat_id,
                    poster_file_id,
                    caption=caption,
                    parse_mode='HTML',
                    reply_markup=markup
                )
                logger.info(f"✅ Non-VIP message dengan poster (File ID) terkirim untuk film {movie_id}")
                return
            except Exception as e:
                logger.warning(f"⚠️ Gagal kirim poster via file_id: {e}, fallback ke poster_url")
        
        # Prioritas 2: Fallback ke poster_url (file lokal)
        poster_path = None
        poster_url = movie.get('poster_url')
        if poster_url and '/media/' in poster_url:
            poster_path = poster_url.split('/media/')[-1]
            poster_path = os.path.join('backend_assets', poster_path)
        
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

def send_series_part(bot, chat_id, movie, part, part_number, total_parts, short_id=None, use_list_buttons=False):
    """
    Helper function untuk mengirim video part ke user.
    Digunakan oleh send_movie_to_vip (Part 1) dan handle_watch_part_callback (semua parts).
    
    Args:
        bot: TeleBot instance
        chat_id: Telegram chat ID
        movie: Movie dictionary
        part: Part dictionary
        part_number: Part number yang dikirim
        total_parts: Total parts dari movie
        short_id: Short ID dari movie
        use_list_buttons: Jika True, tampilkan button list parts. Jika False, tampilkan button navigasi
        
    Returns:
        bool: True jika berhasil kirim, False jika gagal
    """
    logger.info(f"📹 Kirim part {part_number}/{total_parts} ke {chat_id}")
    
    safe_title = escape_html(movie.get('title', 'Unknown'))
    part_title = escape_html(part.get('title', f'Part {part_number}'))
    
    caption = (
        f"🎬 <b>{safe_title}</b>\n"
        f"📺 {part_title}\n\n"
        f"Part {part_number}/{total_parts}"
    )
    
    movie_id = movie.get('id')
    
    if use_list_buttons:
        nav_markup = create_parts_list_markup(movie_id, short_id)
    else:
        nav_markup = create_part_navigation_markup(movie_id, part_number, total_parts, short_id)
    
    telegram_file_id = part.get('telegram_file_id')
    video_link = part.get('video_link')
    
    if telegram_file_id:
        try:
            bot.send_video(
                chat_id,
                telegram_file_id,
                caption=caption,
                parse_mode='HTML',
                reply_markup=nav_markup
            )
            from database import increment_part_views
            increment_part_views(part.get('id'))
            logger.info(f"✅ Part {part_number} terkirim via file_id")
            return True
        except Exception as e:
            logger.error(f"❌ Gagal kirim via file_id: {e}, fallback ke link")
    
    if video_link:
        caption += f"\n\n▶️ <a href='{video_link}'>Tonton Sekarang</a>"
        bot.send_message(
            chat_id,
            caption,
            parse_mode='HTML',
            reply_markup=nav_markup
        )
        from database import increment_part_views
        increment_part_views(part.get('id'))
        logger.info(f"✅ Part {part_number} terkirim via link")
        return True
    
    bot.send_message(
        chat_id,
        "Maaf, part ini belum tersedia.",
        reply_markup=nav_markup
    )
    logger.warning(f"⚠️ Part {part_number} tidak memiliki video")
    return False

def create_part_navigation_markup(movie_id, current_part, total_parts, short_id=None):
    """
    Buat tombol navigasi untuk episode/part (vertikal).
    Menampilkan tombol Previous, Next, List Part, dan Home secara vertikal (satu per baris).
    
    Args:
        movie_id: Movie ID (full ID, dipakai kalau short_id ga ada)
        current_part: Part number yang sedang ditonton
        total_parts: Total number of parts
        short_id: Short ID dari movie (lebih pendek untuk callback data)
        
    Returns:
        InlineKeyboardMarkup dengan tombol navigasi vertikal
    """
    logger.info(f"🎮 Create navigation untuk part {current_part}/{total_parts}")
    
    callback_id = short_id if short_id else movie_id
    markup = types.InlineKeyboardMarkup()
    
    # Tonton selanjutnya (jika ada)
    if current_part < total_parts:
        markup.row(types.InlineKeyboardButton(
            "Tonton selanjutnya ➡️",
            callback_data=f"watch_part_{callback_id}_{current_part + 1}"
        ))
    
    # Tonton sebelumnya (jika ada)
    if current_part > 1:
        markup.row(types.InlineKeyboardButton(
            "⬅️ Tonton sebelumnya",
            callback_data=f"watch_part_{callback_id}_{current_part - 1}"
        ))
    
    # List part
    markup.row(types.InlineKeyboardButton(
        "📋 List part",
        callback_data=f"list_part_{callback_id}"
    ))
    
    # Home
    markup.row(types.InlineKeyboardButton("🏠 Home", callback_data="menu_utama"))
    
    return markup

def create_parts_list_markup(movie_id, short_id=None):
    """
    Buat tombol list semua parts (vertikal).
    Dipakai untuk tampilan pertama kali Part 1 ditampilkan.
    
    Args:
        movie_id: Movie ID (full ID)
        short_id: Short ID dari movie (lebih pendek untuk callback data)
        
    Returns:
        InlineKeyboardMarkup dengan button list parts
    """
    logger.info(f"📋 Create parts list markup untuk movie {movie_id}")
    
    callback_id = short_id if short_id else movie_id
    parts = get_parts_by_movie_id(movie_id)
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    for part in parts:
        part_number = part.get('part_number')
        part_title = part.get('title', f'Part {part_number}')
        btn = types.InlineKeyboardButton(
            part_title,
            callback_data=f"watch_part_{callback_id}_{part_number}"
        )
        markup.add(btn)
    
    markup.row(
        types.InlineKeyboardButton("🔍 Search Via Bot", web_app=types.WebAppInfo(url=URL_CARI_JUDUL))
    )
    markup.row(types.InlineKeyboardButton("🏠 Home", callback_data="menu_utama"))
    
    return markup
