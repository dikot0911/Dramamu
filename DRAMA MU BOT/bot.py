import os
import json
import logging
import requests
from datetime import datetime
from telebot import TeleBot, types
from telebot.apihelper import ApiTelegramException

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
MINIAPP_URL = os.getenv('MINIAPP_URL', 'https://dramamuid.netlify.app')
API_BASE_URL = 'https://dramamu-api.onrender.com/api/v1'

if MINIAPP_URL.endswith('/drama'):
    BASE_URL = MINIAPP_URL.rsplit('/drama', 1)[0]
else:
    BASE_URL = MINIAPP_URL.rsplit('/', 1)[0] if '/' in MINIAPP_URL.split('://')[-1] else MINIAPP_URL

URL_CARI_JUDUL = f"{BASE_URL}/drama.html"
URL_BELI_VIP = f"{BASE_URL}/payment.html"
URL_PROFILE = f"{BASE_URL}/profile.html"
URL_REQUEST = f"{BASE_URL}/request.html"
URL_REFERRAL = f"{BASE_URL}/referal.html"

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("⚠️ TELEGRAM_BOT_TOKEN tidak ditemukan! Silakan set di environment variables.")

bot = TeleBot(TELEGRAM_BOT_TOKEN)

VIP_USERS_FILE = 'vip_users.json'


def load_vip_users():
    """Load VIP users dari file JSON"""
    try:
        if os.path.exists(VIP_USERS_FILE):
            with open(VIP_USERS_FILE, 'r') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Error loading VIP users: {e}")
        return {}


def save_vip_users(vip_data):
    """Save VIP users ke file JSON"""
    try:
        with open(VIP_USERS_FILE, 'w') as f:
            json.dump(vip_data, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving VIP users: {e}")


def is_vip(user_id):
    """Cek apakah user adalah VIP"""
    vip_users = load_vip_users()
    return str(user_id) in vip_users


def add_vip(user_id, username=None):
    """Tambahkan user ke VIP"""
    vip_users = load_vip_users()
    vip_users[str(user_id)] = {
        'username': username,
        'vip_since': datetime.now().isoformat()
    }
    save_vip_users(vip_users)
    logger.info(f"✅ User {user_id} ditambahkan ke VIP")


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
    """Handler untuk command /start - kirim welcome message dengan Web App button"""
    logger.info(f"User {message.from_user.id} memulai bot")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    btn_cari = types.KeyboardButton("🎬 Cari Judul", web_app=types.WebAppInfo(URL_CARI_JUDUL))
    btn_vip = types.KeyboardButton("⭐ Beli VIP", web_app=types.WebAppInfo(URL_BELI_VIP))
    btn_profile = types.KeyboardButton("👤 Profile", web_app=types.WebAppInfo(URL_PROFILE))
    btn_request = types.KeyboardButton("📝 Request Film", web_app=types.WebAppInfo(URL_REQUEST))
    btn_referral = types.KeyboardButton("🎁 Referral", web_app=types.WebAppInfo(URL_REFERRAL))
    
    markup.add(btn_cari, btn_vip)
    markup.add(btn_profile, btn_request)
    markup.add(btn_referral)
    
    welcome_text = (
        f"👋 Halo {message.from_user.first_name}!\n\n"
        "Selamat datang di Dramamu Bot 🎥\n\n"
        "Pilih menu di bawah untuk mulai:\n\n"
        "🎬 <b>Cari Judul</b> - Lihat daftar film\n"
        "⭐ <b>Beli VIP</b> - Upgrade ke member VIP\n"
        "👤 <b>Profile</b> - Lihat profil Anda\n"
        "📝 <b>Request Film</b> - Request film favorit\n"
        "🎁 <b>Referral</b> - Dapatkan bonus referral"
    )
    
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup, parse_mode='HTML')


@bot.message_handler(content_types=['web_app_data'])
def handle_web_app_data(message):
    """
    INI HANDLER YANG PALING PENTING!
    Handler untuk menangkap data dari Mini App ketika user klik film
    """
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


