"""services/suno_lyrics.py — Suno timestamped lyrics, fetched ONCE, at generation time.

WHY THIS EXISTS (2026-10-12)
Every call to sunoapi.org's get-timestamped-lyrics is CHARGED. Before this module the
per-word timings were pulled late and repeatedly: by the host cron
(ops-repo scripts/singalong-suno-sync.py, hourly) and by the sing-along page whenever a
track without a cache was opened. The cron re-bought the same 17 instrumental songs
every hour because an instrumental never produced a cache file. Mike's rule: pull the
lyrics in the same moment the song is generated, once, and never again.

WHAT IT DOES
When a song completes (status poll OR webhook callback) with BOTH a taskId and an
audioId, `schedule()` runs `fetch_once()` in a background thread so song delivery
never waits on it. `fetch_once()`:
  * makes ZERO calls for an instrumental (request had instrumental=true, or the
    lyrics are "[Instrumental]") and records it as instrumental;
  * claims a per-audioId idempotency marker with O_CREAT|O_EXCL BEFORE the call, so
    the callback and the poll (which can both fire for one song, in different
    threads) produce exactly one paid call;
  * writes the sing-along cache in the EXACT format the host sync writes
    (canvas-pages/_data/singalong/lyrics-<slug>.json, source "suno"), atomically;
  * on failure records it in the marker and does NOT retry inline — the host sync is
    the safety net and retries at most once per 24 h;
  * appends one JSON line per paid call to generated_music/suno-lyrics-calls.jsonl
    (host: /mnt/clients/<tenant>/openvoiceui/generated_music/suno-lyrics-calls.jsonl).
    Only real HTTP attempts are logged, so the line count IS the paid-call count.

MARKER CONTRACT (read by the host sync — keep the two in step)
  canvas-pages/_data/singalong/.suno-lyrics-markers/<audio_id>.json
  {"state": "pending"|"ok"|"instrumental"|"failed", "ts": <epoch>, "task_id", "audio_id",
   "filename", "kind", "via", "called": bool, "error"?}
  The host sync never asks again for "instrumental", treats "ts" as the last attempt
  (so a failure or a stuck "pending" is retried no sooner than 24 h later), and skips
  any track whose cache already has source "suno".

lines_from_aligned() is a faithful port of sing-along.html linesFromAligned(); it is
the same code as the host sync's port, which was proven identical by --verify-port.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests as http_requests

from services.paths import CANVAS_PAGES_DIR, GENERATED_MUSIC_DIR

logger = logging.getLogger(__name__)

SUNO_API_BASE = 'https://api.sunoapi.org'
TIMESTAMPED_LYRICS_PATH = '/api/v1/generate/get-timestamped-lyrics'

# Module-level so tests can point them at a tmp dir.
CACHE_DIR = CANVAS_PAGES_DIR / '_data' / 'singalong'
CALL_LOG = GENERATED_MUSIC_DIR / 'suno-lyrics-calls.jsonl'
MARKER_DIRNAME = '.suno-lyrics-markers'
HOST_SYNC_STATE = '.suno-sync-state.json'      # written by the host sync, keyed by filename
VIA = 'openvoiceui-generation'

_TENANT = os.environ.get('JAMBOT_TENANT') or os.environ.get('HOST_TENANT', '') or 'unknown'


# ── shared helpers ───────────────────────────────────────────────────────────

def slug(filename: str) -> str:
    """MUST match sing-along.html `slug` (and the host sync) or the page reads another file."""
    return re.sub(r'[^a-zA-Z0-9\-_]', '_', re.sub(r'\.[^.]+$', '', filename or ''))[:80]


def cache_path(filename: str) -> Path:
    return CACHE_DIR / f'lyrics-{slug(filename)}.json'


def marker_path(audio_id: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9_\-]', '_', audio_id or '')[:128]
    return CACHE_DIR / MARKER_DIRNAME / f'{safe}.json'


def is_instrumental_lyrics(lyrics) -> bool:
    return str(lyrics or '').strip().lower() == '[instrumental]'


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def post_timestamped_lyrics(task_id: str, audio_id: str, api_key: str, timeout: int = 30):
    """The ONE place this app POSTs get-timestamped-lyrics. PAID. Returns the Response."""
    return http_requests.post(
        f'{SUNO_API_BASE}{TIMESTAMPED_LYRICS_PATH}',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'taskId': task_id, 'audioId': audio_id},
        timeout=timeout,
    )


def credits_from(payload) -> dict | None:
    """Any credit-ish field Suno returned (top level or data). None when there is none."""
    out = {}
    if not isinstance(payload, dict):
        return None
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    for src in (payload, data):
        for k, v in src.items():
            if 'credit' in str(k).lower() and isinstance(v, (int, float, str)) and not isinstance(v, bool):
                out[k] = v
    return out or None


def log_call(row: dict) -> None:
    """Append one row per PAID call. Never raises — telemetry must not touch delivery."""
    try:
        full = {'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'tenant': _TENANT}
        full.update({k: v for k, v in row.items() if v is not None})
        CALL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CALL_LOG, 'a') as f:
            f.write(json.dumps(full) + '\n')
    except Exception as exc:  # noqa: BLE001
        logger.warning(f'suno lyrics call log write failed: {exc}')


# ── faithful port of sing-along.html linesFromAligned() ──────────────────────
# Identical to ops-repo scripts/singalong-suno-sync.py lines_from_aligned().

def lines_from_aligned(aligned: list) -> list:
    text = ''
    spans: list = []
    for a in aligned:
        w = str(a.get('word') if a.get('word') is not None else (a.get('text') or ''))
        if not w:
            continue
        t0 = float(a.get('startS', a.get('start_s', a.get('start', 0))) or 0)
        t1 = float(a.get('endS', a.get('end_s', a.get('end', t0))) or t0)
        spans.append({'start': len(text), 'end': len(text) + len(w), 't0': t0, 't1': t1})
        text += w
    if not text.strip():
        return []

    def time_at(pos: int) -> float:
        for s in spans:
            if pos < s['end']:
                if s['end'] <= s['start']:
                    return s['t0']
                f = max(0.0, min(1.0, (pos - s['start']) / (s['end'] - s['start'])))
                return s['t0'] + (s['t1'] - s['t0']) * f
        return spans[-1]['t1'] if spans else 0.0

    # Section tags -> newline + padding, SAME LENGTH so the char map still holds.
    text = re.sub(r'\[[^\]\n]*\]', lambda m: '\n' + ' ' * (len(m.group(0)) - 1), text)

    lines: list = []
    pos = 0
    for line_str in text.split('\n'):
        words = []
        for m in re.finditer(r'\S+', line_str):
            s = pos + m.start()
            words.append({'w': m.group(0), 't0': time_at(s),
                          't1': time_at(s + len(m.group(0)) - 1)})
        if words:
            lines.append({'words': words})
        pos += len(line_str) + 1

    flat = [w for l in lines for w in l['words']]
    for i, w in enumerate(flat):
        if i and w['t0'] < flat[i - 1]['t0']:
            w['t0'] = flat[i - 1]['t0']
        w['t1'] = min(max(w['t1'], w['t0'] + 0.12), w['t0'] + 4)
        if i + 1 < len(flat) and flat[i + 1]['t0'] > w['t0'] + 0.05:
            w['t1'] = min(w['t1'], flat[i + 1]['t0'])
    for l in lines:
        l['t0'] = l['words'][0]['t0']
        l['t1'] = l['words'][-1]['t1']
    return lines


def _monotonic(lines: list) -> bool:
    flat = [w for l in lines for w in l['words']]
    return all(flat[i]['t0'] >= flat[i - 1]['t0'] - 1e-9 for i in range(1, len(flat)))


def write_cache(filename: str, lines: list, n_aligned: int, task_id: str, audio_id: str) -> Path:
    """Same payload + atomic replace as the host sync's write_cache()."""
    out = cache_path(filename)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        bak = out.with_suffix(f'.json.bak-pregen-{os.getpid()}')
        bak.write_bytes(out.read_bytes())
    payload = {'source': 'suno', 'offset': 0, 'lines': lines,
               'filename': filename,
               'saved': _now_iso(),
               'sync': {'via': VIA, 'aligned_words': n_aligned,
                        'task_id': task_id, 'audio_id': audio_id}}
    tmp = out.with_suffix(f'.json.tmp-{os.getpid()}-{threading.get_ident()}')
    tmp.write_text(json.dumps(payload))
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    os.replace(tmp, out)
    return out


