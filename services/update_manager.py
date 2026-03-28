"""
Intelligent Update Manager for OpenVoiceUI
==========================================

Instead of a blind `git pull`, this module:

1. Analyses what's changing (diff, new deps, breaking changes)
2. Detects local customisations that could be destroyed
3. Searches for an available CLI coding agent (claude, codex, z-code, etc.)
4. If an agent is found → spawns it with a comprehensive review prompt
5. If no agent → runs a heuristic-based smart update with backup/rollback
6. Verifies health after every update

Works in ALL deployment scenarios:
  - Native / self-hosted (git repo on disk)
  - Docker single-user
  - Docker multi-tenant (JamBot)
  - Pinokio

Usage from server.py:
    from services.update_manager import UpdateManager
    mgr = UpdateManager(Path(__file__).parent)
    result = mgr.apply_update()
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── CLI agent detection order ────────────────────────────────────────────────
# Each entry: (env_var_override, binary_name, human_label)
# Checked in order; first match wins.
CLI_AGENTS = [
    ("OPENVOICEUI_UPDATE_CLI", None, "custom"),        # user-defined override
    (None, "claude",   "Claude Code"),
    (None, "codex",    "OpenAI Codex"),
    (None, "z-code",   "z-code (Z.AI)"),
    (None, "maxcode",  "MaxCode"),
    (None, "opencode", "OpenCode"),
    (None, "aider",    "Aider"),
]

# Files that are HIGH RISK if they have local modifications and the upstream
# update touches them.  The agent prompt enumerates these explicitly.
HIGH_RISK_FILES = [
    "server.py",
    "app.py",
    "routes/admin.py",
    "routes/canvas.py",
    "routes/conversation.py",
    "services/update_manager.py",
    "services/gateway_manager.py",
    "services/auth.py",
    "services/plugins.py",
    "src/app.js",
    "src/ui/AppShell.js",
    "src/providers/WebSpeechSTT.js",
    "src/styles/base.css",
    "Dockerfile",
    "docker-compose.yml",
    "requirements.txt",
    ".dockerignore",
]

# ── Comprehensive prompt for the AI coding agent ─────────────────────────────
# This is the "well-thought-out prompt" that tells the agent about every
# possible breaking point before it touches anything.

AGENT_PROMPT_TEMPLATE = r"""
# OpenVoiceUI Intelligent Update Review

You are performing a code update for OpenVoiceUI, an AI-powered voice assistant
platform. Your job is to **carefully review and apply** this update without
breaking any local customisations, plugins, frameworks, or configurations the
user has added.

**CRITICAL: Do NOT blindly `git pull`. You must review everything first.**

## Current State
- App directory: {app_dir}
- Current commit: {current_commit} ({current_branch})
- Target: latest release from origin/main
- Deployment type: {deployment_type}

## What Changed Upstream
{upstream_diff_summary}

### Files changed ({changed_file_count}):
{changed_files_list}

### New dependencies (requirements.txt diff):
{deps_diff}

## Local Customisations Detected
{local_modifications}

## Conflicts (files changed BOTH locally AND upstream)
{conflicts}

## Your Update Checklist — Review EVERY item before proceeding

