# Helios — Modern-Optimist Light

## Personality
Helios is bright, confident energy: the boldness of a fintech launch page, Headspace's certainty without the softness, Monocle's summer issue. Everything sits on a warm bright-white page with white cards carrying thick 2px warm borders, chunky rounded corners, and one unmistakable marigold-gold brand paired against a deep warm charcoal anchor. It gets its energy from color, weight, and scale — never from clutter, gradients, or decoration.

## Typography
- **Faces:** headings and big numerals use `--font-display` (Bricolage Grotesque, 600–800 weight);
  body, labels, and UI use `--font-body` (Figtree); numbers, IDs, and code use `--font-mono`
  (DM Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 28–34px / 800 — Helios goes BIGGER than other styles; the display face earns it
  - Section heading: 18–20px / 700
  - Card title: 15–16px / 700
  - Body: 14px / 400 · Secondary: 13px · Caption/label: 11–12px
- **Letter-spacing:** display type is always tight — headings −0.03em, KPI numerals −0.02em;
  micro-labels +0.08em, `text-transform: uppercase`, 11px/700, colored `--text-muted`.
- **Casing:** sentence case everywhere except uppercase micro-labels. Never all-caps headings.
- **Color:** headings `--heading` (near-black warm charcoal #1c1917), body `--text`,
  supporting copy `--text-secondary`, hints/timestamps `--text-muted`. Links use `--brand-deep`
  (#a16207) — raw gold is a FILL color, never running text.
- **Line-height:** 1.55–1.6 paragraphs, 1.15 headings, 1.05 for big KPI numerals.

## Color usage — the contrast law
- `--brand` (#eab308 marigold) is used BOLDLY but only as a fill: primary buttons, progress
  fills, the sun-dot eyebrow, icon chips, the featured card's top bar. **Gold NEVER carries
  white text.** Text on any gold fill is ALWAYS `--brand-contrast` (#1c1917). No exceptions.
- `--brand-deep` (#a16207) is the readable member of the gold family: links, gold-tinted
  badge text, delta text on gold tints. Use it wherever gold must function as text.
- `--anchor` (#292524 deep warm charcoal) is the SECOND anchor color: secondary buttons are
  solid charcoal with `--anchor-contrast` text — a dark fill, not an outline. Charcoal + gold
  side by side is the Helios signature.
- `--accent` (#d97706 amber) for secondary informational highlights only (chart series #2,
  info badges) — never a third button color.
- Semantic colors carry MEANING only: `--success` complete/positive, `--warning` at-risk/
  pending (orange — distinct from brand gold), `--danger` overdue/errors. Always on a
  ~12%-opacity tint background.
- A Helios page is ~85% warm neutral, ~10% gold/charcoal, ~5% semantic.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 2px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` — the 2px border is the Helios
  signature; hairlines read as a different (weaker) style. Padding 20–24px.
- **Key emphasis:** ONE featured card per page may upgrade its border to
  `2px solid var(--brand)` or carry a 4px gold top bar. Never both, never more than one.
- **Inset/nested areas** (table heads, form wells): `--surface-2` (warm sand), radius
  `--radius-sm`, no shadow, no border.
- **Hover lift:** `translateY(-2px)` + deeper shadow, 160ms — chunkier than SaaS-subtle,
  matching the confident scale.
- Radii: `--radius` (16px) cards, `--radius-sm` (10px) inputs/small chips, `999px` for ALL
  buttons and badges — buttons are full pills in Helios.
- No gradients, no glass/blur. Warmth comes from the token palette, not effects.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64.
- Page: max-width 1120px, centered. Section gaps 36px; grid gaps 16–20px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`;
  any grid column holding a table uses `minmax(0, …)` so the table can shrink.
- **Mobile width (hard rule):** the host wrapper injects a 25px safe-area padding on
  html/body — that is the ONLY horizontal inset on phones. At ≤640px, page wrappers and
  sections get ZERO side padding; cards keep only their internal 16–20px. Text spans the
  full remaining width. Never stack padded containers on mobile.

## Component recipes
- **Primary button:** gold fill, `--brand-contrast` (dark) text, 999px pill, 11–12px × 20px
  padding, 14px/700; hover deepens toward amber (`color-mix(in srgb, var(--brand) 78%,
  var(--accent))`) and lifts 2px. Never white text on gold.
- **Secondary button:** solid `--anchor` charcoal fill, `--anchor-contrast` text, 999px pill;
  hover lightens toward `--heading`'s warm black and lifts 2px.
- **Ghost button:** transparent, `--text-secondary` text, 999px pill; hover bg `--surface-2`,
  text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2–2.5px, `currentColor`, 8px gap.
- **Badges:** pills, 11px/700, 4px × 11px padding, semantic tint bg + semantic text, leading
  6px `currentColor` dot via `::before`. Gold-family badges use `--brand-deep` text on a
  gold tint — never raw `--brand` as text.
- **Tables:** uppercase 11px/700 muted headers on `--surface-2`; 2px `--border` line under
  the header row, 1px dividers between body rows; rows 13.5–14px, 13px vertical padding;
  hover `--surface-2`; numeric columns right-aligned tabular mono; primary line
  `--heading`/600 with a muted 12px sub-line.
- **Forms:** 12px/700 `--heading` labels; inputs white, 2px `--border`, `--radius-sm`,
  10px × 14px padding; hover darkens border to `--scrollbar`; focus = `--brand` border +
  3px gold glow ring. Selects: `appearance: none` + inline-SVG data-URI chevron.
- **KPI tiles:** uppercase micro-label + 34px tinted icon chip on the top row, then a
  30–34px display-font tabular value in `--heading`, then a pill delta chip + muted caption.
- **Progress bars:** 10px tall (chunkier than SaaS), `--surface-2` track, gold or semantic
  fill, 999px radius, name left / mono fraction right.
- **Activity lists:** 32px icon-in-tinted-circle rows; bold entity names; 12px muted
  timestamp; 1px dividers.
- **Empty states:** dashed 2px `--border` well on `--surface-2`: muted inline-SVG icon,
  one-line heading, one sentence, a ghost button.

## Motion
- Durations 160ms (hovers) to 400ms (entrances); easing `cubic-bezier(0.22, 1, 0.36, 1)`.
- Entrance: fade + rise 12px, staggered 50–70ms. Animate opacity/transform ONLY.
- Wrap every animation in `@media (prefers-reduced-motion: reduce)` → none.
  Always visible `:focus-visible` rings (gold glow).

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. NEVER white text on gold — gold fills always pair with `--brand-contrast` (#1c1917).
3. No raw `--brand` gold as running text or links — use `--brand-deep` for readable gold.
4. No gradients, glassmorphism, or blur effects — flat confident color only.
5. No hairline 1px card borders — Helios cards are 2px; 1px is reserved for row dividers.
6. No dark sections or dark-mode panels; the charcoal anchor is for buttons/chips, not backgrounds.
7. No emoji as UI icons — inline SVG (15–20px, 2–2.5px stroke, currentColor) only.
8. No all-caps headings or buttons; uppercase lives only in 11px micro-labels.
9. No square/sharp buttons — buttons and badges are always 999px pills.
10. No external scripts, CDN CSS, or images — self-contained HTML with inline SVG.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
