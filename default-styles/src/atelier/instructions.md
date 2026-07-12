# Atelier — Editorial Warm Light

You are building pages in the **Atelier** style: the confidence of a beautifully
set book, applied to living data. Think Stripe Press, a high-end annual report,
editorial data journalism. Everything is calm, warm, and deliberate — the page
persuades through typography and whitespace, never through decoration.

Start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its
`<style>` foundation and `:root` tokens; restyle and replace the content. Never
bolt an old dark theme back on top of this.

## 1. Typography (the whole personality lives here)

- **Display: Fraunces** (variable — use its axes). Headings set
  `font-family: var(--font-display)`. Large headings (h1, KPI numerals) get
  `font-variation-settings: 'opsz' 72` and weight 560–620; small headings
  (h3, card titles) use `'opsz' 24`, weight 600. Negative tracking on big
  sizes: `letter-spacing: -0.02em` at h1, `-0.01em` at h2.
- **Body: Source Sans 3**, 16px base, `line-height: 1.65`, weight 400.
  Secondary copy 14–15px in `--text-secondary`.
- **Small-caps labels** are the signature: 11px, `text-transform: uppercase`,
  `letter-spacing: 0.14em`, weight 600, `--text-muted`. Use them for section
  eyebrows, table headers, KPI captions, badge text, form labels.
- **Numbered sections**: every major section opens with an eyebrow like
  `№ 01 · Overview` (or `01 —`) in small-caps, the number in `--brand`.
- **Numerals**: KPI figures are Fraunces, 2.4–3rem, weight ~560. Table numbers
  use `font-variant-numeric: tabular-nums` and right-align.
- Italic Fraunces is available — use it sparingly for editorial asides and
  pull-quote flavor, never for emphasis inside UI chrome.

## 2. Color

- Page is `--bg` ivory; cards are `--surface`; wells/table-header bands are
  `--surface-2`. Text is warm ink (`--text` / `--heading`), never pure #000.
- **One accent: deep teal `--brand`.** It carries every interactive element —
  primary buttons, links, active states, section numbers, progress fills,
  focus rings. If two things compete for teal, demote one to ink.
- **Amber `--accent` is the second voice**, reserved for warnings, highlights,
  and "attention" semantics only. Never use it as a decorative alternate brand.
- Semantic: `--success` green, `--warning` amber, `--danger` warm red — badge
  text/dots and inline status only, always paired with a label.
- All body text on ivory/surface must stay ≥ 4.5:1 contrast (`--text-secondary`
  is the floor for paragraphs; `--text-muted` is for labels/captions only).
- **Banned: purple, pink, magenta, gradients as decoration, pure black.**

## 3. Surface & depth

Depth comes from **hairline rules and spacing, not blur**.

- Card recipe: `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` — nothing heavier.
- Never stack shadows or raise blur on hover. Hover = border darkens
  (`#cfc6b4`-ish via a token-mixed color) and/or a 2px teal top rule appears.
- Horizontal rules (`1px solid var(--border)`) separate sections and rows.
  A double rule (1px, 3px gap, 1px) may crown the page header — print flavor.
- Radius is near-square: `var(--radius)` (3px) for cards, `var(--radius-sm)`
  for badges/inputs. Nothing pill-shaped except status dots.

## 4. Spacing & layout

- Scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64px. Sections separated by 48–64px;
  card padding 24px (20px under 480px viewports).
- Content column max-width 1100px, centered, with generous side margins.
  The host injects 25px body padding — do not add large body padding.
- Grids: KPI row `repeat(auto-fit, minmax(200px, 1fr))`, card grid
  `repeat(auto-fit, minmax(260px, 1fr))`, gap 16–24px. Mobile-first: single
  column at 375px must be flawless.
- **Mobile width (hard rule):** the host's 25px safe-area padding is the ONLY
  horizontal inset on phones. At ≤640px, wrappers and sections get ZERO side
  padding; panels/cards keep only 16–20px internal padding. Text spans the full
  remaining width; decorative rules/backgrounds may bleed edge-to-edge. Never
  stack padded containers on mobile.

## 5. Component recipes

- **Primary button**: teal fill, `--brand-contrast` text, radius-sm, 10px 20px,
  weight 600, letter-spacing 0.02em; hover darkens to `#0c5f58`.
- **Secondary**: transparent, `1px solid var(--text)` ink border, ink text;
  hover fills `--surface-2`.
- **Ghost**: teal text, no border, small-caps optional; hover underlines with
  a 1px offset rule (`text-underline-offset: 4px`).
- **Badges**: small-caps 10–11px, 3px 10px, radius-sm, hairline border in the
  semantic color at ~35% opacity, tinted background at ~8%, colored dot before.
- **Tables**: small-caps header row on `--surface-2` with hairline top+bottom
  rules; body rows separated by hairline rules only (no zebra); numeric columns
  right-aligned tabular-nums; row hover = `--surface-2` wash.
- **Forms**: small-caps label above; input/select on `--surface` with hairline
  border, radius-sm, 10px 12px; focus = teal border + 3px soft teal ring
  (`rgba(15,118,110,0.15)`).
- **KPI tiles**: card with small-caps caption, Fraunces numeral, then a delta
  line (▲/▼ glyph + percentage in semantic color, 13px).
- **Timeline/activity**: hairline left rule with 8px teal (or semantic) dots
  sitting on it; timestamp in small-caps muted; entry text in body.
- **Empty states**: centered Fraunces italic line + one ghost action, framed
  by hairline rules above and below. No illustration clutter.

## 6. Motion

- One entrance only: fade-up 8px, 480ms, `cubic-bezier(0.22, 1, 0.36, 1)`,
  staggered ≤ 60ms per section. Hovers/focus transition 150–200ms ease.
- Progress bars may animate width once on load (600ms, same easing).
- Always wrap animation in `@media (prefers-reduced-motion: reduce)` guards
  that remove animation and transition-duration. No parallax, no spinners
  where a static state works, nothing loops forever.

## 7. Hard DON'Ts

1. Don't use purple, pink, or magenta — anywhere, ever.
2. Don't use blur shadows for depth; hairline borders + spacing only.
3. Don't set headings in the sans face or body copy in Fraunces.
4. Don't use pure black (#000) or pure white (#fff) — stay in the warm family.
5. Don't round corners past 4px or make pill buttons.
6. Don't zebra-stripe tables or add heavy column dividers.
7. Don't use emoji as UI icons — inline SVG or Unicode glyphs (▲ ▼ № ·) only.
8. Don't let amber act as a second brand color; it is semantic emphasis only.
9. Don't center-align paragraphs or justify text; left-align, ragged right.
10. Don't persist anything to localStorage — all state goes to the server.
