"""
Migration: Add qris_string column to payments table
Date: 2025-11-25
Purpose: Store QRIS string content for QR code generation
"""

import sys
from sqlalchemy import text, inspect
from database import SessionLocal, engine
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_qris_string_column():
    """Add qris_string column to payments table if it doesn't exist"""
    db = SessionLocal()
    try:
        # Check if column already exists
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('payments')]
        
        if 'qris_string' in columns:
            logger.info("✅ Column 'qris_string' already exists in payments table")
            return True
        
        logger.info("Adding 'qris_string' column to payments table...")
        
        # Add column - syntax berbeda untuk SQLite vs PostgreSQL
        db_url = str(engine.url)
        if 'postgresql' in db_url:
            # PostgreSQL syntax
            db.execute(text("ALTER TABLE payments ADD COLUMN qris_string TEXT"))
        else:
            # SQLite syntax
            db.execute(text("ALTER TABLE payments ADD COLUMN qris_string TEXT"))
        
        db.commit()
        logger.info("✅ Successfully added 'qris_string' column to payments table")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error adding column: {e}")
        db.rollback()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🔄 Starting migration: Add qris_string column")
    logger.info("=" * 60)
    
    success = add_qris_string_column()
    
    if success:
        logger.info("=" * 60)
        logger.info("✅ Migration completed successfully!")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("=" * 60)
        logger.error("❌ Migration failed!")
        logger.error("=" * 60)
        sys.exit(1)
