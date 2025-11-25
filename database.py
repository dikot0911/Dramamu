import os
import random
import string
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey, UniqueConstraint, BigInteger, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import logging
from config import now_utc, DATABASE_URL, BASE_URL

logger = logging.getLogger(__name__)

# Konfigurasi engine berdasarkan tipe database
if DATABASE_URL.startswith('postgresql'):
    # PostgreSQL - SSL mode WAJIB 'require' untuk Supabase (production-ready)
    # Default ke 'require' untuk security dan kompatibilitas Supabase
    db_sslmode = os.getenv('DB_SSLMODE', 'require').strip()
    
    # Normalize empty/whitespace value ke default
    if not db_sslmode:
        db_sslmode = 'require'
    
    # Parse sslmode dari DATABASE_URL jika ada (override environment variable)
    if '?' in DATABASE_URL and 'sslmode=' in DATABASE_URL:
        import urllib.parse
        parsed = urllib.parse.urlparse(DATABASE_URL)
        query_params = urllib.parse.parse_qs(parsed.query)
        if 'sslmode' in query_params:
            db_sslmode = query_params['sslmode'][0].strip() or 'require'
            logger.info(f"Using sslmode from DATABASE_URL: {db_sslmode}")
    else:
        logger.info(f"Using DB_SSLMODE: {db_sslmode}")
    
    # PRODUCTION OPTIMIZATION: Balanced connection pool untuk Supabase free tier
    # Supabase free tier limit: ~100 connections total
    # Render free tier: Multi-process bisa spawn 2-4 workers
    # Settings: Balance between availability dan connection limit compliance
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,          # Test connection before use (prevent stale connections)
        pool_recycle=300,             # Recycle connections after 5 minutes (prevent stale)
        pool_size=5,                  # Keep 5 connections in pool per worker (balanced)
        max_overflow=10,              # Allow up to 10 additional connections (adequate headroom)
        pool_timeout=30,              # Wait up to 30s for connection from pool
        echo=False,
        connect_args={
            'sslmode': db_sslmode,
            'connect_timeout': 30,   # INCREASED: 30s timeout (was 10s) - prevent Supabase timeout
            'keepalives': 1,         # Enable TCP keepalive
            'keepalives_idle': 30,   # Start keepalive after 30s idle
            'keepalives_interval': 10, # Send keepalive every 10s
            'keepalives_count': 5    # Drop connection after 5 failed keepalives
        }
    )
else:
    # SQLite ga butuh SSL
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, nullable=True)
    ref_code = Column(String, unique=True, nullable=False)
    referred_by_code = Column(String, nullable=True, index=True)
    is_vip = Column(Boolean, default=False)
    vip_expires_at = Column(DateTime, nullable=True)
    commission_balance = Column(Integer, default=0)
    total_referrals = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_utc)

class Movie(Base):
    __tablename__ = 'movies'
    
    id = Column(String, primary_key=True)
    short_id = Column(String, unique=True, nullable=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    poster_url = Column(String, nullable=True)
    poster_file_id = Column(String, nullable=True)
    video_link = Column(String, nullable=True)
    category = Column(String, nullable=True)
    views = Column(Integer, default=0)
    
    telegram_file_id = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
    telegram_message_id = Column(String, nullable=True)
    is_series = Column(Boolean, default=False)
    total_parts = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=now_utc)

class Favorite(Base):
    __tablename__ = 'favorites'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    created_at = Column(DateTime, default=now_utc)
    
    __table_args__ = (
        UniqueConstraint('telegram_id', 'movie_id', name='uq_favorite_user_movie'),
    )

class Like(Base):
    __tablename__ = 'likes'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    created_at = Column(DateTime, default=now_utc)
    
    __table_args__ = (
        UniqueConstraint('telegram_id', 'movie_id', name='uq_like_user_movie'),
    )

class WatchHistory(Base):
    __tablename__ = 'watch_history'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    watched_at = Column(DateTime, default=now_utc)

class DramaRequest(Base):
    __tablename__ = 'drama_requests'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    judul = Column(String, nullable=False)
    apk_source = Column(String, nullable=True)
    status = Column(String, default='pending')
    admin_notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

