import os
import json
import logging
import requests
import random  # <<< BARU: Untuk ref_code
import string  # <<< BARU: Untuk ref_code
from datetime import datetime, timedelta  # <<< BARU: Tambah timedelta

from telebot import TeleBot, types
from telebot.apihelper import ApiTelegramException
from supabase import create_client, Client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# === KONFIGURASI ===
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MINIAPP_URL = os.getenv('MINIAPP_URL', 'https://dramamuid.netlify.app')
API_BASE_URL = 'https://dramamu-api.onrender.com/api/v1'

# === KONEKSI SUPABASE ===
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("⚠️ TELEGRAM_BOT_TOKEN tidak ditemukan!")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("⚠️ SUPABASE_URL atau SUPABASE_SERVICE_ROLE_KEY tidak ditemukan!")

bot = TeleBot(TELEGRAM_BOT_TOKEN)
# Inisialisasi Supabase Client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
logger.info("🤖 Bot dan Supabase Client berhasil diinisialisasi...")
# === END KONFIGURASI ===


# === PENYESUAIAN URL MINI APP ===
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


# === FUNGSI VIP BARU (PAKE TABEL 'users') ===

def is_vip(user_id):
    """Cek apakah user adalah VIP dari tabel 'users' di Supabase"""
    try:
        user_id_str = str(user_id)
        # NAMA TABEL: 'users', NAMA KOLOM: 'telegram_id'
        # Cek kolom 'is_vip'
        response = supabase.table('users').select('is_vip, vip_expires_at').eq('telegram_id', user_id_str).execute()
        
        if response.data:
            user_data = response.data[0]
            is_vip_status = user_data.get('is_vip', False)
            expires_at_str = user_data.get('vip_expires_at')

            if not is_vip_status:
                logger.info(f"User {user_id_str} ada di DB tapi is_vip=False.")
                return False

            # Kalo ngga ada tanggal kadaluarsa, anggap VIP
            if not expires_at_str:
                logger.info(f"User {user_id_str} adalah VIP (is_vip=True, no expiry).")
                return True
            
            # Cek tanggal kadaluarsa
            try:
                # Coba parse tanggal ISO 8601 dari Supabase
                expires_at = datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
                
                # Cek apakah timezone-aware. Kalo ngga, set manual
                if expires_at.tzinfo is None:
                     # Asumsi 'now()' juga naive, atau kita set UTC
                     pass # Cukup bandingkan
                
                # Bandingkan dengan waktu UTC sekarang
                if expires_at > datetime.now(expires_at.tzinfo):
                    logger.info(f"User {user_id_str} adalah VIP. Kadaluarsa: {expires_at}")
                    return True
                else:
                    logger.info(f"User {user_id_str} VIP-nya kadaluarsa.")
                    # TODO: Bikin fungsi untuk set is_vip=False di DB
                    return False
            except Exception as e:
                logger.error(f"Error parsing tgl 'vip_expires_at': {e}. Anggap VIP (failsafe).")
                return True # Failsafe, anggap VIP kalo tanggalnya error
        
        logger.info(f"User {user_id_str} tidak ditemukan di tabel 'users'.")
        return False
    except Exception as e:
        logger.error(f"Error cek VIP di Supabase: {e}")
        return False


def add_vip(user_id, username=None):
    """Update status user jadi VIP di tabel 'users' Supabase"""
    try:
        user_id_str = str(user_id)
        
        # NAMA TABEL: 'users', NAMA KOLOM: 'telegram_id'
        # Kita update data user yang 'telegram_id'-nya cocok
        # Asumsi: user udah pasti ada di tabel 'users' karena udah /start
        
        # Set kadaluarsa 30 hari dari sekarang
        expires_date = datetime.now() + timedelta(days=30)
        
        data, error = supabase.table('users').update({
            'is_vip': True,
            'vip_expires_at': expires_date.isoformat()
            # 'username': username # Bisa juga update username di sini kalo mau
        }).eq('telegram_id', user_id_str).execute()
        
        if error:
            logger.error(f"Error add VIP ke Supabase: {error}")
        else:
            logger.info(f"✅ User {user_id} diupdate jadi VIP di Supabase")
    except Exception as e:
        logger.error(f"Exception pas add VIP: {e}")

# === END FUNGSI VIP BARU ===


