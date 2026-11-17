"""Drive server.py's own /ws/terminal handler inside a throwaway container.

Run by verify-image.sh as the server user (appuser) with a fake secret in the
environment and fake tenant mounts. Lifts terminal_websocket out of
/app/server.py (as tests/test_terminal_sandbox.py does), feeds it a fake
websocket holding the agent key, types a probe script into the REAL pty shell
and prints what the shell could see. Nothing here opens a network socket.
"""
import ast
import json
import logging
import os
import queue
import sys
import threading
import time

sys.path.insert(0, "/app")
import services.auth as auth_mod  # noqa: E402

KEY = os.environ["AGENT_API_KEY"]
auth_mod.get_token_from_request = lambda *a, **k: None
auth_mod.verify_clerk_token = lambda *a, **k: None

import services.terminal_sandbox as ts  # noqa: E402
from services.events_file import append_event, ensure_private  # noqa: E402

if os.environ.get("PROBE_LEGACY") == "1":
    ts.SANDBOX_HELPER = "/nonexistent"  # negative control: the old same-user shell

# What the server does at boot, and then a private conversation event.
ensure_private()
append_event({"type": "conversation", "message": "owner said something private"})
# Inheritable fds the server might hold (one low, one above the old 65536 cap).
_fd = os.open("/app/server.py", os.O_RDONLY)
os.dup2(_fd, 9, inheritable=True)
os.dup2(_fd, 70000, inheritable=True)

tree = ast.parse(open("/app/server.py").read())
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "terminal_websocket")
node.decorator_list = []


class _Req:
    headers = {"X-Agent-Key": KEY}


ns = {"json": json, "os": os, "logger": logging.getLogger("probe"), "request": _Req()}
exec(compile(ast.Module(body=[node], type_ignores=[]), "/app/server.py", "exec"), ns)


class WS:
    def __init__(self):
        self.q, self.out = queue.Queue(), []

    def receive(self, timeout=None):
        return self.q.get()

    def send(self, d):
        self.out.append(d)

    def close(self):
        pass


PROBE = r"""
PS1=''
echo "@""@UID=$(id -u) GID=$(id -g) GROUPS=$(id -G)"
echo "@""@ENVNAMES=$(env | cut -d= -f1 | sort | tr '\n' ' ')"
echo "@""@CWD=$(pwd)"
cat /proc/1/environ >/dev/null 2>&1 && echo "@""@PROC1_ENVIRON=READABLE" || echo "@""@PROC1_ENVIRON=denied"
echo "@""@PROC1_SECRET_LINES=$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | grep -c FAKE_PLATFORM_SECRET)"
ls /app >/dev/null 2>&1 && echo "@""@APP=listable" || echo "@""@APP=denied"
cat /app/runtime/db/business.db >/dev/null 2>&1 && echo "@""@DB_FILE=READABLE" || echo "@""@DB_FILE=denied"
touch /app/runtime/db/planted 2>/dev/null && echo "@""@DB_DIR=WRITABLE" || echo "@""@DB_DIR=denied"
cat /mnt/system/base/fake-keys.env >/dev/null 2>&1 && echo "@""@KEYFILE=READABLE" || echo "@""@KEYFILE=denied"
echo hi > /app/server.py.probe 2>/dev/null && echo "@""@APP_CODE=WRITABLE" || echo "@""@APP_CODE=denied"
touch ./sandbox-made 2>/dev/null && echo "@""@WORKSPACE=writable" || echo "@""@WORKSPACE=denied"
su -c id root </dev/null >/dev/null 2>&1 && echo "@""@SU=ROOT" || echo "@""@SU=denied"
/usr/local/libexec/ovu-sandbox-shell </dev/null >/dev/null 2>&1 && echo "@""@HELPER_REENTRY=ALLOWED" || echo "@""@HELPER_REENTRY=denied"
cat <&9 >/dev/null 2>&1 && echo "@""@FD9=READABLE" || echo "@""@FD9=closed"
cat <&70000 >/dev/null 2>&1 && echo "@""@FD70000=READABLE" || echo "@""@FD70000=closed"
[ -r /tmp/openvoiceui-events.jsonl ] && echo "@""@EVENTS_FILE=READABLE" || echo "@""@EVENTS_FILE=denied"
echo "@""@ULIMITS=nproc:$(ulimit -u) as_kb:$(ulimit -v) cpu_s:$(ulimit -t) fsize_blk:$(ulimit -f) nofile:$(ulimit -n)"
setsid nohup sleep 300 >/dev/null 2>&1 </dev/null &
echo "@""@PROBE_DONE=1"
exit
"""

ws = WS()
t = threading.Thread(target=ns["terminal_websocket"], args=(ws,), daemon=True)
t.start()
time.sleep(0.5)
ws.q.put(json.dumps({"type": "input", "data": PROBE}))
deadline = time.time() + 20
while time.time() < deadline and "@@PROBE_DONE=1" not in "".join(ws.out):
    time.sleep(0.1)
ws.q.put(None)
t.join(timeout=5)
text = "".join(ws.out).replace("\r", "")
import re  # noqa: E402
for key, val in re.findall(r"@@([A-Z0-9_]+)=([^\n]*)", text):
    print(f"{key}={val.strip()}")
for line in text.splitlines():
    if "refused" in line or "unavailable" in line:
        print(line.strip())
print(f"SERVER_UID={os.getuid()}")


def _procs_of(uid, zombies=False):
    """Count live (or, with zombies=True, zombie) processes whose real uid is uid."""
    n = 0
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/status") as fh:
                    f = dict(ln.split(":", 1) for ln in fh if ":" in ln)
                if f["Uid"].split()[0] == str(uid) and (f["State"].strip().startswith("Z") == zombies):
                    n += 1
            except (OSError, KeyError):
                pass
    return n


time.sleep(2)  # the handler has closed the pty master; the shell must be gone
print(f"SANDBOX_PROCS_AFTER_CLOSE={_procs_of(1500)}")
print(f"SANDBOX_ZOMBIES_AFTER_CLOSE={_procs_of(1500, zombies=True)}")
print(f"SERVER_UID_PROCS_BESIDES_SERVER={_procs_of(os.getuid()) - 1}")
