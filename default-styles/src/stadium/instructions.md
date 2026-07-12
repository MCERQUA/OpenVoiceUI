# Stadium — Sports Broadcast / Game Night

## Personality
Stadium is a live sports broadcast graphics package: ESPN lower-thirds, a jumbotron
stat screen, a Champions League intro board. The page is a floodlit night-navy arena
(`--bg`) where pitch-green (`--brand`) carries the team identity and scoreboard-gold
(`--accent`) marks leaders, records, and highlights. It is FLASHY in its headers,
badges, and dividers — but every table, form, and paragraph stays broadcast-caption
crisp, because a real business dashboard has to be read at a glance.

## Typography
- **Faces:** headlines use `--font-display` (Anton — single weight 400, inherently
  heavy); body/UI uses `--font-body` (Roboto Condensed 400/500/600/700); stat
  figures, scores, ranks, and IDs use `--font-mono` (Oxanium 500–700) with
  `font-variant-numeric: tabular-nums`.
- **Display rules:** Anton is ALWAYS uppercase, `letter-spacing: 0.02em`,
  `line-height: 1.05`. Page title 30–40px; section/card headings 15–20px;
  KPI numerals in Oxanium 32–38px/700.
- **Body:** 14px/400 Roboto Condensed, line-height 1.55. Secondary copy 13px in
  `--text-secondary`. Micro-labels 11px/700 uppercase, `letter-spacing: 0.12em`,
  in `--text-muted`.
- **Casing:** headlines and micro-labels uppercase; body and table cells sentence
  case. Buttons uppercase 12–13px/700 with 0.08em tracking (this style's
  exception — broadcast buttons shout).
- **Color:** headings `--heading` (floodlight white), body `--text`, supporting
  `--text-secondary`, hints `--text-muted`. Declare explicit `h1` and `a` colors —
  never rely on inheritance.

