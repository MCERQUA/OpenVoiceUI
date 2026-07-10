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

from flask import Flask, g, jsonify, redirect, request
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

    # Privileged surfaces — require an admin user (services.auth.ADMIN_USER_IDS),
    # not just any allowlisted tenant user. ALLOWED_USER_IDS gates the voice app;
    # these endpoints can rewrite vault credentials, openclaw.json (incl. provider
    # baseUrl/apiKey), agent workspace files, and inject into the live agent session.
    _ADMIN_ONLY_PREFIXES = (
        '/api/admin/',
        '/api/vault/',      # oauth callback is exempted via _PUBLIC_PREFIXES below
        '/api/workspace/',
        '/api/refactor/',
        '/api/server-stats',
        '/api/plugins/',    # install/uninstall/restart/config (assets exempted below)
        '/api/plugins',     # bare list endpoint
    )

    if not _auth_enabled:
        logger.info('No CLERK_PUBLISHABLE_KEY set — auth disabled (local mode)')
        # Hosted tenants always set CANVAS_REQUIRE_AUTH=true (JamBot .env template).
        # If the Clerk key goes missing there (broken .platform-keys.env mount),
        # the admin surface must fail CLOSED — previously it failed open and every
        # /api/admin, /api/vault and RPC-proxy endpoint became unauthenticated.
        # Local / self-hosted installs (no CANVAS_REQUIRE_AUTH) keep the documented
        # open-access behaviour.
        if os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true':
            logger.error('CANVAS_REQUIRE_AUTH=true but no Clerk key — admin surface fail-closed')

            @app.before_request
            def block_admin_unconfigured():
                path = request.path
                if (path == '/admin' or path.startswith('/admin/')
                        or path == '/src/admin.html'
                        or any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES)):
                    return jsonify({
                        'error': 'Admin surface disabled: auth required but Clerk is not configured',
                        'code': 'admin_auth_unconfigured',
                    }), 503
    else:
        # Routes that never require authentication:
        _PUBLIC_PREFIXES = (
            '/src/',       # static JS/CSS (needed to render the login screen)
            '/sounds/',
            '/music/',
            '/generated_music/',  # AI-generated songs — shareable download links (texted/emailed
                                  # to recipients with no app login). Non-sensitive (client's own
                                  # jingles); the /api/music track list is already public anyway.
            '/images/',    # canvas images (individual pages check their own flag)
            '/uploads/',   # uploaded/generated files — served from VPS filesystem (no secrets)
            '/canvas-data/',  # processed-song media (audio stems) + fixtures for the Suno Studio editor — non-sensitive, served like /uploads/ (added 2026-06-25)
            '/static/',    # PWA icons, app icons
            '/pages/',     # canvas pages — served without auth (CANVAS_REQUIRE_AUTH opt-in)
            '/api/canvas/',  # canvas API — creation, manifest, context (no per-user auth needed)
            '/api/upload',    # file upload — canvas pages lose Clerk JWT on long sessions; files are non-sensitive
            '/api/uploads',   # uploads list — files are already public at /uploads/, listing is fine
            '/api/profiles',  # read-only profile config — loaded before Clerk init
            '/api/plugins/assets',  # Plugin face scripts/CSS — fetched by index.html before login.
                                    # All other /api/plugins routes (install/uninstall/restart/config)
                                    # are state-changing admin operations and require admin auth below.
            '/api/vault/oauth/callback/',  # OAuth callbacks — redirected from external providers
            '/plugins/',      # Plugin static assets — face scripts, CSS, previews
            '/api/chat',      # LLM proxy (Groq) — used by canvas pages for inline AI
            '/api/tts/',      # TTS provider list — loaded before Clerk init
            '/api/stt/',      # STT endpoints (Deepgram token, Groq, local) — mic audio only, no secrets exposed
            '/api/theme',     # theme config — loaded before Clerk init
            '/api/music',     # music track list — loaded before Clerk init
            '/api/faces',     # face list — loaded before Clerk init
            '/api/custom-faces', # custom face manifest + CRUD — loaded by face picker
            '/faces/custom/', # custom face HTML — loaded in iframe by face-box
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
            '/api/config',          # Public client config (Clerk publishable key) — admin.html bootstrap
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

            # CSRF guard: state-changing browser requests must come from our own
            # origin. Cookie (__session) auth makes cross-site request forgery
            # possible; a mismatched Origin header is the reliable browser signal.
            # Non-browser clients (agents, server-to-server callbacks like Suno's)
            # send no Origin header and pass through untouched.
            if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
                _origin = request.headers.get('Origin', '')
                if _origin and not request.headers.get('Authorization', '').startswith('Bearer '):
                    from urllib.parse import urlparse
                    _origin_host = urlparse(_origin).netloc
                    if _origin_host and _origin_host != request.host:
                        return jsonify({'error': 'Cross-origin request blocked', 'code': 'csrf_blocked'}), 403

            # Always allow health probes and static assets
            if path in _PUBLIC_EXACT:
                return
            # /src/ is public (login-screen assets) EXCEPT the admin shell itself —
            # /src/admin.html must go through the same admin gate as /admin.
            if path != '/src/admin.html' and any(path.startswith(p) for p in _PUBLIC_PREFIXES):
                return
            # Canvas pages and images have their own auth logic (public flag)
            # handled inside canvas_bp — let them through here
            if path.startswith('/pages/') or path.startswith('/canvas-proxy') or path.startswith('/website-dev'):
                return

            # Admin-only surfaces are NEVER reachable with the internal agent key.
            # The agent key authorizes Docker-network service calls (canvas,
            # conversation, /api/session/reset, song-tagger, etc.), but a
            # prompt-injected agent must NOT be able to rewrite vault creds /
            # openclaw.json provider baseUrl / workspace files, or inject into the
            # live session via the admin RPC proxy. Without this carve-out the
            # agent-key `return` short-circuited BEFORE the admin gate below,
            # defeating the a957449 admin-authz lockdown. Same admin test as the
            # Clerk-path gate (single source of truth).
            _is_admin_path = (
                path == '/admin' or path.startswith('/admin/')
                or path == '/src/admin.html'
                or any(path.startswith(p) for p in _ADMIN_ONLY_PREFIXES)
            )

            # Internal agent API key — openclaw agents calling NON-admin Flask APIs
            # from inside the Docker network.
            if not _is_admin_path and _agent_api_key and request.headers.get('X-Agent-Key') == _agent_api_key:
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

            # Stash for downstream routes (e.g. conversation.py reads g.clerk_user_id
            # to inject a [CURRENT_USER: ...] tag into the gateway message context).
            g.clerk_user_id = user_id

            # Admin authorization — being an allowlisted voice user does NOT grant
            # access to the admin dashboard or privileged APIs.
            if _is_admin_path:
                from services.auth import is_admin_user
                if not is_admin_user(user_id):
                    logger.warning('Admin authz denied: user_id=%s path=%s', user_id, path)
                    if path.startswith('/api/'):
                        return jsonify({'error': 'Admin access required', 'code': 'admin_required'}), 403
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
            # cdn.tailwindcss.com is REQUIRED — canvas pages load Tailwind via the Play CDN; without it
            # in script-src the script is blocked and pages render as raw unstyled text. Do NOT re-strip
            # (recurring regression; memory feedback_canvas_tailwind_cdn). fonts.googleapis.com (style) +
            # fonts.gstatic.com (font) are needed for the Google-font <link>s canvas pages use.
            # 'unsafe-eval' + 'wasm-unsafe-eval' are REQUIRED: cdn.tailwindcss.com is the Tailwind PLAY
            # CDN, which compiles CSS at runtime via eval()/new Function — without unsafe-eval the script
            # loads but generates NO styles → pages still render as raw text. (canvas.py CSP already had
            # these; the global one didn't — that was the real cause of the unstyled SEO dashboard.)
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net https://cdn.tailwindcss.com https://*.clerk.accounts.dev https://*.jam-bot.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' data: https://fonts.gstatic.com; "
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