def cached_source(filename: str):
    p = cache_path(filename)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text()).get('source')
    except Exception:  # noqa: BLE001
        return 'UNPARSEABLE'


# ── idempotency marker ───────────────────────────────────────────────────────

def read_marker(audio_id: str) -> dict | None:
    p = marker_path(audio_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001  (a claim mid-write reads as pending)
        return {'state': 'pending', 'ts': p.stat().st_mtime}


def _claim(audio_id: str, info: dict):
    """Atomically create the marker. True = we own it, False = already exists,
    None = could not write (then we must NOT pay: no marker means no idempotency)."""
    p = marker_path(audio_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    except OSError as exc:
        logger.warning(f'suno lyrics marker unwritable ({p}): {exc}')
        return None
    with os.fdopen(fd, 'w') as f:
        f.write(json.dumps({**info, 'ts': time.time(), 'at': _now_iso()}))
    return True


def _finish_marker(audio_id: str, info: dict) -> None:
    p = marker_path(audio_id)
    try:
        tmp = p.with_suffix(f'.json.tmp-{os.getpid()}-{threading.get_ident()}')
        tmp.write_text(json.dumps({**info, 'ts': time.time(), 'at': _now_iso()}))
        os.replace(tmp, p)
    except OSError as exc:
        logger.warning(f'suno lyrics marker update failed ({p}): {exc}')


def known_instrumental(filename: str = '', audio_id: str = '') -> bool:
    """True when this app OR the host sync has already established the track has no lyrics."""
    if audio_id:
        mk = read_marker(audio_id) or {}
        if mk.get('state') == 'instrumental':
            return True
    if filename:
        try:
            st = json.loads((CACHE_DIR / HOST_SYNC_STATE).read_text())
            if (st.get(filename) or {}).get('instrumental'):
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


# ── the generation-time fetch ────────────────────────────────────────────────

def fetch_once(*, task_id: str, audio_id: str, filename: str, lyrics: str = '',
               instrumental: bool = False, kind: str = 'song', api_key: str = '',
               receipt=None) -> str:
    """Fetch + cache Suno word timings for one freshly generated song, at most once.

    Returns the outcome: ok | instrumental | failed (a paid call was made) or
    skipped-* (no call was made).
    """
    task_id = (task_id or '').strip()
    audio_id = (audio_id or '').strip()
    if not (task_id and audio_id):
        logger.info(f'suno lyrics: no call for {filename!r} — needs taskId AND audioId '
                    f'(task_id={bool(task_id)} audio_id={bool(audio_id)})')
        return 'skipped-no-ids'
    if not filename:
        return 'skipped-no-filename'
    # Cheap early-out for the common sequential case (callback, then poll). The O_EXCL
    # claim below is what actually decides a true race.
    if read_marker(audio_id) is not None:
        logger.info(f'suno lyrics: {filename} ({audio_id[:8]}) already handled — no second call')
        return 'skipped-duplicate'

    base = {'task_id': task_id, 'audio_id': audio_id, 'filename': filename,
            'kind': kind, 'via': VIA}

    if instrumental or kind == 'sfx' or is_instrumental_lyrics(lyrics):
        _claim(audio_id, {**base, 'state': 'instrumental', 'called': False,
                          'reason': 'instrumental request' if instrumental else 'no lyrics'})
        logger.info(f'suno lyrics: {filename} is instrumental — no call')
        return 'skipped-instrumental'

    if cached_source(filename) == 'suno':
        _claim(audio_id, {**base, 'state': 'ok', 'called': False, 'reason': 'cache already suno'})
        return 'skipped-cached'

    if not api_key:
        return 'skipped-no-key'      # no marker: the host sync can still do it

    claimed = _claim(audio_id, {**base, 'state': 'pending', 'called': True})
    if claimed is False:
        logger.info(f'suno lyrics: {filename} ({audio_id[:8]}) already handled — no second call')
        return 'skipped-duplicate'
    if claimed is None:
        return 'skipped-marker-unwritable'

    state, error = 'failed', None
    http_status = suno_code = credits = None
    n_aligned = 0
    t0 = time.monotonic()
    try:
        resp = post_timestamped_lyrics(task_id, audio_id, api_key)
        http_status = resp.status_code
        if http_status != 200:
            error = f'http {http_status}: {(resp.text or "")[:120]}'
        else:
            d = resp.json() or {}
            suno_code = d.get('code')
            credits = credits_from(d)
            if suno_code != 200:
                error = f"suno code={suno_code} msg={d.get('msg')!r}"[:200]
            else:
                data = d.get('data') or {}
                aw = data.get('alignedWords') or data.get('aligned_words')
                if not aw:
                    state = 'instrumental'              # Suno: nothing to align
                else:
                    n_aligned = len(aw)
                    lines = lines_from_aligned(aw)
                    if not lines:
                        state = 'instrumental'          # only [Section] tags aligned
                    elif not _monotonic(lines):
                        error = 'non-monotonic schedule'
                    else:
                        write_cache(filename, lines, n_aligned, task_id, audio_id)
                        state = 'ok'
    except Exception as exc:  # noqa: BLE001
        error = f'{type(exc).__name__}: {exc}'[:200]

    _finish_marker(audio_id, {**base, 'state': state, 'called': True, 'error': error,
                              'aligned_words': n_aligned or None})
    log_call({'via': 'generation', 'task_id': task_id, 'audio_id': audio_id,
              'filename': filename, 'kind': kind, 'result': state,
              'http_status': http_status, 'suno_code': suno_code,
              'aligned_words': n_aligned or None, 'credits': credits, 'error': error,
              'ms': int((time.monotonic() - t0) * 1000)})
    if receipt and suno_code == 200:
        try:
            receipt('timestamped_lyrics')
        except Exception:  # noqa: BLE001
            pass
    if state == 'failed':
        logger.warning(f'suno lyrics: {filename} failed ({error}) — not retried inline; '
                       f'host sync retries at most once per 24 h')
    else:
        logger.info(f'suno lyrics: {filename} -> {state} ({n_aligned} aligned words)')
    return state


def _start_background(fn, name: str):
    t = threading.Thread(target=fn, name=name, daemon=True)
    t.start()
    return t


def schedule(**kwargs):
    """Run fetch_once() off the request thread. Song delivery never waits on it."""
    def _run():
        try:
            fetch_once(**kwargs)
        except Exception:  # noqa: BLE001
            logger.exception('suno lyrics background fetch crashed')
    return _start_background(_run, name=f"suno-lyrics-{str(kwargs.get('audio_id', ''))[:8]}")
