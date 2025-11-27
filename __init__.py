"""
Security Module for Dramamu Telegram Bot

This module provides comprehensive security features:
- Rate limiting (multi-level)
- Web Application Firewall (WAF)
- Security headers & CSP
- Input validation & sanitization
- IP blocking & SSRF protection
- Brute-force protection
- Security audit logging

Usage:
    from security import setup_security_middleware
    
    # In main.py
    setup_security_middleware(app)
"""

from .rate_limiter import RateLimiter, RateLimitMiddleware
from .waf import WAFMiddleware
from .headers import SecurityHeadersMiddleware, get_csp_header
from .input_validator import InputValidator, sanitize_html, validate_input
from .ip_blocker import IPBlocker, SSRFProtector
from .brute_force import BruteForceProtector
from .audit_logger import AuditLogger, log_security_event
from .config import SecurityConfig

__all__ = [
    'RateLimiter',
    'RateLimitMiddleware',
    'WAFMiddleware',
    'SecurityHeadersMiddleware',
    'get_csp_header',
    'InputValidator',
    'sanitize_html',
    'validate_input',
    'IPBlocker',
    'SSRFProtector',
    'BruteForceProtector',
    'AuditLogger',
    'log_security_event',
    'SecurityConfig',
    'setup_security_middleware',
]


def setup_security_middleware(app, config: SecurityConfig = None):
    """
    Setup all security middleware for FastAPI application.
    
    Args:
        app: FastAPI application instance
        config: SecurityConfig instance (uses defaults if None)
    
    Example:
        from fastapi import FastAPI
        from security import setup_security_middleware
        
        app = FastAPI()
        setup_security_middleware(app)
    """
    if config is None:
        config = SecurityConfig()
    
    app.add_middleware(SecurityHeadersMiddleware, config=config)
    
    app.add_middleware(WAFMiddleware, config=config)
    
    app.add_middleware(RateLimitMiddleware, config=config)
    
    return app
