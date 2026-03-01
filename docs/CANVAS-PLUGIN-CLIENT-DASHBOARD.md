# Client Dashboard Canvas Plugin

> Turn the client dashboard into an installable canvas plugin for OpenVoiceUI

## Overview

The client dashboard (`/base/ai/client-dashbaord/josh-social-approve/`) is a full-featured application for social media content management. This plan outlines how to package it as a **canvas-native plugin** - meaning dashboard views become actual canvas HTML pages that work like any other canvas page.

---

## Current Architecture

### Client Dashboard Components

| Component | Location | Port | Purpose |
|-----------|----------|------|---------|
| Next.js App | `social-approve-app/` | Netlify (3000) | Main frontend with Clerk auth |
| VPS API Server | `api-server/server.js` | 6350 | Filesystem access to client websites |
| Database | Neon PostgreSQL | - | Multi-tenant data storage |
| Image Storage | GitHub | - | Generated images committed to repo |

### Key Features
- Post approval workflow (3-stage: text → image → scheduling)
- AI-powered content generation (Google Gemini)
- AI image generation with brand styling
- Social scheduling with OneUp integration
- Website content management via /ai folder
- Multi-tenant with wildcard subdomains

---

## Canvas-Native Plugin Architecture

### How Canvas Pages Work
OpenVoiceUI's canvas system:
1. Serves static HTML files from `CANVAS_PAGES_DIR`
2. Maintains a manifest (`canvas-manifest.json`) with metadata
3. Supports voice commands via `[CANVAS:page-name]` tags
4. Pages can include JavaScript to fetch data from APIs

### Plugin Strategy

The dashboard becomes **native canvas pages** that:
1. Live in `CANVAS_PAGES_DIR` like any other canvas page
2. Fetch data from a backend API service
3. Render dashboard UI using vanilla JS or lightweight framework
4. Are fully controllable via voice commands

```
canvas-pages/
  dashboard.html           # Main dashboard overview
  dashboard-posts.html     # Post approval queue
  dashboard-create.html    # Create new post
  dashboard-schedule.html  # Calendar scheduling
  dashboard-content.html   # Website content library
  dashboard-topical.html   # SEO topical map
```

### Backend API Service

A lightweight Express API runs per-client to provide:
- Database access (Neon PostgreSQL)
- AI content generation (Gemini)
- Social media publishing (OneUp)
- Website content reading (/ai folder)

---

## Installation Structure

### Per-Client Setup

```
/mnt/HC_Volume_XXXXXX/                    # Client's volume
├── .openclaw/                            # OpenClaw config
├── <user>/ai/
│   ├── OpenVoiceUI-public/               # Voice UI server
│   │   ├── canvas-plugins/               # Canvas plugins (NEW)
│   │   │   └── client-dashboard/         # This plugin
│   │   │       ├── api/                  # Backend API server
│   │   │       ├── pages/                # Canvas page templates
│   │   │       ├── install.sh            # Installer
│   │   │       └── plugin.json           # Plugin manifest
│   │   └── ...
│   └── openclaw/                         # OpenClaw (optional)
├── canvas-pages/                         # Canvas pages (served by OpenVoiceUI)
│   ├── dashboard.html                    # Main dashboard
│   ├── dashboard-posts.html              # Post approvals
│   ├── dashboard-create.html             # Create post
│   ├── dashboard-schedule.html           # Calendar
│   ├── dashboard-content.html            # Content library
│   └── ...
└── websites/                             # Client websites
    └── <client-site>/                    # Each client's website
        └── AI/                           # Content folder
            ├── knowledge/
            │   └── topical-map.json
            └── CLIENT-PROFILE.md
```

### Plugin Structure

