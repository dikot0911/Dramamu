from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response, Cookie
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import timedelta, datetime
from admin_auth import (
    authenticate_admin, 
    create_access_token, 
    verify_token,
    get_admin_by_id,
    get_admin_credentials,
    get_jwt_secret,
    ensure_admin_exists,
    is_super_admin,
    create_admin_session,
    get_admin_session,
    delete_admin_session,
    delete_all_admin_sessions,
    get_active_sessions_for_admin,
    cleanup_expired_sessions,
    touch_admin_session,
    ACCESS_TOKEN_EXPIRE_HOURS
)
from database import (
    SessionLocal, User, Movie, Part, DramaRequest, Withdrawal, Payment, Admin,
    PendingUpload, Settings,
    get_parts_by_movie_id, create_part, update_part, delete_part,
    get_part_by_id, get_pending_uploads, get_unique_short_id
)
from config import now_utc, is_production, TELEGRAM_BOT_TOKEN
from sqlalchemy import func, desc, Integer, case
from sqlalchemy.exc import IntegrityError
import logging
import httpx
import io

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])

def to_iso_utc(dt):
    """
    Convert datetime to ISO format with UTC timezone indicator.
    
    Database timestamps are stored as naive UTC datetime.
    This function adds 'Z' suffix to indicate UTC timezone,
    which JavaScript properly recognizes and converts to local timezone.
    
    Args:
        dt: datetime object (naive UTC)
    
    Returns:
        ISO string with 'Z' suffix (e.g., "2025-11-19T16:50:19Z")
        or None if input is None
    """
    if dt is None:
        return None
    return dt.isoformat() + 'Z'

def query_for_update(query, use_lock=True):
    """
    Apply with_for_update() only for PostgreSQL.
    
    Inspects the actual database dialect of the session/engine
    instead of relying on environment variables.
    
    Args:
        query: SQLAlchemy query object
        use_lock: Whether to attempt row-level locking (default: True)
    
    Returns:
        Query with or without with_for_update() based on dialect
    """
    if not use_lock:
        return query
    
    try:
        # Get the session from the query
        session = query.session
        
        # Inspect actual database dialect
        dialect_name = session.bind.dialect.name
        
        # Only apply row-level locking for PostgreSQL
        if dialect_name == 'postgresql':
            return query.with_for_update()
        
        # Skip locking for SQLite and other dialects
        return query
    
    except (AttributeError, Exception):
        # Fallback: if we can't detect dialect, skip locking (safe default)
        return query

class LoginRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int
    admin: Dict[str, Any]

class AdminInfo(BaseModel):
    id: int
    username: str
    email: Optional[str]
    display_name: Optional[str]
    is_active: bool
    is_super_admin: bool
    created_at: str
    last_login: Optional[str]
    active_sessions: int = 0

