---
sidebar_position: 3
title: Desktop Interface
---

# Desktop Interface

OpenVoiceUI includes a desktop OS-style interface rendered as a canvas page. It provides a familiar windowed environment for managing multiple canvas pages simultaneously.

## Overview

The desktop is a full canvas page (`default-pages/desktop.html`) that renders:

- **Desktop icons** — Clickable shortcuts to canvas pages, auto-populated from the canvas manifest
- **Draggable windows** — Each opened page loads in a resizable, draggable window (minimum 300x200px)
- **Taskbar** — Bottom bar with a start button, window buttons for open pages, and a system tray with clock
- **Theme support** — Multiple OS themes change the visual style

## How It Works

The desktop is itself a canvas page. When you open another page from the desktop, it loads inside an iframe within a desktop window — nested iframes. The desktop fetches the canvas manifest on load to discover available pages and display them as desktop icons.

The agent can open the desktop with `[CANVAS:desktop]`, and it's typically the default page shown on first load.

## Themes

The desktop supports multiple visual themes:

- Windows XP
- macOS
- Ubuntu
- Windows 95
- Windows 3.1

Themes are applied via CSS custom properties and class swapping. Theme selection is persisted.

## Page Discovery

Desktop icons are dynamically generated from the canvas manifest (`/api/canvas/manifest`). When the agent creates a new canvas page, it automatically appears as a desktop icon on next load. Pages can be starred, categorized, and given custom voice aliases — all reflected in the desktop view.

## Integration with Canvas System

The desktop is a first-class canvas page. It uses the same manifest, category, and versioning systems as every other page. See [Canvas System](/features/canvas-system) for details on how pages are created, categorized, and managed.