def escape_html(text):
    """Escape HTML special characters untuk mencegah break formatting"""
    if not text:
        return text
    return (text
            .replace('&', '&amp;')
            .replace("'", '&#39;'))


def get_movie_by_id(movie_id):
    """Fetch movie detail dari API berdasarkan movie_id"""
    try:
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


@bot.message_handler(commands=['start'])
def send_welcome(message):
    """Handler untuk command /start - kirim welcome message dengan banner dan Web App button"""
    logger.info(f"User {message.from_user.id} memulai bot")
    
    # === BARU: Panggil fungsi register/update user ===
    # Ini bakal bikin user baru + ref_code (kalo user baru)
    # atau cuma update username (kalo user lama)
    register_user(message.from_user.id, message.from_user.username)
    # =================================================

    # URL banner promosi Dramamu
    BANNER_URL = "https://geczfycekxkeiubbaijz.supabase.co/storage/v1/object/public/POSTER/banner-dramamu.jpg"
# ... (sisa fungsi send_welcome sama, tidak berubah) ...
    welcome_text = (
# ... (kode ini sama, tidak berubah) ...
    )
    
# ... (kode ini sama, tidak berubah) ...
    keyboard_markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
# ... (kode ini sama, tidak berubah) ...
    btn_cari_judul = types.KeyboardButton("🎬 CARI JUDUL", web_app=types.WebAppInfo(URL_CARI_JUDUL))
# ... (kode ini sama, tidak berubah) ...
    btn_cari_cuan = types.KeyboardButton("💰 CARI CUAN", web_app=types.WebAppInfo(URL_CARI_CUAN))
    
# ... (kode ini sama, tidak berubah) ...
    btn_beli_vip = types.KeyboardButton("💎 BELI VIP", web_app=types.WebAppInfo(URL_BELI_VIP))
# ... (kode ini sama, tidak berubah) ...
    btn_req_drama = types.KeyboardButton("📽 REQ DRAMA", web_app=types.WebAppInfo(URL_REQUEST))
    
# ... (kode ini sama, tidak berubah) ...
    btn_hubungi_kami = types.KeyboardButton("💬 HUBUNGI KAMI", web_app=types.WebAppInfo(URL_HUBUNGI_KAMI))
    
# ... (kode ini sama, tidak berubah) ...
    keyboard_markup.add(btn_cari_judul, btn_cari_cuan)
# ... (kode ini sama, tidak berubah) ...
    keyboard_markup.add(btn_beli_vip, btn_req_drama)
# ... (kode ini sama, tidak berubah) ...
    keyboard_markup.add(btn_hubungi_kami)
    
# ... (kode ini sama, tidak berubah) ...
    try:
# ... (kode ini sama, tidak berubah) ...
            BANNER_URL,
# ... (kode ini sama, tidak berubah) ...
            parse_mode='HTML',
# ... (kode ini sama, tidak berubah) ...
            reply_markup=keyboard_markup
        )
# ... (kode ini sama, tidak berubah) ...
    except Exception as e:
# ... (kode ini sama, tidak berubah) ...
        logger.error(f"Error sending banner: {e}")
# ... (kode ini sama, tidak berubah) ...
        bot.send_message(
# ... (kode ini sama, tidak berubah) ...
            parse_mode='HTML'
        )

# === FUNGSI REGISTER USER (YANG DIUPGRADE) ===
def register_user(user_id, username):
    """Fungsi baru untuk nyatet/update user di tabel 'users' + generate ref_code"""
    try:
        user_id_str = str(user_id)
        
        # 1. Cek dulu user udah ada apa belum
        response = supabase.table('users').select('ref_code').eq('telegram_id', user_id_str).execute()
        
        user_data = None
        if response.data:
            user_data = response.data[0]
        
        # 2. Siapkan data untuk di-upsert
        data_to_upsert = {
            'telegram_id': user_id_str,
            'username': username
            # 'last_seen': datetime.now().isoformat() # Kalo ada kolom last_seen
        }
        
        # 3. Kalo user BARU (data ngga ada), generate ref_code
        if not user_data:
            logger.info(f"User {user_id_str} baru, generate ref_code...")
            
            # === KODE DARI USER ===
            first_five = str(user_id_str)[:5]
            rand_part = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
            ref_code = f"ref{first_five}{rand_part}"
            # === END KODE ===
            
            data_to_upsert['ref_code'] = ref_code
            data_to_upsert['is_vip'] = False # Default pas register
        
        # 4. Lakukan UPSERT
        # Upsert = insert baru kalo 'telegram_id' ngga ada,
        # atau update 'username' kalo 'telegram_id' udah ada.
        # **WAJIB ASUMSI:** kolom 'telegram_id' di tabel 'users' lu itu UNIQUE
        data, error = supabase.table('users').upsert(
            data_to_upsert, 
            on_conflict='telegram_id' # Ini kunci utamanya
        ).execute()

        if error:
            logger.error(f"Error upsert user: {error}")
        else:
            logger.info(f"User {user_id_str} ({username}) datanya di-upsert.")
    
    except Exception as e:
        logger.error(f"Error register user: {e}")
