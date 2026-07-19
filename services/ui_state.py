"""
UI state workspace file — issue #94.

Writes the agent-facing UI state (standing instructions, canvas page list,
track list, current user, canvas/music state) to ``uploads/UI_STATE.md`` so
the agent always has a durable, current copy OUTSIDE conversation history.

Why this exists
---------------
Per-turn context prefixes accumulate in the gateway session history and waste
tokens through compaction (issue #94; PR #297 made it worse by adding
CURRENT_USER + OFFICE_BRIEFING prefixes). The context-bloat guard in
routes/conversation.py already dedupes the static block within a session, but
it still had to periodically RESEND the full ~17KB block so openclaw
auto-compaction could never silently strip the voice action-tag instructions
from history. This file is the durable copy that makes aggressive dedup safe:
the per-turn pointer names this file, so an agent that lost the instructions
to compaction can always recover them with one read.

Injection reality check (verified 2026-07-19 against openclaw docs):
openclaw injects bootstrap files (AGENTS.md, SOUL.md, TOOLS.md, ...) into the
system prompt on the FIRST turn of a session only, from a fixed file list.
An arbitrary workspace file is NOT auto-injected and NOT re-read per turn.
So this file is a read-on-demand recovery source + always-fresh reference,
not an automatic per-turn injection channel. The inline static block still
goes out on session start and whenever its content changes.

Where the agent sees it
-----------------------
Flask writes ``UPLOADS_DIR / 'UI_STATE.md'`` (= /app/runtime/uploads/ in the
openvoiceui container). The tenant compose mounts that same host dir into the
agent container:
  - OpenClaw: /home/node/.openclaw/workspace/uploads/UI_STATE.md
  - Hermes:   /workspace/uploads/UI_STATE.md
Both resolve as ``uploads/UI_STATE.md`` relative to the agent workspace.

Design constraints
------------------
- Atomic write (tmp + os.replace) — the agent may read mid-write otherwise.
- Content-hash skip — most turns change nothing; no disk churn.
- Fail-open — a write failure must NEVER cost a turn. Callers ignore result.
- Zero Flask imports — standalone module, unit-testable without the app.
"""
import hashlib
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

#: Filename inside the uploads dir (agent sees it at workspace/uploads/).
UI_STATE_FILENAME = 'UI_STATE.md'

#: Agent-relative path used in prompts/pointers — identical for both gateways.
UI_STATE_AGENT_PATH = 'uploads/UI_STATE.md'

# Last-written content hash; skip the write when nothing changed.
_last_hash_lock = threading.Lock()
_last_hash: str | None = None


def build_ui_state_doc(static_blob: str, dynamic_lines: list[str]) -> str:
    """Compose the UI_STATE.md document.

    ``static_blob``    — the joined standing-instruction block (voice action
                         tags, canvas page list, track list, profile
                         instructions, canvas style, current user).
    ``dynamic_lines``  — small per-turn state lines (canvas open/closed,
                         music playing). Ephemeral per-turn context (camera
                         vision, uploaded-image analysis, browser-extension
                         page dumps, Suno event announcements) is deliberately
                         EXCLUDED — it belongs to the turn, not to state.
    """
    ts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    parts = [
        '# UI STATE — OpenVoiceUI live state',
        '',
        f'_Auto-written by OpenVoiceUI before each agent turn (last: {ts})._',
        '_This file is always current. If conversation history was compacted',
        'and you no longer remember the voice action-tag instructions, the',
        'canvas page list, or the track list — re-read THIS file. Never guess',
        'a page-id or track name from memory when you can read it here._',
        '',
        '## Current state',
        '',
    ]
    if dynamic_lines:
        parts.extend(f'- {line}' for line in dynamic_lines)
    else:
        parts.append('- (no UI state reported this turn)')
    parts += [
        '',
        '## Standing instructions + catalogs',
        '',
        static_blob.strip(),
        '',
    ]
    return '\n'.join(parts)


def write_ui_state(uploads_dir: Path, static_blob: str,
                   dynamic_lines: list[str]) -> bool:
    """Atomically write UI_STATE.md into ``uploads_dir``.

    Returns True if the file was (re)written, False if skipped (unchanged)
    or failed. Fail-open: never raises.
    """
    global _last_hash
    try:
        doc = build_ui_state_doc(static_blob, dynamic_lines)
        # Hash everything EXCEPT the timestamp line so an otherwise-unchanged
        # doc doesn't rewrite every turn just because the clock moved.
        hash_src = '\n'.join(
            l for l in doc.splitlines() if not l.startswith('_Auto-written')
        )
        digest = hashlib.sha1(hash_src.encode('utf-8', 'replace')).hexdigest()
        with _last_hash_lock:
            if digest == _last_hash:
                return False

        uploads_dir = Path(uploads_dir)
        uploads_dir.mkdir(parents=True, exist_ok=True)
        target = uploads_dir / UI_STATE_FILENAME
        # Atomic: write to a tmp file in the SAME dir, then rename over.
        fd, tmp_path = tempfile.mkstemp(
            prefix='.UI_STATE.', suffix='.tmp', dir=str(uploads_dir))
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(doc)
            os.replace(tmp_path, target)
        except Exception:
            # Best-effort tmp cleanup — never leave droppings in uploads/.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            os.chmod(target, 0o664)
        except OSError:
            pass
        with _last_hash_lock:
            _last_hash = digest
        return True
    except Exception as e:  # noqa: BLE001 — fail-open by contract
        logger.warning(f'UI_STATE.md write failed (non-fatal): {e}')
        return False
