"""
Input Validation & Sanitization Module

Provides comprehensive input validation and sanitization:
- Type validation
- Length validation
- Pattern matching (regex)
- SQL injection prevention
- XSS sanitization
- Special character escaping

All user input should pass through this module before processing.
"""

import re
import html
import logging
from typing import Any, Dict, List, Optional, Union, Callable
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger(__name__)


HTML_ESCAPE_MAP = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#x27;',
    '/': '&#x2F;',
    '`': '&#x60;',
    '=': '&#x3D;',
}

SQL_DANGEROUS_CHARS = [
    "'", '"', ';', '--', '/*', '*/', '@@', '@',
    'char(', 'nchar(', 'varchar(', 'nvarchar(',
    'alter ', 'begin ', 'cast(', 'create ', 'cursor ',
    'declare ', 'delete ', 'drop ', 'end ', 'exec ',
    'execute ', 'fetch ', 'insert ', 'kill ', 'open ',
    'select ', 'sys.', 'sysobjects', 'syscolumns',
    'table ', 'update ', 'union ', 'xp_'
]

COMMON_PATTERNS = {
    'email': r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$',
    'username': r'^[a-zA-Z0-9_]{3,30}$',
    'phone': r'^\+?[0-9]{10,15}$',
    'url': r'^https?://[^\s<>"{}|\\^`\[\]]+$',
    'uuid': r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    'alphanumeric': r'^[a-zA-Z0-9]+$',
    'alpha': r'^[a-zA-Z]+$',
    'numeric': r'^[0-9]+$',
    'telegram_id': r'^[0-9]{5,15}$',
    'order_id': r'^[a-zA-Z0-9_-]{10,50}$',
}


def sanitize_html(text: str, allow_safe_tags: bool = False) -> str:
    """
    Sanitize text to prevent XSS attacks.
    
    Args:
        text: Input text to sanitize
        allow_safe_tags: If True, allow some safe HTML tags
    
    Returns:
        Sanitized text safe for HTML display
    """
    if not text:
        return ""
    
    if not isinstance(text, str):
        text = str(text)
    
    sanitized = html.escape(text)
    
    return sanitized