class Withdrawal(Base):
    __tablename__ = 'withdrawals'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    payment_method = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    account_name = Column(String, nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=now_utc)
    processed_at = Column(DateTime, nullable=True)

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    order_id = Column(String, unique=True, nullable=False)
    transaction_id = Column(String, nullable=True, index=True)
    package_name = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String, default='pending')
    screenshot_url = Column(String, nullable=True)
    qris_url = Column(String, nullable=True)
    qris_string = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    paid_at = Column(DateTime, nullable=True)

class Admin(Base):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    email = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_utc)
    last_login = Column(DateTime, nullable=True)

class AdminSession(Base):
    __tablename__ = 'admin_sessions'
    
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, ForeignKey('admins.id', ondelete='CASCADE'), nullable=False, index=True)
    session_token = Column(String, unique=True, nullable=False, index=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    last_activity = Column(DateTime, default=now_utc)
    expires_at = Column(DateTime, nullable=False)

class Part(Base):
    __tablename__ = 'parts'
    
    id = Column(Integer, primary_key=True, index=True)
    movie_id = Column(String, ForeignKey('movies.id', ondelete='CASCADE'), nullable=False, index=True)
    part_number = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    
    telegram_file_id = Column(String, nullable=True)
    telegram_chat_id = Column(String, nullable=True)
    telegram_message_id = Column(String, nullable=True)
    
    video_link = Column(String, nullable=True)
    
    duration = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    
    views = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)
    
    __table_args__ = (
        UniqueConstraint('movie_id', 'part_number', name='uq_part_movie_number'),
    )

class PendingUpload(Base):
    __tablename__ = 'pending_uploads'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_file_id = Column(String, nullable=False)
    telegram_chat_id = Column(String, nullable=False)
    telegram_message_id = Column(String, nullable=False, unique=True)
    uploader_id = Column(String, nullable=False)
    
    content_type = Column(String, default='video')
    
    duration = Column(Integer, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    thumbnail_url = Column(String, nullable=True)
    
    poster_width = Column(Integer, nullable=True)
    poster_height = Column(Integer, nullable=True)
    
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=now_utc)

class AdminConversation(Base):
    __tablename__ = 'admin_conversations'
    
    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(String, nullable=False, index=True)
    conversation_type = Column(String, nullable=False)
    step = Column(String, nullable=False)
    data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

class Settings(Base):
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)
    updated_by = Column(String, nullable=True)

class Broadcast(Base):
    __tablename__ = 'broadcasts'
    
    id = Column(Integer, primary_key=True, index=True)
    message = Column(Text, nullable=False)
    target = Column(String, default='all')
    is_active = Column(Boolean, default=True)
    broadcast_type = Column(String, default='v1')  # v1=telegram, v2=miniapp
    created_at = Column(DateTime, default=now_utc)
    created_by = Column(String, nullable=True)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)


def seed_sample_movies():
    """
    Tambahin sample movies kalau database masih kosong.
    
    Sample data untuk testing/demo. Ganti dengan konten asli nanti.
    """
    db = SessionLocal()
    try:
        if db.query(Movie).count() > 0:
            return  # Udah ada movies, skip
        
        logger.info("📽️  Adding sample movies...")
        sample_movies = [
            Movie(
                id="sample-1",
                short_id=get_unique_short_id(),
                title="Cincin Lepas, Bursa Runtuh",
                description="Drama tentang pernikahan dan bisnis yang penuh intrik",
                poster_url=f"{BASE_URL}/media/posters/cincin-lepas.jpg",
                video_link="https://www.youtube.com/watch?v=placeholder1",
                category="Romance",
                views=1250
            ),
            Movie(
                id="sample-2",
                short_id=get_unique_short_id(),
                title="Tuan Su, Antri untuk Nikah Lagi",
                description="Kisah cinta yang rumit dan mengharukan",
                poster_url=f"{BASE_URL}/media/posters/tuan-su.jpg",
                video_link="https://www.youtube.com/watch?v=placeholder2",
                category="Romance",
                views=980
            ),
            Movie(
                id="sample-3",
                short_id=get_unique_short_id(),
                title="Suami Bisa Dengar Isi Hatiku",
                description="Drama fantasi romantis yang unik",
                poster_url=f"{BASE_URL}/media/posters/suami-dengar.jpg",
                video_link="https://www.youtube.com/watch?v=placeholder3",
                category="Fantasy",
                views=2100
            ),
            Movie(
                id="sample-4",
                short_id=get_unique_short_id(),
                title="Jodoh Sempurna dari Salah Langkah",
                description="Cerita jodoh yang tak terduga",
                poster_url=f"{BASE_URL}/media/posters/jodoh-sempurna.jpg",
                video_link="https://www.youtube.com/watch?v=placeholder4",
                category="Romance",
                views=1500
            ),
        ]
        db.add_all(sample_movies)
        db.commit()
        logger.info("✅ Sample movies added!")
        
    except Exception as e:
        logger.error(f"❌ Failed to seed movies: {e}")
        db.rollback()
    finally:
        db.close()

