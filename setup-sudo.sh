#!/bin/bash
# =============================================================================
# OpenVoiceUI — generic sudo setup script
# Creates: nginx config, Let's Encrypt SSL, systemd service, prestart script
# Run as: sudo bash setup-sudo.sh
# =============================================================================
set -e

# ── Configure these before running ──────────────────────────────────────────
INSTALL_DIR="$(cd "$(dirname "$0")" && pwd)"
DOMAIN="your-domain.com"        # ← EDIT: your actual domain
PORT=5001                        # ← match PORT in your .env (default: 5001)
EMAIL="your@email.com"           # ← EDIT: for Let's Encrypt notifications
SERVICE_NAME="openvoiceui"
RUN_USER="${SUDO_USER:-$(whoami)}"
WWW_DIR="/var/www/${SERVICE_NAME}"          # canvas pages + any web assets
OPENCLAW_TESTED_VERSION="2026.5.2"        # pinned: the openclaw version tested with this release
# ────────────────────────────────────────────────────────────────────────────

# Guard: refuse to run with placeholder values
if [ "$DOMAIN" = "your-domain.com" ] || [ "$EMAIL" = "your@email.com" ]; then
    echo "ERROR: Edit DOMAIN and EMAIL at the top of this script before running."
    echo "       Open setup-sudo.sh in a text editor and set your real domain and email."
    exit 1
fi

# Check .env exists
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    echo "ERROR: No .env file found at ${INSTALL_DIR}/.env"
    echo "       Run: cp ${INSTALL_DIR}/.env.example ${INSTALL_DIR}/.env"
    echo "       Then edit .env and set your API keys before running this script."
    exit 1
fi

# ── OpenClaw gateway setup ────────────────────────────────────────────────────
# Helper: is OpenClaw currently listening on its default port?
_openclaw_running() {
    ss -tlnp 2>/dev/null | grep -q ':18791'
}

# Helper: is the openclaw binary installed anywhere?
_openclaw_installed() {
    command -v openclaw >/dev/null 2>&1 \
        || [ -f "/usr/local/bin/openclaw" ] \
        || [ -f "${HOME}/.local/bin/openclaw" ]
}

