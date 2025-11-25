import os
import sys
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from jose import JWTError, jwt
from passlib.context import CryptContext
from database import Admin, AdminSession, SessionLocal
import logging
from config import now_utc

logger = logging.getLogger(__name__)

_warning_printed = False

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def get_jwt_secret() -> Optional[str]:
    """
    Baca JWT_SECRET_KEY dari environment secara dynamic.
    
    Function ini selalu baca fresh value dari environment,
    jadi support runtime configuration tanpa restart.
    
    Returns:
        JWT secret key kalo ada, None kalo belum di-set
    """
    return os.getenv('JWT_SECRET_KEY', '').strip() or None

def get_admin_credentials() -> Dict[str, Optional[str]]:
    """
    Ambil semua admin credentials dari environment.
    
    Function ini selalu baca fresh values, support runtime updates.
    
    Returns:
        Dict dengan admin_username, admin_password, jwt_secret, admin_email
    """
    return {
        'admin_username': os.getenv('ADMIN_USERNAME', '').strip() or None,
        'admin_password': os.getenv('ADMIN_PASSWORD', '').strip() or None,
        'jwt_secret': os.getenv('JWT_SECRET_KEY', '').strip() or None,
        'admin_email': os.getenv('ADMIN_EMAIL', '').strip() or None
    }

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Bikin JWT access token untuk admin authentication.
    
    Baca JWT secret secara dynamic, jadi support runtime configuration.
    
    Args:
        data: Payload yang mau di-encode dalam token
        expires_delta: Custom expiration time (optional)
    
    Returns:
        Encoded JWT token
    
    Raises:
        ValueError: Kalo JWT_SECRET_KEY belum di-set
    """
    secret_key = get_jwt_secret()
    if not secret_key:
        raise ValueError("JWT_SECRET_KEY ga dikonfigurasi. Ga bisa bikin access token.")
    
    to_encode = data.copy()
    if expires_delta:
        expire = now_utc() + expires_delta
    else:
        expire = now_utc() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, secret_key, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> Optional[dict]:
    """
    Verify JWT token dan return payload kalo valid.
    
    Baca JWT secret secara dynamic, support runtime updates.
    
    Args:
        token: JWT token yang mau di-verify
    
    Returns:
        Token payload kalo valid, None kalo invalid atau secret ga ada
    """
    secret_key = get_jwt_secret()
    if not secret_key:
        logger.error("JWT_SECRET_KEY ga dikonfigurasi. Ga bisa verifikasi token.")
        return None
    
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.error(f"Verifikasi token gagal: {e}")
        return None

def authenticate_admin(username: str, password: str) -> Optional[Admin]:
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.username == username).first()
        
        if not admin:
            logger.warning(f"Percobaan login dengan username yang ga ada: {username}")
            return None
        
        if not admin.is_active:  # type: ignore
            logger.warning(f"Percobaan login buat admin yang ga aktif: {username}")
            return None
        
        if not verify_password(password, admin.password_hash):  # type: ignore
            logger.warning(f"Percobaan login dengan password salah buat: {username}")
            return None
        
        admin.last_login = now_utc()  # type: ignore
        db.commit()
        db.refresh(admin)
        
        logger.info(f"Admin berhasil login: {username}")
        return admin
    
    except Exception as e:
        logger.error(f"Error waktu autentikasi: {e}")
        return None
    finally:
        db.close()

def get_admin_by_username(username: str) -> Optional[Admin]:
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.username == username).first()
        return admin
    finally:
        db.close()

def get_admin_by_id(admin_id: int) -> Optional[Admin]:
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.id == admin_id).first()
        return admin
    finally:
        db.close()

def ensure_admin_exists() -> Dict[str, Any]:
    """
    Pastikan admin user exists, bikin kalo belum ada.
    
    Function ini idempotent dan aman dipanggil berkali-kali.
    Ga akan throw error - return status dict untuk graceful degradation.
    
    Returns:
        Dict dengan struktur:
        {
            "status": "success" | "missing_secrets" | "error",
            "message": "Deskripsi lengkap status",
            "admin_id": int (kalo success),
            "admin_username": str (kalo success),
            "missing_secrets": list (kalo missing_secrets),
            "remediation": str (kalo missing_secrets)
        }
    """
    creds = get_admin_credentials()
    admin_username = creds['admin_username']
    admin_password = creds['admin_password']
    jwt_secret = creds['jwt_secret']
    admin_email = creds['admin_email']
    
    missing_secrets = []
    if not admin_username:
        missing_secrets.append('ADMIN_USERNAME')
    if not admin_password:
        missing_secrets.append('ADMIN_PASSWORD')
    if not jwt_secret:
        missing_secrets.append('JWT_SECRET_KEY')
    
    if missing_secrets:
        remediation_msg = (
            "=" * 80 + "\n"
            "Admin panel membutuhkan credentials untuk bekerja.\n"
            "=" * 80 + "\n"
            "\n"
            "Yang masih kurang:\n"
            f"  - ADMIN_USERNAME: {'Set' if admin_username else 'MISSING'}\n"
            f"  - ADMIN_PASSWORD: {'Set' if admin_password else 'MISSING'}\n"
            f"  - JWT_SECRET_KEY: {'Set' if jwt_secret else 'MISSING'}\n"
            "\n"
            "Cara setup:\n"
            "1. Generate JWT_SECRET_KEY yang aman:\n"
            "   python -c 'import secrets; print(secrets.token_urlsafe(32))'\n"
            "\n"
            "2. Set environment variables:\n"
            "   - ADMIN_USERNAME: Username admin pilihan kamu\n"
            "   - ADMIN_PASSWORD: Password kuat (min 8 karakter)\n"
            "   - JWT_SECRET_KEY: Secret key dari step 1\n"
            "\n"
            "3. Restart aplikasi setelah environment variables di-set\n"
            "=" * 80
        )
        global _warning_printed
        if not _warning_printed:
            logger.warning(remediation_msg)
            _warning_printed = True
        
        return {
            "status": "missing_secrets",
            "message": f"Admin credentials belum lengkap. Kurang: {', '.join(missing_secrets)}",
            "missing_secrets": missing_secrets,
            "remediation": remediation_msg
        }
    
    assert admin_username is not None
    assert admin_password is not None
    assert jwt_secret is not None
    
    if len(admin_password) < 8:
        error_msg = (
            "Password admin terlalu pendek. Minimal 8 karakter untuk keamanan. "
            f"Panjang sekarang: {len(admin_password)} karakter"
        )
        logger.error(error_msg)
        return {
            "status": "error",
            "message": error_msg,
            "remediation": "Set ADMIN_PASSWORD dengan minimal 8 karakter"
        }
    
    db = SessionLocal()
    try:
        existing_admin = db.query(Admin).filter(Admin.username == admin_username).first()
        
        if existing_admin:
            new_password_hash = hash_password(admin_password)
            password_changed = existing_admin.password_hash != new_password_hash  # type: ignore
            
            existing_admin.password_hash = new_password_hash  # type: ignore
            if admin_email:
                existing_admin.email = admin_email  # type: ignore
            existing_admin.is_active = True  # type: ignore
            
            db.commit()
            db.refresh(existing_admin)
            
            if password_changed:
                logger.info(f"Admin '{admin_username}' sudah ada (ID: {existing_admin.id}) - password di-sync dengan env vars")
            else:
                logger.info(f"Admin '{admin_username}' sudah ada (ID: {existing_admin.id}) - idempotent check OK")
            
            return {
                "status": "success",
                "message": f"Admin '{admin_username}' sudah ada dan password di-sync",
                "admin_id": existing_admin.id,
                "admin_username": existing_admin.username,
                "already_exists": True,
                "password_synced": password_changed
            }
        
        logger.info(f"Bikin admin user baru '{admin_username}'...")
        new_admin = Admin(
            username=admin_username,
            password_hash=hash_password(admin_password),
            email=admin_email if admin_email else None,
            is_active=True,
            created_at=now_utc()
        )
        db.add(new_admin)
        db.commit()
        db.refresh(new_admin)
        
        logger.info("=" * 80)
        logger.info(f"Admin user '{admin_username}' berhasil dibuat!")
        logger.info(f"   ID: {new_admin.id}")
        logger.info(f"   Email: {admin_email if admin_email else 'Tidak di-set'}")
        logger.info("=" * 80)
        
        return {
            "status": "success",
            "message": f"Admin '{admin_username}' berhasil dibuat",
            "admin_id": new_admin.id,
            "admin_username": new_admin.username,
            "created": True
        }
        
    except Exception as e:
        error_msg = f"Gagal ensure admin exists: {str(e)}"
        logger.error(error_msg)
        db.rollback()
        return {
            "status": "error",
            "message": error_msg,
            "error_details": str(e)
        }
    finally:
        db.close()

def is_super_admin(admin: Admin) -> bool:
    """
    Check apakah admin adalah super admin (admin dari ENV).
    
    Super admin punya privilege khusus:
    - Bisa manage admin users (create, edit, delete)
    - Bisa kick admin sessions lain
    - Access ke halaman admin management
    
    Args:
        admin: Admin object dari database
    
    Returns:
        True jika admin adalah super admin, False otherwise
    """
    creds = get_admin_credentials()
    env_username = creds['admin_username']
    
    if not env_username:
        return False
    
    return admin.username == env_username

def create_admin_session(
    admin_id: int,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    expires_in_hours: int = ACCESS_TOKEN_EXPIRE_HOURS
) -> Optional[AdminSession]:
    """
    Create session baru untuk admin.
    
    Session ini digunakan untuk:
    - Track active logins
    - Implement logout/kick functionality
    - Monitor admin activity
    
    Args:
        admin_id: ID admin yang login
        ip_address: IP address admin (optional)
        user_agent: User agent browser (optional)
        expires_in_hours: Session expiry dalam jam
    
    Returns:
        AdminSession object kalau sukses, None kalau gagal
    """
    db = SessionLocal()
    try:
        session_token = secrets.token_urlsafe(32)
        expires_at = now_utc() + timedelta(hours=expires_in_hours)
        
        session = AdminSession(
            admin_id=admin_id,
            session_token=session_token,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=now_utc(),
            last_activity=now_utc(),
            expires_at=expires_at
        )
        
        db.add(session)
        db.commit()
        db.refresh(session)
        
        logger.info(f"Session created for admin_id={admin_id}, expires_at={expires_at}")
        return session
    
    except Exception as e:
        logger.error(f"Error creating admin session: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def get_admin_session(session_token: str) -> Optional[AdminSession]:
    """
    Get admin session by token.
    
    Args:
        session_token: Session token untuk dicari
    
    Returns:
        AdminSession object kalau ditemukan dan belum expired, None otherwise
    """
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token
        ).first()
        
        if not session:
            return None
        
        if session.expires_at < now_utc():
            logger.info(f"Session {session_token[:8]}... sudah expired")
            db.delete(session)
            db.commit()
            return None
        
        session.last_activity = now_utc()
        db.commit()
        db.refresh(session)
        
        return session
    
    except Exception as e:
        logger.error(f"Error getting admin session: {e}")
        return None
    finally:
        db.close()

def touch_admin_session(session_token: str) -> bool:
    """
    Update last_activity timestamp untuk session (lightweight heartbeat).
    
    Dipanggil setiap authenticated request untuk keep session alive
    dan update online status indicator.
    
    Args:
        session_token: Token session yang mau di-update
    
    Returns:
        True kalau sukses, False kalau session tidak ditemukan atau expired
    """
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token
        ).first()
        
        if not session:
            return False
        
        if session.expires_at < now_utc():
            return False
        
        session.last_activity = now_utc()
        db.commit()
        
        return True
    
    except Exception as e:
        logger.error(f"Error touching admin session: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def delete_admin_session(session_token: str) -> bool:
    """
    Delete admin session (untuk logout).
    
    Args:
        session_token: Token session yang mau dihapus
    
    Returns:
        True kalau sukses, False kalau gagal
    """
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token
        ).first()
        
        if session:
            db.delete(session)
            db.commit()
            logger.info(f"Session {session_token[:8]}... deleted")
            return True
        
        return False
    
    except Exception as e:
        logger.error(f"Error deleting admin session: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def delete_all_admin_sessions(admin_id: int) -> int:
    """
    Delete semua sessions untuk admin tertentu (untuk kick user).
    
    Args:
        admin_id: ID admin yang sessions-nya mau dihapus
    
    Returns:
        Jumlah sessions yang dihapus
    """
    db = SessionLocal()
    try:
        sessions = db.query(AdminSession).filter(
            AdminSession.admin_id == admin_id
        ).all()
        
        count = len(sessions)
        
        for session in sessions:
            db.delete(session)
        
        db.commit()
        logger.info(f"Deleted {count} session(s) for admin_id={admin_id}")
        return count
    
    except Exception as e:
        logger.error(f"Error deleting admin sessions: {e}")
        db.rollback()
        return 0
    finally:
        db.close()

def get_active_sessions_for_admin(admin_id: int) -> List[AdminSession]:
    """
    Get semua active sessions untuk admin tertentu.
    
    Args:
        admin_id: ID admin
    
    Returns:
        List of AdminSession objects yang masih aktif
    """
    db = SessionLocal()
    try:
        sessions = db.query(AdminSession).filter(
            AdminSession.admin_id == admin_id,
            AdminSession.expires_at > now_utc()
        ).order_by(AdminSession.last_activity.desc()).all()
        
        return sessions
    
    except Exception as e:
        logger.error(f"Error getting active sessions: {e}")
        return []
    finally:
        db.close()

def cleanup_expired_sessions() -> int:
    """
    Cleanup semua expired sessions dari database.
    
    Function ini bisa dipanggil periodic untuk maintenance.
    
    Returns:
        Jumlah sessions yang dihapus
    """
    db = SessionLocal()
    try:
        expired_sessions = db.query(AdminSession).filter(
            AdminSession.expires_at < now_utc()
        ).all()
        
        count = len(expired_sessions)
        
        for session in expired_sessions:
            db.delete(session)
        
        db.commit()
        
        if count > 0:
            logger.info(f"Cleaned up {count} expired session(s)")
        
        return count
    
    except Exception as e:
        logger.error(f"Error cleaning up expired sessions: {e}")
        db.rollback()
        return 0
    finally:
        db.close()
