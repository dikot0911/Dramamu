"""
Security Audit Logger Module

Provides comprehensive logging for security events:
- Login attempts (success/failure)
- Admin actions
- Payment transactions
- Rate limit triggers
- WAF blocks
- File uploads
- Session management

Logs are structured for easy analysis and forensics.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime
from typing import Any, Dict, Optional
from dataclasses import dataclass, asdict
from logging.handlers import RotatingFileHandler

from .config import AuditLogConfig

logger = logging.getLogger(__name__)


@dataclass
class SecurityEvent:
    """Structured security event"""
    timestamp: str
    event_type: str
    severity: str
    ip_address: Optional[str]
    user_id: Optional[str]
    username: Optional[str]
    session_id: Optional[str]
    user_agent: Optional[str]
    details: Dict[str, Any]
    request_id: Optional[str] = None
    
    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return asdict(self)
    
    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), default=str)


class AuditLogger:
    """
    Security audit logger with file and optional database storage.
    
    Features:
    - Structured JSON logging
    - Log rotation
    - Severity levels (info, warning, error, critical)
    - Thread-safe operations
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, config: AuditLogConfig = None):
        """Singleton pattern for audit logger"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._initialized = False
                    cls._instance = instance
        return cls._instance
    
    def __init__(self, config: AuditLogConfig = None):
        if self._initialized:
            return
        
        self.config = config or AuditLogConfig()
        self._setup_logger()
        self._initialized = True
    
    def _setup_logger(self):
        """Setup the audit logger"""
        self._audit_logger = logging.getLogger("security.audit")
        self._audit_logger.setLevel(logging.INFO)
        
        self._audit_logger.handlers = []
        
        if self.config.log_to_file:
            log_dir = os.path.dirname(self.config.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)
            
            file_handler = RotatingFileHandler(
                self.config.log_file_path,
                maxBytes=self.config.max_log_size,
                backupCount=self.config.backup_count
            )
            file_handler.setLevel(logging.INFO)
            
            formatter = logging.Formatter('%(message)s')
            file_handler.setFormatter(formatter)
            
            self._audit_logger.addHandler(file_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(logging.Formatter(
            '%(asctime)s - AUDIT - %(message)s'
        ))
        self._audit_logger.addHandler(console_handler)
    
    def log(
        self,
        event_type: str,
        severity: str = "info",
        ip_address: str = None,
        user_id: str = None,
        username: str = None,
        session_id: str = None,
        user_agent: str = None,
        details: Dict[str, Any] = None,
        request_id: str = None
    ):
        """
        Log a security event.
        
        Args:
            event_type: Type of security event
            severity: Severity level (info, warning, error, critical)
            ip_address: Client IP address
            user_id: User ID if authenticated
            username: Username if available
            session_id: Session ID if available
            user_agent: Client user agent
            details: Additional event details
            request_id: Request tracking ID
        """
        if not self.config.enabled:
            return
        
        event = SecurityEvent(
            timestamp=datetime.utcnow().isoformat() + "Z",
            event_type=event_type,
            severity=severity,
            ip_address=ip_address,
            user_id=user_id,
            username=username,
            session_id=session_id,
            user_agent=user_agent,
            details=details or {},
            request_id=request_id
        )
        
        log_line = event.to_json()
        
        if severity == "critical":
            self._audit_logger.critical(log_line)
        elif severity == "error":
            self._audit_logger.error(log_line)
        elif severity == "warning":
            self._audit_logger.warning(log_line)
        else:
            self._audit_logger.info(log_line)
    
    def log_login_attempt(
        self,
        success: bool,
        username: str,
        ip_address: str = None,
        user_agent: str = None,
        failure_reason: str = None
    ):
        """Log login attempt"""
        if not self.config.log_login_attempts:
            return
        
        self.log(
            event_type="login_success" if success else "login_failed",
            severity="info" if success else "warning",
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            details={
                "success": success,
                "failure_reason": failure_reason
            }
        )
    
    def log_admin_action(
        self,
        action: str,
        admin_id: str,
        admin_username: str,
        target_type: str = None,
        target_id: str = None,
        ip_address: str = None,
        details: Dict[str, Any] = None
    ):
        """Log admin action"""
        if not self.config.log_admin_actions:
            return
        
        self.log(
            event_type="admin_action",
            severity="info",
            user_id=admin_id,
            username=admin_username,
            ip_address=ip_address,
            details={
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                **(details or {})
            }
        )
    
    def log_payment_event(
        self,
        event: str,
        order_id: str,
        user_id: str = None,
        amount: int = None,
        status: str = None,
        ip_address: str = None,
        details: Dict[str, Any] = None
    ):
        """Log payment event"""
        if not self.config.log_payment_events:
            return
        
        self.log(
            event_type=f"payment_{event}",
            severity="info",
            user_id=user_id,
            ip_address=ip_address,
            details={
                "order_id": order_id,
                "amount": amount,
                "status": status,
                **(details or {})
            }
        )
    
    def log_rate_limit(
        self,
        limit_type: str,
        ip_address: str = None,
        user_id: str = None,
        endpoint: str = None
    ):
        """Log rate limit trigger"""
        if not self.config.log_rate_limits:
            return
        
        self.log(
            event_type="rate_limit_exceeded",
            severity="warning",
            ip_address=ip_address,
            user_id=user_id,
            details={
                "limit_type": limit_type,
                "endpoint": endpoint
            }
        )
    
    def log_waf_block(
        self,
        attack_type: str,
        pattern: str,
        location: str,
        ip_address: str = None,
        path: str = None,
        method: str = None
    ):
        """Log WAF block"""
        if not self.config.log_blocked_requests:
            return
        
        self.log(
            event_type="waf_block",
            severity="warning",
            ip_address=ip_address,
            details={
                "attack_type": attack_type,
                "pattern": pattern[:100],
                "location": location,
                "path": path,
                "method": method
            }
        )
    
    def log_file_upload(
        self,
        filename: str,
        file_size: int,
        content_type: str,
        user_id: str = None,
        ip_address: str = None,
        success: bool = True,
        failure_reason: str = None
    ):
        """Log file upload"""
        if not self.config.log_file_uploads:
            return
        
        self.log(
            event_type="file_upload",
            severity="info" if success else "warning",
            user_id=user_id,
            ip_address=ip_address,
            details={
                "filename": filename,
                "file_size": file_size,
                "content_type": content_type,
                "success": success,
                "failure_reason": failure_reason
            }
        )


_audit_logger = None


def get_audit_logger() -> AuditLogger:
    """Get the singleton audit logger instance"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def log_security_event(
    event_type: str,
    severity: str = "info",
    details: Dict[str, Any] = None,
    ip_address: str = None,
    user_id: str = None,
    username: str = None,
    user_agent: str = None
):
    """
    Convenience function to log security events.
    
    Example:
        log_security_event(
            event_type="suspicious_activity",
            severity="warning",
            details={"action": "multiple_failed_logins"},
            ip_address="1.2.3.4"
        )
    """
    get_audit_logger().log(
        event_type=event_type,
        severity=severity,
        details=details,
        ip_address=ip_address,
        user_id=user_id,
        username=username,
        user_agent=user_agent
    )
