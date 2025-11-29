"""
Security Configuration Module

Centralized configuration for all security features.
All security-related settings should be defined here.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Set, Optional


def get_bool_env(key: str, default: bool = False) -> bool:
    """Get boolean from environment variable"""
    value = os.getenv(key, '').lower().strip()
    if value in ('true', '1', 'yes', 'on'):
        return True
    if value in ('false', '0', 'no', 'off'):
        return False
    return default


def get_int_env(key: str, default: int) -> int:
    """Get integer from environment variable"""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def get_list_env(key: str, default: Optional[List[str]] = None) -> List[str]:
    """Get list from comma-separated environment variable"""
    value = os.getenv(key, '')
    if not value:
        if default is None:
            return []
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


def is_production_env() -> bool:
    """Check if running in production environment"""
    return bool(os.getenv('RENDER')) or bool(os.getenv('RAILWAY_ENVIRONMENT')) or bool(os.getenv('NETLIFY'))


@dataclass
class RateLimitConfig:
    """
    Rate limiting configuration
    
    NOTE: Rate limits are per-IP and do NOT discriminate by country/geo-location.
    All users worldwide get the same generous limits.
    Adjust via environment variables if needed.
    """
    enabled: bool = True
    global_requests_per_minute: int = 5000
    global_block_duration: int = 30
    
    api_requests_per_minute: int = 2000
    api_block_duration: int = 20
    
    auth_requests_per_minute: int = 60
    auth_block_duration: int = 180
    
    payment_requests_per_minute: int = 100
    payment_block_duration: int = 30
    
    admin_requests_per_minute: int = 500
    admin_block_duration: int = 60
    
    def __post_init__(self):
        default_enabled = is_production_env()
        self.enabled = get_bool_env('RATE_LIMIT_ENABLED', default_enabled)
        self.global_requests_per_minute = get_int_env('RATE_LIMIT_GLOBAL', self.global_requests_per_minute)
        self.api_requests_per_minute = get_int_env('RATE_LIMIT_API', self.api_requests_per_minute)
        self.auth_requests_per_minute = get_int_env('RATE_LIMIT_AUTH', self.auth_requests_per_minute)


@dataclass
class BruteForceConfig:
    """Brute-force protection configuration"""
    max_attempts: int = 5
    lockout_duration: int = 1800
    progressive_delay_start: int = 3
    delay_seconds: int = 5
    max_delay: int = 30
    
    def __post_init__(self):
        self.max_attempts = get_int_env('BRUTE_FORCE_MAX_ATTEMPTS', self.max_attempts)
        self.lockout_duration = get_int_env('BRUTE_FORCE_LOCKOUT', self.lockout_duration)


@dataclass
class WAFConfig:
    """Web Application Firewall configuration"""
    enabled: bool = True
    log_blocked_requests: bool = True
    block_sql_injection: bool = True
    block_xss: bool = True
    block_path_traversal: bool = True
    block_command_injection: bool = True
    max_request_size: int = 20 * 1024 * 1024
    max_url_length: int = 4096
    max_header_size: int = 8192
    
    sql_injection_patterns: List[str] = field(default_factory=lambda: [
        r"('\s*OR\s*'?\d+\s*=\s*\d+'?)",
        r"('\s*AND\s*'?\d+\s*=\s*\d+'?)",
        r"(;\s*(DROP|DELETE|TRUNCATE|ALTER)\s+)",
        r"(UNION\s+(ALL\s+)?SELECT\s+)",
        r"(SLEEP\s*\(|WAITFOR\s+DELAY|BENCHMARK\s*\()",
        r"(xp_cmdshell|sp_executesql)",
    ])
    
    xss_patterns: List[str] = field(default_factory=lambda: [
        r"(<script[^>]*>[\s\S]*?<\/script>)",
        r"(javascript\s*:\s*['\"])",
        r"(<\s*img[^>]+onerror\s*=\s*['\"])",
        r"(<\s*svg[^>]+onload\s*=\s*['\"])",
        r"(<\s*iframe[^>]+src\s*=)",
    ])
    
    path_traversal_patterns: List[str] = field(default_factory=lambda: [
        r"(\.\.\/\.\.\/)",
        r"(%2e%2e%2f|%2e%2e\/|\.\.%2f)",
        r"(\/etc\/passwd|\/etc\/shadow)",
        r"(c:\\windows\\)",
    ])
    
    command_injection_patterns: List[str] = field(default_factory=lambda: [
        r"(\|\s*(cat|rm|wget|curl|bash|sh)\s)",
        r"(;\s*(rm|wget|curl|bash|sh)\s+-)",
        r"(\/bin\/(sh|bash)\s+-c)",
    ])
    
    def __post_init__(self):
        default_enabled = is_production_env()
        self.enabled = get_bool_env('WAF_ENABLED', default_enabled)
        self.log_blocked_requests = get_bool_env('WAF_LOG_BLOCKED', self.log_blocked_requests)


@dataclass
class CSPConfig:
    """Content Security Policy configuration - optimized for Telegram WebApp"""
    enabled: bool = True
    
    default_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'", "'unsafe-eval'"])
    script_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://telegram.org", "https://cdn.tailwindcss.com", "https://cdn.jsdelivr.net", "https://*.onrender.com", "https://*.netlify.app"])
    style_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com", "https://cdn.tailwindcss.com"])
    font_src: List[str] = field(default_factory=lambda: ["'self'", "https://fonts.gstatic.com", "data:"])
    img_src: List[str] = field(default_factory=lambda: ["'self'", "data:", "https:", "blob:", "http:"])
    connect_src: List[str] = field(default_factory=lambda: ["'self'", "https:", "http:", "wss:", "ws:"])
    frame_ancestors: List[str] = field(default_factory=lambda: [
        "'self'", 
        "https://web.telegram.org", 
        "https://*.telegram.org", 
        "https://*.onrender.com", 
        "https://*.netlify.app",
        "https://dramamu.com",
        "https://*.dramamu.com",
        "https://dramamu.id",
        "https://*.dramamu.id",
        "https://dramamu.my.id",
        "https://*.dramamu.my.id"
    ])
    form_action: List[str] = field(default_factory=lambda: ["'self'"])
    base_uri: List[str] = field(default_factory=lambda: ["'self'"])
    object_src: List[str] = field(default_factory=lambda: ["'none'"])
    
    def __post_init__(self):
        self.enabled = get_bool_env('CSP_ENABLED', self.enabled)
    
    def to_header(self) -> str:
        """Generate CSP header string"""
        directives = []
        
        if self.default_src:
            directives.append(f"default-src {' '.join(self.default_src)}")
        if self.script_src:
            directives.append(f"script-src {' '.join(self.script_src)}")
        if self.style_src:
            directives.append(f"style-src {' '.join(self.style_src)}")
        if self.font_src:
            directives.append(f"font-src {' '.join(self.font_src)}")
        if self.img_src:
            directives.append(f"img-src {' '.join(self.img_src)}")
        if self.connect_src:
            directives.append(f"connect-src {' '.join(self.connect_src)}")
        if self.frame_ancestors:
            directives.append(f"frame-ancestors {' '.join(self.frame_ancestors)}")
        if self.form_action:
            directives.append(f"form-action {' '.join(self.form_action)}")
        if self.base_uri:
            directives.append(f"base-uri {' '.join(self.base_uri)}")
        if self.object_src:
            directives.append(f"object-src {' '.join(self.object_src)}")
        
        return "; ".join(directives)


@dataclass
class IPBlockerConfig:
    """
    IP blocker configuration
    
    NOTE: This IP blocker does NOT block by country/geo-location.
    It only blocks IPs that exceed the request threshold (potential abuse).
    Users from any country (Cambodia, Malaysia, Thailand, Singapore, etc.)
    are welcome and will NOT be blocked unless they trigger abuse detection.
    
    To completely disable IP blocking, set IP_BLOCKER_ENABLED=false in env vars.
    """
    enabled: bool = True
    auto_block_threshold: int = 2000
    auto_block_window: int = 120
    auto_block_duration: int = 180
    
    whitelist: Set[str] = field(default_factory=lambda: {
        "127.0.0.1",
        "::1",
        "localhost",
    })
    
    trusted_proxy_ranges: List[str] = field(default_factory=lambda: [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "169.254.0.0/16",
        "127.0.0.0/8",
        "fc00::/7",
        "fe80::/10",
    ])
    
    permanent_blacklist: Set[str] = field(default_factory=set)
    
    def __post_init__(self):
        default_enabled = is_production_env()
        self.enabled = get_bool_env('IP_BLOCKER_ENABLED', default_enabled)
        self.auto_block_threshold = get_int_env('IP_BLOCK_THRESHOLD', self.auto_block_threshold)
        self.auto_block_window = get_int_env('IP_BLOCK_WINDOW', self.auto_block_window)
        self.auto_block_duration = get_int_env('IP_BLOCK_DURATION', self.auto_block_duration)
        whitelist_env = get_list_env('IP_WHITELIST')
        if whitelist_env:
            self.whitelist.update(whitelist_env)
        blacklist_env = get_list_env('IP_BLACKLIST')
        if blacklist_env:
            self.permanent_blacklist.update(blacklist_env)


@dataclass
class SSRFConfig:
    """SSRF protection configuration"""
    enabled: bool = False
    
    allowed_domains: Set[str] = field(default_factory=lambda: {
        "api.telegram.org",
        "telegram.org",
        "web.telegram.org",
        "t.me",
        "core.telegram.org",
        "qris.pw",
        "www.qris.pw",
        "api.qris.pw",
        "supabase.co",
        "supabase.com",
        "pooler.supabase.com",
        "db.supabase.co",
        "render.com",
        "onrender.com",
        "netlify.app",
        "netlify.com",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    })
    
    block_private_ips: bool = False
    
    private_ip_ranges: List[str] = field(default_factory=lambda: [
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    ])
    
    def __post_init__(self):
        self.enabled = get_bool_env('SSRF_PROTECTION_ENABLED', False)
        self.block_private_ips = get_bool_env('SSRF_BLOCK_PRIVATE_IPS', False)
        allowed_env = get_list_env('SSRF_ALLOWED_DOMAINS')
        if allowed_env:
            self.allowed_domains.update(allowed_env)


@dataclass
class AuditLogConfig:
    """Audit logging configuration"""
    enabled: bool = True
    log_to_file: bool = True
    log_file_path: str = "logs/security_audit.log"
    log_to_database: bool = False
    max_log_size: int = 10 * 1024 * 1024
    backup_count: int = 5
    
    log_login_attempts: bool = True
    log_failed_auth: bool = True
    log_admin_actions: bool = True
    log_payment_events: bool = True
    log_rate_limits: bool = True
    log_blocked_requests: bool = True
    log_file_uploads: bool = True
    
    def __post_init__(self):
        self.enabled = get_bool_env('AUDIT_LOG_ENABLED', self.enabled)
        self.log_to_file = get_bool_env('AUDIT_LOG_TO_FILE', self.log_to_file)
        log_path = os.getenv('AUDIT_LOG_PATH')
        if log_path:
            self.log_file_path = log_path


@dataclass
class SecurityConfig:
    """Main security configuration container"""
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    brute_force: BruteForceConfig = field(default_factory=BruteForceConfig)
    waf: WAFConfig = field(default_factory=WAFConfig)
    csp: CSPConfig = field(default_factory=CSPConfig)
    ip_blocker: IPBlockerConfig = field(default_factory=IPBlockerConfig)
    ssrf: SSRFConfig = field(default_factory=SSRFConfig)
    audit_log: AuditLogConfig = field(default_factory=AuditLogConfig)
    
    debug_mode: bool = False
    
    def __post_init__(self):
        self.debug_mode = get_bool_env('SECURITY_DEBUG', self.debug_mode)
    
    @classmethod
    def from_env(cls) -> 'SecurityConfig':
        """Create SecurityConfig from environment variables"""
        return cls()
    
    def is_production(self) -> bool:
        """Check if running in production mode"""
        return is_production_env()
