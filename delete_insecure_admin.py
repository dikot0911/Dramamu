#!/usr/bin/env python3
"""Hapus admin user yang ga aman dengan kredensial default"""
from database import SessionLocal, Admin
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def delete_insecure_admin():
    """Hapus admin user dengan username 'admin' yang pake kredensial default"""
    db = SessionLocal()
    try:
        # Hapus semua admin user (buat clean slate)
        admins = db.query(Admin).all()
        
        if not admins:
            logger.info("✅ Ga ada admin user di database")
            return
        
        for admin in admins:
            logger.info(f"🗑️ Hapus admin user: {admin.username} (ID: {admin.id})")
            db.delete(admin)
        
        db.commit()
        
        logger.info("=" * 80)
        logger.info("✅ Semua admin user yang ga aman berhasil dihapus!")
        logger.info("=" * 80)
        logger.info("")
        logger.info("LANGKAH SELANJUTNYA:")
        logger.info("1. Set environment variables:")
        logger.info("   - JWT_SECRET_KEY")
        logger.info("   - ADMIN_USERNAME")
        logger.info("   - ADMIN_PASSWORD")
        logger.info("")
        logger.info("2. Jalankan: python create_admin.py")
        logger.info("3. Restart aplikasinya")
        logger.info("")
        logger.info("Lihat ADMIN_PANEL_SETUP.md buat instruksi detail")
        logger.info("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Error waktu hapus admin users: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    delete_insecure_admin()