## Color usage
- `--brand` (#16a34a turf green) = the team color: primary buttons, active states,
  focus rings, progress fills, the 3px left edge bars on stat blocks, and
  lower-third bar fills. Text placed ON brand is always `--brand-contrast`
  (near-black green) — white on this green fails contrast.
- `--accent` (#facc15 scoreboard gold) = the highlight reel: rank #1, "LEADER"
  badges, record deltas, the gold keyline under the masthead. Gold is for text,
  edges, and keylines ONLY — never a large fill, never a button background.
- Semantics: `--success` wins/paid/up; `--warning` (#f97316 orange — deliberately
  NOT gold) pending/at-risk; `--danger` losses/overdue/live. Badges use a
  14%-opacity tinted background with full-strength color text.
- Ratio: ~80% navy neutrals, ~12% green, ~5% gold, ~3% semantic. The flash lives
  in the header, badges, and dividers; content zones stay calm and readable.
- Never set text lighter than `--text-muted` on `--surface`; body copy sitting on
  `--surface-2` uses `--text` or brighter.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` padding 18–22px.
  Radii are TIGHT (6px / 3px) — broadcast graphics are sharp, never bubbly.
- **Stat blocks / featured cards** carry a 3px `--brand` left edge bar; the single
  leader/record card may use `--accent` for that bar instead.
- **Inset zones** (table headers, form wells, code): `--surface-2`, radius
  `--radius-sm`, no shadow.
- **Lower-third device:** a skewed label bar — outer element
  `transform: skewX(-12deg)` filled with brand (or `--surface-2`), inner label
  counter-skewed `skewX(12deg)` so the text stays upright. Use it for section
  headers and the VS chip on matchup cards. Never skew body copy, tables, or
  form controls.
- Optional arena wash on `body`: ONE fixed radial-gradient of brand at ≤7%
  opacity from the top edge — a subtle floodlight, not a light show.

## Spacing & layout
- 4px scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Page max-width 1160px,
  centered. Section gaps 32px; grid gaps 14–18px.
- KPI row `repeat(auto-fit, minmax(220px, 1fr))`; matchup/card grids
  `minmax(260px, 1fr)`. Any grid column that contains a table MUST use
  `minmax(0, 1fr)` and wrap the table in an `overflow-x: auto` container.
- **Mobile width (hard rule):** the host wrapper injects a 25px safe-area padding
  on html/body — that is the ONLY horizontal inset on phones. At ≤640px, page
  wrappers and sections get ZERO side padding; cards keep only their internal
  16–20px; skewed bars and washes may bleed edge-to-edge. Never stack padded
  containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, `--brand-contrast` text, uppercase
  12.5px/700, 10px × 18px padding, radius `--radius-sm`, faint green shadow;
  hover brightens ~10% (`color-mix` toward `--heading`) and lifts 1px.
- **Secondary button:** transparent bg, 1px `--border`, `--heading` text; hover
  turns border and text toward green.
- **Ghost button:** transparent, `--text-secondary` text; hover `--surface-2` bg,
  `--heading` text. Button icons: inline SVG 15–16px, stroke 2px, `currentColor`.
- **Badges:** sharp chips (3px radius — NOT 999px pills), 11px/700 uppercase,
  tinted bg + color text, leading 6px `currentColor` dot via `::before`. The LIVE
  badge uses danger tint with a pulsing dot (guarded by reduced-motion).
- **Leaderboard tables:** rank number in Oxanium 700 `--text-muted`, rank 1 in
  `--accent`; uppercase 11px column headers on `--surface-2`; rows 13.5–14px with
  12px vertical padding, hairline `--border` dividers only, hover `--surface-2`;
  numeric columns right-aligned tabular Oxanium; each row gets a `--heading` 600
  primary line plus a muted 12px sub-line (captain · court).
- **Forms:** 11px/700 uppercase labels above controls; inputs `--surface-2` bg,
  `--border` border, radius `--radius-sm`, 10px × 12px padding; placeholder
  `--text-muted`; focus = brand border + 3px `--focus-ring` glow. Selects get
  `appearance: none` plus an inline-SVG data-URI chevron.
- **KPI tiles:** micro-label + 32px tinted icon chip on the top row, then an
  Oxanium 34px `--heading` value, then a semantic delta chip and muted caption,
  with the 3px brand (or gold) edge bar on the left.
- **Progress bars:** 8px tall, `--surface-2` track, brand fill (gold ONLY for a
  completed/record state), 999px radius allowed here; name left, Oxanium
  fraction right, above the track.
- **Activity timeline:** 30px tinted icon circles + text rows; bold entity names
  inside the sentence; muted 12px timestamp below; hairline dividers between.
- **Empty states:** dashed `--border` well on `--surface-2`: muted inline-SVG
  icon, one short Anton line, one supporting sentence, a secondary button.

## Motion
- Hovers 150ms; entrances 400–500ms; easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: cards fade + rise 10px, staggered ~50ms. Lower-third bars may slide
  in 12px from the left. The LIVE dot pulses on a 1.6s loop.
- ALL animation — entrances AND the live pulse — is wrapped in
  `@media (prefers-reduced-motion: reduce)` and fully disabled there.
  Always keep visible `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No white text on `--brand` green — always `--brand-contrast`.
3. No gold (`--accent`) as a button background or large surface fill.
4. No skewing body copy, tables, or form controls — skew is for label bars only.
5. No rounded-bubble UI: radius stays 6px/3px; badges are sharp chips, not pills.
6. No emoji as UI icons — inline SVG (stroke, `currentColor`) only.
7. No Anton in body text — Anton is display-only and uppercase-only.
8. No neon glow spam: at most one subtle brand glow on the hero; content cards
   use the standard shadow token.
9. No zebra striping or vertical rules in tables.
10. No external scripts, CDN CSS, or images — self-contained HTML, `@import`
    fonts as the only external reference.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`.
Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for
your task — never bolt another theme back on, and route every color through the
`--token` variables.
