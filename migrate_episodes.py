"""
Migration Script: Tambah Support untuk Episodes System & Telegram File Storage

Menambahkan:
1. Tabel 'episodes' untuk menyimpan episode per film
2. Kolom baru di tabel 'movies' untuk Telegram file_id dan series support

Usage:
    python migrate_episodes.py
"""

import logging
from sqlalchemy import text
from database import SessionLocal, Base, engine
from config import DATABASE_URL

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def is_postgresql():
    """Check if database is PostgreSQL"""
    return DATABASE_URL.startswith('postgresql')

def column_exists(db, table_name, column_name):
    """Check if column exists in table (works for SQLite & PostgreSQL)"""
    try:
        if is_postgresql():
            result = db.execute(text(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='{table_name}' AND column_name='{column_name}'
            """))
            return result.fetchone() is not None
        else:
            # SQLite
            result = db.execute(text(f"PRAGMA table_info({table_name})"))
            columns = [row[1] for row in result.fetchall()]
            return column_name in columns
    except Exception as e:
        logger.error(f"Error checking column {table_name}.{column_name}: {e}")
        return False

def table_exists(db, table_name):
    """Check if table exists"""
    try:
        if is_postgresql():
            result = db.execute(text(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_name='{table_name}'
            """))
            return result.fetchone() is not None
        else:
            # SQLite
            result = db.execute(text(f"""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='{table_name}'
            """))
            return result.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking table {table_name}: {e}")
        return False

