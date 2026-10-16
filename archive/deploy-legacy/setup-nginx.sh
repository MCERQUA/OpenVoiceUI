#!/bin/bash
# =============================================================================
# OpenVoiceUI — quick nginx config for a single domain
#
# For a full setup (SSL, systemd service, www dirs) use deploy/setup-sudo.sh instead.
# This script is for manual nginx-only configuration.
#
# Edit DOMAIN, PORT, and EMAIL before running.
# Run as: sudo bash setup-nginx.sh
# =============================================================================

DOMAIN="your-domain.com"        # ← EDIT: your actual domain
PORT=5001                        # ← match PORT in your .env (default: 5001)
EMAIL="your-email@example.com"  # ← EDIT: for Let's Encrypt notifications

# Guard: refuse to run with placeholder values
if [ "$DOMAIN" = "your-domain.com" ] || [ "$EMAIL" = "your-email@example.com" ]; then
    echo "ERROR: Edit DOMAIN and EMAIL at the top of this script before running."
    exit 1
fi

cat << NGINX | sudo tee /etc/nginx/sites-available/${DOMAIN}
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

    client_max_body_size 100M;
}
NGINX

sudo ln -sf /etc/nginx/sites-available/${DOMAIN} /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m ${EMAIL}

echo ""
echo "Done. OpenVoiceUI accessible at https://${DOMAIN}"
