"""Terminal sandbox — what the /ws/terminal child process becomes.

The terminal canvas block gets a real shell. That shell must NOT be the
server's own identity: the server's environment holds every platform key, and
its uid owns the db, vault and code. So the child execs a small setuid helper
(deploy/terminal-sandbox/ovu-sandbox-shell.c) that drops to the unprivileged
`sandbox` user with a fresh environment and resource limits before starting
bash.

Modes:
  required  the helper must be present, else the terminal is refused. ALWAYS
            the mode on a hosted tenant (CANVAS_REQUIRE_AUTH=true), whatever
            TERMINAL_SANDBOX says.
  auto      helper if present, else a same-user shell with a scrubbed env
            (local / self-hosted installs, which have no helper; TERMINAL_SANDBOX=auto)

Everything the child needs is computed BEFORE fork (plan_terminal_child), so
the forked child of this multithreaded server only calls execve.

Session close: the server cannot signal a uid-1500 process, and a shell can
leave `setsid nohup ... &` survivors that outlive the pty hangup. When the LAST
open terminal session closes, the server runs the helper's one other mode,
`--reap`, which drops to the sandbox uid and kill(-1, SIGKILL)s every sandbox
process (session_opened / session_closed).
"""

import os
import subprocess
import threading
import time

SANDBOX_HELPER = '/usr/local/libexec/ovu-sandbox-shell'
SANDBOX_FALLBACK_UID = 1500
_sandbox_uid_cache = {}


def sandbox_uid(helper: str = None) -> int:
    """This container's sandbox uid, as the helper derives it (`--uid`).

    Per container (from its IPv4 address) so RLIMIT_NPROC, which the kernel counts
    per uid host-wide, is per tenant (review 2 of PR #597, M-A). Cached per helper;
    falls back to the image's `sandbox` uid if the helper cannot say."""
    helper = helper or SANDBOX_HELPER
    if helper not in _sandbox_uid_cache:
        uid = SANDBOX_FALLBACK_UID
        try:
            r = subprocess.run([helper, '--uid'], env={}, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=5, close_fds=True)
            if r.returncode == 0 and r.stdout.strip().isdigit():
                uid = int(r.stdout.strip())
        except Exception:
            pass
        _sandbox_uid_cache[helper] = uid
    return _sandbox_uid_cache[helper]

# The only variables a same-user (local-mode) shell inherits. Everything else —
# API keys, tokens, Clerk secrets, signing keys — is dropped.
_LOCAL_ENV_KEEP = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TZ', 'SHELL')

_sessions_lock = threading.Lock()
_open_sessions = 0


class TerminalSandboxUnavailable(RuntimeError):
    """The sandbox is required here but the helper is missing."""


def _hosted() -> bool:
    return os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true'


def sandbox_mode() -> str:
    if _hosted():
        return 'required'
    mode = os.getenv('TERMINAL_SANDBOX', '').strip().lower()
    return 'required' if mode == 'required' else 'auto'


def helper_available(helper: str = None) -> bool:
    helper = helper or SANDBOX_HELPER
    return os.path.isfile(helper) and os.access(helper, os.X_OK)


def scrubbed_env(source=None) -> dict:
    """Minimal environment for a shell: never any inherited secret."""
    source = os.environ if source is None else source
    env = {k: source[k] for k in _LOCAL_ENV_KEEP if source.get(k)}
    env['TERM'] = 'xterm-256color'
    env.setdefault('PATH', '/usr/local/bin:/usr/bin:/bin')
    return env


def plan_terminal_child(workspace, helper: str = None, mode: str = None):
    """Return (path, argv, env, cwd) for the child to execve, or raise.

    cwd is None when the helper chooses the directory itself.
    """
    helper = helper or SANDBOX_HELPER
    mode = mode or sandbox_mode()
    if helper_available(helper):
        # The helper takes no args and ignores env; pass an empty one anyway.
        return helper, [helper], {}, None
    if mode == 'required':
        raise TerminalSandboxUnavailable(
            f'terminal sandbox helper missing at {helper}; refusing a shell as the server user')
    env = scrubbed_env()
    shell = '/bin/bash' if os.path.exists('/bin/bash') else '/bin/sh'
    return shell, [shell, '-l'], env, str(workspace)


def exec_terminal_child(plan) -> None:
    """In the forked child: become the planned process. Never returns."""
    path, argv, env, cwd = plan
    try:
        if cwd:
            try:
                os.chdir(cwd)
            except OSError:
                pass
        os.execve(path, argv, env)
    except BaseException:
        pass
    os._exit(127)


def session_opened() -> None:
    global _open_sessions
    with _sessions_lock:
        _open_sessions += 1


def session_closed(plan, helper: str = None) -> bool:
    """Count a session closed; when none remain, reap every sandbox process.

    Only for sandboxed sessions (the plan exec'd the helper). The reap runs under
    the session lock so a session opening at the same moment spawns its shell
    after the reap, never before it. Returns True when a reap ran successfully.
    """
    global _open_sessions
    helper = helper or SANDBOX_HELPER
    with _sessions_lock:
        _open_sessions = max(0, _open_sessions - 1)
        if _open_sessions or plan is None or plan[0] != helper:
            return False
        try:
            r = subprocess.run([helper, '--reap'], env={}, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, close_fds=True)
            ok = r.returncode == 0
        except Exception:
            ok = False
        _reap_sandbox_zombies(sandbox_uid(helper))
        return ok


def _sandbox_tasks(uid):
    """[(pid, ppid, state)] of every process whose real uid is `uid`."""
    out = []
    try:
        names = os.listdir('/proc')
    except OSError:
        return out
    for d in names:
        if not d.isdigit():
            continue
        try:
            with open(f'/proc/{d}/status') as fh:
                fields = dict(ln.split(':', 1) for ln in fh if ':' in ln)
            if fields.get('Uid', '').split()[0] != str(uid):
                continue
            out.append((int(d), int(fields.get('PPid', '0').strip() or 0),
                        fields.get('State', '?').strip()[:1]))
        except (OSError, ValueError, IndexError):
            continue
    return out


def _reap_sandbox_zombies(uid, timeout=2.0) -> None:
    """Collect the killed sandbox processes that were re-parented to THIS process.

    In the container the server is pid 1, so every orphaned sandbox process (a
    setsid/nohup survivor) becomes our child; killed and never waited for, it
    stays a zombie that still counts against the sandbox's RLIMIT_NPROC. Only
    sandbox-uid zombies whose parent is us are waited for, by pid, so no other
    thread's subprocess is ever reaped out from under it."""
    me = os.getpid()
    end = time.time() + timeout
    while True:
        tasks = _sandbox_tasks(uid)
        for pid, ppid, state in tasks:
            if ppid == me and state == 'Z':
                try:
                    os.waitpid(pid, os.WNOHANG)
                except OSError:
                    pass
        if not any(state != 'Z' or ppid == me for pid, ppid, state in _sandbox_tasks(uid)):
            return
        if time.time() >= end:
            return
        time.sleep(0.05)
