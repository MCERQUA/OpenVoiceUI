# Twenty CRM Plugin for OpenVoiceUI

Connects OpenVoiceUI to a [Twenty CRM](https://twenty.com) instance, giving your AI agent full contact, company, deal, note, and task management through the CRM REST API.

## What This Plugin Provides

- **CRM canvas page** — embedded iframe view of your Twenty CRM workspace, accessible from the desktop
- **Setup wizard** — guided configuration: server URL, workspace creation instructions, API key entry, connection test
- **Backend API** — `/api/crm/config`, `/api/crm/status`, `/api/crm/test-connection`, `/api/crm/iframe-url`
- **Server-side config** — all settings stored on the server filesystem (`/app/runtime/crm-config.json`), never in localStorage

## Pre-Install Requirements

Before installing this plugin, you need a working Twenty CRM server. This plugin does NOT deploy Twenty CRM — it connects to an existing instance.

### 1. Twenty CRM Server

Deploy Twenty CRM using Docker Compose. The minimum stack is:

| Container | Purpose | RAM |
|-----------|---------|-----|
| twenty-server | Web UI + REST API | 1.5 GB |
| twenty-worker | Async job processor | 768 MB |
| twenty-db | PostgreSQL 16 database | 512 MB |
| twenty-redis | Cache / queue | 256 MB |

Key `.env` settings for multi-tenant use:

```env
IS_MULTIWORKSPACE_ENABLED=true
SERVER_URL=https://crm.yourdomain.com
```

### 2. Domain & DNS

| Record | Type | Value |
|--------|------|-------|
| `crm.yourdomain.com` | A | Your server IP |
| `*.crm.yourdomain.com` | A | Your server IP (multi-workspace only) |

**Cloudflare users:** Set the CRM records to **DNS only (gray cloud)**, not proxied. Cloudflare's free plan does not issue SSL certificates for nested wildcards like `*.crm.yourdomain.com`.

### 3. SSL Certificate

For single-workspace, a standard cert for `crm.yourdomain.com` is sufficient.

For multi-workspace (wildcard subdomains), use Let's Encrypt with DNS-01 challenge:

```bash
# Install certbot + Cloudflare plugin
apt install certbot python3-certbot-dns-cloudflare

# Create Cloudflare credentials file
cat > /path/to/cloudflare.ini << 'EOF'
dns_cloudflare_api_token = YOUR_CLOUDFLARE_API_TOKEN
EOF
chmod 600 /path/to/cloudflare.ini

# Get wildcard cert
certbot certonly \
  --dns-cloudflare \
  --dns-cloudflare-credentials /path/to/cloudflare.ini \
  -d crm.yourdomain.com \
  -d '*.crm.yourdomain.com'
```

The Cloudflare API token needs **Zone > DNS > Edit** permissions for your domain.

Certbot auto-renews via systemd timer.

### 4. Nginx Reverse Proxy

Example nginx configuration:

```nginx
server {
    listen 80;
    server_name crm.yourdomain.com *.crm.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name crm.yourdomain.com ~^[a-z0-9-]+\.crm\.yourdomain\.com$;

    ssl_certificate /etc/letsencrypt/live/crm.yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/crm.yourdomain.com/privkey.pem;

    # CRITICAL: Allow iframe embedding from your OVU domains
    add_header Content-Security-Policy "frame-ancestors https://*.yourdomain.com https://yourdomain.com" always;

    # CRITICAL: No caching — stale iframes cause auth problems
    add_header Cache-Control "no-store, no-cache, must-revalidate" always;
    add_header X-Frame-Options "" always;

    location / {
        proxy_pass http://127.0.0.1:3000;

        # CRITICAL: Pass the full hostname — Twenty uses it to resolve the workspace
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

Key points:
- `proxy_set_header Host $host` — passes the subdomain to Twenty for workspace resolution
- `frame-ancestors` — required for the CRM iframe to load inside OpenVoiceUI
- `X-Frame-Options ""` — remove the default DENY/SAMEORIGIN that blocks iframe embedding
- `Cache-Control no-store` — prevents stale auth state in cached iframe responses

### 5. Workspace Creation

Workspaces **must be created through the Twenty signup UI**. Never insert workspace records directly into the database — they will be missing required metadata, schema tables, and permissions.

1. Navigate to `https://crm.yourdomain.com`
2. Click **Sign up**, enter email + password
3. Complete the onboarding wizard (this initializes ~30 metadata objects and the workspace schema)
4. Note the workspace subdomain from the URL

### 6. API Key Generation

1. Log in to your workspace
2. Go to **Settings** > **APIs & Webhooks**
3. Click **Create API key**
4. Set expiration to **Never** (for persistent agent access)
5. Copy the JWT token — you'll paste it into the plugin setup wizard

## Installation

1. Install the plugin from the OpenVoiceUI plugin catalog (or copy the `twenty-crm/` directory to `/app/plugins/`)
2. Restart the OpenVoiceUI container
3. Open the **CRM Setup** page from the desktop
4. Follow the 4-step wizard

## Agent Integration

For the AI agent to use the CRM autonomously (via the CRM skill), the API key must also be set as an environment variable in the agent's configuration:

```env
TWENTY_CRM_API_KEY=eyJhbGciOiJIUzI1NiIs...
```

The agent uses this key for direct REST API calls to create contacts, log notes, update deals, etc.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/crm/config` | GET | Get saved config (API key masked) |
| `/api/crm/config` | POST | Save config: `{crm_url, subdomain?, api_key}` |
| `/api/crm/status` | GET | Live connection test + status |
| `/api/crm/test-connection` | POST | Test credentials before saving |
| `/api/crm/iframe-url` | GET | Get the workspace iframe URL |

## Troubleshooting

**CRM iframe shows blank/error:**
- Check that nginx has `frame-ancestors` set to allow your OVU domain
- Check that `X-Frame-Options` is not set to `DENY` or `SAMEORIGIN`
- Verify SSL cert is valid (browser console will show mixed-content errors)

**API key returns 401:**
- Keys can expire — generate a new one in Twenty Settings
- Ensure multi-workspace mode matches your setup (key is workspace-scoped)

**Workspace not found after signup:**
- Twenty auto-generates a random UUID subdomain. Check the URL after completing onboarding.
- You can find it in the database: `SELECT subdomain FROM core.workspace`

**Connection timeout:**
- Verify Twenty containers are running: `docker compose ps`
- Check nginx is proxying to the correct port (default: 3000)
- Ensure no firewall rules blocking the CRM port
