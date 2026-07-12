# Meridian — Modern SaaS Light

## Personality
Meridian is the calm confidence of a top-tier SaaS product: Linear's precision, Stripe's typography, Vercel's restraint. Everything sits on a cool gray-50 page (`--bg`) as crisp white cards with hairline borders and soft, layered shadows. It never shouts — hierarchy comes from type weight, spacing, and one disciplined blue, not from decoration.

## Typography
- **Faces:** headings use `--font-display` (Sora); body, labels, and UI use `--font-body` (Inter);
  numbers, IDs, and code use `--font-mono` (JetBrains Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 24–28px / 700
  - Section heading: 16–18px / 600
  - Card title: 14–15px / 600
  - Body: 14px / 400 · Secondary: 13px · Caption/label: 11–12px
- **Letter-spacing:** headings −0.02em (always tighten large type); micro-labels and table
  column headers +0.06em, `text-transform: uppercase`, 11px/600, colored `--text-muted`.
- **Casing:** sentence case everywhere except uppercase micro-labels. Never all-caps headings or buttons.
- **Color:** headings `--heading` (near-black slate), body `--text`, supporting copy
  `--text-secondary`, hints/timestamps `--text-muted`.
- **Line-height:** 1.5–1.6 for paragraphs, 1.2 for headings, 1.1 for big KPI numerals.

## Color usage
- `--brand` (#2563eb) is the ONE saturated color: primary buttons, links, active states, focus rings, progress fills, the occasional KPI accent. Use sparingly — a Meridian page is ~95% neutral.
- `--accent` (sky) only for informational secondary highlights (info badges, chart series #2). Never as a second button color.
- Semantic colors are for MEANING only: `--success` positive deltas/paid/complete; `--warning` pending/at-risk; `--danger` overdue/errors. Always paired with a 10%-opacity tint background (e.g. `color-mix(in srgb, var(--success) 10%, transparent)`).
- Text on `--brand` is always `--brand-contrast` (white). Never place gray text below `--text-muted` contrast on white.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` — border AND shadow together, always. Padding 20–24px.
- **Inset/nested areas** (table headers, form wells, code blocks): `--surface-2`, radius `--radius-sm`, no shadow.
- **Hover lift:** `transform: translateY(-1px)` + slightly deeper shadow, 150ms ease. Subtle — 1px, not 6px.
- Radii: `--radius` (12px) for cards/modals, `--radius-sm` (8px) for buttons/inputs/badges-containers, `999px` for pills.
- No gradients on surfaces. A faint top-border brand accent (2–3px) on one featured card is the maximum flourish.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 / 64. Stick to it.
- Page: max-width 1120px, centered. Section gaps 32px; card grid gaps 16–20px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(280px, 1fr)`. Everything single-column and comfortable at 375px.
- Generous whitespace is the brand: when in doubt, add 8px more, never less.
- **Mobile width (hard rule):** the host wrapper injects a 25px safe-area padding on html/body — that is the ONLY horizontal inset on phones. At ≤640px, page wrappers and sections get ZERO side padding; cards keep only their internal padding, capped at 16–20px. Text spans the full remaining width; decorative backgrounds may bleed edge-to-edge. Never stack padded containers on mobile.

## Component recipes
- **Primary button:** brand bg, white text, 8px radius, 9–10px × 16–18px padding, 14px/600,
  subtle shadow; hover darkens ~8% (`color-mix(in srgb, var(--brand) 88%, var(--heading))`)
  and lifts 1px.
- **Secondary button:** white bg, `--border` border, `--heading` text; hover bg `--surface-2`
  and border darkens to `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary` text; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2–2.25px, `currentColor`, 7px gap before the label.
- **Badges:** pills (999px), 11px/600, ~4px × 10px padding, semantic-tinted bg + semantic-color
  text, leading 6px `currentColor` dot via `::before`.
- **Tables:** uppercase 11px muted column headers on `--surface-2`; rows 13.5–14px with
  12–14px vertical padding; hairline `--border` dividers only (no verticals, no zebra);
  row hover `--surface-2`; numeric columns right-aligned in tabular mono; give each row a
  primary line (`--heading`, 500) with a muted 12px sub-line (ID · address).
- **Forms:** 12px/600 `--heading` labels above the control; inputs white, `--border` border,
  8px radius, 10px × 12px padding; placeholder `--text-muted`; hover darkens border;
  focus = brand border + 3px `--focus-ring` glow. Selects get an inline-SVG chevron
  (data-URI) and `appearance: none`.
- **KPI tiles:** uppercase micro-label + small tinted icon chip (32px, `--radius-sm`) on the
  top row, then a 28–32px `--heading` display-font value (tabular), then a pill delta chip
  in semantic color with a muted comparison caption.
- **Progress bars:** 8px tall, `--surface-2` track, brand (or semantic) fill, 999px radius,
  name left / mono fraction right above the track.
- **Activity lists:** 30px icon-in-tinted-circle + text rows; bold the entity names inside the
  sentence; 12px muted timestamp below; hairline dividers between items.
- **Empty states:** centered in a dashed-border `--surface-2` well: muted inline-SVG icon,
  one-line heading, one supporting sentence, a secondary button.

## Motion
- Durations 150ms (hovers) to 350ms (entrances); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: cards fade + rise 8–12px, staggered 40–60ms. Animate opacity/transform ONLY.
- Every animation wrapped in `@media (prefers-reduced-motion: reduce)` → none. Always visible `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No dark backgrounds or dark-mode sections; Meridian is light-only.
3. No gradients on buttons, cards, or page background.
4. No more than one saturated color family (blue) plus semantic status colors.
5. No heavy shadows (`0 25px 50px…`) or glassmorphism/blur effects.
6. No emoji as UI icons — inline SVG (16–20px, 1.5–2px stroke) only.
7. No all-caps headings/buttons; uppercase is reserved for 11px micro-labels.
8. No zebra striping, vertical rules, or boxed cells in tables.
9. No cramming: never drop below the 4px scale to squeeze content in.
10. No external scripts, CDN CSS, or images — self-contained HTML with inline SVG.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
