# Aurora — Northern Lights / Arctic Night

## Personality
Aurora is the feeling of standing under the borealis on a clear arctic night: a near-black blue sky (`--bg`) with slow ribbons of living green and ice-cyan light drifting behind everything, and a faint static starfield. The light show lives ONLY in the background, the header hero, and card edge accents — content sits on solid frost-glass surfaces and stays crisply, boringly readable. Think planetarium show meets Nat Geo polar photography: atmospheric and flashy, but professional.

## Typography
- **Faces:** headings and KPI numerals use `--font-display` (Unbounded — wide, cosmic; weights 300–600 ONLY, never 700+); body/UI uses `--font-body` (Albert Sans); IDs, coordinates, timestamps, and numbers use `--font-mono` (Sometype Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 26–30px / Unbounded 500, letter-spacing −0.01em
  - Section heading: 15–17px / Unbounded 400
  - Card title: 13–14px / Unbounded 400
  - Body: 14px / 400 · Secondary: 13px · Micro-label: 10–11px
- **Unbounded is a display face** — light-to-medium weights, generous line-height (1.25+), never for paragraphs, never below 12px.
- Micro-labels and table headers: 10–11px Albert Sans 600, `text-transform: uppercase`, letter-spacing +0.14em, color `--text-muted`.
- Headings `--heading` (ice white); body `--text`; support `--text-secondary`; hints `--text-muted`. KPI numerals may glow: `text-shadow: 0 0 24px` brand/accent at ~35% opacity.

## Color usage
- `--brand` (#34d399, arctic green) = primary buttons, active states, links, focus rings, primary progress fills, the aurora's dominant ribbon. Text on brand is ALWAYS `--brand-contrast` (near-black green) — never white.
- `--accent` (#67e8f9, ice cyan) = the aurora's second ribbon, informational badges, secondary chart series, cool highlights. Never a second button color.
- The two may share a gradient ONLY in atmospheric elements (sky ribbons, hero glow, card top-edge lines, progress fills) — never on body text or table content.
- Semantics carry meaning only: `--success` confirmed/live, `--warning` pending/watch, `--danger` cancelled/alert — each on a ~12%-opacity tint pill.
- **HARD BAN: no purple, pink, or magenta anywhere.** This aurora is the green/cyan kind only. No hue between red and blue via violet — ever.
- Contrast is law: body text ≥ `--text` on `--surface`; never set copy directly on the animated sky without a solid surface behind it.

## Surfaces & depth (frost glass)
- **Card recipe:** `background: color-mix(in srgb, var(--surface) 88%, transparent); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); backdrop-filter: blur(8px);` plus a glowing top edge — a 1px `::before` line fading through brand→accent→transparent at ~55% opacity.
- Nested wells (table heads, form wells, code): `--surface-2`, radius `--radius-sm`, no blur, no glow.
- Hover lift: `translateY(-2px)` + border brightens toward `color-mix(in srgb, var(--brand) 35%, var(--border))`, 200ms.
- Radii: `--radius` (14px) cards, `--radius-sm` (9px) buttons/inputs, 999px pills.
- The starfield (tiny `box-shadow` dots) and aurora ribbons are fixed, `z-index: -1`, `pointer-events: none` — content never interleaves with them.

## Spacing & layout
- 4px scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64.
- Page max-width 1120px, centered. Section gaps 32–40px; grid gaps 16–20px.
- KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(280px, 1fr)`; any grid wrapping a table uses `minmax(0, 1fr)` columns.
- **Mobile width (hard rule):** the host injects 25px html/body padding — that is the ONLY horizontal inset. At ≤640px, wrappers/sections get ZERO side padding; cards keep 16–20px internal only; the sky and starfield bleed edge-to-edge. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** brand bg, `--brand-contrast` text, 9px radius, 10px × 18px, 14px/600 Albert Sans, glow shadow `0 0 20px` brand @30%; hover brightens (`color-mix(in srgb, var(--brand) 85%, white)`) and lifts 1px.
- **Secondary button:** transparent frost (`--surface-2`), `--border` border, `--heading` text; hover border → brand @35% mix, faint brand glow.
- **Ghost button:** transparent, `--text-secondary`; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2px, `currentColor`, 7px gap. NEVER emoji.
- **Badges:** 999px pills, 11px/600, 4px × 10px, semantic tint bg (~12%) + semantic text + 6px `currentColor` dot via `::before`. Live/active states may pulse the dot (guarded by reduced-motion).
- **Tables:** uppercase 10–11px muted headers on `--surface-2`; rows 13.5–14px, 12–14px vertical padding, hairline `--border` dividers only (no zebra, no verticals); hover `--surface-2`; numerics right-aligned tabular mono; primary line `--heading` 500 + muted 12px mono sub-line.
- **Forms:** 11px/600 uppercase labels above; inputs on `--surface-2`, `--border` border, 9px radius, 10px × 12px; placeholder `--text-muted`; focus = brand border + `0 0 0 3px` brand @18% ring. Selects: `appearance:none` + data-URI SVG chevron in a text-secondary stroke.
- **KPI tiles:** micro-label + 32px tinted icon chip top row; 28–34px Unbounded 400 numeral (tabular, optional soft glow); semantic delta pill + muted caption below.
- **Progress bars:** 8px, `--surface-2` track, brand→accent gradient fill, 999px; name left / mono value right above.
- **Activity timeline:** 30px icon-in-tinted-circle + rail of hairline `--border`; entity names bolded `--heading`; 12px mono muted timestamps.
- **Empty states:** dashed `--border` well on `--surface-2`: muted SVG icon, one-line Unbounded heading, one sentence, secondary button. Never leave a bare gap where data is missing.

## Iconography & charts
- Icons: inline SVG only, 14–20px, `stroke="currentColor"`, stroke-width 2, round caps/joins. They inherit their chip's tint color — never hardcode icon hex.
- Chips: 30–32px, `--radius-sm` (or 999px in timelines), tint background at ~12%, icon in the matching solid semantic/brand color.
- Charts/sparklines: series 1 = `--brand`, series 2 = `--accent`, then semantics; gridlines `--border`; labels `--text-muted` mono; area fills = the series color at ≤15% opacity. No purple/pink series colors under any circumstances.

## Accessibility
- Keep body copy at `--text` on `--surface`/`--surface-2` — both pass WCAG AA. `--text-muted` is for hints/timestamps only, never sentences.
- Focus rings always visible: brand ring `0 0 0 3px` @ ~20% + 1.5px brand line. Interactive targets ≥ 40px tall on touch.
- Glow (`text-shadow`/`box-shadow`) is decoration — never the only signifier of state; pair it with color + text.

## Motion — the living light
- Aurora ribbons: 2–3 fixed, blurred gradient layers animated with LONG loops (20–40s), `transform`/`opacity` only, `ease-in-out`, infinite alternate. Slow enough that motion is felt, not watched.
- UI motion: hovers 150–200ms; entrances 400–500ms fade + 10px rise, staggered 50–70ms; easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- **`@media (prefers-reduced-motion: reduce)` kills EVERY animation** — ribbons freeze into a static gradient sky (still beautiful), entrances snap visible, pulses stop. No exceptions.
- Always-visible `:focus-visible` rings (brand glow).

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever. Green/cyan aurora only.
2. No emoji as icons — inline stroke SVG (`currentColor`) only.
3. No text placed directly on the animated sky — solid surface behind all copy.
4. No Unbounded at weight 700+, below 12px, or for body paragraphs.
5. No fast/looping UI animation; sky drift is 20s+ and reduced-motion collapses it to static.
6. No heavy white glassmorphism — frost surfaces stay dark (`--surface` family), blur ≤ 10px.
7. No external scripts, CDN CSS, or images — fonts `@import` is the only external ref.
8. No body/wrapper side padding at ≤640px — the host's 25px is the only inset.
9. No neon-on-neon: brand/accent never as long-copy text color, only headings' accents, numerals, and chrome.
10. No zebra stripes, vertical rules, or boxed table cells.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation, `:root` tokens, sky/starfield layers and reduced-motion guards; restyle/replace the content for your task. Route every color through the `--token` variables — never bolt another theme's palette on top.
