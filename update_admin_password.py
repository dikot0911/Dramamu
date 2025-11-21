#!/usr/bin/env python3
import os
import sys
from database import SessionLocal, Admin
from admin_auth import hash_password
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def update_admin_password():
    """Update password admin yang sudah ada"""
    
    admin_username = os.getenv('ADMIN_USERNAME')
    admin_password = os.getenv('ADMIN_PASSWORD')
    
    if not admin_username or not admin_password:
        logger.error("❌ ADMIN_USERNAME dan ADMIN_PASSWORD harus di-set!")
        sys.exit(1)
    
    if len(admin_password) < 8:
        logger.error("❌ Password minimal 8 karakter!")
        sys.exit(1)
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.username == admin_username).first()
        
        if not admin:
            logger.error(f"❌ Admin dengan username '{admin_username}' tidak ditemukan!")
            logger.info("Jalankan: python create_admin.py untuk membuat admin baru")
            sys.exit(1)
        
        # Update password
        admin.password_hash = hash_password(admin_password)
        db.commit()
        
        logger.info("=" * 80)
        logger.info("✅ Password admin berhasil diupdate!")
        logger.info("=" * 80)
        logger.info(f"   Username: {admin_username}")
        logger.info(f"   Admin ID: {admin.id}")
        logger.info("=" * 80)
        logger.info("🔐 Sekarang kamu bisa login dengan password baru!")
        logger.info("=" * 80)
        
        return admin
        
    except Exception as e:
        logger.error(f"❌ Error waktu update password: {e}")
        db.rollback()
        return None
    finally:
        db.close()

if __name__ == "__main__":
    update_admin_password()
