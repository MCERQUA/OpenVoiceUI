# Noir — Monochrome Editorial Brutalism

## Personality
Noir is a high-fashion runway report rendered as a dashboard: Saint Laurent's monochrome nerve, a Pentagram case study's grid discipline, Vogue's typographic scale. Pure white page, true-black ink, and typography doing ALL the design work — heavy Archivo display faces, hard 1–2px black rules, zero rounding, and one crimson red thread stitched sparingly through the black and white. It is brutal but disciplined: every element earns its place on the grid, and the data stays effortlessly readable.

## Typography
- **Faces:** display/headings use `--font-display` (Archivo, weights 700–900); body, labels, UI use `--font-body` (Libre Franklin); numerals, IDs, code use `--font-mono` (Space Mono) with `font-variant-numeric: tabular-nums`.
- **Scale — dramatic contrast is the signature:**
  - Page title: 40–56px / 900, uppercase, letter-spacing −0.03em, line-height 0.95–1.0
  - Section heading: 20–24px / 800, uppercase, −0.02em
  - Card title: 13–14px / 700, uppercase, +0.08em
  - Body: 14px / 400 · Secondary: 13px · Micro-label: 10–11px / 700 uppercase +0.14em
- **Casing:** display moments (page title, section headings, buttons, micro-labels, table headers) are UPPERCASE. Paragraph/body copy stays sentence case — never uppercase running text.
- **Leading:** tight on display (0.95–1.1), 1.6 on body paragraphs.
- **Color:** headings and body both `--text`/`--heading` true black (#111111); supporting copy `--text-secondary`; timestamps/hints `--text-muted`. Declare explicit `h1` and `a` colors — never rely on inheritance.
- KPI numerals: Archivo 800–900 at 36–48px, or Space Mono 700 for tabular figures.

## Color usage
- The page is ~97% black and white. `--brand` IS black (#111111): primary buttons, borders, fills, active states.
- `--accent` (crimson #dc2626) is the ONLY expressive color and it is RATIONED: one hot status badge, one delta that must scream, the active nav underline, a single red rule or index number. If red appears more than ~4 times on a page, remove some.
- Semantic colors are meaning-only and kept muted: `--success` (#15803d) complete/paid, `--warning` (#a16207) pending/at-risk, `--danger` (#b91c1c) overdue/failed. Badges = white or `--surface-2` background, 1.5px solid border in the semantic color, semantic-color uppercase text — never pastel tint fills.
- Text on black (`--brand`) is always `--brand-contrast` (white). Never gray-on-gray below `--text-muted` contrast.
- NO purple, pink, or magenta. No blues, no teals. Monochrome + crimson, full stop.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1.5px solid var(--border); border-radius: 0;` — flat by default. Featured/hover cards take the hard offset shadow: `box-shadow: var(--shadow)` (4px 4px 0 0 black). NEVER soft blurred shadows.
- **Inset areas** (table header rows, form wells, code): `--surface-2` (#f4f4f4), no shadow, square corners.
- **Inverted blocks:** one hero or featured card MAY invert — black background, white type, crimson accent — maximum one per page.
- Radii are 0 everywhere: cards, buttons, inputs, badges, progress bars. No pills, no circles except tiny 8px status dots.
- **Rules as decoration:** heavy 2–3px black top borders on sections, 1px hairlines between rows. The grid lines ARE the ornament.
- Hover: `transform: translate(-2px,-2px)` + shadow grows to `6px 6px 0 0 var(--border)` — the block physically lifts off the page. 120ms linear.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96. Whitespace is dramatic — big gaps between sections (48–64px), tight inside components.
- Page: max-width 1120px, centered. Strong visible grid: card grids may use ZERO gap with shared 1.5px borders (`gap:0` + border-collapse look), or 20–24px gaps — pick one per section, never mix.
- KPI row: `repeat(auto-fit, minmax(220px, 1fr))`. Any grid containing a table: `minmax(0, 1fr)`.
- **Mobile width (hard rule):** the host injects a 25px safe-area on html/body — that is the ONLY horizontal inset ≤640px. Page wrappers and sections get ZERO side padding on mobile; cards keep only their internal 16–20px. Full-bleed black bands may bleed edge-to-edge. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** black bg, white uppercase Archivo 12–13px/800 +0.1em, 12px × 20px padding, square, 1.5px black border; hover: white bg, black text (hard invert) + offset shadow.
- **Secondary button:** white bg, 1.5px black border, black uppercase text; hover: `--surface-2` bg + offset shadow.
- **Ghost button:** transparent, no border, `--text-secondary` uppercase text with 1.5px black underline offset 4px; hover: text and underline go `--accent`.
- **Button icons:** inline SVG, 14–16px, stroke 2px, `currentColor`, 8px gap.
- **Badges:** square, 10px/700 uppercase +0.12em, 3px × 8px padding, white bg + 1.5px semantic border + semantic text; leading 6px square dot via `::before` (`background: currentColor`).
- **Tables:** black 2px top rule; header row `--surface-2` with 10px/700 uppercase +0.14em black text; rows 13.5–14px, 14px vertical padding, 1px `#e5e5e5`-hairline dividers (define as a derived var), row hover `--surface-2`; numerics right-aligned Space Mono; first column gets a mono index number (01, 02…) in `--text-muted`.
- **Forms:** 10–11px/700 uppercase labels above; inputs white, 1.5px black border, square, 10px × 12px padding, Libre Franklin 14px; placeholder `--text-muted`; focus = 2px black border + `3px 3px 0 0` accent-tinted offset (no glow).
- **KPI tiles:** uppercase micro-label top, giant Archivo 40–48px/900 value, then a mono delta line — semantic color text, no chip fill. One tile per page may carry a crimson top rule or inverted treatment.
- **Progress bars:** 10px tall, square, `--surface-2` track with 1px black border, solid black fill (crimson only for the one critical bar); uppercase name left, mono fraction right.
- **Activity timeline:** 2px black left rule, square 10px black markers (crimson for the highlighted event), uppercase mono timestamps, entity names 700 weight.
- **Empty states:** 1.5px dashed black border well on `--surface-2`: black inline-SVG icon, uppercase one-line heading, one sentence, secondary button.

- **Section headers:** a 3px solid black top rule above every section, then the uppercase
  Archivo section title; pair with a right-aligned Space Mono uppercase hint
  (sort order, date range, count) in `--text-muted`.
- **Page masthead:** treat the header like a magazine cover — 6px black top rule,
  mono kicker line with a small crimson square tick, then the giant uppercase title.
  Optionally break the title across two lines for scale.
- **Data/charts:** black bars and lines on white; `--surface-2` for comparison series;
  crimson reserved for the single highlighted series or threshold line. Grid lines
  1px `--hairline`; axis labels Space Mono 10px uppercase.
- **Links in body copy:** `--text` with a 1.5px black underline (3px offset); hover
  turns text and underline `--accent`. Always declare the `a` color explicitly.

## Motion
- Durations 120ms (hovers, linear) to 400ms (entrances); entrance easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: blocks fade + rise 12px (or slide-in from left for the hero rule), staggered 50–70ms. Animate opacity/transform ONLY.
- Hover moves are mechanical and instant-feeling — no springy bounces, no scale.
- EVERY animation wrapped in `@media (prefers-reduced-motion: reduce)` → none. Always visible `:focus-visible` (2px black outline, 2px offset).

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever. No blue, no teal either: monochrome + crimson only.
2. No border-radius above 0–2px. No pills, no rounded avatars, no circles (8px status squares/dots only).
3. No soft/blurred shadows (`0 4px 12px rgba(…)`) — hard offset `Xpx Ypx 0 0 #111111` or nothing.
4. No gradients, no glassmorphism, no blur, no background images.
5. No gray-washed low-contrast type — ink is BLACK; muted text never lighter than `--text-muted`.
6. No emoji as UI icons — inline SVG (14–20px, 2px stroke, currentColor) only.
7. No uppercase running body text — display moments only.
8. No more than one inverted (black) block and ~4 crimson touches per page.
9. No zebra striping or boxed table cells; horizontal rules only.
10. No external scripts, CDN CSS, or images — self-contained HTML, Google Fonts @import is the only external reference.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
