"""Terminal sandbox — what the /ws/terminal child process becomes.

The terminal canvas block gets a real shell. That shell must NOT be the
server's own identity: the server's environment holds every platform key, and
its uid owns the db, vault and code. So the child execs a small setuid helper
(deploy/terminal-sandbox/ovu-sandbox-shell.c) that drops to the unprivileged
`sandbox` user with a fresh environment before starting bash.

Modes (env TERMINAL_SANDBOX):
  required  helper must be present, else the terminal is refused (default on a
            hosted tenant, i.e. CANVAS_REQUIRE_AUTH=true)
  auto      helper if present, else a same-user shell with a scrubbed env
            (default for local / self-hosted installs, which have no helper)

Everything the child needs is computed BEFORE fork (plan_terminal_child), so
the forked child of this multithreaded server only calls execve.
"""

import os
from pathlib import Path

SANDBOX_HELPER = os.getenv('TERMINAL_SANDBOX_HELPER', '/usr/local/libexec/ovu-sandbox-shell')

# The only variables a same-user (local-mode) shell inherits. Everything else —
# API keys, tokens, Clerk secrets, signing keys — is dropped.
_LOCAL_ENV_KEEP = ('PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'TZ', 'SHELL')


class TerminalSandboxUnavailable(RuntimeError):
    """The sandbox is required here but the helper is missing."""


def sandbox_mode() -> str:
    mode = os.getenv('TERMINAL_SANDBOX', '').strip().lower()
    if mode in ('required', 'auto'):
        return mode
    if os.getenv('CANVAS_REQUIRE_AUTH', '').strip().lower() == 'true':
        return 'required'
    return 'auto'


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


def share_with_sandbox(path, root=None) -> None:
    """Make a server-written workspace file/dir group-writable for the sandbox.

    The server's umask is 022; without this a file saved from the Monaco editor
    is read-only in the terminal. With `root`, the directories between `path`
    and `root` that the server just created are shared as well. Best effort.
    """
    p = Path(path)
    targets = [p]
    if root is not None:
        root = Path(root)
        targets += [d for d in p.parents if root in d.parents]
    for t in targets:
        try:
            if t.is_symlink():
                continue
            mode = t.stat().st_mode & 0o7777
            os.chmod(t, mode | (0o2070 if t.is_dir() else 0o060))
        except OSError:
            pass
