"""
host_broker.py — client for the JamBot host provisioning broker (:5200), SEC-059.

On JamBot every tenant's OpenVoiceUI runs in a container. Two things it used to do by
holding the host's docker.sock (root-equivalent on the host):

  1. recreate its own openclaw-<t> / openvoiceui-<t> after a credential save so the new
     env_file values load (services/vault.py);
  2. exec into openclaw-<t> to queue an uploaded image to vision@mesh
     (routes/conversation.py).

Both are now requests to the host broker, which binds the CALLER to the tenant two ways
(source address inside the tenant's docker network, and the per-tenant token at
/mnt/clients/<t>/vault/.sec059-broker-token, visible in-container at /app/runtime/vault).
The broker runs exactly the commands the container used to run; the container holds no
docker capability at all.

Flags (read per call, so a recreate with a new env_file flips them):
  RESTART_VIA_BROKER=1        use the broker for restarts + the vision queue
  VAULT_RESTART_VIA_DOCKER=1  keep the legacy in-container docker subprocess path
                              (explicit opt-in for non-JamBot installs that mount a socket)
  neither                     legacy docker subprocess path, with a loud log line naming
                              both flags — today's behaviour, never a silent change at a
                              fleet roll. See restart_mode().

Nothing here fails silently: an unreachable broker, a 403, a 503 all log at ERROR and
return False / raise BrokerError to the caller, who must report that the action did not
happen (a credential save that did not restart the agent is not "saved").
"""
import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CLIENTS_DIR = Path(os.getenv("JAMBOT_CLIENTS_DIR", "/mnt/clients"))
TOKEN_NAME = ".sec059-broker-token"
DEFAULT_PORT = "5200"
MESH_TASK_TO = "vision@mesh"
SUBJECT_MAX = 120
BODY_MAX = 4000


class BrokerError(RuntimeError):
    """The broker did not do it. .status is the HTTP status (0 = unreachable)."""

    def __init__(self, message: str, status: int = 0, detail: str = "", outcome_unknown: bool = False):
        super().__init__(message)
        self.status = status
        self.detail = detail
        # True when the request REACHED the broker but no answer came back in time (read
        # timeout). The broker may have done the work; the caller must say UNKNOWN, never
        # "did not happen" (PR #541 review D1: client 120 s < broker compose 180 s).
        self.outcome_unknown = outcome_unknown


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------
def restart_mode() -> str:
    """'docker' | 'broker' | 'legacy-docker'. Evaluated per call."""
    if os.getenv("VAULT_RESTART_VIA_DOCKER", "").strip() == "1":
        return "docker"
    if os.getenv("RESTART_VIA_BROKER", "").strip() == "1":
        return "broker"
    return "legacy-docker"


def via_broker() -> bool:
    return restart_mode() == "broker"


# ---------------------------------------------------------------------------
# where is the broker, who am I
# ---------------------------------------------------------------------------
def default_gateway() -> Optional[str]:
    """The container's default route gateway, from /proc/net/route, resolved at call
    time (the per-tenant bridge gateway is where the broker answers; never 172.17.0.1)."""
    try:
        with open("/proc/net/route") as f:
            next(f)
            for line in f:
                fields = line.split()
                if len(fields) > 2 and fields[1] == "00000000":
                    h = fields[2]
                    ip = f"{int(h[6:8], 16)}.{int(h[4:6], 16)}.{int(h[2:4], 16)}.{int(h[0:2], 16)}"
                    ipaddress.ip_address(ip)
                    return ip
    except (OSError, ValueError, StopIteration):
        pass
    return None


def broker_url() -> str:
    port = os.getenv("PROVISION_SERVICE_PORT", DEFAULT_PORT)
    host = os.getenv("PROVISION_SERVICE_HOST", "").strip() or default_gateway() or "172.17.0.1"
    return f"http://{host}:{port}"


_RUNTIME_VAULT_DIR = Path(os.getenv("SEC059_RUNTIME_VAULT_DIR", "/app/runtime/vault"))


def tenant_token_path(tenant: str) -> Path:
    """The tenant's broker token lives in its VAULT dir (host /mnt/clients/<t>/vault, mounted
    at /app/runtime/vault in the tenant's own OVU container). Measured 2026-10-14: the vault
    mount is present in 23/24 OVU containers and in 0/25 openclaw containers, so the tenant's
    agent cannot read it; the compose dir (the first design) was visible to only 12 OVUs."""
    override = os.getenv("SEC059_TOKEN_FILE", "").strip()
    if override:
        return Path(override)
    if _RUNTIME_VAULT_DIR.is_dir():
        return _RUNTIME_VAULT_DIR / TOKEN_NAME
    return _CLIENTS_DIR / tenant / "vault" / TOKEN_NAME


def tenant_token(tenant: str) -> Optional[str]:
    """The minted token, or None (not minted / not readable). Read per call: a rotate
    on the host takes effect without a restart."""
    try:
        tok = tenant_token_path(tenant).read_text().strip()
    except (OSError, ValueError):
        return None
    return tok or None


