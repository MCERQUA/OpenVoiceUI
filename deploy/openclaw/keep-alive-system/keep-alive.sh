#!/usr/bin/env bash
# =============================================================================
# keep-alive-system :: keep-alive.sh
# Host-level health monitor with tiered recovery for Docker Compose services
# Usage: ./keep-alive.sh [--once|--status]
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SELF_DIR}/keep-alive.conf"

STATE_DIR="${SCRIPT_DIR}/.state"
LOG_FILE="${LOG_DIR}/keep-alive.log"
PID_FILE="${STATE_DIR}/keep-alive.pid"

mkdir -p "${STATE_DIR}" "${LOG_DIR}"

# =============================================================================
# Logging
# =============================================================================
log() {
    local level="$1"; shift
    local ts
    ts=$(date '+%Y-%m-%d %H:%M:%S')
    printf '[%s] [%s] %s\n' "${ts}" "${level}" "$*" >> "${LOG_FILE}"
    printf '[%s] [%s] %s\n' "${ts}" "${level}" "$*"
}

rotate_log() {
    if [[ -f "${LOG_FILE}" ]]; then
        local size
        size=$(stat -c%s "${LOG_FILE}" 2>/dev/null || echo 0)
        if (( size > LOG_MAX_BYTES )); then
            for (( i=LOG_KEEP_COUNT; i>1; i-- )); do
                local prev=$(( i - 1 ))
                [[ -f "${LOG_FILE}.${prev}" ]] && mv "${LOG_FILE}.${prev}" "${LOG_FILE}.${i}"
            done
            mv "${LOG_FILE}" "${LOG_FILE}.1"
            log "INFO" "Log rotated"
        fi
    fi
}

# =============================================================================
# State helpers
# =============================================================================
read_counter() {
    local file="${STATE_DIR}/$1"
    if [[ -f "${file}" ]]; then
        cat "${file}"
    else
        echo "0"
    fi
}

write_counter() {
    printf '%s' "$2" > "${STATE_DIR}/$1"
}

increment_counter() {
    local name="$1"
    local current
    current=$(read_counter "${name}")
    local new_val=$(( current + 1 ))
    write_counter "${name}" "${new_val}"
    echo "${new_val}"
}

reset_counter() {
    write_counter "$1" "0"
}

# =============================================================================
# Health checks
# =============================================================================

check_container_health() {
    local container="$1"

    if ! docker inspect --format='{{.State.Running}}' "${container}" 2>/dev/null | grep -q "true"; then
        echo "not_running"
        return 1
    fi

    local health_status
    health_status=$(docker inspect --format='{{.State.Health.Status}}' "${container}" 2>/dev/null || echo "unknown")
    echo "${health_status}"

    if [[ "${health_status}" == "healthy" ]]; then
        return 0
    else
        return 1
    fi
}

check_openclaw_deep() {
    local result
    result=$(timeout "${HEALTH_EXEC_TIMEOUT}" \
        docker exec "${OPENCLAW_CONTAINER}" openclaw health --json 2>/dev/null) || {
        echo "exec_failed"
        return 1
    }

    local ok_value
    ok_value=$(printf '%s' "${result}" | jq -r '.ok' 2>/dev/null || echo "false")

    if [[ "${ok_value}" == "true" ]]; then
        echo "ok"
        return 0
    else
        echo "unhealthy"
        return 1
    fi
}

check_openvoiceui_http() {
    local http_code
    http_code=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time "${OPENVOICEUI_HTTP_TIMEOUT}" \
        "${OPENVOICEUI_HEALTH_URL}" 2>/dev/null || echo "000")

    if [[ "${http_code}" =~ ^2 ]]; then
        echo "ok"
        return 0
    else
        echo "http_${http_code}"
        return 1
    fi
}

# =============================================================================
# Recovery tiers
# =============================================================================

