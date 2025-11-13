from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import timedelta, datetime
from admin_auth import (
    authenticate_admin, 
    create_access_token, 
    verify_token,
    get_admin_by_id,
    ACCESS_TOKEN_EXPIRE_HOURS
)
from database import SessionLocal, User, Movie, DramaRequest, Withdrawal, Payment
from config import now_utc
from sqlalchemy import func, desc
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int

class AdminInfo(BaseModel):
    id: int
    username: str
    email: Optional[str]
    is_active: bool
    created_at: str
    last_login: Optional[str]

def get_current_admin(authorization: Optional[str] = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Header authorization ga ada")
    
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Skema autentikasi ga valid")
    except ValueError:
        raise HTTPException(status_code=401, detail="Format header authorization ga valid")
    
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token ga valid atau udah kadaluarsa")
    
    admin_id = payload.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=401, detail="Payload token ga valid")
    
    admin = get_admin_by_id(admin_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Admin ga ketemu")
    
    if not admin.is_active:  # type: ignore
        raise HTTPException(status_code=403, detail="Akun admin lagi ga aktif")
    
    return admin

@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    admin = authenticate_admin(request.username, request.password)
    
    if not admin:
        logger.warning(f"Percobaan login gagal buat username: {request.username}")
        raise HTTPException(
            status_code=401,
            detail="Username atau password salah"
        )
    
    try:
        access_token = create_access_token(
            data={"admin_id": admin.id, "username": admin.username},
            expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        )
    except ValueError as e:
        logger.error(f"Gagal bikin token JWT: {e}")
        raise HTTPException(
            status_code=503,
            detail="Admin panel belum dikonfigurasi dengan benar. Set JWT_SECRET_KEY di Secrets."
        )
    
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRE_HOURS * 3600
    )

@router.get("/me", response_model=AdminInfo)
async def get_current_admin_info(admin = Depends(get_current_admin)):
    return AdminInfo(
        id=admin.id,
        username=admin.username,
        email=admin.email,
        is_active=admin.is_active,
        created_at=admin.created_at.isoformat(),
        last_login=admin.last_login.isoformat() if admin.last_login else None
    )

@router.get("/protected-test")
async def protected_route_test(admin = Depends(get_current_admin)):
    return {
        "message": "Endpoint ini hanya bisa diakses oleh admin yang sudah login",
        "admin_username": admin.username,
        "admin_id": admin.id
    }

@router.get("/dashboard/stats")
async def get_dashboard_stats(admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        total_users = db.query(func.count(User.id)).scalar()
        vip_users = db.query(func.count(User.id)).filter(User.is_vip == True).scalar()
        total_movies = db.query(func.count(Movie.id)).scalar()
        pending_requests = db.query(func.count(DramaRequest.id)).filter(DramaRequest.status == 'pending').scalar()
        pending_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == 'pending').scalar()
        total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == 'success').scalar() or 0
        
        recent_users = db.query(User).order_by(desc(User.created_at)).limit(5).all()
        recent_payments = db.query(Payment).order_by(desc(Payment.created_at)).limit(5).all()
        
        return {
            "stats": {
                "total_users": total_users,
                "vip_users": vip_users,
                "total_movies": total_movies,
                "pending_requests": pending_requests,
                "pending_withdrawals": pending_withdrawals,
                "total_revenue": total_revenue
            },
            "recent_users": [
                {
                    "id": u.id,
                    "telegram_id": u.telegram_id,
                    "username": u.username,
                    "is_vip": u.is_vip,
                    "created_at": u.created_at.isoformat() if u.created_at else None  # type: ignore
                } for u in recent_users
            ],
            "recent_payments": [
                {
                    "id": p.id,
                    "order_id": p.order_id,
                    "amount": p.amount,
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None  # type: ignore
                } for p in recent_payments
            ]
        }
    finally:
        db.close()

class UserUpdateVIP(BaseModel):
    is_vip: bool
    vip_days: Optional[int] = None