def auth_headers(tenant: str) -> dict:
    """Headers for ANY broker call (the broker binds every route, including the hermes
    plugin ones). An absent token sends no header: the old broker ignores it, the new
    one answers 503 'broker not provisioned for tenant' — loud, never a bypass."""
    tok = tenant_token(tenant) if tenant else None
    return {"X-Tenant-Token": tok} if tok else {}


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------
def _post(tenant: str, path: str, body: dict, timeout: float) -> dict:
    url = f"{broker_url()}{path}"
    try:
        resp = requests.post(url, json=body, headers=auth_headers(tenant), timeout=timeout)
    except requests.ReadTimeout as exc:
        # Connected and sent; no answer in `timeout`. The broker may still be (or have
        # finished) doing it. Not success, not failure: UNKNOWN.
        logger.warning("[host-broker] NO ANSWER in %ss from %s (request was delivered): %s", timeout, url, exc)
        raise BrokerError(f"broker gave no answer within {timeout}s at {url} (request delivered; outcome unknown)",
                          status=0, outcome_unknown=True) from exc
    except requests.RequestException as exc:
        logger.error("[host-broker] UNREACHABLE %s: %s", url, exc)
        raise BrokerError(f"broker unreachable at {url}: {exc}", status=0) from exc
    try:
        data = resp.json()
    except ValueError:
        data = {"error": (resp.text or "")[:300]}
    if resp.status_code >= 300 or not data.get("ok", False):
        err = data.get("error", f"HTTP {resp.status_code}")
        logger.error("[host-broker] %s -> %s: %s", path, resp.status_code, err)
        raise BrokerError(f"broker refused {path}: {resp.status_code} {err}",
                          status=resp.status_code, detail=str(data)[:500])
    return data


def restart_container(service: str, tenant: str, dry_run: bool = False) -> dict:
    """POST /restart/<service>/<tenant>. Returns the broker's JSON on success; raises
    BrokerError otherwise. NOTE: recreating openvoiceui-<t> is recreating the process
    making this call — the response may never arrive; the caller treats a dropped
    connection on that service as 'unknown', not as success."""
    if service not in ("openclaw", "openvoiceui"):
        raise BrokerError(f"service must be openclaw|openvoiceui, got {service!r}", status=400)
    # 240 s > the broker's own compose timeout (180 s), so a slow-but-successful recreate
    # answers before we give up (review D1).
    return _post(tenant, f"/restart/{service}/{tenant}", {"dry_run": bool(dry_run)}, timeout=240)


def safe_subject(stem: str, prefix: str = "analyze-", limit: int = 40) -> str:
    """mesh-send subjects become filenames (kebab-case); the broker rejects anything
    outside [A-Za-z0-9._-]. Upload stems carry spaces and worse."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", stem or "")
    s = re.sub(r"-{2,}", "-", s).strip("-.")[:limit] or "image"
    return (prefix + s)[:SUBJECT_MAX]


def queue_vision_task(tenant: str, subject: str, body: str) -> dict:
    """POST /mesh-task/<tenant> for vision@mesh. Success comes from the broker's
    response (mesh-send exit 0 + delivered path), never from having reached the call."""
    if len(body) > BODY_MAX:
        body = body[:BODY_MAX]
    return _post(tenant, f"/mesh-task/{tenant}",
                 {"to": MESH_TASK_TO, "subject": subject, "body": body}, timeout=20)


def queue_vision_task_legacy(tenant: str, subject: str, body: str) -> bool:
    """Pre-SEC-059 path: exec into the sibling openclaw-<tenant> container (which has
    mesh-send + the mesh mount) through a docker socket mounted into THIS container.
    Used only when restart_mode() is not 'broker'. Lives here, not in
    routes/conversation.py, so the route file carries no docker exec path at all
    (the migration script's P2 gate greps for one).

    CHECK THE RETURN CODE: a mesh-send that exited non-zero queued nothing, and the user
    must not be told an analysis is coming."""
    import subprocess
    cmd = (f"AGENT_URI=openvoiceui@mesh /mnt/shared-skills/agent-mesh/bin/mesh-send "
           f"--to {MESH_TASK_TO} --kind task --subject {subject}")
    try:
        r = subprocess.run(["docker", "exec", "-i", f"openclaw-{tenant}", "bash", "-c", cmd],
                           input=body.encode(), timeout=8, capture_output=True)
    except Exception as exc:
        logger.warning("[host-broker] legacy vision queue failed to run for %s: %s", tenant, exc)
        return False
    if r.returncode != 0:
        logger.error("[host-broker] legacy vision queue FAILED rc=%s tenant=%s stderr=%s",
                     r.returncode, tenant, (r.stderr or b"").decode("utf-8", "replace")[:300])
        return False
    return True
