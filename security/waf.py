"""
Web Application Firewall (WAF) Module

Implements application-level WAF for protection against:
- SQL Injection
- Cross-Site Scripting (XSS)
- Path Traversal
- Command Injection
- Malicious payloads

Uses pattern matching and heuristics to detect and block attacks.
"""

import re
import logging
from typing import Optional, Tuple, List
from urllib.parse import unquote, unquote_plus
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import SecurityConfig, WAFConfig
from .audit_logger import log_security_event

import ipaddress

logger = logging.getLogger(__name__)


class WAFEngine:
    """
    Core WAF engine for attack detection.
    
    Features:
    - Pattern-based detection for common attacks
    - URL decode handling (multiple layers)
    - Request body scanning
    - Configurable patterns
    """
    
    def __init__(self, config: Optional[WAFConfig] = None):
        self.config = config or WAFConfig()
        
        self._sql_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.sql_injection_patterns
        ]
        
        self._xss_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.xss_patterns
        ]
        
        self._path_traversal_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.path_traversal_patterns
        ]
        
        self._command_injection_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in self.config.command_injection_patterns
        ]
    
    def _decode_input(self, value: Optional[str]) -> List[str]:
        """
        Decode input multiple times to handle double/triple encoding.
        Returns list of all decoded versions for scanning.
        """
        if value is None or not isinstance(value, str):
            return []
        
        decoded_versions = [value]
        
        try:
            decoded1 = unquote(value)
            if decoded1 != value:
                decoded_versions.append(decoded1)
            
            decoded2 = unquote_plus(value)
            if decoded2 not in decoded_versions:
                decoded_versions.append(decoded2)
            
            decoded3 = unquote(decoded1)
            if decoded3 not in decoded_versions:
                decoded_versions.append(decoded3)
        except Exception:
            pass
        
        return decoded_versions
    
    def _check_patterns(
        self,
        value: str,
        patterns: List[re.Pattern],
        attack_type: str
    ) -> Optional[Tuple[str, str]]:
        """
        Check value against patterns.
        
        Returns:
            Tuple of (attack_type, matched_pattern) if attack detected, None otherwise
        """
        for decoded in self._decode_input(value):
            for pattern in patterns:
                match = pattern.search(decoded)
                if match:
                    return (attack_type, match.group())
        
        return None
    
    def check_sql_injection(self, value: str) -> Optional[Tuple[str, str]]:
        """Check for SQL injection patterns"""
        if not self.config.block_sql_injection:
            return None
        return self._check_patterns(value, self._sql_patterns, "sql_injection")
    
    def check_xss(self, value: str) -> Optional[Tuple[str, str]]:
        """Check for XSS patterns"""
        if not self.config.block_xss:
            return None
        return self._check_patterns(value, self._xss_patterns, "xss")
    
    def check_path_traversal(self, value: str) -> Optional[Tuple[str, str]]:
        """Check for path traversal patterns"""
        if not self.config.block_path_traversal:
            return None
        return self._check_patterns(value, self._path_traversal_patterns, "path_traversal")
    
    def check_command_injection(self, value: str) -> Optional[Tuple[str, str]]:
        """Check for command injection patterns"""
        if not self.config.block_command_injection:
            return None
        return self._check_patterns(value, self._command_injection_patterns, "command_injection")
    
    def scan_value(self, value: str) -> Optional[Tuple[str, str]]:
        """
        Scan a single value for all attack types.
        
        Returns:
            Tuple of (attack_type, matched_pattern) if attack detected, None otherwise
        """
        if not value or not isinstance(value, str):
            return None
        
        if len(value) > 10000:
            return None
        
        result = self.check_sql_injection(value)
        if result:
            return result
        
        result = self.check_xss(value)
        if result:
            return result
        
        result = self.check_path_traversal(value)
        if result:
            return result
        
        result = self.check_command_injection(value)
        if result:
            return result
        
        return None
    
    def scan_request(
        self,
        path: str,
        query_string: str,
        headers: dict,
        body: str = None
    ) -> Optional[Tuple[str, str, str]]:
        """
        Scan entire request for attacks.
        
        Returns:
            Tuple of (attack_type, matched_pattern, location) if attack detected, None otherwise
        """
        result = self.scan_value(path)
        if result:
            return (result[0], result[1], "path")
        
        if query_string:
            result = self.scan_value(query_string)
            if result:
                return (result[0], result[1], "query")
        
        skip_headers = {
            'cookie', 'user-agent', 'referer', 'origin', 
            'sec-ch-ua', 'sec-ch-ua-mobile', 'sec-ch-ua-platform',
            'sec-fetch-dest', 'sec-fetch-mode', 'sec-fetch-site', 'sec-fetch-user',
            'accept', 'accept-language', 'accept-encoding',
            'connection', 'host', 'cache-control', 'pragma',
            'upgrade-insecure-requests', 'dnt', 'te'
        }
        for header_name, header_value in headers.items():
            if header_name.lower() in skip_headers:
                continue
            
            result = self.scan_value(str(header_value))
            if result:
                return (result[0], result[1], f"header:{header_name}")
        
        if body:
            result = self.scan_value(body)
            if result:
                return (result[0], result[1], "body")
        
        return None


class WAFMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for WAF protection.
    
    Scans all incoming requests for attack patterns and blocks malicious requests.
    """
    
    def __init__(self, app, config: Optional[SecurityConfig] = None):
        super().__init__(app)
        self.config = config or SecurityConfig()
        self.waf = WAFEngine(self.config.waf)
        
        self.skip_paths = {"/health", "/favicon.ico", "/robots.txt", "/api", "/", "/panel", "/drama.html", "/home.html", "/index.html", "/payment.html", "/profil.html", "/favorit.html", "/kategori.html", "/request.html", "/referal.html", "/contact.html", "/test.html"}
        
        self.skip_path_prefixes = [
            "/static/", "/media/", "/qris/", "/assets/",
            "/api/",
            "/admin/",
            "/panel/",
            "/webhook/",
            "/frontend/",
            "/backend_assets/",
            "/posters/",
        ]
        
        self.skip_extensions = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".svg", ".webp", ".json", ".mp4", ".webm"}
        
        self.skip_content_types = {"image/", "audio/", "video/", "application/octet-stream", "multipart/form-data", "application/json", "text/html", "text/css", "text/javascript", "application/javascript"}
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        if request.client:
            return request.client.host
        
        return "unknown"
    
    async def dispatch(self, request: Request, call_next):
        if not self.config.waf.enabled:
            return await call_next(request)
        
        path = request.url.path
        path_lower = path.lower()
        
        if path in self.skip_paths:
            return await call_next(request)
        
        if any(path.startswith(skip) for skip in self.skip_path_prefixes):
            return await call_next(request)
        
        if any(path_lower.endswith(ext) for ext in self.skip_extensions):
            return await call_next(request)
        
        if path.startswith("/api/") or path.startswith("/admin/") or path.startswith("/webhook/"):
            return await call_next(request)
        
        content_type = request.headers.get("content-type", "")
        if any(ct in content_type for ct in self.skip_content_types):
            return await call_next(request)
        
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.config.waf.max_request_size:
            return self._blocked_response("Request too large", "size_limit")
        
        if len(str(request.url)) > self.config.waf.max_url_length:
            return self._blocked_response("URL too long", "url_limit")
        
        headers = dict(request.headers)
        
        attack = self.waf.scan_request(
            path=path,
            query_string=str(request.url.query),
            headers=headers,
            body=""
        )
        
        if attack:
            attack_type, pattern, location = attack
            client_ip = self._get_client_ip(request)
            
            if self.config.waf.log_blocked_requests:
                logger.warning(
                    f"WAF blocked request: type={attack_type}, "
                    f"pattern='{pattern[:50]}', location={location}, "
                    f"ip={client_ip}, path={path}"
                )
                
                log_security_event(
                    event_type="waf_block",
                    severity="warning",
                    details={
                        "attack_type": attack_type,
                        "pattern": pattern[:100],
                        "location": location,
                        "path": path,
                        "method": request.method
                    },
                    ip_address=client_ip,
                    user_agent=request.headers.get("user-agent", "unknown")
                )
            
            return self._blocked_response(
                f"Request blocked: {attack_type}",
                attack_type
            )
        
        return await call_next(request)
    
    def _blocked_response(self, message: str, reason: str) -> JSONResponse:
        """Generate blocked request response"""
        return JSONResponse(
            status_code=403,
            content={
                "error": "request_blocked",
                "message": "Permintaan ditolak karena alasan keamanan.",
                "reason": reason
            }
        )
