# Atlas — Vintage Cartography Light

## Personality
Atlas is a naturalist's field journal turned working dashboard: National Geographic archive maps, expedition ledgers, Monocle travel guides. Aged parchment (`--bg`) carries ink-sepia type inside fine double-keyline frames, with one burnt-sienna compass-red accent doing all the pointing. It feels scholarly and adventurous, yet every table, badge, and button stays crisp enough to run a business from.

## Typography
- **Faces:** headings use `--font-display` (Crimson Pro, semibold serif — 600 default, 700 for the page title only); body, labels, and UI use `--font-body` (Alegreya Sans); coordinates, IDs, dates, and numbers use `--font-mono` (Cousine) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 26–30px / 700 Crimson Pro
  - Section heading: 18–20px / 600 Crimson Pro
  - Card title: 15–16px / 600 Crimson Pro
  - Body: 14.5px / 400 Alegreya Sans · Secondary: 13.5px · Caption: 12px
- **Letter-spacing:** serif headings stay near-neutral (−0.01em max — Crimson Pro is already compact). Micro-labels ("coordinate labels") are Cousine mono, 10.5–11px / 700, `text-transform: uppercase`, +0.14em tracking, colored `--text-muted` or `--accent`.
- **Casing:** sentence case for headings and buttons; uppercase reserved for mono coordinate labels (eyebrows, table headers, KPI captions, field labels).
- **Color:** headings `--heading` (deep ink-sepia), body `--text`, supporting `--text-secondary`, plate numbers and timestamps `--text-muted`.
- Italic Crimson Pro is the "handwritten annotation" voice — welcome for one evocative subtitle, at most once or twice per page.
- **Line-height:** 1.55–1.65 body, 1.15–1.25 headings, 1.05 for KPI numerals.

