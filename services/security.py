"""
Security utilities for OpenVoiceUI.

Provides:
- CSP nonce generation
- File upload validation
- Security headers middleware
- SECRET_KEY validation
"""
import logging
import os
import secrets
from pathlib import Path
from typing import Optional, Tuple
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

# File upload configuration
ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'gif', 'webp',  # Images
    'pdf', 'txt', 'md',  # Documents
    'wav', 'mp3', 'ogg', 'flac', 'm4a',  # Audio
    'html', 'htm'  # Canvas pages
}

ALLOWED_MIME_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/webp',
    'application/pdf', 'text/plain', 'text/markdown',
    'audio/wav', 'audio/mpeg', 'audio/ogg', 'audio/flac', 'audio/mp4',
    'text/html'
}

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB default

def generate_csp_nonce() -> str:
    """Generate a cryptographically secure CSP nonce."""
    return secrets.token_urlsafe(16)

def validate_filename(filename: str) -> Tuple[bool, Optional[str]]:
    """
    Validate and sanitize filename.
    
    Returns:
        (is_valid, sanitized_filename or error_message)
    """
    if not filename:
        return False, "No filename provided"
    
    # Sanitize filename
    safe_name = secure_filename(filename)
    
    if not safe_name:
        return False, "Invalid filename"
    
    # Check extension
    if '.' not in safe_name:
        return False, "No file extension"
    
    ext = safe_name.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"File type .{ext} not allowed"
    
    return True, safe_name

def validate_file_size(file_stream, max_size: int = MAX_FILE_SIZE) -> Tuple[bool, Optional[str]]:
    """
    Validate file size.
    
    Returns:
        (is_valid, error_message or None)
    """
    # Get file size
    file_stream.seek(0, os.SEEK_END)
    size = file_stream.tell()
    file_stream.seek(0)
    
    if size > max_size:
        size_mb = size / (1024 * 1024)
        max_mb = max_size / (1024 * 1024)
        return False, f"File too large ({size_mb:.1f}MB, max {max_mb}MB)"
    
    if size == 0:
        return False, "Empty file"
    
    return True, None

def validate_file_content(file_stream) -> Tuple[bool, Optional[str]]:
    """
    Validate file content matches expected MIME type.
    
    Uses python-magic if available, otherwise basic validation.
    
    Returns:
        (is_valid, error_message or None)
    """
    try:
        import magic
        mime = magic.from_buffer(file_stream.read(2048), mime=True)
        file_stream.seek(0)
        
        if mime not in ALLOWED_MIME_TYPES:
            return False, f"File content type {mime} not allowed"
        
        return True, None
    except ImportError:
        # python-magic not installed, skip content validation
        logger.warning("python-magic not installed, skipping MIME type validation")
        return True, None
    except Exception as e:
        logger.error(f"Error validating file content: {e}")
        return False, "Error validating file content"

def validate_upload(file, max_size: int = MAX_FILE_SIZE) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Comprehensive file upload validation.
    
    Returns:
        (is_valid, sanitized_filename or None, error_message or None)
    """
    if not file:
        return False, None, "No file provided"
    
    # Validate filename
    valid, result = validate_filename(file.filename)
    if not valid:
        return False, None, result
    
    safe_filename = result
    
    # Validate size
    valid, error = validate_file_size(file.stream, max_size)
    if not valid:
        return False, None, error
    
    # Validate content
    valid, error = validate_file_content(file.stream)
    if not valid:
        return False, None, error
    
    return True, safe_filename, None

def validate_production_config() -> None:
    """
    Validate critical configuration in production environment.
    
    Raises SystemExit if critical config is missing.
    """
    env = os.getenv('ENVIRONMENT', 'development').lower()
    
    if env == 'production':
        errors = []
        
        # Check SECRET_KEY
        if not os.getenv('SECRET_KEY'):
            errors.append("SECRET_KEY must be set in production")
        
        # Check gateway auth
        if not os.getenv('CLAWDBOT_AUTH_TOKEN'):
            errors.append("CLAWDBOT_AUTH_TOKEN must be set")
        
        # Check CORS origins
        if not os.getenv('CORS_ORIGINS'):
            logger.warning("CORS_ORIGINS not set in production - using localhost only")
        
        # Check Clerk auth if required
        if os.getenv('CANVAS_REQUIRE_AUTH', '').lower() == 'true':
            if not os.getenv('CLERK_PUBLISHABLE_KEY'):
                errors.append("CLERK_PUBLISHABLE_KEY required when CANVAS_REQUIRE_AUTH=true")
            if not os.getenv('ALLOWED_USER_IDS'):
                logger.warning("ALLOWED_USER_IDS not set - any valid Clerk user can access")
        
        if errors:
            for error in errors:
                logger.critical(f"Configuration error: {error}")
            raise SystemExit("Critical configuration missing. See logs above.")
        
        logger.info("Production configuration validated successfully")

def get_security_headers() -> dict:
    """
    Get security headers for all responses.
    
    Returns:
        Dictionary of security headers
    """
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'SAMEORIGIN',
        'X-XSS-Protection': '1; mode=block',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'geolocation=(), microphone=(self), camera=(self)'
    }

def get_csp_policy(nonce: str, page_type: str = 'main') -> str:
    """
    Get Content Security Policy based on page type.
    
    Args:
        nonce: CSP nonce for inline scripts
        page_type: 'main' or 'canvas'
    
    Returns:
        CSP policy string
    """
    if page_type == 'canvas':
        # More permissive for canvas iframe (user-generated content)
        # Still includes nonce requirement
        return (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://games.jam-bot.com blob:; "
            f"style-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"img-src 'self' data: blob: https:; "
            f"font-src 'self' data: https://cdn.jsdelivr.net; "
            f"connect-src 'self' ws: wss: https:; "
            f"media-src 'self' blob: https:; "
            f"object-src 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"frame-ancestors 'self'; "
            f"upgrade-insecure-requests;"
        )
    else:
        # Strict CSP for main application
        return (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"style-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            f"img-src 'self' data: blob: https:; "
            f"font-src 'self' data: https://cdn.jsdelivr.net; "
            f"connect-src 'self' ws: wss: https:; "
            f"media-src 'self' blob:; "
            f"object-src 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"frame-ancestors 'none'; "
            f"upgrade-insecure-requests;"
        )
