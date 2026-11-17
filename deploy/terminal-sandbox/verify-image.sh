#!/bin/bash
# Prove the terminal sandbox in a throwaway container (no network, no tenant data).
#
#   deploy/terminal-sandbox/verify-image.sh [base-image]
#
# Builds a small overlay of THIS checkout's server.py + services/terminal_sandbox.py
# + deploy/terminal-sandbox/ on top of base-image (default jambot/openvoiceui:latest),
# running the same install.sh the Dockerfile runs, then drives server.py's own
# /ws/terminal handler (verify_probe.py) with:
#   - a fake platform secret in the server's environment
#   - a world-writable (0777) "db" dir mounted at /app/runtime/db
#   - a 0644 key file mounted under /mnt/system/base
#   - the tenant runtime's extra group (--group-add 1002)
# Expected (sandbox):   UID=1500, no secret env names, every read/write denied,
#                       WORKSPACE=writable, SU=denied, HELPER_REENTRY=denied,
#                       FD9/FD70000 closed, EVENTS_FILE denied, the helper's
#                       rlimits in ULIMITS, and SANDBOX_PROCS_AFTER_CLOSE=0 even
#                       though the shell left a `setsid nohup sleep` behind.
# Per-tenant uid: a proof-only helper build stands in for two containers (A, B) with
#   different addresses: A exhausting its NPROC must not stop B forking, while a
#   second shell with A's address (same uid, same budget) is refused (negative control).
# Negative control (PROBE_LEGACY=1: helper hidden, not hosted): the old same-user shell,
# which must show PROC1_ENVIRON=READABLE / DB_FILE=READABLE — proving the probe
# can see a leak when there is one.
set -euo pipefail
BASE="${1:-jambot/openvoiceui:latest}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="ovu-terminal-sandbox-verify:local"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ovu-sbx-verify.XXXXXX")"

mkdir -p "$WORK/ctx" "$WORK/db"
# Overlay = every runtime file this checkout changes vs origin/main (plus new ones),
# so the probe drives exactly this branch's server code on the base image.
OVERLAY="$(cd "$REPO" && { git diff --name-only origin/main -- '*.py'; git ls-files --others --exclude-standard -- '*.py'; } \
           | grep -v '^tests/' | sort -u)"
COPIES=""
for f in $OVERLAY; do
    [ -f "$REPO/$f" ] || continue
    mkdir -p "$WORK/ctx/$(dirname "$f")"; cp "$REPO/$f" "$WORK/ctx/$f"
    COPIES="$COPIES
COPY --chown=appuser:appuser $f /app/$f"
done
mkdir -p "$WORK/ctx/deploy"; cp -r "$REPO/deploy/terminal-sandbox" "$WORK/ctx/deploy/"
cat > "$WORK/ctx/Dockerfile" <<EOF
FROM $BASE
USER root
$COPIES
COPY --chown=appuser:appuser deploy/terminal-sandbox/ /app/deploy/terminal-sandbox/
RUN sh /app/deploy/terminal-sandbox/install.sh
# PROOF-ONLY variant (never in the real image): takes the container address from
# OVU_TEST_IPV4 so two --network none runs can stand in for two tenants.
RUN gcc -O2 -Wall -Wextra -Werror -DOVU_SANDBOX_TEST_BUILD -DCALLER_UID=1001 \
        -DSANDBOX_FALLBACK_UID=1500 -DSANDBOX_GID=1500 '-DSANDBOX_WORKSPACE="/srv/code-workspace"' \
        -o /usr/local/libexec/ovu-sandbox-shell-testaddr /app/deploy/terminal-sandbox/ovu-sandbox-shell.c \
    && chown root:appuser /usr/local/libexec/ovu-sandbox-shell-testaddr \
    && chmod 4750 /usr/local/libexec/ovu-sandbox-shell-testaddr
