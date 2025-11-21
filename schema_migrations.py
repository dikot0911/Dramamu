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

MIGRATIONS = [
    ('001_add_referred_by_code', run_migration_001_add_referred_by_code),
    ('002_ensure_movie_columns', run_migration_002_ensure_movie_columns),
    ('003_add_short_id_to_movies', run_migration_003_add_short_id_to_movies),
    ('004_add_poster_file_id', run_migration_004_add_poster_file_id),
    ('005_add_poster_fields_to_pending_uploads', run_migration_005_add_poster_fields_to_pending_uploads),
    ('006_add_admin_display_name_and_sessions', run_migration_006_add_admin_display_name_and_sessions),
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
    Validate bahwa critical columns yang dibutuhkan app ada di database.
    
    Kalau ada yang missing, kasih error jelas supaya user bisa fix.
    """
    logger.info("🔍 Validating database schema...")
    
    db = SessionLocal()
    try:
        # Test query critical columns
        critical_tests = [
            ("users", ["telegram_id", "ref_code", "referred_by_code", "is_vip"]),
            ("movies", ["id", "title", "category", "views"]),
            ("admins", ["username", "password_hash", "display_name"]),
        ]
        
        for table, columns in critical_tests:
            try:
                # Build select query
                col_str = ", ".join(columns)
                query = text(f"SELECT {col_str} FROM {table} LIMIT 1")
                db.execute(query)
                logger.info(f"  ✓ Table '{table}' schema valid")
            except (OperationalError, ProgrammingError) as e:
                logger.error(f"  ❌ Table '{table}' schema invalid: {e}")
                logger.error(f"     Missing columns: run migrations!")
                return False
        
        logger.info("✅ Schema validation passed!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Schema validation error: {e}")
        return False
    finally:
        db.close()
