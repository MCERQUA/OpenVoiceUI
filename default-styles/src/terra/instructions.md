# Terra — Warm Organic Earthen Light

## Personality
Terra is the calm of a premium landscape-architecture studio or an Aesop counter: warm sand pages, linen-white cards, deep soil-brown type, and one disciplined terracotta. It feels grounded and quietly luxurious — organic through color, roundness, and diffuse warm shadows, never through texture, kitsch, or rustic clutter. Everything stays crisp and professional enough to run a business dashboard on.

## Typography
- **Faces:** headings use `--font-display` (Epilogue — geometric, warm); body, labels, and UI use `--font-body` (Nunito Sans); numbers, IDs, and code use `--font-mono` (Spline Sans Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 25–30px / 700
  - Section heading: 16–18px / 600
  - Card title: 14–15px / 600
  - Body: 14px / 400 · Secondary: 13px · Caption/label: 11–12px
- **Letter-spacing:** headings −0.015em; micro-labels and table column headers +0.08em,
  `text-transform: uppercase`, 11px/700, colored `--text-muted`.
- **Casing:** sentence case everywhere except uppercase micro-labels. Never all-caps headings or buttons.
- **Color:** headings `--heading` (near-black soil), body `--text`, supporting copy
  `--text-secondary`, hints/timestamps `--text-muted`. Declare `h1` and `a` colors
  explicitly in CSS — never rely on inheritance (the serve-time injector adds
  fallbacks for pages that forget, but the template must not depend on them).
- **Weights:** Nunito Sans reads light — use 600 where other styles use 500,
  and 700 for buttons, labels, and emphasized names.
- **Line-height:** 1.55–1.65 for paragraphs, 1.2 for headings, 1.1 for big KPI numerals.

## Color usage
- `--brand` (#c2410c terracotta) is the ONE hot color: primary buttons, links, active states, focus rings, primary progress fills. A Terra page is ~92% warm neutral — clay appears in deliberate touches.
- `--accent` (#5f7a3d olive) is the grounding second voice: informational badges, secondary chart series, icon chips, one featured-card top border. Never a second button color, never for errors.
- Semantic colors carry MEANING only: `--success` complete/positive, `--warning` pending/at-risk, `--danger` overdue/errors. Always pair with a ~10%-opacity tint background (`color-mix(in srgb, var(--success) 10%, transparent)` or an rgba tint token).
- Text on `--brand` is always `--brand-contrast` (white). Never drop text below `--text-muted` contrast on linen.
- NO cool grays anywhere — every neutral is warm (sand, linen, taupe, soil).

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` — border AND diffuse warm shadow together. Padding 22–24px.
- **Inset/nested areas** (table headers, form wells, code blocks): `--surface-2`, radius `--radius-sm`, no shadow.
- Radii are Terra's signature — LARGER than most styles: `--radius` (16px) for cards, `--radius-sm` (10px) for buttons/inputs, `999px` for pills, chips, and avatars. When in doubt, round more.
- Shadows are diffuse and warm-toned (brown-based rgba, never blue-gray), soft and wide rather than tight and dark.
- **Hover lift:** `translateY(-1px)` + slightly deeper warm shadow, 160ms ease. Gentle — this style breathes, it doesn't snap.
- No gradients on surfaces. Max flourish: a 3px `--accent` (olive) top border on ONE featured card.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64. Terra runs airy — prefer the larger step.
- Page: max-width 1120px, centered. Section gaps 32–36px; card grid gaps 16–20px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`; use `minmax(0, …)` on any grid column that holds a min-width table.
- **Mobile width (hard rule):** the host wrapper injects 25px safe-area padding on html/body — that is the ONLY horizontal inset on phones. At ≤640px, page wrappers and sections get ZERO side padding; cards keep only their internal 16–20px. Text spans the full remaining width. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** terracotta bg, white text, 999px pill radius, 10px × 18px padding, 14px/700; hover deepens ~10% (`color-mix(in srgb, var(--brand) 88%, var(--heading))`) and lifts 1px.
- **Secondary button:** linen `--surface` bg, `--border` border, `--heading` text, pill radius; hover bg `--surface-2`, border `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary` text, pill radius; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2–2.25px, `currentColor`, 7px gap. Never emoji.
- **Badges:** pills (999px), 11px/700, ~4px × 11px padding, semantic-tinted bg + semantic-color text, leading 6px `currentColor` dot via `::before`.
- **Tables:** uppercase 11px muted headers on `--surface-2`; rows 13.5–14px with 13px
  vertical padding; hairline `--border` row dividers only (no verticals, no zebra);
  row hover `--surface-2`; numeric columns right-aligned in tabular mono; each row
  gets a `--heading`/600 primary line plus a muted 12px sub-line (ID · address).
  Wrap tables in an `overflow-x: auto` scroller with a sensible `min-width`.
- **Forms:** 12px/700 `--heading` labels above the control; inputs on `--surface`,
  `--border` border, `--radius-sm` (10px) corners, 10px × 14px padding; placeholder
  `--text-muted`; hover darkens border to `--scrollbar`; focus = brand border +
  3px warm terracotta ring. Selects: `appearance: none` + inline-SVG data-URI chevron.
- **KPI tiles:** uppercase micro-label + 34px round (999px) tinted icon chip on the
  top row, then a 28–32px Epilogue `--heading` value (tabular), then a pill delta
  chip in semantic color with a muted comparison caption.
- **Progress bars:** 8px tall, `--surface-2` track, brand/accent/semantic fill,
  999px radius; name left, mono fraction right above the track.
- **Activity lists:** 32px icon-in-tinted-circle rows (olive tint is the default,
  semantic tints for status events), entity names bolded in the sentence,
  12px muted timestamp, hairline dividers between items.
- **Empty states:** centered in a dashed `--border` well on `--surface-2`: muted
  inline-SVG icon, one-line heading, one supporting sentence, a secondary button.

## Motion
- Durations 160ms (hovers) to 450ms (entrances); easing `cubic-bezier(0.22, 1, 0.36, 1)` — soft landings.
- Entrance: cards fade + rise 10px, staggered 50–60ms. Animate opacity/transform ONLY.
- Wrap every animation in `@media (prefers-reduced-motion: reduce)` → none. Always visible `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No cool grays or blue-tinted neutrals — every neutral must be warm (sand/linen/taupe/soil).
3. No dark sections, dark headers, or dark-mode blocks; Terra is warm-light only.
4. No gradients, wood textures, paper grain, or photographic backgrounds — organic comes from color and shape, not texture.
5. No sharp corners: nothing below 10px radius except hairline dividers; buttons and chips are pills.
6. No serif display type — that is atelier's identity; Terra is Epilogue + Nunito Sans, sans-serif throughout.
7. No blue or teal accents — terracotta and olive only, plus semantic status colors.
8. No emoji as UI icons — inline stroke SVG (15–20px, `currentColor`) only.
9. No heavy dark shadows or glassmorphism blur; shadows stay diffuse, warm, and light.
10. No external scripts, CDN CSS, or images — fully self-contained HTML with inline SVG; fonts via the single `@import` only.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