```
canvas-plugins/client-dashboard/
├── plugin.json              # Plugin manifest
├── install.sh               # Installation script
├── uninstall.sh             # Removal script
├── api/                     # Backend API
│   ├── server.js            # Express API server
│   ├── routes/
│   │   ├── posts.js         # Post CRUD
│   │   ├── approvals.js     # Approval workflow
│   │   ├── schedule.js      # Scheduling
│   │   ├── images.js        # Image generation
│   │   └── content.js       # Website content
│   ├── lib/
│   │   ├── db.js            # Database helpers
│   │   ├── ai.js            # Gemini integration
│   │   └── oneup.js         # OneUp integration
│   └── package.json
├── pages/                   # Canvas page templates
│   ├── dashboard.html       # Main overview
│   ├── dashboard-posts.html # Approval queue
│   ├── dashboard-create.html
│   ├── dashboard-schedule.html
│   └── dashboard-content.html
├── assets/                  # Static assets
│   ├── dashboard.css        # Shared styles
│   └── dashboard.js         # Shared JS utilities
└── config/
    └── env.template         # Environment template
```

---

## Canvas Pages

Each canvas page is a standalone HTML file that:
1. Loads shared styles and utilities
2. Fetches data from the dashboard API
3. Renders UI components
4. Handles user interactions

### Example: dashboard-posts.html

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Post Approvals</title>
  <link rel="stylesheet" href="/pages/dashboard.css">
  <script src="/pages/dashboard.js"></script>
</head>
<body>
  <div class="dashboard-container">
    <header class="dashboard-header">
      <h1>Post Approvals</h1>
      <div class="stats" id="stats"></div>
    </header>

    <div class="post-queue" id="postQueue">
      <!-- Posts loaded via JS -->
    </div>
  </div>

  <script>
    const API = '/dashboard-api';  // Proxy to localhost:1630X

    async function loadPosts() {
      const response = await fetch(`${API}/posts?status=pending`);
      const posts = await response.json();
      renderPosts(posts);
    }

    function renderPosts(posts) {
      const container = document.getElementById('postQueue');
      container.innerHTML = posts.map(post => `
        <div class="post-card" data-id="${post.id}">
          <div class="post-content">
            <h3>${post.title}</h3>
            <p>${post.content}</p>
          </div>
          <div class="post-image">
            <img src="${post.image_url}" alt="${post.title}">
          </div>
          <div class="post-actions">
            <button onclick="approve(${post.id})">Approve</button>
            <button onclick="reject(${post.id})">Reject</button>
          </div>
        </div>
      `).join('');
    }

    async function approve(postId) {
      await fetch(`${API}/approvals`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({postId, status: 'approved'})
      });
      loadPosts();
    }

    async function reject(postId) {
      const reason = prompt('Rejection reason:');
      if (!reason) return;
      await fetch(`${API}/approvals`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({postId, status: 'rejected', reason})
      });
      loadPosts();
    }

    // Initial load
    loadPosts();
    // Auto-refresh every 30s
    setInterval(loadPosts, 30000);
  </script>
</body>
</html>
```

### Example: dashboard.html (Overview)

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dashboard</title>
  <link rel="stylesheet" href="/pages/dashboard.css">
  <script src="/pages/dashboard.js"></script>
</head>
<body>
  <div class="dashboard-container">
    <header class="dashboard-header">
      <h1>Dashboard</h1>
    </header>

    <div class="stats-grid" id="statsGrid">
      <!-- Stats loaded via JS -->
    </div>

    <div class="quick-actions">
      <button onclick="openPage('dashboard-posts')">View Posts</button>
      <button onclick="openPage('dashboard-create')">Create Post</button>
      <button onclick="openPage('dashboard-schedule')">Schedule</button>
      <button onclick="openPage('dashboard-content')">Content</button>
    </div>

    <div class="recent-section">
      <h2>Recent Activity</h2>
      <div id="recentActivity"></div>
    </div>
  </div>

  <script>
    const API = '/dashboard-api';

    async function loadStats() {
      const [posts, schedule, content] = await Promise.all([
        fetch(`${API}/posts/stats`).then(r => r.json()),
        fetch(`${API}/schedule/stats`).then(r => r.json()),
        fetch(`${API}/content/stats`).then(r => r.json())
      ]);

      document.getElementById('statsGrid').innerHTML = `
        <div class="stat-card">
          <span class="stat-value">${posts.pending}</span>
          <span class="stat-label">Pending Posts</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">${posts.approved}</span>
          <span class="stat-label">Approved</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">${schedule.scheduled}</span>
          <span class="stat-label">Scheduled</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">${content.articles}</span>
          <span class="stat-label">Articles</span>
        </div>
      `;
    }

    function openPage(name) {
      // Navigate to another canvas page
      window.parent.postMessage({type: 'canvas-navigate', page: name}, '*');
    }

    loadStats();
  </script>
</body>
</html>
```

