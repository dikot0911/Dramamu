import sys
from typing import Optional
from database import SessionLocal, Admin, init_db
from admin_auth import hash_password
from datetime import datetime
from config import now_utc
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_first_admin(username: str, password: str, email: Optional[str] = None):
    logger.info("=" * 60)
    logger.info("🔧 DRAMAMU ADMIN PANEL - STARTUP SCRIPT")
    logger.info("=" * 60)
    
    logger.info("\n📦 Step 1: Inisialisasi database...")
    try:
        init_db()
        logger.info("✅ Database berhasil diinisialisasi!")
    except Exception as e:
        logger.error(f"❌ Gagal inisialisasi database: {e}")
        sys.exit(1)
    
    logger.info("\n👤 Step 2: Membuat admin pertama...")
    
    db = SessionLocal()
    try:
        existing_admin = db.query(Admin).filter(
            Admin.username == username,
            Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
        ).first()
        if existing_admin:
            logger.warning(f"⚠️  Admin dengan username '{username}' sudah ada!")
            logger.info("Apakah Anda ingin:")
            logger.info("  1. Update password admin yang sudah ada")
            logger.info("  2. Batalkan")
            
            choice = input("\nPilihan (1/2): ").strip()
            
            if choice == "1":
                existing_admin.password_hash = hash_password(password)  # type: ignore
                if email:
                    existing_admin.email = email  # type: ignore
                db.commit()
                logger.info(f"✅ Password admin '{username}' berhasil diupdate!")
            else:
                logger.info("❌ Dibatalkan")
                return
        else:
            password_hash = hash_password(password)
            
            new_admin = Admin(
                username=username,
                password_hash=password_hash,
                email=email,
                is_active=True,
                created_at=now_utc()
            )
            
            db.add(new_admin)
            db.commit()
            db.refresh(new_admin)
            
            logger.info("✅ Admin pertama berhasil dibuat!")
            logger.info("\n" + "=" * 60)
            logger.info("📋 DETAIL ADMIN:")
            logger.info("=" * 60)
            logger.info(f"  ID       : {new_admin.id}")
            logger.info(f"  Username : {new_admin.username}")
            logger.info(f"  Email    : {new_admin.email or '(tidak diisi)'}")  # type: ignore
            logger.info(f"  Status   : {'Aktif' if new_admin.is_active else 'Nonaktif'}")  # type: ignore
            logger.info(f"  Dibuat   : {new_admin.created_at}")
            logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"❌ Error saat membuat admin: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()
    
    logger.info("\n🎉 SETUP SELESAI!")
    logger.info("\n📝 LANGKAH SELANJUTNYA:")
    logger.info("  1. Jalankan server: python runner.py")
    logger.info("  2. Test endpoint login: POST /admin/login")
    logger.info(f"     Body: {{\"username\": \"{username}\", \"password\": \"<your-password>\"}}")
    logger.info("  3. Gunakan token yang didapat untuk mengakses endpoint admin lainnya")
    logger.info("\n" + "=" * 60)

def interactive_setup():
    logger.info("=" * 60)
    logger.info("🎬 DRAMAMU ADMIN PANEL - INTERACTIVE SETUP")
    logger.info("=" * 60)
    logger.info("\nSelamat datang! Mari setup admin pertama Anda.\n")
    
    username = input("Masukkan username admin: ").strip()
    while not username:
        logger.warning("⚠️  Username tidak boleh kosong!")
        username = input("Masukkan username admin: ").strip()
    
    password = input("Masukkan password admin: ").strip()
    while not password or len(password) < 6:
        if not password:
            logger.warning("⚠️  Password tidak boleh kosong!")
        else:
            logger.warning("⚠️  Password minimal 6 karakter!")
        password = input("Masukkan password admin: ").strip()
    
    password_confirm = input("Konfirmasi password: ").strip()
    while password != password_confirm:
        logger.warning("⚠️  Password tidak cocok!")
        password_confirm = input("Konfirmasi password: ").strip()
    
    email = input("Email (opsional, tekan Enter untuk skip): ").strip()
    email = email if email else None
    
    print("\n" + "=" * 60)
    print("KONFIRMASI")
    print("=" * 60)
    print(f"Username : {username}")
    print(f"Password : {'*' * len(password)}")
    print(f"Email    : {email or '(tidak diisi)'}")
    print("=" * 60)
    
    confirm = input("\nLanjutkan? (y/n): ").strip().lower()
    if confirm != 'y':
        logger.info("❌ Setup dibatalkan")
        sys.exit(0)
    
    create_first_admin(username, password, email)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if len(sys.argv) < 3:
            print("Usage: python admin_startup.py <username> <password> [email]")
            print("   atau jalankan tanpa argument untuk interactive mode:")
            print("       python admin_startup.py")
            sys.exit(1)
        
        username = sys.argv[1]
        password = sys.argv[2]
        email = sys.argv[3] if len(sys.argv) > 3 else None
        
        create_first_admin(username, password, email)
    else:
        interactive_setup()
