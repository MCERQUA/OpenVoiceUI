"""
Issue reporting — user-submitted bug/feedback reports saved to disk.

POST /api/report-issue   — save an issue report
GET  /api/report-issues  — list recent reports
"""

import json
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from services.paths import RUNTIME_DIR

report_issue_bp = Blueprint('report_issue', __name__)

REPORTS_DIR = RUNTIME_DIR / 'issue-reports'


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

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H-%M-%S')
    filename = f'{date_str}_{time_str}_{issue_type}.json'
    filepath = REPORTS_DIR / filename

    # Handle the (unlikely) same-second collision
    if filepath.exists():
        filepath = REPORTS_DIR / f'{date_str}_{time_str}_{issue_type}_2.json'

    filepath.write_text(json.dumps(report, indent=2))

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
