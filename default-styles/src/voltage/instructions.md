# Voltage — Extreme Sports / Energy Drink

## Personality
Voltage is X-Games broadcast graphics: Monster-Energy loud, Red Bull Rampage confident, skate-brand raw. Electric volt-green hits hard against a green-tinted blacked-out base — big fills, glows, hazard stripes, towering condensed uppercase. But it is still a working business dashboard: the flash lives in headers, dividers, badges, and hover states, while tables, forms, and body copy stay crisp, quiet, and high-contrast.

## Typography
- **Faces:** display/headings use `--font-display` (Bebas Neue — one weight, 400, always uppercase); body and UI use `--font-body` (Chivo 400/500/700, 900 for impact stats); numerals/IDs/code use `--font-mono` (Martian Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 40–52px Bebas Neue, `letter-spacing: 0.02em`, line-height 0.95
  - Section heading: 22–26px Bebas Neue, `letter-spacing: 0.04em`
  - Card title: 17–19px Bebas Neue, `letter-spacing: 0.05em`
  - Body: 14px Chivo 400 · Secondary: 13px · Micro-label: 10–11px / 700 / `letter-spacing: 0.14em` / uppercase
  - KPI numerals: 34–40px Chivo 900 (or Bebas Neue 42px), tabular, tight line-height 1
- Bebas Neue is ALWAYS uppercase (it has no lowercase presence) — never use it under 15px; small text is Chivo.
- Aggressive uppercase micro-labels everywhere labels appear: eyebrows, table headers, badge text, KPI captions.
- Headings `--heading` (near-white), body `--text`, support `--text-secondary`, hints `--text-muted`. Big display headlines may take `--brand` for ONE word max (a knockout emphasis), never the whole line.

## Color usage
- `--brand` (#a3e635 volt) is the identity and is used LOUD: primary button fills, hazard stripes, progress fills, glow accents, active states, skewed badges, the header underline. Volt is a FILL color here, not just a trim.
- **HARD RULE: any volt-filled element carries `--brand-contrast` (near-black #0d0f0a) text/icon — NEVER white on volt.** White knockout text belongs on black surfaces only.
- `--accent` (#84cc16 deeper acid) is volt's shadow tone: hover states of volt fills, gradient partners (`linear-gradient(180deg, var(--brand), var(--accent))`), secondary stripes. Never introduce a second hue family.
- Semantic colors carry meaning only: `--success` emerald (distinct from volt — do not swap them), `--warning` amber, `--danger` red. Tint backdrops at 12–16% via `color-mix(in srgb, var(--success) 14%, transparent)`.
- Body copy and table text NEVER volt — reading surfaces stay `--text` on dark. Links are volt.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` — the shadow token includes a faint volt top-edge inset. Padding 20–24px.
- **Feature/hero cards:** add a 3px volt top bar or a left hazard-stripe rail (see below). One glow card per page max: `box-shadow: 0 0 24px rgba(163, 230, 53, 0.18)`.
- Inset areas (table heads, form wells): `--surface-2`, radius `--radius-sm`, no shadow.
- **Hazard stripes** (signature): `repeating-linear-gradient(-45deg, var(--brand) 0 10px, var(--bg) 10px 20px)` — as 6–8px divider bars, badge rails, and the header's bottom edge. Use 2–3 per page, never as a large background.
- **Angled edges:** section banners/hero strips get `clip-path: polygon(0 0, 100% 0, 100% calc(100% - 14px), 0 100%)` (or mirrored) for the torn-broadcast feel. Cards holding tables/forms stay rectangular.
- **Skew:** badges and micro-tags `transform: skewX(-8deg)` with inner text counter-skewed `skewX(8deg)`. Never skew tables, inputs, or paragraphs.
- Radii are tight: `--radius` 6px cards, `--radius-sm` 3px buttons/badges. No pills except status dots.

## Spacing & layout
- 4px scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1160px, centered; section gaps 32–40px.
- KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`; any grid column containing a table uses `minmax(0, …)` so it can shrink.
- **Mobile width (hard rule):** the host injects 25px body padding — that is the ONLY horizontal inset. At ≤640px wrappers/sections get ZERO side padding; cards keep 16–20px internal; hazard stripes may bleed edge-to-edge. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** volt fill, `--brand-contrast` text, 900-weight uppercase Chivo 13px `letter-spacing: 0.08em`, 3px radius, 11px × 18px pad, glow `0 0 18px rgba(163, 230, 53, 0.35)` on hover, hover bg `--accent`.
- **Secondary button:** transparent, 2px volt border, volt text; hover fills volt with near-black text.
- **Ghost button:** transparent, `--text-secondary` text, uppercase; hover `--surface-2` bg + `--heading` text.
- **Badges:** skewed (`skewX(-8deg)`, counter-skewed text), 10px/700 uppercase, 4px × 10px, semantic tint bg + semantic text, 3px radius. Volt badge = volt fill + black text for "LIVE/HOT" states.
- **Tables:** `--surface-2` header row with 10px/700 uppercase `--text-muted` letterspaced heads; rows 13.5px Chivo with 13px vertical pad; hairline `--border` dividers only; hover row `--surface-2` plus a 2px volt left edge (inset box-shadow). Numerics right-aligned Martian Mono. No zebra, no verticals.
- **Forms:** 11px/700 uppercase labels above; inputs `--surface-2` bg, `--border` border, 3px radius, `--heading` text; focus = volt border + `0 0 0 3px rgba(163, 230, 53, 0.18)` ring.
- **KPI tiles:** uppercase micro-label top, 34–40px 900-weight tabular value, semantic delta chip below; one featured KPI may take the volt-fill treatment (whole tile volt, black text).
- **Progress bars:** 10px tall, `--surface-2` track, volt fill (semantic fills for status meters), square ends (3px radius), mono fraction right-aligned above.
- **Activity/timeline:** 30px squared icon chips (3px radius, tinted bg + semantic color inline SVG), bold entity names, mono timestamps.
- **Empty states:** dashed `--border` well on `--surface-2`, muted SVG icon, one Bebas line, one Chivo sentence, secondary button.

## Motion
- Hovers 120–150ms, entrances 350–450ms, easing `cubic-bezier(0.22, 1, 0.36, 1)`. Entrance: rise 10–14px + fade, staggered 50ms. Animate opacity/transform only — glows may transition box-shadow on hover.
- Wrap EVERY animation in `@media (prefers-reduced-motion: reduce) { animation: none; opacity: 1; transform: none }`. Keep `:focus-visible` volt rings always on.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. NEVER white text on a volt fill — volt always carries near-black (`--brand-contrast`).
3. No volt-colored body copy or table text — reading surfaces stay neutral.
4. No second hue family: volt/acid + the three semantics is the whole palette.
5. No skewing tables, inputs, paragraphs, or whole cards — skew is for badges/tags only.
6. No hazard stripes as large backgrounds — thin bars and rails only (2–3 per page).
7. No emoji as icons — inline stroke/currentColor SVG only.
8. No lowercase Bebas Neue or Bebas under 15px — small type is Chivo.
9. No rounded pills or soft 12px+ radii — Voltage is sharp (3–6px).
10. No external scripts, CDN CSS, or images — self-contained HTML, fonts via the one @import.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
