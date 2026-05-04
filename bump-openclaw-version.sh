#!/bin/bash
# bump-openclaw-version.sh — Update the pinned openclaw version in all installer paths.
#
# Usage: bash bump-openclaw-version.sh 2026.3.31
#
# Updates all three install paths atomically so they never drift:
#   1. docker-compose.yml        (Pinokio / Docker Compose default)
#   2. deploy/openclaw/Dockerfile (Docker native build default)
#   3. setup-sudo.sh              (native Linux install)

set -e

NEW_VERSION="$1"

if [ -z "$NEW_VERSION" ]; then
  echo "Usage: bash bump-openclaw-version.sh <version>"
  echo "Example: bash bump-openclaw-version.sh 2026.3.31"
  exit 1
fi

# Validate format: YYYY.M.D or YYYY.M.DD
if ! echo "$NEW_VERSION" | grep -qE '^[0-9]{4}\.[0-9]+\.[0-9]+$'; then
  echo "Error: version must be in YYYY.M.D format (e.g. 2026.3.24)"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Show current versions before changing
echo "Updating openclaw version to $NEW_VERSION ..."
echo ""

# 1. docker-compose.yml
OLD=$(grep "OPENCLAW_VERSION:-" docker-compose.yml | grep -o '[0-9]\{4\}\.[0-9]*\.[0-9]*' | head -1)
sed -i "s/OPENCLAW_VERSION:-[0-9]*\.[0-9]*\.[0-9]*/OPENCLAW_VERSION:-${NEW_VERSION}/" docker-compose.yml
echo "  docker-compose.yml:          $OLD -> $NEW_VERSION"

# 2. deploy/openclaw/Dockerfile
OLD=$(grep "OPENCLAW_VERSION=" deploy/openclaw/Dockerfile | grep -o '[0-9]\{4\}\.[0-9]*\.[0-9]*' | head -1)
sed -i "s/OPENCLAW_VERSION=[0-9]*\.[0-9]*\.[0-9]*/OPENCLAW_VERSION=${NEW_VERSION}/" deploy/openclaw/Dockerfile
echo "  deploy/openclaw/Dockerfile:  $OLD -> $NEW_VERSION"

# 3. setup-sudo.sh
OLD=$(grep "OPENCLAW_TESTED_VERSION=" setup-sudo.sh | grep -o '[0-9]\{4\}\.[0-9]*\.[0-9]*' | head -1)
sed -i "s/OPENCLAW_TESTED_VERSION=\"[0-9]*\.[0-9]*\.[0-9]*\"/OPENCLAW_TESTED_VERSION=\"${NEW_VERSION}\"/" setup-sudo.sh
echo "  setup-sudo.sh:               $OLD -> $NEW_VERSION"

# 4. services/gateways/compat.py — Python protocol-compat layer holds its own pin
# that consumers (server.py, gateway connector) read on every startup. Was missed
# by this script for months and drifted to 2026.3.13.
OLD=$(grep 'OPENCLAW_TESTED_VERSION = ' services/gateways/compat.py | grep -o '[0-9]\{4\}\.[0-9]*\.[0-9]*' | head -1)
sed -i "s/OPENCLAW_TESTED_VERSION = \"[0-9]*\.[0-9]*\.[0-9]*\"/OPENCLAW_TESTED_VERSION = \"${NEW_VERSION}\"/" services/gateways/compat.py
echo "  services/gateways/compat.py: $OLD -> $NEW_VERSION"

echo ""
echo "All 4 installer paths updated. Verify with:"
echo "  grep -r '$NEW_VERSION' docker-compose.yml deploy/openclaw/Dockerfile setup-sudo.sh services/gateways/compat.py"
echo ""
echo "Then commit:"
echo "  git add docker-compose.yml deploy/openclaw/Dockerfile setup-sudo.sh services/gateways/compat.py"
echo "  git commit -m \"chore: bump openclaw to $NEW_VERSION\""