def send_movie_to_vip(chat_id, movie):
    """Kirim film dengan inline keyboard untuk user VIP"""
    logger.info(f"✅ Mengirim film ke VIP user: {chat_id}")
    logger.info(f"📸 Poster URL: {movie.get('poster_url', 'N/A')}")
    
    caption = (
        f"🎬 <b>{movie['title']}</b>\n\n"
        f"{movie['description']}\n\n"
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
        result = bot.send_photo(
            chat_id, 
            movie['poster_url'], 
            caption=caption,
            parse_mode='HTML',
            reply_markup=markup
        )
        logger.info(f"✅ SUKSES! Photo terkirim ke VIP. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
    except Exception as e:
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
        try:
            result = bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)
            logger.info(f"✅ SUKSES! Text message terkirim ke VIP. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
        except Exception as fallback_error:
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {type(fallback_error).__name__} - {str(fallback_error)}")


def send_non_vip_message(chat_id, movie):
    """Kirim pesan 'belum VIP' dengan inline keyboard"""
    logger.info(f"⚠️ User {chat_id} belum VIP, kirim pesan ajakan")
    logger.info(f"📸 Poster URL: {movie.get('poster_url', 'N/A')}")
    
    text = (
        f"🔒 <b>{movie['title']}</b>\n\n"
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
        result = bot.send_photo(
            chat_id,
            movie['poster_url'],
            caption=text,
            parse_mode='HTML',
            reply_markup=markup
        )
        logger.info(f"✅ SUKSES! Photo terkirim. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
    except Exception as e:
        logger.error(f"❌ Error sending photo: {type(e).__name__} - {str(e)}")
        logger.info(f"🔄 Mencoba fallback: kirim text message tanpa photo")
        try:
            result = bot.send_message(chat_id, text, parse_mode='HTML', reply_markup=markup)
            logger.info(f"✅ SUKSES! Text message terkirim. Message ID: {result.message_id}, Chat ID: {result.chat.id}")
        except Exception as fallback_error:
            logger.error(f"❌❌ GAGAL TOTAL! Fallback message gagal: {type(fallback_error).__name__} - {str(fallback_error)}")


@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    """Handler untuk inline keyboard button callbacks"""
    logger.info(f"Callback received: {call.data} from user {call.from_user.id}")
    
    try:
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
                bot.edit_message_caption(
                    caption=text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=markup
                )
            except ApiTelegramException as e:
                if "message is not modified" not in str(e):
                    bot.edit_message_text(
                        text=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            
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
                bot.edit_message_caption(
                    caption=text,
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    parse_mode='HTML',
                    reply_markup=markup
                )
            except ApiTelegramException as e:
                if "message is not modified" not in str(e):
                    bot.edit_message_text(
                        text=text,
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        parse_mode='HTML',
                        reply_markup=markup
                    )
            
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


@bot.message_handler(commands=['addvip'])
def add_vip_command(message):
    """Command untuk menambahkan user ke VIP (untuk testing)"""
    user_id = message.from_user.id
    add_vip(user_id, message.from_user.username)
    bot.reply_to(message, "✅ Anda sekarang adalah member VIP!\n\nSilakan pilih film lagi.")


@bot.message_handler(commands=['removevip'])
def remove_vip_command(message):
    """Command untuk remove VIP status (untuk testing)"""
    vip_users = load_vip_users()
    user_id = str(message.from_user.id)
    
    if user_id in vip_users:
        del vip_users[user_id]
        save_vip_users(vip_users)
        bot.reply_to(message, "❌ VIP status Anda telah dihapus.")
    else:
        bot.reply_to(message, "⚠️ Anda tidak memiliki status VIP.")


@bot.message_handler(commands=['checkvip'])
def check_vip_command(message):
    """Command untuk cek status VIP"""
    user_id = message.from_user.id
    
    if is_vip(user_id):
        bot.reply_to(message, "✅ Anda adalah member VIP!")
    else:
        bot.reply_to(message, "❌ Anda belum menjadi member VIP.")


if __name__ == '__main__':
    logger.info("🤖 Bot dimulai...")
    logger.info(f"📱 BASE URL: {BASE_URL}")
    logger.info(f"🎬 Cari Judul: {URL_CARI_JUDUL}")
    logger.info(f"⭐ Beli VIP: {URL_BELI_VIP}")
    logger.info(f"👤 Profile: {URL_PROFILE}")
    logger.info(f"📝 Request: {URL_REQUEST}")
    logger.info(f"🎁 Referral: {URL_REFERRAL}")
    bot.infinity_polling()
    
