"""
/ws/terminal hands its user a real shell. That shell must be a sandbox, not
the server's identity (CCA gate-4 re-check #6, finding T): before this fix the
child was a login shell that inherited the server's whole environment —
every platform key, CLERK_SECRET_KEY, CLAUDE_CODE_OAUTH_TOKEN — and ran as the
server's uid, so anyone holding a Clerk session or the tenant's agent key could
read them.

What these tests drive is server.py's own terminal_websocket (lifted out of the
source with ast, same approach as test_push_socket.py, because server.py cannot
be imported outside the container), with a fake websocket and a REAL pty child:

  * on a hosted tenant (sandbox required) with no helper, the terminal is
    refused and no shell is spawned;
  * in local mode the shell's environment carries no secret names;
  * with a helper, the child execs exactly the helper, with no arguments and an
    empty environment.

The uid separation itself (child uid != server uid; no /proc/1/environ, /app or
/mnt access) needs the setuid helper inside the image: deploy/terminal-sandbox/
verify-image.sh proves that against a throwaway container.
"""

import ast
import json
import logging
import os
import queue
import threading
import time
from pathlib import Path

import pytest

import services.auth as auth_mod
import services.paths as paths_mod

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "server.py"

SECRET_NAMES = (
    "CLERK_SECRET_KEY", "AGENT_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY", "TWILIO_AUTH_TOKEN", "UPLOADS_SIGNING_KEY", "SECRET_KEY",
)
AGENT_KEY = "test-agent-key-0123456789"


def _load_terminal_handler():
    tree = ast.parse(SERVER_PY.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "terminal_websocket":
            node.decorator_list = []
            break
    else:
        raise AssertionError("server.py has no terminal_websocket")

    class _Req:
        headers = {"X-Agent-Key": AGENT_KEY}

    ns = {"json": json, "os": os, "logger": logging.getLogger("test-terminal"), "request": _Req()}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SERVER_PY), "exec"), ns)
    return ns["terminal_websocket"]


