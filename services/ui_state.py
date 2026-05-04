"""
UI state writer — Phase A POC for issue #94.

Writes the same per-turn UI state that today's gateway-message prefix carries
(canvas-open, music-playing, available tracks, current user, office briefing)
to a single atomically-rewritten markdown file the OpenClaw bootstrap loader
picks up as part of the system prompt.

Phase A behavior: write the file alongside the existing prefix injection
(dual-write). Phase B will remove the prefix once we verify the agent
receives fresh UI_STATE.md content on every turn.

The file is intentionally simple — flat sections, deterministic order, no
frontmatter — so a fresh agent reading only the system prompt understands
the live UI state without further explanation.

Path: <WORKSPACE_DIR>/UI_STATE.md
  (host: /mnt/clients/<tenant>/openclaw/workspace/UI_STATE.md)
  (openclaw: /home/node/.openclaw/workspace/UI_STATE.md)

REQUIRES per-tenant docker-compose patch: the OpenVoiceUI side mount of
`/app/runtime/workspace/Agent` MUST be flipped from :ro to :rw before this
writer can succeed. The writer is fail-open — if the mount is read-only or
missing, write_ui_state() logs a warning and returns False; the caller
continues normally with the legacy prefix path.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from services.paths import WORKSPACE_DIR

logger = logging.getLogger(__name__)

# Resolve target path. Default to <WORKSPACE_DIR>/Agent/UI_STATE.md so it
# lands inside the openclaw workspace (which OVU bind-mounts at
# /app/runtime/workspace/Agent). Override via env for non-jambot setups.
_DEFAULT_TARGET = WORKSPACE_DIR / "Agent" / "UI_STATE.md"
UI_STATE_PATH = Path(os.getenv("UI_STATE_PATH", str(_DEFAULT_TARGET)))


def render_ui_state(
    canvas_open: Optional[str] = None,
    canvas_closed: bool = False,
    canvas_menu_open: bool = False,
    canvas_errors: Optional[list] = None,
    music_playing: Optional[str] = None,
    music_paused_last: Optional[str] = None,
    available_library_tracks: Optional[list] = None,
    available_generated_tracks: Optional[list] = None,
    available_canvas_pages: Optional[list] = None,
    suno_recently_finished: Optional[list] = None,
    user_tag: Optional[str] = None,
    office_briefing: Optional[str] = None,
    profile_instructions: Optional[str] = None,
) -> str:
    """
    Build the deterministic markdown content for UI_STATE.md.

    Sections only render when their data is present — agents see "Canvas: open
    page-name" rather than "Canvas: (none)" so a quiet section means a quiet
    state, not a stale slot.
    """
    lines = [
        "# UI_STATE",
        "",
        "Live UI state for THIS turn. Refreshed atomically before every user message.",
        "Do not treat as historical — earlier turns wrote different state and that state is gone.",
        "",
    ]

    # Canvas
    canvas_bits = []
    if canvas_open:
        canvas_bits.append(f"- Canvas OPEN: {canvas_open}")
    elif canvas_closed:
        canvas_bits.append("- Canvas CLOSED")
    if canvas_menu_open:
        canvas_bits.append("- Canvas menu visible to user")
    if canvas_errors:
        canvas_bits.append(f"- Canvas JS errors: {' | '.join(canvas_errors)}")
    if canvas_bits:
        lines.append("## Canvas")
        lines.extend(canvas_bits)
        lines.append("")

    # Music
    music_bits = []
    if music_playing:
        music_bits.append(f"- Currently PLAYING: {music_playing}")
    elif music_paused_last:
        music_bits.append(f"- Paused/stopped — last track: {music_paused_last}")
    if available_library_tracks:
        joined = ", ".join(available_library_tracks)
        music_bits.append(f"- Library tracks ({len(available_library_tracks)}): {joined}")
    if available_generated_tracks:
        joined = ", ".join(available_generated_tracks)
        music_bits.append(f"- Generated tracks ({len(available_generated_tracks)}): {joined}")
    if suno_recently_finished:
        joined = ", ".join(repr(t) for t in suno_recently_finished)
        music_bits.append(
            f"- Suno just finished: {joined} — now in Generated playlist"
        )
    if music_bits:
        lines.append("## Music")
        lines.extend(music_bits)
        lines.append("")

    # Canvas pages catalog (separate section — used by [CANVAS:page-id] tag)
    if available_canvas_pages:
        lines.append("## Canvas pages (use [CANVAS:page-id])")
        lines.append(f"- {', '.join(available_canvas_pages)}")
        lines.append("")

    # Identity + office brief
    identity_bits = []
    if user_tag:
        identity_bits.append(user_tag.strip())
    if office_briefing:
        identity_bits.append(f"OFFICE_BRIEFING: {office_briefing.strip()}")
    if identity_bits:
        lines.append("## Identity")
        for b in identity_bits:
            lines.append(f"- {b}")
        lines.append("")

    # Profile (admin-editor-set per-profile system prompt)
    if profile_instructions:
        lines.append("## Profile instructions")
        lines.append(profile_instructions.strip())
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_ui_state(content: str, target: Optional[Path] = None) -> bool:
    """
    Atomically write the UI state markdown to the target path.

    Atomicity: write to <target>.tmp, fsync, rename → target. OpenClaw never
    sees a half-written file even if it's polling fast.

    Returns True on success, False on any failure (caller should fall back
    to legacy prefix path).
    """
    target = target or UI_STATE_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        return True
    except OSError as e:
        # Most likely cause: per-tenant compose still has :ro on the workspace
        # mount, or the target dir doesn't exist. Fail open.
        logger.warning(
            "ui_state write failed (%s) — falling back to legacy prefix path. "
            "Per-tenant compose may need :ro→:rw on the Agent workspace mount.",
            e,
        )
        return False
    except Exception as e:
        logger.warning("ui_state write failed unexpectedly: %s", e)
        return False