# === END FUNGSI REGISTER ===


@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
# ... (kode ini sama, tidak berubah) ...
    try:
# ... (kode ini sama, tidak berubah) ...
        user_id = message.from_user.id
        
# ... (kode ini sama, tidak berubah) ...
            movie = get_movie_by_id(movie_id)
            
# ... (kode ini sama, tidak berubah) ...
                bot.send_message(message.chat.id, "❌ Film tidak ditemukan.")
# ... (kode ini sama, tidak berubah) ...
                return
            
            # Cek VIP dari Supabase (pake fungsi baru)
            if is_vip(user_id):
# ... (kode ini sama, tidak berubah) ...
                send_movie_to_vip(message.chat.id, movie)
            else:
# ... (kode ini sama, tidak berubah) ...
                send_non_vip_message(message.chat.id, movie)
        else:
# ... (kode ini sama, tidak berubah) ...
            bot.send_message(message.chat.id, "⚠️ Data tidak valid.")
            
# ... (kode ini sama, tidak berubah) ...
    except json.JSONDecodeError as e:
# ... (kode ini sama, tidak berubah) ...
        bot.send_message(message.chat.id, "❌ Error: Data tidak valid.")
# ... (kode ini sama, tidak berubah) ...
    except Exception as e:
# ... (kode ini sama, tidak berubah) ...
        bot.send_message(message.chat.id, "❌ Terjadi kesalahan.")


def send_movie_to_vip(chat_id, movie):
# ... (kode ini sama, tidak berubah) ...
    logger.info(f"✅ Mengirim film ke VIP user: {chat_id}")
# ... (kode ini sama, tidak berubah) ...
    logger.info(f"📸 Poster URL: {movie.get('poster_url', 'N/A')}")
    
# ... (kode ini sama, tidak berubah) ...
    safe_title = escape_html(movie.get('title', 'Unknown'))
# ... (kode ini sama, tidak berubah) ...
    safe_description = escape_html(movie.get('description', ''))
    
# ... (kode ini sama, tidak berubah) ...
    caption = (
# ... (kode ini sama, tidak berubah) ...
    )
    
# ... (kode ini sama, tidak berubah) ...
    markup = types.InlineKeyboardMarkup()
# ... (kode ini sama, tidak berubah) ...
    markup.row(
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("▶️ Tonton Sekarang", url=movie['video_link'])
    )
# ... (kode ini sama, tidak berubah) ...
    markup.row(
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("📥 Download", url=movie['video_link']),
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("🔗 Share", callback_data=f"share_{movie['id']}")
    )
    
# ... (kode ini sama, tidak berubah) ...
    try:
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"🔄 Mencoba kirim photo ke VIP chat_id: {chat_id}")
# ... (kode ini sama, tidak berubah) ...
            parse_mode='HTML',
# ... (kode ini sama, tidak berubah) ...
            reply_markup=markup
        )
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"✅ SUKSES! Photo terkirim ke VIP. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
# ... (kode ini sama, tidak berubah) ...
    except Exception as e:
# ... (kode ini sama, tidak berubah) ...
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
# ... (kode ini sama, tidak berubah) ...
            result = bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