ENV CODE_WORKSPACE_DIR=/srv/code-workspace
USER appuser
EOF
docker build -q -t "$TAG" "$WORK/ctx" >/dev/null

echo "fake business db" > "$WORK/db/business.db"
chmod 0777 "$WORK/db"; chmod 0666 "$WORK/db/business.db"
echo "FAKE_KEY=not-a-real-key" > "$WORK/fake-keys.env"; chmod 0644 "$WORK/fake-keys.env"

run() {  # $1 = label, rest = extra docker args
    local label="$1"; shift
    echo "== $label"
    docker run --rm --network none --memory 512m --group-add 1002 --ulimit nofile=131072:131072 \
        -e AGENT_API_KEY=probe-agent-key -e FAKE_PLATFORM_SECRET=probe-secret-value \
        -e CANVAS_REQUIRE_AUTH=true \
        -v "$WORK/db:/app/runtime/db" \
        -v "$WORK/fake-keys.env:/mnt/system/base/fake-keys.env:ro" \
        "$@" --entrypoint python3 "$TAG" /app/deploy/terminal-sandbox/verify_probe.py
}
run "sandbox (hosted tenant, helper present)"
run "negative control (legacy same-user shell)" -e PROBE_LEGACY=1 -e CANVAS_REQUIRE_AUTH=false -e TERMINAL_SANDBOX=auto
echo "== per-tenant sandbox uid: NPROC is per tenant, not host-wide (review 2, M-A)"
T=/usr/local/libexec/ovu-sandbox-shell-testaddr
mkdir -p "$WORK/nproc"
cat > "$WORK/nproc/fill.py" <<'PY'
import os, time
n = 0
while n < 400:
    try:
        pid = os.fork()
    except OSError:
        break
    if pid == 0:
        time.sleep(40)
        os._exit(0)
    n += 1
print(f"A_CHILDREN_LAUNCHED={n}", flush=True)
time.sleep(30)
PY
cat > "$WORK/nproc/forks.py" <<'PY'
import os, sys
ok = 0
for _ in range(20):
    try:
        pid = os.fork()
    except OSError:
        continue
    if pid == 0:
        os._exit(0)
    os.waitpid(pid, 0)
    ok += 1
print(f"{sys.argv[1]}_FORKS_OK={ok}/20", flush=True)
PY
printf 'exec python3 /nproc/fill.py\n' > "$WORK/nproc/A.sh"
printf 'exec python3 /nproc/forks.py B\n' > "$WORK/nproc/B.sh"
printf 'exec python3 /nproc/forks.py A2_SAME_ADDRESS\n' > "$WORK/nproc/A2.sh"
chmod -R a+rX "$WORK/nproc"
probe() {  # $1 script name, $2 container address (proof helper only)
    docker run --rm --network none --memory 512m -v "$WORK/nproc:/nproc:ro" --entrypoint sh "$TAG" -c \
        "echo \"$1 sandbox_uid=\$(OVU_TEST_IPV4=$2 $T --uid)\"; OVU_TEST_IPV4=$2 $T < /nproc/$1.sh 2>&1"
}
# tenant A fills its own process budget (its children exit by themselves after 40 s)
probe A 10.255.0.5 > "$WORK/nproc-A.out" 2>&1 &
sleep 10
probe B 10.255.0.6 2>&1 | grep -E "sandbox_uid=|FORKS|fork" || true
probe A2 10.255.0.5 2>&1 | grep -E "sandbox_uid=|FORKS|fork" | sort | uniq -c || true
wait || true
grep -E "sandbox_uid=|A_CHILDREN" "$WORK/nproc-A.out" || true
echo "== image facts"
docker run --rm --network none --entrypoint sh "$TAG" -c \
    'stat -c "%a %U:%G %n" /app /mnt /srv/code-workspace /usr/local/libexec/ovu-sandbox-shell; id appuser; id sandbox'
echo "workdir: $WORK (db dir listing after the runs:)"; ls -la "$WORK/db"
