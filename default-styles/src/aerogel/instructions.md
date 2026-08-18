# Aerogel — Spatial-Computing Glass, Daylight

## Personality
Aerogel is the interface of a 2035 AR headset rendered in a bright, clean studio: visionOS panels, Fluent acrylic at production polish. Everything FLOATS — frosted translucent glass over a luminous porcelain-silver room washed with faint drifting mint and grey light. Type is graphite ink, calm and engineered; the single electric-mint accent (`--accent`) fires only where something is LIVE. It should feel a decade ahead of every other light theme, without ever getting loud.

## Typography
- **Faces:** `--font-display` (Inter Tight) for everything textual — headings tight and confident, body relaxed; `--font-mono` (JetBrains Mono) for data, IDs, timestamps, KPI deltas, with `font-variant-numeric: tabular-nums`.
- **Scale:** page title 26–30px/600 at −0.03em; section headings 15–17px/600 at −0.01em; card titles 14px/600; body 14px/400; secondary 13px; micro-labels 10.5–11px/600 uppercase at +0.10em in `--text-muted`.
- **KPI numerals are the signature:** 34–40px, weight 300 (thin!), −0.02em, `--heading`, tabular. Large, light, optical — like a heads-up display readout.
- Sentence case everywhere; uppercase is reserved for micro-labels and table column headers. Never all-caps headings or buttons.
- Colors: headings explicit `--heading`, body `--text`, support `--text-secondary`, hints `--text-muted`. Always declare h1 and a colors explicitly.

