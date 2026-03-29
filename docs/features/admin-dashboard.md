---
sidebar_position: 7
title: Admin Dashboard
---

# Admin Dashboard

Access the admin dashboard at **localhost:5001/admin**. It's mobile-responsive and provides full control over agents, content, providers, and system health.

## Panels

### Agents

#### Profiles

View and activate agent personas. Each profile bundles a personality, voice, LLM provider, and feature set. Click to switch the active agent. See [Profiles](/customization/profiles) for the full schema.

#### Agent Editor

Edit the active agent's configuration across 4 tabs:

- **Profile** — Name, description, icon, adapter selection
- **System Prompt** — The agent's personality and instructions
- **Features** — Toggle canvas, vision, music, tools, emotion detection, DJ soundboard
- **Agent Files** — Browse and edit workspace files (SOUL.md, TOOLS.md, AGENTS.md, etc.)

#### Plugins

Install, manage, and uninstall plugins. Shows installed plugins with status and available plugins from the catalog. See [Plugin System](/extending/plugin-system).

### Content

#### Canvas Pages

Manage all canvas pages:

- Toggle **public/private** visibility
- **Lock** pages (admin-only, prevents agent from changing visibility)
- **Delete** with archive (pages are archived, not permanently removed)
- View page URLs for sharing

#### Workspace Files

Browse the agent's workspace directory. Supports:

- File tree navigation
- Inline text editing
- Audio file playback
- Image file preview

#### Music (Suno)

View all generated songs, play tracks inline, and archive/manage the music library. See [Music System](/features/music-system).

### Config

#### Provider Config

Select and configure LLM, TTS, and STT providers. Changes save to the active profile. Provider options depend on which API keys are configured in `.env`.

### System

#### Health & Stats

Live system metrics:

- CPU, RAM, and disk usage
- Server uptime
- Top processes by memory
- Gateway connection status
- Session reset control

#### Connector Tests

12 automated endpoint diagnostics that verify:

- Gateway connectivity
- TTS provider health
- STT provider health
- Canvas system
- Music system
- File system access

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/auth/check` | Verify Clerk session auth |
| `GET` | `/api/admin/gateway/status` | Gateway connection status |
| `POST` | `/api/admin/gateway/rpc` | Proxy RPC call to OpenClaw gateway |
| `GET` | `/api/server-stats` | CPU, RAM, disk, uptime, top processes |
| `GET` | `/api/refactor/status` | Playbook task statuses |
| `GET` | `/api/refactor/activity` | Last 50 activity log entries |
| `GET` | `/api/refactor/metrics` | Performance metrics |
| `POST` | `/api/refactor/control` | Pause/resume/skip refactor tasks |
| `POST` | `/api/admin/install/start` | Trigger framework installation |
| `GET` | `/api/admin/clients` | List all client directories |
