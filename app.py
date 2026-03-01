"""
Flask application factory for ai-eyes2.

Usage:
    from app import create_app
    app, sock = create_app()

This factory pattern allows:
- Blueprint registration (Phase 2 tasks P2-T2 through P2-T8)
- Test isolation via config_override
- Clean extension initialization

ADR-009 (simple manager pattern): factory returns app + sock tuple so
server.py module-level decorators (@app.route, @sock.route) keep working.
"""
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sock import Sock
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger(__name__)

# Reduced from 100 MB — audio uploads don't need more than 25 MB (P7-T3 security audit)
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def create_app(config_override: dict = None):
    """
    Create and configure the Flask application.

    Args:
        config_override: Optional dict of Flask config values to apply.
                         Primarily used in tests to inject TESTING=True etc.

    Returns:
        tuple: (app, sock) — configured Flask app and Flask-Sock instance.
    """
    app = Flask(
        __name__,
        # Serve static files from project root (index.html etc.) via explicit routes
        static_folder=None,
    )

    # Core Flask config
    secret_key = os.getenv('SECRET_KEY')
    if not secret_key:
        import secrets as _secrets
        secret_key = _secrets.token_hex(32)
        logger.warning(
            'No SECRET_KEY set — generated a random key for this session. '
            'Sessions will NOT persist across restarts. '
            'Set SECRET_KEY in .env for production.'
        )
    app.config['SECRET_KEY'] = secret_key
    app.config['MAX_CONTENT_LENGTH'] = _MAX_UPLOAD_BYTES

    # Apply test / caller overrides last so they take precedence
    if config_override:
        app.config.update(config_override)

    # Trust one level of X-Forwarded-* headers (nginx / reverse proxy).
    # Without this, request.remote_addr is always 127.0.0.1 behind nginx,
    # breaking per-IP rate limiting (all users share one bucket).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Initialize Flask-Sock for WebSocket support
    sock = Sock(app)

    # Configure CORS — allow your production domain and any localhost port for dev
    # Anchored regex prevents partial matches like http://localhostX.evil.com
    # Add extra origins via CORS_ORIGINS env var (comma-separated, e.g. https://yourdomain.com)
    _extra_origins = [o.strip() for o in os.getenv('CORS_ORIGINS', '').split(',') if o.strip()]
    CORS(app, origins=[
        r'^http://localhost:\d+$',
        *_extra_origins,
    ], supports_credentials=True)

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Per-IP limits protect expensive endpoints from abuse.
    # Override default via RATELIMIT_DEFAULT env var (e.g. "100 per minute").
    # Disable for tests: config_override={'RATELIMIT_ENABLED': False}.
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[os.getenv('RATELIMIT_DEFAULT', '200 per minute')],
        storage_uri='memory://',
    )
    app.limiter = limiter

    # ── Clerk auth gate ────────────────────────────────────────────────────────
    # Auth is only active when CLERK_PUBLISHABLE_KEY is set in .env.
    # Without it, the app runs fully open (single-user / local mode).
    _clerk_key = (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip()
    _auth_enabled = bool(_clerk_key)

    if not _auth_enabled:
        logger.info('No CLERK_PUBLISHABLE_KEY set — auth disabled (local mode)')
    else:
        # Routes that never require authentication:
        _PUBLIC_PREFIXES = (
            '/src/',       # static JS/CSS (needed to render the login screen)
            '/sounds/',
            '/music/',
            '/images/',    # canvas images (individual pages check their own flag)
            '/static/',    # PWA icons, app icons
            '/pages/',     # canvas pages — served without auth (CANVAS_REQUIRE_AUTH opt-in)
            '/api/canvas/',  # canvas API — creation, manifest, context (no per-user auth needed)
        )
        _PUBLIC_EXACT = {
            '/',           # main page — hosts the Clerk login gate itself
            '/pi',         # Pi-optimized page — same login gate, different entry point
            '/health/live',
            '/health/ready',
            '/api/auth/check',      # Auth check endpoint — does its own token verification
            '/api/suno/callback',   # Suno's servers POST here from external IPs (no Clerk token)
            '/sw.js',           # PWA service worker — browser fetches this before auth
            '/manifest.json',   # PWA manifest — browser fetches this before auth
            '/favicon.ico',     # Browser favicon request — before auth
            '/ws/clawdbot',     # WebSocket — browsers can't send Clerk token in WS headers;
                                # handler secures itself via CLAWDBOT_AUTH_TOKEN to the gateway
        }

        # Detect whether Clerk auth is configured at startup.
        # Auth is opt-in: when no key is set, all routes are accessible (README § Authentication).
        _clerk_key = (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip()

        @app.before_request
        def require_auth():
            """Block unauthenticated requests to all non-exempt routes.

            Skipped entirely when Clerk is not configured (no CLERK_PUBLISHABLE_KEY),
            matching the documented opt-in auth behaviour.
            """
            if not _clerk_key:
                return  # No Clerk configured — open access (single-user / self-hosted)

            path = request.path

            # Always allow health probes and static assets
            if path in _PUBLIC_EXACT:
                return
            if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                return
            # Canvas pages and images have their own auth logic (public flag)
            # handled inside canvas_bp — let them through here
            if path.startswith('/pages/') or path.startswith('/canvas-proxy') or path.startswith('/website-dev') or path.startswith('/dashboard-api'):
                return

            from services.auth import get_token_from_request, verify_clerk_token
            token = get_token_from_request()
            user_id = verify_clerk_token(token) if token else None

            if not user_id:
                # For API calls return JSON 401; for page navigations redirect to /
                if path.startswith('/api/') or request.headers.get('X-Requested-With'):
                    return jsonify({'error': 'Unauthorized', 'code': 'auth_required'}), 401
                # HTML page request — redirect to root (login gate)
                return redirect('/')

    # ── Security headers (P7-T3 security audit) ──────────────────────────────
    @app.after_request
    def add_security_headers(response):
        """Add defensive HTTP security headers to every response."""
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('X-XSS-Protection', '1; mode=block')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        # Allow microphone and camera for voice/vision app; block geolocation
        response.headers.setdefault(
            'Permissions-Policy', 'camera=(self), microphone=(self), geolocation=()'
        )
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://img.clerk.com https://images.clerk.dev https://lh3.googleusercontent.com https://avatars.githubusercontent.com; "
            "media-src 'self' blob:; "
            "connect-src 'self' wss: https:; "
            "frame-src 'self' https://*.clerk.accounts.dev; "
            "worker-src 'self' blob:"
        )
        return response

    # ── Swagger / OpenAPI documentation ───────────────────────────────────────
    # Serves interactive API docs at /apidocs
    # OpenAPI spec is loaded from openapi.yaml in project root
    openapi_path = Path(__file__).parent / 'openapi.yaml'
    if openapi_path.exists():
        try:
            from flasgger import Swagger
            swagger = Swagger(
                app,
                template_file=str(openapi_path),
                config={
                    'headers': [],
                    'specs': [{'endpoint': 'apispec', 'route': '/apispec.json'}],
                    'swagger_ui': True,
                    'specs_route': '/apidocs',
                }
            )
            logger.info('Swagger UI available at /apidocs')
        except ImportError:
            logger.warning('flasgger not installed — Swagger UI disabled. Run: pip install flasgger')
        except Exception as exc:
            logger.warning(f'Failed to initialize Swagger UI: {exc}')
    else:
        logger.debug(f'OpenAPI spec not found at {openapi_path} — Swagger UI disabled')

    return app, sock