def init_db():
    """
    Initialize database: create tables, run migrations, seed data.
    
    Clean dan simple - kompleksitas dipindah ke helper functions.
    """
    try:
        # Step 1: Create tables (kalau belum ada)
        logger.info("🗄️  Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Tables created!")
        
        # Step 2: Run migrations (handle schema changes)
        from schema_migrations import run_migrations, validate_critical_schema
        
        if not run_migrations():
            logger.error("❌ Migrations failed! App cannot start safely.")
            raise RuntimeError("Database migration failed")
        
        # Step 3: Validate schema
        if not validate_critical_schema():
            logger.error("❌ Schema validation failed!")
            raise RuntimeError("Database schema invalid")
        
        # Step 4: Bootstrap admin - ensure admin exists on every init
        from admin_auth import ensure_admin_exists
        result = ensure_admin_exists()
        
        if result['status'] == 'success':
            logger.info(f"✅ Admin bootstrap OK: {result.get('message', 'Ready')}")
        elif result['status'] == 'missing_secrets':
            logger.warning("⚠️  Admin credentials belum di-set - panel unavailable")
        else:
            logger.warning(f"⚠️  Admin setup issue: {result.get('message', 'Error')}")
        
        # Step 5: Seed sample data
        seed_sample_movies()
        
        logger.info("✅ Database initialization complete!")
            
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise


def generate_short_id(length=8):
    """Generate random alphanumeric short ID untuk movie"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))

def get_unique_short_id(max_attempts=10):
    """
    Generate unique short_id yang belum ada di database.
    Cek collision di database untuk memastikan uniqueness.
    
    Returns:
        str: Unique short_id
        
    Raises:
        RuntimeError: Kalau gagal generate unique ID setelah max_attempts
    """
    db = SessionLocal()
    try:
        for attempt in range(max_attempts):
            short_id = generate_short_id(8)
            
            # Check if short_id already exists
            existing = db.query(Movie).filter(Movie.short_id == short_id).first()
            if not existing:
                return short_id
            
            logger.warning(f"Short ID collision: {short_id} (attempt {attempt + 1}/{max_attempts})")
        
        # Kalau sampai sini berarti collision terus
        raise RuntimeError(f"Failed to generate unique short_id after {max_attempts} attempts")
    finally:
        db.close()

def serialize_movie(movie):
    """
    Serialize Movie ORM object to dictionary.
    Berguna untuk konversi movie object yang sudah di-query ke format dict.
    
    Args:
        movie: Movie ORM object
        
    Returns:
        Dictionary dengan semua field movie
    """
    if not movie:
        return None
    return {
        'id': movie.id,
        'short_id': movie.short_id,
        'title': movie.title,
        'description': movie.description,
        'poster_url': movie.poster_url,
        'poster_file_id': movie.poster_file_id,
        'video_link': movie.video_link,
        'category': movie.category,
        'views': movie.views,
        'telegram_file_id': movie.telegram_file_id,
        'telegram_chat_id': movie.telegram_chat_id,
        'telegram_message_id': movie.telegram_message_id,
        'is_series': movie.is_series,
        'total_parts': movie.total_parts,
        'created_at': movie.created_at
    }

def get_movie_by_id(movie_id):
    """Get movie by ID"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        return serialize_movie(movie)
    finally:
        db.close()

