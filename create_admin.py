#!/usr/bin/env python3
import os
import sys
from database import SessionLocal, Admin
from admin_auth import hash_password
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_default_admin():
    """Buat admin user dari environment variables - ga pake default credentials"""
    
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_password = os.getenv('ADMIN_PASSWORD')
    admin_email = os.getenv('ADMIN_EMAIL', 'admin@dramamu.com')
    
    if not admin_username or not admin_password:
        logger.error("=" * 80)
        logger.error("❌ ADMIN CREDENTIALS GA KETEMU!")
        logger.error("=" * 80)
        logger.error("")
        logger.error("Admin panel butuh credentials buat dibuat.")
        logger.error("")
        logger.error("📝 CARA SETUP:")
        logger.error("=" * 80)
        logger.error("1. Generate JWT_SECRET_KEY yang aman:")
        logger.error("   python -c 'import secrets; print(secrets.token_urlsafe(32))'")
        logger.error("")
        logger.error("2. Set environment variables (di .env atau dashboard hosting):")
        logger.error("   - JWT_SECRET_KEY: (hasil generate dari langkah 1)")
        logger.error("   - ADMIN_USERNAME: (username pilihan kamu)")
        logger.error("   - ADMIN_PASSWORD: (password kuat min 8 karakter)")
        logger.error("   - ADMIN_EMAIL: (email kamu, opsional)")
        logger.error("")
        logger.error("3. Setelah di-set, jalankan: python create_admin.py")
        logger.error("=" * 80)
        sys.exit(1)
    
    if len(admin_password) < 8:
        logger.error("=" * 80)
        logger.error("❌ SECURITY ERROR: Password terlalu pendek!")
        logger.error("=" * 80)
        logger.error("Password admin minimal 8 karakter buat keamanan.")
        logger.error("=" * 80)
        sys.exit(1)
    
    db = SessionLocal()
    try:
        existing_admin = db.query(Admin).filter(Admin.username == admin_username).first()
        
        if existing_admin:
            logger.info(f"✅ Admin user '{admin_username}' sudah ada di database")
            logger.info(f"   ID: {existing_admin.id}")
            logger.info(f"   Created: {existing_admin.created_at}")
            return existing_admin
        
        admin = Admin(
            username=admin_username,
            email=admin_email,
            password_hash=hash_password(admin_password),
            is_active=True
        )
        
        db.add(admin)
        db.commit()
        db.refresh(admin)
        
        logger.info("=" * 80)
        logger.info("✅ Admin user berhasil dibuat!")
        logger.info("=" * 80)
        logger.info(f"   Username: {admin_username}")
        logger.info(f"   Email: {admin_email}")
        logger.info("=" * 80)
        logger.info("⚠️  PENTING:")
        logger.info("   1. Simpan credentials kamu dengan aman")
        logger.info("   2. Jangan share password ke siapapun")
        logger.info("   3. Ganti password secara berkala")
        logger.info("=" * 80)
        
        return admin
        
    except Exception as e:
        logger.error(f"❌ Error waktu bikin admin user: {e}")
        db.rollback()
        return None
    finally:
        db.close()

if __name__ == "__main__":
    create_default_admin()