## Color usage
- `--brand` IS graphite ink (#16202c): primary buttons, headings, the dock. Aerogel's authority is dark ink on glass, not a hue.
- `--accent` (electric mint #0bd9a2) is SURGICAL: live/active pulse dots, focus rings, progress fills, sparklines, active segment of segmented controls, one glowing hero delta. Never body text on white (too light) — for mint text use it only ≥13px/600 on tinted chips, or darken via `color-mix(in srgb, var(--accent) 70%, var(--heading))`.
- **NO blue anywhere** (meridian owns blue), no purple/pink/magenta ever. Neutrals stay cool silver-grey, never tinted toward blue accents.
- Semantics carry meaning only: `--success` complete/positive, `--warning` pending/at-risk, `--danger` overdue/failed — always as 10–12% tinted pill backgrounds with the semantic color as text.

## Surfaces & depth — the three glass tiers
Every panel is frosted glass: translucent white + `backdrop-filter: blur()` (ALWAYS with `-webkit-backdrop-filter` too) + a crisp 1px hairline + a specular top-edge inner highlight `inset 0 1px 0 rgba(255,255,255,0.7)`.
- **Tier 1 — floating panels** (cards, table wrap, sections): `rgba(255,255,255,0.62)`, blur 20px saturate(1.4), border `rgba(255,255,255,0.75)` with an outer hairline shadow ring `0 0 0 1px rgba(22,32,44,0.06)`, soft shadow `0 12px 32px rgba(22,32,44,0.08)`, radius `--radius` (22px).
- **Tier 2 — raised controls** (buttons, inputs, chips, segmented controls): `rgba(255,255,255,0.78)`, blur 12px, radius `--radius-sm` (14px) or 999px pills, tighter shadow `0 2px 8px rgba(22,32,44,0.08)`.
- **Tier 3 — the dock** (floating status/action strip): `rgba(255,255,255,0.55)`, blur 28px saturate(1.6), deepest shadow `0 20px 48px rgba(22,32,44,0.16)`, full pill radius.
- Inset wells (table headers, form wells): `--surface-2` at ~65% opacity, radius `--radius-sm`, no shadow.
- Background: `--bg` porcelain-silver plus two or three VERY faint large radial gradients (mint ≤8% alpha, ink-grey ≤5%) as fixed layers, slowly drifting via a 40s+ transform loop (reduced-motion guarded).
- Hover lift: panels `translateY(-2px)` + deeper shadow; controls gain a mint halo `0 0 0 3px rgba(11,217,162,0.18)`.

## Spacing & layout
- 4px scale: 4/8/12/16/20/24/32/48/64. Page max-width 1120px centered. Section gaps 28–36px, grid gaps 16–20px.
- KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`. Any grid that contains a table MUST use `minmax(0, 1fr)` and the table sits inside an `overflow-x: auto` wrapper.
- **Mobile width law (hard):** the host injects 25px body padding — the ONLY horizontal inset. At ≤640px, wrappers and sections get ZERO side padding; cards keep 16–20px internal only; backgrounds may bleed edge-to-edge. Never nest padded containers on mobile. Do not add body padding.

## Component recipes
- **Primary button:** ink pill (`--brand` bg, `--brand-contrast` text, 999px), 10px × 20px, 14px/600, specular inner top highlight `inset 0 1px 0 rgba(255,255,255,0.18)`; hover lifts 1px + mint halo.
- **Secondary button:** tier-2 glass pill, `--heading` text, hairline border; hover brightens fill.
- **Ghost button:** transparent pill, `--text-secondary`; hover glass fill + `--heading` text.
- **Segmented control:** tier-2 glass pill track with 4px padding; active segment = white pill, ink text, 2px mint underline-dot or mint left dot.
- **Badges:** 999px pills, 11px/600, semantic 10–12% tint bg + semantic text + 6px `::before` dot.
- **KPI tiles:** micro-label row with a live pulse dot (mint, animated ripple on active metrics) → thin 36px numeral → mono delta chip → tiny inline-SVG sparkline (mint stroke, 2px, `vector-effect: non-scaling-stroke`). One hero tile gets a soft mint radial glow behind the numeral.
- **Tables:** glass wrap; uppercase 10.5px muted headers on a translucent well; 13.5–14px rows, hairline dividers only, row hover = faint mint-white wash; numerics right-aligned tabular mono; primary line `--heading`/500 + muted mono sub-line.
- **Forms:** 12px/600 labels above; inputs tier-2 glass, 999px or 14px radius, 10px × 16px; focus = mint border + `0 0 0 3px rgba(11,217,162,0.20)` halo; selects `appearance: none` + inline data-URI chevron.
- **Progress:** slim 6–8px rounded track (translucent well) + mint fill with a soft outer glow `0 0 8px rgba(11,217,162,0.5)`; name left, mono fraction right.
- **Activity timeline:** 30px glass icon discs + text; entity names bolded ink; 12px mono timestamps; hairline rail between items.
- **Dock:** fixed-feel tier-3 pill strip (in-flow footer strip is fine) holding status text + ghost/primary actions.
- **Empty states:** dashed hairline glass well, centered muted SVG icon, one-line heading, one sentence, secondary button.

## Motion
- Durations: 150ms hovers, 350–500ms entrances; easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrances: panels fade + rise 10px + very slight blur-in, staggered 50–70ms. Live dots pulse (2.4s ripple). Background wash drifts over 40–60s. Animate opacity/transform/box-shadow only.
- EVERY animation lives inside `@media (prefers-reduced-motion: no-preference)`. Always visible `:focus-visible` mint rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. NO blue accents of any kind — blue belongs to meridian/obsidian; aerogel's only hue is mint.
3. No opaque flat cards — every panel is translucent glass with backdrop blur (and the `-webkit-` prefix).
4. No dark sections or dark-mode blocks; Aerogel is daylight-only.
5. No mint floods: accent on ≤5% of the page — fills, halos, dots, sparklines only, never large surfaces.
6. No emoji as icons — inline stroke SVG (16–20px, 1.5–2px, currentColor) only.
7. No heavy saturated gradients; ambient wash stays ≤8% alpha and enormous.
8. No sharp corners: nothing below 12px radius except hairlines; buttons/inputs are pills.
9. No zebra stripes, vertical rules, or boxed table cells.
10. No external scripts, CDN CSS, or remote images — self-contained HTML, Google Fonts @import only.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
