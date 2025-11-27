"""
Payment Configuration Service

Centralized service untuk membaca dan memvalidasi konfigurasi payment gateway.
Digunakan oleh create_payment endpoint untuk mendukung multiple payment gateways.
"""

import os
import json
import logging
from typing import Tuple, Dict, Any, Optional

from database import SessionLocal, Settings
from config import QRIS_PW_API_KEY, QRIS_PW_API_SECRET, DOKU_CLIENT_ID, DOKU_SECRET_KEY

logger = logging.getLogger(__name__)

DEFAULT_PAYMENT_CONFIG = {
    "active_gateway": "qrispw",
    "gateways": {
        "qrispw": {
            "enabled": True,
            "api_key": "",
            "api_secret": "",
            "api_url": "https://qris.pw/api"
        },
        "qris-interactive": {
            "enabled": False,
            "amounts": []
        },
        "doku": {
            "enabled": False,
            "client_id": "",
            "secret_key": "",
            "environment": "sandbox"
        },
        "midtrans": {
            "enabled": False,
            "server_key": "",
            "client_key": "",
            "merchant_id": "",
            "environment": "sandbox"
        }
    }
}

SUPPORTED_GATEWAYS = ["qrispw", "qris-interactive", "doku", "midtrans"]

def get_payment_config() -> Dict[str, Any]:
    """
    Read payment config from Settings table.
    
    Returns:
        dict: Payment configuration, returns default if not exists or invalid JSON
    """
    db = SessionLocal()
    try:
        config_setting = db.query(Settings).filter(Settings.key == 'payment_config').first()
        
        if config_setting is not None:
            value_str = str(config_setting.value) if config_setting.value else ""
            if value_str:
                try:
                    config = json.loads(value_str)
                    if not isinstance(config.get("gateways"), dict):
                        config["gateways"] = DEFAULT_PAYMENT_CONFIG["gateways"]
                    if not config.get("active_gateway"):
                        config["active_gateway"] = DEFAULT_PAYMENT_CONFIG["active_gateway"]
                    return config
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON in payment_config setting, using default")
                    return DEFAULT_PAYMENT_CONFIG.copy()
        return DEFAULT_PAYMENT_CONFIG.copy()
    except Exception as e:
        logger.error(f"Error reading payment config from database: {e}")
        return DEFAULT_PAYMENT_CONFIG.copy()
    finally:
        db.close()


def get_active_gateway() -> str:
    """
    Returns the active gateway name.
    
    Returns:
        str: One of 'qrispw', 'qris-interactive', 'doku', 'midtrans'
    """
    config = get_payment_config()
    active = config.get("active_gateway", "qrispw")
    
    if active not in SUPPORTED_GATEWAYS:
        logger.warning(f"Unknown gateway '{active}', falling back to 'qrispw'")
        return "qrispw"
    
    return active


def get_gateway_settings(gateway_name: str) -> Dict[str, Any]:
    """
    Get settings for specific gateway.
    
    Args:
        gateway_name: Name of the gateway (qrispw, qris-interactive, doku, midtrans)
    
    Returns:
        dict: Gateway-specific settings, empty dict if gateway not found
    """
    config = get_payment_config()
    gateways = config.get("gateways", {})
    
    return gateways.get(gateway_name, {})


def is_gateway_ready(gateway_name: str) -> Tuple[bool, str]:
    """
    Check if gateway is properly configured (credentials set, etc.)
    
    Args:
        gateway_name: Name of the gateway to check
    
    Returns:
        tuple: (is_ready: bool, error_message: str)
               If ready, error_message is empty string
    """
    if gateway_name not in SUPPORTED_GATEWAYS:
        return False, f"Gateway '{gateway_name}' tidak dikenal"
    
    settings = get_gateway_settings(gateway_name)
    
    if gateway_name == "qrispw":
        api_key = QRIS_PW_API_KEY or settings.get("api_key", "")
        api_secret = QRIS_PW_API_SECRET or settings.get("api_secret", "")
        
        if not api_key or not api_secret:
            return False, "QRIS.PW belum dikonfigurasi. Hubungi admin."
        return True, ""
    
    elif gateway_name == "qris-interactive":
        qris_dir = "frontend/assets/qris"
        
        if not os.path.exists(qris_dir):
            return False, "Gambar QRIS tidak tersedia. Hubungi admin."
        
        available_amounts = get_available_qris_amounts()
        if not available_amounts:
            return False, "Belum ada nominal QRIS yang tersedia. Hubungi admin."
        
        return True, ""
    
    elif gateway_name == "doku":
        return False, "Gateway DOKU belum tersedia. Silakan gunakan metode pembayaran lain."
    
    elif gateway_name == "midtrans":
        return False, "Gateway Midtrans belum tersedia. Silakan gunakan metode pembayaran lain."
    
    return False, "Gateway tidak dikenal"


def get_available_qris_amounts() -> list:
    """
    Get list of available QRIS amounts from the qris images directory.
    
    Returns:
        list: List of available amounts (integers), sorted ascending
    """
    import re
    
    qris_dir = "frontend/assets/qris"
    amounts = []
    
    if os.path.exists(qris_dir):
        for filename in os.listdir(qris_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                match = re.match(r'^(\d+)\.(png|jpg|jpeg)$', filename.lower())
                if match:
                    amounts.append(int(match.group(1)))
    
    amounts.sort()
    return amounts


def get_qris_image_url(amount: int) -> Optional[str]:
    """
    Get the QRIS image URL for a specific amount.
    
    Args:
        amount: Payment amount to find QRIS image for
    
    Returns:
        str: URL path to QRIS image, or None if not found
    """
    qris_dir = "frontend/assets/qris"
    
    possible_extensions = ['.png', '.jpg', '.jpeg', '.PNG', '.JPG', '.JPEG']
    
    for ext in possible_extensions:
        filename = f"{amount}{ext}"
        filepath = os.path.join(qris_dir, filename)
        if os.path.exists(filepath):
            return f"/qris/{amount}{ext.lower()}"
    
    return None


def get_public_config() -> Dict[str, Any]:
    """
    Get payment configuration safe for public exposure (no secrets).
    Used by frontend to know active gateway and available options.
    
    Returns:
        dict: Public-safe payment configuration
    """
    active_gateway = get_active_gateway()
    is_ready, error = is_gateway_ready(active_gateway)
    
    result = {
        "active_gateway": active_gateway,
        "is_ready": is_ready,
        "error": error if not is_ready else None
    }
    
    if active_gateway == "qris-interactive":
        amounts = get_available_qris_amounts()
        result["qris_amounts"] = amounts
        result["qris_images"] = [
            {"amount": amt, "url": get_qris_image_url(amt)}
            for amt in amounts
        ]
    
    return result
