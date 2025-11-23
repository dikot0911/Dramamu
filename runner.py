import os
import sys
import threading
import logging
import time
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from bot_state import bot_state

def run_telegram_bot():
    """
    Run Telegram bot di background thread.
    Reports health status dan handles graceful shutdown.
    
    Catatan: bot_state.started di-set oleh bot.py waktu polling mulai jalan,
    bukan di sini. Ini biar deteksi startup lebih akurat.
    """
    try:
        logger.info("🤖 Starting Telegram bot thread...")
        time.sleep(2)  # Give FastAPI time to initialize first
        
        from bot import run_bot
        
        logger.info("🔄 Calling run_bot() - bot will signal when ready...")
        run_bot()
        
        logger.info("✅ run_bot() exited normally")
        
    except Exception as e:
        logger.error(f"❌ Telegram bot thread crashed: {e}")
        logger.exception("Bot error details:")
        bot_state.signal_failed(str(e))
        
        # Kalau bot gagal start (dalam 30 detik pertama), exit process
        # biar Render restart service otomatis
        if not bot_state.started.is_set():
            logger.critical("❌ Bot failed to start - terminating main process")
            logger.critical("   Render will restart the service automatically")
            os._exit(1)  # Force exit entire process

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"🛑 Received signal {signum}, initiating graceful shutdown...")
    bot_state.signal_shutdown()
    # Give threads time to shutdown
    time.sleep(2)

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🎬 DRAMAMU BOT - Starting All Services")
    logger.info("=" * 60)
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # VALIDATION: Check critical environment variables
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    database_url = os.getenv('DATABASE_URL', '').strip()
    port = int(os.getenv('PORT', 5000))
    is_render = bool(os.getenv('RENDER'))
    
    # Validate config production
    if is_render:
        logger.info("🌐 PRODUCTION MODE (Render)")
        
        # Check database
        if not database_url or 'sqlite' in database_url.lower():
            logger.critical("❌ FATAL: DATABASE_URL tidak di-set atau pakai SQLite!")
            logger.critical("   Production HARUS pakai PostgreSQL (Supabase)")
            logger.critical("   Set DATABASE_URL di Render environment variables")
            sys.exit(1)
        
        # Check admin credentials
        if not os.getenv('ADMIN_USERNAME') or not os.getenv('ADMIN_PASSWORD'):
            logger.warning("⚠️  ADMIN_USERNAME/PASSWORD tidak di-set")
            logger.warning("   Admin panel akan disabled")
        
        if not os.getenv('JWT_SECRET_KEY'):
            logger.warning("⚠️  JWT_SECRET_KEY tidak di-set")
            logger.warning("   Admin panel akan disabled")
    else:
        logger.info("🔧 DEVELOPMENT MODE (Local)")
    
    # PRODUCTION MODE: Skip bot thread, webhook akan disetup oleh FastAPI startup
    # DEVELOPMENT MODE: Jalankan bot dengan polling
    if telegram_token and not is_render:
        logger.info("🔧 Starting bot dengan POLLING (development mode)...")
        bot_state.thread = threading.Thread(target=run_telegram_bot, daemon=False, name="TelegramBot")
        bot_state.thread.start()
        logger.info("✅ Telegram bot thread launched, waiting for startup...")
        
        # Wait for bot to start (max 30 seconds)
        if not bot_state.started.wait(timeout=30):
            logger.critical("❌ Telegram bot failed to start within 30 seconds")
            logger.critical("   Terminating main process")
            sys.exit(1)
        
        logger.info("✅ Telegram bot confirmed healthy")
    elif telegram_token and is_render:
        logger.info("🌐 Bot akan disetup dengan WEBHOOK (production mode)")
        logger.info("   Webhook dikonfigurasi oleh FastAPI startup event")
        logger.info("   ⏭️  Skipping polling thread")
    else:
        if is_render:
            logger.critical("❌ FATAL: TELEGRAM_BOT_TOKEN tidak di-set!")
            logger.critical("   Set TELEGRAM_BOT_TOKEN di Render environment variables")
            sys.exit(1)
        else:
            logger.warning("⚠️  TELEGRAM_BOT_TOKEN not set - Bot will NOT start")
            logger.warning("⚠️  Only FastAPI server and Admin Panel will be available")
    
    # PRODUCTION-READY: FastAPI runs in MAIN PROCESS
    # This ensures Render health checks work correctly
    # If this process dies, Render will detect and restart the service
    try:
        import uvicorn
        
        if is_render:
            logger.info(f"🚀 Starting FastAPI on Render (port {port}, MAIN PROCESS)...")
            logger.info("✅ Production mode: Health checks will reflect actual API status")
            logger.info("✅ Bot supervision: Enabled")
            # Production: Use main process for FastAPI (Render expects this)
            uvicorn.run(
                "main:app", 
                host="0.0.0.0", 
                port=port, 
                log_level="info",
                access_log=True,
                # Jangan pake workers di multiprocessing mode
                # Health check Render monitor process ini
            )
        else:
            logger.info(f"🚀 Starting FastAPI locally (port {port}, MAIN PROCESS)...")
            # Development: Standard config
            uvicorn.run(
                "main:app", 
                host="0.0.0.0", 
                port=port, 
                log_level="info"
            )
    except KeyboardInterrupt:
        logger.info("⚠️  Shutting down gracefully...")
        bot_state.signal_shutdown()
    except Exception as e:
        logger.error(f"❌ FastAPI crashed: {e}")
        logger.exception("Error details:")
        sys.exit(1)
    finally:
        # Graceful shutdown: Signal bot to stop
        logger.info("🛑 Initiating shutdown sequence...")
        bot_state.signal_shutdown()
        
        # Wait for bot thread to finish if it's running
        if bot_state.thread and bot_state.thread.is_alive():
            logger.info("Waiting for Telegram bot to stop...")
            bot_state.thread.join(timeout=5)
            if bot_state.thread.is_alive():
                logger.warning("⚠️  Bot thread did not stop gracefully")
        
        logger.info("✅ All services stopped")
        