recover_openclaw() {
    local failures="$1"
    local tier

    if (( failures >= TIER4_THRESHOLD )); then
        tier=4
    elif (( failures >= TIER3_THRESHOLD )); then
        tier=3
    elif (( failures >= TIER2_THRESHOLD )); then
        tier=2
    elif (( failures >= TIER1_THRESHOLD )); then
        tier=1
    else
        return 0
    fi

    log "WARN" "OpenClaw recovery tier ${tier} (failures: ${failures})"

    case ${tier} in
        1)
            log "INFO" "Tier 1: Running doctor --repair inside container"
            local repair_output
            repair_output=$(timeout 30 \
                docker exec "${OPENCLAW_CONTAINER}" \
                openclaw doctor --repair --non-interactive 2>&1 || echo "repair command failed")
            log "INFO" "Doctor output: ${repair_output:0:500}"
            "${SELF_DIR}/notify.sh" warn "Health Check Failed" \
                "Failure #${failures}. Running doctor --repair."
            ;;
        2)
            log "INFO" "Tier 2: Restarting openclaw container"
            (cd "${COMPOSE_DIR}" && docker compose restart "${COMPOSE_SERVICE_OPENCLAW}") 2>&1 | \
                while read -r line; do log "INFO" "restart: ${line}"; done
            "${SELF_DIR}/notify.sh" warn "Container Restarted" \
                "Failure #${failures}. Tier 2: container restart."
            sleep 20
            ;;
        3)
            log "INFO" "Tier 3: Full service recreate (down + up)"
            (cd "${COMPOSE_DIR}" && docker compose down && docker compose up -d) 2>&1 | \
                while read -r line; do log "INFO" "recreate: ${line}"; done
            "${SELF_DIR}/notify.sh" error "Full Service Recreate" \
                "Failure #${failures}. Tier 3: compose down/up."
            sleep 30
            ;;
        4)
            local cooldown_until=$(( $(date +%s) + COOLDOWN_SECONDS ))
            write_counter "cooldown_until" "${cooldown_until}"
            log "ERROR" "Tier 4: Entering cooldown until $(date -d "@${cooldown_until}" '+%Y-%m-%d %H:%M:%S')"
            "${SELF_DIR}/notify.sh" error "Recovery Exhausted" \
                "All recovery tiers failed after ${failures} attempts. Cooldown ${COOLDOWN_SECONDS}s. Manual intervention required."
            ;;
    esac
}

recover_openvoiceui() {
    local failures="$1"

    if (( failures >= TIER3_THRESHOLD )); then
        log "INFO" "OpenVoiceUI: Full recreate"
        (cd "${COMPOSE_DIR}" && docker compose down && docker compose up -d) 2>&1 | \
            while read -r line; do log "INFO" "recreate: ${line}"; done
        "${SELF_DIR}/notify.sh" error "OpenVoiceUI Full Recreate" \
            "Failure #${failures}. Full compose recreate."
        sleep 30
    elif (( failures >= TIER2_THRESHOLD )); then
        log "INFO" "OpenVoiceUI: Restarting container"
        (cd "${COMPOSE_DIR}" && docker compose restart "${COMPOSE_SERVICE_OPENVOICEUI}") 2>&1 | \
            while read -r line; do log "INFO" "restart: ${line}"; done
        "${SELF_DIR}/notify.sh" warn "OpenVoiceUI Restarted" \
            "Failure #${failures}. Container restart."
        sleep 15
    elif (( failures >= TIER1_THRESHOLD )); then
        log "INFO" "OpenVoiceUI: Health check failed, monitoring"
        "${SELF_DIR}/notify.sh" warn "OpenVoiceUI Health Failed" \
            "Failure #${failures}. Monitoring."
    fi
}

# =============================================================================
# Daily backup trigger
# =============================================================================
maybe_run_backup() {
    if [[ "${BACKUP_ENABLED}" != "true" ]]; then
        return 0
    fi

    local today
    today=$(date '+%Y-%m-%d')
    local last_backup_date
    last_backup_date=$(read_counter "last_backup_date")
    local current_hour
    current_hour=$(date '+%-H')

    if [[ "${last_backup_date}" != "${today}" ]] && (( current_hour == BACKUP_HOUR )); then
        log "INFO" "Triggering daily workspace backup"
        if "${SELF_DIR}/workspace-backup.sh" >> "${LOG_DIR}/workspace-backup.log" 2>&1; then
            write_counter "last_backup_date" "${today}"
            log "INFO" "Daily workspace backup completed"
        else
            log "ERROR" "Daily workspace backup failed"
            "${SELF_DIR}/notify.sh" error "Backup Failed" \
                "Daily workspace snapshot failed. Check logs."
        fi
    fi
}

# =============================================================================
# Signal handling
# =============================================================================
shutdown_requested=false

handle_signal() {
    log "INFO" "Shutdown signal received, exiting gracefully"
    shutdown_requested=true
}

trap handle_signal SIGTERM SIGINT SIGHUP

# =============================================================================
# --status mode
# =============================================================================
if [[ "${1:-}" == "--status" ]]; then
    echo "=== keep-alive-system status ==="
    echo ""
    if [[ -f "${PID_FILE}" ]]; then
        stored_pid=$(cat "${PID_FILE}")
        if kill -0 "${stored_pid}" 2>/dev/null; then
            echo "Monitor: RUNNING (pid ${stored_pid})"
        else
            echo "Monitor: STALE PID (${stored_pid} not running)"
        fi
    else
        echo "Monitor: NOT RUNNING"
    fi
    echo ""
    echo "OpenClaw failures:    $(read_counter openclaw_failures)"
    echo "OpenVoiceUI failures: $(read_counter openvoiceui_failures)"
    cooldown_val=$(read_counter cooldown_until)
    if (( cooldown_val > $(date +%s) )); then
        echo "Cooldown: ACTIVE until $(date -d "@${cooldown_val}" '+%Y-%m-%d %H:%M:%S')"
    else
        echo "Cooldown: inactive"
    fi
    echo "Last backup: $(read_counter last_backup_date)"
    echo ""
    echo "--- Container status ---"
    echo "OpenClaw:    $(check_container_health "${OPENCLAW_CONTAINER}" 2>/dev/null || true)"
    echo "OpenVoiceUI: $(check_container_health "${OPENVOICEUI_CONTAINER}" 2>/dev/null || true)"
    exit 0
