#!/usr/bin/env python3
"""
PRODUCTION READINESS VALIDATOR
Script untuk validasi semua konfigurasi sebelum deployment ke production.

Usage:
    python validate_production_ready.py

Exit codes:
    0 - All checks passed (ready for deployment)
    1 - Critical issues found (deployment will fail)
    2 - Warnings found (deployment might work but not optimal)
"""

import os
import sys
from typing import List, Tuple

# ANSI color codes untuk terminal output
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text: str):
    """Print formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}\n")

def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")

def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")

def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")

def print_info(text: str):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")

class ValidationResult:
    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.info: List[str] = []
    
    def add_error(self, msg: str):
        self.errors.append(msg)
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)
    
    def add_info(self, msg: str):
        self.info.append(msg)
    
    def is_valid(self) -> bool:
        return len(self.errors) == 0
    
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

def validate_database_config() -> ValidationResult:
    """Validate database configuration"""
    print_header("🗄️  DATABASE CONFIGURATION")
    result = ValidationResult()
    
    database_url = os.getenv('DATABASE_URL', '').strip()
    
    if not database_url:
        result.add_warning("DATABASE_URL not set - will use SQLite (not recommended for production)")
        print_warning("DATABASE_URL not set - defaulting to SQLite")
    elif database_url.startswith('postgresql'):
        print_success("PostgreSQL database configured")
        
        # Check SSL mode
        db_sslmode = os.getenv('DB_SSLMODE', 'require').strip()
        if db_sslmode == 'require':
            print_success(f"SSL mode: {db_sslmode} (secure)")
        elif db_sslmode in ['prefer', 'allow']:
            result.add_warning(f"DB_SSLMODE is '{db_sslmode}' - recommend 'require' for Supabase")
            print_warning(f"SSL mode: {db_sslmode} (consider using 'require')")
        
        # Check if sslmode in URL
        if '?' in database_url and 'sslmode=' in database_url:
            if 'sslmode=require' in database_url:
                print_success("SSL mode in DATABASE_URL: require (secure)")
            else:
                result.add_warning("SSL mode in DATABASE_URL is not 'require'")
        
        # Check for password in URL (security)
        if '@' in database_url:
            result.add_info("Database credentials detected in URL")
        else:
            result.add_error("DATABASE_URL format invalid - missing credentials")
    else:
        result.add_warning(f"Using non-PostgreSQL database: {database_url.split(':')[0]}")
    
    return result

def validate_backend_config() -> ValidationResult:
    """Validate backend/API configuration"""
    print_header("🖥️  BACKEND CONFIGURATION")
    result = ValidationResult()
    
    # Check API_BASE_URL or RENDER_EXTERNAL_URL
    api_base_url = os.getenv('API_BASE_URL', '').strip()
    render_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()
    
    if api_base_url:
        print_success(f"API_BASE_URL: {api_base_url}")
        if not api_base_url.startswith('https://'):
            result.add_warning("API_BASE_URL should use HTTPS for production")
    elif render_url:
        print_success(f"RENDER_EXTERNAL_URL detected: {render_url}")
    else:
        result.add_error("Neither API_BASE_URL nor RENDER_EXTERNAL_URL set")
        print_error("Backend URL not configured - deployment will fail")
    
    # Check FRONTEND_URL
    frontend_url = os.getenv('FRONTEND_URL', '').strip()
    if frontend_url:
        print_success(f"FRONTEND_URL: {frontend_url}")
        if not frontend_url.startswith('https://'):
            result.add_warning("FRONTEND_URL should use HTTPS for production")
    else:
        result.add_warning("FRONTEND_URL not set - will default to API_BASE_URL")
    
    # Check CORS
    allowed_origins = os.getenv('ALLOWED_ORIGINS', '').strip()
    if allowed_origins:
        origins = [o.strip() for o in allowed_origins.split(',')]
        print_success(f"CORS configured with {len(origins)} origin(s)")
        for origin in origins:
            print_info(f"  - {origin}")
    else:
        if frontend_url:
            print_info("CORS will auto-configure from FRONTEND_URL")
        else:
            result.add_warning("CORS not explicitly configured - might cause issues")
    
    return result

def validate_telegram_config() -> ValidationResult:
    """Validate Telegram bot configuration"""
    print_header("🤖 TELEGRAM BOT CONFIGURATION")
    result = ValidationResult()
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    if bot_token:
        print_success("TELEGRAM_BOT_TOKEN configured")
        if len(bot_token) < 40:
            result.add_error("TELEGRAM_BOT_TOKEN looks invalid (too short)")
    else:
        result.add_error("TELEGRAM_BOT_TOKEN not set - bot will not work")
        print_error("Bot token missing - application will run without bot features")
    
    bot_username = os.getenv('TELEGRAM_BOT_USERNAME', '').strip()
    if bot_username:
        print_success(f"Bot username: @{bot_username}")
    else:
        result.add_warning("TELEGRAM_BOT_USERNAME not set - will use default")
    
    # Optional: Storage chat ID
    storage_chat = os.getenv('TELEGRAM_STORAGE_CHAT_ID', '').strip()
    if storage_chat:
        print_success("Telegram Storage Group configured")
    else:
        result.add_info("TELEGRAM_STORAGE_CHAT_ID not set - upload via Telegram disabled")
    
    return result

def validate_payment_config() -> ValidationResult:
    """Validate payment gateway configuration"""
    print_header("💳 PAYMENT CONFIGURATION (Optional)")
    result = ValidationResult()
    
    server_key = os.getenv('MIDTRANS_SERVER_KEY', '').strip()
    client_key = os.getenv('MIDTRANS_CLIENT_KEY', '').strip()
    
    if server_key and client_key:
        print_success("Midtrans payment gateway configured")
    else:
        result.add_info("Midtrans keys not set - payment features disabled")
        print_info("Payment features disabled (optional)")
    
    return result

def validate_admin_config() -> ValidationResult:
    """Validate admin panel configuration"""
    print_header("👨‍💼 ADMIN PANEL CONFIGURATION")
    result = ValidationResult()
    
    admin_user = os.getenv('ADMIN_USERNAME', '').strip()
    admin_pass = os.getenv('ADMIN_PASSWORD', '').strip()
    jwt_secret = os.getenv('JWT_SECRET_KEY', '').strip()
    
    if admin_user and admin_pass and jwt_secret:
        print_success("Admin credentials configured")
        if len(jwt_secret) < 32:
            result.add_warning("JWT_SECRET_KEY should be at least 32 characters")
    else:
        missing = []
        if not admin_user:
            missing.append('ADMIN_USERNAME')
        if not admin_pass:
            missing.append('ADMIN_PASSWORD')
        if not jwt_secret:
            missing.append('JWT_SECRET_KEY')
        
        result.add_warning(f"Admin panel not configured - missing: {', '.join(missing)}")
        print_warning("Admin panel disabled - set credentials to enable")
    
    return result

def validate_netlify_frontend() -> ValidationResult:
    """Validate Netlify frontend configuration"""
    print_header("🌐 NETLIFY FRONTEND CONFIGURATION")
    result = ValidationResult()
    
    # This is informational - Netlify config is checked during build
    print_info("Frontend validation:")
    print_info("  1. Ensure API_BASE_URL is set in Netlify dashboard")
    print_info("  2. Format: https://your-backend.onrender.com")
    print_info("  3. Build command: chmod +x build-config.sh && ./build-config.sh")
    
    api_url = os.getenv('API_BASE_URL', '').strip()
    if api_url:
        print_success(f"API_BASE_URL ready for Netlify: {api_url}")
    else:
        result.add_warning("API_BASE_URL not set - remember to set in Netlify")
    
    return result

def main():
    """Run all validation checks"""
    print(f"\n{Colors.BOLD}{'=' * 70}")
    print(f"🔍 DRAMAMU BOT - PRODUCTION READINESS VALIDATION")
    print(f"{'=' * 70}{Colors.RESET}\n")
    
    all_results = []
    
    # Run all validations
    all_results.append(validate_database_config())
    all_results.append(validate_backend_config())
    all_results.append(validate_telegram_config())
    all_results.append(validate_payment_config())
    all_results.append(validate_admin_config())
    all_results.append(validate_netlify_frontend())
    
    # Aggregate results
    total_errors = sum(len(r.errors) for r in all_results)
    total_warnings = sum(len(r.warnings) for r in all_results)
    
    # Print summary
    print_header("📋 VALIDATION SUMMARY")
    
    if total_errors == 0 and total_warnings == 0:
        print_success("ALL CHECKS PASSED! ✨")
        print_success("Your application is ready for production deployment!")
        print()
        print_info("Next steps:")
        print_info("  1. Push code to GitHub")
        print_info("  2. Deploy backend to Render")
        print_info("  3. Deploy frontend to Netlify")
        print_info("  4. Test all features in production")
        return 0
    
    if total_errors > 0:
        print_error(f"Found {total_errors} CRITICAL ERROR(S)")
        print()
        for result in all_results:
            for error in result.errors:
                print_error(error)
        print()
        print_error("FIX CRITICAL ERRORS BEFORE DEPLOYMENT!")
        exit_code = 1
    else:
        exit_code = 0
    
    if total_warnings > 0:
        print()
        print_warning(f"Found {total_warnings} WARNING(S)")
        print()
        for result in all_results:
            for warning in result.warnings:
                print_warning(warning)
        print()
        print_warning("Warnings should be reviewed but won't prevent deployment")
        if exit_code == 0:
            exit_code = 2
    
    return exit_code

if __name__ == '__main__':
    exit_code = main()
    sys.exit(exit_code)
