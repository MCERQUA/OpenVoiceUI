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

        // POST to /checkpoints/snapshot which computes the proper hash and calls api.pinokio.co
        var snapshotUrl = '/checkpoints/snapshot?publish=1'
            + '&registry=' + encodeURIComponent(cfg.registry)
            + '&repo='     + encodeURIComponent(cfg.repo)
            + '&app='      + encodeURIComponent(cfg.appSlug);

        fetch(snapshotUrl, {{
            method: 'POST',
            headers: {{ 'X-Registry-Token': token, 'Content-Type': 'application/json' }},
        }})
        .then(function(r) {{ return r.json().then(function(d) {{ return {{ ok: r.ok, data: d }}; }}); }})
        .then(function(res) {{
            if (!res.ok || !res.data.ok) {{
                var err    = (res.data && res.data.error)  || 'snapshot_failed';
                var detail = (res.data && res.data.detail) || '';
                var msg = 'Check-in failed: ' + err;
                if (detail) msg += ' — ' + (typeof detail === 'object' ? JSON.stringify(detail) : detail);
                showError(msg + '. Redirecting&hellip;');
                if (cfg.returnUrl) {{
                    var sep = cfg.returnUrl.includes('?') ? '&' : '?';
                    setTimeout(function() {{
                        window.location.href = cfg.returnUrl + sep + 'error=' + encodeURIComponent(err);
                    }}, 5000);
                }}
                return;
            }}
            var cpHash = (res.data.created && res.data.created.hash) || '';
            msgEl.innerHTML = '<span style="color:#4ade80">&#10004; Check-in complete!</span><p class="hint">Redirecting back to Pinokio&hellip;</p>';
            if (cfg.returnUrl) {{
                var sep = cfg.returnUrl.includes('?') ? '&' : '?';
                setTimeout(function() {{
                    window.location.href = cfg.returnUrl + sep + 'ok=1' + (cpHash ? '&hash=' + encodeURIComponent(cpHash) : '');
                }}, 1000);
            }}
        }})
        .catch(function(e) {{
            showError('Network error: ' + e.message);
        }});
    }})();
    </script>
