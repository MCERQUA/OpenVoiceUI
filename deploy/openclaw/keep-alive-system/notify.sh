#!/usr/bin/env bash
# =============================================================================
# keep-alive-system :: notify.sh
# Send notifications through the OpenClaw gateway to the agent session
# Usage: ./notify.sh <level> <title> [body]
# Levels: info, warn, error, recovery
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SELF_DIR}/keep-alive.conf"

level="${1:-info}"
title="${2:-Keep-Alive Notification}"
body="${3:-}"

# --- Exit early if notifications disabled ---
if [[ "${NOTIFY_ENABLED}" != "true" ]]; then
    exit 0
fi

# --- Cooldown check ---
cooldown_dir="${SCRIPT_DIR}/.state"
mkdir -p "${cooldown_dir}"
cooldown_key=$(printf '%s:%s' "${level}" "${title}" | md5sum | cut -d' ' -f1)
cooldown_file="${cooldown_dir}/notify_${cooldown_key}"

if [[ -f "${cooldown_file}" ]]; then
    last_sent=$(cat "${cooldown_file}")
    now=$(date +%s)
    elapsed=$(( now - last_sent ))
    if (( elapsed < NOTIFY_COOLDOWN )); then
        exit 0
    fi
fi

# --- Map level to prefix ---
case "${level}" in
    info)     prefix="[INFO]"     ;;
    warn)     prefix="[WARN]"     ;;
    error)    prefix="[ERROR]"    ;;
    recovery) prefix="[OK]"       ;;
    *)        prefix="[NOTICE]"   ;;
esac

# --- Build the message ---
timestamp=$(date '+%Y-%m-%d %H:%M:%S')
hostname=$(hostname -s 2>/dev/null || echo "unknown")

message="${prefix} ${title}"
if [[ -n "${body}" ]]; then
    message="${message} — ${body}"
fi
message="${message} (${hostname}, ${timestamp})"

# --- Send through OpenClaw gateway via docker exec ---
# Uses `openclaw agent -m` to send directly to the agent's main session
# Short timeout so we don't block the monitor
send_result=$(timeout 30 docker exec "${OPENCLAW_CONTAINER}" \
    openclaw agent --agent openvoiceui -m "${message}" --timeout 15 --json 2>&1) || {
    # Gateway is down or unreachable — log only, don't fail
    echo "[${timestamp}] notify: send failed (gateway unreachable): ${title}" >> "${LOG_DIR}/keep-alive.log"
    exit 0
}

# --- Record send time for cooldown ---
date +%s > "${cooldown_file}"

exit 0
