import threading
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class BotState:
    """
    Shared state for bot health monitoring dan supervision.
    
    This module is imported by:
    - runner.py: Sets state and manages bot thread
    - bot.py: Checks shutdown flag and signals when started
    - main.py: Reads state for health endpoint
    
    Architecture: Shared singleton to avoid circular imports.
    """
    def __init__(self):
        self.thread: Optional[threading.Thread] = None
        self.started = threading.Event()
        self.failed = threading.Event()
        self.shutdown = threading.Event()
        self.error_message: Optional[str] = None
    
    def is_healthy(self) -> bool:
        """
        Bot considered healthy if:
        1. Started successfully (started flag set)
        2. Not failed (failed flag not set)
        3. Thread still alive (if thread exists)
        """
        if not self.started.is_set():
            return False
        if self.failed.is_set():
            return False
        if self.thread and not self.thread.is_alive():
            return False
        return True
    
    def signal_started(self):
        """Called by bot.py when polling actually starts"""
        self.started.set()
        logger.info("✅ BotState: Bot marked as started")
    
    def signal_failed(self, error_message: str):
        """Called by bot.py or runner.py when bot fails"""
        self.failed.set()
        self.error_message = error_message
        logger.error(f"❌ BotState: Bot marked as failed: {error_message}")
    
    def signal_shutdown(self):
        """Called by runner.py when shutdown requested"""
        self.shutdown.set()
        logger.info("🛑 BotState: Shutdown signal sent")
    
    def should_shutdown(self) -> bool:
        """Called by bot.py to check if shutdown requested"""
        return self.shutdown.is_set()

bot_state = BotState()
