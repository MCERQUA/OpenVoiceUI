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

from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sock import Sock
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger(__name__)

# Match static_files.py and nginx (100 MB) — bulk uploads need full limit
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


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
        r'^chrome-extension://',   # JamBot Browser Companion extension
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
            '/uploads/',   # uploaded/generated files — served from VPS filesystem (no secrets)
            '/static/',    # PWA icons, app icons
            '/pages/',     # canvas pages — served without auth (CANVAS_REQUIRE_AUTH opt-in)
            '/api/canvas/',  # canvas API — creation, manifest, context (no per-user auth needed)
            '/api/upload',    # file upload — canvas pages lose Clerk JWT on long sessions; files are non-sensitive
            '/api/uploads',   # uploads list — files are already public at /uploads/, listing is fine
            '/api/profiles',  # read-only profile config — loaded before Clerk init
            '/api/plugins',   # Plugin system — asset loading, install/uninstall
            '/plugins/',      # Plugin static assets — face scripts, CSS, previews
            '/api/chat',      # LLM proxy (Groq) — used by canvas pages for inline AI
            '/api/tts/',      # TTS provider list — loaded before Clerk init
            '/api/theme',     # theme config — loaded before Clerk init
            '/api/music',     # music track list — loaded before Clerk init
            '/api/faces',     # face list — loaded before Clerk init
            '/api/icons/',    # icon library + generated icons — static images, no secrets
            '/api/suno',      # Suno song generation — status polling + song list (no secrets)
            '/registry/',     # Pinokio registry check-in — accessed by Pinokio, not logged-in user
            '/checkpoints/',  # Pinokio snapshot endpoint — called from /registry/checkin page JS
            '/openclaw-ui/',  # OpenClaw Control UI SPA + assets — proxied to internal gateway
        )
        _PUBLIC_EXACT = {
            '/',           # main page — hosts the Clerk login gate itself
            '/pi',         # Pi-optimized page — same login gate, different entry point
            '/health/live',
            '/health/ready',
            '/api/auth/check',      # Auth check endpoint — does its own token verification
            '/api/suno/callback',   # Suno's servers POST here from external IPs (no Clerk token)
            '/api/version',         # Version check — loaded before auth to show update banner
            '/sw.js',           # PWA service worker — browser fetches this before auth
            '/manifest.json',   # PWA manifest — browser fetches this before auth
            '/favicon.ico',     # Browser favicon request — before auth
            '/ws/clawdbot',     # WebSocket — browsers can't send Clerk token in WS headers;
                                # handler secures itself via CLAWDBOT_AUTH_TOKEN to the gateway
            '/openclaw-ui',     # WebSocket upgrade for OpenClaw Control UI proxy (no trailing slash)
        }

        # Detect whether Clerk auth is configured at startup.
        # Auth is opt-in: when no key is set, all routes are accessible (README § Authentication).
        _clerk_key = (os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip()

        # Internal agent API key — allows openclaw agents to call Flask APIs
        # without a Clerk JWT. Set AGENT_API_KEY in the container .env.
        _agent_api_key = os.getenv('AGENT_API_KEY', '').strip()

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
            if path.startswith('/pages/') or path.startswith('/canvas-proxy') or path.startswith('/website-dev'):
                return

            # Internal agent API key — openclaw agents calling Flask APIs from inside Docker network
            if _agent_api_key and request.headers.get('X-Agent-Key') == _agent_api_key:
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

    # ── JSON error handler for 413 (file too large) ────────────────────────
    @app.errorhandler(413)
    def handle_413(e):
        return jsonify({'error': 'File too large (100 MB max)'}), 413

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
            'Permissions-Policy', 'camera=(self), microphone=*, geolocation=(), pointer-lock=*'
        )
        response.headers.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://*.clerk.accounts.dev https://*.jam-bot.com; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://img.clerk.com https://images.clerk.dev https://*.clerk.accounts.dev https://lh3.googleusercontent.com https://avatars.githubusercontent.com https://bhaleyart.github.io; "
            "media-src 'self' blob:; "
            "connect-src 'self' wss: https:; "
            "frame-src 'self' https://*.clerk.accounts.dev https://*.jam-bot.com https:; "
            "worker-src 'self' blob:"
        )
        return response

    # ── CDN cache cleanup (MUST run after flask_cors) ──────────────────────
    # flask_cors adds Vary:Origin + Access-Control-* to ALL responses, which
    # causes Cloudflare to mark them cf-cache-status:DYNAMIC (uncacheable).
    # Canvas media files don't need CORS — strip those headers so CDN caches them.
    # Inserted at position 0 in after_request list so it runs LAST in LIFO order.
    def _strip_cdn_blocking_headers(response):
        _media_exts = ('.mp4', '.webm', '.mp3', '.wav', '.ogg', '.png', '.jpg',
                       '.jpeg', '.gif', '.svg', '.webp', '.pdf')
        if request.path.startswith('/pages/') and any(request.path.endswith(e) for e in _media_exts):
            for h in ['Vary', 'Access-Control-Allow-Origin',
                      'Access-Control-Allow-Credentials',
                      'Content-Security-Policy', 'X-Frame-Options',
                      'Permissions-Policy', 'X-XSS-Protection',
                      'Referrer-Policy']:
                response.headers.pop(h, None)
        return response
    app.after_request_funcs.setdefault(None, []).insert(0, _strip_cdn_blocking_headers)

    return app, sock
