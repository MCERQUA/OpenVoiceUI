#!/bin/sh
# Waits for the OpenClaw gateway's TCP port to accept connections before
# starting the Flask app. Docker restarts sibling containers without
# compose `depends_on` ordering (e.g. after a host reboot), so
# openvoiceui can otherwise come up before openclaw is listening — the
# boot warm-up thread in server.py now retries indefinitely, but starting
# the app only after the port answers avoids the failed-attempt noise in
# the common case. Bounded: proceeds after 120s regardless, since
# server.py's warm-up and per-request lazy connect are the real fallback
# and must never be blocked forever by this wait.
#
# Reads CLAWDBOT_GATEWAY_URL (ws://host:port[/path]) — the same env var
# server.py and services/gateways/openclaw.py read. If unset, skips the
# wait entirely.

set -e

GATEWAY_URL="${CLAWDBOT_GATEWAY_URL:-}"

if [ -n "$GATEWAY_URL" ]; then
    # Strip scheme, then trailing path, then split host:port.
    HOSTPORT=$(echo "$GATEWAY_URL" | sed -E 's#^wss?://##' | cut -d/ -f1)
    GW_HOST=$(echo "$HOSTPORT" | cut -d: -f1)
    GW_PORT=$(echo "$HOSTPORT" | cut -d: -f2)

    if [ -n "$GW_HOST" ] && [ -n "$GW_PORT" ]; then
        WAITED=0
        MAX_WAIT=120
        LAST_LOG=-10
        while ! python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('$GW_HOST', $GW_PORT))
except Exception:
    sys.exit(1)
finally:
    s.close()
" 2>/dev/null; do
            if [ "$WAITED" -ge "$MAX_WAIT" ]; then
                echo "entrypoint: gateway $GW_HOST:$GW_PORT not up after ${MAX_WAIT}s — starting anyway (lazy connect will retry)"
                break
            fi
            if [ $((WAITED - LAST_LOG)) -ge 10 ]; then
                echo "entrypoint: waiting for gateway $GW_HOST:$GW_PORT ... (${WAITED}s)"
                LAST_LOG=$WAITED
            fi
            sleep 2
            WAITED=$((WAITED + 2))
        done
    fi
fi

exec "$@"
