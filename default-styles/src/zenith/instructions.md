# Zenith — design system instructions

You are building pages in **Zenith**: the bridge interface of an orbital station in
2090. Deep space blue-black behind frosted-glass panels, hairline white borders,
and one ice-white glow carrying the entire identity. Think Tron: Legacy's white-suit
aesthetic and a luxury chronograph's dial — impressiveness comes from light, glass,
and precision, never from hue. This is a strictly monochrome-chrome dark theme.

## Typography

- **Display (`--font-display`, Michroma, 400 only):** page titles, section titles,
  KPI values, card titles. Michroma is wide and engineered — keep sizes modest
  (page title 24–26px, section titles 13–15px, KPI values 30–34px) and add
  letter-spacing: 0.04–0.10em. Section titles and eyebrows are UPPERCASE with
  0.14–0.22em tracking. Never bold Michroma (it has one weight); scale, don't weight.
- **Body (`--font-body`, Titillium Web):** everything else. 400 base, 600 for
  emphasis and buttons, 700 sparingly. Base 14px / 1.6.
- **Mono (`--font-mono`, Space Mono):** ALL data readouts — numbers in tables,
  timestamps, IDs, meter values. Always `font-variant-numeric: tabular-nums`.

## Color rules

- `--brand` (#e8f1ff, ice-white) is the ONLY highlight. It appears as glow
  (rgba white box-shadows), the primary button fill, active states, the pulsing
  status dot, and gradient hairlines. `--brand-contrast` (near-black) sits on it.
- `--accent` (#9db4cc, whisper steel-blue) is SECONDARY ONLY: link color, minor
  icon tints, quiet meta text. It must never dominate a component.
- Semantic colors (`--success`, `--warning`, `--danger`) are deliberately
  desaturated. They appear ONLY inside badges, deltas, and timeline icon chips —
  never as button fills, large surfaces, headings, or progress fills.
- NO saturated color anywhere. No blue brand (obsidian owns it), no green/cyan
  (aurora owns it), and absolutely no purple/pink/magenta.
- Explicitly set `h1 { color: var(--heading) }` and `a { color: var(--accent) }` —
  never rely on inheritance.

## Surface & depth

- Page sits on `--bg` with the fixed backdrop stack: layered radial-gradient
  starfield, a faint white halo at the top, and a perspective grid receding at
  the bottom. All fixed, `z-index` negative, `pointer-events: none`.
- Cards are frosted glass: `background: rgba(255,255,255,0.04)` (use the
  `--glass` var), `border: 1px solid rgba(255,255,255,0.12)` (`--hairline`),
  `backdrop-filter: blur(10px)` WITH the `-webkit-` prefix, `border-radius:
  var(--radius)`, `box-shadow: var(--shadow)`.
- Key cards get a gradient top hairline (`::before`, white fading both ways) and
  on hover: `translateY(-2px)`, brighter hairline, soft white outer glow
  (`0 0 24px rgba(232,241,255,0.10)`).
- Inputs/selects and table headers use `--surface-2`; never stack glass on glass.

## Spacing & layout

- Scale: 4 / 8 / 12 / 16 / 20 / 28 / 36 / 48. Page max-width 1120px, centered.
- Sections separated by 36px (28px mobile). Card internal padding 20px (16px mobile).
- Grids: KPI `repeat(auto-fit, minmax(220px, 1fr))`; card grid `minmax(280px,1fr)`;
  any grid column that holds a table MUST be `minmax(0, 1fr)`.
- **Mobile law (≤640px):** the host injects 25px body padding — that is the ONLY
  horizontal inset. Wrappers and sections get ZERO side padding; cards keep only
  their internal 16–20px. Never add body padding.

## Component recipes

- **Buttons:** primary = solid `--brand` fill, `--brand-contrast` text, white glow
  shadow that intensifies on hover with a -1px lift. Secondary = glass slab
  (`--glass` fill + `--hairline` border, heading-colored text) whose border and
  glow brighten on hover. Ghost = transparent, `--text-secondary`, glass fill on
  hover. All: 10px 18px padding, `--radius-sm`, 600 weight, inline SVG icons only.
- **Badges:** pill, 11px/600, tinted rgba background of their semantic color +
  matching text, leading 6px dot (`::before`, currentColor). Nominal/live badges
  may pulse the dot. Neutral state uses a `--brand` white tint.
- **Tables:** wrap in a padding-0 glass card + `.table-scroll { overflow-x: auto }`;
  table `min-width` ~640px. Uppercase mono-tracked headers on `--surface-2`,
  hairline row borders, row hover = faint white wash. Numeric cells right-aligned
  Space Mono tabular.
- **Forms:** uppercase 11px labels, `--surface-2` fields with `--border`,
  focus = `--brand` border + soft white ring (`--focus-ring`). Selects get an
  inline SVG chevron data-URI, never the native arrow alone.
- **KPI tiles:** glass card; uppercase muted label + icon chip (white-tint bg,
  brand icon); huge thin Michroma value with a slow animated white glow pulse
  (text-shadow keyframes, guarded); mono delta pill + muted meta below.
- **Progress = docking indicators:** segmented meters — a `--surface-2` track with
  a white gradient fill, sliced into segments by a repeating-linear-gradient
  overlay of `--bg` gaps. Value in Space Mono to the right. Warning-level meters
  may use `--warning` fill; that is the only non-white fill allowed.
- **Timeline:** hairline-separated rows, 30px circular icon chips (tinted rgba bg
  + semantic/brand icon), body text with 600 heading-colored lead-in, mono timestamp.
- **Empty states:** centered muted text + a single hairline-bordered circle
  containing a stroke SVG icon. No illustrations, no color.

## Motion

- Entrance: staggered rise-in (opacity + 12px translateY), 0.5s,
  `cubic-bezier(0.16,1,0.3,1)`, delays 0.05–0.35s.
- Ambient (all wrapped in `@media (prefers-reduced-motion: no-preference)`):
  KPI glow pulse (~6s), status-dot pulse (~2.5s), orbital-ring rotation behind
  the hero (60s+ linear), shimmer sweep on hovered section hairlines.
- Hovers 0.18–0.2s ease-out, transform/opacity/box-shadow/border-color only.
  Under reduced motion everything freezes but remains visible.

## Hard DON'Ts

1. NO saturated color accents — no blues, greens, cyans; the brand is white light.
2. NO purple, pink, or magenta anywhere, ever.
3. NO semantic colors outside badges, deltas, and icon chips.
4. NO emoji as icons — stroke/currentColor inline SVG only.
5. NO bold or lowercase-tracking Michroma; one weight, spaced and sized instead.
6. NO opaque flat cards — panels are glass (rgba white fill + backdrop-filter + hairline).
7. NO unguarded animation — every keyframe sits inside a no-preference media query.
8. NO wrapper side padding at ≤640px, and never add body padding (host injects 25px).
9. NO external scripts, CDNs, or images — Google Fonts @import is the only external ref.
10. NO hardcoded hex in component rules — every color flows through the :root tokens.

Start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`, keep its
`<style>` foundation and token variables, and restyle the content. Never bolt the
old dark theme back on.