@router.get("/users")
async def get_all_users(page: int = 1, limit: int = 20, search: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(User)
        
        if search:
            search_clean = search.strip().replace('%', '').replace('_', '')
            if search_clean:
                query = query.filter(
                    (User.username.contains(search_clean)) | (User.telegram_id.contains(search_clean))
                )
        
        total = query.count()
        offset = (page - 1) * limit
        users = query.order_by(desc(User.created_at)).offset(offset).limit(limit).all()
        
        return {
            "users": [
                {
                    "id": u.id,
                    "telegram_id": u.telegram_id,
                    "username": u.username,
                    "ref_code": u.ref_code,
                    "is_vip": u.is_vip,
                    "vip_expires_at": u.vip_expires_at.isoformat() if u.vip_expires_at else None,  # type: ignore
                    "commission_balance": u.commission_balance,
                    "total_referrals": u.total_referrals,
                    "created_at": u.created_at.isoformat() if u.created_at else None  # type: ignore
                } for u in users
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

@router.get("/users/{user_id}")
async def get_user_detail(user_id: int, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        return {
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "ref_code": user.ref_code,
            "is_vip": user.is_vip,
            "vip_expires_at": user.vip_expires_at.isoformat() if user.vip_expires_at else None,  # type: ignore
            "commission_balance": user.commission_balance,
            "total_referrals": user.total_referrals,
            "created_at": user.created_at.isoformat() if user.created_at else None  # type: ignore
        }
    finally:
        db.close()

@router.put("/users/{user_id}/vip")
async def update_user_vip(user_id: int, data: UserUpdateVIP, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        user.is_vip = data.is_vip  # type: ignore
        
        if data.is_vip and data.vip_days:
            from datetime import timedelta
            user.vip_expires_at = now_utc() + timedelta(days=data.vip_days)  # type: ignore
        elif not data.is_vip:
            user.vip_expires_at = None  # type: ignore
        
        db.commit()
        
        return {"message": "VIP status berhasil diupdate", "is_vip": user.is_vip}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu update status VIP user: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        db.delete(user)
        db.commit()
        
        return {"message": "User berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu hapus user: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

class MovieCreate(BaseModel):
    id: str
    title: str
    description: Optional[str] = None
    poster_url: Optional[str] = None
    video_link: str
    category: Optional[str] = None

class MovieUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    video_link: Optional[str] = None
    category: Optional[str] = None

@router.get("/movies")
async def get_all_movies_admin(page: int = 1, limit: int = 20, search: Optional[str] = None, category: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(Movie)
        
        if search:
            query = query.filter(
                (Movie.title.contains(search)) | (Movie.description.contains(search))
            )
        
        if category:
            query = query.filter(Movie.category == category)
        
        total = query.count()
        offset = (page - 1) * limit
        movies = query.order_by(desc(Movie.created_at)).offset(offset).limit(limit).all()
        
        return {
            "movies": [
                {
                    "id": m.id,
                    "title": m.title,
                    "description": m.description,
                    "poster_url": m.poster_url,
                    "video_link": m.video_link,
                    "category": m.category,
                    "views": m.views,
                    "created_at": m.created_at.isoformat() if m.created_at else None  # type: ignore
                } for m in movies
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

@router.post("/movies")
async def create_movie(data: MovieCreate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        existing = db.query(Movie).filter(Movie.id == data.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Movie ID sudah ada")
        
        movie = Movie(
            id=data.id,
            title=data.title,
            description=data.description,
            poster_url=data.poster_url,
            video_link=data.video_link,
            category=data.category,
            views=0
        )
        
        db.add(movie)
        db.commit()
        db.refresh(movie)
        
        return {"message": "Movie berhasil ditambahkan", "movie_id": movie.id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu bikin movie: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/movies/{movie_id}")
async def get_movie_detail(movie_id: str, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie tidak ditemukan")
        
        return {
            "id": movie.id,
            "title": movie.title,
            "description": movie.description,
            "poster_url": movie.poster_url,
            "video_link": movie.video_link,
            "category": movie.category,
            "views": movie.views,
            "created_at": movie.created_at.isoformat() if movie.created_at else None  # type: ignore
        }
    finally:
        db.close()

@router.put("/movies/{movie_id}")
async def update_movie(movie_id: str, data: MovieUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie tidak ditemukan")
        
        if data.title is not None:
            movie.title = data.title  # type: ignore
        if data.description is not None:
            movie.description = data.description  # type: ignore
        if data.poster_url is not None:
            movie.poster_url = data.poster_url  # type: ignore
        if data.video_link is not None:
            movie.video_link = data.video_link  # type: ignore
        if data.category is not None:
            movie.category = data.category  # type: ignore
        
        db.commit()
        
        return {"message": "Movie berhasil diupdate"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu update movie: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/movies/{movie_id}")
async def delete_movie(movie_id: str, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie tidak ditemukan")
        
        db.delete(movie)
        db.commit()
        
        return {"message": "Movie berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu hapus movie: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/drama-requests")
async def get_drama_requests(page: int = 1, limit: int = 20, status: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(DramaRequest)
        
        if status:
            query = query.filter(DramaRequest.status == status)
        
        total = query.count()
        offset = (page - 1) * limit
        requests = query.order_by(desc(DramaRequest.created_at)).offset(offset).limit(limit).all()
        
        return {
            "requests": [
                {
                    "id": r.id,
                    "telegram_id": r.telegram_id,
                    "judul": r.judul,
                    "apk_source": r.apk_source,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None  # type: ignore
                } for r in requests
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

class RequestStatusUpdate(BaseModel):
    status: str

@router.put("/drama-requests/{request_id}/status")
async def update_request_status(request_id: int, data: RequestStatusUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        drama_request = db.query(DramaRequest).filter(DramaRequest.id == request_id).first()
        if not drama_request:
            raise HTTPException(status_code=404, detail="Request tidak ditemukan")
        
        drama_request.status = data.status  # type: ignore
        db.commit()
        
        return {"message": "Status request berhasil diupdate", "status": drama_request.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu update status request: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/drama-requests/{request_id}")
async def delete_drama_request(request_id: int, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        drama_request = db.query(DramaRequest).filter(DramaRequest.id == request_id).first()
        if not drama_request:
            raise HTTPException(status_code=404, detail="Request tidak ditemukan")
        
        db.delete(drama_request)
        db.commit()
        
        return {"message": "Request berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu hapus request drama: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/withdrawals")
async def get_withdrawals(page: int = 1, limit: int = 20, status: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(Withdrawal)
        
        if status:
            query = query.filter(Withdrawal.status == status)
        
        total = query.count()
        offset = (page - 1) * limit
        withdrawals = query.order_by(desc(Withdrawal.created_at)).offset(offset).limit(limit).all()
        
        return {
            "withdrawals": [
                {
                    "id": w.id,
                    "telegram_id": w.telegram_id,
                    "amount": w.amount,
                    "payment_method": w.payment_method,
                    "account_number": w.account_number,
                    "account_name": w.account_name,
                    "status": w.status,
                    "created_at": w.created_at.isoformat() if w.created_at else None,  # type: ignore
                    "processed_at": w.processed_at.isoformat() if w.processed_at else None  # type: ignore
                } for w in withdrawals
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

class WithdrawalStatusUpdate(BaseModel):
    status: str

@router.put("/withdrawals/{withdrawal_id}/status")
async def update_withdrawal_status(withdrawal_id: int, data: WithdrawalStatusUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        withdrawal = db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id).first()
        if not withdrawal:
            raise HTTPException(status_code=404, detail="Withdrawal tidak ditemukan")
        
        withdrawal.status = data.status  # type: ignore
        
        if data.status in ['approved', 'rejected']:
            withdrawal.processed_at = now_utc()  # type: ignore
        
        if data.status == 'approved':
            user = db.query(User).filter(User.telegram_id == withdrawal.telegram_id).first()
            if user:
                user.commission_balance = max(0, user.commission_balance - withdrawal.amount)  # type: ignore
        
        db.commit()
        
        return {"message": "Status withdrawal berhasil diupdate", "status": withdrawal.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu update status withdrawal: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/payments")
async def get_payments(page: int = 1, limit: int = 20, status: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(Payment)
        
        if status:
            query = query.filter(Payment.status == status)
        
        total = query.count()
        offset = (page - 1) * limit
        payments = query.order_by(desc(Payment.created_at)).offset(offset).limit(limit).all()
        
        return {
            "payments": [
                {
                    "id": p.id,
                    "telegram_id": p.telegram_id,
                    "order_id": p.order_id,
                    "package_name": p.package_name,
                    "amount": p.amount,
                    "status": p.status,
                    "created_at": p.created_at.isoformat() if p.created_at else None,  # type: ignore
                    "paid_at": p.paid_at.isoformat() if p.paid_at else None  # type: ignore
                } for p in payments
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()