def migration_001_add_movies_telegram_columns():
    """
    Migration 001: Add Telegram-related columns to movies table
    
    Columns:
    - telegram_file_id: File ID dari Telegram (untuk film single)
    - telegram_chat_id: ID grup storage
    - telegram_message_id: ID pesan di grup
    - is_series: Boolean flag untuk series vs single film
    - total_episodes: Jumlah total episodes (untuk series)
    """
    logger.info("🔧 Running Migration 001: Add Telegram columns to movies")
    
    db = SessionLocal()
    try:
        columns_to_add = [
            ('telegram_file_id', 'VARCHAR(500)'),
            ('telegram_chat_id', 'VARCHAR(255)'),
            ('telegram_message_id', 'VARCHAR(255)'),
            ('is_series', 'BOOLEAN DEFAULT FALSE' if is_postgresql() else 'BOOLEAN DEFAULT 0'),
            ('total_episodes', 'INTEGER DEFAULT 0'),
        ]
        
        for column_name, column_type in columns_to_add:
            if column_exists(db, 'movies', column_name):
                logger.info(f"  ✓ Column movies.{column_name} already exists, skip")
                continue
            
            logger.info(f"  → Adding column movies.{column_name}...")
            
            if is_postgresql():
                # PostgreSQL syntax
                db.execute(text(f"""
                    ALTER TABLE movies 
                    ADD COLUMN {column_name} {column_type}
                """))
            else:
                # SQLite syntax
                db.execute(text(f"""
                    ALTER TABLE movies 
                    ADD COLUMN {column_name} {column_type}
                """))
            
            db.commit()
            logger.info(f"  ✅ Column movies.{column_name} added successfully")
        
        # Make video_link nullable (untuk films yang hanya punya file_id)
        logger.info("  → Making movies.video_link nullable...")
        # Note: SQLite tidak support ALTER COLUMN, jadi ini untuk PostgreSQL saja
        # Untuk SQLite, kita handle di aplikasi level
        if is_postgresql():
            try:
                db.execute(text("""
                    ALTER TABLE movies 
                    ALTER COLUMN video_link DROP NOT NULL
                """))
                db.commit()
                logger.info("  ✅ movies.video_link is now nullable")
            except Exception as e:
                logger.warning(f"  ⚠️  Could not make video_link nullable: {e}")
                logger.info("  → This is OK, we'll handle it at application level")
        
        logger.info("✅ Migration 001 completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration 001 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def migration_002_create_episodes_table():
    """
    Migration 002: Create episodes table
    
    Table structure:
    - id: Primary key
    - movie_id: Foreign key to movies.id
    - episode_number: Episode number (1, 2, 3...)
    - title: Episode title
    - telegram_file_id: Telegram file ID
    - telegram_chat_id: Storage group chat ID
    - telegram_message_id: Message ID in storage group
    - video_link: Fallback video link
    - duration: Video duration in seconds
    - file_size: File size in bytes
    - thumbnail_url: Thumbnail URL
    - views: View count
    - created_at, updated_at: Timestamps
    """
    logger.info("🔧 Running Migration 002: Create episodes table")
    
    db = SessionLocal()
    try:
        if table_exists(db, 'episodes'):
            logger.info("  ✓ Table episodes already exists, skip")
            return True
        
        logger.info("  → Creating episodes table...")
        
        if is_postgresql():
            # PostgreSQL
            db.execute(text("""
                CREATE TABLE episodes (
                    id SERIAL PRIMARY KEY,
                    movie_id VARCHAR(255) NOT NULL,
                    episode_number INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    
                    telegram_file_id VARCHAR(500),
                    telegram_chat_id VARCHAR(255),
                    telegram_message_id VARCHAR(255),
                    
                    video_link VARCHAR(500),
                    
                    duration INTEGER,
                    file_size BIGINT,
                    thumbnail_url VARCHAR(500),
                    
                    views INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
                    UNIQUE (movie_id, episode_number)
                )
            """))
        else:
            # SQLite
            db.execute(text("""
                CREATE TABLE episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    movie_id VARCHAR(255) NOT NULL,
                    episode_number INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    
                    telegram_file_id VARCHAR(500),
                    telegram_chat_id VARCHAR(255),
                    telegram_message_id VARCHAR(255),
                    
                    video_link VARCHAR(500),
                    
                    duration INTEGER,
                    file_size BIGINT,
                    thumbnail_url VARCHAR(500),
                    
                    views INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
                    UNIQUE (movie_id, episode_number)
                )
            """))
        
        db.commit()
        logger.info("  ✅ Table episodes created successfully")
        
        # Create indexes
        logger.info("  → Creating indexes...")
        
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_episodes_movie_id 
            ON episodes(movie_id)
        """))
        
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_episodes_number 
            ON episodes(episode_number)
        """))
        
        db.commit()
        logger.info("  ✅ Indexes created successfully")
        
        logger.info("✅ Migration 002 completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration 002 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def migration_003_create_pending_uploads_table():
    """
    Migration 003: Create pending_uploads table
    
    Table untuk menyimpan video yang baru diupload ke grup storage
    sebelum admin input metadata lengkap.
    
    Table structure:
    - id: Primary key
    - telegram_file_id: File ID video
    - telegram_chat_id: Storage group chat ID
    - telegram_message_id: Message ID
    - uploader_id: Telegram user ID yang upload
    - duration, file_size, thumbnail_url: Video metadata
    - status: pending/processed/cancelled
    - created_at: Timestamp
    """
    logger.info("🔧 Running Migration 003: Create pending_uploads table")
    
    db = SessionLocal()
    try:
        if table_exists(db, 'pending_uploads'):
            logger.info("  ✓ Table pending_uploads already exists, skip")
            return True
        
        logger.info("  → Creating pending_uploads table...")
        
        if is_postgresql():
            db.execute(text("""
                CREATE TABLE pending_uploads (
                    id SERIAL PRIMARY KEY,
                    telegram_file_id VARCHAR(500) NOT NULL,
                    telegram_chat_id VARCHAR(255) NOT NULL,
                    telegram_message_id VARCHAR(255) NOT NULL,
                    uploader_id VARCHAR(255) NOT NULL,
                    
                    duration INTEGER,
                    file_size BIGINT,
                    thumbnail_url VARCHAR(500),
                    
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    UNIQUE (telegram_message_id)
                )
            """))
        else:
            db.execute(text("""
                CREATE TABLE pending_uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_file_id VARCHAR(500) NOT NULL,
                    telegram_chat_id VARCHAR(255) NOT NULL,
                    telegram_message_id VARCHAR(255) NOT NULL,
                    uploader_id VARCHAR(255) NOT NULL,
                    
                    duration INTEGER,
                    file_size BIGINT,
                    thumbnail_url VARCHAR(500),
                    
                    status VARCHAR(50) DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    
                    UNIQUE (telegram_message_id)
                )
            """))
        
        db.commit()
        logger.info("  ✅ Table pending_uploads created successfully")
        
        # Create index
        logger.info("  → Creating indexes...")
        db.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_pending_uploads_status 
            ON pending_uploads(status)
        """))
        db.commit()
        logger.info("  ✅ Indexes created successfully")
        
        logger.info("✅ Migration 003 completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Migration 003 failed: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def run_all_migrations():
    """Run all migrations in order"""
    logger.info("="*60)
    logger.info("🚀 Starting Episodes System Migration")
    logger.info("="*60)
    logger.info(f"Database: {DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else 'SQLite'}")
    logger.info("")
    
    migrations = [
        ("001_add_movies_telegram_columns", migration_001_add_movies_telegram_columns),
        ("002_create_episodes_table", migration_002_create_episodes_table),
        ("003_create_pending_uploads_table", migration_003_create_pending_uploads_table),
    ]
    
    results = []
    
    for migration_name, migration_func in migrations:
        logger.info("")
        logger.info(f"Running {migration_name}...")
        success = migration_func()
        results.append((migration_name, success))
        
        if not success:
            logger.error(f"❌ Migration {migration_name} failed!")
            logger.error("Stopping migration process.")
            break
    
    # Summary
    logger.info("")
    logger.info("="*60)
    logger.info("📊 Migration Summary")
    logger.info("="*60)
    
    for migration_name, success in results:
        status = "✅ SUCCESS" if success else "❌ FAILED"
        logger.info(f"{status} - {migration_name}")
    
    all_success = all(success for _, success in results)
    
    if all_success:
        logger.info("")
        logger.info("🎉 All migrations completed successfully!")
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Set TELEGRAM_STORAGE_CHAT_ID environment variable")
        logger.info("2. Update bot.py with new handlers")
        logger.info("3. Update admin panel with episodes UI")
        logger.info("4. Test upload video ke grup storage")
    else:
        logger.error("")
        logger.error("❌ Some migrations failed. Please check logs above.")
    
    logger.info("="*60)
    
    return all_success

if __name__ == "__main__":
    run_all_migrations()
