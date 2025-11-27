"""
Referral System Utilities
Centralized logic untuk commission calculation dan payment processing
"""

import logging
from typing import Optional, Tuple
from sqlalchemy import update
from sqlalchemy.orm import Session
from database import User, Payment, Withdrawal, PaymentCommission
from config import now_utc

logger = logging.getLogger(__name__)

# Constants
COMMISSION_RATE = 0.25  # 25% komisi dari payment
MIN_WITHDRAWAL = 50000  # Minimum withdrawal: Rp 50.000


def process_referral_commission(
    db: Session, 
    payment: 'Payment', 
    user: 'User'
) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Proses komisi referral secara centralized dengan race condition protection.
    
    Logika:
    1. ⚠️ Check apakah commission sudah pernah diproses untuk payment ini (race condition check)
    2. Check apakah user punya referred_by_code
    3. Check apakah ini first payment dari user
    4. Cari referrer berdasarkan ref_code
    5. Hitung komisi 25% dari payment amount
    6. Update commission_balance referrer + create PaymentCommission record (atomic)
    
    Args:
        db: Database session
        payment: Payment object yang berhasil
        user: User object yang membeli VIP
        
    Returns:
        Tuple[bool, Optional[int], Optional[str]]:
        - bool: True jika commission paid, False jika tidak
        - Optional[int]: Commission amount yang dibayar (None jika tidak ada)
        - Optional[str]: Referrer telegram_id (None jika tidak ada)
        
    Raises:
        Exception: Jika ada error di database operations
    """
    
    # ⚠️ STEP 0 (RACE CONDITION PROTECTION): Check apakah commission sudah diproses
    existing_commission = (
        db.query(PaymentCommission)
        .filter(PaymentCommission.payment_id == payment.id)
        .first()
    )
    
    if existing_commission:
        existing_amount: int = int(existing_commission.commission_amount)  # type: ignore
        existing_referrer: str | None = str(existing_commission.referrer_telegram_id) if existing_commission.referrer_telegram_id is not None else None  # type: ignore
        logger.warning(
            f"⚠️ RACE CONDITION DETECTED: Commission sudah diproses untuk payment {payment.id} "
            f"(user {payment.telegram_id}). "
            f"Returning existing commission: Rp {existing_amount}"
        )
        return True, existing_amount, existing_referrer
    
    # Step 1: Check apakah user punya referred_by_code
    referred_by_code: str | None = user.referred_by_code  # type: ignore
    if referred_by_code is None:
        logger.debug(f"⏭️ No referrer code for user {user.telegram_id}")
        # Still create record untuk track bahwa kami sudah check untuk payment ini
        pc = PaymentCommission(
            payment_id=payment.id,
            referrer_telegram_id=None,
            commission_amount=0,
            referral_user_telegram_id=payment.telegram_id
        )
        db.add(pc)
        db.flush()  # Flush sebelum commit untuk ensure unique constraint di-enforce
        return False, None, None
    
    # Step 2: Check apakah ini first payment
    is_first_payment = (
        db.query(Payment)
        .filter(
            Payment.telegram_id == payment.telegram_id,
            Payment.status == 'success',
            Payment.id != payment.id
        )
        .first() is None
    )
    
    if not is_first_payment:
        logger.info(f"⏭️ Skip commission - not first payment for user {payment.telegram_id}")
        # Still create record untuk track
        pc = PaymentCommission(
            payment_id=payment.id,
            referrer_telegram_id=None,
            commission_amount=0,
            referral_user_telegram_id=payment.telegram_id
        )
        db.add(pc)
        db.flush()
        return False, None, None
    
    # Step 3: Find referrer (BUG FIX #8: Exclude soft-deleted referrers)
    referrer = db.query(User).filter(
        User.ref_code == referred_by_code,
        User.deleted_at == None
    ).first()
    if not referrer:
        logger.warning(
            f"⚠️ Referrer dengan kode '{referred_by_code}' tidak ditemukan "
            f"(user {payment.telegram_id})"
        )
        # Still create record untuk track
        pc = PaymentCommission(
            payment_id=payment.id,
            referrer_telegram_id=None,
            commission_amount=0,
            referral_user_telegram_id=payment.telegram_id
        )
        db.add(pc)
        db.flush()
        return False, None, None
    
    # Step 4: Calculate commission
    payment_amount: int = int(payment.amount)  # type: ignore
    commission: int = int(payment_amount * COMMISSION_RATE)
    
    # Step 5: Update referrer balance + create PaymentCommission record (ATOMIC)
    # BUG FIX #3: Add IntegrityError handling for unique constraint violations
    from sqlalchemy.exc import IntegrityError
    
    try:
        # Update referrer balance
        db.execute(
            update(User)
            .where(User.id == referrer.id)
            .values(
                commission_balance=User.commission_balance + commission
            )
        )
        
        # Create PaymentCommission record untuk prevent double processing
        pc = PaymentCommission(
            payment_id=payment.id,
            referrer_telegram_id=referrer.telegram_id,
            commission_amount=commission,
            referral_user_telegram_id=payment.telegram_id
        )
        db.add(pc)
        db.flush()  # Flush untuk enforce unique constraint sebelum commit
        
        referrer_id_str: str = str(referrer.telegram_id)  # type: ignore
        logger.info(
            f"💰 Komisi dibayar: Rp {commission} ke user {referrer_id_str} "
            f"(referrer dari {payment.telegram_id}, payment Rp {payment_amount}). "
            f"PaymentCommission record created untuk prevent double processing."
        )
        return True, commission, referrer_id_str
    
    except IntegrityError as ie:
        # BUG FIX #3: Handle race condition - another process already created commission
        logger.warning(
            f"⚠️ RACE CONDITION: IntegrityError saat create PaymentCommission "
            f"untuk payment {payment.id}. Concurrent process mungkin sudah proses commission ini. "
            f"Rolling back balance update dan returning existing commission."
        )
        db.rollback()
        
        # Re-check untuk existing commission (yang dibuat oleh concurrent process)
        existing = db.query(PaymentCommission).filter(
            PaymentCommission.payment_id == payment.id
        ).first()
        
        if existing:
            existing_amount: int = int(existing.commission_amount)  # type: ignore
            existing_referrer: str | None = str(existing.referrer_telegram_id) if existing.referrer_telegram_id is not None else None  # type: ignore
            logger.info(
                f"✅ Found existing PaymentCommission: Rp {existing_amount} "
                f"to {existing_referrer}. No duplicate commission paid."
            )
            return True, existing_amount, existing_referrer
        else:
            # Unexpected: IntegrityError tapi tidak ada existing record
            logger.error(
                f"❌ Unexpected IntegrityError tanpa existing commission record: {ie}"
            )
            raise
    
    except Exception as e:
        logger.error(f"❌ Gagal update commission: {e}")
        raise


def validate_withdrawal_request(
    db: Session,
    telegram_id: str,
    amount: int,
    user: Optional['User'] = None
) -> Tuple[bool, Optional[str]]:
    """
    Validasi withdrawal request sebelum diproses.
    
    Validasi yang dilakukan:
    1. Amount >= MIN_WITHDRAWAL
    2. User punya balance yang cukup
    3. Tidak ada pending withdrawal request
    
    Args:
        db: Database session
        telegram_id: Telegram ID dari user
        amount: Jumlah withdrawal yang diminta
        user: User object (opsional, bisa di-fetch dari DB)
        
    Returns:
        Tuple[bool, Optional[str]]:
        - bool: True jika valid, False jika tidak
        - Optional[str]: Error message jika tidak valid (None jika valid)
    """
    
    # Validasi 1: Amount >= MIN_WITHDRAWAL
    if amount < MIN_WITHDRAWAL:
        return False, f"Minimum penarikan adalah Rp {MIN_WITHDRAWAL:,}"
    
    # Get user jika belum di-pass (BUG FIX #8: Exclude soft-deleted users)
    if not user:
        user = db.query(User).filter(
            User.telegram_id == telegram_id,
            User.deleted_at == None
        ).first()
    
    if not user:
        return False, "User tidak ditemukan"
    
    # Validasi 2: Balance cukup
    commission_balance: int = int(user.commission_balance)  # type: ignore
    if amount > commission_balance:
        return False, (
            f"Saldo tidak cukup. "
            f"Saldo Anda: Rp {commission_balance:,}, "
            f"Withdrawal: Rp {amount:,}"
        )
    
    # Validasi 3: Tidak ada pending withdrawal
    pending_withdrawal = (
        db.query(Withdrawal)
        .filter(
            Withdrawal.telegram_id == telegram_id,
            Withdrawal.status == 'pending'
        )
        .first()
    )
    
    if pending_withdrawal is not None:
        return False, (
            "Anda sudah punya request withdrawal yang masih pending. "
            "Tunggu sampai diproses oleh admin."
        )
    
    return True, None


def approve_withdrawal(
    db: Session,
    withdrawal: 'Withdrawal'
) -> Tuple[bool, Optional[str]]:
    """
    Proses approval withdrawal request.
    
    Langkah-langkah:
    1. Find user
    2. Deduct commission_balance
    3. Update withdrawal status
    4. Set processed_at timestamp
    
    Args:
        db: Database session
        withdrawal: Withdrawal object
        
    Returns:
        Tuple[bool, Optional[str]]:
        - bool: True jika berhasil, False jika gagal
        - Optional[str]: Error message jika gagal (None jika berhasil)
    """
    
    try:
        # Find user (BUG FIX #8: Exclude soft-deleted users)
        user = db.query(User).filter(
            User.telegram_id == withdrawal.telegram_id,
            User.deleted_at == None
        ).first()
        
        if not user:
            return False, "User tidak ditemukan"
        
        # Deduct balance
        commission_balance: int = int(user.commission_balance)  # type: ignore
        withdrawal_amount: int = int(withdrawal.amount)  # type: ignore
        if withdrawal_amount > commission_balance:
            return False, "Saldo tidak cukup (mungkin sudah di-withdraw)"
        
        user.commission_balance = commission_balance - withdrawal_amount  # type: ignore
        withdrawal.status = 'approved'  # type: ignore
        withdrawal.processed_at = now_utc()  # type: ignore
        
        db.commit()
        
        logger.info(
            f"✅ Withdrawal approved: {withdrawal.id} "
            f"(user {withdrawal.telegram_id}, amount Rp {withdrawal.amount})"
        )
        
        return True, None
        
    except Exception as e:
        logger.error(f"❌ Error approving withdrawal: {e}")
        db.rollback()
        return False, str(e)


def reject_withdrawal(
    db: Session,
    withdrawal: 'Withdrawal'
) -> Tuple[bool, Optional[str]]:
    """
    Proses rejection withdrawal request.
    
    Langkah-langkah:
    1. Update withdrawal status to rejected
    2. Set processed_at timestamp
    3. Balance user tetap utuh (tidak dikurangi)
    
    Args:
        db: Database session
        withdrawal: Withdrawal object
        
    Returns:
        Tuple[bool, Optional[str]]:
        - bool: True jika berhasil, False jika gagal
        - Optional[str]: Error message jika gagal (None jika berhasil)
    """
    
    try:
        withdrawal.status = 'rejected'  # type: ignore
        withdrawal.processed_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(
            f"⛔ Withdrawal rejected: {withdrawal.id} "
            f"(user {withdrawal.telegram_id}, amount Rp {withdrawal.amount})"
        )
        
        return True, None
        
    except Exception as e:
        logger.error(f"❌ Error rejecting withdrawal: {e}")
        db.rollback()
        return False, str(e)


def send_referrer_notification(
    bot,
    referrer_telegram_id: Optional[str],
    referred_user_telegram_id: str,
    commission_amount: int
) -> Tuple[bool, Optional[str]]:
    """
    Send notification ke referrer tentang komisi yang diterima.
    
    Notification berisi:
    - Siapa yang membeli VIP (referred user)
    - Berapa komisi yang diterima
    - Info untuk withdrawal
    
    Args:
        bot: TeleBot instance (bisa None)
        referrer_telegram_id: Telegram ID dari referrer
        referred_user_telegram_id: Telegram ID dari user yang beli VIP
        commission_amount: Jumlah komisi dalam Rupiah
        
    Returns:
        Tuple[bool, Optional[str]]:
        - bool: True jika notification terkirim, False jika gagal/bot None
        - Optional[str]: Error message jika gagal (None jika berhasil)
    """
    
    if not bot or not referrer_telegram_id:
        logger.debug(f"⏭️ Skip notification: bot={bool(bot)}, referrer={referrer_telegram_id}")
        return False, None
    
    try:
        # Format rupiah
        commission_formatted = f"Rp {commission_amount:,}".replace(",", ".")
        
        # Notification message
        message = (
            "💰 <b>Komisi Referral Diterima!</b>\n\n"
            f"Pengguna dengan ID <code>{referred_user_telegram_id}</code> telah membeli VIP membership.\n\n"
            f"💵 <b>Komisi: {commission_formatted}</b>\n\n"
            "Komisi ini sekarang ditambahkan ke saldo withdraw Anda. "
            "Anda bisa withdraw kapan saja ke rekening bank Anda.\n\n"
            "Terima kasih telah menjadi bagian dari referral program kami!"
        )
        
        # Send notification
        bot.send_message(
            referrer_telegram_id,
            message,
            parse_mode='HTML'
        )
        
        logger.info(
            f"✅ Notification terkirim ke referrer {referrer_telegram_id}: "
            f"Commission Rp {commission_amount} dari user {referred_user_telegram_id}"
        )
        return True, None
        
    except Exception as e:
        # Log error tapi jangan stop commission processing
        logger.warning(
            f"⚠️ Failed to send referrer notification: {e} "
            f"(referrer: {referrer_telegram_id}, amount: {commission_amount})"
        )
        return False, str(e)


def get_referral_stats(
    db: Session,
    user: 'User'
) -> dict:
    """
    Get lengkap referral stats untuk user.
    
    Mengembalikan:
    - ref_code: Unique referral code
    - commission_balance: Saldo komisi yang bisa di-withdraw
    - total_referrals: Total user yang di-refer
    - total_commission_earned: Total komisi yang pernah diterima (untuk analytics)
    
    Args:
        db: Database session
        user: User object
        
    Returns:
        dict: Referral statistics
    """
    
    return {
        "ref_code": str(user.ref_code),  # type: ignore
        "commission_balance": int(user.commission_balance),  # type: ignore
        "total_referrals": int(user.total_referrals),  # type: ignore
        # Total yang pernah earned = balance + yang sudah di-withdraw
        "withdrawal_history": db.query(Withdrawal).filter(
            Withdrawal.telegram_id == user.telegram_id,
            Withdrawal.status == 'approved'
        ).all()
    }


def get_referral_program_analytics(db: Session) -> dict:
    """
    Get comprehensive analytics tentang referral program performance.
    
    Metrics:
    - Total active referrers
    - Total earnings dari referral program
    - Average commission per referrer
    - Top 10 referrers by earnings
    - Recent commissions (last 30 days)
    - Program health metrics
    
    Args:
        db: Database session
        
    Returns:
        dict: Comprehensive referral program analytics
    """
    
    try:
        # Total active referrers (users dengan at least 1 referral)
        # BUG FIX #8: Exclude soft-deleted users
        from sqlalchemy import func
        total_active_referrers: int = (
            db.query(User)
            .filter(
                User.total_referrals > 0,
                User.deleted_at == None
            )
            .count()
        )
        
        # Total earnings dalam program (semua commission yang pernah dibayar)
        total_earnings: int = int(db.query(func.sum(PaymentCommission.commission_amount)).scalar() or 0)  # type: ignore
        
        # Top referrers by total commission earned
        top_referrers = (
            db.query(
                User.telegram_id,
                User.username,
                func.count(PaymentCommission.id).label('commission_count'),
                func.sum(PaymentCommission.commission_amount).label('total_earned')
            )
            .outerjoin(PaymentCommission, User.telegram_id == PaymentCommission.referrer_telegram_id)
            .group_by(User.id, User.telegram_id, User.username)
            .filter(func.sum(PaymentCommission.commission_amount) > 0)
            .order_by(func.sum(PaymentCommission.commission_amount).desc())
            .limit(10)
            .all()
        )
        
        # Format top referrers
        top_referrers_formatted = []
        for ref in top_referrers:
            top_referrers_formatted.append({
                "telegram_id": ref.telegram_id,
                "username": ref.username or f"User {ref.telegram_id}",
                "commissions_count": int(ref.commission_count),
                "total_earned": int(ref.total_earned) if ref.total_earned else 0
            })
        
        # Recent commissions (last 30 days)
        from datetime import datetime, timedelta
        thirty_days_ago = now_utc() - timedelta(days=30)
        recent_commissions = (
            db.query(PaymentCommission)
            .filter(PaymentCommission.created_at >= thirty_days_ago)
            .order_by(PaymentCommission.created_at.desc())
            .limit(50)
            .all()
        )
        
        recent_commissions_formatted = []
        for pc in recent_commissions:
            created_at_str: str | None = pc.created_at.isoformat() if pc.created_at is not None else None  # type: ignore
            recent_commissions_formatted.append({
                "referrer_telegram_id": str(pc.referrer_telegram_id) if pc.referrer_telegram_id else None,  # type: ignore
                "commission_amount": int(pc.commission_amount),  # type: ignore
                "referred_user": str(pc.referral_user_telegram_id),  # type: ignore
                "created_at": created_at_str
            })
        
        # Calculate average commission
        avg_commission = 0
        if total_active_referrers > 0:
            avg_commission = int(total_earnings / total_active_referrers)
        
        return {
            "total_active_referrers": total_active_referrers,
            "total_program_earnings": int(total_earnings),
            "average_earnings_per_referrer": avg_commission,
            "top_referrers": top_referrers_formatted,
            "recent_commissions": recent_commissions_formatted,
            "program_health": {
                "active_referrers": total_active_referrers,
                "total_referral_transactions": db.query(PaymentCommission).count(),
                "average_commission_size": avg_commission if avg_commission > 0 else "N/A"
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting referral analytics: {e}")
        return {
            "error": str(e),
            "total_active_referrers": 0,
            "total_program_earnings": 0
        }