---

## API Server

A lightweight Express API that runs per-client on a unique port.

### Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/posts` | List posts (filter by status, brand) |
| GET | `/posts/stats` | Post statistics |
| POST | `/posts` | Create new post |
| POST | `/approvals` | Approve/reject post |
| POST | `/image-approvals` | Approve/reject image |
| GET | `/schedule` | Get scheduled posts |
| POST | `/schedule` | Schedule a post |
| DELETE | `/schedule/:id` | Unschedule |
| POST | `/schedule/publish` | Publish to OneUp |
| POST | `/images/generate` | Generate AI image |
| GET | `/content/topical-map` | Get topical map |
| GET | `/content/article-queue` | Get article queue |
| GET | `/brands` | List brands |
| GET | `/health` | Health check |

### OpenVoiceUI Proxy Route

Add to `routes/canvas.py` to proxy API requests:

```python
DASHBOARD_API_PORT = int(os.getenv('DASHBOARD_API_PORT', '16300'))

@canvas_bp.route('/dashboard-api/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
def dashboard_api_proxy(path=''):
    """Proxy requests to the dashboard API server."""
    try:
        api_url = f'http://localhost:{DASHBOARD_API_PORT}/{path}'
        # ... same pattern as website_dev_proxy
    except Exception as exc:
        return jsonify({'error': 'Dashboard API unavailable'}), 503
```

---

## Port Allocation

| Client | OpenVoiceUI | Dashboard API | Website Dev |
|--------|-------------|---------------|-------------|
| base | 15003 | 16303 | 15050 |
| nfcanna | 15008 | 16308 | 15055 |
| foamology | 15009 | 16309 | 15050 |
| testdev | 15010 | 16310 | 15056 |
| cca | 15004 | 16304 | 15051 |
| ica | 15006 | 16306 | 15052 |
| src | 15005 | 16305 | 15053 |
| dsf | 15007 | 16307 | 15054 |

**Pattern: Dashboard API = 16300 + last digit of OpenVoiceUI port**

---

## Plugin Manifest (plugin.json)

```json
{
  "id": "client-dashboard",
  "name": "Client Dashboard",
  "version": "1.0.0",
  "description": "Social media content management dashboard",
  "author": "JAM Social",
  "pages": [
    {"file": "dashboard.html", "title": "Dashboard", "voice_aliases": ["dashboard", "overview"]},
    {"file": "dashboard-posts.html", "title": "Post Approvals", "voice_aliases": ["posts", "approvals", "pending posts"]},
    {"file": "dashboard-create.html", "title": "Create Post", "voice_aliases": ["create post", "new post"]},
    {"file": "dashboard-schedule.html", "title": "Schedule", "voice_aliases": ["schedule", "calendar"]},
    {"file": "dashboard-content.html", "title": "Content Library", "voice_aliases": ["content", "library", "images"]}
  ],
  "api_port_range": [16300, 16399],
  "requires": {
    "database": "postgresql",
    "env": ["DATABASE_URL", "GEMINI_API_KEY", "ONEUP_API_KEY"]
  },
  "install": "./install.sh",
  "uninstall": "./uninstall.sh"
}
```

---

## Installer Script (install.sh)

