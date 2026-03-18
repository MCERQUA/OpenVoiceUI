"""
Issue reporting — user-submitted bug/feedback reports.

POST /api/report-issue   — save an issue report locally + forward to feedback service
GET  /api/report-issues  — list recent local reports

For public installs (no Clerk auth configured), reports are also sent to
https://feedback.openvoiceui.com/api/report so the dev team can see them.
This is fire-and-forget — local save always succeeds regardless.
"""

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

import requests as http_requests
from flask import Blueprint, jsonify, request

from services.paths import RUNTIME_DIR

logger = logging.getLogger(__name__)

report_issue_bp = Blueprint('report_issue', __name__)

REPORTS_DIR = RUNTIME_DIR / 'issue-reports'

FEEDBACK_URL = 'https://feedback.openvoiceui.com/api/report'


def _is_public_install() -> bool:
    """True if this is a public/Pinokio install (no Clerk auth configured)."""
    return not os.environ.get('CLERK_PUBLISHABLE_KEY', '').strip()


def _forward_to_feedback_service(report: dict):
    """Fire-and-forget POST to the public feedback service."""
    try:
        resp = http_requests.post(
            FEEDBACK_URL,
            json=report,
            timeout=10,
            headers={'Content-Type': 'application/json'},
        )
        if resp.ok:
            logger.info('Feedback forwarded to %s', FEEDBACK_URL)
        else:
            logger.warning('Feedback forward failed: %s %s', resp.status_code, resp.text[:200])
    except Exception as e:
        logger.warning('Feedback forward error: %s', e)


@report_issue_bp.route('/api/report-issue', methods=['POST'])
def submit_issue():
    data = request.get_json(force=True, silent=True) or {}

    issue_type = (data.get('type') or 'other').strip()[:50]
    description = (data.get('description') or '').strip()[:2000]
    context = data.get('context') or {}

    if not description:
        return jsonify({'error': 'Description required'}), 400

    now = datetime.now()
    report = {
        'ts': now.isoformat(),
        'type': issue_type,
        'description': description,
        'context': context,
        'ua': request.headers.get('User-Agent', ''),
    }

    # Always save locally
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H-%M-%S')
    filename = f'{date_str}_{time_str}_{issue_type}.json'
    filepath = REPORTS_DIR / filename

    if filepath.exists():
        filepath = REPORTS_DIR / f'{date_str}_{time_str}_{issue_type}_2.json'

    filepath.write_text(json.dumps(report, indent=2))

    # Forward to public feedback service (fire-and-forget, non-blocking)
    if _is_public_install():
        threading.Thread(
            target=_forward_to_feedback_service,
            args=(report,),
            daemon=True,
        ).start()

    return jsonify({'ok': True, 'saved': filename})


@report_issue_bp.route('/api/report-issues', methods=['GET'])
def list_issues():
    """Return last N issue reports, newest first."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob('*.json'), reverse=True)[:50]
    reports = []
    for f in files:
        try:
            reports.append(json.loads(f.read_text()))
        except Exception:
            pass
    return jsonify(reports)