# ... (kode ini sama, tidak berubah) ...
            logger.info(f"✅ SUKSES! Text message terkirim ke VIP. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
# ... (kode ini sama, tidak berubah) ...
        except Exception as fallback_error:
# ... (kode ini sama, tidak berubah) ...
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {type(fallback_error).__name__} - {str(fallback_error)}")


def send_non_vip_message(chat_id, movie):
# ... (kode ini sama, tidak berubah) ...
    logger.info(f"⚠️ User {chat_id} belum VIP, kirim pesan ajakan")
# ... (kode ini sama, tidak berubah) ...
    logger.info(f"📸 Poster URL: {movie.get('poster_url', 'N/A')}")
    
# ... (kode ini sama, tidak berubah) ...
    safe_title = escape_html(movie.get('title', 'Unknown'))
    
# ... (kode ini sama, tidak berubah) ...
    text = (
# ... (kode ini sama, tidak berubah) ...
    )
    
# ... (kode ini sama, tidak berubah) ...
    markup = types.InlineKeyboardMarkup()
# ... (kode ini sama, tidak berubah) ...
    markup.row(
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("⭐ Join VIP Sekarang", callback_data="join_vip")
    )
# ... (kode ini sama, tidak berubah) ...
    markup.row(
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("ℹ️ Info VIP", callback_data="info_vip"),
# ... (kode ini sama, tidak berubah) ...
        types.InlineKeyboardButton("🎬 Pilih Film Lain", callback_data="back_to_app")
    )
    
# ... (kode ini sama, tidak berubah) ...
    try:
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"🔄 Mencoba kirim photo ke chat_id: {chat_id}")
# ... (kode ini sama, tidak berubah) ...
            parse_mode='HTML',
# ... (kode ini sama, tidak berubah) ...
            reply_markup=markup
        )
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"✅ SUKSES! Photo terkirim. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
# ... (kode ini sama, tidak berubah) ...
    except Exception as e:
# ... (kode ini sama, tidak berubah) ...
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
# ... (kode ini sama, tidak berubah) ...
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
# ... (kode ini sama, tidak berubah) ...
            result = bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)
