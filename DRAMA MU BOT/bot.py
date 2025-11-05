import os
import json
import logging
import requests
import random
import string
from datetime import datetime, timedelta
from telebot import TeleBot, types
from telebot.apihelper import ApiTelegramException
from supabase import create_client, Client

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')

MINIAPP_URL = os.getenv('MINIAPP_URL', 'https://dramamuid.netlify.app')
API_BASE_URL = 'https://dramamu-api.onrender.com/api/v1'

# --- Validasi Environment Variables ---
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("⚠️ TELEGRAM_BOT_TOKEN tidak ditemukan!")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("⚠️ SUPABASE_URL dan SUPABASE_KEY tidak ditemukan!")

# --- Inisialisasi Klien ---
bot = TeleBot(TELEGRAM_BOT_TOKEN)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Konfigurasi URL Mini App ---
if MINIAPP_URL.endswith('/drama'):
    BASE_URL = MINIAPP_URL.rsplit('/drama', 1)[0]
else:
    BASE_URL = MINIAPP_URL.rsplit('/', 1)[0] if '/' in MINIAPP_URL.split('://')[-1] else MINIAPP_URL

URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_CARI_CUAN = f"{BASE_URL}/referal.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_HUBUNGI_KAMI = f"{BASE_URL}/contact.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"


# --- Fungsi Helper ---

