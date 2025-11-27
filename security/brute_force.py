"""
Brute-Force Protection Module

Implements protection against brute-force attacks:
- Progressive delay for failed attempts
- Account lockout after threshold
- IP-based tracking
- Username-based tracking

For production with multiple workers, consider Redis-backed storage.
"""

import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from collections import defaultdict

from .config import SecurityConfig, BruteForceConfig
from .audit_logger import log_security_event

logger = logging.getLogger(__name__)


@dataclass
class LoginAttempt:
    """Track login attempts for an account/IP"""
    failed_attempts: int = 0
    last_attempt: float = 0
    locked_until: float = 0
    total_failed: int = 0
    last_success: float = 0


class BruteForceProtector:
    """
    Brute-force attack protection.
    
    Features:
    - Track failed login attempts per username and IP
    - Progressive delay after multiple failures
    - Account lockout after threshold
    - Automatic unlock after lockout period
    """
    
    def __init__(self, config: BruteForceConfig = None):
        self.config = config or BruteForceConfig()
        
        self._username_attempts: Dict[str, LoginAttempt] = defaultdict(LoginAttempt)
        
        self._ip_attempts: Dict[str, LoginAttempt] = defaultdict(LoginAttempt)
        
        self._last_cleanup = time.time()
        self._cleanup_interval = 3600
    
    def _calculate_delay(self, failed_attempts: int) -> int:
        """Calculate progressive delay based on failed attempts"""
        if failed_attempts < self.config.progressive_delay_start:
            return 0
        
        delay = self.config.delay_seconds * (failed_attempts - self.config.progressive_delay_start + 1)
        
        return min(delay, self.config.max_delay)
    
    async def check_allowed(
        self,
        username: str = None,
        ip: str = None
    ) -> Tuple[bool, Optional[str], int]:
        """
        Check if login attempt is allowed.
        
        Args:
            username: Username attempting login
            ip: IP address of requester
        
        Returns:
            Tuple of (is_allowed, block_reason, retry_after_seconds)
        """
        now = time.time()
        
        if username:
            attempt = self._username_attempts[username]
            
            if attempt.locked_until > now:
                retry_after = int(attempt.locked_until - now)
                return False, "account_locked", retry_after
            
            if attempt.failed_attempts >= self.config.max_attempts:
                attempt.locked_until = now + self.config.lockout_duration
                
                log_security_event(
                    event_type="account_locked",
                    severity="warning",
                    details={
                        "username": username,
                        "failed_attempts": attempt.failed_attempts,
                        "lockout_duration": self.config.lockout_duration
                    },
                    ip_address=ip
                )
                
                return False, "max_attempts_exceeded", self.config.lockout_duration
        
        if ip:
            ip_attempt = self._ip_attempts[ip]
            
            if ip_attempt.locked_until > now:
                retry_after = int(ip_attempt.locked_until - now)
                return False, "ip_locked", retry_after
            
            ip_threshold = self.config.max_attempts * 3
            if ip_attempt.failed_attempts >= ip_threshold:
                ip_attempt.locked_until = now + self.config.lockout_duration
                return False, "ip_max_attempts_exceeded", self.config.lockout_duration
        
        delay = 0
        if username:
            delay = max(delay, self._calculate_delay(self._username_attempts[username].failed_attempts))
        if ip:
            delay = max(delay, self._calculate_delay(self._ip_attempts[ip].failed_attempts))
        
        if delay > 0:
            await asyncio.sleep(delay)
        
        return True, None, 0
    
    def record_failure(
        self,
        username: str = None,
        ip: str = None,
        reason: str = "invalid_credentials"
    ):
        """
        Record a failed login attempt.
        
        Args:
            username: Username that failed
            ip: IP address of requester
            reason: Reason for failure
        """
        now = time.time()
        
        if username:
            attempt = self._username_attempts[username]
            attempt.failed_attempts += 1
            attempt.total_failed += 1
            attempt.last_attempt = now
            
            logger.warning(
                f"Failed login for '{username}': attempts={attempt.failed_attempts}, "
                f"total_failed={attempt.total_failed}, reason={reason}"
            )
        
        if ip:
            ip_attempt = self._ip_attempts[ip]
            ip_attempt.failed_attempts += 1
            ip_attempt.total_failed += 1
            ip_attempt.last_attempt = now
        
        log_security_event(
            event_type="login_failed",
            severity="info",
            details={
                "username": username,
                "reason": reason,
                "attempt_count": self._username_attempts.get(username, LoginAttempt()).failed_attempts if username else None
            },
            ip_address=ip
        )
    
    def record_success(self, username: str = None, ip: str = None):
        """
        Record a successful login (resets failed attempts).
        
        Args:
            username: Username that succeeded
            ip: IP address of requester
        """
        now = time.time()
        
        if username and username in self._username_attempts:
            attempt = self._username_attempts[username]
            attempt.failed_attempts = 0
            attempt.locked_until = 0
            attempt.last_success = now
            
            logger.info(f"Successful login for '{username}', failed attempts reset")
        
        if ip and ip in self._ip_attempts:
            ip_attempt = self._ip_attempts[ip]
            ip_attempt.failed_attempts = max(0, ip_attempt.failed_attempts - 1)
            ip_attempt.last_success = now
        
        log_security_event(
            event_type="login_success",
            severity="info",
            details={"username": username},
            ip_address=ip
        )
    
    def is_locked(self, username: str) -> Tuple[bool, int]:
        """
        Check if account is locked.
        
        Returns:
            Tuple of (is_locked, remaining_seconds)
        """
        if username not in self._username_attempts:
            return False, 0
        
        attempt = self._username_attempts[username]
        now = time.time()
        
        if attempt.locked_until > now:
            return True, int(attempt.locked_until - now)
        
        return False, 0
    
    def unlock_account(self, username: str) -> bool:
        """Manually unlock an account"""
        if username in self._username_attempts:
            attempt = self._username_attempts[username]
            attempt.failed_attempts = 0
            attempt.locked_until = 0
            logger.info(f"Account unlocked manually: {username}")
            return True
        return False
    
    def get_attempt_info(self, username: str = None, ip: str = None) -> dict:
        """Get information about login attempts"""
        info = {}
        
        if username and username in self._username_attempts:
            attempt = self._username_attempts[username]
            info["username"] = {
                "failed_attempts": attempt.failed_attempts,
                "total_failed": attempt.total_failed,
                "is_locked": attempt.locked_until > time.time(),
                "locked_until": attempt.locked_until if attempt.locked_until > time.time() else None
            }
        
        if ip and ip in self._ip_attempts:
            attempt = self._ip_attempts[ip]
            info["ip"] = {
                "failed_attempts": attempt.failed_attempts,
                "total_failed": attempt.total_failed,
                "is_locked": attempt.locked_until > time.time()
            }
        
        return info
    
    def can_attempt(self, username: str = None, ip: str = None) -> bool:
        """
        Synchronous check if login attempt is allowed.
        Wrapper for check_allowed for non-async contexts.
        
        Returns:
            True if attempt allowed, False if blocked
        """
        now = time.time()
        
        if username and username in self._username_attempts:
            attempt = self._username_attempts[username]
            if attempt.locked_until > now:
                return False
            if attempt.failed_attempts >= self.config.max_attempts:
                attempt.locked_until = now + self.config.lockout_duration
                return False
        
        if ip and ip in self._ip_attempts:
            ip_attempt = self._ip_attempts[ip]
            if ip_attempt.locked_until > now:
                return False
            ip_threshold = self.config.max_attempts * 3
            if ip_attempt.failed_attempts >= ip_threshold:
                ip_attempt.locked_until = now + self.config.lockout_duration
                return False
        
        return True
    
    def get_lockout_time(self, username: str = None, ip: str = None) -> int:
        """
        Get remaining lockout time in minutes.
        
        Returns:
            Minutes until lockout expires, 0 if not locked
        """
        now = time.time()
        max_remaining = 0
        
        if username and username in self._username_attempts:
            attempt = self._username_attempts[username]
            if attempt.locked_until > now:
                remaining = int((attempt.locked_until - now) / 60) + 1
                max_remaining = max(max_remaining, remaining)
        
        if ip and ip in self._ip_attempts:
            ip_attempt = self._ip_attempts[ip]
            if ip_attempt.locked_until > now:
                remaining = int((ip_attempt.locked_until - now) / 60) + 1
                max_remaining = max(max_remaining, remaining)
        
        return max_remaining
    
    def record_failed_attempt(self, username: str = None, ip: str = None):
        """
        Alias for record_failure for backwards compatibility.
        """
        self.record_failure(username=username, ip=ip, reason="invalid_credentials")
    
    def reset_attempts(self, username: str = None, ip: str = None):
        """
        Alias for record_success for backwards compatibility.
        """
        self.record_success(username=username, ip=ip)
    
    def cleanup_expired(self):
        """Remove expired entries to free memory"""
        now = time.time()
        cutoff = now - 86400
        
        expired_users = [
            username for username, attempt in self._username_attempts.items()
            if attempt.last_attempt < cutoff and attempt.locked_until < now
        ]
        for username in expired_users:
            del self._username_attempts[username]
        
        expired_ips = [
            ip for ip, attempt in self._ip_attempts.items()
            if attempt.last_attempt < cutoff and attempt.locked_until < now
        ]
        for ip in expired_ips:
            del self._ip_attempts[ip]
        
        logger.debug(f"Brute-force cleanup: removed {len(expired_users)} users, {len(expired_ips)} IPs")


brute_force_protector = BruteForceProtector()