## Color usage & contrast
- `--brand` (#6b4f2a sepia-brown) owns primary buttons, links, active nav, progress fills, and timeline node rings. Text on brand is always `--brand-contrast` (warm cream).
- `--accent` (#9a3412 burnt sienna / compass red) is the pointer color: eyebrow labels, delta arrows, one featured keyline, chart series #2. Never a full button background — at most a ghost/link treatment.
- Semantic colors carry MEANING only: `--success` (moss #4d7c0f) confirmed/complete/paid; `--warning` (#b45309) pending/at-risk; `--danger` (#b91c1c) overdue/failed.
- Badge/chip recipe: 10–14% opacity semantic tint background + full-strength semantic text — never semantic text on raw `--bg`.
- **Contrast rules:** body copy is always `--text` or darker on `--surface`/`--bg`. `--text-muted` is the floor — never set copy lighter than it, and never use `--text-muted` on `--surface-2` for anything longer than a timestamp.
- A page is ~90% parchment neutrals. If sepia, sienna, and three semantics are all shouting at once, mute something.
- Never use cool grays or blue-tinted neutrals — every neutral comes from the warm token ramp. NO purple/pink/magenta, ever.

## Surfaces & depth (map frames)
- **Card recipe (the double keyline — the signature):**
  `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);`
  `box-shadow: inset 0 0 0 3px var(--surface), inset 0 0 0 4px var(--frame-inner), var(--shadow);`
  Outer 1px border + a fine inner rule drawn 3px inside, like an old map plate frame. Card padding ≥ 18px so the rule never touches content.
- **Plain card** (tables, dense wide containers where the inner rule would fight content): same recipe minus the two inset shadows.
- **Inset areas** (table header rows, form wells, code blocks): `--surface-2`, radius `--radius-sm`, single hairline or none, no shadow, no inner rule.
- **Double horizontal rules:** page header and footer close with a 1px `--border` line plus a second 1px `--frame-inner` line 3–4px below (via `::after`). Header/footer only — not between every section.
- Radii are small and bookish: `--radius` (6px) cards, `--radius-sm` (4px) buttons/inputs, `999px` only for pills, dots, and progress tracks.
- **Paper tone:** the body layers two or three ultra-faint radial-gradients (2–4% opacity warm brown, `background-attachment: fixed`) over `--bg` for mottled-paper variation. CSS only — never images, never visible banding.
- Hover lift: `translateY(-1px)` + shadow deepens to `--shadow-lift`, 160ms. Restrained — parchment doesn't float.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Stick to it; never squeeze below it to fit content.
- Page: max-width 1120px, centered. Section gaps 32px; grid gaps 16–20px (12px at ≤640px).
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`.
- Any grid that contains a min-width table MUST use `minmax(0, 1fr)` columns, and tables always live inside an `overflow-x: auto` wrapper with `min-width` on the table itself.
- **Mobile width (hard rule):** the host wrapper injects a 25px safe-area padding on html/body — that is the ONLY horizontal inset on phones. At ≤640px, page wrappers and sections get ZERO side padding; cards keep only their internal padding, capped at 16px. Text spans the full remaining width; decorative rules may bleed edge-to-edge. Never stack padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, `--brand-contrast` text, `--radius-sm`, 10px × 18px padding, 14px / 700 Alegreya Sans; hover darkens ~12% via `color-mix(in srgb, var(--brand) 88%, var(--heading))`, lifts 1px, shadow deepens.
- **Secondary button:** `--surface` bg, 1px `--border`, `--heading` text; hover bg `--surface-2`, border darkens to `--scrollbar`, 1px lift.
- **Ghost button:** transparent, `--text-secondary` text, no border ever; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 1.75–2px, `currentColor`, 7px gap before the label, `flex: none`.
- **Badges:** pills (999px), Cousine mono 11px / 700 uppercase +0.08em, 3px × 10px padding, semantic tint bg + semantic text, leading 5px `currentColor` dot via `::before`.
- Neutral/info badges use `--brand-tint` + `--brand`; "newly charted" style callouts use `--accent-tint` + `--accent`.
- **Tables (the ledger):** header row on `--surface-2` with mono uppercase 10.5px `--text-muted` column labels tracked +0.14em; body rows 14px with ~13px vertical padding.
- Hairline `--border` dividers only — no verticals, no zebra, no boxed cells; row hover `--surface-2`.
- Numeric columns right-aligned in tabular Cousine at 13px, colored `--heading`.
- Each row gets a primary line (`--heading`, 500) with a muted 11.5px mono sub-line (route ID · coordinates).
- **Forms:** labels are mono uppercase 11px / 700 `--text-secondary`, 6px above the control.
- Inputs/selects: `--surface` bg, 1px `--border`, `--radius-sm`, 10px × 12px padding, 14px text in `--heading`; placeholder `--text-muted`; hover darkens border to `--scrollbar`.
- Focus = `--brand` border + 3px `--focus-ring` glow, default outline removed.
- Selects get `appearance: none` + an inline-SVG chevron as a data-URI background — the ONE place a hex may appear outside `:root`, since data-URIs cannot read CSS vars.
- **KPI tiles:** double-keyline card; top row = mono coordinate micro-label + 32px tinted icon chip (`--brand-tint` default, semantic tint where the metric warrants); then a 28–32px Crimson Pro 700 `--heading` value (tabular numerals); then a mono pill delta (success/danger tint) beside a 12px muted comparison caption.
- **Progress bars ("route completion"):** 8px `--surface-2` track, 999px radius, brand fill (semantic where status matters); label row above = 14px / 500 `--heading` name left, 12px mono `--text-muted` fraction right.
- **Timeline ("field log"):** a `2px dotted var(--scrollbar)` left rule — route markings on a chart — with 11px circular nodes: `--surface` fill + 2.5px ring in brand or semantic color, aligned to the first text line.
- Timeline entries: 14px `--text` with `--heading` 700 entity names bolded inline, then a mono 11px muted timestamp line ("09:42 · Jul 10 · Basecamp"); 20px between entries.
- **Empty states:** dashed 1px `--border` well on `--surface-2`, `--radius`, 32px padding, centered — muted 20px inline-SVG compass, one-line Crimson Pro 600 heading, one supporting sentence in `--text-secondary`, a secondary button. No illustrations, no oversized icons.
- **Links:** `--brand`, underlined with `--scrollbar` decoration color, `text-underline-offset: 3px`; hover shifts text and underline to `--accent`.

## Icons
- Inline stroke SVG only: 24×24 viewBox, `fill="none" stroke="currentColor"`, stroke-width 1.75–2 (2.5 for tiny 11px delta arrows), round caps and joins, `aria-hidden="true"`.
- Cartographic vocabulary first: compass rose, folded map, mountain, route pin, flag, shield, calendar. Never emoji, never icon fonts, never external sheets.

## Motion
- Durations: 140–160ms hovers, 380–440ms entrances. Easing `cubic-bezier(0.22, 1, 0.36, 1)` everywhere; no bounce, no spring.
- Entrances: cards fade in + rise 8–10px, staggered 50–70ms down the page via delay classes.
- Animate opacity and transform ONLY on entrance — never width, height, margin, or color.
- Hover transitions cover background, border-color, color, transform, box-shadow — nothing else.
- Wrap ALL animation in `@media (prefers-reduced-motion: reduce)` → animation none, opacity 1, transform none, durations clamped to 0.01ms.
- Always keep visible `:focus-visible` rings (3px `--focus-ring` + 1.5px `--brand`), motion preferences notwithstanding.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No cool grays or blue-tinted neutrals — all neutrals come from the warm parchment/sepia ramp.
3. No background images or image textures — paper tone is CSS gradients only, and imperceptibly subtle.
4. No heavy drop shadows, glassmorphism, or blur — depth is keylines first, faint warm shadow second.
5. No emoji as icons — inline stroke SVG (`currentColor`) only.
6. No all-caps serif headings; uppercase belongs only to small mono coordinate labels.
7. No large radii (>8px on cards) or pill-shaped buttons — this is a bound atlas, not a bubbly app.
8. No zebra striping, vertical rules, or boxed table cells.
9. No second saturated accent family — sienna red is the only pointer; don't add teals or blues.
10. No skipping the double keyline on primary cards — it IS the signature — but never nest double-keyline frames inside each other.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
