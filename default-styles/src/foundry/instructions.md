# Foundry — Dark Copper Industrial Luxe

## Personality
Foundry is the warmth of hot metal in a dark workshop: a whiskey distillery's tasting room, a premium leather-goods atelier, Tom Dixon pendant lamps over waxed timber. Surfaces are warm near-black BROWN (never blue-black), text is warm parchment, and one molten-copper family carries every highlight, with ember-orange reserved for glow moments. Underneath the atmosphere it is a disciplined, professional dashboard — condensed uppercase display headings, tight data tables, honest utility.

## Typography
- **Faces:** headings and KPI numerals use `--font-display` (Big Shoulders Display — condensed);
  body, labels, and UI use `--font-body` (Assistant); IDs, quantities, and code use
  `--font-mono` (Overpass Mono) with `font-variant-numeric: tabular-nums`.
- **Display type is UPPERCASE and condensed** — this is Foundry's signature. h1/h2 are
  `text-transform: uppercase` with POSITIVE letter-spacing (+0.03em to +0.05em); the
  condensed face needs air between letters, never negative tracking.
- **Scale:**
  - Page title (h1): 30–38px / 800, uppercase, `--heading`
  - Section heading (h2): 19–21px / 700, uppercase, +0.04em
  - Card title (h3): 15–16px / 700, uppercase, +0.05em
  - Body: 14px / 400 · Secondary: 13px · Micro-label: 11px / 600 uppercase +0.1em
- KPI values: 32–38px display face / 700–800, `--heading`, line-height 1.05.
- Body copy stays sentence case in Assistant — only display headings and micro-labels go uppercase.
- Color: headings `--heading` (warm ivory), body `--text`, supporting `--text-secondary`,
  hints/timestamps `--text-muted`. Never cool grays — every neutral is brown-tinted.