def get_movie_by_short_id(short_id):
    """
    Get movie by short_id.
    Support untuk resolving short_id ke movie object.
    
    Args:
        short_id: Short ID dari movie
        
    Returns:
        Movie dictionary atau None kalau ga ketemu
    """
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.short_id == short_id).first()
        return serialize_movie(movie)
    finally:
        db.close()

def get_parts_by_movie_id(movie_id):
    """Get all parts for a movie, sorted by part_number"""
    db = SessionLocal()
    try:
        parts = db.query(Part).filter(Part.movie_id == movie_id).order_by(Part.part_number).all()
        return [{
            'id': ep.id,
            'part_number': ep.part_number,
            'title': ep.title,
            'telegram_file_id': ep.telegram_file_id,
            'telegram_chat_id': ep.telegram_chat_id,
            'telegram_message_id': ep.telegram_message_id,
            'video_link': ep.video_link,
            'duration': ep.duration,
            'file_size': ep.file_size,
            'thumbnail_url': ep.thumbnail_url,
            'views': ep.views,
            'created_at': ep.created_at,
            'updated_at': ep.updated_at
        } for ep in parts]
    finally:
        db.close()

def get_part(movie_id, part_number):
    """Get specific part by movie_id and part_number"""
    db = SessionLocal()
    try:
        ep = db.query(Part).filter(
            Part.movie_id == movie_id,
            Part.part_number == part_number
        ).first()
        if not ep:
            return None
        return {
            'id': ep.id,
            'part_number': ep.part_number,
            'title': ep.title,
            'telegram_file_id': ep.telegram_file_id,
            'telegram_chat_id': ep.telegram_chat_id,
            'telegram_message_id': ep.telegram_message_id,
            'video_link': ep.video_link,
            'duration': ep.duration,
            'file_size': ep.file_size,
            'thumbnail_url': ep.thumbnail_url,
            'views': ep.views,
            'created_at': ep.created_at,
            'updated_at': ep.updated_at
        }
    finally:
        db.close()

def get_part_by_id(part_id):
    """Get part by ID"""
    db = SessionLocal()
    try:
        ep = db.query(Part).filter(Part.id == part_id).first()
        if not ep:
            return None
        return {
            'id': ep.id,
            'movie_id': ep.movie_id,
            'part_number': ep.part_number,
            'title': ep.title,
            'telegram_file_id': ep.telegram_file_id,
            'telegram_chat_id': ep.telegram_chat_id,
            'telegram_message_id': ep.telegram_message_id,
            'video_link': ep.video_link,
            'duration': ep.duration,
            'file_size': ep.file_size,
            'thumbnail_url': ep.thumbnail_url,
            'views': ep.views,
            'created_at': ep.created_at,
            'updated_at': ep.updated_at
        }
    finally:
        db.close()

