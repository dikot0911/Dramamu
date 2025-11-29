from fastapi import APIRouter, HTTPException, Depends, Header, Request, Response, Cookie, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, cast
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
from csrf_protection import require_csrf_token, get_csrf_token_for_session
from database import (
    SessionLocal, User, Movie, Part, DramaRequest, Withdrawal, Payment, Admin,
    PendingUpload, Settings, Broadcast,
    get_parts_by_movie_id, create_part, update_part, delete_part,
    get_part_by_id, get_pending_uploads, get_unique_short_id
)
from config import now_utc, is_production, TELEGRAM_BOT_TOKEN
from sqlalchemy import func, desc, Integer, case
from sqlalchemy.exc import IntegrityError
from referral_utils import process_referral_commission, send_referrer_notification
from security.brute_force import BruteForceProtector
from security.config import SecurityConfig
from security.audit_logger import log_security_event
from security.ip_blocker import SSRFProtector
import logging
import httpx
import io
import asyncio

logger = logging.getLogger(__name__)

security_config = SecurityConfig()
brute_force_protector = BruteForceProtector(security_config.brute_force)
ssrf_protector = SSRFProtector(security_config.ssrf)

def validate_external_url(url: str) -> bool:
    """Validate URL using SSRF protector before making external requests."""
    return ssrf_protector.is_safe_url(url)

