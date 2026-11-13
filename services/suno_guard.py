"""services/suno_guard.py — Suno spend guards (NEWBORN 2026-11-13, Mike: "build the guards").

ONE FILE, TWO COPIES, BYTE-IDENTICAL:
  OpenVoiceUI  services/suno_guard.py              (called by routes/suno.py at submit time)
  host         /home/mike/MIKE-AI/scripts/lib/suno_guard.py  (called by scripts/suno-spend-guard.py)
A parity test (scripts/test-suno-spend-guard.py) fails if they differ. Pure stdlib, no Flask.

WHY (docs/incidents/2026-11-13-suno-credit-drain-attribution.md): on 2026-11-11 one tenant agent made
10 songs for ONE owner request (112 credits in 35 min). MEASURED cause: the lyrics used Spanish
section labels ([Verso 1], [Intro hablado]); routes/suno.py only treats a prompt as lyrics when it
contains "[Verse"/"[Chorus"/"[Hook"/"[Bridge", so all 9 takes fell through to DESCRIPTION mode
(customMode=false, title dropped, lyrics truncated to 500 chars as a "description") and Suno wrote
its own lyrics and titles (9/9 returned an invented title; 11/11 English-labelled takes kept theirs).
The take that worked switched to [Verse 1].

What this module provides:
  normalize_section_labels(text) — ACTIVE. Rewrites ONLY a whole-line bracket tag whose FIRST word is
      a known Spanish section name ([Verso 1] -> [Verse 1], [Coro] -> [Chorus], [Puente] -> [Bridge]).
      Lyric words, inline brackets, unknown tags and English tags are never touched.
  evaluate(...) — the per-tenant DAILY CAP (default 6 generations/UTC day) and the 3-RETRY RULE
      (a 4th generation of the same lyrics/prompt fingerprint inside 6 h). PROBATION: the caller logs
      "[probation] would-block" and must NOT refuse unless mode() == 'enforce' (env SUNO_GUARD_MODE).
      owner_confirmed=True (the agent states the OWNER asked for more) clears both rules.
Counters are derived from the tenant's own generated_music/suno-task-log.jsonl (per-tenant volume,
server filesystem) — no second counter that can drift, nothing in the browser.
"""

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone

# Generations that cost a full song's credits (~12). sfx (2.5), lyrics, stems are not songs.
GEN_OPS = frozenset({'song', 'jingle', 'extend', 'cover', 'add_vocals', 'add_instrumental',
                     'replace_section'})

DEFAULT_DAILY_CAP = 6
DEFAULT_RETRY_LIMIT = 3          # this many prior takes of the same fingerprint -> the next is flagged
DEFAULT_RETRY_WINDOW_S = 6 * 3600

# Spanish section names -> the English label Suno and routes/suno.py's mode check recognise.
# Keys are accent-stripped, lower-case. Only UNAMBIGUOUS section words are listed on purpose
# ("final", "cierre" are left alone: they are also ordinary words).
_LABEL_MAP = {
    'verso': 'Verse', 'estrofa': 'Verse',
    'coro': 'Chorus', 'estribillo': 'Chorus',
    'precoro': 'Pre-Chorus', 'pre-coro': 'Pre-Chorus', 'pre coro': 'Pre-Chorus',
    'puente': 'Bridge',
    'introduccion': 'Intro',
    'interludio': 'Interlude',
    'gancho': 'Hook',
    'salida': 'Outro',
}
# A section tag is a WHOLE line: optional spaces, [ ... ], optional spaces. Max 80 chars inside.
_TAG_LINE = re.compile(r'^(?P<lead>[ \t]*)\[(?P<inner>[^\[\]\n]{1,80})\](?P<trail>[ \t]*)$', re.M)
# head = first word (or "pre coro"/"pre-coro"), then the rest kept verbatim.
_HEAD = re.compile(r'^(?P<head>pre[\s-]?coro|[^\W\d_]+)(?P<rest>.*)$', re.S | re.I)


def _fold(s: str) -> str:
    """lower-case + strip accents (Introducción -> introduccion)."""
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '')
                   if not unicodedata.combining(c)).lower()


