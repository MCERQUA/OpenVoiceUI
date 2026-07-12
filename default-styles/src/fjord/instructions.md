# Fjord — Scandinavian Minimal Light

## Personality
Fjord is Nordic design-studio restraint: Bang & Olufsen's precision, Kvadrat's textile calm, Danish-modern furniture energy. Cool near-white air (`--bg`), deep charcoal-slate type, hairline cool-gray borders, and almost no shadow — hierarchy is built from type weight, spacing, and negative space, never from decoration. One muted glacial petrol (`--brand`) and one birch-evergreen (`--accent`) are the only voices above the neutrals, and both speak quietly.

## Typography
- **Faces:** headings and KPI numerals use `--font-display` (Outfit); body, labels,
  and UI use `--font-body` (Karla); numbers-in-tables, IDs, and code use
  `--font-mono` (Fira Code) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 24–26px / 600 (never 700 — Fjord headings are medium-weight; air does the work)
  - Section heading: 15–16px / 600
  - Card title: 13.5–14px / 600
  - Body: 14px / 400 · Secondary: 13px · Caption/label: 11px
- **Letter-spacing:** large headings −0.02em; micro-labels and table column headers
  +0.08em, `text-transform: uppercase`, 11px/600, colored `--text-muted`.
- **Casing:** sentence case everywhere; uppercase lives ONLY at 11px micro-label size.
- **Color:** headings `--heading`, body `--text`, supporting copy `--text-secondary`,
  hints/timestamps `--text-muted`. Declare `h1` and `a` colors explicitly —
  never rely on inheritance (the serve-time injector assumes explicit colors).
- **Line-height:** 1.55 paragraphs, 1.2 headings, 1.05 KPI numerals.

## Color usage
- `--brand` (#35617a, glacial petrol) is deliberately muted — primary buttons, links,
  focus rings, active states, progress fills. It should read as "grayed blue slate",
  never vivid. Do NOT substitute vivid blues (#2563eb / #3b82f6 belong to other presets).
- `--accent` (#2f6b4f, birch evergreen) is the single counterpoint: positive-leaning
  highlights, a featured card's 2px top rule, chart series #2. Never a second button color.
- Semantic colors carry MEANING only: `--success` complete/paid, `--warning`
  pending/at-risk, `--danger` overdue/failed. Pair each with its ~10%-opacity
  tint background (the rgba tint variables defined in `:root`).
- Text on `--brand` is always `--brand-contrast` (white).
- A Fjord page is ~96% neutral; if a screen feels colorful, it is wrong.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` — the shadow token is a
  whisper (1px/2px); the hairline border does the separation. Padding 20px.
- **Inset areas** (table header rows, form wells, code): `--surface-2`,
  radius `--radius-sm`, no shadow.
- **Hover:** border darkens to `--scrollbar`, background may shift to `--surface-2`.
  NO lift transforms on cards — Fjord surfaces are flat and stay flat;
  buttons may translate 0.5px on press only.
- Radii: `--radius` (6px) cards/inputs, `--radius-sm` (4px) buttons/chips,
  `999px` pills. Nothing rounder — soft, bubbly corners are not Fjord.
- No gradients, no blur/glass, ever. Maximum flourish: a 2px `--accent` or
  `--brand` top rule on one featured card.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64. Precision is the
  aesthetic — off-scale values read as sloppy.
- Page: max-width 1080px, centered. Section gaps 40px (Fjord breathes wider
  than most styles); card grid gaps 16px.
- Grids: KPI row `repeat(auto-fit, minmax(210px, 1fr))`; card grids
  `minmax(260px, 1fr)`; any grid column holding a table uses `minmax(0, 1fr)`.
- When in doubt, add whitespace; never remove it to fit content.
- **Mobile width (hard rule):** the host injects a 25px safe-area padding on
  html/body — that is the ONLY horizontal inset on phones. At ≤640px, page
  wrappers and sections get ZERO side padding; cards keep only their internal
  16–20px. Text spans the full remaining width; decorative backgrounds may
  bleed edge-to-edge. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, white text, `--radius-sm`, 9px × 16px padding,
  14px/600; hover darkens ~10% via `color-mix(in srgb, var(--brand) 88%, var(--heading))`.
  No lift, no glow, no shadow.
- **Secondary button:** `--surface` bg, `--border` border, `--heading` text;
  hover bg `--surface-2`, border `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary` text; hover bg `--surface-2`,
  text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 1.75–2px, `currentColor`, 7px gap.
- **Badges:** pills (999px), 11px/600, 3–4px × 10px padding, semantic-tint bg +
  semantic-color text, leading 6px `currentColor` dot via `::before`.
- **Tables:** uppercase 11px `--text-muted` headers on `--surface-2`; rows 13.5px
  with 12px vertical padding; hairline `--border` horizontal dividers only —
  no verticals, no zebra; row hover `--surface-2`; numeric columns right-aligned
  in tabular `--font-mono`; each row gets a `--heading`/500 primary line plus a
  muted 12px sub-line (ID · spec).
- **Forms:** 11px uppercase micro-labels above controls; inputs `--surface` bg,
  `--border` border, `--radius-sm`, 9px × 12px padding; placeholder `--text-muted`;
  focus = `--brand` border + 3px `--focus-ring`. Selects get `appearance: none`
  plus an inline-SVG data-URI chevron.
- **KPI tiles:** uppercase micro-label on top, 28px Outfit `--heading` value
  (tabular), then a small semantic delta + muted comparison caption. Optional
  28px tinted icon chip at `--radius-sm`.
- **Progress bars:** 6px tall, `--surface-2` track, `--brand` (or semantic) fill,
  999px radius; name left / mono fraction right above the track.
- **Activity/timeline:** 28px icon-in-tinted-SQUARE (`--radius-sm`, not circles)
  + text rows; bold entity names in-sentence; 12px muted timestamp below;
  hairline dividers between items.
- **Empty states:** dashed `--border` well on `--surface-2`: muted inline-SVG
  icon, one-line heading, one supporting sentence, a secondary button.

## Motion
- Understated: 120–150ms hovers, 300–400ms entrances;
  easing `cubic-bezier(0.25, 0.6, 0.3, 1)`.
- Entrance: fade + rise 6px max, staggered ~50ms. Animate opacity/transform ONLY.
  Hovers change color/border, never position.
- Wrap ALL animation in `@media (prefers-reduced-motion: reduce)` → none.
  Always provide visible `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No vivid blues (#2563eb, #3b82f6 family) — Fjord's petrol must stay muted and grayed.
3. No gradients, glassmorphism, blur, or layered/heavy shadows.
4. No card hover-lift transforms — surfaces are flat and stay flat.
5. No emoji as UI icons — inline SVG (15–20px, 1.75–2px stroke, currentColor) only.
6. No radii above 8px except 999px pills; no bubbly, friendly rounding.
7. No bold-700 headings or all-caps headlines; uppercase is 11px micro-labels only.
8. No zebra stripes, vertical rules, or boxed table cells.
9. No off-scale spacing — every gap sits on the 4/8px rhythm.
10. No external scripts, CDN CSS, or images — self-contained HTML; the Google
    Fonts `@import` is the only permitted external reference.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`.
Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for
your task — never bolt the old dark theme back on, and route every color through
the `--token` variables.
