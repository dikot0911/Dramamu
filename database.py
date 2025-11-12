import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Text, ForeignKey, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///dramamu.db')

# BASE_URL buat poster musti ngarah ke backend API (kalo produksi pake Render)
_api_base_url = os.getenv('API_BASE_URL', '').strip()
BASE_URL = _api_base_url if _api_base_url else "http://localhost:8000"

if DATABASE_URL.startswith('sqlite'):
    logger.info("✅ Using SQLite database (default)")
elif DATABASE_URL.startswith('postgresql'):
    logger.info("✅ Using PostgreSQL from DATABASE_URL")
else:
    logger.info(f"✅ Using custom DATABASE_URL")

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
    is_vip = Column(Boolean, default=False)
    vip_expires_at = Column(DateTime, nullable=True)
    commission_balance = Column(Integer, default=0)
    total_referrals = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

class Movie(Base):
    __tablename__ = 'movies'
    
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    poster_url = Column(String, nullable=True)
    video_link = Column(String, nullable=False)
    category = Column(String, nullable=True)
    views = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

class Favorite(Base):
    __tablename__ = 'favorites'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('telegram_id', 'movie_id', name='uq_favorite_user_movie'),
    )

class Like(Base):
    __tablename__ = 'likes'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    
    __table_args__ = (
        UniqueConstraint('telegram_id', 'movie_id', name='uq_like_user_movie'),
    )

class WatchHistory(Base):
    __tablename__ = 'watch_history'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    movie_id = Column(String, ForeignKey('movies.id'), nullable=False)
    watched_at = Column(DateTime, default=datetime.now)

class DramaRequest(Base):
    __tablename__ = 'drama_requests'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    judul = Column(String, nullable=False)
    apk_source = Column(String, nullable=True)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=datetime.now)

class Withdrawal(Base):
    __tablename__ = 'withdrawals'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    amount = Column(Integer, nullable=False)
    payment_method = Column(String, nullable=False)
    account_number = Column(String, nullable=False)
    account_name = Column(String, nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=datetime.now)
    processed_at = Column(DateTime, nullable=True)

class Payment(Base):
    __tablename__ = 'payments'
    
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, nullable=False, index=True)
    order_id = Column(String, unique=True, nullable=False)
    package_name = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=datetime.now)
    paid_at = Column(DateTime, nullable=True)

def init_db():
    """Buat/setup tabel database"""
    try:
        logger.info("🗄️ Creating database tables...")
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created successfully!")
        
        db = SessionLocal()
        try:
            movie_count = db.query(Movie).count()
            if movie_count == 0:
                logger.info("📽️ Adding sample movies...")
                sample_movies = [
                    Movie(
                        id="sample-1",
                        title="Cincin Lepas, Bursa Runtuh",
                        description="Drama tentang pernikahan dan bisnis yang penuh intrik",
                        poster_url=f"{BASE_URL}/static/posters/cincin-lepas.jpg",
                        video_link="https://example.com/video1",
                        category="Romance",
                        views=1250
                    ),
                    Movie(
                        id="sample-2",
                        title="Tuan Su, Antri untuk Nikah Lagi",
                        description="Kisah cinta yang rumit dan mengharukan",
                        poster_url=f"{BASE_URL}/static/posters/tuan-su.jpg",
                        video_link="https://example.com/video2",
                        category="Romance",
                        views=980
                    ),
                    Movie(
                        id="sample-3",
                        title="Suami Bisa Dengar Isi Hatiku",
                        description="Drama fantasi romantis yang unik",
                        poster_url=f"{BASE_URL}/static/posters/suami-dengar.jpg",
                        video_link="https://example.com/video3",
                        category="Fantasy",
                        views=2100
                    ),
                    Movie(
                        id="sample-4",
                        title="Jodoh Sempurna dari Salah Langkah",
                        description="Cerita jodoh yang tak terduga",
                        poster_url=f"{BASE_URL}/static/posters/jodoh-sempurna.jpg",
                        video_link="https://example.com/video4",
                        category="Romance",
                        views=1500
                    ),
                ]
                db.add_all(sample_movies)
                db.commit()
                logger.info("✅ Sample movies added!")
            else:
                existing_movies = db.query(Movie).all()
                updated = False
                for movie in existing_movies:
                    if movie.category is None:
                        if 'fantasi' in movie.description.lower():
                            movie.category = 'Fantasy'  # type: ignore
                        else:
                            movie.category = 'Romance'  # type: ignore
                        updated = True
                    if movie.views is None:
                        movie.views = 0  # type: ignore
                        updated = True
                
                if updated:
                    db.commit()
                    logger.info("✅ Updated existing movies with categories and views")
                    
        except Exception as e:
            logger.error(f"Error adding sample data: {e}")
        finally:
            db.close()
            
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}")
        raise

def get_db():
    """Ambil session database"""
    db = SessionLocal()
    try:
        return db
    except:
        db.close()
        raise