class FakeWS:
    """flask_sock's ws: the handler's receive() blocks until the 'browser' sends."""

    def __init__(self):
        self._in = queue.Queue()
        self.out = []
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def receive(self, timeout=None):
        return self._in.get()

    def send(self, data):
        with self._lock:
            self.out.append(data)

    def close(self):
        self.closed.set()

    def type(self, text):
        self._in.put(json.dumps({"type": "input", "data": text}))

    def hangup(self):
        self._in.put(None)

    def text(self):
        with self._lock:
            return "".join(self.out)

    def wait_for(self, needle, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.text():
                return self.text()
            time.sleep(0.05)
        raise AssertionError(f"{needle!r} never arrived; got {self.text()[-2000:]!r}")


@pytest.fixture
def terminal(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_API_KEY", AGENT_KEY)
    for n in SECRET_NAMES:
        if n != "AGENT_API_KEY":
            monkeypatch.setenv(n, f"secret-value-of-{n.lower()}")
    monkeypatch.setattr(auth_mod, "get_token_from_request", lambda *a, **k: None)
    monkeypatch.setattr(auth_mod, "verify_clerk_token", lambda *a, **k: None)
    monkeypatch.setattr(paths_mod, "CODE_WORKSPACE_DIR", tmp_path / "code-workspace")
    try:
        import services.terminal_sandbox as ts
        monkeypatch.setattr(ts, "SANDBOX_HELPER", str(tmp_path / "no-such-helper"))
    except ImportError:  # pre-fix tree: no sandbox module at all
        pass
    return _load_terminal_handler()


def _run(handler, ws):
    t = threading.Thread(target=handler, args=(ws,), daemon=True)
    t.start()
    return t


def test_hosted_tenant_without_helper_refuses_instead_of_server_shell(terminal, monkeypatch):
    monkeypatch.setenv("CANVAS_REQUIRE_AUTH", "true")
    monkeypatch.delenv("TERMINAL_SANDBOX", raising=False)
    ws = FakeWS()
    t = _run(terminal, ws)
    ws.type("echo SHELL_IS_''ALIVE\n")
    t.join(timeout=5)
    alive = t.is_alive()
    ws.hangup()
    t.join(timeout=5)
    assert not alive, "handler kept a shell session open on a hosted tenant without the sandbox helper"
    assert "SHELL_IS_ALIVE" not in ws.text()
    assert "Terminal sandbox unavailable" in ws.text()


def test_local_shell_inherits_no_secret_names(terminal, monkeypatch):
    monkeypatch.setenv("TERMINAL_SANDBOX", "auto")
    ws = FakeWS()
    t = _run(terminal, ws)
    # __EN''D__ so the pty's echo of the command line never matches the marker
    ws.type("env | cut -d= -f1 | sort | tr '\\n' ' '; echo; echo __EN''D__\n")
    out = ws.wait_for("__END__")
    ws.type("exit\n")
    ws.hangup()
    t.join(timeout=5)
    env_line = out.split("__EN''D__")[-1].split("__END__")[0]
    leaked = [n for n in SECRET_NAMES if n in env_line]
    assert not leaked, f"terminal shell inherited server secrets: {leaked}"
    assert "PATH" in env_line  # it really is the env listing


def test_helper_is_exec_d_with_no_args_and_empty_env(terminal, monkeypatch, tmp_path):
    import services.terminal_sandbox as ts
    helper = tmp_path / "fake-helper"
    helper.write_text("#!/bin/sh\necho ARGC=$#\necho ENV_BEGIN; env | cut -d= -f1 | sort; echo ENV_""END\n")
    helper.chmod(0o755)
    monkeypatch.setattr(ts, "SANDBOX_HELPER", str(helper))
    monkeypatch.setenv("CANVAS_REQUIRE_AUTH", "true")
    ws = FakeWS()
    t = _run(terminal, ws)
    out = ws.wait_for("ENV_END")
    ws.hangup()
    t.join(timeout=5)
    assert "ARGC=0" in out
    names = set(out.split("ENV_BEGIN")[-1].split("ENV_END")[0].split())
    # /bin/sh itself adds PWD (and maybe SHLVL/OLDPWD); nothing comes from the server
    assert names <= {"PWD", "SHLVL", "OLDPWD", "_"}, names


def test_plan_unit_contract(tmp_path, monkeypatch):
    import services.terminal_sandbox as ts
    for n in SECRET_NAMES:
        monkeypatch.setenv(n, "x")
    with pytest.raises(ts.TerminalSandboxUnavailable):
        ts.plan_terminal_child(tmp_path, helper=str(tmp_path / "missing"), mode="required")
    path, argv, env, cwd = ts.plan_terminal_child(tmp_path, helper=str(tmp_path / "missing"), mode="auto")
    assert not set(env) & set(SECRET_NAMES)
    assert cwd == str(tmp_path) and argv[-1] == "-l"
    monkeypatch.setenv("CANVAS_REQUIRE_AUTH", "true")
    monkeypatch.delenv("TERMINAL_SANDBOX", raising=False)
    assert ts.sandbox_mode() == "required"
    monkeypatch.setenv("TERMINAL_SANDBOX", "auto")
    assert ts.sandbox_mode() == "auto"


def test_share_with_sandbox_makes_editor_files_group_writable(tmp_path):
    import services.terminal_sandbox as ts
    root = tmp_path / "ws"
    f = root / "a" / "b" / "file.txt"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    for p in (root, root / "a", root / "a" / "b", f):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)
    ts.share_with_sandbox(f, root)
    assert f.stat().st_mode & 0o060 == 0o060
    assert (root / "a").stat().st_mode & 0o070 == 0o070
    assert (root / "a" / "b").stat().st_mode & 0o070 == 0o070
    assert root.stat().st_mode & 0o020 == 0  # the root itself is not touched
