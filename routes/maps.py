"""
Google Maps integration — config + server-side directions proxy.

GET  /api/maps/config
  returns: { google_maps_api_key, ok } — canvas pages fetch this to load the
  Google Maps JS API dynamically (the key never gets baked into page HTML).

POST /api/maps/directions
  body: { origin, destination, waypoints?: [...], mode?: driving|walking|bicycling|transit }
  returns: the Google Maps Directions API (REST) JSON response verbatim.
  Lets the openclaw agent do server-side directions lookups (narrate a route
  over voice without building a canvas page) using the server-side key.

Auth: there is NO per-route decorator here on purpose — the global
`require_auth` before_request gate in app.py covers every /api/* path that is
not in its public allowlists. /api/maps/* is intentionally NOT listed there,
so both endpoints require a Clerk session (browser / canvas authFetch) or the
internal X-Agent-Key (openclaw agent inside the Docker network). Same pattern
as routes/fal.py.

Key: GOOGLE_MAPS_API_KEY lives in /mnt/system/base/.platform-keys.env, which is
mounted into every openvoiceui container via env_file. Read at request time so
a key rotation only needs a container restart, never a code change.
"""
import logging
import os

import requests as http
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)
maps_bp = Blueprint('maps', __name__)

DIRECTIONS_URL = 'https://maps.googleapis.com/maps/api/directions/json'

# Google's accepted travel modes. The UI often says "cycling"; Google says
# "bicycling" — accept both.
_VALID_MODES = {'driving', 'walking', 'bicycling', 'transit'}
_MODE_ALIASES = {'cycling': 'bicycling', 'bike': 'bicycling', 'car': 'driving'}


def _api_key() -> str:
    return os.environ.get('GOOGLE_MAPS_API_KEY', '').strip()


@maps_bp.route('/api/maps/config', methods=['GET'])
def maps_config():
    """Return the Google Maps API key for canvas pages to load the JS API."""
    key = _api_key()
    if not key:
        return jsonify({'ok': False, 'error': 'Maps API key not configured'}), 503
    return jsonify({'ok': True, 'google_maps_api_key': key})


@maps_bp.route('/api/maps/directions', methods=['POST'])
def maps_directions():
    """Server-side Directions API lookup (REST, not JS).

    body: { origin, destination, waypoints?: [str, ...], mode?: str }
    Returns Google's JSON response as-is so callers get routes/legs/steps,
    distance and duration exactly as documented by Google.
    """
    key = _api_key()
    if not key:
        return jsonify({'ok': False, 'error': 'Maps API key not configured'}), 503

    body = request.get_json(silent=True) or {}
    origin = (body.get('origin') or '').strip()
    destination = (body.get('destination') or '').strip()
    if not origin or not destination:
        return jsonify({'ok': False, 'error': 'origin and destination are required'}), 400

    mode = (body.get('mode') or 'driving').strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in _VALID_MODES:
        return jsonify({'ok': False,
                        'error': f"invalid mode '{mode}' — use one of {sorted(_VALID_MODES)}"}), 400

    params = {
        'origin': origin,
        'destination': destination,
        'mode': mode,
        'key': key,
    }

    waypoints = body.get('waypoints') or []
    if waypoints:
        if not isinstance(waypoints, list):
            return jsonify({'ok': False, 'error': 'waypoints must be a list of strings'}), 400
        stops = [str(w).strip() for w in waypoints if str(w).strip()]
        if stops:
            # optimize:true lets Google reorder intermediate stops for the
            # shortest overall route unless the caller pins the order.
            prefix = 'optimize:true|' if body.get('optimize') else ''
            params['waypoints'] = prefix + '|'.join(stops)

    try:
        resp = http.get(DIRECTIONS_URL, params=params, timeout=20)
        data = resp.json()
    except Exception as exc:
        logger.error('maps: directions lookup failed: %s', exc)
        return jsonify({'ok': False, 'error': f'directions lookup failed: {exc}'}), 502

    # Google returns 200 with a status field even on logical failures
    # (NOT_FOUND, ZERO_RESULTS, REQUEST_DENIED...). Pass it through so the
    # caller can narrate "no route found" instead of a generic error.
    data['ok'] = data.get('status') == 'OK'
    return jsonify(data), 200
