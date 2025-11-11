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
    logger.info("🚀 Starting FastAPI server on port 5000...")
    uvicorn.run("main:app", host="0.0.0.0", port=5000, log_level="info")

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
    
    fastapi_process = Process(target=run_fastapi)
    bot_process = Process(target=launch_bot_process)
    
    try:
        fastapi_process.start()
        logger.info("✅ FastAPI server started")
        
        bot_process.start()
        logger.info("✅ Telegram bot started")
        
        fastapi_process.join()
        bot_process.join()
        
    except KeyboardInterrupt:
        logger.info("⚠️ Shutting down...")
        bot_process.terminate()
        fastapi_process.terminate()
        bot_process.join(timeout=5)
        fastapi_process.join(timeout=5)
        logger.info("✅ All services stopped")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        bot_process.terminate()
        fastapi_process.terminate()
        bot_process.join(timeout=5)
        fastapi_process.join(timeout=5)