def escape_html(text):
    """Escape HTML special characters"""
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
    Generate referral code (ref + 5 digit + random 4 char)
    Sesuai permintaan Anda.
    """
    logger.info(f"Membuat ref_code untuk {telegram_id}...")
    first_five = str(telegram_id)[:5]
    rand_part = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
    ref_code = f"ref{first_five}{rand_part}"
    logger.info(f"Ref_code dibuat: {ref_code}")
    return ref_code

# --- Fungsi Manajemen User (Supabase) ---

def get_or_create_user(user):
    """
    Ambil user dari DB atau buat baru jika belum ada.
    Ini juga akan membuatkan ref_code untuk user baru.
    """
    try:
        telegram_id = str(user.id)
        res = supabase.table('users').select('*').eq('telegram_id', telegram_id).execute()
        
        if res.data:
            logger.info(f"User {telegram_id} ditemukan di DB.")
            return res.data[0]
        else:
            logger.info(f"User {telegram_id} tidak ditemukan, membuat user baru...")
            ref_code = generate_ref_code(telegram_id)
            new_user_data = {
                'telegram_id': telegram_id,
                'username': user.username,
                'ref_code': ref_code,
                'is_vip': False,
                'created_at': datetime.now().isoformat()
            }
            insert_res = supabase.table('users').insert(new_user_data).execute()
            
            if insert_res.data:
                logger.info(f"✅ User {telegram_id} berhasil dibuat dengan ref_code {ref_code}.")
                return insert_res.data[0]
            else:
                logger.error(f"❌ Gagal insert user baru: {insert_res.error}")
                return None
    except Exception as e:
        logger.error(f"❌ Error di get_or_create_user: {e}")
        return None

def is_vip(user_id):
    """
    Cek apakah user adalah VIP dari Supabase.
    Juga menangani jika VIP sudah expired.
    """
    try:
        user_id_str = str(user_id)
        res = supabase.table('users').select('is_vip, vip_expires_at').eq('telegram_id', user_id_str).execute()
        
        if not res.data:
            logger.warning(f"is_vip check: User {user_id_str} tidak ditemukan.")
            return False
            
        user_data = res.data[0]
        is_vip_status = user_data.get('is_vip')
        expires_at_str = user_data.get('vip_expires_at')
        
        if not is_vip_status:
            return False # User bukan VIP
            
        # Jika VIP tapi ada expiration date
        if expires_at_str:
            # Parse waktu, abaikan timezone jika ada (buat perbandingan naive)
            expires_at_dt = datetime.fromisoformat(expires_at_str).replace(tzinfo=None)
            
            # Cek apakah sudah expired
            if expires_at_dt <= datetime.now():
                logger.info(f"VIP {user_id_str} expired. Update status ke False.")
                # Update DB jadi non-VIP
                try:
                    supabase.table('users').update({'is_vip': False, 'vip_expires_at': None}).eq('telegram_id', user_id_str).execute()
                except Exception as e:
                    logger.error(f"Gagal update status expired VIP: {e}")
                return False # VIP sudah expired
        
        # Jika is_vip=True dan (expires_at=null ATAU belum expired)
        return True
        
    except Exception as e:
        logger.error(f"❌ Error di is_vip: {e}")
        return False

# --- Fungsi Fetch Movie (Tidak Diubah) ---

def get_movie_by_id(movie_id):
    """Fetch movie detail dari API berdasarkan movie_id"""
    try:
        # Ini tetap mengambil dari API Render Anda, BUKAN dari Supabase
        response = requests.get(f"{API_BASE_URL}/movies", timeout=10)
        if response.status_code == 200:
            data = response.json()
            movies = data.get('movies', [])
            for movie in movies:
                if movie['id'] == movie_id:
                    return movie
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching movie: {e}")
        return None

# --- Message Handlers ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handler untuk command /start - kirim welcome message"""
    logger.info(f"User {message.from_user.id} memulai bot")
    
    # PENTING: Panggil get_or_create_user di sini
    # Ini akan memastikan user ada di DB dan punya ref_code
    try:
        user_db = get_or_create_user(message.from_user)
        if not user_db:
            logger.error(f"Gagal membuat/mengambil data user {message.from_user.id}")
            bot.send_message(message.chat.id, "⚠️ Terjadi masalah saat sinkronisasi data Anda. Silakan coba lagi nanti.")
            return
    except Exception as e:
        logger.error(f"Error besar di /start saat get_or_create_user: {e}")
        bot.send_message(message.chat.id, "⚠️ Bot sedang mengalami gangguan. Coba lagi beberapa saat.")
        return

    BANNER_URL = "https://geczfycekxkeiubbaijz.supabase.co/storage/v1/object/public/POSTER/banner-dramamu.jpg"
    
    welcome_text = (
        "🎬 <b>Selamat datang di Dramamu</b>\n\n"
        "Nonton semua drama favorit cuma segelas kopi ☕\n\n"
        "⭐ Join <a href='https://t.me/dramamuofficial'>GRUP DRAMA MU OFFICIAL</a> ⭐\n\n"
        "Pilih menu di bawah, bre!"
    )
    
    keyboard_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    btn_cari_judul = types.KeyboardButton("🎬 CARI JUDUL", web_app=types.WebAppInfo(URL_CARI_JUDUL))
    btn_cari_cuan = types.KeyboardButton("💰 CARI CUAN", web_app=types.WebAppInfo(URL_CARI_CUAN))
    btn_beli_vip = types.KeyboardButton("💎 BELI VIP", web_app=types.WebAppInfo(URL_BELI_VIP))
    btn_req_drama = types.KeyboardButton("📽 REQ DRAMA", web_app=types.WebAppInfo(URL_REQUEST))
    btn_hubungi_kami = types.KeyboardButton("💬 HUBUNGI KAMI", web_app=types.WebAppInfo(URL_HUBUNGI_KAMI))
    
    keyboard_markup.add(btn_cari_judul, btn_cari_cuan)
    keyboard_markup.add(btn_beli_vip, btn_req_drama)
    keyboard_markup.add(btn_hubungi_kami)
    
    try:
        bot.send_photo(
            message.chat.id,
            BANNER_URL,
            caption=welcome_text,
            parse_mode='HTML',
            reply_markup=keyboard_markup
        )
    except Exception as e:
        logger.error(f"Error sending banner: {e}")
        bot.send_message(
            message.chat.id,    
            welcome_text,    
            reply_markup=keyboard_markup,    
            parse_mode='HTML'
        )

@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    """Handler untuk menangkap data dari Mini App"""
    try:
        data = json.loads(message.web_app_data.data)
        logger.info(f"📥 Data diterima dari Mini App: {data}")
        
        action = data.get('action')
        movie_id = data.get('movie_id')
        user_id = message.from_user.id
        
        if action == 'watch' and movie_id:
            logger.info(f"User {user_id} ingin nonton film ID: {movie_id}")
            
            movie = get_movie_by_id(movie_id)
            
            if not movie:
                bot.send_message(message.chat.id, "❌ Film tidak ditemukan.")
                return
            
            # Cek VIP menggunakan fungsi baru
            if is_vip(user_id):
                send_movie_to_vip(message.chat.id, movie)
            else:
                send_non_vip_message(message.chat.id, movie)
        else:
            bot.send_message(message.chat.id, "⚠️ Data tidak valid.")
            
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON: {e}")
        bot.send_message(message.chat.id, "❌ Error: Data tidak valid.")
    except Exception as e:
        logger.error(f"Error handling web app data: {e}")
        bot.send_message(message.chat.id, "❌ Terjadi kesalahan.")

