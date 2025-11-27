"""
CSRF Protection Module for Admin API

Implements CSRF token generation, validation, and FastAPI dependency for protecting
state-changing admin endpoints against Cross-Site Request Forgery attacks.

Security Model:
- Generate unique CSRF token per admin session
- Store token in AdminSession table (server-side)
- Expose via /admin/csrf endpoint for frontend to fetch
- Require X-CSRF-Token header on all POST/PUT/DELETE/PATCH requests
- Validate token matches session token in database

Usage:
    from csrf_protection import require_csrf_token
    
    @app.post("/admin/payments/{id}/approve", dependencies=[Depends(require_csrf_token)])
    async def approve_payment(...):
        ...
"""

import secrets
import logging
from typing import Optional
from fastapi import Header, HTTPException, Request, Depends
from database import AdminSession, SessionLocal
from config import now_utc

logger = logging.getLogger(__name__)


def generate_csrf_token() -> str:
    """
    Generate cryptographically secure CSRF token.
    
    Returns:
        64-character URL-safe token
    """
    return secrets.token_urlsafe(48)


def get_csrf_token_for_session(session_token: str) -> Optional[str]:
    """
    Get CSRF token for given admin session.
    
    Args:
        session_token: Admin session token from cookie
    
    Returns:
        CSRF token if session exists and valid, None otherwise
    """
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token,
            AdminSession.expires_at > now_utc()
        ).first()
        
        if not session:
            return None
        
        # Generate new CSRF token if not exists
        if not session.csrf_token:  # type: ignore
            csrf_token = generate_csrf_token()
            session.csrf_token = csrf_token  # type: ignore
            db.commit()
            db.refresh(session)
            logger.info(f"Generated new CSRF token for session {session_token[:8]}...")
            return csrf_token
        
        return str(session.csrf_token)  # type: ignore
    
    except Exception as e:
        logger.error(f"Error getting CSRF token: {e}")
        return None
    finally:
        db.close()


def validate_csrf_token(session_token: str, csrf_token: str) -> bool:
    """
    Validate CSRF token against session.
    
    Args:
        session_token: Admin session token from cookie
        csrf_token: CSRF token from request header
    
    Returns:
        True if valid, False otherwise
    """
    if not session_token or not csrf_token:
        return False
    
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token,
            AdminSession.expires_at > now_utc()
        ).first()
        
        if not session:
            logger.warning("CSRF validation failed: session not found or expired")
            return False
        
        stored_csrf = session.csrf_token  # type: ignore
        if not stored_csrf:
            logger.warning("CSRF validation failed: no CSRF token in session")
            return False
        
        # Constant-time comparison to prevent timing attacks
        is_valid = secrets.compare_digest(str(stored_csrf), csrf_token)
        
        if not is_valid:
            logger.warning(f"CSRF validation failed: token mismatch for session {session_token[:8]}...")
        
        return is_valid
    
    except Exception as e:
        logger.error(f"Error validating CSRF token: {e}")
        return False
    finally:
        db.close()


async def require_csrf_token(
    request: Request,
    x_csrf_token: Optional[str] = Header(None, alias="X-CSRF-Token")
) -> None:
    """
    FastAPI dependency to require and validate CSRF token.
    
    Use this as dependency on all state-changing endpoints (POST/PUT/DELETE/PATCH).
    
    Args:
        request: FastAPI request object (to access cookies)
        x_csrf_token: CSRF token from X-CSRF-Token header
    
    Raises:
        HTTPException: 403 if CSRF token missing or invalid
    
    Example:
        @app.post("/admin/users", dependencies=[Depends(require_csrf_token)])
        async def create_user(...):
            ...
    """
    # Get session token from cookie
    session_token = request.cookies.get("admin_session")
    
    if not session_token:
        logger.warning("CSRF check failed: no admin session cookie")
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )
    
    if not x_csrf_token:
        logger.warning("CSRF check failed: X-CSRF-Token header missing")
        raise HTTPException(
            status_code=403,
            detail="CSRF token required. Include X-CSRF-Token header."
        )
    
    if not validate_csrf_token(session_token, x_csrf_token):
        logger.warning("CSRF check failed: invalid token")
        raise HTTPException(
            status_code=403,
            detail="Invalid CSRF token"
        )
    
    # CSRF validation passed
    logger.debug("CSRF validation passed")


def update_session_csrf_token(session_token: str, csrf_token: str) -> bool:
    """
    Update CSRF token for existing session.
    
    Called when creating new admin session to set initial CSRF token.
    
    Args:
        session_token: Admin session token
        csrf_token: CSRF token to store
    
    Returns:
        True if successful, False otherwise
    """
    db = SessionLocal()
    try:
        session = db.query(AdminSession).filter(
            AdminSession.session_token == session_token
        ).first()
        
        if not session:
            return False
        
        session.csrf_token = csrf_token  # type: ignore
        db.commit()
        return True
    
    except Exception as e:
        logger.error(f"Error updating session CSRF token: {e}")
        db.rollback()
        return False
    finally:
        db.close()
