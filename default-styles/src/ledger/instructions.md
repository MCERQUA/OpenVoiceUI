# Ledger — Private-Banking Broadsheet

## Personality
Ledger is the quiet authority of a private-banking statement: Coutts stationery, an FT premium report, a wealth-management annual review set in metal type. Cream paper (`--bg`), near-black ink, one deep forest green, one thread of aged gold — everything else is typography, hairline rules, and whitespace. It persuades by precision, never by decoration; if a page feels "designed," strip it back until it feels *set*.

## Typography
- **Faces:** headings and display figures use `--font-display` (Libre Caslon Text — a bookish serif; italics welcome for editorial asides); body, labels, and UI use `--font-body` (IBM Plex Sans); every number that matters — currency, dates, IDs — uses `--font-mono` (IBM Plex Mono) with `font-variant-numeric: tabular-nums`.
- **Scale:**
  - Page title: 26–30px / 700 serif, letter-spacing −0.01em
  - Section heading: 17–18px / 700 serif
  - Card title: 15px / 700 serif
  - Body: 14px / 400 sans · Secondary: 13px · Caption: 12px
- **Small-caps labels are the signature:** 10.5–11px, `text-transform: uppercase`, letter-spacing 0.12–0.14em, weight 500–600, color `--text-muted` (or `--accent` for the page eyebrow). Use them for column headers, KPI labels, field labels, and footer credits.
- **Casing:** serif headings in sentence case; uppercase is RESERVED for the small-caps micro-labels. Never uppercase a serif heading.
- **Color:** headings `--heading` (ink), body `--text`, supporting `--text-secondary`, captions `--text-muted`. Links are `--brand`, underlined on hover.
- **Line-height:** 1.6 body, 1.25 serif headings, 1.05 large statement figures.

## Color usage
- `--brand` (#14532d forest green) is the ink of authority: primary buttons, links, active states, focus rings, progress fills, the seal/monogram. A Ledger page is ~93% neutral cream-and-ink; green appears where money moves.
- `--accent` (#a16207 aged gold) is a THREAD, not a fill: eyebrow labels, the thick rule under the masthead, one featured-card top rule, delta arrows on notable figures. Never use gold for buttons or large areas.
- Semantic colors carry meaning only: `--success` settled/cleared/gains, `--warning` pending/review, `--danger` overdue/losses. Pair each with its ~9% tint variable for badge/chip backgrounds.
- Negative currency figures may be set in `--danger`; positive deltas in `--success`. Text on `--brand` is always `--brand-contrast` (cream-white).
- NO purple, pink, or magenta in any form — not in hex, not in gradients, not in tints.

## Surfaces & depth
- **Card recipe:** `background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);` — the shadow is barely-there paper lift; if a shadow is noticeable, it is too strong.
- **Statement double-rule:** headers of important blocks close with the classic thin double rule — `border-bottom: 1px solid var(--border)` plus a 3px-offset second hairline (use a `::after` or a wrapper with `border-bottom`). The masthead uses a 3px `--accent` rule over a 1px `--border` rule.
- **Inset areas** (table headers, form wells, totals rows): `--surface-2`, radius `--radius-sm`, no shadow.
- Radii stay tight and formal: `--radius` (6px) cards, `--radius-sm` (4px) buttons/inputs. Pills (999px) ONLY for status badges.
- Hover states shift background or border color; movement is restrained (≤1px lift). No glassmorphism, no blur, no gradients on surfaces.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Whitespace is generous — a statement never crowds its figures.
- Page: max-width 1080px, centered. Section gaps 40px (roomier than a SaaS dashboard); card grid gaps 16–20px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids `minmax(260px, 1fr)`; any grid column that holds a min-width table MUST be `minmax(0, …)`.
- **Mobile width (hard rule):** the host wrapper injects 25px html/body padding — that is the ONLY horizontal inset on phones. At ≤640px, page wrappers and sections get ZERO side padding; cards keep only 16–20px internal padding. Text spans the full remaining width. Never nest padded containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, `--brand-contrast` text, 4px radius, 10px × 18px, 13.5px/600 sans with 0.02em tracking; hover deepens toward ink (`color-mix(in srgb, var(--brand) 85%, var(--heading))`).
- **Secondary button:** `--surface` bg, 1px `--border`, `--heading` text; hover bg `--surface-2`, border `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary`; hover bg `--surface-2`, text `--heading`.
- **Button icons:** inline SVG, 15px, stroke 1.75–2px, `currentColor`, 7px gap. Never emoji.
- **Badges:** pills, small-caps 10.5px/600 + 0.08em tracking, 4px × 10px, semantic tint bg + semantic text, 5px `currentColor` dot via `::before`.
- **Tables — the centerpiece, set like a statement:** small-caps column headers on `--surface-2` over a 1px rule; rows 13.5–14px with 13px vertical padding and hairline `--border` dividers only (no verticals, no zebra); every figure right-aligned, `--font-mono`, tabular-nums; description cell gets a 500-weight `--heading` main line + muted 12px reference sub-line; close with a `--surface-2` totals row — serif "Total" label, bold mono sum, double rule above.
- **Forms:** small-caps 11px labels above; inputs on `--surface`, 1px `--border`, 4px radius, 10px × 12px; focus = `--brand` border + 3px `--focus-ring` halo. Selects: `appearance: none` + inline-SVG chevron data-URI.
- **KPI tiles:** small-caps label + hairline-boxed 32px icon chip on the top row; 28–30px serif tabular value; mono delta chip in semantic color with a muted comparison caption.
- **Progress bars:** 6px tall (finer than SaaS), `--surface-2` track, `--brand` or semantic fill, name left / mono fraction right.
- **Activity/timeline:** 30px icon-in-tinted-circle rows, hairline dividers; bold entity names; 12px mono-adjacent muted timestamps.
- **Empty states:** dashed `--border` well on `--surface-2`: muted SVG icon, one serif line, one sans sentence, a secondary button.

## Motion
- Durations 150ms (hovers) to 400ms (entrances); easing `cubic-bezier(0.16, 1, 0.3, 1)`.
- Entrance: sections fade + rise 8px, staggered 50–70ms — a page settling, not sliding. Animate opacity/transform ONLY.
- Wrap ALL animation in `@media (prefers-reduced-motion: reduce)` → none. Keep `:focus-visible` rings always on.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No dark sections or dark mode; Ledger is cream-light only.
3. No gradients — not on buttons, cards, backgrounds, or text.
4. No second saturated family beyond green + the gold thread; gold never becomes a button or a fill.
5. No heavy shadows, glassmorphism, or blur — paper has no glow.
6. No emoji as UI icons — inline stroke SVG (15–20px) or Unicode glyphs only.
7. No uppercase serif headings; uppercase lives only in the 11px small-caps labels.
8. No zebra striping, vertical rules, or boxed cells — hairline horizontal dividers only.
9. No left-aligned or proportional-figure currency columns — figures are right-aligned tabular mono, always.
10. No rounded-bubble UI (radius > 8px on cards, pills outside badges) — Ledger's geometry is formal.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep its `<style>` foundation and `:root` tokens, restyle/replace the content for your task — never bolt the old dark theme back on, and route every color through the `--token` variables.
