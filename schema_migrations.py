"""
Simple migration system buat handle schema changes dengan aman.

Cara pakai:
1. Tambahin migration baru di MIGRATIONS list
2. Migration akan jalan otomatis saat startup
3. Track migration yang udah jalan di table schema_migrations
"""

import logging
import random
import string
from sqlalchemy import Column, String, DateTime, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from database import Base, SessionLocal, engine
from config import now_utc

logger = logging.getLogger(__name__)

class SchemaMigration(Base):
    """Track migration yang udah dijalankan"""
    __tablename__ = 'schema_migrations'
    
    migration_id = Column(String, primary_key=True)
    applied_at = Column(DateTime, default=now_utc)

def column_exists(db, table_name, column_name):
    """
    Check apakah kolom exist di table (works for SQLite & PostgreSQL)
    """
    from config import DATABASE_URL
    
    try:
        if DATABASE_URL.startswith('postgresql'):
            # PostgreSQL: pake information_schema
            result = db.execute(text(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='{table_name}' AND column_name='{column_name}'
            """))
            return result.fetchone() is not None
        else:
            # SQLite: pake PRAGMA
            result = db.execute(text(f"PRAGMA table_info({table_name})"))
            columns = [row[1] for row in result.fetchall()]
            return column_name in columns
    except Exception as e:
        logger.error(f"Error checking column {table_name}.{column_name}: {e}")
        return False

def run_migration_001_add_referred_by_code():
    """
    Migration 001: Add kolom referred_by_code ke table users
    
    Fix untuk production bug dimana kolom ini ga exist di database lama.
    Works untuk SQLite (dev) dan PostgreSQL (production).
    """
    logger.info("🔧 Running migration 001: Add users.referred_by_code")
    
    db = SessionLocal()
    try:
        # Cek apakah kolom udah ada
        if column_exists(db, 'users', 'referred_by_code'):
            logger.info("  ✓ Column referred_by_code already exists, skip")
            return True
        
        # Add kolom baru
        logger.info("  → Adding column referred_by_code...")
        db.execute(text("""
            ALTER TABLE users 
            ADD COLUMN referred_by_code VARCHAR
        """))
        
        # Add index buat performance
        logger.info("  → Creating index...")
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_users_referred_by_code 
            ON users(referred_by_code)
        """))
        
        db.commit()
        logger.info("  ✅ Migration 001 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 001 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_002_ensure_movie_columns():
    """
    Migration 002: Pastikan table movies punya kolom category dan views
    
    Fix untuk case dimana movies table dibuat sebelum kolom ini ditambahkan.
    Works untuk SQLite (dev) dan PostgreSQL (production).
    """
    logger.info("🔧 Running migration 002: Ensure movies.category and movies.views")
    
    db = SessionLocal()
    try:
        # Cek dan add category column
        if not column_exists(db, 'movies', 'category'):
            logger.info("  → Adding column category...")
            db.execute(text("""
                ALTER TABLE movies 
                ADD COLUMN category VARCHAR
            """))
        
        # Cek dan add views column
        if not column_exists(db, 'movies', 'views'):
            logger.info("  → Adding column views...")
            db.execute(text("""
                ALTER TABLE movies 
                ADD COLUMN views INTEGER DEFAULT 0
            """))
        
        # Update existing movies yang belum punya category/views
        db.execute(text("""
            UPDATE movies 
            SET category = 'Romance' 
            WHERE category IS NULL
        """))
        
        db.execute(text("""
            UPDATE movies 
            SET views = 0 
            WHERE views IS NULL
        """))
        
        db.commit()
        logger.info("  ✅ Migration 002 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 002 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def generate_short_id(length=8):
    """Generate random alphanumeric short ID"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))

def run_migration_003_add_short_id_to_movies():
    """
    Migration 003: Add kolom short_id ke table movies
    
    Fix untuk BUTTON_DATA_INVALID error di Telegram bot.
    Movie ID sekarang panjang banget (telegram_file_id), bikin callback data
    lebih dari 64 bytes. short_id akan dipake di callback data, lebih pendek.
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 003: Add movies.short_id")
    
    db = SessionLocal()
    try:
        # Step 1: Pastikan kolom short_id ada
        if not column_exists(db, 'movies', 'short_id'):
            logger.info("  → Adding column short_id...")
            db.execute(text("""
                ALTER TABLE movies 
                ADD COLUMN short_id VARCHAR
            """))
            db.commit()
        else:
            logger.info("  ✓ Column short_id already exists")
        
        # Step 2: Seed collision avoidance set dengan existing short_id values
        logger.info("  → Loading existing short_id values...")
        result = db.execute(text("SELECT short_id FROM movies WHERE short_id IS NOT NULL"))
        used_short_ids = {row[0] for row in result.fetchall()}
        logger.info(f"    • Found {len(used_short_ids)} existing short_ids")
        
        # Step 3: Backfill movies yang short_id-nya masih NULL
        logger.info("  → Backfilling NULL short_id values...")
        result = db.execute(text("SELECT id FROM movies WHERE short_id IS NULL"))
        movies_to_backfill = result.fetchall()
        
        if movies_to_backfill:
            logger.info(f"    • Backfilling {len(movies_to_backfill)} movies...")
            for movie in movies_to_backfill:
                movie_id = movie[0]
                
                # Generate unique short_id
                while True:
                    short_id = generate_short_id(8)
                    if short_id not in used_short_ids:
                        used_short_ids.add(short_id)
                        break
                
                # Update movie dengan short_id baru
                db.execute(
                    text("UPDATE movies SET short_id = :short_id WHERE id = :movie_id"),
                    {"short_id": short_id, "movie_id": movie_id}
                )
                logger.info(f"      • Movie {movie_id[:30]}... → short_id: {short_id}")
            
            db.commit()
            logger.info("  ✅ Backfill complete!")
        else:
            logger.info("  ✓ No NULL short_ids found, skip backfill")
        
        # Step 4: Verify data quality sebelum apply constraints
        logger.info("  → Verifying data quality...")
        
        # Check for NULL values
        result = db.execute(text("SELECT COUNT(*) FROM movies WHERE short_id IS NULL"))
        row = result.fetchone()
        null_count = row[0] if row else 0
        if null_count > 0:
            logger.error(f"  ❌ Found {null_count} movies with NULL short_id!")
            return False
        
        # Check for duplicates
        result = db.execute(text("""
            SELECT short_id, COUNT(*) as cnt 
            FROM movies 
            GROUP BY short_id 
            HAVING COUNT(*) > 1
        """))
        duplicates = result.fetchall()
        if duplicates:
            logger.error(f"  ❌ Found duplicate short_ids: {duplicates}")
            return False
        
        logger.info("  ✓ Data quality check passed")
        
        # Step 5: Apply NOT NULL constraint (PostgreSQL only)
        from config import DATABASE_URL
        if DATABASE_URL.startswith('postgresql'):
            # Check if NOT NULL constraint already exists
            try:
                result = db.execute(text("""
                    SELECT is_nullable 
                    FROM information_schema.columns 
                    WHERE table_name='movies' AND column_name='short_id'
                """))
                row = result.fetchone()
                if row and row[0] == 'YES':
                    logger.info("  → Setting short_id as NOT NULL...")
                    db.execute(text("""
                        ALTER TABLE movies 
                        ALTER COLUMN short_id SET NOT NULL
                    """))
                    db.commit()
                else:
                    logger.info("  ✓ short_id already NOT NULL")
            except Exception as e:
                logger.warning(f"  ⚠️  Could not check/set NOT NULL: {e}")
        else:
            logger.info("  → SQLite: Skip NOT NULL constraint check")
        
        # Step 6: Ensure unique index exists
        logger.info("  → Ensuring unique index exists...")
        db.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_movies_short_id 
            ON movies(short_id)
        """))
        db.commit()
        logger.info("  ✓ Unique index ready")
        
        logger.info("  ✅ Migration 003 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 003 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

# List semua migration yang harus dijalankan (urutan penting!)
def run_migration_004_add_poster_file_id():
    """
    Migration 004: Add kolom poster_file_id ke table movies
    
    Untuk support poster dari Telegram File ID.
    Admin bisa upload poster ke Telegram, copy File ID, dan paste di admin panel.
    Poster akan di-proxy via backend endpoint /api/poster/{file_id}.
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 004: Add movies.poster_file_id")
    
    db = SessionLocal()
    try:
        # Cek apakah kolom udah ada
        if column_exists(db, 'movies', 'poster_file_id'):
            logger.info("  ✓ Column poster_file_id already exists, skip")
            return True
        
        # Add kolom baru
        logger.info("  → Adding column poster_file_id...")
        db.execute(text("""
            ALTER TABLE movies 
            ADD COLUMN poster_file_id VARCHAR
        """))
        
        db.commit()
        logger.info("  ✅ Migration 004 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 004 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_005_add_poster_fields_to_pending_uploads():
    """
    Migration 005: Add kolom untuk poster di table pending_uploads
    
    Untuk support upload poster otomatis ke grup storage seperti video.
    Kolom baru:
    - content_type: 'video' atau 'poster'
    - poster_width: lebar poster (pixels)
    - poster_height: tinggi poster (pixels)
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 005: Add poster fields to pending_uploads")
    
    db = SessionLocal()
    try:
        # Add content_type column
        if not column_exists(db, 'pending_uploads', 'content_type'):
            logger.info("  → Adding column content_type...")
            db.execute(text("""
                ALTER TABLE pending_uploads 
                ADD COLUMN content_type VARCHAR DEFAULT 'video'
            """))
            # Set default value for existing rows
            db.execute(text("""
                UPDATE pending_uploads 
                SET content_type = 'video' 
                WHERE content_type IS NULL
            """))
        else:
            logger.info("  ✓ Column content_type already exists")
        
        # Add poster_width column
        if not column_exists(db, 'pending_uploads', 'poster_width'):
            logger.info("  → Adding column poster_width...")
            db.execute(text("""
                ALTER TABLE pending_uploads 
                ADD COLUMN poster_width INTEGER
            """))
        else:
            logger.info("  ✓ Column poster_width already exists")
        
        # Add poster_height column
        if not column_exists(db, 'pending_uploads', 'poster_height'):
            logger.info("  → Adding column poster_height...")
            db.execute(text("""
                ALTER TABLE pending_uploads 
                ADD COLUMN poster_height INTEGER
            """))
        else:
            logger.info("  ✓ Column poster_height already exists")
        
        db.commit()
        logger.info("  ✅ Migration 005 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 005 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_007_ensure_users_have_ref_codes():
    """
    Migration 007: Ensure semua users punya ref_code
    
    Backfill ref_code untuk existing users yang belum punya.
    Ini penting untuk sistem referral berfungsi dengan baik.
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 007: Ensure all users have ref_codes")
    
    db = SessionLocal()
    try:
        # Cek users yang belum punya ref_code
        result = db.execute(text("SELECT telegram_id FROM users WHERE ref_code IS NULL OR ref_code = ''"))
        users_without_ref = result.fetchall()
        
        if not users_without_ref:
            logger.info("  ✓ All users already have ref_codes, skip")
            return True
        
        logger.info(f"  → Found {len(users_without_ref)} users without ref_code, backfilling...")
        
        # Get existing ref_codes untuk avoid collision
        result = db.execute(text("SELECT ref_code FROM users WHERE ref_code IS NOT NULL AND ref_code != ''"))
        used_ref_codes = {row[0] for row in result.fetchall()}
        
        # Generate ref_code untuk setiap user yang belum punya
        for user in users_without_ref:
            telegram_id = user[0]
            
            # Generate unique ref_code
            while True:
                first_five = str(telegram_id)[:5]
                rand_part = ''.join(random.choices(string.ascii_letters + string.digits, k=4))
                ref_code = f"{first_five}{rand_part}"
                
                if ref_code not in used_ref_codes:
                    used_ref_codes.add(ref_code)
                    break
            
            # Update user dengan ref_code baru
            db.execute(
                text("UPDATE users SET ref_code = :ref_code WHERE telegram_id = :telegram_id"),
                {"ref_code": ref_code, "telegram_id": telegram_id}
            )
            logger.info(f"      • User {telegram_id} → ref_code: {ref_code}")
        
        db.commit()
        logger.info("  ✅ Migration 007 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 007 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_008_add_screenshot_url_to_payments():
    """
    Migration 008: Add kolom screenshot_url ke table payments
    
    Fix untuk production bug dimana kolom screenshot_url tidak exist.
    Kolom ini digunakan untuk menyimpan screenshot bukti transfer QRIS
    yang diupload user saat melakukan pembayaran manual.
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 008: Add payments.screenshot_url")
    
    db = SessionLocal()
    try:
        # Cek apakah kolom udah ada
        if column_exists(db, 'payments', 'screenshot_url'):
            logger.info("  ✓ Column screenshot_url already exists, skip")
            return True
        
        # Add kolom baru
        logger.info("  → Adding column screenshot_url...")
        db.execute(text("""
            ALTER TABLE payments 
            ADD COLUMN screenshot_url VARCHAR
        """))
        
        db.commit()
        logger.info("  ✅ Migration 008 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 008 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_006_add_admin_display_name_and_sessions():
    """
    Migration 006: Add display_name to admins table and create admin_sessions table
    
    For multi-admin login support with display names and session tracking.
    Allows super admin to manage and kick other admin sessions.
    
    Changes:
    - admins.display_name: Display name untuk ditampilkan di sidebar
    - admin_sessions table: Track active admin sessions untuk kick feature
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 006: Add admin display_name and sessions table")
    
    db = SessionLocal()
    try:
        from config import DATABASE_URL
        is_postgresql = DATABASE_URL.startswith('postgresql')
        
        # Add display_name column to admins
        if not column_exists(db, 'admins', 'display_name'):
            logger.info("  → Adding column admins.display_name...")
            db.execute(text("""
                ALTER TABLE admins 
                ADD COLUMN display_name VARCHAR
            """))
            logger.info("  ✓ Column display_name added")
        else:
            logger.info("  ✓ Column display_name already exists")
        
        # Create admin_sessions table if not exists
        if not column_exists(db, 'admin_sessions', 'id'):
            logger.info("  → Creating admin_sessions table...")
            
            if is_postgresql:
                db.execute(text("""
                    CREATE TABLE admin_sessions (
                        id SERIAL PRIMARY KEY,
                        admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                        session_token VARCHAR NOT NULL UNIQUE,
                        ip_address VARCHAR,
                        user_agent VARCHAR,
                        created_at TIMESTAMP NOT NULL,
                        last_activity TIMESTAMP NOT NULL,
                        expires_at TIMESTAMP NOT NULL
                    )
                """))
                db.execute(text("CREATE INDEX idx_admin_sessions_admin_id ON admin_sessions(admin_id)"))
                db.execute(text("CREATE INDEX idx_admin_sessions_session_token ON admin_sessions(session_token)"))
            else:
                db.execute(text("""
                    CREATE TABLE admin_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        admin_id INTEGER NOT NULL,
                        session_token VARCHAR NOT NULL UNIQUE,
                        ip_address VARCHAR,
                        user_agent VARCHAR,
                        created_at DATETIME NOT NULL,
                        last_activity DATETIME NOT NULL,
                        expires_at DATETIME NOT NULL,
                        FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE
                    )
                """))
                db.execute(text("CREATE INDEX idx_admin_sessions_admin_id ON admin_sessions(admin_id)"))
                db.execute(text("CREATE INDEX idx_admin_sessions_session_token ON admin_sessions(session_token)"))
            
            logger.info("  ✓ Table admin_sessions created")
        else:
            logger.info("  ✓ Table admin_sessions already exists")
        
        db.commit()
        logger.info("  ✅ Migration 006 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 006 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_009_add_drama_request_columns():
    """
    Migration 009: Add kolom admin_notes dan updated_at ke table drama_requests
    
    Fix untuk production bug dimana kolom admin_notes dan updated_at tidak exist.
    Kolom ini digunakan untuk:
    - admin_notes: Catatan admin tentang status permintaan drama
    - updated_at: Timestamp terakhir kali request di-update
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 009: Add drama_requests.admin_notes and updated_at")
    
    db = SessionLocal()
    try:
        # Add admin_notes column
        if not column_exists(db, 'drama_requests', 'admin_notes'):
            logger.info("  → Adding column admin_notes...")
            db.execute(text("""
                ALTER TABLE drama_requests 
                ADD COLUMN admin_notes TEXT
            """))
            logger.info("  ✓ Column admin_notes added")
        else:
            logger.info("  ✓ Column admin_notes already exists")
        
        # Add updated_at column
        if not column_exists(db, 'drama_requests', 'updated_at'):
            logger.info("  → Adding column updated_at...")
            db.execute(text("""
                ALTER TABLE drama_requests 
                ADD COLUMN updated_at TIMESTAMP
            """))
            
            # Set updated_at sama dengan created_at untuk data yang sudah ada
            logger.info("  → Setting updated_at = created_at for existing records...")
            db.execute(text("""
                UPDATE drama_requests 
                SET updated_at = created_at 
                WHERE updated_at IS NULL
            """))
            logger.info("  ✓ Column updated_at added and backfilled")
        else:
            logger.info("  ✓ Column updated_at already exists")
        
        db.commit()
        logger.info("  ✅ Migration 009 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 009 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_010_ensure_payment_commission_unique_constraint():
    """
    Migration 010: Ensure unique constraint on payment_commissions.payment_id
    
    BUG FIX #3: Prevent double commission payment race condition.
    Ensures that one payment can only have one commission entry.
    
    The constraint already exists in the ORM model (database.py line 208-210),
    but this migration ensures it's actually enforced in the database.
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 010: Ensure PaymentCommission unique constraint")
    
    db = SessionLocal()
    try:
        from config import DATABASE_URL
        is_postgresql = DATABASE_URL.startswith('postgresql')
        
        # Check if unique constraint already exists
        logger.info("  → Checking for existing constraint...")
        
        if is_postgresql:
            result = db.execute(text("""
                SELECT constraint_name 
                FROM information_schema.table_constraints 
                WHERE table_name='payment_commissions' 
                AND constraint_type='UNIQUE'
                AND constraint_name='uq_payment_commission'
            """))
            existing_constraint = result.fetchone()
            
            if existing_constraint:
                logger.info("  ✓ Unique constraint already exists")
                return True
            
            logger.info("  → Creating unique constraint on payment_id...")
            db.execute(text("""
                ALTER TABLE payment_commissions 
                ADD CONSTRAINT uq_payment_commission UNIQUE (payment_id)
            """))
        else:
            # SQLite: Check for unique index
            result = db.execute(text("""
                SELECT name FROM sqlite_master 
                WHERE type='index' 
                AND tbl_name='payment_commissions' 
                AND sql LIKE '%UNIQUE%payment_id%'
            """))
            existing_index = result.fetchone()
            
            if existing_index:
                logger.info("  ✓ Unique index already exists")
                return True
            
            logger.info("  → Creating unique index on payment_id...")
            db.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_payment_commission 
                ON payment_commissions(payment_id)
            """))
        
        db.commit()
        logger.info("  ✅ Migration 010 complete! Double commission race condition prevented.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 010 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_011_add_soft_delete_columns():
    """
    Migration 011: Add deleted_at columns for soft delete functionality
    
    BUG FIX #8: Replace hard delete with soft delete for better data integrity.
    Adds nullable deleted_at timestamp to tables that need soft delete:
    - movies: Prevent permanent data loss of content
    - parts: Keep episode history
    - admins: Audit trail for admin accounts
    - broadcasts: Keep broadcast history
    
    Soft delete pattern:
    - deleted_at IS NULL: Active records (default filter)
    - deleted_at IS NOT NULL: Deleted records (hidden from normal queries)
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 011: Add soft delete (deleted_at) columns")
    
    db = SessionLocal()
    try:
        from config import DATABASE_URL
        is_postgresql = DATABASE_URL.startswith('postgresql')
        
        # Tables that need soft delete
        tables_to_update = ['movies', 'parts', 'admins', 'broadcasts']
        
        for table in tables_to_update:
            if not column_exists(db, table, 'deleted_at'):
                logger.info(f"  → Adding deleted_at to {table}...")
                
                if is_postgresql:
                    db.execute(text(f"""
                        ALTER TABLE {table} 
                        ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE
                    """))
                else:
                    db.execute(text(f"""
                        ALTER TABLE {table} 
                        ADD COLUMN deleted_at DATETIME
                    """))
                
                # Add index for performance (filtering by deleted_at IS NULL is common)
                logger.info(f"  → Adding index on {table}.deleted_at...")
                db.execute(text(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table}_deleted_at 
                    ON {table}(deleted_at)
                """))
                
                logger.info(f"  ✓ Table {table} now supports soft delete")
            else:
                logger.info(f"  ✓ Table {table} already has deleted_at column")
        
        db.commit()
        logger.info("  ✅ Migration 011 complete! Soft delete enabled for critical tables.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 011 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_012_add_performance_indexes():
    """
    Migration 012: Add database indexes for frequently queried columns
    
    BUG FIX #11: Improve query performance by adding missing indexes.
    Prevents full table scans on common query patterns.
    
    Indexes added:
    - payments.transaction_id: Used in payment status checks
    - payments.status: Filtered in many queries (pending, success)
    - payments (telegram_id, status): Composite for user payment history
    - withdrawals.status: Filtered for pending withdrawals
    - broadcasts.is_active: Filtered for active broadcasts only
    - watch_history.movie_id: Used in view count aggregations
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 012: Add performance indexes")
    
    db = SessionLocal()
    try:
        # Define indexes to create
        indexes = [
            # Payments table indexes
            ("idx_payments_transaction_id", "payments", "transaction_id"),
            ("idx_payments_status", "payments", "status"),
            
            # Withdrawals table indexes
            ("idx_withdrawals_status", "withdrawals", "status"),
            
            # Broadcasts table indexes
            ("idx_broadcasts_is_active", "broadcasts", "is_active"),
            
            # Watch history indexes
            ("idx_watch_history_movie_id", "watch_history", "movie_id"),
        ]
        
        for index_name, table, column in indexes:
            logger.info(f"  → Creating index {index_name} on {table}({column})...")
            try:
                db.execute(text(f"""
                    CREATE INDEX IF NOT EXISTS {index_name} 
                    ON {table}({column})
                """))
                logger.info(f"  ✓ Index {index_name} created")
            except Exception as idx_error:
                logger.warning(f"  ⚠️ Index {index_name} skipped: {idx_error}")
        
        # Composite index for user payment history queries
        logger.info("  → Creating composite index on payments(telegram_id, status)...")
        try:
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_payments_telegram_id_status 
                ON payments(telegram_id, status)
            """))
            logger.info("  ✓ Composite index created")
        except Exception as idx_error:
            logger.warning(f"  ⚠️ Composite index skipped: {idx_error}")
        
        db.commit()
        logger.info("  ✅ Migration 012 complete! Query performance improved.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 012 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_013_add_csrf_token_to_admin_sessions():
    """
    Migration 013: Add csrf_token column to admin_sessions table
    
    BUG FIX #2: Add CSRF protection to admin endpoints.
    Stores per-session CSRF token for validating state-changing requests.
    
    Security improvement:
    - Prevents Cross-Site Request Forgery attacks
    - Each admin session gets unique CSRF token
    - Token validated on all POST/PUT/DELETE/PATCH requests
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 013: Add csrf_token to admin_sessions")
    
    db = SessionLocal()
    try:
        # Check if csrf_token column already exists
        if column_exists(db, 'admin_sessions', 'csrf_token'):
            logger.info("  ✓ Column csrf_token already exists, skip")
            return True
        
        # Add csrf_token column
        logger.info("  → Adding column csrf_token to admin_sessions...")
        db.execute(text("""
            ALTER TABLE admin_sessions 
            ADD COLUMN csrf_token VARCHAR
        """))
        
        # Add index for performance
        logger.info("  → Creating index on csrf_token...")
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_admin_sessions_csrf_token 
            ON admin_sessions(csrf_token)
        """))
        
        db.commit()
        logger.info("  ✅ Migration 013 complete! CSRF protection schema ready.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 013 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_014_add_deleted_at_to_users_and_requests():
    """
    Migration 014: Add deleted_at columns to users and drama_requests tables
    
    BUG FIX #8: Complete soft delete implementation for ALL tables with delete operations.
    This migration adds deleted_at columns to tables that were missing them.
    
    Tables updated:
    - users: Soft delete for user accounts
    - drama_requests: Soft delete for drama requests
    """
    logger.info("🔧 Running migration 014: Add deleted_at to users and drama_requests")
    
    from config import DATABASE_URL
    db = SessionLocal()
    
    try:
        tables = ['users', 'drama_requests']
        
        for table in tables:
            if not column_exists(db, table, 'deleted_at'):
                logger.info(f"  → Adding deleted_at to {table}...")
                
                # Add column (Postgres vs SQLite syntax)
                if DATABASE_URL.startswith('postgresql'):
                    db.execute(text(f"""
                        ALTER TABLE {table} 
                        ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE
                    """))
                else:
                    db.execute(text(f"""
                        ALTER TABLE {table} 
                        ADD COLUMN deleted_at DATETIME
                    """))
                
                # Add index for performance (filtering by deleted_at IS NULL is common)
                logger.info(f"  → Adding index on {table}.deleted_at...")
                db.execute(text(f"""
                    CREATE INDEX IF NOT EXISTS idx_{table}_deleted_at 
                    ON {table}(deleted_at)
                """))
                
                logger.info(f"  ✓ Added deleted_at to {table}")
            else:
                logger.info(f"  ✓ Table {table} already has deleted_at column")
        
        db.commit()
        logger.info("  ✅ Migration 014 complete! Users and drama_requests now support soft delete.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 014 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_015_add_deleted_at_to_broadcasts():
    """
    Migration 015: Add deleted_at column to broadcasts table
    
    This completes the soft delete implementation for the Broadcast model.
    Broadcasts can now be soft-deleted instead of hard-deleted.
    """
    logger.info("🔧 Running migration 015: Add deleted_at to broadcasts")
    
    from config import DATABASE_URL
    
    db = SessionLocal()
    try:
        if not column_exists(db, 'broadcasts', 'deleted_at'):
            logger.info(f"  → Adding deleted_at to broadcasts...")
            
            # Use appropriate type for database dialect
            if DATABASE_URL.startswith('postgresql'):
                db.execute(text("""
                    ALTER TABLE broadcasts 
                    ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE
                """))
            else:
                db.execute(text("""
                    ALTER TABLE broadcasts 
                    ADD COLUMN deleted_at DATETIME
                """))
            
            # Add index for performance
            logger.info(f"  → Adding index on broadcasts.deleted_at...")
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_broadcasts_deleted_at 
                ON broadcasts(deleted_at)
            """))
            
            logger.info(f"  ✓ Added deleted_at to broadcasts")
        else:
            logger.info(f"  ✓ Table broadcasts already has deleted_at column")
        
        db.commit()
        logger.info("  ✅ Migration 015 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 015 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_016_add_telegram_columns_to_movies():
    """
    Migration 016: Add telegram columns and series-related columns to movies table
    
    These columns are used to store telegram video metadata and series configuration.
    """
    logger.info("🔧 Running migration 016: Add telegram and series columns to movies")
    
    db = SessionLocal()
    try:
        columns_to_add = [
            ('telegram_file_id', 'VARCHAR'),
            ('telegram_chat_id', 'VARCHAR'),
            ('telegram_message_id', 'VARCHAR'),
            ('is_series', 'BOOLEAN DEFAULT 0'),
            ('total_parts', 'INTEGER DEFAULT 0'),
            ('deleted_at', 'DATETIME'),
        ]
        
        for col_name, col_type in columns_to_add:
            if not column_exists(db, 'movies', col_name):
                logger.info(f"  → Adding {col_name} to movies...")
                db.execute(text(f"""
                    ALTER TABLE movies 
                    ADD COLUMN {col_name} {col_type}
                """))
                logger.info(f"  ✓ Added {col_name} to movies")
            else:
                logger.info(f"  ✓ Column movies.{col_name} already exists")
        
        db.commit()
        logger.info("  ✅ Migration 016 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 016 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_017_add_series_columns_to_movies():
    """
    Migration 017: Add is_series, total_parts, and deleted_at columns to movies table
    
    These columns support drama series and soft delete functionality.
    """
    logger.info("🔧 Running migration 017: Add series and deleted_at columns to movies")
    
    db = SessionLocal()
    try:
        columns_to_add = [
            ('is_series', 'BOOLEAN DEFAULT 0'),
            ('total_parts', 'INTEGER DEFAULT 0'),
            ('deleted_at', 'DATETIME'),
        ]
        
        for col_name, col_type in columns_to_add:
            if not column_exists(db, 'movies', col_name):
                logger.info(f"  → Adding {col_name} to movies...")
                db.execute(text(f"""
                    ALTER TABLE movies 
                    ADD COLUMN {col_name} {col_type}
                """))
                logger.info(f"  ✓ Added {col_name} to movies")
            else:
                logger.info(f"  ✓ Column movies.{col_name} already exists")
        
        db.commit()
        logger.info("  ✅ Migration 017 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 017 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_018_fix_missing_columns():
    """
    Migration 018: Fix ALL missing columns in production database
    
    CRITICAL FIX: Comprehensive check for ALL columns that may be missing.
    This migration ensures ALL required columns exist:
    
    Table: users
    - deleted_at: Soft delete support
    
    Table: payments
    - transaction_id: QRIS.PW transaction ID storage
    - qris_url: QRIS QR code URL
    - qris_string: QRIS string for dynamic QR
    - expires_at: Payment expiration timestamp
    - paid_at: Payment completion timestamp
    
    Table: drama_requests
    - apk_source: Source APK for drama request
    
    Total: 7 kolom yang diperbaiki
    Migration ini idempotent - cek dulu sebelum create.
    """
    logger.info("🔧 Running migration 018: Fix missing columns (users.deleted_at, payments.transaction_id)")
    
    from config import DATABASE_URL
    db = SessionLocal()
    
    try:
        is_postgresql = DATABASE_URL.startswith('postgresql')
        
        # Fix 1: users.deleted_at
        if not column_exists(db, 'users', 'deleted_at'):
            logger.info("  → Adding deleted_at to users...")
            if is_postgresql:
                db.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE
                """))
            else:
                db.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN deleted_at DATETIME
                """))
            
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_users_deleted_at 
                ON users(deleted_at)
            """))
            logger.info("  ✓ Added users.deleted_at")
        else:
            logger.info("  ✓ Column users.deleted_at already exists")
        
        # Fix 2: payments.transaction_id
        if not column_exists(db, 'payments', 'transaction_id'):
            logger.info("  → Adding transaction_id to payments...")
            db.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN transaction_id VARCHAR
            """))
            
            db.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_payments_transaction_id 
                ON payments(transaction_id)
            """))
            logger.info("  ✓ Added payments.transaction_id")
        else:
            logger.info("  ✓ Column payments.transaction_id already exists")
        
        # Fix 3: payments.qris_url
        if not column_exists(db, 'payments', 'qris_url'):
            logger.info("  → Adding qris_url to payments...")
            db.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN qris_url VARCHAR
            """))
            logger.info("  ✓ Added payments.qris_url")
        else:
            logger.info("  ✓ Column payments.qris_url already exists")
        
        # Fix 4: payments.qris_string
        if not column_exists(db, 'payments', 'qris_string'):
            logger.info("  → Adding qris_string to payments...")
            db.execute(text("""
                ALTER TABLE payments 
                ADD COLUMN qris_string TEXT
            """))
            logger.info("  ✓ Added payments.qris_string")
        else:
            logger.info("  ✓ Column payments.qris_string already exists")
        
        # Fix 5: payments.expires_at
        if not column_exists(db, 'payments', 'expires_at'):
            logger.info("  → Adding expires_at to payments...")
            if is_postgresql:
                db.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE
                """))
            else:
                db.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN expires_at DATETIME
                """))
            logger.info("  ✓ Added payments.expires_at")
        else:
            logger.info("  ✓ Column payments.expires_at already exists")
        
        # Fix 6: payments.paid_at
        if not column_exists(db, 'payments', 'paid_at'):
            logger.info("  → Adding paid_at to payments...")
            if is_postgresql:
                db.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN paid_at TIMESTAMP WITH TIME ZONE
                """))
            else:
                db.execute(text("""
                    ALTER TABLE payments 
                    ADD COLUMN paid_at DATETIME
                """))
            logger.info("  ✓ Added payments.paid_at")
        else:
            logger.info("  ✓ Column payments.paid_at already exists")
        
        # Fix 7: drama_requests.apk_source
        if not column_exists(db, 'drama_requests', 'apk_source'):
            logger.info("  → Adding apk_source to drama_requests...")
            db.execute(text("""
                ALTER TABLE drama_requests 
                ADD COLUMN apk_source VARCHAR
            """))
            logger.info("  ✓ Added drama_requests.apk_source")
        else:
            logger.info("  ✓ Column drama_requests.apk_source already exists")
        
        db.commit()
        logger.info("  ✅ Migration 018 complete! All missing columns fixed.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 018 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_019_add_email_to_admins():
    """
    Migration 019: Add email column to admins table
    
    Kolom email untuk admin, digunakan untuk:
    - Recovery password
    - Notifikasi admin
    - Identifikasi tambahan
    
    Migration ini idempotent - bisa dijalankan berulang kali dengan aman.
    """
    logger.info("🔧 Running migration 019: Add admins.email")
    
    db = SessionLocal()
    try:
        if column_exists(db, 'admins', 'email'):
            logger.info("  ✓ Column email already exists, skip")
            return True
        
        logger.info("  → Adding column email to admins...")
        db.execute(text("""
            ALTER TABLE admins 
            ADD COLUMN email VARCHAR
        """))
        
        db.commit()
        logger.info("  ✅ Migration 019 complete!")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 019 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def table_exists(db, table_name):
    """
    Check apakah table exist (works for SQLite & PostgreSQL)
    """
    from config import DATABASE_URL
    
    try:
        if DATABASE_URL.startswith('postgresql'):
            result = db.execute(text(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name='{table_name}' AND table_schema='public'
            """))
            return result.fetchone() is not None
        else:
            result = db.execute(text(f"""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='{table_name}'
            """))
            return result.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking table {table_name}: {e}")
        return False

