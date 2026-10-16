#!/bin/bash
# =============================================================================
# OpenVoiceUI — generic sudo setup script
# Creates: nginx config, Let's Encrypt SSL, systemd service, prestart script
# Run as: sudo bash deploy/setup-sudo.sh
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
# ────────────────────────────────────────────────────────────────────────────

# Guard: refuse to run with placeholder values
if [ "$DOMAIN" = "your-domain.com" ] || [ "$EMAIL" = "your@email.com" ]; then
    echo "ERROR: Edit DOMAIN and EMAIL at the top of this script before running."
    echo "       Open deploy/setup-sudo.sh in a text editor and set your real domain and email."
    exit 1
fi

# Check .env exists
if [ ! -f "${INSTALL_DIR}/.env" ]; then
    echo "ERROR: No .env file found at ${INSTALL_DIR}/.env"
    echo "       Run: cp ${INSTALL_DIR}/.env.example ${INSTALL_DIR}/.env"
    echo "       Then edit .env and set your API keys before running this script."
    exit 1
fi

# ── OpenClaw gateway detection ────────────────────────────────────────────────
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

# Helper: try to start openclaw and wait up to 5s for port to open
_openclaw_start() {
    echo "  Attempting to start OpenClaw..."
    if command -v openclaw >/dev/null 2>&1; then
        openclaw start 2>/dev/null &
    elif [ -f "/usr/local/bin/openclaw" ]; then
        /usr/local/bin/openclaw start 2>/dev/null &
    else
        return 1
    fi
    local i=0
    while [ $i -lt 5 ]; do
        sleep 1
        _openclaw_running && return 0
        i=$((i+1))
    done
    return 1
}

echo ""
echo "── OpenClaw Gateway ──────────────────────────────────────────────────────"

OPENCLAW_GATEWAY_URL=$(grep -E "^CLAWDBOT_GATEWAY_URL=" "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-ws://127.0.0.1:18791}"
OPENCLAW_TOKEN=$(grep -E "^CLAWDBOT_AUTH_TOKEN=" "${INSTALL_DIR}/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
OPENCLAW_CONFIGURED=false

if _openclaw_running; then
    echo "  ✓ OpenClaw is running (port 18791 active)"
    printf "    Use this OpenClaw instance for OpenVoiceUI? [Y/n] "
    read -r REPLY
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        printf "    Enter your gateway WebSocket URL [${OPENCLAW_GATEWAY_URL}]: "
        read -r CUSTOM_URL
        if [ -n "$CUSTOM_URL" ]; then
            sed -i "s|^CLAWDBOT_GATEWAY_URL=.*|CLAWDBOT_GATEWAY_URL=${CUSTOM_URL}|" "${INSTALL_DIR}/.env"
            OPENCLAW_GATEWAY_URL="$CUSTOM_URL"
            echo "    Updated CLAWDBOT_GATEWAY_URL in .env"
        fi
    fi
    OPENCLAW_CONFIGURED=true

elif _openclaw_installed; then
    echo "  OpenClaw is installed but not running."
    printf "  Start OpenClaw now? [Y/n] "
    read -r REPLY
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        if _openclaw_start; then
            echo "  ✓ OpenClaw started successfully."
            OPENCLAW_CONFIGURED=true
        else
            echo "  ⚠  Could not start OpenClaw automatically."
            echo "     Start it manually (e.g. 'openclaw start' or restart its service),"
            echo "     then re-run this script — or continue and start it later."
            printf "  Continue anyway? [y/N] "
            read -r REPLY
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "Stopping. Start OpenClaw and re-run this script."
                exit 1
            fi
        fi
    else
        OPENCLAW_CONFIGURED=false
    fi

else
    echo "  OpenClaw is not installed on this system."
    echo "  OpenClaw is the AI gateway that processes all voice conversations."
    echo ""
    printf "  Install OpenClaw now? [Y/n] "
    read -r REPLY
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo ""
        echo "  ── OpenClaw Installation ─────────────────────────────────────"
        echo "  1. Visit https://openclaw.ai and follow the install guide"
        echo "  2. Run the installer for your OS"
        echo "  3. Start OpenClaw (it will listen on ws://127.0.0.1:18791)"
        echo "  4. Create an agent workspace and copy your auth token"
        echo "  ──────────────────────────────────────────────────────────────"
        echo ""
        printf "  Press Enter once OpenClaw is installed and running (Ctrl+C to abort)... "
        read -r
        if _openclaw_running; then
            echo "  ✓ OpenClaw detected — continuing."
            OPENCLAW_CONFIGURED=true
        else
            echo "  ⚠  OpenClaw port 18791 is still not responding."
            printf "  Continue anyway and configure OpenClaw later? [y/N] "
            read -r REPLY
            if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                echo "Stopping. Install and start OpenClaw, then re-run this script."
                exit 1
            fi
        fi
    else
        echo ""
        echo "  ┌────────────────────────────────────────────────────────────────┐"
        echo "  │  OpenVoiceUI requires a gateway framework to process voice     │"
        echo "  │  conversations. Without one the server will start but the      │"
        echo "  │  voice agent will not respond to any input.                    │"
        echo "  │                                                                │"
        echo "  │  You can install a compatible gateway later and point .env at  │"
        echo "  │  it via CLAWDBOT_GATEWAY_URL + CLAWDBOT_AUTH_TOKEN.            │"
        echo "  │  See plugins/README.md for building a custom gateway plugin.   │"
        echo "  └────────────────────────────────────────────────────────────────┘"
        echo ""
        printf "  Continue setup without a gateway? [y/N] "
        read -r REPLY
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Stopping. Visit https://openclaw.ai to get started."
            exit 1
        fi
        echo "  Continuing — the server will be installed but conversations will not work"
        echo "  until a gateway is configured."
    fi
fi

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
echo "[0/5] Creating www directory for ${RUN_USER}..."
mkdir -p "${WWW_DIR}/canvas-pages"
chown -R "${RUN_USER}:${RUN_USER}" "${WWW_DIR}"
chmod -R 755 "${WWW_DIR}"

# 1. Prestart script (kills stale process on port before service starts)
echo "[1/5] Creating prestart script..."
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
echo "[2/5] Creating systemd service..."
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
Restart=always
RestartSec=10
Environment=PATH=/usr/bin:/usr/local/bin
EnvironmentFile=${INSTALL_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICE

# 3. Nginx config
echo "[3/5] Creating nginx config..."
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

    client_max_body_size 100M;
}
NGINX

ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/${DOMAIN}

# 4. SSL cert
echo "[4/5] Obtaining SSL certificate..."
if [ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]; then
    certbot certonly --nginx -d ${DOMAIN} --non-interactive --agree-tos -m ${EMAIL}
else
    echo "  SSL cert already exists, skipping."
fi

# 5. Enable and start service
echo "[5/5] Enabling and starting service..."
nginx -t
systemctl reload nginx
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}.service
systemctl restart ${SERVICE_NAME}.service

sleep 3
systemctl status ${SERVICE_NAME}.service --no-pager

echo ""
echo "=== Done! OpenVoiceUI running at https://${DOMAIN} ==="
echo ""
echo "Useful commands:"
echo "  sudo systemctl status ${SERVICE_NAME}"
echo "  sudo systemctl restart ${SERVICE_NAME}"
echo "  sudo journalctl -u ${SERVICE_NAME} -f"