class CreateAdminRequest(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_active: bool = True

class UpdateAdminRequest(BaseModel):
    password: Optional[str] = None
    email: Optional[str] = None
    display_name: Optional[str] = None
    is_active: Optional[bool] = None

class AdminSessionInfo(BaseModel):
    id: int
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: str
    last_activity: str

class UserInfo(BaseModel):
    id: int
    telegram_id: str
    username: Optional[str]
    ref_code: str
    referred_by_code: Optional[str]
    is_vip: bool
    vip_expires_at: Optional[str]
    commission_balance: int
    total_referrals: int
    created_at: str

class UsersListResponse(BaseModel):
    users: List[UserInfo]
    total: int
    page: int
    pages: int

def get_current_admin(
    authorization: Optional[str] = Header(None),
    admin_token: Optional[str] = Cookie(None),
    admin_session: Optional[str] = Cookie(None)
):
    """
    Get current admin dari JWT token (support header atau cookie).
    
    Enforces session validation - kicked/expired sessions are rejected.
    
    Priority:
    1. Authorization header (Bearer token)
    2. admin_token cookie
    """
    token = None
    
    # Try get from Authorization header first
    if authorization:
        try:
            scheme, token = authorization.split()
            if scheme.lower() != "bearer":
                raise HTTPException(status_code=401, detail="Invalid auth scheme")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid authorization header")
    
    # Fallback to cookie if no header
    if not token and admin_token:
        token = admin_token
    
    # No token found
    if not token:
        raise HTTPException(status_code=401, detail="Tidak ada token autentikasi")
    
    # Verify JWT token
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token tidak valid atau expired")
    
    admin_id = payload.get("admin_id")
    session_token_in_jwt = payload.get("session_token")
    
    if not admin_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    # Enforce session validation biar ga bisa bypass revocation
    # JWT harus tied ke session yang valid - no session = no access
    if not session_token_in_jwt:
        raise HTTPException(status_code=401, detail="Token tidak memiliki session binding")
    
    # Verify session exists and is valid
    session = get_admin_session(session_token_in_jwt)
    if not session:
        raise HTTPException(status_code=401, detail="Session tidak valid atau sudah di-kick")
    
    if session.admin_id != admin_id:
        raise HTTPException(status_code=401, detail="Session mismatch dengan token")
    
    # Optional: Verify cookie matches JWT for defense in depth
    if admin_session and admin_session != session_token_in_jwt:
        logger.warning(f"Session token mismatch: cookie={admin_session[:8]}... jwt={session_token_in_jwt[:8]}...")
        raise HTTPException(status_code=401, detail="Session cookie tidak match")
    
    # Touch session buat update last_activity
    # Ini biar indicator online status jalan dengan benar
    # Kalau touch gagal, session expired atau dihapus - force re-auth
    if not touch_admin_session(session_token_in_jwt):
        raise HTTPException(status_code=401, detail="Session tidak valid atau sudah expired")
    
    admin = get_admin_by_id(admin_id)
    if not admin:
        raise HTTPException(status_code=401, detail="Admin tidak ditemukan")
    
    if not admin.is_active:
        raise HTTPException(status_code=403, detail="Admin account tidak aktif")
    
    return admin

@router.get("/health")
async def admin_health_check():
    """
    Health check endpoint untuk admin panel.
    
    Cek readiness admin panel: secrets ada, admin exists, JWT usable.
    Public endpoint (no auth) - buat monitoring dan troubleshooting.
    
    Returns:
        JSON dengan status lengkap dan actionable instructions
    """
    creds = get_admin_credentials()
    jwt_secret = get_jwt_secret()
    
    secrets_present = {
        'admin_username': bool(creds['admin_username']),
        'admin_password': bool(creds['admin_password']),
        'jwt_secret': bool(jwt_secret)
    }
    
    all_secrets_present = all(secrets_present.values())
    missing_secrets = [k for k, v in secrets_present.items() if not v]
    
    admin_exists = False
    admin_count = 0
    if all_secrets_present:
        db = SessionLocal()
        try:
            admin_count = db.query(func.count(Admin.id)).scalar()
            admin_exists = admin_count > 0
        except Exception as e:
            logger.error(f"Error checking admin existence: {e}")
        finally:
            db.close()
    
    if all_secrets_present and admin_exists:
        status = "healthy"
        message = "Admin panel siap digunakan"
        ready = True
    elif all_secrets_present and not admin_exists:
        status = "degraded"
        message = "Secrets ada tapi admin belum dibuat. Call POST /admin/initialize untuk auto-create."
        ready = False
    else:
        status = "unavailable"
        message = f"Secrets belum lengkap. Kurang: {', '.join(missing_secrets)}"
        ready = False
    
    response = {
        "status": status,
        "ready": ready,
        "message": message,
        "checks": {
            "secrets_present": all_secrets_present,
            "admin_exists": admin_exists,
            "jwt_usable": bool(jwt_secret)
        },
        "details": {
            "admin_count": admin_count,
            "secrets_status": secrets_present
        }
    }
    
    if not ready:
        response["remediation"] = {
            "missing_secrets": missing_secrets if missing_secrets else None,
            "next_steps": (
                "Set environment variables: ADMIN_USERNAME, ADMIN_PASSWORD, JWT_SECRET_KEY" 
                if missing_secrets 
                else "Call POST /admin/initialize untuk create admin"
            )
        }
    
    return response

@router.post("/initialize")
async def initialize_admin():
    """
    Initialize admin user dengan current environment variables.
    
    Public endpoint (no auth) untuk first-time setup dan auto-recovery.
    Idempotent - aman dipanggil berkali-kali.
    
    Use case:
    - First-time setup setelah deploy
    - Recovery setelah secrets di-update
    - Manual troubleshooting
    
    Returns:
        Status lengkap dengan instructions
    """
    logger.info("Admin initialization dipanggil - checking current environment...")
    
    result = ensure_admin_exists()
    
    http_status = 200
    if result['status'] == 'success':
        http_status = 200
        if result.get('created'):
            logger.info(f"Admin baru berhasil dibuat: {result.get('admin_username')}")
        else:
            logger.info(f"Admin sudah ada: {result.get('admin_username')} (idempotent)")
    elif result['status'] == 'missing_secrets':
        http_status = 503
    elif result['status'] == 'error':
        http_status = 500
    
    response = {
        "status": result['status'],
        "message": result['message'],
        "timestamp": now_utc().isoformat()
    }
    
    if result['status'] == 'success':
        response["admin"] = {
            "username": result.get('admin_username'),
            "admin_id": result.get('admin_id'),
            "created": result.get('created', False),
            "already_exists": result.get('already_exists', False)
        }
        response["next_steps"] = "Admin ready! You can now login at /admin/login"
    elif result['status'] == 'missing_secrets':
        response["missing_secrets"] = result.get('missing_secrets', [])
        response["remediation"] = result.get('remediation', '')
    elif result['status'] == 'error':
        response["error_details"] = result.get('error_details', '')
        response["remediation"] = result.get('remediation', '')
    
    return JSONResponse(content=response, status_code=http_status)

@router.post("/login")
async def login(request: LoginRequest, response: Response, http_request: Request):
    """
    Multi-admin login endpoint dengan cookie-based authentication.
    
    Features:
    - Support login untuk semua admin di database
    - HttpOnly cookies untuk security
    - Session tracking untuk kick functionality
    - Display name support
    - Auto-recovery untuk ENV admin
    """
    jwt_secret = get_jwt_secret()
    if not jwt_secret:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Admin panel belum dikonfigurasi",
                "remediation": "Set JWT_SECRET_KEY di environment variables"
            }
        )
    
    # Auto-recovery untuk ENV admin jika belum exists
    creds = get_admin_credentials()
    if creds['admin_username'] and creds['admin_password']:
        if request.username == creds['admin_username']:
            db = SessionLocal()
            try:
                admin_exists = db.query(Admin).filter(Admin.username == creds['admin_username']).first()
                if not admin_exists:
                    logger.info(f"ENV admin '{request.username}' belum exists - auto-recovery...")
                    result = ensure_admin_exists()
                    if result['status'] != 'success':
                        raise HTTPException(status_code=503, detail="Auto-recovery gagal")
            finally:
                db.close()
    
    # Authenticate admin (support semua admin di database)
    admin = authenticate_admin(request.username, request.password)
    
    if not admin:
        logger.warning(f"Login gagal untuk username: {request.username}")
        raise HTTPException(
            status_code=401,
            detail={"error": "Username atau password salah"}
        )
    
    # Update display_name jika provided
    if request.display_name:
        db = SessionLocal()
        try:
            db_admin = db.query(Admin).filter(Admin.id == admin.id).first()
            if db_admin:
                db_admin.display_name = request.display_name
                db.commit()
                admin.display_name = request.display_name
        except Exception as e:
            logger.error(f"Error updating display_name: {e}")
            db.rollback()
        finally:
            db.close()
    
    # SINGLE DEVICE LOGIN: Kick all existing sessions for regular admins
    # Super admin can have multiple sessions (multi-device support)
    if not is_super_admin(admin):
        existing_sessions = get_active_sessions_for_admin(admin.id)
        if existing_sessions:
            count = delete_all_admin_sessions(admin.id)
            logger.info(f"Regular admin {admin.username} login - kicked {count} existing session(s)")
    else:
        logger.info(f"Super admin {admin.username} login - allowing multiple sessions")
    
    # Create session untuk tracking FIRST (needed for JWT)
    client_ip = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")
    
    session = create_admin_session(
        admin_id=admin.id,
        ip_address=client_ip,
        user_agent=user_agent
    )
    
    if not session:
        logger.error("Failed to create session - aborting login")
        raise HTTPException(status_code=500, detail="Session creation failed")
    
    # Create JWT token with session_token embedded (ties JWT to session)
    try:
        access_token = create_access_token(
            data={
                "admin_id": admin.id,
                "username": admin.username,
                "session_token": session.session_token
            },
            expires_delta=timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
        )
    except ValueError as e:
        logger.error(f"Error creating JWT: {e}")
        delete_admin_session(session.session_token)
        raise HTTPException(status_code=503, detail="JWT error")
    
    # Set HttpOnly cookie
    response.set_cookie(
        key="admin_token",
        value=access_token,
        httponly=True,
        max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        samesite="lax",
        secure=is_production()
    )
    
    # Set session token cookie untuk logout tracking
    if session:
        response.set_cookie(
            key="admin_session",
            value=session.session_token,
            httponly=True,
            max_age=ACCESS_TOKEN_EXPIRE_HOURS * 3600,
            samesite="lax",
            secure=is_production()
        )
    
    logger.info(f"Login berhasil: {admin.username} (display_name: {admin.display_name})")
    
    # Return admin info
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_HOURS * 3600,
        "admin": {
            "id": admin.id,
            "username": admin.username,
            "display_name": admin.display_name,
            "email": admin.email,
            "is_super_admin": is_super_admin(admin),
            "is_active": admin.is_active
        }
    }

@router.get("/me", response_model=AdminInfo)
async def get_current_admin_info(admin = Depends(get_current_admin)):
    active_sessions_count = len(get_active_sessions_for_admin(admin.id))
    return AdminInfo(
        id=admin.id,
        username=admin.username,
        email=admin.email,
        display_name=admin.display_name,
        is_active=admin.is_active,
        is_super_admin=is_super_admin(admin),
        created_at=to_iso_utc(admin.created_at),
        last_login=to_iso_utc(admin.last_login),
        active_sessions=active_sessions_count
    )

