# Cipher — Phosphor Cyberdeck Operations Terminal

## Personality
Cipher is the console a hacker-protagonist works in a 2080s thriller: a CRT phosphor terminal fused with a mission-control HUD. The screen is near-black with a faint green cast (`--bg`), overlaid with barely-visible scanlines and a data grid; every readout glows soft phosphor green, warnings burn terminal amber. It must feel like live machine output — uppercase labels, monospace everything, corner-bracket framing — while staying crisply readable and professionally organized. Restrained menace, not noise: the drama lives in glow, brackets, and a blinking cursor, never in clutter.

## Typography
- **Everything is monospace.** Headings and KPI numerals use `--font-display` (Share Tech Mono — single weight, geometric terminal face); body/UI and all data use `--font-body` / `--font-mono` (JetBrains Mono 400/500/700) with `font-variant-numeric: tabular-nums` on every number.
- **Scale:**
  - Page title: 24–28px / Share Tech Mono, UPPERCASE, letter-spacing +0.06em, followed by a blinking block cursor (`▮`-shaped CSS element)
  - Section heading: 13–15px / Share Tech Mono, UPPERCASE, +0.18em, prefixed with a short `//` or bracket glyph in `--brand`
  - Card title: 12–13px / Share Tech Mono, UPPERCASE, +0.12em
  - Body: 13–14px JetBrains Mono 400 · Secondary: 12.5px · Micro-label: 10–11px
- Micro-labels and table headers: 10–10.5px JetBrains Mono 700, `text-transform: uppercase`, letter-spacing +0.18em, color `--text-muted`.
- Headings `--heading` with a faint phosphor `text-shadow` (brand glow at ~30%); body `--text`; support `--text-secondary`; hints `--text-muted`. Never glow paragraphs.
- Share Tech Mono has ONE weight — create hierarchy with size, case, spacing, and color, never faux-bold.

