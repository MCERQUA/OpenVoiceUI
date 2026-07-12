# Verdant — Dark Botanical Luxe

## Personality
Verdant is a members-only conservatory bar after dark: deep green-black rooms, brass rails catching low light, pressed ferns behind glass. Surfaces are green-tinted near-black (never blue-tinted), text is warm ivory, and the only jewelry is a restrained emerald and a fine antique-gold hairline. It whispers wealth — hierarchy comes from serif display type, generous air, and candle-soft depth, while tables and KPIs stay crisp, modern, and effortlessly legible.

## Typography
- **Faces:** headings and display numerals use `--font-display` (Cormorant Garamond, an elegant fine-detail serif); body, labels, and UI chrome use `--font-body` (Work Sans); IDs, figures in tables, and code use `--font-mono` (IBM Plex Mono) with `font-variant-numeric: tabular-nums`.
- **Weights:** Cormorant Garamond at 600 (semibold) for titles, 700 only for the page title; it is a light-boned face, so never use 400/500 below 20px for headings.
- **Scale:**
  - Page title: 32–40px / 600–700 Cormorant, line-height 1.1
  - Section heading: 22–24px / 600 Cormorant
  - Card title: 17–19px / 600 Cormorant
  - Body: 14px / 400 Work Sans · Secondary: 13px · Caption/label: 11–12px
- **Letter-spacing:** Cormorant headings ±0em (the serif carries itself — never tighten below −0.01em); micro-labels and table column headers +0.14em, `text-transform: uppercase`, 10.5–11px / 600 Work Sans, colored `--text-muted` or `--accent` for featured labels.
- **Casing:** sentence case for headings (serif all-caps is banned); uppercase lives only in wide-tracked micro-labels.
- **Color:** headings explicitly `--heading` (warm ivory), body `--text`, supporting copy `--text-secondary`, hints/timestamps `--text-muted`. Links are explicitly `--accent` (gold), underlined on hover only.
- **Line-height:** generous — 1.65–1.75 for paragraphs, 1.1–1.2 for display headings.