async def send_telegram_notification(telegram_id: int, message: str, logger_context: str = "") -> bool:
    """
    Send Telegram notification with SSRF validation.
    
    Args:
        telegram_id: User's Telegram ID
        message: HTML formatted message
        logger_context: Context for logging (e.g., "approval", "rejection")
    
    Returns:
        True if sent successfully, False otherwise
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("⚠️ TELEGRAM_BOT_TOKEN tidak tersedia - skip notifikasi")
        return False
    
    telegram_api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if not validate_external_url(telegram_api_url):
        logger.error(f"❌ SSRF protection blocked Telegram API URL for {logger_context}")
        return False
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                telegram_api_url,
                json={
                    "chat_id": telegram_id,
                    "text": message,
                    "parse_mode": "HTML"
                },
                timeout=10.0
            )
        logger.info(f"✅ Notifikasi {logger_context} dikirim ke user {telegram_id}")
        return True
    except Exception as notif_error:
        logger.error(f"❌ Gagal kirim notifikasi {logger_context} ke user {telegram_id}: {notif_error}")
        return False

router = APIRouter(prefix="/admin", tags=["Admin"])

SPECIAL_PROTECTED_USERNAME = "Notfound"

def is_special_protected_user(admin) -> bool:
    """
    Check apakah admin adalah user spesial yang dilindungi.
    
    User spesial "Notfound" memiliki privilege khusus:
    - Tidak bisa dihapus atau diedit oleh siapapun
    - Tidak ditampilkan di sidebar admin aktif
    - Tidak ditampilkan di halaman kelola admin
    - Punya akses ke payment-settings seperti super admin
    
    Args:
        admin: Admin object dari database
    
    Returns:
        True jika admin adalah user spesial yang dilindungi
    """
    if not admin:
        return False
    return admin.username == SPECIAL_PROTECTED_USERNAME

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

class QRISApproveRequest(BaseModel):
    order_id: str

class QRISRejectRequest(BaseModel):
    order_id: str
    reason: Optional[str] = None

class ManualVIPActivationRequest(BaseModel):
    telegram_id: str
    package_name: str
    order_id: Optional[str] = None

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
    
    # CRITICAL: Enforce session validation to prevent revocation bypass
    # JWT must be tied to a valid session - no session = no access
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
    
    # CRITICAL FIX: Touch session to update last_activity
    # This ensures online status indicator works correctly
    # If touch fails, session is expired or deleted - force re-auth
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
            admin_count = db.query(func.count(Admin.id)).filter(
                Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
            ).scalar()
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
    - Brute force protection
    """
    client_ip = http_request.client.host if http_request.client else "unknown"
    
    if not brute_force_protector.can_attempt(request.username, client_ip):
        lockout_time = brute_force_protector.get_lockout_time(request.username, client_ip)
        log_security_event(
            event_type="brute_force_lockout",
            severity="warning",
            ip_address=client_ip,
            username=request.username,
            details={"lockout_minutes_remaining": lockout_time}
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Terlalu banyak percobaan login gagal",
                "retry_after_minutes": lockout_time
            }
        )
    
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
                admin_exists = db.query(Admin).filter(
                    Admin.username == creds['admin_username'],
                    Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
                ).first()
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
        brute_force_protector.record_failed_attempt(request.username, client_ip)
        log_security_event(
            event_type="login_failed",
            severity="warning",
            ip_address=client_ip,
            username=request.username,
            details={"reason": "Invalid credentials"}
        )
        logger.warning(f"Login gagal untuk username: {request.username}")
        raise HTTPException(
            status_code=401,
            detail={"error": "Username atau password salah"}
        )
    
    brute_force_protector.reset_attempts(request.username, client_ip)
    
    # Update display_name jika provided
    if request.display_name:
        db = SessionLocal()
        try:
            db_admin = db.query(Admin).filter(
                Admin.id == admin.id,
                Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
            ).first()
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
    
    log_security_event(
        event_type="login_success",
        severity="info",
        ip_address=client_ip,
        user_id=str(admin.id),
        username=admin.username,
        details={
            "is_super_admin": is_super_admin(admin),
            "session_created": True
        }
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

@router.get("/csrf")
async def get_csrf_token(admin_session: Optional[str] = Cookie(None)):
    """
    Get CSRF token for current session.
    
    Frontend should call this endpoint after login to get the CSRF token,
    then include it as X-CSRF-Token header in all state-changing requests.
    
    Returns:
        csrf_token: Token to include in X-CSRF-Token header
    
    Raises:
        401: If not authenticated (no valid session)
    """
    if not admin_session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    csrf_token = get_csrf_token_for_session(admin_session)
    
    if not csrf_token:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    return {"csrf_token": csrf_token}

@router.get("/admin-users", response_model=List[AdminInfo])
async def list_admins(current_admin = Depends(get_current_admin)):
    """
    List semua admin users (khusus super admin).
    User spesial "Notfound" dikecualikan dari daftar.
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can access this endpoint")
    
    db = SessionLocal()
    try:
        admins = db.query(Admin).filter(
            Admin.deleted_at == None,
            Admin.username != SPECIAL_PROTECTED_USERNAME
        ).all()
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

@router.post("/admin-users", response_model=AdminInfo, dependencies=[Depends(require_csrf_token)])
async def create_admin(request: CreateAdminRequest, current_admin = Depends(get_current_admin)):
    """
    Create admin user baru (khusus super admin).
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can create admin")
    
    from admin_auth import hash_password
    
    db = SessionLocal()
    try:
        existing = db.query(Admin).filter(
            Admin.username == request.username,
            Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
        ).first()
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

@router.put("/admin-users/{admin_id}", response_model=AdminInfo, dependencies=[Depends(require_csrf_token)])
async def update_admin(admin_id: int, request: UpdateAdminRequest, current_admin = Depends(get_current_admin)):
    """
    Update admin user (khusus super admin).
    User spesial "Notfound" tidak bisa diedit.
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can update admin")
    
    from admin_auth import hash_password
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(
            Admin.id == admin_id,
            Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
        ).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        if is_special_protected_user(admin):
            raise HTTPException(status_code=403, detail="User ini dilindungi dan tidak bisa diedit")
        
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

@router.delete("/admin-users/{admin_id}", dependencies=[Depends(require_csrf_token)])
async def delete_admin(admin_id: int, current_admin = Depends(get_current_admin)):
    """
    Delete admin user (khusus super admin).
    User spesial "Notfound" tidak bisa dihapus.
    """
    if not is_super_admin(current_admin):
        raise HTTPException(status_code=403, detail="Only super admin can delete admin")
    
    if admin_id == current_admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    
    db = SessionLocal()
    try:
        admin = db.query(Admin).filter(
            Admin.id == admin_id,
            Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
        ).first()
        if not admin:
            raise HTTPException(status_code=404, detail="Admin not found")
        
        if is_special_protected_user(admin):
            raise HTTPException(status_code=403, detail="User ini dilindungi dan tidak bisa dihapus")
        
        if is_super_admin(admin):
            raise HTTPException(status_code=400, detail="Cannot delete super admin")
        
        # BUG FIX #8: Soft delete instead of hard delete
        username = admin.username
        admin.deleted_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(f"Super admin {current_admin.username} soft-deleted admin: {username}")
        
        return {"message": f"Admin {username} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting admin: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete admin")
    finally:
        db.close()

@router.post("/admin-users/{admin_id}/kick", dependencies=[Depends(require_csrf_token)])
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
        admin = db.query(Admin).filter(
            Admin.id == admin_id,
            Admin.deleted_at == None  # BUG FIX #8: Exclude soft-deleted admins
        ).first()
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
    User spesial "Notfound" dikecualikan dari daftar.
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
        
        # Get ALL admins (not just active ones), exclude special protected user
        admins = db.query(Admin).filter(
            Admin.is_active == True,
            Admin.deleted_at == None,
            Admin.username != SPECIAL_PROTECTED_USERNAME
        ).all()
        
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
        # BUG FIX #8: Exclude soft-deleted users
        query = db.query(User).filter(User.deleted_at == None)
        
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
        # BUG FIX #8: Exclude soft-deleted records from all counts
        total_users = db.query(func.count(User.id)).filter(User.deleted_at == None).scalar()
        vip_users = db.query(func.count(User.id)).filter(
            User.is_vip == True,
            User.deleted_at == None
        ).scalar()
        total_movies = db.query(func.count(Movie.id)).filter(Movie.deleted_at == None).scalar()
        pending_requests = db.query(func.count(DramaRequest.id)).filter(
            DramaRequest.status == 'pending',
            DramaRequest.deleted_at == None
        ).scalar()
        pending_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == 'pending').scalar()
        total_revenue = db.query(func.sum(Payment.amount)).filter(Payment.status == 'success').scalar() or 0
        
        # Get 5 most recent for display list (no date filter - always show latest 5)
        recent_users = db.query(User).filter(User.deleted_at == None).order_by(desc(User.created_at)).limit(5).all()
        recent_payments = db.query(Payment).order_by(desc(Payment.created_at)).limit(5).all()
        
        # FIX: Get data for last 60 days for charts (separate from display list)
        from datetime import timedelta
        sixty_days_ago = now_utc() - timedelta(days=60)
        
        # Get users created in last 60 days for chart data
        chart_users = db.query(User).filter(
            User.deleted_at == None,
            User.created_at >= sixty_days_ago
        ).order_by(desc(User.created_at)).all()
        
        # Get payments in last 60 days for chart data
        chart_payments = db.query(Payment).filter(
            Payment.created_at >= sixty_days_ago
        ).order_by(desc(Payment.created_at)).all()
        
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
            ],
            "chart_users": [
                {
                    "id": u.id,
                    "created_at": to_iso_utc(u.created_at)  # type: ignore
                } for u in chart_users
            ],
            "chart_payments": [
                {
                    "id": p.id,
                    "amount": p.amount,
                    "status": p.status,
                    "created_at": to_iso_utc(p.created_at)  # type: ignore
                } for p in chart_payments
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
        # BUG FIX #8: Exclude soft-deleted records from all counts
        pending_requests = db.query(func.count(DramaRequest.id)).filter(
            DramaRequest.status == 'pending',
            DramaRequest.deleted_at == None  # Exclude soft-deleted requests
        ).scalar()
        pending_withdrawals = db.query(func.count(Withdrawal.id)).filter(Withdrawal.status == 'pending').scalar()
        total_users = db.query(func.count(User.id)).filter(
            User.deleted_at == None  # Exclude soft-deleted users
        ).scalar()
        vip_users = db.query(func.count(User.id)).filter(
            User.is_vip == True,
            User.deleted_at == None  # Exclude soft-deleted users
        ).scalar()
        total_movies = db.query(func.count(Movie.id)).filter(Movie.deleted_at == None).scalar()
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
    mode: str = "absolute"

@router.get("/users")
async def get_all_users(page: int = 1, limit: int = 20, search: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(User).filter(
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        )
        
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
        user = db.query(User).filter(
            User.id == user_id,
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        ).first()
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

@router.put("/users/{user_id}/vip", dependencies=[Depends(require_csrf_token)])
async def update_user_vip(user_id: int, data: UserUpdateVIP, admin = Depends(get_current_admin)):
    """
    Update VIP status user dengan mode delta atau absolute.
    
    Mode 'delta': vip_days positif = tambah durasi, negatif = kurangi durasi
    Mode 'absolute': vip_days = set durasi baru dari sekarang
    """
    db = SessionLocal()
    try:
        from datetime import timedelta, datetime
        
        user = db.query(User).filter(
            User.id == user_id,
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        ).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        user.is_vip = data.is_vip  # type: ignore
        
        if data.is_vip and data.vip_days is not None:
            if data.mode == "delta":
                current_expiry = cast(datetime | None, user.vip_expires_at)
                
                if current_expiry is not None and current_expiry > now_utc():
                    user.vip_expires_at = current_expiry + timedelta(days=data.vip_days)  # type: ignore
                else:
                    if data.vip_days > 0:
                        user.vip_expires_at = now_utc() + timedelta(days=data.vip_days)  # type: ignore
                    else:
                        user.vip_expires_at = now_utc()  # type: ignore
                
                if user.vip_expires_at and user.vip_expires_at < now_utc():
                    user.vip_expires_at = now_utc()  # type: ignore
            else:
                user.vip_expires_at = now_utc() + timedelta(days=data.vip_days)  # type: ignore
        elif not data.is_vip:
            user.vip_expires_at = None  # type: ignore
        
        db.commit()
        
        logger.info(f"Admin {admin.username} updated VIP for user {user_id}: is_vip={data.is_vip}, mode={data.mode}, days={data.vip_days}")
        
        return {
            "message": "VIP status berhasil diupdate",
            "is_vip": user.is_vip,
            "vip_expires_at": to_iso_utc(user.vip_expires_at)
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error waktu update status VIP user: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/users/{user_id}", dependencies=[Depends(require_csrf_token)])
async def delete_user(user_id: int, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        user = db.query(User).filter(
            User.id == user_id,
            User.deleted_at == None  # BUG FIX #8: Exclude soft-deleted users
        ).first()
        if not user:
            raise HTTPException(status_code=404, detail="User tidak ditemukan")
        
        # BUG FIX #8: Soft delete instead of hard delete
        user.deleted_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(f"Admin soft-deleted user: {user.telegram_id}")
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
    base_like_count: Optional[int] = None
    base_favorite_count: Optional[int] = None

@router.get("/movies")
async def get_all_movies_admin(page: int = 1, limit: int = 20, search: Optional[str] = None, category: Optional[str] = None, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        query = db.query(Movie).filter(Movie.deleted_at == None)
        
        if search:
            query = query.filter(
                (Movie.title.contains(search)) | (Movie.description.contains(search))
            )
        
        if category:
            query = query.filter(Movie.category == category)
        
        total = query.count()
        offset = (page - 1) * limit
        movies = query.order_by(desc(Movie.created_at)).offset(offset).limit(limit).all()
        
        movie_ids = [m.id for m in movies]
        
        part_views_by_movie = {}
        if movie_ids:
            part_views_query = db.query(
                Part.movie_id,
                func.coalesce(func.sum(Part.views), 0).label('total_part_views')
            ).filter(
                Part.movie_id.in_(movie_ids),
                Part.deleted_at == None
            ).group_by(Part.movie_id).all()
            
            for movie_id, total_part_views in part_views_query:
                part_views_by_movie[movie_id] = int(total_part_views)
        
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
                    "views": (m.views or 0) + part_views_by_movie.get(m.id, 0),
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

@router.get("/movies/stats")
async def get_movies_stats(days: int = 30, admin = Depends(get_current_admin)):
    """
    Get aggregated movie statistics from database.
    
    This endpoint provides accurate totals without pagination limits,
    ensuring correct statistics display in admin panel.
    
    Args:
        days: Number of days for timeline data (default: 30)
    """
    db = SessionLocal()
    try:
        from sqlalchemy import func, case
        from datetime import timedelta
        
        # BUG FIX: Aggregate stats directly from database (no pagination limit)
        # Exclude soft-deleted movies
        base_query = db.query(Movie).filter(Movie.deleted_at == None)
        
        # Total movie count
        total_movies = base_query.count()
        
        # Total views - sum Movie.views AND Part.views for complete cross-platform count
        # Movie.views = views from web mini app (tracked in /api/v1/watch_history)
        # Part.views = views from Telegram bot (tracked in telegram_delivery.py)
        # These are SEPARATE counters for DIFFERENT platforms, no double counting
        movie_views = db.query(func.coalesce(func.sum(Movie.views), 0)).filter(
            Movie.deleted_at == None
        ).scalar() or 0
        
        part_views = db.query(func.coalesce(func.sum(Part.views), 0)).filter(
            Part.deleted_at == None
        ).scalar() or 0
        
        total_views = int(movie_views) + int(part_views)
        
        # Category breakdown
        category_stats = db.query(
            Movie.category,
            func.count(Movie.id).label('count')
        ).filter(
            Movie.deleted_at == None,
            Movie.category.isnot(None)
        ).group_by(Movie.category).all()
        
        # Top category
        top_category = None
        top_category_count = 0
        for cat, count in category_stats:
            if count > top_category_count:
                top_category = cat
                top_category_count = count
        
        # Recent additions (last 7 days)
        seven_days_ago = now_utc() - timedelta(days=7)
        recent_additions = base_query.filter(
            Movie.created_at >= seven_days_ago
        ).count()
        
        # Timeline data: count movies added per day for the given period
        n_days_ago = now_utc() - timedelta(days=days)
        movies_in_period = base_query.filter(
            Movie.created_at >= n_days_ago
        ).all()
        
        # Build timeline data with movie created_at dates
        timeline_movies = [
            {"created_at": to_iso_utc(m.created_at)}
            for m in movies_in_period
        ]
        
        return {
            "total_movies": total_movies,
            "total_views": total_views,
            "top_category": top_category,
            "top_category_count": top_category_count,
            "recent_additions": recent_additions,
            "category_breakdown": [
                {"name": cat, "count": count}
                for cat, count in category_stats
            ],
            "timeline_movies": timeline_movies
        }
    finally:
        db.close()

@router.post("/movies", dependencies=[Depends(require_csrf_token)])
async def create_movie(data: MovieCreate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    max_retries = 2
    
    try:
        for attempt in range(max_retries):
            try:
                # Cek apakah movie ID sudah ada (exclude soft-deleted)
                existing = db.query(Movie).filter(
                    Movie.id == data.id,
                    Movie.deleted_at == None
                ).first()
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
                        # Cek apakah ID baru sudah unique (exclude soft-deleted)
                        if not db.query(Movie).filter(
                            Movie.id == new_id,
                            Movie.deleted_at == None
                        ).first():
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
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
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
            "base_like_count": movie.base_like_count or 0,
            "base_favorite_count": movie.base_favorite_count or 0,
            "created_at": to_iso_utc(movie.created_at)  # type: ignore
        }
    finally:
        db.close()

@router.put("/movies/{movie_id}", dependencies=[Depends(require_csrf_token)])
async def update_movie(movie_id: str, data: MovieUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
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
        if data.base_like_count is not None:
            movie.base_like_count = data.base_like_count  # type: ignore
        if data.base_favorite_count is not None:
            movie.base_favorite_count = data.base_favorite_count  # type: ignore
        
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

@router.delete("/movies/{movie_id}", dependencies=[Depends(require_csrf_token)])
async def delete_movie(movie_id: str, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude already soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
        if not movie:
            raise HTTPException(status_code=404, detail="Movie tidak ditemukan")
        
        # BUG FIX #8: Soft delete instead of hard delete
        movie.deleted_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(f"Admin soft-deleted movie: {movie_id} ({movie.title})")
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
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
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

@router.post("/movies/{movie_id}/parts", dependencies=[Depends(require_csrf_token)])
async def create_movie_part(movie_id: str, data: PartCreate, admin = Depends(get_current_admin)):
    """Create new part for a movie"""
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted movies
        movie = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
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
        
        # BUG FIX #8: Exclude soft-deleted movies
        movie_obj = db.query(Movie).filter(
            Movie.id == movie_id,
            Movie.deleted_at == None
        ).first()
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

@router.put("/movies/{movie_id}/parts/{part_id}", dependencies=[Depends(require_csrf_token)])
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

@router.delete("/movies/{movie_id}/parts/{part_id}", dependencies=[Depends(require_csrf_token)])
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
        # BUG FIX #8: Exclude soft-deleted drama requests
        query = db.query(DramaRequest).filter(DramaRequest.deleted_at == None)
        
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

@router.put("/drama-requests/{request_id}/status", dependencies=[Depends(require_csrf_token)])
async def update_request_status(request_id: int, data: RequestStatusUpdate, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted drama requests
        drama_request = db.query(DramaRequest).filter(
            DramaRequest.id == request_id,
            DramaRequest.deleted_at == None
        ).first()
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

@router.delete("/drama-requests/{request_id}", dependencies=[Depends(require_csrf_token)])
async def delete_drama_request(request_id: int, admin = Depends(get_current_admin)):
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude already soft-deleted drama requests
        drama_request = db.query(DramaRequest).filter(
            DramaRequest.id == request_id,
            DramaRequest.deleted_at == None
        ).first()
        if not drama_request:
            raise HTTPException(status_code=404, detail="Request tidak ditemukan")
        
        # BUG FIX #8: Soft delete instead of hard delete
        drama_request.deleted_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(f"Admin soft-deleted drama request: {request_id}")
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

@router.put("/withdrawals/{withdrawal_id}/status", dependencies=[Depends(require_csrf_token)])
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
                db.query(User).filter(
                    User.telegram_id == withdrawal.telegram_id,
                    User.deleted_at == None
                )
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
                    "paid_at": to_iso_utc(p.paid_at),  # type: ignore
                    "screenshot_url": p.screenshot_url,
                    "qris_url": p.qris_url,
                    "expires_at": to_iso_utc(p.expires_at)  # type: ignore
                } for p in payments
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    finally:
        db.close()

@router.post("/payments/qris/approve", dependencies=[Depends(require_csrf_token)])
async def approve_qris_payment(data: QRISApproveRequest, admin = Depends(get_current_admin)):
    """
    Approve QRIS payment manual dan aktifkan VIP user.
    
    Flow:
    1. Cari payment dengan order_id dan status 'qris_pending'
    2. Update status ke 'paid' dan paid_at timestamp
    3. Aktifkan VIP berdasarkan package_name (1/3/7/15/30 hari)
    4. Process referral commission (25%) jika pembayaran pertama
    5. Kirim notifikasi Telegram ke user
    """
    db = SessionLocal()
    try:
        logger.info(f"Admin {admin.username} approve QRIS payment: {data.order_id}")
        
        # CRITICAL: Use row-level locking to prevent race conditions
        payment = query_for_update(
            db.query(Payment).filter(Payment.order_id == data.order_id)
        ).first()
        
        if not payment:
            logger.error(f"Payment tidak ditemukan: {data.order_id}")
            raise HTTPException(status_code=404, detail=f"Payment dengan order_id {data.order_id} tidak ditemukan")
        
        # CRITICAL: Refresh row to get latest data after lock acquired
        # Without refresh, we might be working with stale in-memory data
        db.refresh(payment)
        
        # CRITICAL: Idempotency check AFTER lock AND refresh
        # This prevents duplicate processing if concurrent webhook/polling already processed
        if str(payment.status) != 'qris_pending':
            logger.warning(f"⏭️ Payment {data.order_id} already processed (status: {payment.status}), skipping admin approval")
            raise HTTPException(status_code=400, detail=f"Payment sudah diproses dengan status {payment.status}, silakan refresh halaman")
        
        payment.status = 'paid'  # Admin approval uses 'paid' status (distinct from 'success')
        payment.paid_at = now_utc()
        
        # CRITICAL: Lock user record to prevent concurrent VIP activation
        user = query_for_update(
            db.query(User).filter(
                User.telegram_id == payment.telegram_id,
                User.deleted_at == None
            )
        ).first()
        if not user:
            logger.error(f"User tidak ditemukan: {payment.telegram_id}")
            raise HTTPException(status_code=404, detail=f"User dengan telegram_id {payment.telegram_id} tidak ditemukan")
        
        days_map = {
            "VIP 1 Hari": 1,
            "VIP 3 Hari": 3,
            "VIP 7 Hari": 7,
            "VIP 30 Hari": 30,
            "VIP 180 Hari": 180
        }
        days = days_map.get(str(payment.package_name), 1)
        
        user.is_vip = True
        current_expiry_col = user.vip_expires_at
        current_expiry: datetime | None = cast(datetime | None, current_expiry_col)
        
        if current_expiry is not None and current_expiry > now_utc():
            user.vip_expires_at = current_expiry + timedelta(days=days)
        else:
            user.vip_expires_at = now_utc() + timedelta(days=days)
        
        logger.info(f"VIP diaktifkan untuk user {payment.telegram_id} selama {days} hari (paket: {package_name_str})")
        
        # CRITICAL: Commit VIP activation BEFORE processing commission
        # This ensures VIP persists even if commission processing fails with IntegrityError
        # Separating commits prevents VIP rollback when commission has race condition
        db.commit()
        logger.info(f"✅ VIP activation committed for user {payment.telegram_id}")
        
        # CRITICAL: Process commission AFTER VIP commit
        # If concurrent webhook already paid commission, IntegrityError is caught gracefully
        # VIP is already committed above, so it won't be rolled back
        commission_paid = False
        commission_amount = 0
        referrer_id = None
        
        try:
            commission_paid, commission_amount, referrer_id = process_referral_commission(db, payment, user)
            if commission_paid:
                logger.info(f"💰 Commission paid via admin approval: Rp {commission_amount} to {referrer_id}")
            db.commit()  # Commit commission if successful
            logger.info(f"✅ Commission processing committed for payment {payment.id}")
        except IntegrityError as integrity_error:
            # PaymentCommission unique constraint violated - commission already processed by concurrent transaction
            logger.warning(f"⏭️ Commission already paid by concurrent transaction for payment {payment.id}, rolling back commission attempt")
            db.rollback()  # Rollback commission attempt (VIP already committed above)
            commission_paid = False
            commission_amount = 0
            referrer_id = None
        except Exception as e:
            # Other errors during commission processing
            logger.error(f"❌ Error processing commission for payment {payment.id}: {e}")
            db.rollback()  # Rollback commission attempt (VIP already committed above)
            commission_paid = False
            commission_amount = 0
            referrer_id = None
            # Don't raise - VIP is already committed, commission failure is non-critical
        
        telegram_id_int = int(payment.telegram_id)
        message = (
            f"✅ <b>Pembayaran QRIS Disetujui!</b>\n\n"
            f"Paket: {payment.package_name}\n"
            f"Status VIP kamu sudah aktif!\n\n"
            f"Selamat menonton! 🎬"
        )
        await send_telegram_notification(telegram_id_int, message, "approval")
        
        return {
            "success": True,
            "message": f"Payment {data.order_id} berhasil diapprove",
            "payment": {
                "order_id": payment.order_id,
                "telegram_id": payment.telegram_id,
                "package_name": payment.package_name,
                "amount": payment.amount,
                "status": payment.status,
                "paid_at": to_iso_utc(payment.paid_at)
            },
            "vip_activated": True,
            "vip_days": days,
            "vip_expires_at": to_iso_utc(user.vip_expires_at),
            "commission_paid": commission_paid,
            "commission_amount": commission_amount if commission_amount else 0
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error approve QRIS payment: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=f"Error approve payment: {str(e)}")
    finally:
        db.close()

@router.post("/payments/qris/reject", dependencies=[Depends(require_csrf_token)])
async def reject_qris_payment(data: QRISRejectRequest, admin = Depends(get_current_admin)):
    """
    Reject QRIS payment manual dan kirim notifikasi ke user.
    
    Flow:
    1. Cari payment dengan order_id
    2. Update status ke 'rejected'
    3. Kirim notifikasi Telegram ke user dengan alasan penolakan
    """
    db = SessionLocal()
    try:
        logger.info(f"Admin {admin.username} reject QRIS payment: {data.order_id}, reason: {data.reason or 'tidak ada alasan'}")
        
        payment = db.query(Payment).filter(Payment.order_id == data.order_id).first()
        
        if not payment:
            logger.error(f"Payment tidak ditemukan: {data.order_id}")
            raise HTTPException(status_code=404, detail=f"Payment dengan order_id {data.order_id} tidak ditemukan")
        
        previous_status = payment.status
        payment.status = 'rejected'
        
        db.commit()
        
        telegram_id_int = int(payment.telegram_id)
        reason_text = data.reason if data.reason else "Pembayaran tidak valid atau tidak sesuai"
        message = (
            f"❌ <b>Pembayaran QRIS Ditolak</b>\n\n"
            f"Order ID: {payment.order_id}\n"
            f"Paket: {payment.package_name}\n"
            f"Amount: Rp {payment.amount:,}\n\n"
            f"Alasan: {reason_text}\n\n"
            f"Silakan hubungi admin jika ada pertanyaan."
        )
        await send_telegram_notification(telegram_id_int, message, "rejection")
        
        return {
            "success": True,
            "message": f"Payment {data.order_id} berhasil direject",
            "payment": {
                "order_id": payment.order_id,
                "telegram_id": payment.telegram_id,
                "package_name": payment.package_name,
                "amount": payment.amount,
                "previous_status": previous_status,
                "current_status": payment.status
            },
            "reason": data.reason
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error reject QRIS payment: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=f"Error reject payment: {str(e)}")
    finally:
        db.close()

@router.post("/manual-vip-activation", dependencies=[Depends(require_csrf_token)])
async def manual_vip_activation(request: ManualVIPActivationRequest, admin = Depends(get_current_admin)):
    """
    Endpoint untuk admin mengaktifkan VIP user secara manual.
    Untuk antisipasi ketika gateway error atau gangguan lain.
    
    Flow:
    1. Cari user berdasarkan telegram_id
    2. Set user.is_vip = True dan hitung expiry berdasarkan package_name
    3. Update payment status jadi 'success' jika order_id diberikan
    4. Kirim notifikasi Telegram ke user
    5. Log semua aktivitas
    """
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta
        
        telegram_id = request.telegram_id
        package_name = request.package_name
        order_id = request.order_id
        
        logger.info(f"👤 Admin {admin.username} manual activate VIP untuk user {telegram_id}, paket: {package_name}, order_id: {order_id}")
        
        # CRITICAL: Lock user record to prevent concurrent VIP activation
        user = query_for_update(
            db.query(User).filter(
                User.telegram_id == str(telegram_id),
                User.deleted_at == None
            )
        ).first()
        if not user:
            logger.error(f"❌ User tidak ditemukan: {telegram_id}")
            raise HTTPException(status_code=404, detail=f"User dengan telegram_id {telegram_id} tidak ditemukan")
        
        # Hitung jumlah hari dari package_name
        days_map = {
            "VIP 1 Hari": 1,
            "VIP 3 Hari": 3,
            "VIP 7 Hari": 7,
            "VIP 30 Hari": 30,
            "VIP 180 Hari": 180
        }
        days = days_map.get(str(package_name), 1)
        
        # Cek apakah user sudah VIP sebelum aktivasi (untuk notifikasi berbeda)
        was_already_vip = user.is_vip
        
        # Aktifkan VIP
        user.is_vip = True  # type: ignore
        current_expiry_col = user.vip_expires_at
        current_expiry = cast(datetime | None, current_expiry_col)
        
        if current_expiry is not None and current_expiry > now_utc():
            # Jika VIP sudah aktif, extend expiry
            user.vip_expires_at = current_expiry + timedelta(days=days)  # type: ignore
        else:
            # Jika VIP belum aktif, set expiry baru
            user.vip_expires_at = now_utc() + timedelta(days=days)  # type: ignore
        
        # Update payment status jadi 'success' jika order_id diberikan
        # IMPORTANT: Manual VIP activation ALWAYS proceeds regardless of payment status
        # This allows admins to repair VIP status even if payment processing had errors
        payment_updated = False
        if order_id:
            # CRITICAL: Lock payment record to prevent race conditions
            payment = query_for_update(
                db.query(Payment).filter(Payment.order_id == order_id)
            ).first()
            if payment:
                # Allow manual override but log for audit trail
                if str(payment.status) == 'success':
                    logger.warning(f"⚠️ Manual override: Payment {order_id} already success, updating paid_at for audit")
                    payment.paid_at = now_utc()  # type: ignore
                    payment_updated = True
                elif str(payment.status) in ['pending', 'qris_pending', 'failed']:
                    payment.status = 'success'  # type: ignore
                    payment.paid_at = now_utc()  # type: ignore
                    payment_updated = True
                    logger.info(f"✅ Payment {order_id} status updated to 'success'")
                else:
                    # Unknown status - still allow but log warning
                    logger.warning(f"⚠️ Manual override: Payment {order_id} has status '{payment.status}', updating anyway")
                    payment.status = 'success'  # type: ignore
                    payment.paid_at = now_utc()  # type: ignore
                    payment_updated = True
            else:
                logger.warning(f"⚠️ Payment with order_id {order_id} not found")
        
        db.commit()
        
        logger.info(f"✅ VIP manual diaktifkan untuk user {telegram_id} selama {days} hari (paket: {package_name})")
        
        telegram_id_int = int(str(telegram_id))
        if was_already_vip:
            message = (
                f"✅ <b>VIP Ditambahkan!</b>\n\n"
                f"Admin: {admin.username}\n"
                f"Paket: {package_name}\n"
                f"Durasi VIP kamu sudah ditambahkan {days} hari!\n\n"
                f"Selamat menonton! 🎬"
            )
        else:
            message = (
                f"✅ <b>VIP Diaktifkan Manual!</b>\n\n"
                f"Admin: {admin.username}\n"
                f"Paket: {package_name}\n"
                f"Status VIP kamu sudah aktif!\n\n"
                f"Selamat menonton! 🎬"
            )
        await send_telegram_notification(telegram_id_int, message, "manual VIP")
        
        return {
            "success": True,
            "message": f"VIP berhasil diaktifkan manual untuk user {telegram_id}",
            "user": {
                "telegram_id": str(user.telegram_id),
                "is_vip": user.is_vip,
                "vip_expires_at": to_iso_utc(user.vip_expires_at),
                "vip_days": days
            },
            "payment_updated": payment_updated,
            "admin": admin.username,
            "activated_at": to_iso_utc(now_utc())
        }
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error manual VIP activation: {e}")
        logger.exception("Full error traceback:")
        raise HTTPException(status_code=500, detail=f"Error manual VIP activation: {str(e)}")
    finally:
        db.close()


# ==================== PAYMENT SYNC ENDPOINTS ====================
# Background sync worker untuk memastikan VIP otomatis aktif
# Meskipun webhook QRIS.PW gagal (misal karena cold start Render)

@router.get("/payment-sync/stats")
async def get_payment_sync_stats(admin = Depends(get_current_admin)):
    """
    Get statistics dari Payment Sync Worker.
    
    Menampilkan:
    - Status worker (running/stopped)
    - Jumlah sync yang sudah dilakukan
    - Jumlah payments yang berhasil diaktifkan via sync
    - Waktu sync terakhir
    """
    try:
        from payment_sync import get_payment_sync_worker
        
        worker = get_payment_sync_worker()
        if not worker:
            return {
                "status": "not_initialized",
                "message": "Payment Sync Worker belum diinisialisasi. Pastikan QRIS.PW credentials sudah di-set."
            }
        
        stats = worker.get_stats()
        return {
            "status": "ok",
            "worker": stats,
            "message": "Payment Sync Worker aktif dan berjalan"
        }
    except Exception as e:
        logger.error(f"Error getting payment sync stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/payment-sync/sync-single", dependencies=[Depends(require_csrf_token)])
async def sync_single_payment(
    request: Request,
    admin = Depends(get_current_admin)
):
    """
    Manual sync untuk satu payment berdasarkan transaction_id.
    
    Ini berguna untuk:
    - Memaksa re-check payment ke QRIS.PW
    - Mengaktifkan VIP jika pembayaran sudah masuk tapi webhook gagal
    
    Request body:
    {
        "transaction_id": "TRX-xxxxx"
    }
    """
    try:
        from payment_sync import get_payment_sync_worker
        
        body = await request.json()
        transaction_id = body.get("transaction_id")
        
        if not transaction_id:
            raise HTTPException(status_code=400, detail="transaction_id wajib diisi")
        
        worker = get_payment_sync_worker()
        if not worker:
            raise HTTPException(
                status_code=503, 
                detail="Payment Sync Worker belum aktif. Pastikan QRIS.PW credentials sudah di-set."
            )
        
        logger.info(f"👤 Admin {admin.username} manual sync payment: {transaction_id}")
        
        success, message = worker.sync_single_payment(transaction_id)
        
        if success:
            logger.info(f"✅ Manual sync berhasil untuk {transaction_id}: {message}")
            return {
                "success": True,
                "message": message,
                "transaction_id": transaction_id,
                "synced_by": admin.username
            }
        else:
            logger.warning(f"⚠️ Manual sync tidak berhasil untuk {transaction_id}: {message}")
            return {
                "success": False,
                "message": message,
                "transaction_id": transaction_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in sync_single_payment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/payment-sync/force-sync-all", dependencies=[Depends(require_csrf_token)])
async def force_sync_all_pending(admin = Depends(get_current_admin)):
    """
    Force sync semua pending payments dengan QRIS.PW.
    
    PERINGATAN: Ini akan mengecek SEMUA pending payments (max 50)
    ke QRIS.PW API. Gunakan dengan bijak!
    
    Returns:
    - Jumlah payments yang di-sync
    - Jumlah VIP yang diaktifkan
    """
    try:
        from payment_sync import get_payment_sync_worker
        
        worker = get_payment_sync_worker()
        if not worker:
            raise HTTPException(
                status_code=503,
                detail="Payment Sync Worker belum aktif"
            )
        
        logger.info(f"👤 Admin {admin.username} memulai force sync all pending payments")
        
        # Ambil stats sebelum sync
        stats_before = worker.get_stats()
        activated_before = stats_before.get("payments_activated", 0)
        
        # Trigger sync manually
        worker._sync_pending_payments()
        
        # Ambil stats sesudah sync
        stats_after = worker.get_stats()
        activated_after = stats_after.get("payments_activated", 0)
        
        newly_activated = activated_after - activated_before
        
        logger.info(f"✅ Force sync selesai: {newly_activated} VIP baru diaktifkan")
        
        return {
            "success": True,
            "message": f"Force sync selesai. {newly_activated} VIP berhasil diaktifkan.",
            "newly_activated": newly_activated,
            "total_activated": activated_after,
            "triggered_by": admin.username
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in force_sync_all_pending: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        
        # BUG FIX #8: Exclude soft-deleted users from analytics
        # BUG FIX #9: Use database-specific date functions to avoid SQLite CAST issues
        if period == 'monthly':
            if dialect_name == 'postgresql':
                query = db.query(
                    func.date_trunc('month', User.created_at).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(
                    User.created_at >= start_date,
                    User.deleted_at == None
                ).group_by('period').order_by('period')
            else:
                # SQLite: use strftime for monthly grouping
                query = db.query(
                    func.strftime('%Y-%m', User.created_at).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(
                    User.created_at >= start_date,
                    User.deleted_at == None
                ).group_by('period').order_by('period')
        else:
            if dialect_name == 'postgresql':
                # PostgreSQL: use CAST to Date which works correctly
                query = db.query(
                    cast(User.created_at, Date).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(
                    User.created_at >= start_date,
                    User.deleted_at == None
                ).group_by('period').order_by('period')
            else:
                # SQLite: use date() function - CAST to Date returns integer (year) in SQLite
                query = db.query(
                    func.date(User.created_at).label('period'),
                    func.count(User.id).label('total'),
                    func.sum(case((User.is_vip == True, 1), else_=0)).label('vip_count')
                ).filter(
                    User.created_at >= start_date,
                    User.deleted_at == None
                ).group_by('period').order_by('period')
        
        results = query.all()
        
        def format_period(period_value):
            """Format period value to ISO string, handling both date objects and strings"""
            if period_value is None:
                return None
            if hasattr(period_value, 'isoformat'):
                return period_value.isoformat()
            # Already a string
            return str(period_value)
        
        return {
            "period": period,
            "data": [
                {
                    "date": format_period(r.period),
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
        
        # BUG FIX #9: Use database-specific date functions to avoid SQLite CAST issues
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
                # SQLite: use strftime for monthly grouping
                query = db.query(
                    func.strftime('%Y-%m', Payment.created_at).label('period'),
                    func.sum(Payment.amount).label('revenue'),
                    func.count(Payment.id).label('transaction_count')
                ).filter(
                    Payment.status == 'success',
                    Payment.created_at >= start_date
                ).group_by('period').order_by('period')
        else:
            if dialect_name == 'postgresql':
                # PostgreSQL: use CAST to Date which works correctly
                query = db.query(
                    cast(Payment.created_at, Date).label('period'),
                    func.sum(Payment.amount).label('revenue'),
                    func.count(Payment.id).label('transaction_count')
                ).filter(
                    Payment.status == 'success',
                    Payment.created_at >= start_date
                ).group_by('period').order_by('period')
            else:
                # SQLite: use date() function - CAST to Date returns integer (year) in SQLite
                query = db.query(
                    func.date(Payment.created_at).label('period'),
                    func.sum(Payment.amount).label('revenue'),
                    func.count(Payment.id).label('transaction_count')
                ).filter(
                    Payment.status == 'success',
                    Payment.created_at >= start_date
                ).group_by('period').order_by('period')
        
        results = query.all()
        
        def format_period(period_value):
            """Format period value to ISO string, handling both date objects and strings"""
            if period_value is None:
                return None
            if hasattr(period_value, 'isoformat'):
                return period_value.isoformat()
            return str(period_value)
        
        return {
            "period": period,
            "data": [
                {
                    "date": format_period(r.period),
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
    """Get top movies by views (Movie.views + sum of Part.views)"""
    db = SessionLocal()
    try:
        from sqlalchemy.orm import aliased
        from sqlalchemy import select, literal_column
        
        part_views_subquery = db.query(
            Part.movie_id,
            func.coalesce(func.sum(Part.views), 0).label('part_views_total')
        ).filter(
            Part.deleted_at == None
        ).group_by(Part.movie_id).subquery()
        
        movies_with_total_views = db.query(
            Movie,
            (func.coalesce(Movie.views, 0) + func.coalesce(part_views_subquery.c.part_views_total, 0)).label('total_views')
        ).outerjoin(
            part_views_subquery,
            Movie.id == part_views_subquery.c.movie_id
        ).filter(
            Movie.deleted_at == None
        ).order_by(
            desc('total_views')
        ).limit(limit).all()
        
        return {
            "movies": [
                {
                    "id": m.id,
                    "title": m.title,
                    "category": m.category,
                    "views": int(total_views),
                    "is_series": m.is_series,
                    "total_parts": m.total_parts
                } for m, total_views in movies_with_total_views
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
        # BUG FIX #8: Exclude soft-deleted users from analytics
        total_users = db.query(func.count(User.id)).filter(User.deleted_at == None).scalar() or 0
        vip_users = db.query(func.count(User.id)).filter(
            User.is_vip == True,
            User.deleted_at == None
        ).scalar() or 0
        
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
        # BUG FIX #8: Exclude soft-deleted users from export
        users = db.query(User).filter(User.deleted_at == None).all()
        
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

@router.post("/bulk/users/delete", dependencies=[Depends(require_csrf_token)])
async def bulk_delete_users(data: BulkDeleteRequest, admin = Depends(get_current_admin)):
    """Bulk soft delete users"""
    db = SessionLocal()
    try:
        # Soft delete: update deleted_at instead of hard delete
        deleted_count = db.query(User).filter(
            User.id.in_(data.ids),
            User.deleted_at == None  # Only delete non-deleted users
        ).update({"deleted_at": now_utc()}, synchronize_session=False)
        db.commit()
        
        logger.info(f"Bulk soft-deleted {deleted_count} users by admin {admin.username}")
        return {"message": f"{deleted_count} users berhasil dihapus", "count": deleted_count}
    except Exception as e:
        logger.error(f"Error bulk deleting users: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.post("/bulk/movies/delete", dependencies=[Depends(require_csrf_token)])
async def bulk_delete_movies(data: BulkDeleteRequest, admin = Depends(get_current_admin)):
    """Bulk soft delete movies - BUG FIX #8"""
    db = SessionLocal()
    try:
        deleted_count = db.query(Movie).filter(
            Movie.id.in_(data.ids),
            Movie.deleted_at == None  # Only delete non-deleted movies
        ).update({"deleted_at": now_utc()}, synchronize_session=False)
        db.commit()
        
        logger.info(f"Bulk soft-deleted {deleted_count} movies by admin {admin.username}")
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

@router.post("/bulk/users/update-vip", dependencies=[Depends(require_csrf_token)])
async def bulk_update_vip(data: BulkUpdateVIPRequest, admin = Depends(get_current_admin)):
    """Bulk update user VIP status"""
    db = SessionLocal()
    try:
        users = db.query(User).filter(
            User.id.in_(data.user_ids),
            User.deleted_at == None
        ).all()
        
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

@router.put("/settings/{key}", dependencies=[Depends(require_csrf_token)])
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
    broadcast_type: str = 'v1'  # v1=telegram, v2=miniapp

@router.post("/broadcast", dependencies=[Depends(require_csrf_token)])
async def broadcast_message(data: BroadcastRequest, admin = Depends(get_current_admin)):
    """
    Send broadcast message to users.
    - v1: Sends via Telegram bot API to each user
    - v2: Saves to database only (mini app will fetch)
    
    Returns detailed results per user (success/failed) for v1.
    """
    if data.broadcast_type == 'v1' and not TELEGRAM_BOT_TOKEN:
        raise HTTPException(
            status_code=503, 
            detail="TELEGRAM_BOT_TOKEN tidak tersedia. Broadcast tidak dapat dikirim."
        )
    
    # For v2, just save to database and return success immediately
    if data.broadcast_type == 'v2':
        db = SessionLocal()
        try:
            broadcast = Broadcast(
                message=data.message,
                target='vip' if data.vip_only else 'all',
                is_active=True,
                broadcast_type='v2',
                created_by=admin.username
            )
            db.add(broadcast)
            db.commit()
            logger.info(f"Broadcast v2 saved to database by admin {admin.username}")
            return {
                "message": "Broadcast mini app berhasil disimpan",
                "total": 1,
                "success_count": 1,
                "failed_count": 0
            }
        except Exception as e:
            logger.error(f"Error saving v2 broadcast: {e}")
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            db.close()
    
    db = SessionLocal()
    try:
        query = db.query(User).filter(User.deleted_at == None)
        
        if data.vip_only:
            query = query.filter(User.is_vip == True)
        
        users = query.all()
        
        if not users:
            raise HTTPException(status_code=404, detail="Tidak ada user yang memenuhi kriteria")
        
        logger.info(f"Sending broadcast to {len(users)} users by admin {admin.username}")
        
        telegram_api_base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
        if not validate_external_url(telegram_api_base):
            logger.error(f"❌ SSRF protection blocked Telegram API URL")
            raise HTTPException(status_code=500, detail="Konfigurasi API tidak valid")
        
        async def send_message_to_user(user: User, client: httpx.AsyncClient):
            """Send message to a single user and return result"""
            try:
                url = f"{telegram_api_base}/sendMessage"
                payload = {
                    "chat_id": user.telegram_id,
                    "text": data.message,
                    "parse_mode": "HTML"
                }
                
                response = await client.post(url, json=payload, timeout=10.0)
                response.raise_for_status()
                
                result = response.json()
                if result.get("ok"):
                    return {
                        "success": True,
                        "telegram_id": user.telegram_id,
                        "username": user.username or "Unknown"
                    }
                else:
                    return {
                        "success": False,
                        "telegram_id": user.telegram_id,
                        "username": user.username or "Unknown",
                        "error": result.get("description", "Unknown error")
                    }
            except httpx.HTTPStatusError as e:
                error_msg = f"HTTP {e.response.status_code}"
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("description", error_msg)
                except:
                    pass
                return {
                    "success": False,
                    "telegram_id": user.telegram_id,
                    "username": user.username or "Unknown",
                    "error": error_msg
                }
            except httpx.RequestError as e:
                return {
                    "success": False,
                    "telegram_id": user.telegram_id,
                    "username": user.username or "Unknown",
                    "error": f"Network error: {str(e)}"
                }
            except Exception as e:
                return {
                    "success": False,
                    "telegram_id": user.telegram_id,
                    "username": user.username or "Unknown",
                    "error": str(e)
                }
        
        async with httpx.AsyncClient() as client:
            tasks = [send_message_to_user(user, client) for user in users]
            results = await asyncio.gather(*tasks)
        
        success_list = [r for r in results if r["success"]]
        failed_list = [r for r in results if not r["success"]]
        
        logger.info(f"Broadcast completed: {len(success_list)} success, {len(failed_list)} failed")
        
        # Save broadcast to database
        try:
            broadcast = Broadcast(
                message=data.message,
                target='vip' if data.vip_only else 'all',
                is_active=True,
                broadcast_type=data.broadcast_type,
                created_by=admin.username
            )
            db.add(broadcast)
            db.commit()
            logger.info(f"Broadcast saved to database with ID {broadcast.id}, type: {data.broadcast_type}")
        except Exception as e:
            logger.error(f"Failed to save broadcast to database: {e}")
            db.rollback()
        
        return {
            "message": f"Broadcast selesai: {len(success_list)} berhasil, {len(failed_list)} gagal",
            "total": len(users),
            "success_count": len(success_list),
            "failed_count": len(failed_list),
            "success": success_list,
            "failed": failed_list
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== BROADCAST MANAGEMENT ====================

class UpdateBroadcastRequest(BaseModel):
    message: Optional[str] = None
    is_active: Optional[bool] = None

@router.get("/broadcasts")
async def get_all_broadcasts(
    page: int = 1,
    limit: int = 20,
    admin = Depends(get_current_admin)
):
    """Get all broadcasts with pagination"""
    db = SessionLocal()
    try:
        offset = (page - 1) * limit
        
        # BUG FIX #8: Exclude soft-deleted broadcasts
        total = db.query(func.count(Broadcast.id)).filter(Broadcast.deleted_at == None).scalar()
        broadcasts = db.query(Broadcast).filter(Broadcast.deleted_at == None).order_by(
            desc(Broadcast.created_at)
        ).offset(offset).limit(limit).all()
        
        result = []
        for broadcast in broadcasts:
            result.append({
                "id": broadcast.id,
                "message": broadcast.message,
                "target": broadcast.target,
                "is_active": broadcast.is_active,
                "created_at": to_iso_utc(broadcast.created_at),
                "created_by": broadcast.created_by,
                "updated_at": to_iso_utc(broadcast.updated_at)
            })
        
        return {
            "broadcasts": result,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit
        }
    except Exception as e:
        logger.error(f"Error getting broadcasts: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.patch("/broadcasts/{broadcast_id}", dependencies=[Depends(require_csrf_token)])
async def update_broadcast(
    broadcast_id: int,
    data: UpdateBroadcastRequest,
    admin = Depends(get_current_admin)
):
    """Update broadcast message or toggle is_active"""
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted broadcasts
        broadcast = db.query(Broadcast).filter(
            Broadcast.id == broadcast_id,
            Broadcast.deleted_at == None
        ).first()
        
        if not broadcast:
            raise HTTPException(status_code=404, detail="Broadcast tidak ditemukan")
        
        if data.message is not None:
            broadcast.message = data.message
        
        if data.is_active is not None:
            broadcast.is_active = data.is_active
        
        broadcast.updated_at = now_utc()
        db.commit()
        
        return {
            "message": "Broadcast berhasil diupdate",
            "broadcast": {
                "id": broadcast.id,
                "message": broadcast.message,
                "target": broadcast.target,
                "is_active": broadcast.is_active,
                "created_at": to_iso_utc(broadcast.created_at),
                "created_by": broadcast.created_by,
                "updated_at": to_iso_utc(broadcast.updated_at)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating broadcast: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.delete("/broadcasts/{broadcast_id}", dependencies=[Depends(require_csrf_token)])
async def delete_broadcast(broadcast_id: int, admin = Depends(get_current_admin)):
    """Delete a broadcast"""
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude already soft-deleted broadcasts
        broadcast = db.query(Broadcast).filter(
            Broadcast.id == broadcast_id,
            Broadcast.deleted_at == None
        ).first()
        
        if not broadcast:
            raise HTTPException(status_code=404, detail="Broadcast tidak ditemukan")
        
        # BUG FIX #8: Soft delete instead of hard delete
        broadcast.deleted_at = now_utc()  # type: ignore
        db.commit()
        
        logger.info(f"Admin soft-deleted broadcast: {broadcast_id}")
        return {"message": "Broadcast berhasil dihapus"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting broadcast: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==================== PUBLIC BROADCAST V2 ENDPOINT (NO AUTH) ====================

# Note: This endpoint is in admin_router but is accessible publicly
# It's for mini app only, returns v2 broadcasts
@router.get("/broadcasts-v2/active")
async def get_active_broadcasts_v2():
    """Get active v2 broadcasts for mini app (no authentication required)"""
    db = SessionLocal()
    try:
        # BUG FIX #8: Exclude soft-deleted broadcasts
        broadcasts = db.query(Broadcast).filter(
            Broadcast.is_active == True,
            Broadcast.broadcast_type == 'v2',
            Broadcast.deleted_at == None
        ).order_by(desc(Broadcast.created_at)).all()
        
        result = []
        for broadcast in broadcasts:
            result.append({
                "id": broadcast.id,
                "message": broadcast.message,
                "target": broadcast.target,
                "created_at": to_iso_utc(broadcast.created_at)
            })
        
        return {"broadcasts": result}
    except Exception as e:
        logger.error(f"Error getting active v2 broadcasts: {e}")
        return {"broadcasts": []}
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
                if not validate_external_url(file_id):
                    logger.error(f"❌ SSRF protection blocked URL: {file_id[:50]}...")
                    raise HTTPException(status_code=400, detail="URL tidak diizinkan")
                
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
            if not validate_external_url(get_file_url):
                logger.error(f"❌ SSRF protection blocked Telegram API URL")
                raise HTTPException(status_code=500, detail="Konfigurasi API tidak valid")
            
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
            if not validate_external_url(download_url):
                logger.error(f"❌ SSRF protection blocked Telegram download URL")
                raise HTTPException(status_code=500, detail="URL tidak valid")
            
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

# ==================== PAYMENT GATEWAY CONFIGURATION ====================

class PaymentConfigUpdate(BaseModel):
    active_gateway: str
    gateways: Dict[str, Any]
    qris_status: Optional[Dict[str, bool]] = None

@router.get("/payment-config")
async def get_payment_config(admin = Depends(get_current_admin)):
    """Get current payment gateway configuration from Settings table"""
    db = SessionLocal()
    try:
        import json
        
        config_setting = db.query(Settings).filter(Settings.key == 'payment_config').first()
        
        if config_setting and config_setting.value:
            try:
                config = json.loads(config_setting.value)
            except json.JSONDecodeError:
                config = get_default_payment_config()
        else:
            config = get_default_payment_config()
        
        return {"config": config}
    except Exception as e:
        logger.error(f"Error getting payment config: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

def get_default_payment_config():
    """Return default payment gateway configuration"""
    return {
        "active_gateway": "qrispw",
        "gateways": {
            "qrispw": {
                "enabled": True,
                "api_key": "",
                "api_secret": "",
                "api_url": "https://qris.pw/api"
            },
            "qris-interactive": {
                "enabled": False,
                "amounts": []
            },
            "doku": {
                "enabled": False,
                "client_id": "",
                "secret_key": "",
                "environment": "sandbox"
            },
            "midtrans": {
                "enabled": False,
                "server_key": "",
                "client_key": "",
                "merchant_id": "",
                "environment": "sandbox"
            }
        }
    }

@router.put("/payment-config", dependencies=[Depends(require_csrf_token)])
async def update_payment_config(data: PaymentConfigUpdate, admin = Depends(get_current_admin)):
    """Update payment gateway configuration"""
    db = SessionLocal()
    try:
        import json
        
        config_data = {
            "active_gateway": data.active_gateway,
            "gateways": data.gateways
        }
        
        if data.qris_status is not None:
            config_data["qris_status"] = data.qris_status
        
        config_setting = db.query(Settings).filter(Settings.key == 'payment_config').first()
        
        if config_setting:
            existing_config = {}
            try:
                existing_config = json.loads(config_setting.value) if config_setting.value else {}
            except json.JSONDecodeError:
                pass
            
            if data.qris_status is None and "qris_status" in existing_config:
                config_data["qris_status"] = existing_config["qris_status"]
            
            config_setting.value = json.dumps(config_data)
            config_setting.updated_at = now_utc()
            config_setting.updated_by = admin.username
        else:
            new_setting = Settings(
                key='payment_config',
                value=json.dumps(config_data),
                description='Payment gateway configuration',
                updated_by=admin.username
            )
            db.add(new_setting)
        
        db.commit()
        
        logger.info(f"Admin {admin.username} updated payment config: active_gateway={data.active_gateway}")
        
        return {"message": "Konfigurasi payment berhasil disimpan", "config": config_data}
    except Exception as e:
        logger.error(f"Error updating payment config: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@router.get("/payment-env-status")
async def get_payment_env_status(admin = Depends(get_current_admin)):
    """Get status of payment credentials from env vars AND database"""
    import os
    import json
    
    db = SessionLocal()
    try:
        qrispw_env_key = os.getenv('QRIS_PW_API_KEY', '')
        qrispw_env_secret = os.getenv('QRIS_PW_API_SECRET', '')
        
        doku_env_client_id = os.getenv('DOKU_CLIENT_ID', '')
        doku_env_secret_key = os.getenv('DOKU_SECRET_KEY', '')
        
        midtrans_env_server_key = os.getenv('MIDTRANS_SERVER_KEY', '')
        midtrans_env_client_key = os.getenv('MIDTRANS_CLIENT_KEY', '')
        
        qrispw_db_key = ''
        qrispw_db_secret = ''
        doku_db_client_id = ''
        doku_db_secret_key = ''
        midtrans_db_server_key = ''
        midtrans_db_client_key = ''
        
        config_setting = db.query(Settings).filter(Settings.key == 'payment_config').first()
        if config_setting and config_setting.value:
            try:
                config = json.loads(config_setting.value)
                gateways = config.get('gateways', {})
                
                qrispw_config = gateways.get('qrispw', {})
                qrispw_db_key = qrispw_config.get('api_key', '')
                qrispw_db_secret = qrispw_config.get('api_secret', '')
                
                doku_config = gateways.get('doku', {})
                doku_db_client_id = doku_config.get('client_id', '')
                doku_db_secret_key = doku_config.get('secret_key', '')
                
                midtrans_config = gateways.get('midtrans', {})
                midtrans_db_server_key = midtrans_config.get('server_key', '')
                midtrans_db_client_key = midtrans_config.get('client_key', '')
            except json.JSONDecodeError:
                pass
        
        return {
            "qrispw": {
                "configured": bool((qrispw_env_key and qrispw_env_secret) or (qrispw_db_key and qrispw_db_secret)),
                "env_configured": bool(qrispw_env_key and qrispw_env_secret),
                "db_configured": bool(qrispw_db_key and qrispw_db_secret),
                "source": "env" if (qrispw_env_key and qrispw_env_secret) else ("db" if (qrispw_db_key and qrispw_db_secret) else "none"),
                "api_key_set": bool(qrispw_env_key or qrispw_db_key),
                "api_secret_set": bool(qrispw_env_secret or qrispw_db_secret)
            },
            "doku": {
                "configured": bool((doku_env_client_id and doku_env_secret_key) or (doku_db_client_id and doku_db_secret_key)),
                "env_configured": bool(doku_env_client_id and doku_env_secret_key),
                "db_configured": bool(doku_db_client_id and doku_db_secret_key),
                "source": "env" if (doku_env_client_id and doku_env_secret_key) else ("db" if (doku_db_client_id and doku_db_secret_key) else "none"),
                "client_id_set": bool(doku_env_client_id or doku_db_client_id),
                "secret_key_set": bool(doku_env_secret_key or doku_db_secret_key)
            },
            "midtrans": {
                "configured": bool((midtrans_env_server_key and midtrans_env_client_key) or (midtrans_db_server_key and midtrans_db_client_key)),
                "env_configured": bool(midtrans_env_server_key and midtrans_env_client_key),
                "db_configured": bool(midtrans_db_server_key and midtrans_db_client_key),
                "source": "env" if (midtrans_env_server_key and midtrans_env_client_key) else ("db" if (midtrans_db_server_key and midtrans_db_client_key) else "none"),
                "server_key_set": bool(midtrans_env_server_key or midtrans_db_server_key),
                "client_key_set": bool(midtrans_env_client_key or midtrans_db_client_key)
            }
        }
    finally:
        db.close()

@router.get("/qris-images")
async def get_qris_images(admin = Depends(get_current_admin)):
    """Get list of available QRIS images for manual payment"""
    import os
    import re
    
    qris_dir = "frontend/assets/qris"
    images = []
    
    if os.path.exists(qris_dir):
        for filename in os.listdir(qris_dir):
            if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                match = re.match(r'^(\d+)\.(png|jpg|jpeg)$', filename.lower())
                if match:
                    amount = int(match.group(1))
                    images.append({
                        "amount": amount,
                        "filename": filename,
                        "url": f"/qris/{filename}"
                    })
    
    images.sort(key=lambda x: x['amount'])
    
    return {"images": images}

@router.post("/qris-images/upload", dependencies=[Depends(require_csrf_token)])
async def upload_qris_image(
    amount: int = Form(...),
    image: UploadFile = File(...),
    admin = Depends(get_current_admin)
):
    """Upload a new QRIS image for a specific amount with secure validation"""
    import os
    import shutil
    from file_validation import validate_file_extension, validate_mime_type, MAX_FILE_SIZE, CHUNK_SIZE
    
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal harus lebih dari 0")
    
    if not image.filename:
        raise HTTPException(status_code=400, detail="Nama file tidak ada")
    
    valid_ext, ext_error = validate_file_extension(image.filename)
    if not valid_ext:
        raise HTTPException(status_code=400, detail=ext_error)
    
    valid_mime, mime_error = validate_mime_type(image.content_type)
    if not valid_mime:
        raise HTTPException(status_code=400, detail=mime_error)
    
    file_size = 0
    chunks = []
    try:
        while True:
            chunk = await image.read(CHUNK_SIZE)
            if not chunk:
                break
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=400, 
                    detail=f"File terlalu besar. Maksimal {MAX_FILE_SIZE / (1024 * 1024):.0f} MB"
                )
            chunks.append(chunk)
        await image.seek(0)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading uploaded file: {e}")
        raise HTTPException(status_code=400, detail="Gagal membaca file")
    
    if file_size == 0:
        raise HTTPException(status_code=400, detail="File kosong tidak diperbolehkan")
    
    if file_size < 100:
        raise HTTPException(status_code=400, detail="File terlalu kecil, kemungkinan corrupt")
    
    qris_dir = "frontend/assets/qris"
    admin_qris_dir = "admin/assets/qris"
    os.makedirs(qris_dir, exist_ok=True)
    os.makedirs(admin_qris_dir, exist_ok=True)
    
    file_ext = os.path.splitext(image.filename)[1].lower()
    filename = f"{amount}{file_ext}"
    filepath = os.path.join(qris_dir, filename)
    admin_filepath = os.path.join(admin_qris_dir, filename)
    
    for ext in ['.png', '.jpg', '.jpeg']:
        if ext != file_ext:
            old_file = os.path.join(qris_dir, f"{amount}{ext}")
            old_admin_file = os.path.join(admin_qris_dir, f"{amount}{ext}")
            if os.path.exists(old_file):
                os.remove(old_file)
            if os.path.exists(old_admin_file):
                os.remove(old_admin_file)
    
    try:
        with open(filepath, "wb") as buffer:
            for chunk in chunks:
                buffer.write(chunk)
        
        shutil.copy2(filepath, admin_filepath)
        
        logger.info(f"Admin {admin.username} uploaded QRIS image for amount {amount} ({file_size/1024:.1f} KB)")
        
        return {
            "message": f"Gambar QRIS untuk nominal {amount} berhasil diupload",
            "filename": filename,
            "url": f"/qris/{filename}",
            "size_kb": round(file_size / 1024, 1)
        }
    except Exception as e:
        logger.error(f"Error uploading QRIS image: {e}")
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass
        raise HTTPException(status_code=500, detail=f"Gagal menyimpan file: {str(e)}")

@router.delete("/qris-images/{amount}", dependencies=[Depends(require_csrf_token)])
async def delete_qris_image(amount: int, admin = Depends(get_current_admin)):
    """Delete a QRIS image for a specific amount from all directories"""
    import os
    
    qris_dir = "frontend/assets/qris"
    admin_qris_dir = "admin/assets/qris"
    deleted = False
    
    for ext in ['.png', '.jpg', '.jpeg']:
        filepath = os.path.join(qris_dir, f"{amount}{ext}")
        admin_filepath = os.path.join(admin_qris_dir, f"{amount}{ext}")
        
        if os.path.exists(filepath):
            os.remove(filepath)
            deleted = True
            logger.info(f"Admin {admin.username} deleted QRIS image for amount {amount}")
        
        if os.path.exists(admin_filepath):
            os.remove(admin_filepath)
    
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Gambar QRIS untuk nominal {amount} tidak ditemukan")
    
    return {"message": f"Gambar QRIS untuk nominal {amount} berhasil dihapus"}
