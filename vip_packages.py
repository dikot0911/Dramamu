"""
VIP Package Validation and Management

BUG FIX #6: Strict package name validation without dangerous fallback defaults.

This module provides centralized VIP package validation to prevent:
- Wrong VIP duration assignment (user pays for 30 days, gets 1 day)
- Financial loss due to typos in payment gateway data
- Customer complaints and refund requests

Security improvements:
1. Strict package name validation (reject unknown packages)
2. No default fallback (fail fast on errors)
3. Centralized package definition (single source of truth)
4. Type-safe package handling
"""

import logging
from enum import Enum
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class VipPackage(str, Enum):
    """
    Enumeration of valid VIP packages.
    
    Using Enum ensures type safety and prevents typos.
    Each package has exact duration mapping.
    """
    VIP_1_DAY = "VIP 1 Hari"
    VIP_3_DAYS = "VIP 3 Hari"
    VIP_7_DAYS = "VIP 7 Hari"
    VIP_15_DAYS = "VIP 15 Hari"
    VIP_30_DAYS = "VIP 30 Hari"


# Package duration mapping (days)
PACKAGE_DURATIONS: dict[VipPackage, int] = {
    VipPackage.VIP_1_DAY: 1,
    VipPackage.VIP_3_DAYS: 3,
    VipPackage.VIP_7_DAYS: 7,
    VipPackage.VIP_15_DAYS: 15,
    VipPackage.VIP_30_DAYS: 30,
}


# Package price mapping (Rupiah)
PACKAGE_PRICES = {
    VipPackage.VIP_1_DAY: 2000,
    VipPackage.VIP_3_DAYS: 5000,
    VipPackage.VIP_7_DAYS: 10000,
    VipPackage.VIP_15_DAYS: 30000,
    VipPackage.VIP_30_DAYS: 150000,
}


def validate_package_name(package_name: str) -> Tuple[bool, Optional[int], Optional[str]]:
    """
    Validate VIP package name and return duration if valid.
    
    CRITICAL: NO DEFAULT FALLBACK. Invalid package names are rejected.
    This prevents financial loss from typos or malformed data.
    
    Args:
        package_name: Package name from payment gateway
        
    Returns:
        Tuple[bool, Optional[int], Optional[str]]:
        - is_valid: True if package name is recognized
        - duration_days: Number of VIP days (None if invalid)
        - error_message: Error description (None if valid)
    
    Example:
        >>> validate_package_name("VIP 30 Hari")
        (True, 30, None)
        
        >>> validate_package_name("VIP 30 Days")  # Typo!
        (False, None, "Package name 'VIP 30 Days' tidak dikenali...")
    """
    # Normalize whitespace
    package_name = package_name.strip()
    
    # Check if package name exists in our enum
    try:
        # Try exact match first
        if package_name in [pkg.value for pkg in VipPackage]:
            # Find matching enum member
            for pkg in VipPackage:
                if pkg.value == package_name:
                    duration = PACKAGE_DURATIONS[pkg]
                    logger.info(f"✅ Valid package: {package_name} → {duration} days")
                    return True, duration, None
        
        # No match found - REJECT (no fallback!)
        error_msg = (
            f"Package name '{package_name}' tidak dikenali. "
            f"Package yang valid: {', '.join([pkg.value for pkg in VipPackage])}. "
            f"⚠️ CRITICAL: Ini mungkin typo dari payment gateway! "
            f"Silakan hubungi admin segera."
        )
        
        logger.error(
            f"❌ INVALID PACKAGE NAME: '{package_name}'. "
            f"Payment REJECTED to prevent wrong VIP duration assignment."
        )
        
        return False, None, error_msg
        
    except Exception as e:
        logger.error(f"❌ Error validating package name '{package_name}': {e}")
        return False, None, f"Error validasi package: {str(e)}"


def get_package_duration(package_name: str) -> int:
    """
    Get VIP duration for a package name.
    
    Raises ValueError if package name is invalid (no silent fallback).
    Use this in contexts where exceptions are appropriate.
    
    Args:
        package_name: Package name from payment
        
    Returns:
        Duration in days
        
    Raises:
        ValueError: If package name is invalid
    """
    valid, duration, error = validate_package_name(package_name)
    
    if not valid or duration is None:
        raise ValueError(error or f"Invalid package name: {package_name}")
    
    return duration


def get_package_price(package_name: str) -> Optional[int]:
    """
    Get price for a package name.
    
    Args:
        package_name: Package name
        
    Returns:
        Price in Rupiah, or None if invalid package
    """
    try:
        for pkg in VipPackage:
            if pkg.value == package_name:
                return PACKAGE_PRICES[pkg]
        return None
    except Exception as e:
        logger.error(f"Error getting package price for '{package_name}': {e}")
        return None


def list_all_packages() -> list:
    """
    Get list of all valid VIP packages with details.
    
    Useful for admin panel and API documentation.
    
    Returns:
        List of package dictionaries
    """
    packages = []
    for pkg in VipPackage:
        packages.append({
            "name": pkg.value,
            "duration_days": PACKAGE_DURATIONS[pkg],
            "price_rupiah": PACKAGE_PRICES[pkg],
        })
    return packages


def is_valid_package(package_name: str) -> bool:
    """
    Quick boolean check if package name is valid.
    
    Args:
        package_name: Package name to check
        
    Returns:
        True if valid, False otherwise
    """
    valid, _, _ = validate_package_name(package_name)
    return valid