def create_part(movie_id, part_number, title, telegram_file_id=None, video_link=None, **kwargs):
    """
    Create new part
    
    Args:
        movie_id: Movie ID
        part_number: Part number (1, 2, 3...)
        title: Part title
        telegram_file_id: Telegram file ID (optional)
        video_link: Video link (optional)
        **kwargs: Additional fields (duration, file_size, thumbnail_url, etc)
    """
    db = SessionLocal()
    try:
        part = Part(
            movie_id=movie_id,
            part_number=part_number,
            title=title,
            telegram_file_id=telegram_file_id,
            video_link=video_link,
            **kwargs
        )
        db.add(part)
        db.commit()
        db.refresh(part)
        
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if movie:
            movie.total_parts = db.query(Part).filter(Part.movie_id == movie_id).count()  # type: ignore
            movie.is_series = True  # type: ignore
            db.commit()
        
        logger.info(f"✅ Part {part_number} created for movie {movie_id}")
        return part
    except Exception as e:
        logger.error(f"❌ Error creating part: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def update_part(part_id, **kwargs):
    """Update part fields"""
    db = SessionLocal()
    try:
        part = db.query(Part).filter(Part.id == part_id).first()
        if not part:
            return None
        
        for key, value in kwargs.items():
            if hasattr(part, key):
                setattr(part, key, value)
        
        part.updated_at = now_utc()  # type: ignore
        db.commit()
        db.refresh(part)
        logger.info(f"✅ Part {part_id} updated")
        return part
    except Exception as e:
        logger.error(f"❌ Error updating part: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def delete_part(part_id):
    """Delete part and update movie total_parts"""
    db = SessionLocal()
    try:
        part = db.query(Part).filter(Part.id == part_id).first()
        if not part:
            return False
        
        movie_id = part.movie_id
        db.delete(part)
        db.commit()
        
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if movie:
            movie.total_parts = db.query(Part).filter(Part.movie_id == movie_id).count()  # type: ignore
            if movie.total_parts == 0:
                movie.is_series = False  # type: ignore
            db.commit()
        
        logger.info(f"✅ Part {part_id} deleted")
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting part: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def increment_part_views(part_id):
    """Increment part view count"""
    db = SessionLocal()
    try:
        part = db.query(Part).filter(Part.id == part_id).first()
        if not part:
            logger.warning(f"Cannot increment views: part {part_id} not found")
            return False
        part.views += 1  # type: ignore
        db.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Error incrementing part views: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def create_pending_upload(telegram_file_id, telegram_chat_id, telegram_message_id, uploader_id, **kwargs):
    """Create pending upload record"""
    db = SessionLocal()
    try:
        upload = PendingUpload(
            telegram_file_id=telegram_file_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            uploader_id=uploader_id,
            **kwargs
        )
        db.add(upload)
        db.commit()
        db.refresh(upload)
        return upload
    except Exception as e:
        logger.error(f"❌ Error creating pending upload: {e}")
        db.rollback()
        return None
    finally:
        db.close()

def get_pending_upload(message_id):
    """Get pending upload by message_id"""
    db = SessionLocal()
    try:
        upload = db.query(PendingUpload).filter(PendingUpload.telegram_message_id == str(message_id)).first()
        if not upload:
            return None
        return {
            'id': upload.id,
            'telegram_file_id': upload.telegram_file_id,
            'telegram_chat_id': upload.telegram_chat_id,
            'telegram_message_id': upload.telegram_message_id,
            'uploader_id': upload.uploader_id,
            'content_type': upload.content_type if hasattr(upload, 'content_type') else 'video',
            'duration': upload.duration,
            'file_size': upload.file_size,
            'thumbnail_url': upload.thumbnail_url,
            'poster_width': upload.poster_width if hasattr(upload, 'poster_width') else None,
            'poster_height': upload.poster_height if hasattr(upload, 'poster_height') else None,
            'status': upload.status,
            'created_at': upload.created_at
        }
    finally:
        db.close()

def update_pending_upload_status(message_id, status):
    """Update pending upload status"""
    db = SessionLocal()
    try:
        upload = db.query(PendingUpload).filter(PendingUpload.telegram_message_id == str(message_id)).first()
        if upload:
            upload.status = status
            db.commit()
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error updating pending upload: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def get_pending_uploads(status='pending', page=1, limit=20, content_type=None):
    """
    Get pending uploads from database with pagination
    
    Args:
        status: Upload status filter (default: 'pending')
        page: Page number (default: 1)
        limit: Items per page (default: 20)
        content_type: Filter by content type: 'video', 'poster', or None for all (default: None)
    
    Returns:
        dict with uploads list, total count, and pagination info
    """
    db = SessionLocal()
    try:
        query = db.query(PendingUpload).filter(PendingUpload.status == status)
        
        if content_type:
            query = query.filter(PendingUpload.content_type == content_type)
        
        total = query.count()
        offset = (page - 1) * limit
        uploads = query.order_by(desc(PendingUpload.created_at)).offset(offset).limit(limit).all()
        
        return {
            'uploads': [{
                'id': u.id,
                'telegram_file_id': u.telegram_file_id,
                'telegram_chat_id': u.telegram_chat_id,
                'telegram_message_id': u.telegram_message_id,
                'content_type': u.content_type if hasattr(u, 'content_type') else 'video',
                'duration': u.duration,
                'file_size': u.file_size,
                'thumbnail_url': u.thumbnail_url,
                'poster_width': u.poster_width if hasattr(u, 'poster_width') else None,
                'poster_height': u.poster_height if hasattr(u, 'poster_height') else None,
                'status': u.status,
                'created_at': u.created_at
            } for u in uploads],
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': (total + limit - 1) // limit if total > 0 else 0
        }
    except Exception as e:
        logger.error(f"❌ Error getting pending uploads: {e}")
        return {
            'uploads': [],
            'total': 0,
            'page': page,
            'limit': limit,
            'total_pages': 0
        }
    finally:
        db.close()

def create_conversation(admin_id, conversation_type, step, data=None):
    """Buat conversation baru untuk admin"""
    db = SessionLocal()
    try:
        delete_conversation(admin_id)
        
        import json
        conversation = AdminConversation(
            admin_id=str(admin_id),
            conversation_type=conversation_type,
            step=step,
            data=json.dumps(data) if data else None
        )
        db.add(conversation)
        db.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Error creating conversation: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def get_conversation(admin_id):
    """Ambil conversation yang aktif untuk admin"""
    db = SessionLocal()
    try:
        conv = db.query(AdminConversation).filter(AdminConversation.admin_id == str(admin_id)).first()
        if conv:
            import json
            return {
                'id': conv.id,
                'admin_id': conv.admin_id,
                'conversation_type': conv.conversation_type,
                'step': conv.step,
                'data': json.loads(conv.data) if conv.data else {},  # type: ignore
                'created_at': conv.created_at
            }
        return None
    except Exception as e:
        logger.error(f"❌ Error getting conversation: {e}")
        return None
    finally:
        db.close()

def update_conversation(admin_id, step, data=None):
    """Update conversation step dan data"""
    db = SessionLocal()
    try:
        conv = db.query(AdminConversation).filter(AdminConversation.admin_id == str(admin_id)).first()
        if conv:
            import json
            conv.step = step  # type: ignore
            if data is not None:
                existing_data = json.loads(conv.data) if conv.data else {}  # type: ignore
                existing_data.update(data)
                conv.data = json.dumps(existing_data)  # type: ignore
            db.commit()
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Error updating conversation: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def delete_conversation(admin_id):
    """Hapus conversation yang aktif untuk admin"""
    db = SessionLocal()
    try:
        db.query(AdminConversation).filter(AdminConversation.admin_id == str(admin_id)).delete()
        db.commit()
        return True
    except Exception as e:
        logger.error(f"❌ Error deleting conversation: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def check_and_update_vip_expiry(user: User, db_session) -> bool:
    """
    Helper buat ngecek VIP expiration dan update database secara atomic.
    
    Args:
        user: User object yang udah di-fetch dari database
        db_session: SQLAlchemy session yang aktif
        
    Returns:
        bool: True kalau user masih VIP (atau ga expired), False kalau bukan VIP atau udah expired
        
    Thread-safe: Function ini ngelakuin check dan update dalam satu transaction,
    jadi ga ada race condition.
    """
    try:
        if user is None:
            logger.warning("check_and_update_vip_expiry dipanggil dengan user None")
            return False
        
        if not user.is_vip:  # type: ignore
            return False
        
        if user.vip_expires_at is None:
            return True
        
        current_time = now_utc()
        if user.vip_expires_at <= current_time:  # type: ignore
            logger.info(f"VIP {user.telegram_id} udah expired, update jadi False")
            user.is_vip = False  # type: ignore
            user.vip_expires_at = None  # type: ignore
            db_session.commit()
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error waktu check_and_update_vip_expiry: {e}")
        db_session.rollback()
        return False