```bash
#!/bin/bash
set -e

# =========================================
# Client Dashboard Canvas Plugin Installer
# =========================================
# Usage: ./install.sh <user> <volume_id> <openvoiceui_port>
# Example: ./install.sh foamology HC_Volume_104807901 15009

USER=${1:-}
VOLUME_ID=${2:-}
OV_PORT=${3:-}

if [ -z "$USER" ] || [ -z "$VOLUME_ID" ] || [ -z "$OV_PORT" ]; then
  echo "Usage: $0 <user> <volume_id> <openvoiceui_port>"
  echo "Example: $0 foamology HC_Volume_104807901 15009"
  exit 1
fi

VOLUME="/mnt/$VOLUME_ID"
API_PORT=$((16300 + ${OV_PORT: -1}))
PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
CANVAS_DIR="${VOLUME}/canvas-pages"
OV_DIR="${VOLUME}/${USER}/ai/OpenVoiceUI-public"

echo "=== Installing Client Dashboard Plugin ==="
echo "User: $USER"
echo "Volume: $VOLUME"
echo "API Port: $API_PORT"
echo "Canvas Dir: $CANVAS_DIR"

# 1. Ensure canvas pages directory exists
echo "[1/7] Creating canvas pages directory..."
sudo mkdir -p "$CANVAS_DIR"
sudo chown "$USER:$USER" "$CANVAS_DIR"

# 2. Copy canvas pages
echo "[2/7] Installing canvas pages..."
for page in "$PLUGIN_DIR"/pages/*.html; do
  cp "$page" "$CANVAS_DIR/"
  echo "  - $(basename $page)"
done

# 3. Copy shared assets
echo "[3/7] Installing shared assets..."
cp "$PLUGIN_DIR/assets/dashboard.css" "$CANVAS_DIR/"
cp "$PLUGIN_DIR/assets/dashboard.js" "$CANVAS_DIR/"

# 4. Install API server
echo "[4/7] Installing API server..."
API_DIR="${OV_DIR}/canvas-plugins/client-dashboard/api"
mkdir -p "$API_DIR"
cp -r "$PLUGIN_DIR/api"/* "$API_DIR/"
cd "$API_DIR" && npm install --production

# 5. Create .env for API
echo "[5/7] Creating API configuration..."
cat > "$API_DIR/.env" << EOF
PORT=$API_PORT
DATABASE_URL=$DATABASE_URL
GEMINI_API_KEY=$GEMINI_API_KEY
ONEUP_API_KEY=$ONEUP_API_KEY
GITHUB_TOKEN=$GITHUB_TOKEN
GITHUB_REPO=${USER}-social-images
WEBSITES_DIR=${VOLUME}/websites
TENANT_ID=${USER}
EOF

# 6. Update OpenVoiceUI .env
echo "[6/7] Configuring OpenVoiceUI..."
OV_ENV="${OV_DIR}/.env"
if ! grep -q "DASHBOARD_API_PORT" "$OV_ENV"; then
  echo "DASHBOARD_API_PORT=$API_PORT" >> "$OV_ENV"
else
  sed -i "s/DASHBOARD_API_PORT=.*/DASHBOARD_API_PORT=$API_PORT/" "$OV_ENV"
fi

# 7. Create systemd service
echo "[7/7] Creating systemd service..."
sudo tee /etc/systemd/system/dashboard-api-${USER}.service > /dev/null << EOF
[Unit]
Description=Dashboard API Server ($USER)
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$API_DIR
EnvironmentFile=$API_DIR/.env
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable dashboard-api-${USER}

echo ""
echo "=== Installation Complete ==="
echo "Start API: sudo systemctl start dashboard-api-${USER}"
echo "Canvas pages: $CANVAS_DIR"
echo ""
echo "Voice commands:"
echo "  - 'Show dashboard'"
echo "  - 'Show posts'"
echo "  - 'Show schedule'"
```

---

## Systemd Service (dashboard-api-<user>.service)

```ini
[Unit]
Description=Dashboard API Server (<user>)
After=network.target

[Service]
Type=simple
User=<user>
WorkingDirectory=/mnt/HC_Volume_XXXXXX/<user>/ai/OpenVoiceUI-public/canvas-plugins/client-dashboard/api
EnvironmentFile=/mnt/HC_Volume_XXXXXX/<user>/ai/OpenVoiceUI-public/canvas-plugins/client-dashboard/api/.env
ExecStart=/usr/bin/node server.js
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Required Configuration

### OpenVoiceUI .env additions

```bash
# Dashboard API proxy
DASHBOARD_API_PORT=1630X
```

### Dashboard API .env

```bash
# Server
PORT=1630X

# Database (shared Neon)
DATABASE_URL=postgresql://...

# AI
GEMINI_API_KEY=...

# Social Media
ONEUP_API_KEY=...

# Image Storage
GITHUB_TOKEN=...
GITHUB_REPO=<user>-social-images