## Color usage
- `--brand` (#c2703d molten copper) is the working highlight: primary buttons, links,
  active states, focus rings, progress fills, the copper rule under section headings.
- `--accent` (#e08e45 ember) is the GLOW color only: hover glows, the hot end of copper
  gradients, delta arrows, one featured stat. Never a second button color.
- Text on `--brand` is `--brand-contrast` (near-black brown) — copper is a midtone; dark
  text on it reads like a stamped metal plate and passes contrast. Never white-on-copper.
- Semantic colors carry meaning only: `--success` aged/complete, `--warning` resting/at-risk,
  `--danger` overdue/fault. Pair each with a ~12%-opacity tint background
  (`color-mix(in srgb, var(--success) 12%, transparent)`).
- Metallic warmth comes from SUBTLE CSS gradients only (copper→ember at low contrast on
  buttons/logo/fills, faint radial ember washes on the page) — no images, no textures.
- Long body text is always `--text` on `--surface`/`--bg` (contrast ≥ 10:1). Never set
  paragraphs in copper.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` padding 20–24px. The 1px warm
  border is the "riveted seam" — always present, never removed in favor of shadow alone.
- **Inset areas** (table headers, form wells, code): `--surface-2`, radius `--radius-sm`, no shadow.
- **Ember hover:** cards/buttons that lift gain a faint ember glow —
  `box-shadow: var(--shadow), 0 0 20px rgba(224, 142, 69, 0.18)` + `translateY(-2px)`, 180ms.
- A featured card may take a 3px copper top border (`border-top: 3px solid var(--brand)`).
- Radii are MEDIUM: `--radius` (10px) cards, `--radius-sm` (6px) buttons/inputs/badges,
  999px only for pills and progress tracks. Nothing sharper than 6px, nothing over 12px.
- No glassmorphism, no backdrop blur — Foundry surfaces are solid metal and timber.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1160px, centered.
- Section gaps 32px; grid gaps 16–20px. KPI row `repeat(auto-fit, minmax(220px, 1fr))`;
  card grids `minmax(280px, 1fr)`.
- **Any grid column that contains a table or other min-width content uses `minmax(0, 1fr)`**
  so it can shrink; tables scroll inside `overflow-x: auto` wrappers.
- **Mobile width (hard rule):** the host injects 25px html/body padding — that is the ONLY
  horizontal inset. At ≤640px, page wrappers and sections get ZERO side padding; cards keep
  only their internal 16–20px. Never nest padded containers on mobile; decorative washes
  may bleed edge-to-edge.

## Component recipes
- **Primary button:** copper gradient bg (`linear-gradient(150deg, var(--brand), var(--accent))`
  kept subtle), `--brand-contrast` text, 6px radius, 9–10px × 18px padding, 13px/700
  UPPERCASE +0.06em (buttons follow the display voice); hover brightens ~8% + ember glow + 1px lift.
- **Secondary button:** transparent-to-`--surface-2` bg, `--border` border, `--text` label;
  hover: border warms to `--brand`, text to `--heading`.
- **Ghost button:** transparent, `--text-secondary` text; hover bg `--surface-2`, text `--heading`.
- **Button/UI icons:** inline SVG, 15–16px, stroke 2px, `currentColor`, 7px gap. Never emoji.
- **Badges:** pills, 11px/600 uppercase +0.06em, 4px × 10px padding, semantic tint bg +
  semantic text + leading 6px `currentColor` dot via `::before`.
- **Tables:** `--surface-2` header band with 11px uppercase `--text-muted` +0.1em column
  labels; rows 13.5–14px, 12–14px vertical padding, hairline `--border` dividers only
  (no verticals, no zebra); row hover `--surface-2`; numeric columns right-aligned in mono;
  each row gets a `--heading` 600 primary line with a muted 12px mono sub-line.
- **Forms:** 11px uppercase `--text-secondary` labels above; inputs on `--bg`-dark wells
  (`--surface-2`), `--border` border, 6px radius, 10px × 12px padding; placeholder
  `--text-muted`; focus = copper border + 3px `rgba(194,112,61,0.25)` ring. Selects use an
  inline-SVG data-URI chevron with `appearance: none`.
- **KPI tiles:** micro-label + 32px copper-tinted icon chip on top, condensed display value,
  then a semantic pill delta + muted caption.
- **Progress bars:** 8px, `--surface-2` track, copper→ember gradient fill, 999px radius,
  name left / mono fraction right.
- **Activity timeline:** 30px tinted icon circles joined by a 1px `--border` vertical rail;
  bold entity names in the sentence, 12px muted mono timestamp below.
- **Empty states:** dashed `--border` well on `--surface-2`: muted SVG icon, one uppercase
  display line, one sentence, a secondary button.

## Motion
- Durations 160ms (hovers) to 400ms (entrances); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: sections fade + rise 10px, staggered 50–70ms. Animate opacity/transform only.
- Hover glows animate `box-shadow` at 180ms — the "ember catching" moment. Nothing pulses on a loop.
- Wrap ALL animation in `@media (prefers-reduced-motion: reduce)` → none. Keep visible
  `:focus-visible` copper rings everywhere.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No cool/blue-tinted darks (#0b0e16-style) — every dark in Foundry is BROWN-tinted (#181310 family).
3. No white text on copper buttons — always `--brand-contrast` dark text.
4. No images or textures for the metal feel — subtle CSS gradients only.
5. No glassmorphism, backdrop blur, or neon cyan/blue glows — that's obsidian, not Foundry.
6. No negative letter-spacing on the condensed display face; uppercase display always tracks OUT.
7. No emoji as UI icons — inline SVG (15–20px, 2px stroke, currentColor) only.
8. No lowercase or sentence-case h1/h2 — display headings are always uppercase.
9. No radii under 6px or over 12px (pills excepted) — Foundry is medium-radius machined metal.
10. No external scripts, CDN CSS, or images — self-contained HTML, fonts via @import only.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