def sanitize_for_sql(text: str) -> str:
    """
    Sanitize text for SQL (as extra layer, ORM should handle this).
    
    Note: This is a defense-in-depth measure. Always use parameterized queries!
    
    Args:
        text: Input text to sanitize
    
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    if not isinstance(text, str):
        text = str(text)
    
    text = text.replace("'", "''")
    text = text.replace("\\", "\\\\")
    
    return text


def strip_dangerous_chars(text: str) -> str:
    """
    Remove potentially dangerous characters from input.
    
    Args:
        text: Input text
    
    Returns:
        Text with dangerous characters removed
    """
    if not text:
        return ""
    
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    
    text = re.sub(r'[\u200b-\u200f\u2028-\u202f\u2060-\u206f]', '', text)
    
    return text


def validate_length(text: str, min_length: int = 0, max_length: int = 10000) -> bool:
    """Validate text length"""
    if not text:
        return min_length == 0
    return min_length <= len(text) <= max_length


def validate_pattern(text: str, pattern: str) -> bool:
    """Validate text against regex pattern"""
    if not text:
        return False
    try:
        return bool(re.match(pattern, text))
    except re.error:
        return False


def validate_email(email: str) -> bool:
    """Validate email format"""
    return validate_pattern(email, COMMON_PATTERNS['email'])


def validate_username(username: str) -> bool:
    """Validate username format"""
    return validate_pattern(username, COMMON_PATTERNS['username'])


def validate_telegram_id(telegram_id: Union[str, int]) -> bool:
    """Validate Telegram ID format"""
    return validate_pattern(str(telegram_id), COMMON_PATTERNS['telegram_id'])


def validate_url(url: str, allowed_domains: List[str] = None) -> bool:
    """
    Validate URL format and optionally check domain whitelist.
    
    Args:
        url: URL to validate
        allowed_domains: Optional list of allowed domains
    
    Returns:
        True if URL is valid and (if specified) domain is allowed
    """
    if not validate_pattern(url, COMMON_PATTERNS['url']):
        return False
    
    if allowed_domains:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            return any(
                domain == allowed or domain.endswith('.' + allowed)
                for allowed in allowed_domains
            )
        except Exception:
            return False
    
    return True


@dataclass
class ValidationRule:
    """Single validation rule"""
    field_name: str
    required: bool = True
    min_length: int = 0
    max_length: int = 10000
    pattern: str = None
    pattern_name: str = None
    custom_validator: Callable = None
    sanitize: bool = True
    error_message: str = None


@dataclass
class ValidationResult:
    """Result of validation"""
    is_valid: bool
    errors: Dict[str, str] = field(default_factory=dict)
    sanitized_data: Dict[str, Any] = field(default_factory=dict)


class InputValidator:
    """
    Comprehensive input validator.
    
    Validates and sanitizes input data based on defined rules.
    
    Example:
        validator = InputValidator()
        validator.add_rule(ValidationRule(
            field_name="username",
            required=True,
            pattern_name="username",
            max_length=30
        ))
        validator.add_rule(ValidationRule(
            field_name="email",
            required=True,
            pattern_name="email"
        ))
        
        result = validator.validate({"username": "john_doe", "email": "john@example.com"})
        if result.is_valid:
            process(result.sanitized_data)
    """
    
    def __init__(self):
        self.rules: Dict[str, ValidationRule] = {}
    
    def add_rule(self, rule: ValidationRule) -> 'InputValidator':
        """Add validation rule"""
        self.rules[rule.field_name] = rule
        return self
    
    def validate(self, data: Dict[str, Any]) -> ValidationResult:
        """
        Validate input data against all rules.
        
        Args:
            data: Dictionary of field_name -> value
        
        Returns:
            ValidationResult with is_valid, errors, and sanitized_data
        """
        errors = {}
        sanitized = {}
        
        for field_name, rule in self.rules.items():
            value = data.get(field_name)
            
            if value is None or (isinstance(value, str) and not value.strip()):
                if rule.required:
                    errors[field_name] = rule.error_message or f"{field_name} is required"
                continue
            
            if isinstance(value, str):
                value = strip_dangerous_chars(value.strip())
                
                if not validate_length(value, rule.min_length, rule.max_length):
                    errors[field_name] = (
                        rule.error_message or 
                        f"{field_name} must be between {rule.min_length} and {rule.max_length} characters"
                    )
                    continue
                
                pattern = rule.pattern
                if not pattern and rule.pattern_name:
                    pattern = COMMON_PATTERNS.get(rule.pattern_name)
                
                if pattern and not validate_pattern(value, pattern):
                    errors[field_name] = (
                        rule.error_message or 
                        f"{field_name} has invalid format"
                    )
                    continue
                
                if rule.sanitize:
                    value = sanitize_html(value)
            
            if rule.custom_validator:
                try:
                    if not rule.custom_validator(value):
                        errors[field_name] = (
                            rule.error_message or 
                            f"{field_name} failed custom validation"
                        )
                        continue
                except Exception as e:
                    errors[field_name] = str(e)
                    continue
            
            sanitized[field_name] = value
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            sanitized_data=sanitized
        )


def validate_input(
    data: Dict[str, Any],
    required_fields: List[str] = None,
    max_lengths: Dict[str, int] = None,
    patterns: Dict[str, str] = None
) -> ValidationResult:
    """
    Quick validation function for simple cases.
    
    Args:
        data: Input data dictionary
        required_fields: List of required field names
        max_lengths: Dict of field_name -> max_length
        patterns: Dict of field_name -> pattern or pattern_name
    
    Returns:
        ValidationResult
    """
    validator = InputValidator()
    
    all_fields = set(data.keys())
    if required_fields:
        all_fields.update(required_fields)
    if max_lengths:
        all_fields.update(max_lengths.keys())
    if patterns:
        all_fields.update(patterns.keys())
    
    for field_name in all_fields:
        rule = ValidationRule(
            field_name=field_name,
            required=field_name in (required_fields or []),
            max_length=max_lengths.get(field_name, 10000) if max_lengths else 10000
        )
        
        if patterns and field_name in patterns:
            pattern = patterns[field_name]
            if pattern in COMMON_PATTERNS:
                rule.pattern_name = pattern
            else:
                rule.pattern = pattern
        
        validator.add_rule(rule)
    
    return validator.validate(data)


def check_sql_injection(text: str) -> bool:
    """
    Check if text contains potential SQL injection patterns.
    
    Note: This is for logging/alerting only. Always use parameterized queries!
    
    Returns:
        True if suspicious patterns detected
    """
    if not text:
        return False
    
    text_lower = text.lower()
    
    for dangerous in SQL_DANGEROUS_CHARS:
        if dangerous.lower() in text_lower:
            return True
    
    return False