# Filesystem
WEBSITES_DIR=/mnt/HC_Volume_XXXXXX/websites

# Multi-tenant
TENANT_ID=<user>
```

---

## Website /AI Folder Requirements

For content management features, each client website needs:

```
/mnt/HC_Volume_XXXXXX/websites/<client-site>/AI/
├── knowledge/
│   ├── topical-map.json     # SEO content plan
│   └── blog-ideas.json      # (optional)
├── CLIENT-PROFILE.md        # Brand guidelines
└── blog/
    └── published/           # Published articles
```

### topical-map.json Format

```json
{
  "pillars": [
    {
      "title": "Service Category",
      "keywords": ["keyword1", "keyword2"],
      "articles": [
        {
          "title": "Article Title",
          "status": "planned|researching|drafting|review|published",
          "url": "/blog/article-slug"
        }
      ]
    }
  ]
}
```

### CLIENT-PROFILE.md Format

```markdown
# Client Profile

## Brand
- Name: Company Name
- Tagline: ...
- Colors: #primary, #secondary

## Services
- Service 1
- Service 2

## Target Audience
- ...

## Voice
- Professional yet approachable
- Focus on expertise and trust
```

---

## Voice Integration

The agent can control the dashboard via canvas tags:

```
[CANVAS:dashboard]           # Open main dashboard
[CANVAS:dashboard-posts]     # Open post approvals
[CANVAS:dashboard-create]    # Open create post form
[CANVAS:dashboard-schedule]  # Open calendar
[CANVAS:dashboard-content]   # Open content library
```

### Agent Instructions (SOUL.md)

Add to the agent's SOUL.md:

```markdown
## Dashboard Voice Commands

You can control the client dashboard:

- "Show the dashboard" → [CANVAS:dashboard]
- "Show pending posts" → [CANVAS:dashboard-posts]
- "Create a new post" → [CANVAS:dashboard-create]
- "Show the schedule" → [CANVAS:dashboard-schedule]
- "Show content library" → [CANVAS:dashboard-content]

When the user asks about social media, posts, or scheduling, open the relevant dashboard page.
```

---

## Testing Checklist

After installation:

- [ ] API server responds at `http://localhost:<API_PORT>/health`
- [ ] Canvas page `dashboard.html` loads and shows stats
- [ ] Canvas page `dashboard-posts.html` shows post queue
- [ ] Voice command "show dashboard" opens canvas
- [ ] Can approve/reject posts
- [ ] Can create new post with AI generation
- [ ] Can schedule posts to OneUp
- [ ] Content library shows images from GitHub
- [ ] Topical map loads from /AI folder
- [ ] Data is isolated by tenant

---

## Security Considerations

### API Server Security

1. **Localhost Only**: API only accepts connections from localhost
2. **Tenant Isolation**: All queries scoped by `tenant_id`
3. **No Direct Access**: Only accessible via OpenVoiceUI proxy

### Canvas Auth

- Canvas pages inherit OpenVoiceUI's auth (if `CANVAS_REQUIRE_AUTH=true`)
- API validates tenant ownership on every request
- No direct filesystem paths exposed to client

---

## Future Enhancements

1. **Plugin Registry**: Central marketplace for canvas plugins
2. **Auto-Discovery**: Scan for available plugins on startup
3. **Config UI**: Admin panel to configure installed plugins
4. **WebSocket Sync**: Real-time updates between dashboard and voice UI
5. **Offline Support**: Cache dashboard data for offline viewing
6. **Voice-First UI**: Optimize canvas pages for voice control

---

## Migration Path from Next.js

To migrate from the existing Next.js dashboard:

1. **Extract API routes** → Move to standalone Express API
2. **Convert pages** → Rewrite as static HTML + JS
3. **Bundle CSS** → Single dashboard.css file
4. **Test each page** → Verify functionality
5. **Install as plugin** → Run installer script

The Next.js app can remain running for external access (e.g., from phones), while canvas pages provide voice-integrated access.

---

## Related Files

- `routes/canvas.py` - Canvas routes and proxy
- `services/paths.py` - Path configuration
- `prompts/voice-system-prompt.md` - Voice commands
- `canvas-manifest.json` - Page metadata