# Helper: get installed openclaw version string (e.g. "2026.3.2")
_openclaw_version() {
    local ver=""
    if command -v openclaw >/dev/null 2>&1; then
        ver=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    elif [ -f "/usr/local/bin/openclaw" ]; then
        ver=$(/usr/local/bin/openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    elif [ -f "${HOME}/.local/bin/openclaw" ]; then
        ver=$(${HOME}/.local/bin/openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    fi
    echo "$ver"
}

# Helper: compare two version strings (format: YYYY.M.D)
# Returns: 0 = match, 1 = first is newer, 2 = first is older
_version_compare() {
    local a="$1" b="$2"
    if [ "$a" = "$b" ]; then return 0; fi
    local older
    older=$(printf '%s\n%s' "$a" "$b" | sort -V | head -n1)
    if [ "$older" = "$a" ]; then return 2; else return 1; fi
}

# Helper: display the requirements checklist for existing openclaw installs
_show_requirements() {
    echo ""
    echo "  ┌────────────────────────────────────────────────────────────────────┐"
    echo "  │  OpenVoiceUI Gateway Requirements                                  │"
    echo "  │                                                                    │"
    echo "  │  Your openclaw.json must include these settings for voice to work. │"
    echo "  │  OpenVoiceUI will NOT modify your config — these are for you to    │"
    echo "  │  review and apply manually.                                        │"
    echo "  │                                                                    │"
    echo "  │  VERSION                                                           │"
    echo "  │    Tested: openclaw@${OPENCLAW_TESTED_VERSION}                               │"
    echo "  │    Other versions may work but are not guaranteed.                  │"
    echo "  │                                                                    │"
    echo "  │  GATEWAY (global — affects all agents on this instance)            │"
    echo "  │    gateway.auth.mode: \"token\"                                      │"
    echo "  │    gateway.controlUi.dangerouslyDisableDeviceAuth: true            │"
    echo "  │                                                                    │"
    echo "  │  AGENT (can be scoped to the openvoiceui agent only)              │"
    echo "  │    agents.defaults.thinkingDefault: \"off\"                          │"
    echo "  │    agents.defaults.blockStreamingDefault: \"on\"                     │"
    echo "  │    agents.defaults.timeoutSeconds: 300                             │"
    echo "  │    agents.defaults.compaction.reserveTokensFloor: 120000           │"
    echo "  │                                                                    │"
    echo "  │  Full details: docs/openclaw-requirements.md                       │"
    echo "  └────────────────────────────────────────────────────────────────────┘"
    echo ""
}

echo ""
echo "── OpenClaw Gateway ──────────────────────────────────────────────────────"
echo ""
echo "  OpenVoiceUI uses OpenClaw as its AI gateway for voice conversations."
echo "  Tested version: openclaw@${OPENCLAW_TESTED_VERSION}"
echo ""

OPENCLAW_GATEWAY_URL=$(grep -E "^CLAWDBOT_GATEWAY_URL=" "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18791}"
OPENCLAW_TOKEN=$(grep -E "^CLAWDBOT_AUTH_TOKEN=" "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
OPENCLAW_CONFIGURED=false

# Detect current state
HAS_RUNNING=false
HAS_INSTALLED=false
INSTALLED_VERSION=""
if _openclaw_running; then HAS_RUNNING=true; HAS_INSTALLED=true; fi
if _openclaw_installed; then HAS_INSTALLED=true; fi
if [ "$HAS_INSTALLED" = "true" ]; then
    INSTALLED_VERSION=$(_openclaw_version)
fi

# Show options based on current state
if [ "$HAS_RUNNING" = "true" ] || [ "$HAS_INSTALLED" = "true" ]; then
    echo "  Detected: OpenClaw is installed on this system."
    [ -n "$INSTALLED_VERSION" ] && echo "  Installed version: ${INSTALLED_VERSION}"
    [ "$HAS_RUNNING" = "true" ] && echo "  Status: running (port 18791 active)"
    echo ""
    echo "  How would you like to set up the gateway?"
    echo ""
    echo "    1) Install a fresh OpenClaw for OpenVoiceUI (recommended)"
    echo "       Installs openclaw@${OPENCLAW_TESTED_VERSION} alongside your existing install."
    echo "       This will replace the globally installed version."
    echo ""
    echo "    2) Use my existing OpenClaw"
    echo "       You must configure it yourself to meet OpenVoiceUI's requirements."
    echo "       Voice features may not work if settings don't match."
    echo ""
    echo "    3) Skip — I'll set up the gateway later"
    echo ""
    printf "  Choice [1]: "
    read -r CHOICE
    CHOICE="${CHOICE:-1}"
else
    echo "  OpenClaw is not installed on this system."
    echo ""
    echo "  How would you like to set up the gateway?"
    echo ""
    echo "    1) Install OpenClaw now (recommended)"
    echo "       Installs openclaw@${OPENCLAW_TESTED_VERSION} configured for voice conversations."
    echo ""
    echo "    2) Skip — I'll install and configure OpenClaw later"
    echo ""
    printf "  Choice [1]: "
    read -r CHOICE
    CHOICE="${CHOICE:-1}"
    # Map choice 2 to "skip" (choice 3 in the full menu)
    if [ "$CHOICE" = "2" ]; then CHOICE="3"; fi
fi

case "$CHOICE" in
    1)
        # ── Fresh install ──────────────────────────────────────────────────
        echo ""
        echo "  Installing openclaw@${OPENCLAW_TESTED_VERSION}..."

        # Check for npm/pnpm
        if command -v pnpm >/dev/null 2>&1; then
            PKG_MGR="pnpm"
            INSTALL_CMD="pnpm add -g openclaw@${OPENCLAW_TESTED_VERSION}"
        elif command -v npm >/dev/null 2>&1; then
            PKG_MGR="npm"
            INSTALL_CMD="npm i -g openclaw@${OPENCLAW_TESTED_VERSION}"
        else
            echo "  ⚠  Neither npm nor pnpm found. Install Node.js 22+ first:"
            echo "     curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -"
            echo "     sudo apt install -y nodejs"
            echo "  Then re-run this script."
            exit 1
        fi

        echo "  Using ${PKG_MGR}..."
        if $INSTALL_CMD; then
            echo "  ✓ openclaw@${OPENCLAW_TESTED_VERSION} installed."
        else
            echo "  ⚠  Installation failed. Check the output above for errors."
            echo "     You can install manually: ${INSTALL_CMD}"
            printf "  Continue setup without the gateway? [y/N] "
            read -r REPLY
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
        fi

        if _openclaw_installed; then
            # Run onboard wizard if no config exists yet
            OPENCLAW_HOME="$(eval echo "~${RUN_USER}")/.openclaw"
            if [ ! -f "${OPENCLAW_HOME}/openclaw.json" ]; then
                echo ""
                echo "  ── OpenClaw First-Time Setup ───────────────────────────────────"
                echo "  OpenClaw needs to know which AI model to use."
                echo "  The setup wizard will ask for your LLM provider and API key."
                echo "  ────────────────────────────────────────────────────────────────"
                echo ""
                # Run onboard as the service user, not root
                sudo -u "${RUN_USER}" openclaw onboard 2>&1 || true
            fi

            # Start the gateway
            echo ""
            echo "  Starting OpenClaw gateway..."
            if ! _openclaw_running; then
                sudo -u "${RUN_USER}" openclaw gateway &
                WAIT_I=0
                while [ $WAIT_I -lt 10 ]; do
                    sleep 1
                    _openclaw_running && break
                    WAIT_I=$((WAIT_I+1))
                done
            fi

            if _openclaw_running; then
                echo "  ✓ OpenClaw gateway is running on port 18791."
                OPENCLAW_CONFIGURED=true
            else
                echo "  ⚠  Gateway did not start. You can start it manually later:"
                echo "     openclaw gateway"
            fi
        fi
        ;;

    2)
        # ── Use existing install ───────────────────────────────────────────
        echo ""
        echo "  Using your existing OpenClaw installation."

        # Version check
        if [ -n "$INSTALLED_VERSION" ]; then
            _version_compare "$INSTALLED_VERSION" "$OPENCLAW_TESTED_VERSION"
            VERSION_CMP=$?
            if [ $VERSION_CMP -ne 0 ]; then
                echo ""
                echo "  ┌────────────────────────────────────────────────────────────────┐"
                echo "  │  ⚠  VERSION MISMATCH                                          │"
                echo "  │                                                                │"
                printf "  │  %-62s │\n" "Your version:   openclaw@${INSTALLED_VERSION}"
                printf "  │  %-62s │\n" "Tested version: openclaw@${OPENCLAW_TESTED_VERSION}"
                echo "  │                                                                │"
                if [ $VERSION_CMP -eq 1 ]; then
                    echo "  │  Your version is NEWER and may include breaking changes       │"
                    echo "  │  (especially around auth and config validation).              │"
                else
                    echo "  │  Your version is OLDER and may be missing required features.  │"
                fi
                echo "  │  Voice conversations may not work correctly.                  │"
                echo "  │                                                                │"
                printf "  │  %-62s │\n" "To install the tested version:"
                printf "  │  %-62s │\n" "  npm i -g openclaw@${OPENCLAW_TESTED_VERSION}"
                echo "  └────────────────────────────────────────────────────────────────┘"
                echo ""
                printf "  Continue with openclaw@${INSTALLED_VERSION} anyway? [y/N] "
                read -r REPLY
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                    echo "  Stopping. Install the tested version and re-run this script."
                    exit 1
                fi
            else
                echo "  ✓ Version matches (${INSTALLED_VERSION})."
            fi
        fi

        # Show requirements checklist
        _show_requirements

        echo "  Please review the requirements above and confirm your OpenClaw"
        echo "  is configured to meet them. OpenVoiceUI will NOT modify your"
        echo "  OpenClaw config."
        echo ""
        printf "  I've reviewed the requirements and my OpenClaw is configured [y/N]: "
        read -r REPLY
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo ""
            echo "  No problem. Configure your OpenClaw to meet the requirements,"
            echo "  then re-run this script. Or choose option 1 for a fresh install."
            exit 1
        fi

        # Try to use it
        if [ "$HAS_RUNNING" = "true" ]; then
            OPENCLAW_CONFIGURED=true
        elif _openclaw_installed; then
            echo "  OpenClaw is not currently running."
            printf "  Start it now? [Y/n] "
            read -r REPLY
            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                echo "  Starting..."
                sudo -u "${RUN_USER}" openclaw gateway &
                sleep 5
                if _openclaw_running; then
                    echo "  ✓ OpenClaw started."
                    OPENCLAW_CONFIGURED=true
                else
                    echo "  ⚠  Could not start. Start it manually, then restart OpenVoiceUI."
                fi
            fi
        fi

        # Custom gateway URL
        if [ "$OPENCLAW_CONFIGURED" = "true" ]; then
            printf "  Gateway URL [${OPENCLAW_GATEWAY_URL}]: "
            read -r CUSTOM_URL
            if [ -n "$CUSTOM_URL" ]; then
                sed -i "s|^CLAWDBOT_GATEWAY_URL=.*|CLAWDBOT_GATEWAY_URL=${CUSTOM_URL}|" "${INSTALL_DIR}/.env"
                OPENCLAW_GATEWAY_URL="$CUSTOM_URL"
                echo "  Updated CLAWDBOT_GATEWAY_URL in .env"
            fi
        fi
        ;;

    3|*)
        # ── Skip ───────────────────────────────────────────────────────────
        echo ""
        echo "  ┌────────────────────────────────────────────────────────────────┐"
        echo "  │  OpenVoiceUI needs an AI gateway for voice conversations.      │"
        echo "  │  Without one the server will start but the voice agent will    │"
        echo "  │  not respond to any input.                                     │"
        echo "  │                                                                │"
        echo "  │  To set up later:                                              │"
        echo "  │    1. Install: npm i -g openclaw@${OPENCLAW_TESTED_VERSION}              │"
        echo "  │    2. Run: openclaw onboard                                    │"
        echo "  │    3. Set CLAWDBOT_AUTH_TOKEN in .env                          │"
        echo "  │    4. Restart: sudo systemctl restart openvoiceui              │"
        echo "  │                                                                │"
        echo "  │  Or use Docker instead (includes everything):                  │"
        echo "  │    docker compose up --build                                   │"
        echo "  └────────────────────────────────────────────────────────────────┘"
        echo ""
        printf "  Continue setup without a gateway? [y/N] "
        read -r REPLY
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Stopping."
            exit 1
        fi
        echo "  Continuing — voice conversations will not work until a gateway is configured."
        ;;
esac

# Check auth token is set and not placeholder
if [ "$OPENCLAW_CONFIGURED" = "true" ]; then
    if [ -z "$OPENCLAW_TOKEN" ] || [ "$OPENCLAW_TOKEN" = "your-openclaw-gateway-token" ]; then
        echo ""
        echo "  ⚠  CLAWDBOT_AUTH_TOKEN is not set in .env."
        echo "     OpenClaw is running but OpenVoiceUI cannot authenticate to it."
        echo "     Copy your agent auth token from your OpenClaw workspace and set:"
        echo "       CLAWDBOT_AUTH_TOKEN=your-token-here"
        echo "     in ${INSTALL_DIR}/.env, then restart the service."
    else
        echo "  ✓ Auth token configured. Gateway: ${OPENCLAW_GATEWAY_URL}"
    fi
    echo ""
    echo "  Note: The AI model (LLM) is configured inside your OpenClaw agent"
    echo "  workspace — OpenVoiceUI works with any model OpenClaw is connected to."
    echo "  Voice settings (TTS, profile, wake words) are in the admin dashboard:"
    echo "  https://${DOMAIN}/src/admin.html"
fi
echo ""

echo "=== OpenVoiceUI setup: ${DOMAIN} on port ${PORT} ==="
echo "    Install dir : ${INSTALL_DIR}"
echo "    Service user: ${RUN_USER}"
echo "    WWW dir     : ${WWW_DIR}"
echo ""

# 0. Per-instance www directory (canvas pages, isolated from other users)
echo "[0/6] Creating www directory for ${RUN_USER}..."
mkdir -p "${WWW_DIR}/canvas-pages"
chown -R "${RUN_USER}:${RUN_USER}" "${WWW_DIR}"
chmod -R 755 "${WWW_DIR}"

# 0b. OpenVoiceUI agent workspace (dedicated agent — does NOT touch user's main agent)
OPENCLAW_DIR="$(eval echo "~${RUN_USER}")/.openclaw"
AGENT_TEMPLATE_DIR="${INSTALL_DIR}/setup/openvoiceui-agent"
AGENT_DEST_DIR="${OPENCLAW_DIR}/agents/openvoiceui"

if [ -d "${AGENT_TEMPLATE_DIR}" ] && [ -d "${OPENCLAW_DIR}" ]; then
    if [ -d "${AGENT_DEST_DIR}" ]; then
        echo "  OpenVoiceUI agent already exists at ${AGENT_DEST_DIR} — skipping (not overwriting)"
    else
        echo "  Creating OpenVoiceUI agent workspace at ${AGENT_DEST_DIR}..."
        mkdir -p "${AGENT_DEST_DIR}/memory"
        for f in SOUL.md TOOLS.md AGENTS.md IDENTITY.md MEMORY.md USER.md; do
            if [ -f "${AGENT_TEMPLATE_DIR}/${f}" ]; then
                # Fill in instance-specific placeholder values
                sed "s|{{CANVAS_PAGES_DIR}}|${WWW_DIR}/canvas-pages|g" \
                    "${AGENT_TEMPLATE_DIR}/${f}" > "${AGENT_DEST_DIR}/${f}"
            fi
        done
        chown -R "${RUN_USER}:${RUN_USER}" "${AGENT_DEST_DIR}"
        echo "  Agent workspace created. Register it in ~/.openclaw/openclaw.json:"
        echo "    { id: \"openvoiceui\", workspace: \"${AGENT_DEST_DIR}\", model: { primary: \"your-model\" } }"
        echo "  Then set VOICE_SESSION_PREFIX=voice-openvoiceui in your .env"
    fi
elif [ ! -d "${OPENCLAW_DIR}" ]; then
    echo "  OpenClaw not configured yet (~/.openclaw missing) — skipping agent setup"
    echo "  Run setup again after configuring OpenClaw, or manually copy setup/openvoiceui-agent/"
fi

# 1. Prestart script (kills stale process on port before service starts)
echo "[1/6] Creating prestart script..."
cat > /usr/local/bin/prestart-${SERVICE_NAME}.sh << PRESTART
#!/bin/bash
PORT=${PORT}
LOG=/var/log/${SERVICE_NAME}.log
PID=\$(fuser \${PORT}/tcp 2>/dev/null)
if [ -n "\$PID" ]; then
    echo "\$(date): Found stale process \$PID on port \$PORT, killing..." | tee -a \$LOG
    kill \$PID 2>/dev/null
    sleep 2
    if kill -0 \$PID 2>/dev/null; then
        kill -9 \$PID 2>/dev/null
        sleep 1
    fi
fi
exit 0
PRESTART
chmod +x /usr/local/bin/prestart-${SERVICE_NAME}.sh

# 2. Systemd service
echo "[2/6] Creating systemd service..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << SERVICE
[Unit]
Description=OpenVoiceUI Voice Agent (${DOMAIN})
After=network.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStartPre=/usr/local/bin/prestart-${SERVICE_NAME}.sh
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/server.py
Restart=on-failure
RestartSec=10
Environment=PATH=/usr/bin:/usr/local/bin
EnvironmentFile=${INSTALL_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICE

# 3. Nginx config
# 3a. Write HTTP-only nginx config so certbot can serve ACME challenges
echo "[3/6] Creating nginx config..."
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    echo "  No SSL cert yet -- writing HTTP-only config for certbot..."
    cat > /etc/nginx/sites-available/${DOMAIN} << NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 86400;
    }

    client_max_body_size 25M;
}
NGINX
    ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/${DOMAIN}
    nginx -t
    systemctl reload nginx

    # 4. Obtain SSL cert (nginx can now serve ACME challenge on port 80)
    echo "[4/6] Obtaining SSL certificate..."
    certbot certonly --nginx -d ${DOMAIN} --non-interactive --agree-tos -m ${EMAIL}
else
    echo "  SSL cert already exists, skipping certbot."
fi

# 3b. Write full HTTPS config (cert now exists)
echo "  Writing HTTPS nginx config..."
cat > /etc/nginx/sites-available/${DOMAIN} << NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
        proxy_read_timeout 86400;
    }

    client_max_body_size 25M;
}
NGINX

ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/${DOMAIN}

# 5. Enable and start service
echo "[5/6] Enabling and starting service..."
nginx -t
systemctl reload nginx
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service
systemctl restart ${SERVICE_NAME}.service

sleep 3
systemctl status ${SERVICE_NAME}.service --no-pager

echo "[6/6] Setup complete."
echo ""
echo "=== Done! OpenVoiceUI running at https://${DOMAIN} ==="
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
