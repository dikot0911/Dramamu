import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import logging
from config import now_utc, DATABASE_URL, BASE_URL

logger = logging.getLogger(__name__)

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
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    poster_url = Column(String, nullable=True)
    video_link = Column(String, nullable=False)
    category = Column(String, nullable=True)
    views = Column(Integer, default=0)
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
    created_at = Column(DateTime, default=now_utc)

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
    package_name = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=now_utc)
    paid_at = Column(DateTime, nullable=True)

class Admin(Base):
    __tablename__ = 'admins'
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    email = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_utc)
    last_login = Column(DateTime, nullable=True)

def init_db():
    """Buat/setup tabel database"""
    try:
        logger.info("🗄️ Lagi bikin tabel database...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Tabel database udah jadi!")
        
        db = SessionLocal()
        try:
            admin_count = db.query(Admin).count()
            if admin_count == 0:
                logger.warning("=" * 80)
                logger.warning("⚠️ Belum ada admin user di database!")
                logger.warning("=" * 80)
                logger.warning("Admin panel ga bisa diakses tanpa admin user.")
                logger.warning("")
                logger.warning("📝 CARA BIKIN ADMIN:")
                logger.warning("1. Set environment variables: ADMIN_USERNAME, ADMIN_PASSWORD, JWT_SECRET_KEY")
                logger.warning("2. Jalankan: python create_admin.py")
                logger.warning("")
                logger.warning("Atau lihat dokumentasi: ADMIN_PANEL_SETUP.md")
                logger.warning("=" * 80)
            
            movie_count = db.query(Movie).count()
            if movie_count == 0:
                logger.info("📽️ Lagi tambahin sample movies...")
                logger.info("ℹ️ Sample movies pake placeholder video links - ganti dengan URL konten asli ya")
                sample_movies = [
                    Movie(
                        id="sample-1",
                        title="Cincin Lepas, Bursa Runtuh",
                        description="Drama tentang pernikahan dan bisnis yang penuh intrik",
                        poster_url=f"{BASE_URL}/media/posters/cincin-lepas.jpg",
                        video_link="https://www.youtube.com/watch?v=placeholder1",
                        category="Romance",
                        views=1250
                    ),
                    Movie(
                        id="sample-2",
                        title="Tuan Su, Antri untuk Nikah Lagi",
                        description="Kisah cinta yang rumit dan mengharukan",
                        poster_url=f"{BASE_URL}/media/posters/tuan-su.jpg",
                        video_link="https://www.youtube.com/watch?v=placeholder2",
                        category="Romance",
                        views=980
                    ),
                    Movie(
                        id="sample-3",
                        title="Suami Bisa Dengar Isi Hatiku",
                        description="Drama fantasi romantis yang unik",
                        poster_url=f"{BASE_URL}/media/posters/suami-dengar.jpg",
                        video_link="https://www.youtube.com/watch?v=placeholder3",
                        category="Fantasy",
                        views=2100
                    ),
                    Movie(
                        id="sample-4",
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
                logger.info("✅ Sample movies udah ditambahkan!")
            else:
                existing_movies = db.query(Movie).all()
                updated = False
                for movie in existing_movies:
                    if movie.category is None:
                        if movie.description and 'fantasi' in movie.description.lower():  # type: ignore
                            movie.category = 'Fantasy'  # type: ignore
                        else:
                            movie.category = 'Romance'  # type: ignore
                        updated = True
                    if movie.views is None:
                        movie.views = 0  # type: ignore
                        updated = True
                
                if updated:
                    db.commit()
                    logger.info("✅ Film yang ada udah diupdate dengan kategori dan views")
                    
        except Exception as e:
            logger.error(f"Error nambahin sample data: {e}")
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"❌ Error waktu inisialisasi database: {e}")
        raise

def get_db():
    """Ambil session database"""
    db = SessionLocal()
    return db

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