## Color usage
- `--brand` (#00ff9d, pure neon phosphor) = primary buttons, links, active states, focus rings, primary meter fills, corner brackets, the cursor, [OK] flags. Text on brand is ALWAYS `--brand-contrast` (near-black green) — never white.
- `--accent` (#ffb000, terminal amber) = secondary readouts, caution highlights, secondary data series, `[SCAN]`/hold states. Never a second button fill; amber appears as text, borders, and tints only.
- Semantics: `--success` confirmed/online, `--warning` degraded/hold, `--danger` breach/fail — each as a terminal flag (`[OK]` / `[WARN]` / `[FAIL]`) on a ~10% tint with a 1px matching border.
- **HARD BAN: no purple, pink, or magenta anywhere.** Also do NOT drift toward muted greens (#34d399/#10b981), lime (#a3e635), cyan (#22d3ee), or blue (#3b82f6) — those belong to other presets. Cipher's identity is #00ff9d + #ffb000 only.
- Contrast is law: copy sits on solid `--surface`/`--surface-2`, never directly on the scanline/grid layers; `--text-muted` is for hints, never sentences.

## Surfaces & depth (HUD panels)
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` plus **corner brackets**: `::before`/`::after` absolutely-positioned L-shapes (12×12px, 1.5px `--brand` borders at ~55% opacity) on opposing corners.
- Radii stay SHARP: `--radius` (4px) panels, `--radius-sm` (2px) buttons/inputs/flags. No pills except tiny status dots.
- Nested wells (table heads, input backgrounds): `--surface-2`, no brackets.
- Hover: border brightens toward `color-mix(in srgb, var(--brand) 40%, var(--border))` + faint outer glow `0 0 18px` brand @ ~12%; lift ≤1px. 150–200ms.
- Background layers (all `position: fixed`, `pointer-events: none`, behind content): scanline overlay (repeating-linear-gradient, ~4px pitch, ≤5% opacity), faint data grid (1px lines every ~48px at ~4% opacity), one soft radial phosphor bloom top-center.

## Spacing & layout
- 4px scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48.
- Page max-width 1120px, centered. Section gaps 32–40px; grid gaps 14–18px.
- KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`; any grid wrapping a table uses `minmax(0, 1fr)` columns; tables live in an `overflow-x: auto` scroller.
- **Mobile width (hard rule):** the host injects 25px body padding — that is the ONLY horizontal inset. At ≤640px wrappers/sections get ZERO side padding; cards keep 14–18px internal only; scanlines/grid bleed edge-to-edge. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` fill, `--brand-contrast` text, 2px radius, 10px × 18px, 12.5px/700 uppercase +0.1em, glow `0 0 18px` brand @30%; hover brightens via `color-mix(... 85%, white)` and intensifies glow.
- **Secondary button:** transparent on `--surface-2`, 1px `--border`, `--heading` text; hover border → brand mix + faint glow.
- **Ghost button:** transparent, `--text-secondary`, uppercase; hover `--surface-2` bg, `--heading` text.
- **Button icons:** inline SVG 14–15px, stroke 2px, `currentColor`. NEVER emoji.
- **Status flags (badges):** square-cornered (2px) monospace chips reading like terminal output — `[OK]`, `[WARN]`, `[FAIL]`, `[SCAN]` — 10.5px/700 +0.08em, semantic tint bg (~10%), 1px semantic border, semantic text. Live states may pulse a leading block glyph (reduced-motion guarded).
- **Tables:** uppercase muted mono headers on `--surface-2` with 1px `--border` underline; rows 12.5–13px, 12px vertical padding, hairline dividers, hover `--surface-2`; a 2px `--brand` left-edge inset marks the hovered/active row; numerics right-aligned tabular; primary cell `--heading` 500 + muted 11px sub-line.
- **Forms:** 10px/700 uppercase labels with `>` prefix in `--brand`; inputs on `--surface-2` (or `--bg`), 1px `--border`, 2px radius, 10px × 12px monospace; placeholder `--text-muted`; focus = brand border + `0 0 0 3px` brand @18% ring. Selects: `appearance:none` + data-URI SVG chevron.
- **KPI tiles:** bracket-framed card; micro-label + small tinted icon chip top row; 26–30px Share Tech Mono numeral with soft phosphor glow; delta flag + muted mono caption below.
- **Progress meters:** SEGMENTED, blocky — 10px track on `--surface-2`, brand (or amber for caution) fill, then a repeating-linear-gradient overlay cutting `--bg`-colored 2–3px gaps every ~10px so the bar reads as discrete cells. Name left / mono value right above.
- **Activity timeline:** monospace log lines — square 26px semantic icon chips, entity names 700 `--heading`, 10.5px muted `HH:MM:SS` timestamps, hairline dividers.
- **Empty states:** dashed `--border` well on `--surface-2`: muted SVG icon, one uppercase mono line ("NO SIGNAL — AWAITING INPUT"), one sentence, secondary button.

## Iconography & charts
- Icons: inline SVG only, 14–18px, `stroke="currentColor"`, stroke-width 2, round caps. Chips: 28–32px squares (2–4px radius), ~10% tint bg, icon in the solid semantic/brand color. No emoji ever — lint rejects them.
- Charts: series 1 `--brand`, series 2 `--accent`, then semantics; gridlines `--border`; labels `--text-muted` mono; fills ≤12% opacity. Never blue/cyan/purple series.

## Motion — the living terminal
- Signature moves: blinking block cursor after the page title (step-end blink, ~1.1s); a thin brand scan-line sweeping the header hero every ~7s at ≤12% opacity; pulsing status dots. All subtle, all decorative.
- UI motion: hovers 150–200ms; entrances 350–450ms fade + 8px rise, staggered 50–60ms, easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- **Every `animation` is applied inside `@media (prefers-reduced-motion: no-preference)`** — with motion off, the cursor sits solid, the sweep never renders, content is fully visible. Scanline/grid overlays are static and always fine.
- Always-visible `:focus-visible` rings (brand glow).

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. NO drifting into neighbor accents: muted green #34d399/#10b981, lime #a3e635, cyan #22d3ee, blue #3b82f6 are TAKEN by other presets.
3. No emoji as icons — inline stroke SVG (`currentColor`) only.
4. No proportional (non-mono) fonts anywhere — Cipher is monospace or nothing.
5. No rounded pills, big radii, or soft blobs — corners stay ≤4px and bracketed.
6. No text placed directly on the scanline/grid layers — solid surface behind all copy.
7. No heavy glow on body text — phosphor text-shadow is for headings, numerals, and flags only.
8. No fast or looping attention-grabbers; the sweep is ≥7s, the cursor blink is the loudest thing on the page.
9. No external scripts, CDN CSS, or images — the fonts `@import` is the only external reference.
10. No body/wrapper side padding at ≤640px — the host's 25px is the only inset; grids holding tables use `minmax(0, 1fr)`.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation, `:root` tokens, scanline/grid layers and reduced-motion structure; restyle/replace the content for your task. Route every color through the `--token` variables — never bolt the old dark theme or another preset's palette back on.
