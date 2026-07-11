# Arcade — Retro 8-Bit Cabinet Dark

## Personality
Arcade is an 80s high-score screen rebuilt as a working business dashboard: deep CRT black-blue glass, insert-coin red, pixel-yellow score glow, a faint scanline hum over everything. The flash lives strictly in the chrome — headings, badges, borders, score readouts — while body copy and table data stay clean, modern, and instantly readable. Think a barcade's back-office terminal: nostalgic cabinet energy, professional operator discipline.

## Typography
- **Faces:** `--font-display` ('Press Start 2P') for headings, micro-labels, and rank badges ONLY; `--font-body` ('Jost') for ALL body copy, table cells, buttons, and form controls; `--font-mono` ('VT323') for score/figure readouts at 22px+ where its thin strokes stay legible.
- **Press Start 2P is HUGE at any size — cap it hard:**
  - Page title: 20–24px / 400 (the face has one weight), `letter-spacing: 0.04em`, `line-height: 1.5`
  - Section heading: 13–15px, same spacing
  - Micro-labels / eyebrows / table column headers: 8–10px, `letter-spacing: 0.12em`, uppercase
  - NEVER use it for paragraphs, table body cells, buttons, or inputs.
- **Body (Jost):** 15px / 400 base, 1.6 line-height; secondary 13–14px; weights 500–600 for emphasis and row primaries.
- **Score numerals (VT323):** 26–40px in KPI tiles and progress fractions, `--accent` or `--heading` colored. Below 20px, switch to Jost with `font-variant-numeric: tabular-nums`.
- **Color:** headings `--heading`; body `--text`; supporting `--text-secondary`; hints `--text-muted`.

## Color usage
- `--brand` (#ef4444, insert-coin red): primary buttons, the blinking live badge, links, focus rings, active nav, danger-adjacent CTAs. The ONE saturated action color.
- `--accent` (#fbbf24, pixel yellow): score numerals, RANK/1ST badges, progress fills, coin/highlight icons, hover glints. Never on primary buttons.
- `--success` (#22c55e, 1-up green): positive deltas, ONLINE/paid states, level-up moments. `--warning` (#f59e0b) for pending/at-risk; `--danger` (#dc2626) for down/overdue.
- Semantic + accent chips always sit on a ~12%-opacity tint of themselves (`color-mix(in srgb, var(--success) 12%, transparent)`), never on raw color.
- Text on `--brand` is `--brand-contrast` (white). Long text is always `--text` on `--bg`/`--surface` — never yellow or red paragraphs.
- NO purple, pink, or magenta. Ever. The CRT blue lives only in the near-black base tones.

## Surfaces & depth (pixel construction)
- **Card recipe:** `background: var(--surface); border: 2px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` — the shadow's hard `0 3px 0` step IS the pixel look. Padding 18–20px.
- **Featured/hero cards:** add a 3px top border in `--brand` or `--accent` — one per view, maximum.
- **Inset wells** (table headers, form wells, progress tracks): `--surface-2`, radius `--radius-sm`, 1px `--border` border, no shadow.
- **Scanlines:** one fixed full-page overlay via `body::after` — `repeating-linear-gradient(0deg, rgba(0,0,0,0.14) 0 1px, transparent 1px 3px)`, `pointer-events: none`, opacity ≤ 0.5. Never scanline individual cards.
- Radii stay chunky-small: `--radius` 4px, `--radius-sm` 2px, pills 999px only for status badges. No large rounded corners — this is a cabinet, not a bubble.
- Hover: border brightens toward `--scrollbar`/`--accent` and shadow deepens one step; lift max 2px.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1120px, centered; section gaps 32px, card gaps 16px.
- KPI row: `repeat(auto-fit, minmax(220px, 1fr))`. Card grids: `minmax(260px, 1fr)`. Any grid track that holds a table: `minmax(0, 1fr)`.
- **Mobile width (hard rule):** the host injects 25px html/body padding — that is the ONLY horizontal inset. At ≤640px, wrappers and sections get ZERO side padding; cards keep 16px internal padding; tables scroll inside `overflow-x: auto` wells.

## Component recipes
- **Primary button:** `--brand` bg, white text, Jost 15px/600, 10px × 18px padding, radius `--radius-sm`, hard `0 3px 0` darker-red shadow; `:active` pushes down 2px and flattens the shadow (button-press feel).
- **Secondary button:** `--surface-2` bg, 2px `--border` border, `--heading` text; hover border `--accent`, text stays.
- **Ghost button:** transparent, `--text-secondary`; hover `--surface-2` bg + `--heading` text.
- **Status badges:** pills, Jost 12px/600 uppercase, tinted bg + semantic text, leading 6px square (not dot — `::before` with no radius) in `currentColor`.
- **Rank badges (high-score table):** 22px square chip, Press Start 2P 9px, `--accent` tint for 1st, `--surface-2` for the rest.
- **Blinking live badge:** "● LIVE"-style chip with a `steps(2, end)` 1.2s opacity blink on the square marker only — text never blinks; kill the blink under reduced-motion.
- **Tables (high-score treatment):** Press Start 2P 8–9px uppercase `--text-muted` column headers on `--surface-2`; Jost 14px rows, 12–14px vertical padding, hairline `--border` dividers, row hover `--surface-2`; numeric columns right-aligned tabular; primary line `--heading` 500 with muted 12–13px sub-line.
- **Forms:** Jost 12px/600 uppercase labels; inputs `--surface-2` bg, 2px `--border`, radius `--radius-sm`, 10px × 12px padding; focus = `--brand` border + soft red ring. Selects: `appearance: none` + inline-SVG data-URI chevron.
- **KPI tiles:** Press Start 2P 8px uppercase micro-label + tinted 32px icon chip, then a VT323 34–40px value, then a semantic delta chip + muted caption.
- **Progress bars (XP bars):** 14px tall `--surface-2` track with 1px border; `--accent` (or semantic) fill; segmented notches via a `repeating-linear-gradient` mask of transparent 1–2px gaps every 10–12px; VT323 fraction right-aligned above.
- **Activity timeline:** 30px tinted square icon chips + Jost text; bold entity names; 12px muted timestamps; hairline dividers.
- **Empty states:** dashed 2px `--border` well on `--surface-2`, muted inline-SVG icon, one Press Start 2P 11px line ("NO PLAYERS YET"), one Jost sentence, a secondary button.

## Iconography
- Inline SVG only, 15–20px, `stroke="currentColor"`, stroke-width 2, `stroke-linecap: square`
  (square caps read pixel-crisp — never rounded caps in this style).
- Icons live inside tinted square chips (`--radius-sm`, semantic tint bg + semantic color)
  in KPI tiles, timelines, and buttons. Never freestanding colored icons in body copy.
- Marker squares (eyebrow, badges, live chip) are plain `background: currentColor` divs/
  pseudo-elements with NO border-radius — square pixels, not dots.
- Select chevrons: inline-SVG data URI with square linecaps, `appearance: none`.

## Motion
- Durations 150ms (hover) to 400ms (entrance); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
  The blink is the one exception: `steps(2, end)`, 1.2s — a hard CRT flick, never a soft fade.
- Entrance: cards fade + rise 8–10px, staggered 50ms per card. Animate opacity/transform only —
  never width/height/box-shadow.
- Button presses are physical: primary buttons drop 2px on `:active` and flatten their
  hard shadow step, like a cabinet button being pushed.
- Wrap EVERY animation in `@media (prefers-reduced-motion: reduce)` → none (blink included).
  Keep visible `:focus-visible` rings always.

## DON'Ts
1. NO purple, pink, or magenta anywhere — the retro palette is red/yellow/green on CRT black-blue only.
2. Never set body copy, table cells, buttons, or inputs in Press Start 2P — headings, labels, and badges only, capped at 24px.
3. Never use VT323 below ~20px — it turns to dust; drop to Jost tabular numerals instead.
4. No unguarded blinking or flicker — every blink/glow animation dies under `prefers-reduced-motion`.
5. No scanlines on cards or text blocks — one subtle full-page overlay, opacity ≤ 0.5, is the maximum.
6. No neon glow soup: at most one `--accent` glow accent per card; body text never has text-shadow.
7. No big radii, glassmorphism, or blur — corners stay 0–4px, surfaces stay flat pixel panels.
8. No emoji as UI icons — inline SVG (16–20px, 1.5–2px stroke, `currentColor`) only.
9. No red/yellow paragraphs — saturated colors are for chrome, badges, numerals, and actions, never running text.
10. No external scripts, CDN CSS, or images — self-contained HTML; the Google Fonts `@import` is the only external reference.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt a different theme back on, and route every color through the `--token` variables.
