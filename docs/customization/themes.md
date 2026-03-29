---
sidebar_position: 3
title: Themes
---

# Themes

OpenVoiceUI supports dynamic color theming with 7 built-in presets and full customization.

## Color System

Themes define two customizable colors that generate a full palette:

| Variable | Description | Default |
|----------|-------------|---------|
| `primary` | Main interface color | `#0088ff` |
| `primaryDim` | Darker variant | `#0055aa` |
| `primaryBright` | Brighter variant | `#00aaff` |
| `accent` | Secondary highlight color | `#00ffff` |
| `accentDim` | Darker accent | `#008888` |

These **fixed colors** are preserved across all themes:

| Color | Value | Usage |
|-------|-------|-------|
| `green` | `#00ff66` | Success states |
| `yellow` | `#ffdd00` | Warnings |
| `orange` | `#ff6600` | Alerts |
| `red` | `#ff2244` | Errors |

## Preset Themes

| Name | Primary | Accent |
|------|---------|--------|
| Classic Blue | `#0088ff` | `#00ffff` |
| Neon Pink | `#ff0088` | `#ff66cc` |
| Cyber Green | `#00ff88` | `#88ffcc` |
| Deep Ocean | `#0044cc` | `#4488ff` |
| Sunset Orange | `#ff6600` | `#ffaa00` |
| Blood Red | `#cc0033` | `#ff6666` |
| Matrix | `#00ff00` | `#88ff88` |

## Theme Persistence

Themes are saved to the server via `/api/theme` and loaded on page init. The `ThemeManager` in `src/ui/themes/ThemeManager.js` handles application and storage.

## API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/theme` | Load current theme |
| `POST` | `/api/theme` | Save theme. Body: `{primary, accent}` |

## Desktop Themes

The [Desktop Interface](/features/desktop-interface) has its own theme system for OS-style visual skins (Windows XP, macOS, Ubuntu, Windows 95, Windows 3.1). These are separate from the main color theme and apply only within the desktop canvas page.
