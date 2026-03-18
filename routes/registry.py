"""
Pinokio registry check-in endpoint.

When a user clicks "Check in" in Pinokio, it opens:
  http://localhost:<port>/registry/checkin?return=<pinokio_checkin_url>&app=<slug>&registry=<api>&...#token=<one-time-token>

The #token is in the URL hash — it never reaches Flask. Client-side JS reads it,
calls POST /checkpoints/snapshot with X-Registry-Token header, which posts the
token + current git hash to api.pinokio.co, then redirects back to beta.pinokio.co.
"""

import json
import os
import subprocess
import time

import requests
from flask import Blueprint, Response, jsonify, request

registry_bp = Blueprint('registry', __name__)


@registry_bp.route('/registry/checkin')
def registry_checkin():
    return_url = request.args.get('return', '')
    repo       = request.args.get('repo', 'OpenVoiceUI')
    app_slug   = request.args.get('app', '')
    registry   = request.args.get('registry', 'https://api.pinokio.co')

    # Sanitize return_url — only allow http/https redirects
    if return_url and not return_url.startswith(('http://', 'https://')):
        return_url = ''

    # Quick health check
    try:
        from services.health import health_checker
        readiness = health_checker.readiness()
        is_healthy = readiness.healthy
        status_msg = readiness.message
        uptime = int(time.time() - health_checker.start_time)
        uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
    except Exception:
        is_healthy = True
        status_msg = "Server is running"
        uptime_str = "unknown"

    status_icon  = "&#10004;" if is_healthy else "&#9888;"
    status_color = "#4ade80" if is_healthy else "#fbbf24"

    # Pass values to JS safely via JSON encoding
    js_config = json.dumps({
        'returnUrl': return_url,
        'registry':  registry,
        'appSlug':   app_slug,
        'repo':      repo,
    })

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OpenVoiceUI — Install Verified</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .card {{
            background: #151520;
            border: 1px solid #2a2a3a;
            border-radius: 16px;
            padding: 48px;
            max-width: 520px;
            width: 90%;
            text-align: center;
        }}
        .logo {{ font-size: 48px; margin-bottom: 8px; }}
        h1 {{ font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #fff; }}
        .subtitle {{ color: #888; font-size: 14px; margin-bottom: 32px; }}
        .status-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            background: #1a1a28;
            border-radius: 8px;
            margin-bottom: 8px;
            font-size: 14px;
        }}
        .status-row .label {{ color: #888; }}
        .status-row .value {{ font-weight: 500; }}
        .status-row .value.ok {{ color: {status_color}; }}
        #status-msg {{
            margin-top: 28px;
            font-size: 14px;
            color: #888;
            min-height: 40px;
        }}
        .checkin-btn {{
            display: inline-block;
            margin-top: 28px;
            padding: 14px 32px;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: #fff;
            text-decoration: none;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.15s, box-shadow 0.15s;
        }}
        .checkin-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(99, 102, 241, 0.3);
        }}
        .error {{ color: #f87171; margin-top: 16px; font-size: 13px; }}
        .hint {{ color: #666; font-size: 12px; margin-top: 16px; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="logo">{status_icon}</div>
        <h1>OpenVoiceUI is Running</h1>
        <p class="subtitle">Install verified and healthy</p>

        <div class="status-row">
            <span class="label">Server</span>
            <span class="value ok">{status_msg}</span>
        </div>
        <div class="status-row">
            <span class="label">Uptime</span>
            <span class="value">{uptime_str}</span>
        </div>
        <div class="status-row">
            <span class="label">Repository</span>
            <span class="value">{repo}</span>
        </div>

        <div id="status-msg">Completing check-in&hellip;</div>
    </div>

    <script>
    (function() {{
        var cfg = {js_config};

        // Read token from URL hash (#token=...) — never sent to server
        var hash = window.location.hash.slice(1);
        var token = new URLSearchParams(hash).get('token');

        // Clear token from URL bar immediately
        if (token) history.replaceState(null, '', window.location.pathname + window.location.search);

        var msgEl = document.getElementById('status-msg');

        function showError(msg) {{
            msgEl.innerHTML = '<span class="error">' + msg + '</span>';
            if (cfg.returnUrl) {{
                var sep = cfg.returnUrl.includes('?') ? '&' : '?';
                setTimeout(function() {{
                    window.location.href = cfg.returnUrl + sep + 'error=checkin_failed';
                }}, 3000);
            }}
        }}

        function showManualBtn() {{
            if (cfg.returnUrl) {{
                msgEl.innerHTML = '<a href="' + cfg.returnUrl + '" class="checkin-btn">Complete Check-in on Pinokio</a>' +
                    '<p class="hint">Click to confirm your install on the Pinokio community page.</p>';
            }} else {{
                msgEl.innerHTML = '<p class="hint">Check-in confirmed locally (no return URL).</p>';
            }}
        }}

        if (!token) {{
            // No token — fall back to manual button (e.g. user navigated here directly)
            showManualBtn();
            return;
        }}

        // The app just proves it's running by redirecting back to beta.pinokio.co
        // with the token — Pinokio handles the actual registry API call with its session.
        msgEl.innerHTML = '<span style="color:#4ade80">&#10004; Install verified!</span><p class="hint">Redirecting back to Pinokio&hellip;</p>';

        if (cfg.returnUrl) {{
            var sep = cfg.returnUrl.includes('?') ? '&' : '?';
            // Pass token back so beta.pinokio.co can complete the checkin server-side
            setTimeout(function() {{
                window.location.href = cfg.returnUrl + sep + 'token=' + encodeURIComponent(token) + '&ok=1';
            }}, 1000);
        }} else {{
            msgEl.innerHTML += '<p class="hint">No return URL — check-in confirmed locally.</p>';
        }}
    }})();
    </script>
</body>
</html>'''

    return Response(html, content_type='text/html')


def _get_commit_hash() -> str:
    """Return the current git commit hash using the best available method."""
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. Try git directly (works on host / dev installs)
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=app_root, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass

    # 2. GIT_COMMIT env var baked in at Docker build time
    env_hash = os.getenv('GIT_COMMIT', '').strip()
    if env_hash:
        return env_hash

    # 3. GIT_HASH file written during Docker build (via `git rev-parse HEAD > /app/GIT_HASH`)
    hash_file = os.path.join(app_root, 'GIT_HASH')
    if os.path.exists(hash_file):
        return open(hash_file).read().strip()

    # 4. Derive a stable hash from package.json version
    try:
        import hashlib
        pkg = os.path.join(app_root, 'package.json')
        version = json.load(open(pkg)).get('version', '0.0.0')
        return hashlib.sha1(f'openvoiceui-{version}'.encode()).hexdigest()
    except Exception:
        pass

    import hashlib
    return hashlib.sha1(b'openvoiceui-unknown').hexdigest()


@registry_bp.route('/checkpoints/snapshot', methods=['POST'])
def checkpoints_snapshot():
    """
    Called by the /registry/checkin page JS.
    Creates a git snapshot of the app and publishes it to api.pinokio.co.
    """
    token    = request.headers.get('X-Registry-Token', '').strip()
    registry = request.args.get('registry', 'https://api.pinokio.co').rstrip('/')
    publish  = request.args.get('publish', '0') == '1'
    repo_url = request.args.get('repo', 'https://github.com/MCERQUA/OpenVoiceUI')
    app_slug = request.args.get('app', 'github-com-mcerqua-openvoiceui')

    if not token:
        return jsonify({'ok': False, 'error': 'missing_token'}), 400

    # Get current git hash — try several strategies since git may not be in the container
    commit_hash = _get_commit_hash()

    created = {'hash': commit_hash}

    if not publish:
        return jsonify({'ok': True, 'created': created})

    # Publish to Pinokio registry
    # Include checkpoint + system metadata that the API expects
    import platform as _platform
    post_body = {
        'hash':       commit_hash,
        'visibility': 'public',
        'checkpoint': {
            'repoUrl': repo_url,
            'appSlug': app_slug,
        },
        'system': {
            'platform': _platform.system().lower(),
            'arch':     _platform.machine(),
        },
    }

    try:
        resp = requests.post(
            f'{registry}/checkpoints',
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type':  'application/json',
            },
            json=post_body,
            timeout=15,
        )
    except requests.RequestException as e:
        return jsonify({'ok': False, 'error': 'publish_failed', 'detail': str(e)}), 502

    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        import logging
        logging.getLogger(__name__).warning(
            f'Pinokio registry publish failed: HTTP {resp.status_code} — {detail}'
        )
        return jsonify({'ok': False, 'error': 'publish_failed', 'detail': detail,
                        'status': resp.status_code}), 502

    try:
        pub_data = resp.json()
    except Exception:
        pub_data = {}

    return jsonify({
        'ok':      True,
        'created': created,
        'publish': {'ok': True, 'hash': commit_hash, **pub_data},
    })
