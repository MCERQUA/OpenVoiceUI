# Velocity — Motorsport / Pit-Wall Telemetry Dark

## Personality
Velocity is an F1 pit wall at night: carbon-black surfaces, one vicious racing red, silver
detailing, and lap-timer typography. Everything leans forward — italic Saira headings, slanted
cut corners, checkered-flag dividers — like the page itself is doing 300 km/h. The flash lives in
headers, badges, dividers and hover states; tables, forms and body copy stay ruthlessly crisp and
readable, because a race team that can't read its own telemetry loses.

## Typography
- **Faces:** headings use `--font-display` (Saira) in **italic** — `font-style: italic` on every
  heading, paired with tight letter-spacing (−0.01em to −0.02em). Body and UI use `--font-body`
  (Titillium Web). All numbers, lap times, IDs and gaps use `--font-mono` (Chivo Mono) with
  `font-variant-numeric: tabular-nums`.
- **Scale:** page title 26–30px/800 italic uppercase; section heading 16–18px/700 italic
  uppercase; card title 14–15px/700; body 14px/400; secondary 13px; micro-label 11px/700.
- **Casing:** headings and micro-labels are UPPERCASE (Velocity inverts the usual rule — racing
  liveries shout). Body copy and table cell text stay sentence case. Micro-labels get
  +0.10–0.14em letter-spacing in mono, colored `--text-muted`.
- **Color:** headings `--heading` (near-white); body `--text`; supporting `--text-secondary`;
  hints/timestamps `--text-muted`. Big KPI numerals: `--heading` mono, 30–36px/700, line-height 1.05.
- Never set body copy in italic — italics are for display type only.