def normalize_section_labels(text: str):
    """Return (new_text, changes). changes = [(old_tag, new_tag), ...]. Pure; never raises."""
    if not text or '[' not in text:
        return text, []
    changes = []

    def _sub(m):
        inner = m.group('inner')
        hm = _HEAD.match(inner.strip())
        if not hm:
            return m.group(0)
        eng = _LABEL_MAP.get(re.sub(r'\s+', ' ', _fold(hm.group('head'))))
        if not eng:
            return m.group(0)
        new_inner = eng + hm.group('rest')
        new = f"{m.group('lead')}[{new_inner}]{m.group('trail')}"
        changes.append((f'[{inner}]', f'[{new_inner}]'))
        return new

    try:
        out = _TAG_LINE.sub(_sub, text)
    except Exception:  # a guard must never break a generation
        return text, []
    return out, changes


def fingerprint(text: str, title: str = '') -> str:
    """Stable id of "the same lyrics / the same prompt", blind to section tags, case, accents,
    punctuation and whitespace. Uses the first 160 significant chars so a one-line tweak at the END
    of a song still matches, but new lyrics do not. Empty text falls back to the title."""
    body = _TAG_LINE.sub('', text or '')
    body = re.sub(r'[^a-z0-9]+', ' ', _fold(body)).strip()
    if not body:
        body = 'title:' + re.sub(r'[^a-z0-9]+', ' ', _fold(title or '')).strip()
    return hashlib.sha1(body[:160].encode('utf-8')).hexdigest()[:12]


def _epoch(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def load_submissions(task_log_path, since_epoch: float = 0.0) -> list:
    """Submitted generation rows from one tenant's suno-task-log.jsonl, oldest first, deduped on
    task_id (the log may duplicate rows by design). Each: {ts, op, task_id, fp}. Unreadable -> []."""
    subs, seen = [], set()
    try:
        with open(task_log_path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '"submitted"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get('event') != 'submitted' or d.get('op') not in GEN_OPS:
                    continue
                t = _epoch(d.get('ts'))
                if t < since_epoch:
                    continue
                tid = d.get('task_id') or f"noid-{t}"
                if tid in seen:
                    continue
                seen.add(tid)
                subs.append({'ts': t, 'op': d.get('op'), 'task_id': tid,
                             'fp': d.get('fp') or fingerprint(d.get('prompt', ''), d.get('title', ''))})
    except OSError:
        return []
    subs.sort(key=lambda r: r['ts'])
    return subs


def evaluate(prior: list, *, op: str, fp: str, now: float, owner_confirmed: bool = False,
             daily_cap: int = DEFAULT_DAILY_CAP, retry_limit: int = DEFAULT_RETRY_LIMIT,
             retry_window_s: int = DEFAULT_RETRY_WINDOW_S) -> dict:
    """Decide on ONE new generation given the tenant's PRIOR submissions (rows with ts <= now).
    Returns {would_block, reasons, day_count, retry_count, fp}. Non-generation ops never block."""
    if op not in GEN_OPS:
        return {'would_block': False, 'reasons': [], 'day_count': 0, 'retry_count': 0, 'fp': fp}
    day = datetime.fromtimestamp(now, timezone.utc).date()
    earlier = [r for r in prior if r['ts'] <= now]   # caller passes only PRIOR rows
    day_count = sum(1 for r in earlier
                    if datetime.fromtimestamp(r['ts'], timezone.utc).date() == day)
    retry_count = sum(1 for r in earlier if r['fp'] == fp and now - r['ts'] <= retry_window_s)
    reasons = []
    if day_count >= daily_cap:
        reasons.append(f'daily-cap: generation {day_count + 1} today (cap {daily_cap})')
    if retry_count >= retry_limit:
        reasons.append(f'retry-rule: take {retry_count + 1} of the same lyrics/prompt inside '
                       f'{retry_window_s // 3600}h (limit {retry_limit}) — ask the owner first')
    if owner_confirmed and reasons:
        reasons = [r + ' [owner_confirmed]' for r in reasons]
    return {'would_block': bool(reasons) and not owner_confirmed, 'reasons': reasons,
            'day_count': day_count, 'retry_count': retry_count, 'fp': fp}


def mode() -> str:
    """'shadow' (default: log + alert only) or 'enforce' (refuse). The ONE flag: SUNO_GUARD_MODE."""
    return 'enforce' if (os.environ.get('SUNO_GUARD_MODE', '') or '').strip().lower() == 'enforce' \
        else 'shadow'


def daily_cap() -> int:
    try:
        return max(1, int(os.environ.get('SUNO_DAILY_CAP', DEFAULT_DAILY_CAP)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP
