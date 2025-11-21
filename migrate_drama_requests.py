"""
Migration script to add admin_notes and updated_at columns to drama_requests table
"""
import sqlite3
import os
from datetime import datetime

def migrate_sqlite():
    """Migrate SQLite database"""
    db_path = 'dramamu.db'
    
    if not os.path.exists(db_path):
        print(f"Database {db_path} tidak ditemukan. Skip migration SQLite.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if columns already exist
        cursor.execute("PRAGMA table_info(drama_requests)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'admin_notes' not in columns:
            print("Menambahkan kolom admin_notes...")
            cursor.execute("ALTER TABLE drama_requests ADD COLUMN admin_notes TEXT")
            print("✓ Kolom admin_notes berhasil ditambahkan")
        else:
            print("Kolom admin_notes sudah ada")
        
        if 'updated_at' not in columns:
            print("Menambahkan kolom updated_at...")
            cursor.execute("ALTER TABLE drama_requests ADD COLUMN updated_at TIMESTAMP")
            # Set updated_at sama dengan created_at untuk data yang sudah ada
            cursor.execute("UPDATE drama_requests SET updated_at = created_at WHERE updated_at IS NULL")
            print("✓ Kolom updated_at berhasil ditambahkan")
        else:
            print("Kolom updated_at sudah ada")
        
        conn.commit()
        print("\n✓ Migration SQLite berhasil!")
        
    except Exception as e:
        print(f"✗ Error migration SQLite: {e}")
        conn.rollback()
    finally:
        conn.close()

def migrate_postgres():
    """Migrate PostgreSQL database"""
    try:
        from database import engine, DramaRequest
        from sqlalchemy import text
        
        with engine.connect() as conn:
            # Check if columns exist
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'drama_requests'
            """))
            columns = [row[0] for row in result]
            
            if 'admin_notes' not in columns:
                print("Menambahkan kolom admin_notes ke PostgreSQL...")
                conn.execute(text("ALTER TABLE drama_requests ADD COLUMN admin_notes TEXT"))
                conn.commit()
                print("✓ Kolom admin_notes berhasil ditambahkan")
            else:
                print("Kolom admin_notes sudah ada di PostgreSQL")
            
            if 'updated_at' not in columns:
                print("Menambahkan kolom updated_at ke PostgreSQL...")
                conn.execute(text("ALTER TABLE drama_requests ADD COLUMN updated_at TIMESTAMP"))
                conn.execute(text("UPDATE drama_requests SET updated_at = created_at WHERE updated_at IS NULL"))
                conn.commit()
                print("✓ Kolom updated_at berhasil ditambahkan")
            else:
                print("Kolom updated_at sudah ada di PostgreSQL")
        
        print("\n✓ Migration PostgreSQL berhasil!")
        
    except ImportError:
        print("PostgreSQL dependencies tidak tersedia. Skip migration PostgreSQL.")
    except Exception as e:
        print(f"✗ Error migration PostgreSQL: {e}")

if __name__ == "__main__":
    print("=== Drama Request Migration ===\n")
    
    # Try SQLite first
    migrate_sqlite()
    
    # Then try PostgreSQL
    print("\n")
    migrate_postgres()
    
    print("\n=== Migration selesai ===")
