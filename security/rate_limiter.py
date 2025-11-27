"""
Rate Limiter Module

Implements multi-level rate limiting for DDoS protection:
- Global rate limit (per IP)
- API rate limit (per user)
- Auth rate limit (per IP) - Stricter for auth endpoints
- Payment rate limit (per user)
- Admin rate limit (per IP)

Uses in-memory storage with automatic cleanup.
For production with multiple workers, consider Redis-backed storage.
"""

import time
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import SecurityConfig, RateLimitConfig

logger = logging.getLogger(__name__)


@dataclass
class RateLimitEntry:
    """Entry for tracking rate limit"""
    requests: int = 0
    window_start: float = field(default_factory=time.time)
    blocked_until: float = 0


class RateLimiter:
    """
    Thread-safe rate limiter with sliding window algorithm.
    
    Features:
    - Multiple rate limit levels
    - Automatic cleanup of expired entries
    - Configurable limits and block durations
    """
    
    def __init__(self, config: RateLimitConfig = None):
        self.config = config or RateLimitConfig()
        
        self._global_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._api_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._auth_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._payment_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        self._admin_limits: Dict[str, RateLimitEntry] = defaultdict(RateLimitEntry)
        
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
        self._cleanup_interval = 300
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, handling proxies"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        
        cf_ip = request.headers.get("cf-connecting-ip")
        if cf_ip:
            return cf_ip.strip()
        
        if request.client:
            return request.client.host
        
        return "unknown"
    
    def _check_limit(
        self,
        key: str,
        limits: Dict[str, RateLimitEntry],
        max_requests: int,
        window_seconds: int,
        block_duration: int
    ) -> Tuple[bool, int, int]:
        """
        Check if request should be rate limited.
        
        Returns:
            Tuple of (is_allowed, remaining_requests, retry_after_seconds)
        """
        now = time.time()
        entry = limits[key]
        
        if entry.blocked_until > now:
            retry_after = int(entry.blocked_until - now)
            return False, 0, retry_after
        
        if now - entry.window_start >= window_seconds:
            entry.requests = 0
            entry.window_start = now
        
        entry.requests += 1
        
        if entry.requests > max_requests:
            entry.blocked_until = now + block_duration
            logger.warning(
                f"Rate limit exceeded for {key}: {entry.requests}/{max_requests} requests. "
                f"Blocked for {block_duration}s"
            )
            return False, 0, block_duration
        
        remaining = max_requests - entry.requests
        return True, remaining, 0
    
    async def check_global_limit(self, request: Request) -> Tuple[bool, int, int]:
        """Check global rate limit (per IP)"""
        ip = self._get_client_ip(request)
        
        async with self._lock:
            await self._maybe_cleanup()
            return self._check_limit(
                ip,
                self._global_limits,
                self.config.global_requests_per_minute,
                60,
                self.config.global_block_duration
            )
    
    async def check_api_limit(self, request: Request, user_id: str = None) -> Tuple[bool, int, int]:
        """Check API rate limit (per user or IP if no user)"""
        key = user_id or self._get_client_ip(request)
        
        async with self._lock:
            return self._check_limit(
                f"api:{key}",
                self._api_limits,
                self.config.api_requests_per_minute,
                60,
                self.config.api_block_duration
            )
    
    async def check_auth_limit(self, request: Request) -> Tuple[bool, int, int]:
        """Check auth rate limit (per IP) - Stricter for login/auth endpoints"""
        ip = self._get_client_ip(request)
        
        async with self._lock:
            return self._check_limit(
                f"auth:{ip}",
                self._auth_limits,
                self.config.auth_requests_per_minute,
                60,
                self.config.auth_block_duration
            )
    
    async def check_payment_limit(self, request: Request, user_id: str) -> Tuple[bool, int, int]:
        """Check payment rate limit (per user)"""
        async with self._lock:
            return self._check_limit(
                f"payment:{user_id}",
                self._payment_limits,
                self.config.payment_requests_per_minute,
                60,
                self.config.payment_block_duration
            )
    
    async def check_admin_limit(self, request: Request) -> Tuple[bool, int, int]:
        """Check admin rate limit (per IP)"""
        ip = self._get_client_ip(request)
        
        async with self._lock:
            return self._check_limit(
                f"admin:{ip}",
                self._admin_limits,
                self.config.admin_requests_per_minute,
                60,
                self.config.admin_block_duration
            )
    
    async def _maybe_cleanup(self):
        """Cleanup expired entries periodically"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        
        self._last_cleanup = now
        cutoff = now - 3600
        
        for limits in [
            self._global_limits,
            self._api_limits,
            self._auth_limits,
            self._payment_limits,
            self._admin_limits
        ]:
            expired = [
                key for key, entry in limits.items()
                if entry.window_start < cutoff and entry.blocked_until < now
            ]
            for key in expired:
                del limits[key]
        
        logger.debug(f"Rate limiter cleanup completed")
    
    def reset_limit(self, key: str, limit_type: str = "global"):
        """Reset rate limit for a specific key"""
        limits = {
            "global": self._global_limits,
            "api": self._api_limits,
            "auth": self._auth_limits,
            "payment": self._payment_limits,
            "admin": self._admin_limits
        }.get(limit_type, self._global_limits)
        
        if key in limits:
            del limits[key]
            logger.info(f"Rate limit reset for {limit_type}:{key}")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting.
    
    Automatically applies rate limits based on endpoint patterns:
    - /admin/* - Admin rate limit
    - /api/auth/* or /admin/login - Auth rate limit
    - /api/payment/* - Payment rate limit
    - /api/* - API rate limit
    - Everything else - Global rate limit
    """
    
    def __init__(self, app, config: SecurityConfig = None):
        super().__init__(app)
        self.config = config or SecurityConfig()
        self.rate_limiter = RateLimiter(self.config.rate_limit)
        
        self.skip_paths = {"/health", "/favicon.ico", "/robots.txt", "/", "/panel", "/drama.html", "/home.html", "/index.html", "/payment.html", "/profil.html", "/favorit.html", "/kategori.html", "/request.html", "/referal.html", "/contact.html", "/test.html"}
        
        self.skip_path_prefixes = ["/static", "/media/", "/qris/", "/assets/", "/panel/", "/frontend/", "/backend_assets/", "/posters/"]
        
        self.skip_extensions = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".svg", ".webp", ".json", ".mp4", ".webm"}
    
    async def dispatch(self, request: Request, call_next):
        if not self.config.rate_limit.enabled:
            return await call_next(request)
        
        path = request.url.path
        path_lower = path.lower()
        
        if path in self.skip_paths or any(path.startswith(p) for p in self.skip_path_prefixes):
            return await call_next(request)
        
        if any(path_lower.endswith(ext) for ext in self.skip_extensions):
            return await call_next(request)
        
        allowed, remaining, retry_after = await self.rate_limiter.check_global_limit(request)
        if not allowed:
            return self._rate_limit_response(retry_after, "global")
        
        if path.startswith("/admin/login") or "/auth/" in path:
            allowed, remaining, retry_after = await self.rate_limiter.check_auth_limit(request)
            if not allowed:
                return self._rate_limit_response(retry_after, "auth")
        
        elif path.startswith("/admin"):
            allowed, remaining, retry_after = await self.rate_limiter.check_admin_limit(request)
            if not allowed:
                return self._rate_limit_response(retry_after, "admin")
        
        elif "/payment" in path:
            allowed, remaining, retry_after = await self.rate_limiter.check_api_limit(request)
            if not allowed:
                return self._rate_limit_response(retry_after, "payment")
        
        elif path.startswith("/api"):
            allowed, remaining, retry_after = await self.rate_limiter.check_api_limit(request)
            if not allowed:
                return self._rate_limit_response(retry_after, "api")
        
        response = await call_next(request)
        
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        
        return response
    
    def _rate_limit_response(self, retry_after: int, limit_type: str) -> JSONResponse:
        """Generate rate limit exceeded response"""
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_exceeded",
                "message": f"Terlalu banyak permintaan. Coba lagi dalam {retry_after} detik.",
                "retry_after": retry_after,
                "limit_type": limit_type
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Remaining": "0"
            }
        )