def run_migration_020_create_admin_conversations_table():
    """
    Migration 020: Create admin_conversations table if not exists
    
    Table untuk menyimpan state conversation admin di Telegram bot.
    Digunakan untuk multi-step workflows seperti:
    - Upload movie/part
    - Edit movie details
    - Broadcast messages
    
    Migration ini idempotent - cek dulu sebelum create.
    """
    logger.info("🔧 Running migration 020: Create admin_conversations table")
    
    from config import DATABASE_URL
    db = SessionLocal()
    
    try:
        if table_exists(db, 'admin_conversations'):
            logger.info("  ✓ Table admin_conversations already exists, skip")
            return True
        
        logger.info("  → Creating admin_conversations table...")
        
        if DATABASE_URL.startswith('postgresql'):
            db.execute(text("""
                CREATE TABLE admin_conversations (
                    id SERIAL PRIMARY KEY,
                    admin_id VARCHAR NOT NULL,
                    conversation_type VARCHAR NOT NULL,
                    step VARCHAR NOT NULL,
                    data TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.execute(text("CREATE INDEX idx_admin_conversations_admin_id ON admin_conversations(admin_id)"))
        else:
            db.execute(text("""
                CREATE TABLE admin_conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_id VARCHAR NOT NULL,
                    conversation_type VARCHAR NOT NULL,
                    step VARCHAR NOT NULL,
                    data TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.execute(text("CREATE INDEX idx_admin_conversations_admin_id ON admin_conversations(admin_id)"))
        
        db.commit()
        logger.info("  ✅ Migration 020 complete! admin_conversations table created.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 020 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_migration_021_create_settings_table():
    """
    Migration 021: Create settings table if not exists
    
    Table untuk menyimpan konfigurasi aplikasi:
    - Payment gateway settings (QRIS API key, merchant ID)
    - Bot settings
    - Feature flags
    
    Migration ini idempotent - cek dulu sebelum create.
    """
    logger.info("🔧 Running migration 021: Create settings table")
    
    from config import DATABASE_URL
    db = SessionLocal()
    
    try:
        if table_exists(db, 'settings'):
            logger.info("  ✓ Table settings already exists, skip")
            return True
        
        logger.info("  → Creating settings table...")
        
        if DATABASE_URL.startswith('postgresql'):
            db.execute(text("""
                CREATE TABLE settings (
                    id SERIAL PRIMARY KEY,
                    key VARCHAR NOT NULL UNIQUE,
                    value TEXT,
                    description TEXT,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_by VARCHAR
                )
            """))
            db.execute(text("CREATE UNIQUE INDEX idx_settings_key ON settings(key)"))
        else:
            db.execute(text("""
                CREATE TABLE settings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key VARCHAR NOT NULL UNIQUE,
                    value TEXT,
                    description TEXT,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_by VARCHAR
                )
            """))
            db.execute(text("CREATE UNIQUE INDEX idx_settings_key ON settings(key)"))
        
        db.commit()
        logger.info("  ✅ Migration 021 complete! settings table created.")
        return True
        
    except Exception as e:
        logger.error(f"  ❌ Migration 021 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

MIGRATIONS = [
    ('001_add_referred_by_code', run_migration_001_add_referred_by_code),
    ('002_ensure_movie_columns', run_migration_002_ensure_movie_columns),
    ('003_add_short_id_to_movies', run_migration_003_add_short_id_to_movies),
    ('004_add_poster_file_id', run_migration_004_add_poster_file_id),
    ('005_add_poster_fields_to_pending_uploads', run_migration_005_add_poster_fields_to_pending_uploads),
    ('006_add_admin_display_name_and_sessions', run_migration_006_add_admin_display_name_and_sessions),
    ('007_ensure_users_have_ref_codes', run_migration_007_ensure_users_have_ref_codes),
    ('008_add_screenshot_url_to_payments', run_migration_008_add_screenshot_url_to_payments),
    ('009_add_drama_request_columns', run_migration_009_add_drama_request_columns),
    ('010_ensure_payment_commission_unique_constraint', run_migration_010_ensure_payment_commission_unique_constraint),
    ('011_add_soft_delete_columns', run_migration_011_add_soft_delete_columns),
    ('012_add_performance_indexes', run_migration_012_add_performance_indexes),
    ('013_add_csrf_token_to_admin_sessions', run_migration_013_add_csrf_token_to_admin_sessions),
    ('014_add_deleted_at_to_users_and_requests', run_migration_014_add_deleted_at_to_users_and_requests),
    ('015_add_deleted_at_to_broadcasts', run_migration_015_add_deleted_at_to_broadcasts),
    ('016_add_telegram_columns_to_movies', run_migration_016_add_telegram_columns_to_movies),
    ('017_add_series_columns_to_movies', run_migration_017_add_series_columns_to_movies),
    ('018_fix_missing_columns', run_migration_018_fix_missing_columns),
    ('019_add_email_to_admins', run_migration_019_add_email_to_admins),
    ('020_create_admin_conversations_table', run_migration_020_create_admin_conversations_table),
    ('021_create_settings_table', run_migration_021_create_settings_table),
]

def run_migrations():
    """
    Jalankan semua pending migrations.
    
    Return True kalau semua sukses, False kalau ada yang gagal.
    """
    logger.info("=" * 80)
    logger.info("🗄️ Checking database migrations...")
    logger.info("=" * 80)
    
    # Buat table schema_migrations kalau belum ada
    try:
        Base.metadata.create_all(bind=engine, tables=[SchemaMigration.__table__])
    except Exception as e:
        logger.error(f"❌ Gagal buat table schema_migrations: {e}")
        return False
    
    db = SessionLocal()
    try:
        # Ambil list migration yang udah jalan
        applied = {m.migration_id for m in db.query(SchemaMigration).all()}
        
        # Jalankan pending migrations
        pending = [(mid, func) for mid, func in MIGRATIONS if mid not in applied]
        
        if not pending:
            logger.info("✅ Semua migrations udah jalan, database up-to-date!")
            return True
        
        logger.info(f"📋 Found {len(pending)} pending migration(s)")
        
        for migration_id, migration_func in pending:
            logger.info(f"\n🔄 Applying: {migration_id}")
            
            # Jalankan migration
            success = migration_func()
            
            if not success:
                logger.error(f"❌ Migration {migration_id} gagal, stop!")
                return False
            
            # Catat migration yang udah jalan
            db.add(SchemaMigration(
                migration_id=migration_id,
                applied_at=now_utc()
            ))
            db.commit()
            logger.info(f"✅ Migration {migration_id} recorded")
        
        logger.info("")
        logger.info("=" * 80)
        logger.info("✅ Semua migrations berhasil!")
        logger.info("=" * 80)
        return True
        
    except Exception as e:
        logger.error(f"❌ Error saat run migrations: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def validate_critical_schema():
    """
    Validate bahwa semua tables dan critical columns ada di database.
    
    Comprehensive check untuk semua 16 tables:
    1. users - User accounts
    2. movies - Movie/drama content
    3. favorites - User favorites
    4. likes - User likes
    5. watch_history - Watch tracking
    6. drama_requests - Drama requests
    7. withdrawals - Withdrawal requests
    8. payments - Payment records
    9. payment_commissions - Commission tracking
    10. admins - Admin accounts
    11. admin_sessions - Admin sessions
    12. parts - Series episodes
    13. pending_uploads - Upload queue
    14. admin_conversations - Admin conversation state
    15. settings - App settings
    16. broadcasts - Broadcast messages
    
    Kalau ada yang missing, kasih error jelas supaya user bisa fix.
    """
    logger.info("🔍 Validating database schema (all 16 tables)...")
    
    db = SessionLocal()
    try:
        critical_tests = [
            ("users", ["id", "telegram_id", "ref_code", "referred_by_code", "is_vip", "deleted_at"]),
            ("movies", ["id", "short_id", "title", "category", "views", "poster_file_id", "telegram_file_id", "is_series", "deleted_at"]),
            ("favorites", ["id", "telegram_id", "movie_id"]),
            ("likes", ["id", "telegram_id", "movie_id"]),
            ("watch_history", ["id", "telegram_id", "movie_id", "watched_at"]),
            ("drama_requests", ["id", "telegram_id", "judul", "status", "admin_notes", "apk_source", "deleted_at"]),
            ("withdrawals", ["id", "telegram_id", "amount", "status", "processed_at"]),
            ("payments", ["id", "telegram_id", "order_id", "transaction_id", "status", "screenshot_url", "qris_url", "expires_at", "paid_at"]),
            ("payment_commissions", ["id", "payment_id", "referrer_telegram_id", "commission_amount"]),
            ("admins", ["id", "username", "password_hash", "email", "display_name", "deleted_at"]),
            ("admin_sessions", ["id", "admin_id", "session_token", "csrf_token", "expires_at"]),
            ("parts", ["id", "movie_id", "part_number", "title", "telegram_file_id", "deleted_at"]),
            ("pending_uploads", ["id", "telegram_file_id", "content_type", "poster_width", "poster_height"]),
            ("admin_conversations", ["id", "admin_id", "conversation_type", "step"]),
            ("settings", ["id", "key", "value"]),
            ("broadcasts", ["id", "message", "is_active", "broadcast_type", "deleted_at"]),
        ]
        
        all_valid = True
        for table, columns in critical_tests:
            try:
                col_str = ", ".join(columns)
                query = text(f"SELECT {col_str} FROM {table} LIMIT 1")
                db.execute(query)
                logger.info(f"  ✓ Table '{table}' ({len(columns)} columns) valid")
            except (OperationalError, ProgrammingError) as e:
                logger.error(f"  ❌ Table '{table}' schema invalid: {e}")
                logger.error(f"     Expected columns: {columns}")
                all_valid = False
        
        if all_valid:
            logger.info("✅ Schema validation passed! All 16 tables verified.")
            return True
        else:
            logger.error("❌ Schema validation failed! Run migrations to fix.")
            return False
        
    except Exception as e:
        logger.error(f"❌ Schema validation error: {e}")
        return False
    finally:
        db.close()
