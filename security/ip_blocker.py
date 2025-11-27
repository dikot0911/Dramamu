"""
IP Blocker & SSRF Protection Module

Implements:
- IP-based blocking (blacklist/whitelist)
- Automatic blocking for suspicious activity
- SSRF protection with domain whitelist
- Private IP range blocking

For production with multiple workers, consider Redis-backed storage.
"""

import time
import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Dict, Set, Optional, Tuple
from urllib.parse import urlparse
import socket

from .config import SecurityConfig, IPBlockerConfig, SSRFConfig

logger = logging.getLogger(__name__)


@dataclass
class BlockEntry:
    """Entry for blocked IP"""
    blocked_at: float
    expires_at: float
    reason: str
    request_count: int = 0


class IPBlocker:
    """
    IP-based access control with automatic blocking.
    
    Features:
    - Whitelist for trusted IPs
    - Permanent blacklist
    - Automatic temporary blocking based on suspicious activity
    - Rate-based automatic blocking
    - Trusted proxy range support (for load balancers/proxies)
    """
    
    def __init__(self, config: IPBlockerConfig = None):
        self.config = config or IPBlockerConfig()
        
        self._blocked_ips: Dict[str, BlockEntry] = {}
        
        self._request_counts: Dict[str, list] = {}
        
        self._whitelist: Set[str] = set(self.config.whitelist)
        self._permanent_blacklist: Set[str] = set(self.config.permanent_blacklist)
        
        self._trusted_proxy_networks = []
        for cidr in self.config.trusted_proxy_ranges:
            try:
                self._trusted_proxy_networks.append(ipaddress.ip_network(cidr))
            except ValueError:
                logger.warning(f"Invalid trusted proxy range: {cidr}")
    
    def is_trusted_proxy(self, ip: str) -> bool:
        """Check if IP is from a trusted proxy/load balancer"""
        try:
            ip_obj = ipaddress.ip_address(ip)
            return any(ip_obj in network for network in self._trusted_proxy_networks)
        except ValueError:
            return False
    
    def is_whitelisted(self, ip: str) -> bool:
        """Check if IP is in whitelist or trusted proxy range"""
        if ip in self._whitelist:
            return True
        return self.is_trusted_proxy(ip)
    
    def is_blacklisted(self, ip: str) -> bool:
        """Check if IP is permanently blacklisted"""
        return ip in self._permanent_blacklist
    
    def is_blocked(self, ip: str) -> Tuple[bool, Optional[str], Optional[int]]:
        """
        Check if IP is blocked.
        
        Returns:
            Tuple of (is_blocked, reason, retry_after_seconds)
        """
        if not self.config.enabled:
            return False, None, None
        
        if self.is_whitelisted(ip):
            return False, None, None
        
        if self.is_blacklisted(ip):
            return True, "permanently_blocked", None
        
        if ip in self._blocked_ips:
            entry = self._blocked_ips[ip]
            now = time.time()
            
            if now < entry.expires_at:
                retry_after = int(entry.expires_at - now)
                return True, entry.reason, retry_after
            else:
                del self._blocked_ips[ip]
        
        return False, None, None
    
    def record_request(self, ip: str) -> bool:
        """
        Record request from IP for rate tracking.
        
        Returns:
            True if IP should be blocked (exceeded threshold)
        """
        if not self.config.enabled:
            return False
        
        if self.is_whitelisted(ip):
            return False
        
        now = time.time()
        window_start = now - self.config.auto_block_window
        
        if ip not in self._request_counts:
            self._request_counts[ip] = []
        
        self._request_counts[ip] = [
            t for t in self._request_counts[ip]
            if t > window_start
        ]
        
        self._request_counts[ip].append(now)
        
        if len(self._request_counts[ip]) > self.config.auto_block_threshold:
            self.block_ip(ip, "rate_exceeded", self.config.auto_block_duration)
            return True
        
        return False
    
    def block_ip(self, ip: str, reason: str, duration: int = None) -> bool:
        """
        Block an IP address.
        
        Args:
            ip: IP address to block
            reason: Reason for blocking
            duration: Duration in seconds (None for default)
        
        Returns:
            True if blocked successfully
        """
        if self.is_whitelisted(ip):
            logger.warning(f"Cannot block whitelisted IP: {ip}")
            return False
        
        if duration is None:
            duration = self.config.auto_block_duration
        
        now = time.time()
        
        self._blocked_ips[ip] = BlockEntry(
            blocked_at=now,
            expires_at=now + duration,
            reason=reason
        )
        
        logger.warning(f"IP blocked: {ip}, reason={reason}, duration={duration}s")
        
        return True
    
    def unblock_ip(self, ip: str) -> bool:
        """Unblock an IP address"""
        if ip in self._blocked_ips:
            del self._blocked_ips[ip]
            logger.info(f"IP unblocked: {ip}")
            return True
        return False
    
    def add_to_whitelist(self, ip: str):
        """Add IP to whitelist"""
        self._whitelist.add(ip)
        self.unblock_ip(ip)
    
    def add_to_blacklist(self, ip: str):
        """Add IP to permanent blacklist"""
        self._permanent_blacklist.add(ip)
    
    def get_blocked_ips(self) -> Dict[str, dict]:
        """Get all currently blocked IPs with info"""
        now = time.time()
        active_blocks = {}
        
        for ip, entry in self._blocked_ips.items():
            if entry.expires_at > now:
                active_blocks[ip] = {
                    "reason": entry.reason,
                    "blocked_at": entry.blocked_at,
                    "expires_at": entry.expires_at,
                    "remaining_seconds": int(entry.expires_at - now)
                }
        
        for ip in self._permanent_blacklist:
            active_blocks[ip] = {
                "reason": "permanent_blacklist",
                "blocked_at": 0,
                "expires_at": None,
                "remaining_seconds": None
            }
        
        return active_blocks
    
    def cleanup_expired(self):
        """Remove expired block entries"""
        now = time.time()
        expired = [ip for ip, entry in self._blocked_ips.items() if entry.expires_at <= now]
        for ip in expired:
            del self._blocked_ips[ip]


PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),
]


class SSRFProtector:
    """
    SSRF (Server-Side Request Forgery) protection.
    
    Features:
    - Domain whitelist validation
    - Private IP range blocking
    - URL validation and normalization
    """
    
    def __init__(self, config: SSRFConfig = None):
        self.config = config or SSRFConfig()
    
    def is_private_ip(self, ip_str: str) -> bool:
        """Check if IP is in private range"""
        try:
            ip = ipaddress.ip_address(ip_str)
            return any(ip in network for network in PRIVATE_IP_RANGES)
        except ValueError:
            return True
    
    def is_domain_allowed(self, domain: str) -> bool:
        """Check if domain is in whitelist"""
        domain = domain.lower().strip()
        
        for allowed in self.config.allowed_domains:
            if domain == allowed:
                return True
            if domain.endswith('.' + allowed):
                return True
        
        return False
    
    def validate_url(self, url: str) -> Tuple[bool, Optional[str]]:
        """
        Validate URL for SSRF protection.
        
        Args:
            url: URL to validate
        
        Returns:
            Tuple of (is_safe, error_message)
        """
        if not self.config.enabled:
            return True, None
        
        try:
            parsed = urlparse(url)
            
            if parsed.scheme not in ('http', 'https'):
                return False, "Only HTTP/HTTPS protocols allowed"
            
            if not parsed.netloc:
                return False, "Invalid URL: no host"
            
            host = parsed.hostname
            if not host:
                return False, "Invalid URL: no hostname"
            
            if not self.is_domain_allowed(host):
                return False, f"Domain not allowed: {host}"
            
            if self.config.block_private_ips:
                try:
                    ip = ipaddress.ip_address(host)
                    if self.is_private_ip(str(ip)):
                        return False, f"Private IP not allowed: {host}"
                except ValueError:
                    try:
                        resolved_ips = socket.getaddrinfo(host, None)
                        for item in resolved_ips:
                            ip_str = item[4][0]
                            if self.is_private_ip(ip_str):
                                return False, f"Domain resolves to private IP: {host} -> {ip_str}"
                    except socket.gaierror:
                        pass
            
            return True, None
            
        except Exception as e:
            return False, f"URL validation error: {str(e)}"
    
    def is_safe_url(self, url: str) -> bool:
        """
        Check if URL is safe for external requests.
        
        This is a convenience method that returns only the boolean result.
        Use this for simple checks where you don't need the error message.
        
        Args:
            url: URL to validate
        
        Returns:
            True if URL is safe, False otherwise
        """
        is_safe, _ = self.validate_url(url)
        return is_safe
    
    def safe_request(self, url: str, method: str = "GET", **kwargs) -> Tuple[bool, Optional[str]]:
        """
        Make a safe HTTP request with SSRF protection.
        
        This is a wrapper that validates URL before making request.
        Use this instead of raw requests.get/post.
        
        Returns:
            Tuple of (is_allowed, error_message)
            If is_allowed is True, caller can proceed with the request
        """
        is_safe, error = self.validate_url(url)
        if not is_safe:
            logger.warning(f"SSRF blocked: url={url}, error={error}")
            return False, error
        
        return True, None