# --- Fungsi Kirim Pesan (Tidak Diubah) ---

def send_movie_to_vip(chat_id, movie):
    """Kirim film dengan inline keyboard untuk user VIP"""
    logger.info(f"✅ Mengirim film ke VIP user: {chat_id}")
    
    safe_title = escape_html(movie.get('title', 'Unknown'))
    safe_description = escape_html(movie.get('description', ''))
    
    caption = (
        f"🎬 <b>{safe_title}</b>\n\n"
        f"{safe_description}\n\n"
        f"🌟 <i>Selamat menonton!</i>"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("▶️ Tonton Sekarang", url=movie['video_link'])
    )
    markup.row(
        types.InlineKeyboardButton("📥 Download", url=movie['video_link']),
        types.InlineKeyboardButton("🔗 Share", callback_data=f"share_{movie['id']}")
    )
    
    try:
        logger.info(f"🔄 Mencoba kirim photo ke VIP chat_id: {chat_id}")
        bot.send_photo(
            chat_id,    
            movie['poster_url'],    
            caption=caption,
            parse_mode='HTML',
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
        try:
            bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
        except Exception as fallback_error:
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {fallback_error}")

def send_non_vip_message(chat_id, movie):
    """Kirim pesan 'belum VIP' dengan inline keyboard"""
    logger.info(f"⚠️ User {chat_id} belum VIP, kirim pesan ajakan")
    
    safe_title = escape_html(movie.get('title', 'Unknown'))
    
    text = (
        f"🔒 <b>{safe_title}</b>\n\n"
        f"Maaf, konten ini hanya untuk member VIP.\n\n"
        f"Anda belum menjadi member VIP.\n"
        f"Silakan join VIP terlebih dahulu untuk menonton film ini! 🌟"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("⭐ Join VIP Sekarang", callback_data="join_vip")
    )
    markup.row(
        types.InlineKeyboardButton("ℹ️ Info VIP", callback_data="info_vip"),
        types.InlineKeyboardButton("🎬 Pilih Film Lain", callback_data="back_to_app")
    )
    
    try:
        logger.info(f"🔄 Mencoba kirim photo ke chat_id: {chat_id}")
        bot.send_photo(
            chat_id,
            movie['poster_url'],
            caption=text,
            parse_mode='HTML',
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
        try:
            bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)
        except Exception as fallback_error:
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {fallback_error}")

# --- Callback Handler (Tidak Diubah) ---

@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    """Handler untuk inline keyboard button callbacks"""
    logger.info(f"Callback received: {call.data} from user {call.from_user.id}")
    
    try:
        has_photo = call.message.photo is not None and len(call.message.photo) > 0
        
        if call.data == "join_vip":
            text = (
                "💎 <b>Paket VIP Dramamu</b>\n\n"
                "✨ Keuntungan VIP:\n"
                "• Akses unlimited semua film\n"
                "• Tanpa iklan\n"
                "• Kualitas HD\n"
                "• Download unlimited\n\n"
                "Klik tombol di bawah untuk melakukan pembayaran:"
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("💳 Beli VIP Sekarang", web_app=types.WebAppInfo(URL_BELI_VIP))
            )
            markup.row(
                types.InlineKeyboardButton("ℹ️ Info Lebih Lanjut", callback_data="info_vip"),
                types.InlineKeyboardButton("« Kembali", callback_data="back_to_app")
            )
            
            try:
                if has_photo:
                    bot.edit_message_caption(
                        caption=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
                else:
                    bot.edit_message_text(
                        text=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            except ApiTelegramException as e:
                if "message is not modified" not in str(e):
                    logger.error(f"Error editing message: {e}")
            
            bot.answer_callback_query(call.id)
            return
            
        elif call.data == "info_vip":
            text = (
                "ℹ️ <b>Informasi VIP</b>\n\n"
                "Dengan menjadi member VIP, Anda bisa:\n"
                "✅ Nonton semua film tanpa batas\n"
                "✅ Kualitas video HD\n"
                "✅ Download sepuasnya\n"
                "✅ Bebas iklan\n"
                "✅ Akses film terbaru duluan\n\n"
                "💰 Harga: Rp 50.000/bulan\n"
            )
            
            markup = types.InlineKeyboardMarkup()
            markup.row(
                types.InlineKeyboardButton("⭐ Join VIP", callback_data="join_vip")
            )
            
            try:
                if has_photo:
                    bot.edit_message_caption(
                        caption=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
                else:
                    bot.edit_message_text(
                        text=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            except ApiTelegramException as e:
                if "message is not modified" not in str(e):
                    logger.error(f"Error editing message: {e}")
            
            bot.answer_callback_query(call.id)
            return
            
        elif call.data == "back_to_app":
            bot.answer_callback_query(call.id, "Klik tombol '🎬 Cari Judul' di bawah untuk membuka Mini App lagi")
            return
            
        elif call.data.startswith("share_"):
            movie_id = call.data.split("_")[1]
            bot.answer_callback_query(call.id, "🔗 Link film berhasil disalin!")
            return
        
        bot.answer_callback_query(call.id)
        
    except Exception as e:
        logger.error(f"Error handling callback query: {e}")
        try:
            bot.answer_callback_query(call.id, "⚠️ Terjadi kesalahan")
        except:
            pass

# --- Perintah Admin/Testing (Diperbarui) ---

@bot.message_handler(commands=['addvip'])
def add_vip_command(message):
    """Command untuk menambahkan user ke VIP (untuk testing) - Update ke Supabase"""
    user = get_or_create_user(message.from_user)
    if not user:
        bot.reply_to(message, "❌ Gagal memproses user.")
        return

    telegram_id = str(message.from_user.id)
    # Set VIP 30 hari dari sekarang
    expires_at = (datetime.now() + timedelta(days=30)).isoformat()
    
    try:
        supabase.table('users').update({
            'is_vip': True,
            'vip_expires_at': expires_at
        }).eq('telegram_id', telegram_id).execute()
        
        logger.info(f"✅ User {telegram_id} ditambahkan ke VIP (30 hari)")
        bot.reply_to(message, "✅ Anda sekarang adalah member VIP selama 30 hari!\n\nSilakan pilih film lagi.")
    except Exception as e:
        logger.error(f"❌ Error di addvip: {e}")
        bot.reply_to(message, "❌ Terjadi error saat update VIP.")

@bot.message_handler(commands=['removevip'])
def remove_vip_command(message):
    """Command untuk remove VIP status (untuk testing) - Update ke Supabase"""
    telegram_id = str(message.from_user.id)
    try:
        supabase.table('users').update({
            'is_vip': False,
            'vip_expires_at': None
        }).eq('telegram_id', telegram_id).execute()
        
        logger.info(f"VIP status untuk {telegram_id} dihapus.")
        bot.reply_to(message, "❌ VIP status Anda telah dihapus.")
    except Exception as e:
        logger.error(f"❌ Error di removevip: {e}")
        bot.reply_to(message, "❌ Terjadi error saat remove VIP.")

@bot.message_handler(commands=['checkvip'])
def check_vip_command(message):
    """Command untuk cek status VIP"""
    user_id = message.from_user.id
    # Panggil get_or_create_user untuk memastikan user terdaftar
    get_or_create_user(message.from_user) 
    
    if is_vip(user_id):
        bot.reply_to(message, "✅ Anda adalah member VIP!")
    else:
        bot.reply_to(message, "❌ Anda belum menjadi member VIP.")

# --- Main Loop ---

if __name__ == '__main__':
    logger.info("🤖 Bot dimulai...")
    logger.info(f"Connecting to Supabase at: {SUPABASE_URL[:20]}...")
    logger.info(f"📱 BASE URL: {BASE_URL}")
    logger.info(f"🎬 Cari Judul: {URL_CARI_JUDUL}")
    logger.info(f"💰 Cari Cuan: {URL_CARI_CUAN}")
    logger.info("✅ Bot terhubung ke Supabase & siap menerima perintah.")
    bot.infinity_polling()