## Color usage
- `--brand` (#e10600, F1 red) is the engine: primary buttons, active states, progress fills,
  P1 badges, the 3px racing stripe on featured cards, header speed-lines. Velocity owns vivid
  red as a full brand color — use it confidently in chrome, never as long-form text on dark.
- `--brand-bright` (derived, ~#ff5347) is the LINK/text red: anchors, red inline values, focus
  accents. Raw #e10600 fails contrast as small text on carbon — always step up for text.
- `--accent` (silver #c7ccd6) is the secondary metal: secondary-button borders/text, silver
  micro-details, P2 badges, chart series 2. Never fill a button with accent.
- Semantic colors carry MEANING only: `--success` on-track/complete/positive delta, `--warning`
  pit/pending, `--danger` DNF/errors — always on a ~12%-opacity tint chip, never as plain
  paragraph text.
- Text on `--brand` is always `--brand-contrast` (white). Checker motifs use only
  `--heading` × `--bg` squares (via a low-opacity light var), never colored checkers.

## Surfaces & depth
- Page background: `--bg` with a carbon-fiber weave — two offset 1px `radial-gradient` dot
  layers at a 6px tile, opacity ≤0.03. Subtle: visible up close, invisible in thumbnails.
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` plus the angular cut corner:
  `clip-path: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 0 100%)`.
  Featured cards add a 3px `--brand` left stripe (inset box-shadow).
- Inset areas (table heads, form wells): `--surface-2`, radius `--radius-sm`, no shadow.
- Radii are TIGHT: 4px cards, 2px buttons/inputs. Pills (999px) only for status badges/deltas.
- **Checkered strips** are the signature divider: an 8–12px band, `conic-gradient` checker of
  the light var over transparent at ~20px tiles, opacity ≤0.2, under the page header or between
  major sections. Max ONE full-width checker per screen region — it's a flag, not wallpaper.
- Speed-line gradients (90deg, `--brand` → transparent, ≤25% opacity, 1–2px tall) may streak
  header/hero backgrounds only — never behind table text.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1160px, centered.
- Section gaps 32px; KPI grid `repeat(auto-fit, minmax(220px, 1fr))` gap 14px; card grids
  `minmax(280px, 1fr)`; two-column zones `minmax(0, 1fr)` tracks.
- **Mobile width (hard rule):** the host wrapper injects 25px html/body padding — that is the
  ONLY horizontal inset on phones. At ≤640px page wrappers and sections get ZERO side padding;
  cards keep 16–20px internal padding only. Any grid that contains a table uses `minmax(0, 1fr)`
  tracks, and every table lives in an `overflow-x: auto` wrapper. Decorative checker strips may
  bleed edge-to-edge. Never stack padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, white text, 700 weight, uppercase, +0.06em tracking,
  parallelogram slant `clip-path: polygon(8px 0, 100% 0, calc(100% - 8px) 100%, 0 100%)`,
  padding 11px 22px; hover brightens (`color-mix(in srgb, var(--brand) 82%, white)`) and
  shifts 2px RIGHT (speed, not lift).
- **Secondary button:** transparent bg, 1px silver-tinted border, `--heading` text, same slant;
  hover fills `--surface-2`, border goes full `--accent`.
- **Ghost button:** transparent, `--text-secondary` text, no border/clip; hover text `--heading`,
  bg `--surface-2`.
- **Badges:** pill, 10.5–11px/700 uppercase mono, 3px × 10px padding, semantic tint bg +
  semantic text, leading 6px `currentColor` dot via `::before`.
- **Position chips (P1/P2/P3…):** ~30×26px slanted parallelogram, Saira italic 800; P1 =
  `--brand` bg/white; P2 = `--accent` bg/`--bg` text; P3+ = `--surface-2` bg, `--border`
  border, muted text, square corners.
- **Tables:** uppercase 11px mono `--text-muted` headers on `--surface-2`; rows 13.5–14px with
  12–14px vertical padding; hairline `--border` dividers only (no zebra, no verticals); row
  hover `--surface-2`; ALL timing/gap/number columns right-aligned tabular mono; the leader row
  may carry a 3px `--brand` left stripe; each driver row gets a `--heading` 600 name line plus
  a 12px mono muted sub-line (license · class).
- **Forms:** 11px/700 uppercase mono labels above; inputs `--surface-2` bg, `--border` border,
  2px radius, 10px 12px padding, `--heading` text; placeholder `--text-muted`; hover darkens
  border to `--scrollbar`; focus = `--brand` border + 3px red-tint ring. Selects get
  `appearance: none` + an inline-SVG silver chevron (data-URI).
- **KPI tiles:** uppercase mono micro-label + 32px slanted icon chip (brand/semantic tint) on
  the top row; then a 30–36px mono `--heading` value; then a delta pill + muted caption. One
  featured tile may take the red left stripe.
- **Progress bars:** 8px tall, `--surface-2` track, `--brand` (or semantic) fill with a subtle
  lighter gradient tip, 2px radius; name left, mono value right above the track.
- **Activity/timeline:** 30px slanted tinted icon chips + text rows; bold entity names in
  `--heading`; 12px mono muted timestamps below; hairline dividers between items.
- **Empty states:** dashed `--border` well on `--surface-2`: silver inline-SVG icon, one italic
  heading line, one supporting sentence, a ghost button.

## Motion
- Durations 140ms (hovers) to 400ms (entrances); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: elements slide in from the LEFT 14–18px with a fade (launching off the grid),
  staggered 40–60ms. Animate opacity/transform ONLY.
- Hovers translate on X (forward motion), max 2px; no vertical lift, no scale pops.
- EVERY animation and transition sits behind `@media (prefers-reduced-motion: reduce)` →
  disabled, opacity 1, transform none. Always show `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No light-mode sections — Velocity is carbon-dark only.
3. Never set paragraph or table text in raw `--brand` red — bright variant for links,
   semantic colors for status.
4. No italic body copy; italics belong to Saira display type only.
5. No emoji as UI icons — inline stroke/currentColor SVG (14–20px) only.
6. No checker overload: max one checker strip per header/section boundary.
7. No speed-line gradients behind tables, forms, or any long-form text.
8. No rounded-soft look — radii stay ≤4px except badge pills; no glassmorphism or blur.
9. No hover lifts or scale effects — motion is lateral (X-axis) and ≤2px.
10. No external scripts, CDN CSS, or images — self-contained HTML, fonts via the one `@import`.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its
`<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt
another theme back on, and route every color through the `--token` variables.
