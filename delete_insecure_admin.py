#!/usr/bin/env python3
"""Hapus admin user yang ga aman dengan kredensial default"""
from database import SessionLocal, Admin
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INSECURE_USERNAMES = ['admin']

def delete_insecure_admin():
    """Hapus admin user dengan username 'admin' yang pake kredensial default"""
    db = SessionLocal()
    try:
        from config import now_utc
        insecure_admins = db.query(Admin).filter(
            Admin.deleted_at == None,
            Admin.username.in_(INSECURE_USERNAMES)
        ).all()
        
        if not insecure_admins:
            logger.info("✅ Ga ada admin user dengan username insecure (admin) di database")
            return
        
        deleted_count = 0
        for admin in insecure_admins:
            logger.info(f"🗑️ Soft-delete insecure admin user: {admin.username} (ID: {admin.id})")
            admin.deleted_at = now_utc()  # type: ignore
            deleted_count += 1
        
        db.commit()
        
        logger.info("=" * 80)
        logger.info(f"✅ {deleted_count} admin user dengan username insecure berhasil dihapus!")
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
