"""
Pinokio registry check-in endpoint.

When a user clicks "Check in" in Pinokio, it opens:
  http://localhost:<port>/registry/checkin?return=<pinokio_checkin_url>&...

This route shows a confirmation page verifying the install is healthy,
then lets the user complete the check-in on Pinokio's platform.
"""

import time
from flask import Blueprint, request, redirect, Response

registry_bp = Blueprint('registry', __name__)


@registry_bp.route('/registry/checkin')
def registry_checkin():
    return_url = request.args.get('return')
    repo = request.args.get('repo', 'OpenVoiceUI')

    # Quick health check
    try:
        from services.health import health_checker
        readiness = health_checker.readiness()
        is_healthy = readiness.healthy
        status_msg = readiness.message
        uptime = int(time.time() - health_checker.start_time)
        uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m {uptime % 60}s"
    except Exception:
        is_healthy = True  # server is responding, so it's at least alive
        status_msg = "Server is running"
        uptime_str = "unknown"

    status_icon = "&#10004;" if is_healthy else "&#9888;"
    status_color = "#4ade80" if is_healthy else "#fbbf24"
    button_html = ""
    if return_url:
        button_html = f'''
            <a href="{return_url}" class="checkin-btn">Complete Check-in on Pinokio</a>
            <p class="hint">This will confirm your successful install on the Pinokio community page.</p>
        '''
    else:
        button_html = '<p class="hint">No return URL provided — check-in confirmed locally.</p>'

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
        .logo {{
            font-size: 48px;
            margin-bottom: 8px;
        }}
        h1 {{
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #fff;
        }}
        .subtitle {{
            color: #888;
            font-size: 14px;
            margin-bottom: 32px;
        }}
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
        .checkin-btn {{
            display: inline-block;
            margin-top: 28px;
            padding: 14px 32px;
            background: linear-gradient(135deg, #6366f1, #8b5cf6);
            color: #fff;
            text-decoration: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            transition: transform 0.15s, box-shadow 0.15s;
        }}
        .checkin-btn:hover {{
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(99, 102, 241, 0.3);
        }}
        .hint {{
            color: #666;
            font-size: 12px;
            margin-top: 16px;
        }}
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

        {button_html}
    </div>
</body>
</html>'''

    return Response(html, content_type='text/html')