fi

# =============================================================================
# PID management
# =============================================================================
if [[ -f "${PID_FILE}" ]]; then
    existing_pid=$(cat "${PID_FILE}")
    if kill -0 "${existing_pid}" 2>/dev/null; then
        echo "keep-alive-system is already running (pid ${existing_pid})"
        exit 1
    fi
fi
echo $$ > "${PID_FILE}"

cleanup() {
    rm -f "${PID_FILE}"
    log "INFO" "Monitor stopped (pid $$)"
}
trap cleanup EXIT

# =============================================================================
# Main loop
# =============================================================================
log "INFO" "keep-alive-system started (pid $$, interval ${CHECK_INTERVAL}s)"
"${SELF_DIR}/notify.sh" info "Monitor Started" "keep-alive-system is now monitoring services."

run_once="${1:-}"

while true; do
    rotate_log

    # --- Cooldown check ---
    cooldown_until=$(read_counter "cooldown_until")
    now=$(date +%s)
    if (( cooldown_until > now )); then
        remaining=$(( cooldown_until - now ))
        log "INFO" "In cooldown, ${remaining}s remaining. Skipping checks."
        if [[ "${run_once}" == "--once" ]]; then exit 0; fi
        sleep "${CHECK_INTERVAL}"
        continue
    fi

    # =========================================================================
    # OpenClaw health check
    # =========================================================================
    openclaw_ok=true

    docker_health=$(check_container_health "${OPENCLAW_CONTAINER}" 2>/dev/null || true)
    if [[ "${docker_health}" != "healthy" ]]; then
        log "WARN" "OpenClaw Docker health: ${docker_health}"
        openclaw_ok=false
    fi

    if [[ "${openclaw_ok}" == "true" ]]; then
        deep_result=$(check_openclaw_deep 2>/dev/null || true)
        if [[ "${deep_result}" != "ok" ]]; then
            log "WARN" "OpenClaw deep health: ${deep_result}"
            openclaw_ok=false
        fi
    fi

    if [[ "${openclaw_ok}" == "true" ]]; then
        prev_failures=$(read_counter "openclaw_failures")
        if (( prev_failures > 0 )); then
            log "INFO" "OpenClaw recovered after ${prev_failures} failure(s)"
            "${SELF_DIR}/notify.sh" recovery "OpenClaw Recovered" \
                "Service healthy after ${prev_failures} consecutive failure(s)."
        fi
        reset_counter "openclaw_failures"
    else
        failures=$(increment_counter "openclaw_failures")
        log "WARN" "OpenClaw consecutive failures: ${failures}"
        recover_openclaw "${failures}"
    fi

    # =========================================================================
    # OpenVoiceUI health check
    # =========================================================================
    voiceui_ok=true

    docker_health=$(check_container_health "${OPENVOICEUI_CONTAINER}" 2>/dev/null || true)
    if [[ "${docker_health}" != "healthy" ]]; then
        log "WARN" "OpenVoiceUI Docker health: ${docker_health}"
        voiceui_ok=false
    fi

    if [[ "${voiceui_ok}" == "true" ]]; then
        http_result=$(check_openvoiceui_http 2>/dev/null || true)
        if [[ "${http_result}" != "ok" ]]; then
            log "WARN" "OpenVoiceUI HTTP health: ${http_result}"
            voiceui_ok=false
        fi
    fi

    if [[ "${voiceui_ok}" == "true" ]]; then
        prev_failures=$(read_counter "openvoiceui_failures")
        if (( prev_failures > 0 )); then
            log "INFO" "OpenVoiceUI recovered after ${prev_failures} failure(s)"
            "${SELF_DIR}/notify.sh" recovery "OpenVoiceUI Recovered" \
                "Service healthy after ${prev_failures} consecutive failure(s)."
        fi
        reset_counter "openvoiceui_failures"
    else
        failures=$(increment_counter "openvoiceui_failures")
        log "WARN" "OpenVoiceUI consecutive failures: ${failures}"
        recover_openvoiceui "${failures}"
    fi

    # =========================================================================
    # Daily backup trigger
    # =========================================================================
    maybe_run_backup

    # =========================================================================
    # Exit or sleep
    # =========================================================================
    if [[ "${run_once}" == "--once" ]]; then
        log "INFO" "Single check completed, exiting"
        exit 0
    fi

    sleep "${CHECK_INTERVAL}" &
    wait $! || true

    if [[ "${shutdown_requested}" == "true" ]]; then
        break
    fi
done
