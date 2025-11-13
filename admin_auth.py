import os
import sys
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from database import Admin, SessionLocal
import logging
from config import now_utc

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv('JWT_SECRET_KEY')

if not SECRET_KEY:
    logger.warning("=" * 80)
    logger.warning("⚠️  PERINGATAN: JWT_SECRET_KEY belum dikonfigurasi!")
    logger.warning("=" * 80)
    logger.warning("")
    logger.warning("Admin panel membutuhkan JWT_SECRET_KEY untuk bekerja.")
    logger.warning("Admin panel akan TIDAK DAPAT DIAKSES sampai JWT_SECRET_KEY di-set.")
    logger.warning("")
    logger.warning("Set environment variable JWT_SECRET_KEY dengan value yang kuat:")
    logger.warning("")
    logger.warning("Cara generate secret key yang aman:")
    logger.warning("  python -c 'import secrets; print(secrets.token_urlsafe(32))'")
    logger.warning("")
    logger.warning("Lalu set sebagai environment variable:")
    logger.warning("  export JWT_SECRET_KEY='generated_secret_here'")
    logger.warning("")
    logger.warning("=" * 80)
    SECRET_KEY = None

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    if not SECRET_KEY:
        raise ValueError("JWT_SECRET_KEY ga dikonfigurasi. Ga bisa bikin access token.")
    
    to_encode = data.copy()
    if expires_delta:
        expire = now_utc() + expires_delta
    else:
        expire = now_utc() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str) -> Optional[dict]:
    if not SECRET_KEY:
        logger.error("JWT_SECRET_KEY ga dikonfigurasi. Ga bisa verifikasi token.")
        return None
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
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
