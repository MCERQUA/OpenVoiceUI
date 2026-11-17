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
echo "== image facts"
docker run --rm --network none --entrypoint sh "$TAG" -c \
    'stat -c "%a %U:%G %n" /app /mnt /srv/code-workspace /usr/local/libexec/ovu-sandbox-shell; id appuser; id sandbox'
echo "workdir: $WORK (db dir listing after the runs:)"; ls -la "$WORK/db"