# ... (kode ini sama, tidak berubah) ...
            logger.info(f"✅ SUKSES! Text message terkirim. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
# ... (kode ini sama, tidak berubah) ...
        except Exception as fallback_error:
# ... (kode ini sama, tidak berubah) ...
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {type(fallback_error).__name__} - {str(fallback_error)}")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
# ... (kode ini sama, tidak berubah) ...
    logger.info(f"Callback received: {call.data} from user {call.from_user.id}")
    
# ... (kode ini sama, tidak berubah) ...
        has_photo = call.message.photo is not None and len(call.message.photo) > 0
        
# ... (kode ini sama, tidak berubah) ...
        if call.data == "join_vip":
# ... (kode ini sama, tidak berubah) ...
            text = (
# ... (kode ini sama, tidak berubah) ...
            )
            
# ... (kode ini sama, tidak berubah) ...
            markup = types.InlineKeyboardMarkup()
# ... (kode ini sama, tidak berubah) ...
            markup.row(
# ... (kode ini sama, tidak berubah) ...
                types.InlineKeyboardButton("💳 Beli VIP Sekarang", web_app=types.WebAppInfo(URL_BELI_VIP))
            )
# ... (kode ini sama, tidak berubah) ...
            markup.row(
# ... (kode ini sama, tidak berubah) ...
                types.InlineKeyboardButton("ℹ️ Info Lebih Lanjut", callback_data="info_vip"),
# ... (kode ini sama, tidak berubah) ...
                types.InlineKeyboardButton("« Kembali", callback_data="back_to_app")
            )
            
# ... (kode ini sama, tidak berubah) ...
                if has_photo:
# ... (kode ini sama, tidak berubah) ...
                    bot.edit_message_caption(
# ... (kode ini sama, tidak berubah) ...
                    )
                else:
# ... (kode ini sama, tidak berubah) ...
                    bot.edit_message_text(
# ... (kode ini sama, tidak berubah) ...
                    )
# ... (kode ini sama, tidak berubah) ...
                if "message is not modified" not in str(e):
# ... (kode ini sama, tidak berubah) ...
                    logger.error(f"Error editing message: {e}")
            
# ... (kode ini sama, tidak berubah) ...
            bot.answer_callback_query(call.id)
# ... (kode ini sama, tidak berubah) ...
            return
            
# ... (kode ini sama, tidak berubah) ...
        elif call.data == "info_vip":
# ... (kode ini sama, tidak berubah) ...
            text = (
# ... (kode ini sama, tidak berubah) ...
            )
            
# ... (kode ini sama, tidak berubah) ...
            markup = types.InlineKeyboardMarkup()
# ... (kode ini sama, tidak berubah) ...
            markup.row(
# ... (kode ini sama, tidak berubah) ...
                types.InlineKeyboardButton("⭐ Join VIP", callback_data="join_vip")
            )
            
# ... (kode ini sama, tidak berubah) ...
                if has_photo:
# ... (kode ini sama, tidak berubah) ...
                    bot.edit_message_caption(
# ... (kode ini sama, tidak berubah) ...
                    )
                else:
# ... (kode ini sama, tidak berubah) ...
                    bot.edit_message_text(
# ... (kode ini sama, tidak berubah) ...
                    )
# ... (kode ini sama, tidak berubah) ...
                if "message is not modified" not in str(e):
# ... (kode ini sama, tidak berubah) ...
                    logger.error(f"Error editing message: {e}")
            
# ... (kode ini sama, tidak berubah) ...
            bot.answer_callback_query(call.id)
# ... (kode ini sama, tidak berubah) ...
            return
            
# ... (kode ini sama, tidak berubah) ...
        elif call.data == "back_to_app":
# ... (kode ini sama, tidak berubah) ...
            bot.answer_callback_query(call.id, "Klik tombol '🎬 Cari Judul' di bawah untuk membuka Mini App lagi")
# ... (kode ini sama, tidak berubah) ...
            return
            
# ... (kode ini sama, tidak berubah) ...
        elif call.data.startswith("share_"):
# ... (kode ini sama, tidak berubah) ...
            movie_id = call.data.split("_")[1]
# ... (kode ini sama, tidak berubah) ...
            bot.answer_callback_query(call.id, "🔗 Link film berhasil disalin!")
# ... (kode ini sama, tidak berubah) ...
            return
        
# ... (kode ini sama, tidak berubah) ...
        bot.answer_callback_query(call.id)
        
# ... (kode ini sama, tidak berubah) ...
    except Exception as e:
# ... (kode ini sama, tidak berubah) ...
        logger.error(f"Error handling callback query: {e}")
# ... (kode ini sama, tidak berubah) ...
            pass


@bot.message_handler(commands=['addvip'])
def add_vip_command(message):
    """Command untuk menambahkan user ke VIP (untuk testing)"""
    user_id = message.from_user.id
    # Panggil fungsi add_vip yang baru (pake tabel 'users')
    add_vip(user_id, message.from_user.username)
    bot.reply_to(message, "✅ Anda sekarang adalah member VIP!\n\nSilakan pilih film lagi.")


@bot.message_handler(commands=['removevip'])
def remove_vip_command(message):
    """Command untuk remove VIP status dari Supabase (set is_vip=False)"""
    user_id_str = str(message.from_user.id)
    try:
        # NAMA TABEL: 'users', NAMA KOLOM: 'telegram_id'
        # Kita set is_vip jadi False
        data, error = supabase.table('users').update({
            'is_vip': False,
            'vip_expires_at': None # Set tgl kadaluarsa jadi null
        }).eq('telegram_id', user_id_str).execute()
        
        if error:
            logger.error(f"Error remove VIP: {error}")
            bot.reply_to(message, "⚠️ Terjadi error pas hapus data.")
        # Cek data[1] (list hasil) untuk tau beneran ada yg diupdate apa ngga
        elif data and len(data[1]) > 0:
            logger.info(f"User {user_id_str} status VIP-nya di-set False")
            bot.reply_to(message, "❌ VIP status Anda telah dihapus.")
        else:
            logger.info(f"User {user_id_str} ngga ada di database.")
            bot.reply_to(message, "⚠️ User tidak ditemukan.")
    
    except Exception as e:
        logger.error(f"Exception pas remove VIP: {e}")
        bot.reply_to(message, "❌ Terjadi kesalahan.")


@bot.message_handler(commands=['checkvip'])
def check_vip_command(message):
    """Command untuk cek status VIP"""
    user_id = message.from_user.id
    
    # Panggil fungsi is_vip yang baru (pake tabel 'users')
    if is_vip(user_id):
        bot.reply_to(message, "✅ Anda adalah member VIP!")
    else:
        bot.reply_to(message, "❌ Anda belum menjadi member VIP.")


if __name__ == '__main__':
# ... (kode ini sama, tidak berubah) ...
    logger.info("🤖 Bot memulai polling...")
# ... (kode ini sama, tidak berubah) ...
    logger.info("✅ Bot layout updated - Welcome message & inline keyboards fixed!")
# ... (kode ini sama, tidak berubah) ...
    bot.infinity_polling()