### 1. SERVER & APP CORE
- [ ] **server.py** — Check for custom routes, middleware, rate limits, monitor
      endpoints, or any @app.route additions not in the upstream version.
      If the user added custom API endpoints (like /api/monitor/*), they MUST
      be preserved after the update.
- [ ] **app.py** — Check for auth logic changes, before_request hooks, CSP
      header modifications, CORS settings, or custom middleware.
- [ ] **routes/*.py** — Every route file. Look for custom endpoints, modified
      response formats, or patched behaviour.

### 2. FRONTEND
- [ ] **src/app.js** — This file often has monkey-patches and custom behaviour.
      Check for PTT modifications, wake word changes, STT patches, custom
      event handlers, or UI tweaks.
- [ ] **src/ui/AppShell.js** — Layout customisations, custom panels, branding.
- [ ] **src/styles/base.css** — Theme customisations, colour scheme changes.
- [ ] **src/providers/WebSpeechSTT.js** — Voice flow is delicate. Any local
      changes to STT/wake word MUST be preserved.

### 3. SERVICES & INTEGRATIONS
- [ ] **services/gateway_manager.py** — Custom gateway configurations.
- [ ] **services/auth.py** — Auth provider changes, token handling.
- [ ] **services/plugins.py** — Plugin system API. If the plugin interface
      changed upstream, existing plugins may break.
- [ ] **services/tts.py** — TTS provider integrations, voice settings.
- [ ] **services/canvas_versioning.py** — Canvas page versioning logic.

### 4. CONFIGURATION & INFRASTRUCTURE
- [ ] **requirements.txt** — New dependencies may conflict with locally
      installed packages. Removed deps may break custom code that imports them.
- [ ] **Dockerfile** — Build process changes. Check if new system packages
      are needed, if the base image changed, or if COPY paths moved.
- [ ] **.dockerignore** — Could accidentally exclude needed files.
- [ ] **docker-compose.yml** (if present) — Volume mounts, env vars, networks.

### 5. CANVAS & PLUGIN SYSTEM
- [ ] **Canvas manifest format** — If the manifest schema changed, existing
      pages may not load.
- [ ] **Canvas page API** — Routes like /pages/*, /api/canvas/* changes.
- [ ] **Plugin loading** — Plugin directory structure, discovery, registration.
- [ ] **Default pages** — New default pages may overwrite user-customised ones.

### 6. DATABASE & STATE
- [ ] **SQLite/DB schema** — Any migration needed?
- [ ] **File path changes** — UPLOADS_DIR, CANVAS_PAGES_DIR, RUNTIME_DIR.
      If paths changed, existing data may become orphaned.

### 7. DOCKER / MULTI-TENANT (if applicable)
- [ ] **Volume mount compatibility** — New code expects dirs that aren't mounted.
- [ ] **Container UID/GID** — Permission changes break bind-mounted files.
- [ ] **Inter-container communication** — WebSocket URLs, port changes.
- [ ] **OpenClaw config template** — openclaw.json field changes.
- [ ] **Environment variables** — New required vars that aren't in .env.
- [ ] **Health check changes** — Could break monitoring/orchestration.
- [ ] **Nginx proxy patterns** — Header changes, new routes to proxy.

### 8. VOICE & REAL-TIME
- [ ] **WebSocket protocol** — Message format changes break voice streaming.
- [ ] **TTS integration** — Provider API changes, new providers, removed ones.
- [ ] **STT flow** — Chrome SpeechRecognition handling is fragile.

## Your Procedure

1. **READ the diff carefully.** Understand what changed and why.
2. **CHECK each local modification** against the upstream changes.
3. **For each conflict:**
   - If the upstream change is a bug fix that doesn't affect the customisation
     → take the upstream version
   - If the upstream change would overwrite a custom feature →
     merge intelligently (keep the custom code, integrate the upstream fix)
   - If unclear → keep the local version and note it in your report
4. **Back up** any files you're about to modify:
   `cp file.py file.py.pre-update`
5. **Apply the update:**
   - `git stash` local changes (if working tree is dirty)
   - `git pull origin main`
   - Re-apply any stashed customisations that were overwritten
   - `pip install -r requirements.txt` (if requirements changed)
6. **Verify:**
   - `python3 -c "from server import app; print('Import OK')"` must pass
   - Check that custom endpoints still respond
   - Check that no import errors in routes/
7. **If ANYTHING fails** → rollback:
   - `git reset --hard {current_commit}`
   - Restore .pre-update backups
   - Report what went wrong

## Output Format

When done, print a JSON summary:
```json
{{
  "status": "success|failed|partial",
  "previous_commit": "...",
  "new_commit": "...",
  "files_updated": [...],
  "customisations_preserved": [...],
  "conflicts_resolved": [...],
  "warnings": [...],
  "rollback_performed": false
}}
```
""".strip()


class UpdateManager:
    """Orchestrates intelligent updates for OpenVoiceUI."""

    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self.git_dir = self.app_dir / ".git"

    # ── Git helpers ──────────────────────────────────────────────────────

    def _git(self, *args, timeout=30) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(self.app_dir),
            capture_output=True, text=True, timeout=timeout,
        )

    def _current_commit(self) -> str:
        r = self._git("rev-parse", "--short", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else "unknown"

    def _current_branch(self) -> str:
        r = self._git("branch", "--show-current")
        return r.stdout.strip() if r.returncode == 0 else "unknown"

    def _is_git_repo(self) -> bool:
        return self.git_dir.is_dir()

    # ── Deployment type detection ────────────────────────────────────────

    def detect_deployment_type(self) -> str:
        """Detect how OpenVoiceUI is deployed."""
        # Check for Docker
        if Path("/.dockerenv").exists():
            # Check for multi-tenant markers
            if os.environ.get("CLIENT_NAME"):
                return "docker-multi-tenant"
            return "docker"
        # Check for Pinokio
        if os.environ.get("PINOKIO"):
            return "pinokio"
        # Check for systemd service
        if Path("/etc/systemd/system/openvoiceui.service").exists():
            return "systemd"
        return "native"

    # ── CLI agent detection ──────────────────────────────────────────────

    def detect_cli_agent(self) -> Optional[dict]:
        """Find the best available CLI coding agent.

        Checks environment overrides first, then scans PATH for known agents.
        Returns dict with 'cmd', 'label', 'source' or None.
        """
        for env_var, binary, label in CLI_AGENTS:
            # Check environment override
            if env_var:
                custom_cmd = os.environ.get(env_var, "").strip()
                if custom_cmd:
                    # Verify it exists
                    resolved = shutil.which(custom_cmd) or custom_cmd
                    if Path(resolved).is_file() or shutil.which(resolved):
                        logger.info(f"[update] Using custom CLI agent: {custom_cmd}")
                        return {
                            "cmd": resolved,
                            "label": f"Custom ({custom_cmd})",
                            "source": "env",
                        }
                continue

            # Check PATH
            if binary:
                resolved = shutil.which(binary)
                if resolved:
                    logger.info(f"[update] Found CLI agent: {label} at {resolved}")
                    return {
                        "cmd": resolved,
                        "label": label,
                        "source": "path",
                    }

        # Check additional PATH locations (Docker containers may prepend)
        extra_paths = os.environ.get("OPENVOICEUI_CLI_PATH", "").split(":")
        extra_paths += [
            "/home/node/.local/bin",  # openclaw container
            "/usr/local/bin",
            str(Path.home() / ".local" / "bin"),
        ]
        for directory in extra_paths:
            if not directory:
                continue
            for _, binary, label in CLI_AGENTS:
                if not binary:
                    continue
                candidate = Path(directory) / binary
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    logger.info(f"[update] Found CLI agent: {label} at {candidate}")
                    return {
                        "cmd": str(candidate),
                        "label": label,
                        "source": "extra_path",
                    }

        logger.info("[update] No CLI coding agent found — will use smart fallback")
        return None

    # ── Pre-flight analysis ──────────────────────────────────────────────

    def analyze(self) -> dict:
        """Analyse what the update will change and what could break.

        Returns a dict with all the information needed for the update decision.
        """
        if not self._is_git_repo():
            return {"error": "Not a git repository", "can_update": False}

        current_commit = self._current_commit()
        current_branch = self._current_branch()

        # Fetch latest from origin
        fetch = self._git("fetch", "origin", "main", timeout=60)
        if fetch.returncode != 0:
            return {
                "error": f"git fetch failed: {fetch.stderr[:200]}",
                "can_update": False,
            }

        # What files changed upstream?
        diff_names = self._git(
            "diff", "--name-only", "HEAD..origin/main"
        )
        changed_files = [
            f.strip() for f in diff_names.stdout.splitlines() if f.strip()
        ]

        # Full diff stat
        diff_stat = self._git("diff", "--stat", "HEAD..origin/main")

        # Summarise the diff (first 200 lines of actual diff for context)
        diff_full = self._git(
            "diff", "HEAD..origin/main", "--no-color"
        )
        diff_lines = diff_full.stdout.splitlines()
        diff_summary = "\n".join(diff_lines[:300])
        if len(diff_lines) > 300:
            diff_summary += f"\n... ({len(diff_lines) - 300} more lines)"

        # Dependencies diff
        deps_diff = self._git(
            "diff", "HEAD..origin/main", "--", "requirements.txt"
        )

        # What commits are we pulling?
        log_result = self._git(
            "log", "--oneline", "HEAD..origin/main"
        )
        incoming_commits = log_result.stdout.strip()

        # Local modifications (uncommitted changes + untracked files)
        status = self._git("status", "--porcelain")
        local_mods = [
            line.strip() for line in status.stdout.splitlines() if line.strip()
        ]

        # Detect bind-mounted overrides (Docker pattern)
        bind_mounts = self._detect_bind_mounts()

        # Detect installed plugins
        plugins = self._detect_plugins()

        # Detect custom routes not in upstream
        custom_routes = self._detect_custom_routes(changed_files)

        # Classify conflicts
        conflicts = []
        high_risk = []
        for f in changed_files:
            # Check if file is locally modified
            is_local_mod = any(f in line for line in local_mods)
            is_bind_mount = f in bind_mounts
            is_high_risk = f in HIGH_RISK_FILES

            if is_local_mod or is_bind_mount:
                entry = {
                    "file": f,
                    "locally_modified": is_local_mod,
                    "bind_mounted": is_bind_mount,
                    "high_risk": is_high_risk,
                }
                conflicts.append(entry)

            if is_high_risk and f in changed_files:
                high_risk.append(f)

        # Risk assessment
        if conflicts:
            risk = "high" if any(c["high_risk"] for c in conflicts) else "medium"
        elif high_risk:
            risk = "medium"
        else:
            risk = "low"

        return {
            "can_update": True,
            "current_commit": current_commit,
            "current_branch": current_branch,
            "deployment_type": self.detect_deployment_type(),
            "changed_files": changed_files,
            "changed_file_count": len(changed_files),
            "diff_stat": diff_stat.stdout.strip(),
            "diff_summary": diff_summary,
            "deps_diff": deps_diff.stdout.strip() or "(no changes)",
            "incoming_commits": incoming_commits,
            "local_modifications": local_mods,
            "bind_mounts": bind_mounts,
            "plugins": plugins,
            "custom_routes": custom_routes,
            "conflicts": conflicts,
            "high_risk_files_changed": high_risk,
            "risk": risk,
        }

    def _detect_bind_mounts(self) -> list:
        """Detect Docker bind-mounted files (overrides that live outside the container)."""
        mounts = []
        try:
            # /proc/mounts shows bind mounts inside Docker
            if Path("/proc/mounts").exists():
                with open("/proc/mounts") as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].startswith("/app/"):
                            rel = parts[1].replace("/app/", "", 1)
                            if rel and not rel.startswith("runtime/"):
                                mounts.append(rel)
        except Exception:
            pass
        return mounts

    def _detect_plugins(self) -> list:
        """Detect installed plugins."""
        plugins = []
        plugins_dir = self.app_dir / "plugins"
        if plugins_dir.is_dir():
            for d in plugins_dir.iterdir():
                if d.is_dir() and (d / "plugin.json").exists():
                    try:
                        info = json.loads((d / "plugin.json").read_text())
                        plugins.append({
                            "name": info.get("name", d.name),
                            "version": info.get("version", "?"),
                        })
                    except Exception:
                        plugins.append({"name": d.name, "version": "?"})
        return plugins

    def _detect_custom_routes(self, upstream_changed: list) -> list:
        """Detect locally-added route files not tracked upstream."""
        custom = []
        routes_dir = self.app_dir / "routes"
        if routes_dir.is_dir():
            # Check which route files exist locally but aren't in upstream
            r = self._git("ls-tree", "-r", "--name-only", "origin/main", "--", "routes/")
            upstream_routes = set(r.stdout.strip().splitlines())
            for f in routes_dir.glob("*.py"):
                rel = f"routes/{f.name}"
                if rel not in upstream_routes and f.name != "__pycache__":
                    custom.append(rel)
        return custom

    # ── Agent prompt generation ──────────────────────────────────────────

    def generate_agent_prompt(self, analysis: dict) -> str:
        """Build the comprehensive prompt for the CLI coding agent."""

        # Format conflicts
        if analysis["conflicts"]:
            conflicts_text = ""
            for c in analysis["conflicts"]:
                flags = []
                if c["locally_modified"]:
                    flags.append("locally modified")
                if c["bind_mounted"]:
                    flags.append("bind-mounted override")
                if c["high_risk"]:
                    flags.append("HIGH RISK")
                conflicts_text += f"  - {c['file']} [{', '.join(flags)}]\n"
        else:
            conflicts_text = "  (none detected)\n"

        # Format local modifications
        if analysis["local_modifications"]:
            local_mods_text = "\n".join(
                f"  {line}" for line in analysis["local_modifications"]
            )
        else:
            local_mods_text = "  (working tree is clean)"

        # Add bind mounts and plugins
        if analysis["bind_mounts"]:
            local_mods_text += "\n\nBind-mounted overrides (Docker):\n"
            local_mods_text += "\n".join(
                f"  - {m}" for m in analysis["bind_mounts"]
            )

        if analysis["plugins"]:
            local_mods_text += "\n\nInstalled plugins:\n"
            local_mods_text += "\n".join(
                f"  - {p['name']} v{p['version']}" for p in analysis["plugins"]
            )

        if analysis["custom_routes"]:
            local_mods_text += "\n\nCustom route files (not in upstream):\n"
            local_mods_text += "\n".join(
                f"  - {r}" for r in analysis["custom_routes"]
            )

        return AGENT_PROMPT_TEMPLATE.format(
            app_dir=str(self.app_dir),
            current_commit=analysis["current_commit"],
            current_branch=analysis["current_branch"],
            deployment_type=analysis["deployment_type"],
            upstream_diff_summary=analysis["diff_stat"],
            changed_file_count=analysis["changed_file_count"],
            changed_files_list="\n".join(
                f"  - {f}" for f in analysis["changed_files"]
            ) or "  (none)",
            deps_diff=analysis["deps_diff"],
            local_modifications=local_mods_text,
            conflicts=conflicts_text,
        )

    # ── Agent-assisted update ────────────────────────────────────────────

    def run_agent_update(self, agent: dict, analysis: dict) -> dict:
        """Spawn a CLI coding agent to review and apply the update.

        The agent gets the full prompt with all breaking points enumerated,
        then works autonomously to apply the update safely.
        """
        prompt = self.generate_agent_prompt(analysis)
        agent_cmd = agent["cmd"]
        agent_label = agent["label"]

        logger.info(f"[update] Spawning {agent_label} for update review...")

        # Write prompt to a temp file (some CLIs prefer file input)
        prompt_file = self.app_dir / ".update-prompt.md"
        try:
            prompt_file.write_text(prompt)
        except Exception:
            # Read-only filesystem — try /tmp
            prompt_file = Path("/tmp/.openvoiceui-update-prompt.md")
            prompt_file.write_text(prompt)

        # Build the agent command based on what CLI it is
        cmd = self._build_agent_command(agent_cmd, str(prompt_file))

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.app_dir),
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute max for agent review
                env={**os.environ, "NONINTERACTIVE": "1"},
            )

            output = result.stdout + result.stderr
            logger.info(f"[update] Agent finished (exit={result.returncode})")

            # Try to parse JSON result from agent output
            agent_result = self._parse_agent_result(output)

            if result.returncode == 0 and agent_result.get("status") == "success":
                return {
                    "status": "success",
                    "method": f"agent ({agent_label})",
                    "agent_output": output[-2000:],  # last 2K chars
                    **agent_result,
                }
            elif agent_result.get("rollback_performed"):
                return {
                    "status": "rolled_back",
                    "method": f"agent ({agent_label})",
                    "reason": agent_result.get("warnings", ["Agent rolled back"]),
                    "agent_output": output[-2000:],
                }
            else:
                # Agent failed — fall back to smart update
                logger.warning(
                    f"[update] Agent returned non-success, falling back to smart update"
                )
                return {
                    "status": "agent_failed",
                    "method": f"agent ({agent_label})",
                    "reason": output[-500:],
                    "fallback": True,
                }

        except subprocess.TimeoutExpired:
            logger.error("[update] Agent timed out after 5 minutes")
            return {
                "status": "agent_timeout",
                "method": f"agent ({agent_label})",
                "fallback": True,
            }
        except Exception as exc:
            logger.error(f"[update] Agent error: {exc}")
            return {
                "status": "agent_error",
                "method": f"agent ({agent_label})",
                "reason": str(exc),
                "fallback": True,
            }
        finally:
            # Clean up prompt file
            try:
                prompt_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _build_agent_command(self, agent_cmd: str, prompt_file: str) -> list:
        """Build the command to invoke the CLI agent.

        Different agents have different invocation patterns.
        """
        agent_name = Path(agent_cmd).name.lower()

        if agent_name in ("claude", "z-code"):
            # Claude Code / z-code: supports --print for non-interactive mode
            return [
                agent_cmd, "--print",
                "--prompt-file", prompt_file,
                "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
            ]
        elif agent_name == "codex":
            # OpenAI Codex CLI
            return [
                agent_cmd,
                "--prompt-file", prompt_file,
                "--auto-approve",
            ]
        elif agent_name in ("aider",):
            return [
                agent_cmd,
                "--message-file", prompt_file,
                "--yes",
                "--no-auto-commits",
            ]
        else:
            # Generic: try passing prompt via stdin
            # Caller should pipe the prompt file content
            return [agent_cmd, "--prompt-file", prompt_file]

    def _parse_agent_result(self, output: str) -> dict:
        """Try to extract a JSON result from the agent's output.

        Uses bracket-matching to handle nested JSON (arrays, objects).
        Scans backwards so we find the LAST JSON object (the final result).
        """
        # Strategy 1: Find JSON objects by bracket-matching (handles nesting)
        candidates = []
        for i in range(len(output) - 1, -1, -1):
            if output[i] == '}':
                # Walk backwards to find the matching '{'
                depth = 0
                for j in range(i, -1, -1):
                    if output[j] == '}':
                        depth += 1
                    elif output[j] == '{':
                        depth -= 1
                    if depth == 0:
                        candidate = output[j:i + 1]
                        if '"status"' in candidate:
                            try:
                                return json.loads(candidate)
                            except json.JSONDecodeError:
                                candidates.append(candidate)
                        break
                # Only check the last few candidates
                if len(candidates) > 5:
                    break

        # Strategy 2: Try code blocks (```json ... ```)
        blocks = re.findall(r'```(?:json)?\s*(\{.+?\})\s*```', output, re.DOTALL)
        for block in reversed(blocks):
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue

        return {}

    # ── Smart (no-agent) update ──────────────────────────────────────────

    def run_smart_update(self, analysis: dict) -> dict:
        """Heuristic-based update when no CLI agent is available.

        This is the fallback that works everywhere without an LLM.
        It's careful: backs up, stashes, pulls, re-applies, verifies, rolls back
        on failure.
        """
        previous_commit = analysis["current_commit"]
        backed_up = []

        try:
            # ── Step 1: Back up conflicting files ────────────────────────
            for conflict in analysis["conflicts"]:
                src = self.app_dir / conflict["file"]
                if src.exists():
                    backup = Path(str(src) + ".pre-update")
                    try:
                        shutil.copy2(src, backup)
                        backed_up.append(conflict["file"])
                        logger.info(f"[update] Backed up: {conflict['file']}")
                    except Exception as exc:
                        logger.warning(
                            f"[update] Could not back up {conflict['file']}: {exc}"
                        )

            # ── Step 2: Fetch + advance HEAD (bind-mount safe) ──────────────
            # Docker bind mounts make some files read-only — `git pull` and
            # `git reset --hard` both fail when they can't overwrite them.
            # Solution: fetch, then advance HEAD+index without touching the
            # working tree (--mixed), then selectively checkout non-mounted
            # files. Bind-mounted files (our customisations) stay untouched —
            # which is exactly what we want.
            bind_mounted = set(
                c["file"] for c in analysis["conflicts"] if c.get("bind_mounted")
            )
            all_bind_mounts = set(analysis.get("bind_mounts", []))

            # Unstage any staged changes from soft-reset state
            self._git("reset", "HEAD")

            # Fetch latest
            fetch = self._git("fetch", "origin", "main", timeout=60)
            if fetch.returncode != 0:
                return {
                    "status": "failed",
                    "method": "smart",
                    "reason": f"git fetch failed: {fetch.stderr[:200]}",
                }

            # Move HEAD + index to origin/main without touching working tree
            self._git("reset", "--mixed", "origin/main")
            logger.info("[update] HEAD advanced to origin/main (mixed reset)")

            # Checkout each changed file EXCEPT bind-mounted ones
            checkout_failed = []
            for f in analysis["changed_files"]:
                if f in bind_mounted or f in all_bind_mounts:
                    logger.info(f"[update] Skipping bind-mounted: {f}")
                    continue
                co = self._git("checkout", "HEAD", "--", f)
                if co.returncode != 0:
                    # File might be deleted upstream or directory issue
                    logger.warning(f"[update] Could not checkout {f}: {co.stderr[:100]}")
                    checkout_failed.append(f)
                else:
                    logger.info(f"[update] Updated: {f}")
            new_commit = self._current_commit()

            # ── Step 4: (Backups are restored in Step 5 below) ────────────
            stash_conflicts = []

            # ── Step 5: Restore backed-up customisations ─────────────────
            restored = []
            for fname in backed_up:
                backup = self.app_dir / (fname + ".pre-update")
                target = self.app_dir / fname

                # Check if the backup differs from what git pulled
                # (i.e., user had customisations that were overwritten)
                if backup.exists() and target.exists():
                    try:
                        if backup.read_bytes() != target.read_bytes():
                            # The upstream changed this file AND user had
                            # customisations. Keep the user's version but
                            # log a warning.
                            shutil.copy2(backup, target)
                            restored.append(fname)
                            logger.info(
                                f"[update] Restored local customisation: {fname}"
                            )
                    except Exception:
                        pass

            # ── Step 6: Install new dependencies ─────────────────────────
            if "requirements.txt" in analysis["changed_files"]:
                pip = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     "-r", "requirements.txt", "--quiet"],
                    cwd=str(self.app_dir),
                    capture_output=True, text=True, timeout=120,
                )
                if pip.returncode != 0:
                    logger.warning(f"[update] pip install issues: {pip.stderr[:200]}")

            # ── Step 7: Update version.json ──────────────────────────────
            try:
                branch = self._current_branch()
                version_file = self.app_dir / "version.json"
                version_file.write_text(json.dumps({
                    "commit": new_commit,
                    "branch": branch,
                    "date": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                }))
            except Exception:
                pass

            # ── Step 8: Verify ───────────────────────────────────────────
            verify = self.verify()
            if not verify["healthy"]:
                logger.error("[update] Post-update verification FAILED — rolling back")
                self.rollback(previous_commit, backed_up)
                return {
                    "status": "rolled_back",
                    "method": "smart",
                    "reason": verify.get("error", "Health check failed"),
                    "previous_commit": previous_commit,
                }

            return {
                "status": "success",
                "method": "smart",
                "previous_commit": previous_commit,
                "new_commit": new_commit,
                "files_updated": analysis["changed_files"],
                "customisations_preserved": restored,
                "stash_conflicts": stash_conflicts,
                "warnings": (
                    [f"Stash conflicts in: {stash_conflicts}"]
                    if stash_conflicts else []
                ),
            }

        except Exception as exc:
            logger.error(f"[update] Smart update failed: {exc}")
            self.rollback(previous_commit, backed_up)
            return {
                "status": "failed",
                "method": "smart",
                "reason": str(exc),
            }

    # ── Verification ─────────────────────────────────────────────────────

    def verify(self) -> dict:
        """Post-update health checks."""
        errors = []

        # 1. Can we import the app?
        try:
            result = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, '.'); "
                 "from server import app; print('OK')"],
                cwd=str(self.app_dir),
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0 or "OK" not in result.stdout:
                errors.append(f"Import check failed: {result.stderr[:200]}")
        except Exception as exc:
            errors.append(f"Import check error: {exc}")

        # 2. Check that key route files parse
        for route_file in (self.app_dir / "routes").glob("*.py"):
            try:
                result = subprocess.run(
                    [sys.executable, "-c", f"import ast; ast.parse(open('{route_file}').read())"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0:
                    errors.append(f"Syntax error in {route_file.name}")
            except Exception:
                pass

        # 3. Check requirements satisfied
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "check"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                errors.append(f"Dependency issues: {result.stdout[:200]}")
        except Exception:
            pass

        return {
            "healthy": len(errors) == 0,
            "errors": errors,
        }

    # ── Rollback ─────────────────────────────────────────────────────────

    def rollback(self, target_commit: str, backed_up_files: list = None):
        """Roll back to a previous commit and restore backups."""
        logger.info(f"[update] Rolling back to {target_commit}")

        self._git("reset", "--hard", target_commit)

        if backed_up_files:
            for fname in backed_up_files:
                backup = self.app_dir / (fname + ".pre-update")
                target = self.app_dir / fname
                if backup.exists():
                    try:
                        shutil.copy2(backup, target)
                    except Exception:
                        pass

    # ── Main entry point ─────────────────────────────────────────────────

    def apply_update(self) -> dict:
        """Run the full intelligent update flow.

        Returns a dict with status, method used, and details.
        """
        logger.info("[update] ═══════════════════════════════════════════")
        logger.info("[update] Starting intelligent update...")

        # ── Step 1: Pre-flight analysis ──────────────────────────────────
        analysis = self.analyze()
        if not analysis.get("can_update"):
            return {
                "status": "error",
                "error": analysis.get("error", "Cannot update"),
            }

        if not analysis["changed_files"]:
            return {"status": "current", "message": "Already up to date"}

        logger.info(
            f"[update] {analysis['changed_file_count']} files changed, "
            f"risk={analysis['risk']}, "
            f"conflicts={len(analysis['conflicts'])}"
        )

        # ── Step 2: Try agent-assisted update ────────────────────────────
        agent = self.detect_cli_agent()

        if agent and (analysis["risk"] != "low" or analysis["conflicts"]):
            # Use agent for medium/high risk or when conflicts exist
            result = self.run_agent_update(agent, analysis)

            if not result.get("fallback"):
                # Agent handled it (success or deliberate rollback)
                self._schedule_restart(result)
                return result

            # Agent failed — fall through to smart update
            logger.info("[update] Agent failed, falling back to smart update")

        # ── Step 3: Smart update (fallback or low-risk) ──────────────────
        result = self.run_smart_update(analysis)

        if result["status"] == "success":
            self._schedule_restart(result)

        return result

    def _schedule_restart(self, result: dict):
        """Schedule a process restart after the HTTP response is sent."""
        def _do_restart():
            time.sleep(1.5)
            logger.info("[update] Restarting server process...")
            os.execv(sys.executable, [sys.executable] + sys.argv)

        if result.get("status") == "success":
            threading.Thread(target=_do_restart, daemon=True).start()

    # ── Analysis-only endpoint (no actual update) ────────────────────────

    def get_update_preview(self) -> dict:
        """Return a preview of what the update would do, without applying it.

        Used by the frontend to show the user what's coming before they confirm.
        """
        analysis = self.analyze()
        if not analysis.get("can_update"):
            return analysis

        agent = self.detect_cli_agent()

        return {
            "can_update": True,
            "current_commit": analysis["current_commit"],
            "changed_files": analysis["changed_files"],
            "changed_file_count": analysis["changed_file_count"],
            "incoming_commits": analysis["incoming_commits"],
            "conflicts": analysis["conflicts"],
            "risk": analysis["risk"],
            "high_risk_files": analysis["high_risk_files_changed"],
            "local_modifications": len(analysis["local_modifications"]),
            "plugins": analysis["plugins"],
            "bind_mounts": analysis["bind_mounts"],
            "custom_routes": analysis["custom_routes"],
            "agent_available": agent["label"] if agent else None,
            "update_method": (
                f"AI-assisted ({agent['label']})" if agent
                else "Smart update (heuristic)"
            ),
        }