## Color usage
- The page is ~92% green-black neutrals. Two jewels only:
  - `--brand` (#10b981 emerald) — primary buttons, progress fills, active states, focus rings, positive KPI accents. Text on brand is always `--brand-contrast` (deep green-black), never white.
  - `--accent` (#c9973f antique brass) — hairline card borders, links, eyebrow labels, icon strokes on featured chips, the single flourish per section. Never a button fill; brass is trim, not furniture.
- Semantic colors carry MEANING only: `--success` bottled/complete/positive deltas; `--warning` resting/pending/at-risk; `--danger` holds/overdue/errors. Always on a ~12%-opacity tint backdrop (`color-mix(in srgb, var(--warning) 12%, transparent)`), never as large fills.
- Text contrast is non-negotiable: nothing dimmer than `--text-muted` for readable copy; data values in tables use `--text` or `--heading`.
- No blue anywhere — Verdant's cool notes come from green. No purple, pink, or magenta, ever.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border-gold); border-radius: var(--radius); box-shadow: var(--shadow);` where `--border-gold` is brass at ~26% opacity — the signature fine gold hairline. Padding 22–26px.
- **Structural dividers** (table row lines, list separators, section rules) use plain `--border` (green hairline), NOT gold — gold outlines cards; green divides content.
- **Inset/nested areas** (table headers, form wells, code): `--surface-2`, radius `--radius-sm`, no shadow, no gold.
- **Hover:** cards lift 1px with a slightly deeper shadow and the gold hairline warming to ~45% opacity, 180ms ease. Subtle — this is a drawing room, not an arcade.
- Radii: `--radius` (10px) cards/modals, `--radius-sm` (6px) buttons/inputs, `999px` pills.
- Depth comes from soft, deep, wide shadows (`--shadow`) on the near-black page — never from glassmorphism, blur, or gradients on surfaces. A faint radial green glow behind the header is the maximum atmosphere.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64.
- Page: max-width 1120px, centered. Section gaps 40–48px (more air than a typical dashboard); card grid gaps 18–20px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(280px, 1fr)`; any grid column holding a min-width table MUST use `minmax(0, 1fr)`.
- **Mobile width (hard rule):** the host injects a 25px safe-area on html/body — the ONLY horizontal inset on phones. At ≤640px wrappers and sections get ZERO side padding; cards keep only 16–20px internal padding. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, `--brand-contrast` text, 6px radius, 10px × 18px padding, 13.5px/600 Work Sans; hover brightens ~8% (`color-mix(in srgb, var(--brand) 88%, var(--heading))`) and lifts 1px.
- **Secondary button:** transparent bg, 1px `--accent`-tinted border (~55% opacity), `--heading` text; hover bg gold tint ~10%, border to full `--accent`.
- **Ghost button:** transparent, `--text-secondary` text; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2px, `currentColor`, 8px gap.
- **Badges:** pills (999px), 11px/600, 4px × 11px padding, semantic tint bg + semantic text, leading 6px `currentColor` dot via `::before`.
- **Tables:** uppercase 10.5px wide-tracked muted headers on `--surface-2`; rows 13.5–14px, 13px vertical padding; hairline `--border` dividers only (no verticals, no zebra); hover `--surface-2`; numerics right-aligned tabular mono; primary line `--heading`/500 with a muted 12px sub-line.
- **Forms:** 12px/600 `--heading` labels above; inputs `--surface-2` bg, `--border` border, 6px radius, 10px × 12px padding; placeholder `--text-muted`; focus = `--brand` border + 3px emerald ring. Selects: `appearance:none` + inline-SVG data-URI chevron.
- **KPI tiles:** wide-tracked micro-label + 34px gold-tinted icon chip on top, then a 34–38px Cormorant `--heading` value (tabular numerals), then a semantic pill delta + muted caption.
- **Progress bars:** 8px, `--surface-2` track, emerald (or semantic) fill, 999px radius; name left, mono fraction right.
- **Activity timeline:** 32px icon-in-tinted-circle + text rows; entity names bolded `--heading`; 12px muted timestamp; hairline `--border` dividers.
- **Empty states:** dashed `--border` well on `--surface-2`: muted inline-SVG icon, one Cormorant line, one sentence, a secondary button.
- **Section rules:** close a section quietly with a 1px brass fade
  (`linear-gradient(90deg, var(--border-gold-strong), transparent 70%)`) — the only
  gradient permitted anywhere.

## Iconography
- Inline SVG only, 15–17px in chrome, stroke 1.8–2px, `stroke="currentColor"`,
  `fill="none"`, round caps/joins — botanical-adjacent line icons (leaf, flask,
  glass, calendar) suit the house; anything filled or duotone does not.
- Icon chips: 32–34px rounded squares (`--radius-sm`) or circles, gold tint
  (`--accent-tint` + `--accent`) by default, emerald tint for positive/brand moments.
- Never load icon fonts or image sprites; never use emoji as icons.

## Atmosphere
- The page background may carry two faint radial glows (emerald top-left, brass
  top-right, ≤6% opacity, `transparent` by 60%) to suggest low conservatory light.
  Nothing else glows; cards stay matte.
- Density stays low: one table, one form, and generous 40px+ section gaps —
  Verdant pages should feel curated, not crowded.

## Motion
- Durations 180ms (hovers) to 500ms (entrances); easing `cubic-bezier(0.22, 1, 0.36, 1)` — a slow, gracious settle.
- Entrance: sections fade + rise 10px, staggered 60–80ms. Animate opacity/transform ONLY.
- Wrap every animation in `@media (prefers-reduced-motion: reduce)` → none. Always show `:focus-visible` rings (emerald).

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No blue-tinted grays or blue accents — Verdant's darks are green-tinted, its metals brass.
3. No white (#ffffff) text or fills — ivory (`--heading`) is the ceiling; pure white glares here.
4. No gold button fills or large gold areas — brass is hairline trim and small labels only.
5. No glassmorphism, backdrop blur, or gradient-filled cards; depth = shadow + hairline.
6. No all-caps serif headings; uppercase only for tiny wide-tracked sans micro-labels.
7. No emoji as UI icons — inline SVG (stroke, currentColor) only.
8. No Cormorant for body copy, table cells, or buttons — the serif is display-only.
9. No zebra striping, vertical rules, or heavy cell boxes in tables.
10. No external scripts, CDN CSS, or images — self-contained HTML, fonts via the one @import.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt another theme back on, and route every color through the `--token` variables.
