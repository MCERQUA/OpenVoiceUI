#!/usr/bin/env bash
# =============================================================================
# keep-alive-system :: install.sh
# Install the monitoring system as a systemd service
# Usage: sudo ./install.sh
# =============================================================================
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SELF_DIR}/keep-alive.conf"

SERVICE_NAME="keep-alive-system"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
RUN_USER="${1:-$(logname 2>/dev/null || echo "${SUDO_USER:-$(whoami)}")}"

echo "=== keep-alive-system installer ==="
echo ""

# --- Prerequisite checks ---
echo "Checking prerequisites..."

missing=()
for cmd in docker jq curl git python3; do
    if ! command -v "${cmd}" &>/dev/null; then
        missing+=("${cmd}")
    fi
done

if (( ${#missing[@]} > 0 )); then
    echo "ERROR: Missing required commands: ${missing[*]}"
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "ERROR: Cannot connect to Docker daemon. Is the user in the docker group?"
    exit 1
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "ERROR: Compose file not found at ${COMPOSE_FILE}"
    exit 1
fi

echo "  All prerequisites met."

# --- Create directories (owned by run user, not root) ---
echo "Creating directories..."
mkdir -p "${LOG_DIR}"
mkdir -p "${SCRIPT_DIR}/.state"
chown -R "${RUN_USER}:${RUN_USER}" "${LOG_DIR}"
chown -R "${RUN_USER}:${RUN_USER}" "${SCRIPT_DIR}/.state"

# --- Set permissions ---
echo "Setting script permissions..."
chmod +x "${SELF_DIR}/keep-alive.sh"
chmod +x "${SELF_DIR}/workspace-backup.sh"
chmod +x "${SELF_DIR}/notify.sh"
chmod +x "${SELF_DIR}/install.sh"
chmod +x "${SELF_DIR}/uninstall.sh"

# --- Initialize workspace backup ---
echo "Initializing workspace backup repository..."
sudo -u "${RUN_USER}" "${SELF_DIR}/workspace-backup.sh" --init

# --- Create systemd service ---
echo "Installing systemd service..."

cat > "${SERVICE_FILE}" <<SERVICE_EOF
[Unit]
Description=keep-alive-system: Docker Compose health monitor
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=${RUN_USER}
Group=docker
WorkingDirectory=${SELF_DIR}
ExecStart=${SELF_DIR}/keep-alive.sh
ExecStop=/bin/kill -SIGTERM \$MAINPID
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=${SELF_DIR} ${LOG_DIR} ${WORKSPACE_DIR}
ProtectHome=read-only
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
SERVICE_EOF

# --- Enable and start ---
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"

echo ""
echo "=== Installation complete ==="
echo ""
echo "  Service:      ${SERVICE_NAME}"
echo "  Status:       systemctl status ${SERVICE_NAME}"
echo "  Logs:         journalctl -u ${SERVICE_NAME} -f"
echo "  File log:     ${LOG_DIR}/keep-alive.log"
echo "  Config:       ${SELF_DIR}/keep-alive.conf"
echo ""
echo "  Quick check:  ${SELF_DIR}/keep-alive.sh --status"
echo "  Manual backup: ${SELF_DIR}/workspace-backup.sh"
echo ""
