#!/usr/bin/env bash
# =============================================================================
# keep-alive-system :: uninstall.sh
# Remove the systemd service and clean up state
# Usage: sudo ./uninstall.sh [--purge]
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SELF_DIR}/keep-alive.conf"

SERVICE_NAME="keep-alive-system"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PURGE="${1:-}"

echo "=== keep-alive-system uninstaller ==="
echo ""

# --- Stop and disable service ---
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "Stopping service..."
    systemctl stop "${SERVICE_NAME}"
fi

if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    echo "Disabling service..."
    systemctl disable "${SERVICE_NAME}"
fi

# --- Remove service file ---
if [[ -f "${SERVICE_FILE}" ]]; then
    echo "Removing systemd unit file..."
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
fi

# --- Clean up state ---
echo "Removing state directory..."
rm -rf "${SCRIPT_DIR}/.state"

# --- Purge mode: remove logs too ---
if [[ "${PURGE}" == "--purge" ]]; then
    echo "Purging logs..."
    rm -rf "${LOG_DIR}"
    echo "NOTE: Workspace git history was NOT removed."
    echo "      To remove it: rm -rf ${WORKSPACE_DIR}/.git ${WORKSPACE_DIR}/.gitignore"
fi

echo ""
echo "=== Uninstall complete ==="
echo ""
echo "  Scripts remain in ${SELF_DIR}"
echo "  To fully remove: rm -rf ${SELF_DIR}"
echo ""
