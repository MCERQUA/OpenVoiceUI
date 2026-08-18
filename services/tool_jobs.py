"""
Durable job store for async voice-agent tools.

Spec: docs/jambot/voice-agent-tool-bus.md §3

A `server_async` tool (generate_song, code_task) must NOT block the voice
conversation. It writes a job here, returns {job_id, status: "dispatched"}
immediately so the agent says "on it" and keeps talking, and the real answer
arrives later over /ws/agent-events.

── WHY FILES AND NOT A QUEUE LIBRARY ──────────────────────────────────────────
The OVU container has NO coding agent in it — `which claude` returns nothing;
only python3 exists. So the executor CANNOT live in this process. It is a
host-side worker (scripts/tool-job-worker.sh) that shares this directory via a
bind mount. A plain-file spool is the interface between two processes that share
a filesystem and nothing else — and it is the same shape as every other durable
queue on this box (mesh inbox, SUDO-QUEUE, build triggers).

It is also the security answer: the container never gains code execution. The
worker runs on the host with a pinned model and a scoped working directory.

── DURABILITY ─────────────────────────────────────────────────────────────────
Jobs default to living beside the durable DB (services/paths.DB_PATH), which is
documented there as bind-mounted precisely because the container's writable layer
is WIPED on recreate. A job that vanishes on a container recreate would leave a
voice agent that promised a result and can never deliver one — the exact
silent-loss shape called out in memory `ack-must-come-from-receiver`.

Override with TOOL_JOBS_DIR if you want them somewhere else.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.paths import DB_PATH

logger = logging.getLogger(__name__)

# Beside the durable DB by default — same bind-mount, same recreate survival.
JOBS_DIR = Path(os.getenv("TOOL_JOBS_DIR", str(DB_PATH.parent / "tool-jobs")))

# Job lifecycle
QUEUED = "queued"      # written, waiting for the host worker
RUNNING = "running"    # worker claimed it
DONE = "done"          # finished, result present
FAILED = "failed"      # finished, error present

TERMINAL = {DONE, FAILED}

# A job the worker claimed but never finished (worker killed, box rebooted) must
# not hang the agent's promise forever.
STALE_RUNNING_SECS = int(os.getenv("TOOL_JOB_STALE_SECS", "1800"))  # 30 min


def _ensure_dir() -> None:
    """Create the spool so BOTH sides of the bind mount can write it.

    This directory is the interface between two processes with different uids:
    this container runs as appuser (1001) and the host worker runs as mike
    (1000). Whichever side creates it first owns it, and the default 0o775 then
    locks the other one out — a 500 on every job create when the host got there
    first, and a silently unprocessable spool when the container did.

    Neither side can chown to the other without root, so the directory is made
    world-writable, matching the convention the rest of the tenant's
    openvoiceui/ tree already uses (canvas-pages, uploads) for exactly this
    reason. The path itself is inside the tenant's own private volume.
    """
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if (JOBS_DIR.stat().st_mode & 0o777) != 0o777:
            JOBS_DIR.chmod(0o777)
    except OSError as exc:
        # Not ours to chmod (created by the other uid) — log rather than fail;
        # writes may still work if the modes already happen to allow it.
        logger.warning("tool_jobs: could not widen %s permissions: %s", JOBS_DIR, exc)


def _path(job_id: str) -> Path:
    # job ids are generated here (uuid4 hex); reject anything else so a crafted
    # id can never escape the jobs dir.
    if not job_id or not all(c in "0123456789abcdef-" for c in job_id):
        raise ValueError(f"invalid job id: {job_id!r}")
    return JOBS_DIR / f"{job_id}.json"


def _write_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Write via temp + os.replace so a reader never sees a half-written job.

    The worker polls this directory; a torn read would look like a malformed job
    and get skipped or, worse, re-run.

    The explicit chmod is load-bearing, not tidiness. `tempfile.mkstemp` creates
    with mode 0o600 by design, so a job written by this container (appuser, 1001)
    was UNREADABLE to the host worker (mike, 1000) even with the spool directory
    world-writable — every job sat queued forever while the worker logged
    "unreadable job" once a minute. A spool shared across two uids has to be
    readable by both, at the file level as well as the directory level.
    """
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o666)  # see docstring — mkstemp's 0600 locks out the worker
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create(
    tool: str,
    params: Dict[str, Any],
    *,
    tenant: Optional[str] = None,
    user_id: Optional[str] = None,
    profile: Optional[str] = None,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Spool a new job. Returns the job dict (caller hands job_id to the agent)."""
    job_id = uuid.uuid4().hex[:16]
    now = time.time()
    job = {
        "id": job_id,
        "tool": tool,
        "params": params or {},
        "status": QUEUED,
        "created_at": now,
        "updated_at": now,
        # Provenance — every invocation must be auditable after the fact, not
        # only gated before it (voice-agent-tool-bus.md §4).
        "tenant": tenant,
        "user_id": user_id,
        "profile": profile,
        "session_id": session_id,
        # Execution scope for the host worker.
        "cwd": cwd,
        "model": model,
        "result": None,
        "error": None,
    }
    _write_atomic(_path(job_id), job)
    logger.info(
        "tool_jobs: queued %s tool=%s tenant=%s profile=%s", job_id, tool, tenant, profile
    )
    return job


def get(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        path = _path(job_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.error("tool_jobs: unreadable job %s: %s", job_id, exc)
        # An unreadable job is an ALERT, not a nothing — memory
        # `monitors-that-report-unreadable-as-fine`.
        return {"id": job_id, "status": FAILED, "error": f"job file unreadable: {exc}"}


def update(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    job = get(job_id)
    if not job:
        return None
    job.update(fields)
    job["updated_at"] = time.time()
    _write_atomic(_path(job_id), job)
    return job


def finish(
    job_id: str,
    *,
    summary: str,
    detail: Optional[str] = None,
    artifacts: Optional[List[str]] = None,
    ok: bool = True,
) -> Optional[Dict[str, Any]]:
    """Mark a job terminal. `summary` is what the voice agent will SAY."""
    return update(
        job_id,
        status=DONE if ok else FAILED,
        result={
            "summary": summary,
            "detail": detail,
            "artifacts": artifacts or [],
        } if ok else None,
        error=None if ok else summary,
    )


def list_jobs(
    *,
    status: Optional[str] = None,
    tenant: Optional[str] = None,
    session_id: Optional[str] = None,
    since: float = 0.0,
) -> List[Dict[str, Any]]:
    """List jobs, newest first. Missing dir = empty list, not an error."""
    _ensure_dir()
    out: List[Dict[str, Any]] = []
    for path in JOBS_DIR.glob("*.json"):
        if path.name.startswith(".tmp-"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                job = json.load(fh)
        except Exception:
            continue
        if status and job.get("status") != status:
            continue
        if tenant and job.get("tenant") != tenant:
            continue
        if session_id and job.get("session_id") != session_id:
            continue
        if job.get("updated_at", 0) <= since:
            continue
        out.append(job)
    out.sort(key=lambda j: j.get("updated_at", 0), reverse=True)
    return out


def reap_stale() -> List[str]:
    """Fail jobs stuck in RUNNING past the stale window.

    Without this, a worker killed mid-job leaves the agent holding a promise it
    can never fulfil — and the user never finds out. Better a spoken failure than
    silence.
    """
    reaped = []
    cutoff = time.time() - STALE_RUNNING_SECS
    for job in list_jobs(status=RUNNING):
        if job.get("updated_at", 0) < cutoff:
            finish(
                job["id"],
                summary=(
                    f"The {job.get('tool')} job stalled and was cancelled after "
                    f"{STALE_RUNNING_SECS // 60} minutes with no progress."
                ),
                ok=False,
            )
            reaped.append(job["id"])
            logger.warning("tool_jobs: reaped stale job %s (tool=%s)", job["id"], job.get("tool"))
    return reaped
