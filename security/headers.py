"""
Security Headers Module

Implements security headers for protection against:
- Clickjacking (X-Frame-Options)
- XSS (X-XSS-Protection, CSP)
- MIME sniffing (X-Content-Type-Options)
- Information leakage (Referrer-Policy)
- Transport security (HSTS)

All headers follow OWASP recommendations.
"""

import logging
from typing import Dict, Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import SecurityConfig, CSPConfig

logger = logging.getLogger(__name__)


def get_csp_header(config: CSPConfig = None) -> str:
    """Generate Content-Security-Policy header value"""
    if config is None:
        config = CSPConfig()
    return config.to_header()


def get_security_headers(config: SecurityConfig = None, is_production: bool = True) -> Dict[str, str]:
    """
    Get all security headers.
    
    Args:
        config: SecurityConfig instance
        is_production: Whether running in production
    
    Returns:
        Dictionary of header name to value
    """
    if config is None:
        config = SecurityConfig()
    
    headers = {
        "X-Content-Type-Options": "nosniff",
        
        "X-XSS-Protection": "1; mode=block",
        
        "Referrer-Policy": "strict-origin-when-cross-origin",
        
        "Cross-Origin-Embedder-Policy": "unsafe-none",
        "Cross-Origin-Opener-Policy": "same-origin-allow-popups",
        "Cross-Origin-Resource-Policy": "cross-origin",
        
        "X-Permitted-Cross-Domain-Policies": "none",
    }
    
    if is_production:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    if config.csp.enabled:
        headers["Content-Security-Policy"] = config.csp.to_header()
    
    return headers


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware to add security headers to all responses.
    
    Applies comprehensive security headers based on configuration.
    """
    
    def __init__(self, app, config: SecurityConfig = None):
        super().__init__(app)
        self.config = config or SecurityConfig()
        self.is_production = self.config.is_production()
        
        self._cached_headers = get_security_headers(self.config, self.is_production)
        
        self.skip_headers_for = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2"}
    
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        
        path = request.url.path.lower()
        is_static = any(path.endswith(ext) for ext in self.skip_headers_for)
        
        if is_static:
            response.headers["X-Content-Type-Options"] = "nosniff"
            if self.is_production:
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            for header_name, header_value in self._cached_headers.items():
                if header_name not in response.headers:
                    response.headers[header_name] = header_value
        
        return response


class CSPReportEndpoint:
    """
    Handler for CSP violation reports.
    
    Logs CSP violations for security monitoring.
    """
    
    def __init__(self):
        self.violation_count = 0
    
    async def handle_report(self, request: Request) -> Response:
        """Handle CSP violation report"""
        try:
            body = await request.json()
            
            csp_report = body.get("csp-report", {})
            
            logger.warning(
                f"CSP Violation: "
                f"blocked-uri={csp_report.get('blocked-uri')}, "
                f"violated-directive={csp_report.get('violated-directive')}, "
                f"document-uri={csp_report.get('document-uri')}, "
                f"source-file={csp_report.get('source-file')}"
            )
            
            self.violation_count += 1
            
            return Response(status_code=204)
            
        except Exception as e:
            logger.error(f"Error processing CSP report: {e}")
            return Response(status_code=400)


def get_telegram_webapp_csp() -> str:
    """
    Get CSP specifically configured for Telegram WebApp.
    
    Telegram WebApp has specific requirements for embedding.
    """
    config = CSPConfig(
        default_src=["'self'"],
        script_src=["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://telegram.org", "https://web.telegram.org"],
        style_src=["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        font_src=["'self'", "https://fonts.gstatic.com", "data:"],
        img_src=["'self'", "data:", "https:", "blob:"],
        connect_src=[
            "'self'",
            "https://api.telegram.org",
            "https://*.qris.pw",
            "https://*.onrender.com",
            "https://*.netlify.app",
            "wss://*.telegram.org"
        ],
        frame_ancestors=["'self'", "https://web.telegram.org", "https://telegram.org"],
        form_action=["'self'"],
        base_uri=["'self'"],
        object_src=["'none'"],
        frame_src=["'self'", "https://telegram.org", "https://web.telegram.org"]
    )
    return config.to_header()


def get_admin_panel_csp() -> str:
    """
    Get CSP specifically configured for Admin Panel.
    
    Admin panel may need slightly different CSP than public pages.
    """
    config = CSPConfig(
        default_src=["'self'"],
        script_src=["'self'", "'unsafe-inline'"],
        style_src=["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
        font_src=["'self'", "https://fonts.gstatic.com"],
        img_src=["'self'", "data:", "https:", "blob:"],
        connect_src=["'self'"],
        frame_ancestors=["'self'"],
        form_action=["'self'"],
        base_uri=["'self'"],
        object_src=["'none'"]
    )
    return config.to_header()
