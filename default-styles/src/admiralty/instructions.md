# Admiralty — Navy & Gold Heritage Classic

## Personality
Admiralty is old money that reads its own spreadsheets: a New England yacht club ledger, a heritage law firm's letterhead, Ralph Lauren's flagship with a working operations desk behind it. Everything sits on warm cream paper (`--bg`) as ivory cards ruled with fine warm keylines, deep navy type, and one disciplined thread of burnished gold. Formality comes from typography and rule-work — double rules, small-caps labels, engraved precision — never from decoration or bright color.

## Typography
- **Faces:** headings use `--font-display` (Playfair Display — serif, high-contrast, always
  navy); body, labels, and UI use `--font-body` (Public Sans); numerals, IDs, dates, and
  ledger figures use `--font-mono` (Courier Prime) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 26–30px / 700 Playfair
  - Section heading: 17–19px / 600 Playfair
  - Card title: 15–16px / 600 Playfair
  - Body: 14px / 400 · Secondary: 13px · Micro-label: 10.5–11.5px
- **Small-caps labels are the signature:** section eyebrows, table column headers, KPI
  labels, and stat captions are 10.5–11.5px / 600, `text-transform: uppercase`,
  `letter-spacing: 0.12em` (wider than a SaaS style — this is the engraved look),
  colored `--text-muted` or `--accent` for the featured one.
- **Casing:** Playfair headings in sentence case or Title Case, never uppercase. Uppercase
  lives ONLY in the wide-tracked micro-labels.
- **Color:** headings `--heading` (ink navy), body `--text`, supporting `--text-secondary`,
  captions/timestamps `--text-muted`. Links are `--brand`, underlined on hover.
- **Line-height:** 1.55–1.65 body, 1.15–1.25 Playfair headings, 1.05 large KPI numerals.

## Color usage
- `--brand` (#1e3a5f navy) is the institutional color: primary buttons, links, active states,
  focus rings, default progress fills, the flag stripe on a featured card. It is a NAVY —
  dark, grayed, dignified. Never substitute a vivid blue (#2563eb / #3b82f6 belong to other
  presets and are banned here).
- `--accent` (#a17c1a gold) is the honor color: double-rule flourishes, the eyebrow tick,
  award/featured highlights, one KPI icon, small-caps label accents. Gold is trim, not
  paint — never a button background, never body text, never large fills.
- Semantic colors carry MEANING only: `--success` (sea green) paid/confirmed/on-track;
  `--warning` (ochre) pending/attention; `--danger` (signal red) overdue/failed. Pair each
  with a ~10% tint background (`--success-tint` etc. derived in :root).
- Text on `--brand` is always `--brand-contrast` (cream). Keep everything else navy-on-cream;
  a page should read ~95% cream/navy with gold trim.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` — keyline AND soft shadow,
  always. Padding 20–24px.
- **Double rules are the crest move:** the page header's bottom edge and a featured card's
  top edge use `border-bottom: 3px double var(--border)` (or gold `--accent` for the one
  featured element). Use at most two double rules per page.
- **Inset areas** (table headers, form wells, code): `--surface-2` (deeper parchment),
  radius `--radius-sm`, no shadow.
- **Radii are modest:** `--radius` (6px) cards, `--radius-sm` (3px) buttons/inputs/chips.
  Pills (999px) only for status badges and deltas. Nothing bubbly.
- **Hover:** border deepens toward `--scrollbar` + shadow deepens slightly + 1px lift.
  No glow, no gradients, no glass.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1080px, centered.
- Section gaps 32px; grid gaps 16–20px. Rule-work replaces some whitespace: a hairline
  `--border` divider under every section head.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`;
  any column that contains a table uses `minmax(0, …)` so it can shrink.
- **Mobile width (hard rule):** the host wrapper injects 25px html/body padding — that is
  the ONLY horizontal inset on phones. At ≤640px, page wrappers and sections get ZERO side
  padding; cards keep only 16px internal padding. Text spans the full remaining width.
  Never nest padded containers on mobile.

## Component recipes
- **Primary button:** navy bg, cream text, `--radius-sm`, 10px × 18px, 13px/600 with
  +0.04em tracking; hover lightens ~10% toward `--heading`-mixed navy and lifts 1px.
- **Secondary button:** `--surface` bg, 1px `--border`, `--heading` text; hover bg
  `--surface-2`, border `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary`; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 1.75–2px, `currentColor`, 7px gap.
- **Badges:** pills, 10.5px/600 uppercase +0.08em tracking, 4px × 10px, semantic tint bg +
  semantic text, 6px `currentColor` dot via `::before`.
- **Tables (the ledger):** small-caps column headers on `--surface-2` under a 1px rule;
  rows 13.5–14px, 12–14px vertical padding, hairline horizontal dividers only; row hover
  `--surface-2`; numerals right-aligned in Courier Prime tabular; each row gets a
  `--heading`/500 primary line + muted 12px sub-line (ID · detail).
- **Forms:** 11px small-caps `--text-secondary` labels above; inputs `--surface` bg, 1px
  `--border`, `--radius-sm`, 10px × 12px; focus = `--brand` border + 3px `--focus-ring`
  halo. Selects: `appearance: none` + inline data-URI SVG chevron.
- **KPI tiles:** small-caps micro-label + 32px tinted icon chip up top, then 28–32px
  Playfair `--heading` value (tabular-nums), then a semantic pill delta + muted caption.
- **Progress bars:** 7px track in `--surface-2`, navy (or semantic) fill, 999px radius,
  name left / Courier fraction right above the track.
- **Activity list:** 30px tinted-circle icon + text rows, hairline dividers; bold entity
  names with `--heading`; 12px muted timestamps.
- **Empty states:** dashed `--border` well on `--surface-2`: muted SVG icon, one Playfair
  line, one sentence, a secondary button.

## Motion
- Durations 150ms (hovers) – 400ms (entrances); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: cards fade + rise 8–10px, staggered 50ms, like pages settling into a folio.
  Animate opacity/transform ONLY.
- Wrap every animation in `@media (prefers-reduced-motion: reduce)` → none.
  Keep visible `:focus-visible` rings at all times.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. NO vivid SaaS blues (#2563eb / #3b82f6 family) — Admiralty's blue is navy #1e3a5f only.
3. No gold as a button background, large fill, or body text — gold is trim.
4. No dark sections or dark-mode panels; Admiralty is warm light only.
5. No gradients, glassmorphism, blur, or neon glow — depth is keylines + soft paper shadow.
6. No emoji as UI icons — inline stroke SVG (15–20px, 1.75–2px) or Unicode glyphs only.
7. No big radii (>8px on cards) or bubble buttons — the geometry stays engraved and modest.
8. No uppercase Playfair headings; uppercase belongs to wide-tracked micro-labels only.
9. No zebra striping, vertical rules, or boxed table cells — hairline horizontals only.
10. No external scripts, CDN CSS, or images — self-contained HTML, fonts via @import only.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
