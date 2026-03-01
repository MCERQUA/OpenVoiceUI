#!/bin/bash
set -e

# =========================================
# Client Dashboard Canvas Plugin Uninstaller
# =========================================

USER=${1:-}
VOLUME_ID=${2:-}

if [ -z "$USER" ] || [ -z "$VOLUME_ID" ]; then
  echo "Usage: $0 <user> <volume_id>"
  echo "Example: $0 foamology HC_Volume_104807901"
  exit 1
fi

VOLUME="/mnt/$VOLUME_ID"
CANVAS_DIR="${VOLUME}/canvas-pages"
OV_DIR="${VOLUME}/${USER}/ai/OpenVoiceUI-public"
API_DIR="${OV_DIR}/canvas-plugins/client-dashboard"

echo "=== Uninstalling Client Dashboard Plugin ==="
echo "User: $USER"
echo "Volume: $VOLUME"

# 1. Stop and disable service
echo "[1/4] Stopping service..."
sudo systemctl stop dashboard-api-${USER} 2>/dev/null || true
sudo systemctl disable dashboard-api-${USER} 2>/dev/null || true
sudo rm -f /etc/systemd/system/dashboard-api-${USER}.service
sudo systemctl daemon-reload

# 2. Remove canvas pages
echo "[2/4] Removing canvas pages..."
rm -f "$CANVAS_DIR"/dashboard*.html
rm -f "$CANVAS_DIR"/dashboard.css
rm -f "$CANVAS_DIR"/dashboard.js

# 3. Remove API files
echo "[3/4] Removing API server..."
rm -rf "$API_DIR"

# 4. Remove env var from OpenVoiceUI
echo "[4/4] Cleaning up configuration..."
OV_ENV="${OV_DIR}/.env"
sed -i "/DASHBOARD_API_PORT/d" "$OV_ENV" 2>/dev/null || true

echo ""
echo "=== Uninstall Complete ==="
