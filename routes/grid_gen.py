"""Grid Creator order tracking.

The Grid Creator page records an order here, then triggers this tenant's voice agent
(via a canvas 'speak' shortcut) to mesh-send the job to the Mac creative node under the
agent's own identity — so the Mac knows the client/brand. The Mac generates via ChatGPT
(no per-image cost), writes the finished images into this tenant's uploads/, and drops a
status marker the page polls here.

This route only OWNS the order record + status read-back. It deliberately does NOT touch the
mesh queue (OVU has no mesh mount) — the voice agent is the bridge, per the confirmed contract
in docs/jambot/grid-creator-spec.md.

Order/status files live in the tenant runtime (OVU-writable, host-visible):
  runtime/grid-orders/<request_id>.json         # created by this route (status=queued)
  runtime/grid-orders/<request_id>.status.json  # written by the Mac on progress/completion
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import Blueprint, request, jsonify

from services.paths import RUNTIME_DIR

grid_gen_bp = Blueprint('grid_gen', __name__)

ORDERS_DIR = RUNTIME_DIR / 'grid-orders'
_VALID_TYPES = {'post', 'logo', 'character', 'mascot'}


def _orders_dir() -> Path:
    ORDERS_DIR.mkdir(parents=True, exist_ok=True)
    return ORDERS_DIR


@grid_gen_bp.route('/api/grid-gen/orders', methods=['POST'])
def create_order():
    d = request.get_json(silent=True) or {}
    rid = (d.get('request_id') or '').strip()
    if not rid or '/' in rid or '..' in rid:
        return jsonify({'error': 'valid request_id required'}), 400
    if (d.get('grid_type') or '') not in _VALID_TYPES:
        return jsonify({'error': 'invalid grid_type'}), 400
    order = {
        'request_id': rid,
        'grid_type': d.get('grid_type'),
        'brief': (d.get('brief') or '')[:2000],
        'count': max(1, min(24, int(d.get('count') or 9))),
        'aspect': d.get('aspect') or '1:1',
        'layout': 'composite' if d.get('layout') != 'separate' else 'separate',
        'status': 'queued',
        'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    (_orders_dir() / f'{rid}.json').write_text(json.dumps(order))
    return jsonify({'ok': True, 'request_id': rid})


@grid_gen_bp.route('/api/grid-gen/orders', methods=['GET'])
def list_orders():
    out = []
    d = _orders_dir()
    for f in sorted(d.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.name.endswith('.status.json'):
            continue
        try:
            order = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        # Merge the Mac's status marker if present (it wins on status/files/error).
        marker = d / f"{order.get('request_id')}.status.json"
        if marker.exists():
            try:
                st = json.loads(marker.read_text())
                for k in ('status', 'files', 'error', 'done', 'updated_at'):
                    if k in st:
                        order[k] = st[k]
            except (OSError, json.JSONDecodeError):
                pass
        out.append(order)
    return jsonify(out[:50])