@router.post("/logout")
async def logout(response: Response, admin_session: Optional[str] = None):
    """
    Logout endpoint - clear cookies dan delete session.
    """
    # Delete session from database
    if admin_session:
        delete_admin_session(admin_session)
    
    # Clear cookies
    response.delete_cookie("admin_token")
    response.delete_cookie("admin_session")
    
    return {"message": "Logout berhasil"}

@router.get("/admin-users", response_model=List[AdminInfo])
async def list_admins(current_admin = Depends(get_current_admin)):
    """
    List semua admin users (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can access this endpoint")
    
    db = SessionLocal()
    try:
        admins = db.query(Admin).all()
        return [
            AdminInfo(
                id=admin.id,
                username=admin.username,
                email=admin.email,
                display_name=admin.display_name,
                is_active=admin.is_active,
                is_super_admin=is_super_admin(admin),
                created_at=to_iso_utc(admin.created_at),
                last_login=to_iso_utc(admin.last_login),
                active_sessions=len(get_active_sessions_for_admin(admin.id))
            )
            for admin in admins
        ]
    finally:
        db.close()

@router.post("/admin-users", response_model=AdminInfo)
async def create_admin(request: CreateAdminRequest, current_admin = Depends(get_current_admin)):
    """
    Create admin user baru (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can create admin")
    
    from admin_auth import hash_password
    
    db = SessionLocal()
    try:
        existing = db.query(Admin).filter(Admin.username == request.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        
        new_admin = Admin(
            username=request.username,
            password_hash=hash_password(request.password),
            email=request.email,
            display_name=request.display_name,
            is_active=request.is_active,
            created_at=now_utc()
        )
        
        db.add(new_admin)
        db.commit()
        db.refresh(new_admin)
        
        logger.info(f"Super admin {current_admin.username} created new admin: {new_admin.username}")
        
        return AdminInfo(
            id=new_admin.id,
            username=new_admin.username,
            email=new_admin.email,
            display_name=new_admin.display_name,
            is_active=new_admin.is_active,
            is_super_admin=False,
            created_at=to_iso_utc(new_admin.created_at),
            last_login=None
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to create admin")
    finally:
        db.close()

@router.put("/admin-users/{admin_id}", response_model=AdminInfo)
async def update_admin(admin_id: int, request: UpdateAdminRequest, current_admin = Depends(get_current_admin)):
    """
    Update admin user (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can update admin")
    
    from admin_auth import hash_password
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        if request.password:
            admin.password_hash = hash_password(request.password)
        if request.email is not None:
            admin.email = request.email
        if request.display_name is not None:
            admin.display_name = request.display_name
        if request.is_active is not None:
            admin.is_active = request.is_active
        
        db.commit()
        db.refresh(admin)
        
        logger.info(f"Super admin {current_admin.username} updated admin: {admin.username}")
        
        return AdminInfo(
            id=admin.id,
            username=admin.username,
            email=admin.email,
            display_name=admin.display_name,
            is_active=admin.is_active,
            is_super_admin=is_super_admin(admin),
            created_at=to_iso_utc(admin.created_at),
            last_login=to_iso_utc(admin.last_login)
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to update admin")
    finally:
        db.close()

@router.delete("/admin-users/{admin_id}")
async def delete_admin(admin_id: int, current_admin = Depends(get_current_admin)):
    """
    Delete admin user (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can delete admin")
    
    if admin_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        if is_super_admin(admin):
            raise HTTPException(status_code=400, detail="Cannot delete super admin")
        
        username = admin.username
        db.delete(admin)
        db.commit()
        
        logger.info(f"Super admin {current_admin.username} deleted admin: {username}")
        
        return {"message": f"Admin {username} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete admin")
    finally:
        db.close()

@router.post("/admin-users/{admin_id}/kick")
async def kick_admin_sessions(admin_id: int, current_admin = Depends(get_current_admin)):
    """
    Logout paksa admin tertentu dengan menghapus semua sessions (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can kick sessions")
    
    if admin_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot kick yourself")
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(Admin.id == admin_id).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        count = delete_all_admin_sessions(admin_id)
        
        logger.info(f"Super admin {current_admin.username} kicked {count} session(s) for admin: {admin.username}")
        
        return {
            "message": f"Successfully kicked {count} session(s) for {admin.username}",
            "sessions_deleted": count
        }
    finally:
        db.close()

@router.get("/admin-users/{admin_id}/sessions", response_model=List[AdminSessionInfo])
async def get_admin_sessions(admin_id: int, current_admin = Depends(get_current_admin)):
    """
    Get active sessions untuk admin tertentu (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can view sessions")
    
    sessions = get_active_sessions_for_admin(admin_id)
    
    return [
        AdminSessionInfo(
            id=session.id,
            ip_address=session.ip_address,
            user_agent=session.user_agent,
            created_at=to_iso_utc(session.created_at),
            last_activity=to_iso_utc(session.last_activity)
        )
        for session in sessions
    ]

@router.get("/active-admins")
async def get_active_admins(current_admin = Depends(get_current_admin)):
    """
    Get list semua admin dengan status online/offline.
    Online = punya session dengan activity dalam 5 menit terakhir.
    Lightweight endpoint untuk sidebar display.
    """
    from database import AdminSession
    
    db = SessionLocal()
    try:
        # Get time threshold (5 minutes ago)
        five_minutes_ago = now_utc() - timedelta(minutes=5)
        
        # Get unique admin IDs with recent activity
        active_session_query = db.query(AdminSession.admin_id.distinct()).filter(
            AdminSession.expires_at > now_utc(),
            AdminSession.last_activity > five_minutes_ago
        )
        
        active_admin_ids = [row[0] for row in active_session_query.all()]
        
        # Get ALL admins (not just active ones)
        admins = db.query(Admin).filter(Admin.is_active == True).all()
        
        # Build result with online/offline status
        result = []
        for admin in admins:
            is_online = admin.id in active_admin_ids
            
            admin_data = {
                "id": admin.id,
                "username": admin.username,
                "display_name": admin.display_name,
                "is_online": is_online
            }
            
            if is_online:
                # Online: get most recent activity from active sessions
                sessions = get_active_sessions_for_admin(admin.id)
                if sessions:
                    most_recent_activity = max(s.last_activity for s in sessions)
                    admin_data["last_activity"] = to_iso_utc(most_recent_activity)
                    admin_data["active_sessions"] = len(sessions)
            else:
                # Offline: try to get last activity from any session (not just recent ones)
                all_sessions = db.query(AdminSession).filter(
                    AdminSession.admin_id == admin.id
                ).order_by(AdminSession.last_activity.desc()).first()
                
                if all_sessions:
                    admin_data["last_activity"] = to_iso_utc(all_sessions.last_activity)
                elif admin.last_login:
                    # Fallback to last_login if no session found
                    admin_data["last_activity"] = to_iso_utc(admin.last_login)
            
            result.append(admin_data)
        
        # Sort: online first (by recent activity), then offline (alphabetically)
        result.sort(key=lambda x: (
            not x["is_online"],  # False (online) comes before True (offline)
            x.get("last_activity", "") if x["is_online"] else x.get("display_name") or x.get("username", "")
        ), reverse=False)
        
        return result
    finally:
        db.close()

@router.get("/users", response_model=UsersListResponse)
async def list_bot_users(
    page: int = 1, 
    limit: int = 20, 
    search: Optional[str] = None,
    current_admin = Depends(get_current_admin)
):
    """
    List semua bot users dengan pagination dan search.
    """
    db = SessionLocal()
    try:
        query = db.query(User)
        
        if search:
            search_filter = f"%{search}%"
            query = query.filter(
                (User.username.ilike(search_filter)) | 
                (User.telegram_id.ilike(search_filter))
            )
        
        total = query.count()
        
        offset = (page - 1) * limit
        users = query.order_by(desc(User.created_at)).offset(offset).limit(limit).all()
        
        pages = (total + limit - 1) // limit
        
        return UsersListResponse(
            users=[
                UserInfo(
                    id=u.id,
                    telegram_id=u.telegram_id,
                    username=u.username,
                    ref_code=u.ref_code,
                    referred_by_code=u.referred_by_code,
                    is_vip=u.is_vip,
                    vip_expires_at=to_iso_utc(u.vip_expires_at),
                    commission_balance=u.commission_balance,
                    total_referrals=u.total_referrals,
                    created_at=to_iso_utc(u.created_at)
                )
                for u in users
            ],
            total=total,
            page=page,
            pages=pages
        )
    finally:
        db.close()

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
                    "created_at": to_iso_utc(u.created_at)  # type: ignore
                } for u in recent_users
            ],
            "recent_payments": [
                {
                    "id": p.id,
                    "order_id": p.order_id,
                    "amount": p.amount,
                    "status": p.status,
                    "created_at": to_iso_utc(p.created_at)  # type: ignore
                } for p in recent_payments
            ]
        }
    finally:
        db.close()

@router.get("/stats/pending-counts")
async def get_pending_counts(admin = Depends(get_current_admin)):
    """
    Lightweight endpoint untuk get real-time pending counts.
    Digunakan untuk notification badges dan auto-refresh.
    """
    db = SessionLocal()
    try:
        pending_requests = db.query(func.count(DramaRequest.id)).filter(DramaRequest.status == 'pending').scalar()
        pending_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == 'pending').scalar()
        total_users = db.query(func.count(User.id)).scalar()
        vip_users = db.query(func.count(User.id)).filter(User.is_vip == True).scalar()
        total_movies = db.query(func.count(Movie.id)).scalar()
        total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == 'success').scalar() or 0
        
        return {
            "pending_requests": pending_requests,
            "pending_withdrawals": pending_withdrawals,
            "total_users": total_users,
            "vip_users": vip_users,
            "total_movies": total_movies,
            "total_revenue": total_revenue,
            "timestamp": now_utc().isoformat()
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
                    "vip_expires_at": to_iso_utc(u.vip_expires_at),  # type: ignore
                    "commission_balance": u.commission_balance,
                    "total_referrals": u.total_referrals,
                    "created_at": to_iso_utc(u.created_at)  # type: ignore
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
            "vip_expires_at": to_iso_utc(user.vip_expires_at),  # type: ignore
            "commission_balance": user.commission_balance,
            "total_referrals": user.total_referrals,
            "created_at": to_iso_utc(user.created_at)  # type: ignore
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
    poster_file_id: Optional[str] = None
    video_link: Optional[str] = None
    category: Optional[str] = None
    is_series: Optional[bool] = False
    force_duplicate: Optional[bool] = False

class MovieUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    poster_file_id: Optional[str] = None
    video_link: Optional[str] = None
    category: Optional[str] = None
    is_series: Optional[bool] = None

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
                    "poster_file_id": m.poster_file_id,
                    "video_link": m.video_link,
                    "category": m.category,
                    "is_series": m.is_series,
                    "total_parts": m.total_parts,
                    "views": m.views,
                    "created_at": to_iso_utc(m.created_at)  # type: ignore
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
    max_retries = 2
    
    try:
        for attempt in range(max_retries):
            try:
                # Cek apakah movie ID sudah ada
                existing = db.query(Movie).filter(Movie.id == data.id).first()
                if existing and not data.force_duplicate:
                    # Tampilkan warning, biarkan user memilih
                    return {
                        "warning": "duplicate",
                        "message": f"⚠️ Film dengan ID '{data.id}' sudah ada. Apakah Anda ingin tetap menambahkan film ini?",
                        "existing_movie": {
                            "id": existing.id,
                            "title": existing.title,
                            "category": existing.category
                        }
                    }
                # Simpan original ID untuk pending upload lookup
                original_id = data.id
                final_id = data.id
                
                # Jika force_duplicate, generate ID baru yang unique
                if existing and data.force_duplicate:
                    import time
                    # Retry sampai dapat ID yang unique
                    for retry_attempt in range(10):
                        timestamp_suffix = str(int(time.time() * 1000000))[-8:]  # 8 digit dari microseconds untuk lebih unique
                        new_id = f"{original_id}_{timestamp_suffix}"
                        # Cek apakah ID baru sudah unique
                        if not db.query(Movie).filter(Movie.id == new_id).first():
                            final_id = new_id
                            logger.info(f"⚠️ Duplicate detected, generated new unique ID: {original_id} → {final_id}")
                            break
                    else:
                        raise HTTPException(status_code=500, detail="Gagal generate ID unik setelah beberapa percobaan")
                
                # Cari PendingUpload by telegram_file_id (gunakan original ID)
                pending_upload = db.query(PendingUpload).filter(
                    PendingUpload.telegram_file_id == original_id
                ).first()
                
                # Siapkan field telegram metadata
                telegram_file_id = None
                telegram_chat_id = None
                telegram_message_id = None
                
                if pending_upload:
                    logger.info(f"📹 Film dibuat dari pending upload: {data.id}")
                    telegram_file_id = pending_upload.telegram_file_id
                    telegram_chat_id = pending_upload.telegram_chat_id
                    telegram_message_id = pending_upload.telegram_message_id
                    
                    # Mark pending upload sebagai 'used'
                    pending_upload.status = 'used'  # type: ignore
                else:
                    logger.info(f"🎬 Film dibuat manual (tidak dari pending upload): {data.id}")
                
                # Generate unique short_id
                short_id = get_unique_short_id()
                
                movie = Movie(
                    id=final_id,  # Gunakan final_id yang sudah di-generate jika duplicate
                    short_id=short_id,
                    title=data.title,
                    description=data.description,
                    poster_url=data.poster_url,
                    poster_file_id=data.poster_file_id,
                    video_link=data.video_link,
                    category=data.category,
                    telegram_file_id=telegram_file_id,
                    telegram_chat_id=telegram_chat_id,
                    telegram_message_id=telegram_message_id,
                    is_series=data.is_series or False,
                    views=0
                )
                
                db.add(movie)
                db.commit()
                db.refresh(movie)
                
                logger.info(f"✅ Film '{movie.title}' berhasil dibuat (ID: {movie.id}, short_id: {short_id}, has telegram video: {bool(telegram_file_id)})")
                
                # Jika film adalah series dan ada telegram_file_id, otomatis buat Part 1
                if data.is_series and telegram_file_id:  # type: ignore
                    try:
                        part_1 = Part(
                            movie_id=movie.id,
                            part_number=1,
                            title="Part 1",
                            telegram_file_id=telegram_file_id,
                            telegram_chat_id=telegram_chat_id,
                            telegram_message_id=telegram_message_id,
                            video_link=data.video_link,
                            views=0
                        )
                        db.add(part_1)
                        
                        # Update total_parts di movie
                        movie.total_parts = 1  # type: ignore
                        
                        db.commit()
                        db.refresh(part_1)
                        
                        logger.info(f"✅ Part 1 otomatis dibuat untuk series '{movie.title}' dengan telegram_file_id")
                    except Exception as part_error:
                        logger.warning(f"⚠️ Gagal auto-create Part 1: {part_error}")
                        # Tidak perlu raise error, film sudah berhasil dibuat
                
                # Buat response dengan info ID yang digunakan
                response = {
                    "message": "Movie berhasil ditambahkan",
                    "movie_id": movie.id,
                    "duplicate_resolved": final_id != original_id
                }
                if final_id != original_id:
                    response["message"] = f"Movie berhasil ditambahkan dengan ID baru (duplikat terdeteksi)"
                    response["original_id"] = original_id
                    response["new_id"] = final_id
                
                return response
                
            except HTTPException:
                raise
            except IntegrityError as ie:
                db.rollback()
                # Jika short_id collision, retry sekali
                if 'short_id' in str(ie) and attempt < max_retries - 1:
                    logger.warning(f"Short ID collision during insert, retrying... (attempt {attempt + 1}/{max_retries})")
                    continue
                else:
                    logger.error(f"IntegrityError waktu bikin movie: {ie}")
                    raise HTTPException(status_code=500, detail="Gagal membuat movie: Database constraint violation")
            except Exception as e:
                logger.error(f"Error waktu bikin movie: {e}")
                db.rollback()
                raise HTTPException(status_code=500, detail=str(e))
        
        # Jika sampai sini berarti loop selesai tanpa return (shouldn't happen)
        raise HTTPException(status_code=500, detail="Gagal membuat movie setelah beberapa percobaan")
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
            "poster_file_id": movie.poster_file_id,
            "video_link": movie.video_link,
            "category": movie.category,
            "is_series": movie.is_series,
            "total_parts": movie.total_parts,
            "views": movie.views,
            "created_at": to_iso_utc(movie.created_at)  # type: ignore
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
        if data.poster_file_id is not None:
            movie.poster_file_id = data.poster_file_id  # type: ignore
        if data.video_link is not None:
            movie.video_link = data.video_link  # type: ignore
        if data.category is not None:
            movie.category = data.category  # type: ignore
        if data.is_series is not None:
            movie.is_series = data.is_series  # type: ignore
        
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

@router.get("/movies/{movie_id}/parts")
async def get_movie_parts(movie_id: str, admin = Depends(get_current_admin)):
    """Get all parts for a movie"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        parts = db.query(Part).filter(Part.movie_id == movie_id).order_by(Part.part_number).all()
        
        return {
            "movie_id": movie_id,
            "movie_title": movie.title,
            "is_series": movie.is_series,
            "total_parts": movie.total_parts,
            "parts": [
                {
                    "id": ep.id,
                    "part_number": ep.part_number,
                    "title": ep.title,
                    "telegram_file_id": ep.telegram_file_id,
                    "telegram_chat_id": ep.telegram_chat_id,
                    "telegram_message_id": ep.telegram_message_id,
                    "video_link": ep.video_link,
                    "duration": ep.duration,
                    "file_size": ep.file_size,
                    "thumbnail_url": ep.thumbnail_url,
                    "views": ep.views,
                    "created_at": ep.created_at.isoformat() if ep.created_at is not None else None,
                    "updated_at": ep.updated_at.isoformat() if ep.updated_at is not None else None
                } for ep in parts
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting parts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

class PartCreate(BaseModel):
    part_number: int
    title: str
    pending_upload_id: Optional[int] = None
    telegram_file_id: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_message_id: Optional[str] = None
    video_link: Optional[str] = None
    duration: Optional[int] = None
    file_size: Optional[int] = None
    thumbnail_url: Optional[str] = None

class PartUpdate(BaseModel):
    part_number: Optional[int] = None
    title: Optional[str] = None
    telegram_file_id: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_message_id: Optional[str] = None
    video_link: Optional[str] = None
    duration: Optional[int] = None
    file_size: Optional[int] = None
    thumbnail_url: Optional[str] = None

@router.post("/movies/{movie_id}/parts")
async def create_movie_part(movie_id: str, data: PartCreate, admin = Depends(get_current_admin)):
    """Create new part for a movie"""
    db = SessionLocal()
    try:
        movie = db.query(Movie).filter(Movie.id == movie_id).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Film tidak ditemukan")
        
        telegram_file_id = data.telegram_file_id
        telegram_chat_id = data.telegram_chat_id
        telegram_message_id = data.telegram_message_id
        duration = data.duration
        file_size = data.file_size
        thumbnail_url = data.thumbnail_url
        
        if data.pending_upload_id:
            pending_upload = query_for_update(
                db.query(PendingUpload).filter(PendingUpload.id == data.pending_upload_id)
            ).first()
            
            if not pending_upload:
                raise HTTPException(status_code=404, detail="Pending upload tidak ditemukan")
            
            if pending_upload.status != 'pending':  # type: ignore
                raise HTTPException(
                    status_code=400,
                    detail=f"Upload ini sudah digunakan (status: {pending_upload.status})"
                )
            
            telegram_file_id = pending_upload.telegram_file_id
            telegram_chat_id = pending_upload.telegram_chat_id
            telegram_message_id = pending_upload.telegram_message_id
            duration = pending_upload.duration
            file_size = pending_upload.file_size
            thumbnail_url = pending_upload.thumbnail_url
            
            pending_upload.status = 'used'  # type: ignore
            
            logger.info(
                f"Part {data.part_number} created from pending upload {data.pending_upload_id} "
                f"by {admin.username}"
            )
        else:
            if not telegram_file_id or not telegram_file_id.strip():
                raise HTTPException(
                    status_code=400, 
                    detail="Telegram File ID atau Pending Upload ID diperlukan"
                )
            
            logger.info(
                f"Part {data.part_number} created manually (no pending upload) by {admin.username}"
            )
        
        existing = db.query(Part).filter(
            Part.movie_id == movie_id,
            Part.part_number == data.part_number
        ).first()
        
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Part {data.part_number} sudah ada untuk film ini"
            )
        
        part = Part(
            movie_id=movie_id,
            part_number=data.part_number,
            title=data.title,
            telegram_file_id=telegram_file_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
            video_link=data.video_link,
            duration=duration,
            file_size=file_size,
            thumbnail_url=thumbnail_url,
            views=0
        )
        
        db.add(part)
        db.commit()
        
        part_id = part.id
        part_number = part.part_number
        
        movie_obj = db.query(Movie).filter(Movie.id == movie_id).first()
        if movie_obj:
            movie_obj.total_parts = db.query(Part).filter(Part.movie_id == movie_id).count()  # type: ignore
            movie_obj.is_series = True  # type: ignore
            db.commit()
        
        return {
            "message": "Part berhasil dibuat",
            "part_id": part_id,
            "part_number": part_number,
            "from_pending_upload": bool(data.pending_upload_id)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating part: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.put("/movies/{movie_id}/parts/{part_id}")
async def update_movie_part(movie_id: str, part_id: int, data: PartUpdate, admin = Depends(get_current_admin)):
    """Update part"""
    db = SessionLocal()
    try:
        part = db.query(Part).filter(
            Part.id == part_id,
            Part.movie_id == movie_id
        ).first()
        
        if not part:
            raise HTTPException(status_code=404, detail="Part tidak ditemukan")
        
        update_data = data.dict(exclude_unset=True)
        
        if data.part_number and data.part_number != part.part_number:
            existing = db.query(Part).filter(
                Part.movie_id == movie_id,
                Part.part_number == data.part_number,
                Part.id != part_id
            ).first()
            
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail=f"Part {data.part_number} sudah ada untuk film ini"
                )
        
        updated_part = update_part(part_id, **update_data)
        
        if not updated_part:
            raise HTTPException(status_code=500, detail="Gagal mengupdate part")
        
        logger.info(f"Part {part_id} updated by {admin.username}")
        
        return {"message": "Part berhasil diupdate"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating part: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/movies/{movie_id}/parts/{part_id}")
async def delete_movie_part(movie_id: str, part_id: int, admin = Depends(get_current_admin)):
    """Delete part"""
    db = SessionLocal()
    try:
        part = db.query(Part).filter(
            Part.id == part_id,
            Part.movie_id == movie_id
        ).first()
        
        if not part:
            raise HTTPException(status_code=404, detail="Part tidak ditemukan")
        
        success = delete_part(part_id)
        
        if not success:
            raise HTTPException(status_code=500, detail="Gagal menghapus part")
        
        logger.info(f"Part {part_id} deleted from movie {movie_id} by {admin.username}")
        
        return {"message": "Part berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting part: {e}")
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
                    "admin_notes": r.admin_notes,
                    "created_at": to_iso_utc(r.created_at),  # type: ignore
                    "updated_at": to_iso_utc(r.updated_at)  # type: ignore
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
    admin_notes: Optional[str] = None

@router.put("/drama-requests/{request_id}/status")
async def update_request_status(request_id: int, data: RequestStatusUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        drama_request = db.query(DramaRequest).filter(DramaRequest.id == request_id).first()
        if not drama_request:
            raise HTTPException(status_code=404, detail="Request tidak ditemukan")
        
        drama_request.status = data.status  # type: ignore
        drama_request.admin_notes = data.admin_notes  # type: ignore
        drama_request.updated_at = now_utc()  # type: ignore
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
                    "created_at": to_iso_utc(w.created_at),  # type: ignore
                    "processed_at": to_iso_utc(w.processed_at)  # type: ignore
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
    with SessionLocal.begin() as db:
        withdrawal = query_for_update(
            db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id)
        ).first()
        
        if not withdrawal:
            raise HTTPException(status_code=404, detail="Withdrawal tidak ditemukan")
        
        previous_status = withdrawal.status
        
        if previous_status == 'approved' and data.status == 'approved':  # type: ignore
            logger.info(f"Withdrawal {withdrawal_id} already approved, idempotent request by admin {admin.username}")
            return {
                "message": "Already approved, no changes made",
                "status": "approved"
            }
        
        if previous_status in ['approved', 'rejected']:
            logger.warning(
                f"Attempted invalid transition for withdrawal {withdrawal_id}: {previous_status} → {data.status} by admin {admin.username}"
            )
            raise HTTPException(
                status_code=409,
                detail=f"Cannot change {previous_status} withdrawal. Create reversal if needed."
            )
        
        if previous_status != 'pending':  # type: ignore
            raise HTTPException(
                status_code=409,
                detail=f"Invalid state transition from {previous_status} to {data.status}"
            )
        
        allowed_statuses = ['approved', 'rejected']
        if data.status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{data.status}'. Allowed: {', '.join(allowed_statuses)}"
            )
        
        if data.status == 'approved':
            user = query_for_update(
                db.query(User).filter(User.telegram_id == withdrawal.telegram_id)
            ).first()
            
            if not user:
                raise HTTPException(status_code=404, detail="User tidak ditemukan")
            
            if user.commission_balance < withdrawal.amount:  # type: ignore
                logger.warning(
                    f"Insufficient balance for withdrawal {withdrawal_id}: "
                    f"user {user.telegram_id} has {user.commission_balance}, needs {withdrawal.amount}. "
                    f"Attempted by admin {admin.username}"
                )
                raise HTTPException(
                    status_code=409,
                    detail=f"Insufficient balance. User has {user.commission_balance}, withdrawal amount is {withdrawal.amount}"
                )
            
            user.commission_balance -= withdrawal.amount  # type: ignore
            withdrawal.status = 'approved'  # type: ignore
            withdrawal.processed_at = now_utc()  # type: ignore
            
            logger.info(
                f"Withdrawal {withdrawal_id} status changed: {previous_status} → {data.status} by admin {admin.username}. "
                f"Deducted {withdrawal.amount} from user {user.telegram_id}, new balance: {user.commission_balance}"
            )
        
        elif data.status == 'rejected':
            withdrawal.status = 'rejected'  # type: ignore
            withdrawal.processed_at = now_utc()  # type: ignore
            
            logger.info(
                f"Withdrawal {withdrawal_id} status changed: {previous_status} → {data.status} by admin {admin.username}"
            )
        
        logger.info(f"Withdrawal {withdrawal_id} transaction committed successfully")
        
        return {
            "message": f"Status withdrawal berhasil diupdate ke {data.status}",
            "status": data.status,
            "previous_status": previous_status
        }

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
                    "created_at": to_iso_utc(p.created_at),  # type: ignore
                    "paid_at": to_iso_utc(p.paid_at)  # type: ignore
                } for p in payments
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

@router.get("/pending-uploads")
async def get_pending_uploads_endpoint(page: int = 1, limit: int = 20, content_type: Optional[str] = None, admin = Depends(get_current_admin)):
    """
    Get pending uploads from Telegram storage (video or poster).
    
    Admin can select from these uploads when creating parts or movies,
    instead of manually pasting file_id.
    
    Query params:
    - content_type: 'video' or 'poster' to filter (optional)
    """
    try:
        result = get_pending_uploads(status='pending', page=page, limit=limit, content_type=content_type)
        
        return {
            "uploads": [
                {
                    "id": u['id'],
                    "telegram_file_id": u['telegram_file_id'],
                    "telegram_chat_id": u['telegram_chat_id'],
                    "telegram_message_id": u['telegram_message_id'],
                    "content_type": u.get('content_type', 'video'),
                    "duration": u['duration'],
                    "file_size": u['file_size'],
                    "thumbnail_url": u['thumbnail_url'],
                    "poster_width": u.get('poster_width'),
                    "poster_height": u.get('poster_height'),
                    "created_at": to_iso_utc(u['created_at'])
                } for u in result['uploads']
            ],
            "total": result['total'],
            "page": result['page'],
            "limit": result['limit'],
            "total_pages": result['total_pages']
        }
    except Exception as e:
        logger.error(f"Error getting pending uploads: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== ANALYTICS ENDPOINTS ====================

@router.get("/analytics/user-growth")
async def get_user_growth(period: str = 'daily', days: int = 30, admin = Depends(get_current_admin)):
    """
    Get user growth statistics over time.
    
    Query params:
    - period: 'daily' or 'monthly'
    - days: number of days to look back (default 30)
    """
    db = SessionLocal()
    try:
        from datetime import timedelta
        from sqlalchemy import cast, Date
        
        end_date = now_utc()
        start_date = end_date - timedelta(days=days)
        
        dialect_name = db.bind.dialect.name
        
        if period == 'monthly':
            if dialect_name == 'postgresql':
                query = db.query(
                    func.date_trunc('month', User.created_at).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(User.created_at >= start_date).group_by('period').order_by('period')
            else:
                query = db.query(
                    func.strftime('%Y-%m', User.created_at).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(User.created_at >= start_date).group_by('period').order_by('period')
        else:
            query = db.query(
                cast(User.created_at, Date).label('period'),
                func.count(User.id).label('total'),
                func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
            ).filter(User.created_at >= start_date).group_by('period').order_by('period')
        
        results = query.all()
        
        return {
            "period": period,
            "data": [
                {
                    "date": r.period.isoformat() if hasattr(r.period, 'isoformat') else str(r.period),
                    "total_users": r.total,
                    "vip_users": r.vip_count or 0
                } for r in results
            ]
        }
    except Exception as e:
        logger.error(f"Error getting user growth: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/analytics/revenue")
async def get_revenue_analytics(period: str = 'daily', days: int = 30, admin = Depends(get_current_admin)):
    """
    Get revenue analytics over time.
    
    Query params:
    - period: 'daily' or 'monthly'
    - days: number of days to look back (default 30)
    """
    db = SessionLocal()
    try:
        from datetime import timedelta
        from sqlalchemy import cast, Date
        
        end_date = now_utc()
        start_date = end_date - timedelta(days=days)
        
        dialect_name = db.bind.dialect.name
        
        if period == 'monthly':
            if dialect_name == 'postgresql':
                query = db.query(
                    func.date_trunc('month', Payment.created_at).label('period'),
                    func.sum(Payment.amount).label('revenue'),
                    func.count(Payment.id).label('transaction_count')
                ).filter(
                    Payment.status == 'success',
                    Payment.created_at >= start_date
                ).group_by('period').order_by('period')
            else:
                query = db.query(
                    func.strftime('%Y-%m', Payment.created_at).label('period'),
                    func.sum(Payment.amount).label('revenue'),
                    func.count(Payment.id).label('transaction_count')
                ).filter(
                    Payment.status == 'success',
                    Payment.created_at >= start_date
                ).group_by('period').order_by('period')
        else:
            query = db.query(
                cast(Payment.created_at, Date).label('period'),
                func.sum(Payment.amount).label('revenue'),
                func.count(Payment.id).label('transaction_count')
            ).filter(
                Payment.status == 'success',
                Payment.created_at >= start_date
            ).group_by('period').order_by('period')
        
        results = query.all()
        
        return {
            "period": period,
            "data": [
                {
                    "date": r.period.isoformat() if hasattr(r.period, 'isoformat') else str(r.period),
                    "revenue": r.revenue or 0,
                    "transactions": r.transaction_count
                } for r in results
            ]
        }
    except Exception as e:
        logger.error(f"Error getting revenue analytics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/analytics/top-movies")
async def get_top_movies(limit: int = 10, admin = Depends(get_current_admin)):
    """Get top movies by views"""
    db = SessionLocal()
    try:
        movies = db.query(Movie).order_by(desc(Movie.views)).limit(limit).all()
        
        return {
            "movies": [
                {
                    "id": m.id,
                    "title": m.title,
                    "category": m.category,
                    "views": m.views,
                    "is_series": m.is_series,
                    "total_parts": m.total_parts
                } for m in movies
            ]
        }
    except Exception as e:
        logger.error(f"Error getting top movies: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/analytics/conversion")
async def get_conversion_metrics(admin = Depends(get_current_admin)):
    """Get conversion metrics (free to VIP)"""
    db = SessionLocal()
    try:
        total_users = db.query(func.count(User.id)).scalar()
        vip_users = db.query(func.count(User.id)).filter(User.is_vip == True).scalar()
        
        conversion_rate = (vip_users / total_users * 100) if total_users > 0 else 0
        
        total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == 'success').scalar() or 0
        avg_revenue_per_user = total_revenue / total_users if total_users > 0 else 0
        
        return {
            "total_users": total_users,
            "vip_users": vip_users,
            "free_users": total_users - vip_users,
            "conversion_rate": round(conversion_rate, 2),
            "total_revenue": total_revenue,
            "avg_revenue_per_user": round(avg_revenue_per_user, 2)
        }
    except Exception as e:
        logger.error(f"Error getting conversion metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== EXPORT ENDPOINTS ====================

@router.get("/export/users")
async def export_users_csv(admin = Depends(get_current_admin)):
    """Export all users to CSV"""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    
    db = SessionLocal()
    try:
        users = db.query(User).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['ID', 'Telegram ID', 'Username', 'Ref Code', 'Is VIP', 'VIP Expires', 'Commission Balance', 'Total Referrals', 'Created At'])
        
        for u in users:
            writer.writerow([
                u.id,
                u.telegram_id,
                u.username or '',
                u.ref_code,
                'Yes' if u.is_vip else 'No',
                u.vip_expires_at.isoformat() if u.vip_expires_at else '',
                u.commission_balance,
                u.total_referrals,
                u.created_at.isoformat() if u.created_at else ''
            ])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users.csv"}
        )
    except Exception as e:
        logger.error(f"Error exporting users: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/export/payments")
async def export_payments_csv(admin = Depends(get_current_admin)):
    """Export all payments to CSV"""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    
    db = SessionLocal()
    try:
        payments = db.query(Payment).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['ID', 'Telegram ID', 'Order ID', 'Package', 'Amount', 'Status', 'Created At', 'Paid At'])
        
        for p in payments:
            writer.writerow([
                p.id,
                p.telegram_id,
                p.order_id,
                p.package_name,
                p.amount,
                p.status,
                p.created_at.isoformat() if p.created_at else '',
                p.paid_at.isoformat() if p.paid_at else ''
            ])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=payments.csv"}
        )
    except Exception as e:
        logger.error(f"Error exporting payments: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/export/withdrawals")
async def export_withdrawals_csv(admin = Depends(get_current_admin)):
    """Export all withdrawals to CSV"""
    import csv
    import io
    from fastapi.responses import StreamingResponse
    
    db = SessionLocal()
    try:
        withdrawals = db.query(Withdrawal).all()
        
        output = io.StringIO()
        writer = csv.writer(output)
        
        writer.writerow(['ID', 'Telegram ID', 'Amount', 'Payment Method', 'Account Number', 'Account Name', 'Status', 'Created At', 'Processed At'])
        
        for w in withdrawals:
            writer.writerow([
                w.id,
                w.telegram_id,
                w.amount,
                w.payment_method,
                w.account_number,
                w.account_name,
                w.status,
                w.created_at.isoformat() if w.created_at else '',
                w.processed_at.isoformat() if w.processed_at else ''
            ])
        
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=withdrawals.csv"}
        )
    except Exception as e:
        logger.error(f"Error exporting withdrawals: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== BULK OPERATIONS ====================

class BulkDeleteRequest(BaseModel):
    ids: List[int]

@router.post("/bulk/users/delete")
async def bulk_delete_users(data: BulkDeleteRequest, admin = Depends(get_current_admin)):
    """Bulk delete users"""
    db = SessionLocal()
    try:
        deleted_count = db.query(User).filter(User.id.in_(data.ids)).delete(synchronize_session=False)
        db.commit()
        
        logger.info(f"Bulk deleted {deleted_count} users by admin {admin.username}")
        return {"message": f"{deleted_count} users berhasil dihapus", "count": deleted_count}
    except Exception as e:
        logger.error(f"Error bulk deleting users: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.post("/bulk/movies/delete")
async def bulk_delete_movies(data: BulkDeleteRequest, admin = Depends(get_current_admin)):
    """Bulk delete movies"""
    db = SessionLocal()
    try:
        deleted_count = db.query(Movie).filter(Movie.id.in_(data.ids)).delete(synchronize_session=False)
        db.commit()
        
        logger.info(f"Bulk deleted {deleted_count} movies by admin {admin.username}")
        return {"message": f"{deleted_count} movies berhasil dihapus", "count": deleted_count}
    except Exception as e:
        logger.error(f"Error bulk deleting movies: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

class BulkUpdateVIPRequest(BaseModel):
    user_ids: List[int]
    is_vip: bool
    vip_days: Optional[int] = None

@router.post("/bulk/users/update-vip")
async def bulk_update_vip(data: BulkUpdateVIPRequest, admin = Depends(get_current_admin)):
    """Bulk update user VIP status"""
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.id.in_(data.user_ids)).all()
        
        for user in users:
            user.is_vip = data.is_vip  # type: ignore
            
            if data.is_vip and data.vip_days:
                from datetime import timedelta
                user.vip_expires_at = now_utc() + timedelta(days=data.vip_days)  # type: ignore
            elif not data.is_vip:
                user.vip_expires_at = None  # type: ignore
        
        db.commit()
        
        logger.info(f"Bulk updated VIP status for {len(users)} users by admin {admin.username}")
        return {"message": f"{len(users)} users berhasil diupdate", "count": len(users)}
    except Exception as e:
        logger.error(f"Error bulk updating VIP: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== SETTINGS ENDPOINTS ====================

class SettingUpdate(BaseModel):
    key: str
    value: str
    description: Optional[str] = None

@router.get("/settings")
async def get_all_settings(admin = Depends(get_current_admin)):
    """Get all settings"""
    db = SessionLocal()
    try:
        settings = db.query(Settings).all()
        
        return {
            "settings": [
                {
                    "id": s.id,
                    "key": s.key,
                    "value": s.value,
                    "description": s.description,
                    "updated_at": to_iso_utc(s.updated_at),
                    "updated_by": s.updated_by
                } for s in settings
            ]
        }
    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/settings/{key}")
async def get_setting(key: str, admin = Depends(get_current_admin)):
    """Get a specific setting"""
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        
        if not setting:
            raise HTTPException(status_code=404, detail="Setting tidak ditemukan")
        
        return {
            "key": setting.key,
            "value": setting.value,
            "description": setting.description
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.put("/settings/{key}")
async def update_setting(key: str, data: SettingUpdate, admin = Depends(get_current_admin)):
    """Update or create a setting"""
    db = SessionLocal()
    try:
        setting = db.query(Settings).filter(Settings.key == key).first()
        
        if setting:
            setting.value = data.value  # type: ignore
            if data.description:
                setting.description = data.description  # type: ignore
            setting.updated_at = now_utc()  # type: ignore
            setting.updated_by = admin.username  # type: ignore
        else:
            setting = Settings(
                key=data.key,
                value=data.value,
                description=data.description,
                updated_by=admin.username
            )
            db.add(setting)
        
        db.commit()
        
        logger.info(f"Setting {key} updated by admin {admin.username}")
        return {"message": "Setting berhasil diupdate"}
    except Exception as e:
        logger.error(f"Error updating setting: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== BROADCAST MESSAGE ====================

class BroadcastRequest(BaseModel):
    message: str
    target: str = 'all'
    vip_only: bool = False

@router.post("/broadcast")
async def broadcast_message(data: BroadcastRequest, admin = Depends(get_current_admin)):
    """
    Send broadcast message to users via Telegram bot.
    
    Note: This requires the bot to be running and TELEGRAM_BOT_TOKEN to be set.
    """
    db = SessionLocal()
    try:
        query = db.query(User)
        
        if data.vip_only:
            query = query.filter(User.is_vip == True)
        
        users = query.all()
        
        if not users:
            raise HTTPException(status_code=404, detail="Tidak ada user yang memenuhi kriteria")
        
        logger.info(f"Broadcast message prepared for {len(users)} users by admin {admin.username}")
        
        return {
            "message": f"Broadcast akan dikirim ke {len(users)} users",
            "user_count": len(users),
            "note": "Fitur broadcast memerlukan bot Telegram aktif. Message akan dikirim via background task."
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error preparing broadcast: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== TELEGRAM FILE PREVIEW ====================

@router.get("/telegram-file/{file_id:path}")
async def get_telegram_file_preview(file_id: str, admin = Depends(get_current_admin)):
    """
    Mendapatkan preview file (thumbnail/poster) dari Telegram Bot API.
    File ID diambil dari pending_uploads.thumbnail_url atau telegram_file_id.
    
    Supports both:
    - Telegram file_id (e.g., "AgACAgUAAxkBAAI...")
    - Direct HTTPS URLs (untuk backward compatibility dengan data lama)
    
    Returns file sebagai image stream untuk ditampilkan di admin panel.
    """
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(
            status_code=503, 
            detail="Telegram Bot Token tidak tersedia. Preview tidak dapat ditampilkan."
        )
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if file_id.startswith('http://') or file_id.startswith('https://'):
                file_response = await client.get(file_id)
                file_response.raise_for_status()
                
                content_type = file_response.headers.get('Content-Type', 'image/jpeg')
                
                return StreamingResponse(
                    io.BytesIO(file_response.content),
                    media_type=content_type,
                    headers={
                        "Cache-Control": "public, max-age=3600",
                        "Content-Disposition": f"inline; filename=preview.jpg"
                    }
                )
            
            get_file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile"
            params = {"file_id": file_id}
            
            response = await client.get(get_file_url, params=params)
            response.raise_for_status()
            
            data = response.json()
            if not data.get("ok"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Telegram API error: {data.get('description', 'Invalid file_id')}"
                )
            
            file_path = data["result"]["file_path"]
            download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
            
            file_response = await client.get(download_url)
            file_response.raise_for_status()
            
            content_type = file_response.headers.get('Content-Type', 'image/jpeg')
            
            return StreamingResponse(
                io.BytesIO(file_response.content),
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Content-Disposition": f"inline; filename=preview_{file_id[:10]}.jpg"
                }
            )
        
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching file: {e.response.status_code} - {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Gagal mengambil file: HTTP {e.response.status_code}"
        )
    except httpx.RequestError as e:
        logger.error(f"Request error fetching file from Telegram: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"Gagal mengambil file dari Telegram: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Error in telegram file preview: {e}")
        raise HTTPException(status_code=500, detail=str(e))
