"""
Payment Sync Worker - Background task untuk sync pending payments dengan QRIS.PW

TUJUAN:
- Memastikan VIP otomatis aktif meskipun webhook gagal
- Fallback mechanism jika QRIS.PW webhook tidak sampai ke server
- Menghandle cold start issue pada Render free tier

CARA KERJA:
1. Background task berjalan setiap SYNC_INTERVAL detik
2. Ambil semua payment pending yang dibuat dalam SYNC_LOOKBACK_HOURS terakhir
3. Cek status masing-masing ke QRIS.PW API
4. Update payment dan aktifkan VIP jika sudah paid
"""

import logging
import asyncio
import threading
import time
import requests
import hmac
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from config import QRIS_PW_API_KEY, QRIS_PW_API_SECRET, QRIS_PW_API_URL, now_utc, is_production
from database import SessionLocal, User, Payment
from payment_processing import extend_vip_atomic
from referral_utils import process_referral_commission, send_referrer_notification

logger = logging.getLogger(__name__)

SYNC_INTERVAL = 30
SYNC_LOOKBACK_HOURS = 24
MAX_PENDING_TO_SYNC = 50


class PaymentSyncWorker:
    """
    Background worker untuk sync pending payments dengan QRIS.PW
    """
    
    def __init__(self, bot=None):
        self.bot = bot
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.sync_count = 0
        self.last_sync_time: Optional[datetime] = None
        self.payments_synced = 0
        self.payments_activated = 0
        
    def start(self):
        """Start background sync worker"""
        if self.running:
            logger.warning("PaymentSyncWorker already running")
            return
            
        if not (QRIS_PW_API_KEY and QRIS_PW_API_SECRET):
            logger.warning("QRIS.PW credentials not configured - PaymentSyncWorker disabled")
            return
            
        self.running = True
        self.thread = threading.Thread(
            target=self._run_sync_loop,
            daemon=True,
            name="PaymentSyncWorker"
        )
        self.thread.start()
        logger.info(f"✅ PaymentSyncWorker started (interval: {SYNC_INTERVAL}s)")
        
    def stop(self):
        """Stop background sync worker"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        logger.info("PaymentSyncWorker stopped")
        
    def _run_sync_loop(self):
        """Main sync loop - berjalan di background thread"""
        logger.info("🔄 PaymentSyncWorker loop started")
        
        time.sleep(10)
        
        while self.running:
            try:
                self._sync_pending_payments()
                self.last_sync_time = datetime.now()
                self.sync_count += 1
                
            except Exception as e:
                logger.error(f"❌ Error in PaymentSyncWorker: {e}")
                logger.exception("Sync worker error details:")
                
            time.sleep(SYNC_INTERVAL)
            
    def _sync_pending_payments(self):
        """Sync semua pending payments dengan QRIS.PW API"""
        db = SessionLocal()
        try:
            cutoff_time = now_utc() - timedelta(hours=SYNC_LOOKBACK_HOURS)
            
            pending_payments = db.query(Payment).filter(
                Payment.status == 'pending',
                Payment.created_at >= cutoff_time,
                Payment.transaction_id.isnot(None)
            ).order_by(Payment.created_at.desc()).limit(MAX_PENDING_TO_SYNC).all()
            
            if not pending_payments:
                return
                
            logger.info(f"🔄 Syncing {len(pending_payments)} pending payments...")
            
            for payment in pending_payments:
                try:
                    self._check_and_process_payment(db, payment)
                    self.payments_synced += 1
                except Exception as e:
                    logger.error(f"Error syncing payment {payment.transaction_id}: {e}")
                    
        finally:
            db.close()
            
    def _check_and_process_payment(self, db, payment: Payment):
        """Check single payment status dari QRIS.PW dan process jika paid"""
        transaction_id = payment.transaction_id
        
        if db.query(Payment).filter(
            Payment.id == payment.id,
            Payment.status != 'pending'
        ).first():
            return
            
        headers = {
            "X-API-Key": QRIS_PW_API_KEY,
            "X-API-Secret": QRIS_PW_API_SECRET
        }
        
        try:
            api_url = f"{QRIS_PW_API_URL}/check-payment.php"
            response = requests.get(
                api_url,
                params={"transaction_id": transaction_id},
                headers=headers,
                timeout=10
            )
            
            if response.status_code != 200:
                logger.warning(f"QRIS.PW API error for {transaction_id}: {response.status_code}")
                return
                
            result = response.json()
            
            if not result.get("success"):
                return
                
            payment_status = result.get("status")
            
            if payment_status == 'paid':
                self._activate_vip_for_payment(db, payment)
                
            elif payment_status == 'expired':
                payment.status = 'expired'
                db.commit()
                logger.info(f"⏰ Payment marked expired via sync: {transaction_id}")
                
        except requests.RequestException as e:
            logger.warning(f"Network error checking {transaction_id}: {e}")
            
    def _activate_vip_for_payment(self, db, payment: Payment):
        """Activate VIP untuk payment yang sudah paid"""
        from vip_packages import validate_package_name
        
        fresh_payment = db.query(Payment).filter(Payment.id == payment.id).with_for_update().first()
        
        if not fresh_payment or str(fresh_payment.status) != 'pending':
            logger.info(f"⏭️ Payment {payment.transaction_id} already processed, skipping")
            return
            
        package_name_str = str(fresh_payment.package_name)
        valid, days, error = validate_package_name(package_name_str)
        
        if not valid or days is None:
            logger.error(f"❌ Invalid package name '{package_name_str}' for payment {fresh_payment.order_id}")
            fresh_payment.status = 'manual_review'
            db.commit()
            return
            
        fresh_payment.status = 'success'
        fresh_payment.paid_at = now_utc()
        
        user = db.query(User).filter(
            User.telegram_id == fresh_payment.telegram_id,
            User.deleted_at == None
        ).first()
        
        if not user:
            logger.error(f"❌ User not found for payment: telegram_id={fresh_payment.telegram_id}")
            db.commit()
            return
            
        success, error = extend_vip_atomic(db, user, days)
        if not success:
            logger.error(f"Failed to extend VIP via sync: {error}")
            db.rollback()
            return
            
        logger.info(f"✅ VIP activated via SYNC for user {fresh_payment.telegram_id} for {days} days")
        
        commission_paid, commission_amount, referrer_id = process_referral_commission(db, fresh_payment, user)
        if commission_paid:
            logger.info(f"💰 Commission paid via sync: Rp {commission_amount} to {referrer_id}")
            
        db.commit()
        self.payments_activated += 1
        
        if commission_paid and referrer_id and self.bot:
            send_referrer_notification(self.bot, referrer_id, str(fresh_payment.telegram_id), commission_amount)
            
        if self.bot:
            try:
                telegram_id_str = str(fresh_payment.telegram_id)
                self.bot.send_message(
                    int(telegram_id_str),
                    f"✅ <b>Pembayaran Berhasil!</b>\n\n"
                    f"Paket: {fresh_payment.package_name}\n"
                    f"Status VIP kamu sudah aktif!\n\n"
                    f"Selamat menonton! 🎬",
                    parse_mode='HTML'
                )
                logger.info(f"✅ Success notification sent to user {fresh_payment.telegram_id} (via sync)")
            except Exception as bot_error:
                logger.error(f"❌ Failed to send Telegram notification: {bot_error}")
                
    def sync_single_payment(self, transaction_id: str) -> Tuple[bool, str]:
        """
        Manual sync untuk satu payment
        Returns: (success, message)
        """
        if not (QRIS_PW_API_KEY and QRIS_PW_API_SECRET):
            return False, "QRIS.PW credentials not configured"
            
        db = SessionLocal()
        try:
            payment = db.query(Payment).filter(Payment.transaction_id == transaction_id).first()
            
            if not payment:
                return False, f"Payment not found: {transaction_id}"
                
            if str(payment.status) != 'pending':
                return False, f"Payment already processed (status: {payment.status})"
                
            headers = {
                "X-API-Key": QRIS_PW_API_KEY,
                "X-API-Secret": QRIS_PW_API_SECRET
            }
            
            api_url = f"{QRIS_PW_API_URL}/check-payment.php"
            response = requests.get(
                api_url,
                params={"transaction_id": transaction_id},
                headers=headers,
                timeout=10
            )
            
            if response.status_code != 200:
                return False, f"QRIS.PW API error: {response.status_code}"
                
            result = response.json()
            
            if not result.get("success"):
                return False, f"QRIS.PW error: {result.get('error', 'Unknown error')}"
                
            payment_status = result.get("status")
            
            if payment_status == 'paid':
                self._activate_vip_for_payment(db, payment)
                return True, f"VIP activated for user {payment.telegram_id}"
                
            elif payment_status == 'pending':
                return False, "Payment still pending on QRIS.PW"
                
            elif payment_status == 'expired':
                payment.status = 'expired'
                db.commit()
                return False, "Payment expired on QRIS.PW"
                
            else:
                return False, f"Unknown status from QRIS.PW: {payment_status}"
                
        except Exception as e:
            logger.error(f"Error in sync_single_payment: {e}")
            return False, str(e)
        finally:
            db.close()
            
    def get_stats(self) -> dict:
        """Get worker statistics"""
        return {
            "running": self.running,
            "sync_count": self.sync_count,
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "payments_synced": self.payments_synced,
            "payments_activated": self.payments_activated,
            "sync_interval": SYNC_INTERVAL,
            "lookback_hours": SYNC_LOOKBACK_HOURS
        }


payment_sync_worker: Optional[PaymentSyncWorker] = None


def init_payment_sync(bot=None):
    """Initialize and start payment sync worker"""
    global payment_sync_worker
    
    if payment_sync_worker is not None:
        logger.warning("Payment sync worker already initialized")
        return payment_sync_worker
        
    payment_sync_worker = PaymentSyncWorker(bot=bot)
    payment_sync_worker.start()
    return payment_sync_worker


def get_payment_sync_worker() -> Optional[PaymentSyncWorker]:
    """Get current payment sync worker instance"""
    return payment_sync_worker


def stop_payment_sync():
    """Stop payment sync worker"""
    global payment_sync_worker
    if payment_sync_worker:
        payment_sync_worker.stop()
        payment_sync_worker = None