</body>
</html>'''

    return Response(html, content_type='text/html')


def _get_git_sha(repo_url: str) -> str:
    """Get the real git commit SHA — try local git first, then GitHub API."""
    app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 1. Local git (works in dev/host installs)
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=app_root, stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        pass

    # 2. GIT_COMMIT env var baked in at Docker build time
    env_sha = os.getenv('GIT_COMMIT', '').strip()
    if env_sha:
        return env_sha

    # 3. GIT_HASH file written during Docker build
    hash_file = os.path.join(app_root, 'GIT_HASH')
    if os.path.exists(hash_file):
        return open(hash_file).read().strip()

    # 4. Fetch from GitHub API — repo_url like https://github.com/OWNER/REPO
    try:
        parts = repo_url.rstrip('/').rstrip('.git').split('/')
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
            resp = requests.get(
                f'https://api.github.com/repos/{owner}/{repo}/commits/main',
                headers={'Accept': 'application/vnd.github.sha'},
                timeout=8,
            )
            if resp.ok:
                return resp.text.strip()
    except Exception:
        pass

    return ''


def _compute_checkpoint_hash(repo_url: str, git_sha: str) -> str:
    """
    Compute the Pinokio checkpoint hash exactly as pinokiod does.
    Format: "sha256:" + SHA256(stableStringify({version, root, repos[]}))
    stableStringify = JSON with keys sorted alphabetically at every level.
    """
    import hashlib

    def stable_stringify(obj) -> str:
        if isinstance(obj, dict):
            pairs = ','.join(
                f'{json.dumps(k)}:{stable_stringify(v)}'
                for k, v in sorted(obj.items())
            )
            return '{' + pairs + '}'
        if isinstance(obj, list):
            return '[' + ','.join(stable_stringify(v) for v in obj) + ']'
        return json.dumps(obj)

    # canonicalRepoUrl() in pinokiod strips protocol, then lowercases the full URL
    canonical_url = repo_url.strip().rstrip('/')
    if canonical_url.lower().endswith('.git'):
        canonical_url = canonical_url[:-4]
    canonical_url = canonical_url.lower()

    canonical = {
        'version': 1,
        'root': canonical_url,
        'repos': [{'commit': git_sha, 'path': '.', 'repo': canonical_url}],
    }
    serialized = stable_stringify(canonical)
    digest = hashlib.sha256(serialized.encode()).hexdigest()
    return f'sha256:{digest}'


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

    # Get real git SHA, then compute the proper Pinokio checkpoint hash
    git_sha = _get_git_sha(repo_url)
    if not git_sha:
        return jsonify({'ok': False, 'error': 'snapshot_failed',
                        'detail': 'Could not determine git commit SHA'}), 500

    checkpoint_hash = _compute_checkpoint_hash(repo_url, git_sha)
    created = {'hash': checkpoint_hash}

    if not publish:
        return jsonify({'ok': True, 'created': created})

    # Publish to Pinokio registry with the exact body format pinokiod uses.
    # The checkpoint URLs must be lowercased to match the registry's canonicalRepoUrl().
    import platform as _platform
    canon_url = repo_url.strip().rstrip('/')
    if canon_url.lower().endswith('.git'):
        canon_url = canon_url[:-4]
    canon_url = canon_url.lower()
    post_body = {
        'hash':       checkpoint_hash,
        'visibility': 'public',
        'checkpoint': {
            'version': 1,
            'root':    canon_url,
            'repos':   [{'commit': git_sha, 'path': '.', 'repo': canon_url}],
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
            f'Pinokio registry publish failed: HTTP {resp.status_code} — {detail} '
            f'(our_sha={git_sha[:12]}, our_hash={checkpoint_hash})'
        )
        return jsonify({'ok': False, 'error': 'publish_failed', 'detail': detail,
                        'status': resp.status_code,
                        '_debug': {'sha': git_sha, 'hash': checkpoint_hash, 'canon_url': canon_url}}), 502

    try:
        pub_data = resp.json()
    except Exception:
        pub_data = {}

    return jsonify({
        'ok':      True,
        'created': created,
        'publish': {'ok': True, 'hash': checkpoint_hash, **pub_data},
    })


@registry_bp.route('/registry/debug')
def registry_debug():
    """
    Debug endpoint — visit http://localhost:<port>/registry/debug to see what
    SHA and checkpoint hash this server would compute for the registry check-in.
    Compare with what Pinokio computed locally.
    """
    repo_url = request.args.get('repo', 'https://github.com/MCERQUA/OpenVoiceUI')

    import platform as _platform
    git_sha = _get_git_sha(repo_url)

    canon_url = repo_url.strip().rstrip('/')
    if canon_url.lower().endswith('.git'):
        canon_url = canon_url[:-4]
    canon_url = canon_url.lower()

    checkpoint_hash = _compute_checkpoint_hash(repo_url, git_sha) if git_sha else ''

    import json as _json

    # Show what the POST body would look like
    post_body = {
        'hash': checkpoint_hash,
        'visibility': 'public',
        'checkpoint': {
            'version': 1,
            'root': canon_url,
            'repos': [{'commit': git_sha, 'path': '.', 'repo': canon_url}],
        },
        'system': {
            'platform': _platform.system().lower(),
            'arch': _platform.machine(),
        },
    }

    import os as _os
    app_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    hash_file = _os.path.join(app_root, 'GIT_HASH')
    git_hash_file_contents = ''
    if _os.path.exists(hash_file):
        git_hash_file_contents = open(hash_file).read().strip()

    return jsonify({
        'git_sha': git_sha,
        'git_sha_source': 'git' if not git_sha else (
            'env' if _os.getenv('GIT_COMMIT', '').strip() == git_sha else (
                'file' if git_hash_file_contents == git_sha else 'github_api'
            )
        ),
        'git_hash_file': git_hash_file_contents,
        'canonical_url': canon_url,
        'checkpoint_hash': checkpoint_hash,
        'post_body': post_body,
    })
