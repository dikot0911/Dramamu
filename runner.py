import os
import sys
import threading
import logging
from multiprocessing import Process
import time

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_fastapi():
    """Jalanin server FastAPI"""
    import uvicorn
    port = int(os.getenv('PORT', 5000))
    logger.info(f"🚀 Starting FastAPI server on port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")

def launch_bot_process():
    """Jalanin bot Telegram"""
    logger.info("🤖 Starting Telegram bot...")
    time.sleep(2)
    from bot import run_bot
    run_bot()

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🎬 DRAMAMU BOT - Starting All Services")
    logger.info("=" * 50)
    
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    
    fastapi_process = Process(target=run_fastapi)
    bot_process = None
    
    try:
        fastapi_process.start()
        logger.info("✅ FastAPI server started")
        
        if telegram_token:
            bot_process = Process(target=launch_bot_process)
            bot_process.start()
            logger.info("✅ Telegram bot process started")
        else:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN not set - Telegram bot will NOT start")
            logger.warning("⚠️ Only FastAPI server and Admin Panel will be available")
        
        fastapi_process.join()
        if bot_process:
            bot_process.join()
        
    except KeyboardInterrupt:
        logger.info("⚠️ Shutting down...")
        if bot_process:
            bot_process.terminate()
        fastapi_process.terminate()
        if bot_process:
            bot_process.join(timeout=5)
        fastapi_process.join(timeout=5)
        logger.info("✅ All services stopped")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        if bot_process:
            bot_process.terminate()
        fastapi_process.terminate()
        if bot_process:
            bot_process.join(timeout=5)
        fastapi_process.join(timeout=5)
