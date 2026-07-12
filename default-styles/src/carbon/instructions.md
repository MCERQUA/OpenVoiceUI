# Carbon — Precision-Instrument Dark

## Personality
Carbon is engineering hardware turned into an interface: a CNC machine HMI, a
flight-test telemetry console, Teenage Engineering's restraint — professional,
never "gamer." Surfaces are matte graphite panels with 1px technical borders;
nothing glows, nothing is glassy, nothing floats. Hierarchy comes from mono
figures, uppercase micro-labels with wide tracking, and two disciplined signal
colors: orange for controls, lime for live/positive telemetry.

## Typography
- **Faces:** headings and display values use `--font-display` (Chakra Petch);
  body copy and UI use `--font-body` (Barlow); ALL figures, IDs, timestamps,
  units, and table numerics use `--font-mono` (JetBrains Mono) with
  `font-variant-numeric: tabular-nums`. Mono is used PROMINENTLY — if it's a
  number, it's mono.
- **Scale:** page title 24–28px/700 (Chakra Petch) · section heading 15–17px/600
  · card title 13–14px/600 · body 14px/400 · secondary 13px · micro-label 10–11px.
- **Micro-labels:** 10–11px/600 mono, `text-transform: uppercase`,
  `letter-spacing: 0.12em`, color `--text-muted`. They label everything:
  KPI tiles, table columns, form fields, panel headers.
- **Headings:** Chakra Petch, letter-spacing −0.01em (its geometry is already
  technical; don't over-tighten). Page title may be uppercase; section titles
  stay sentence case.
- **Color:** headings `--heading`, body `--text`, supporting `--text-secondary`,
  labels/units `--text-muted`. Declare explicit `h1` and `a` colors — never
  rely on inheritance.

## Color usage
- `--brand` (#ea580c signal orange) = the CONTROL color: primary buttons,
  links, active states, focus rings, the featured panel's top rule. Use it
  sparingly — a Carbon page is ~92% graphite/gray.
- `--accent` (#84cc16 HUD lime) = LIVE/positive telemetry only: online
  indicators, uptime figures, "running" states, chart series #1. Never a
  button fill, never a link color.
- Semantic: `--success` complete/passed, `--warning` pending/attention,
  `--danger` fault/overdue. Tint backgrounds via
  `color-mix(in srgb, var(--success) 12%, transparent)` — badges read as
  status LEDs on a panel.
- Text on `--brand` is `--brand-contrast` (white). Never set text below
  `--text-muted` contrast on graphite. NO purple, pink, or magenta — ever.

## Surfaces & depth
- **Panel recipe:** `background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);` — matte. No blur,
  no translucency, no glass.
- **Inset wells** (table headers, form fields, code): `--surface-2`,
  radius `--radius-sm`.
- Radii are SMALL and stay small: 6px panels, 3px buttons/inputs/badges.
  No pills except tiny status dots. Angular is the identity.
- **Texture (optional, subtle):** a CSS-only scanline/grid may sit on the page
  background — e.g. `repeating-linear-gradient(0deg, transparent 0 3px,
  rgba(255,255,255,0.012) 3px 4px)` at ≤2% opacity. Never on cards, never animated.
- Hover: border sharpens to `--scrollbar`, background steps to `--surface-2`.
  NO translateY lift, NO glow shadows — panels are bolted down.
- Flourish budget: a 2px `--brand` top rule on ONE featured panel; corner tick
  marks (1px absolutely-positioned lines) on the page header only.

## Spacing & layout
- 4px base scale: 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48. Denser than SaaS styles —
  instrument panels are compact but never cramped.
- Page: max-width 1120px, centered. Section gaps 28–32px; grid gaps 14–16px.
- Grids: KPI row `repeat(auto-fit, minmax(220px, 1fr))`; card grids
  `minmax(250px, 1fr)`; any grid column holding a table MUST use `minmax(0, …)`.
- **Mobile width (hard rule):** the host injects 25px html/body padding — that
  is the ONLY horizontal inset on phones. At ≤640px wrappers/sections get ZERO
  side padding; panels keep only 14–16px internal padding. Never nest padded
  containers on mobile.

## Component recipes
- **Primary button:** `--brand` bg, white text, 3px radius, 9–10px × 16px pad,
  13px/600 uppercase with 0.04em tracking; hover brightens ~10%
  (`color-mix(in srgb, var(--brand) 88%, white)`). No lift.
- **Secondary button:** `--surface-2` bg, `--border` border, `--heading` text;
  hover border `--scrollbar`.
- **Ghost button:** transparent, `--text-secondary`; hover bg `--surface-2`,
  text `--heading`.
- **Button icons:** inline SVG, 15–16px, stroke 2px, `currentColor`, 7px gap.
- **Badges:** rectangular, 3px radius, 10.5px/600 uppercase mono, 3–4px × 8px
  padding, semantic tint bg + semantic text, 6px square `::before` LED in
  `currentColor` (1px radius, not a circle).
- **Tables:** mono uppercase 10.5px muted headers on `--surface-2`; rows 13.5px
  with 12px vertical padding; hairline `--border` dividers only; row hover
  `--surface-2`; numerics right-aligned mono in `--heading`; each row gets a
  primary line (`--heading`, 500) plus a muted mono sub-line (ID · location).
- **Forms:** uppercase 10.5px `--text-muted` micro-labels above controls;
  inputs `--surface-2` bg, `--border` border, 3px radius, 10px × 12px padding;
  focus = `--brand` border + 3px `--focus-ring` ring; selects get an inline-SVG
  data-URI chevron and `appearance: none`.
- **KPI tiles:** micro-label + 28px square tinted icon chip on the top row;
  28–30px Chakra Petch tabular value (mono `.unit` suffix for %/h); mono delta
  chip + muted caption below a 1px `--border` rule.
- **Progress/gauge bars:** 6px tall, square ends (2px radius), `--surface-2`
  track with 1px border, brand/accent/semantic fill; mono fraction
  right-aligned above the track.
- **Activity feed:** 28px square tinted icon chips (3px radius, not circles),
  mono timestamps, hairline dividers between entries.
- **Empty states:** dashed `--border` well on `--surface-2`, muted SVG icon,
  one heading line, one sentence, a secondary button.

## Motion
- Durations 120ms (hovers) to 320ms (entrances); easing
  `cubic-bezier(0.2, 0.8, 0.2, 1)`.
- Entrance: panels fade + rise 6–8px, staggered 40ms. Animate opacity/transform
  ONLY — no width/height animation, no flicker or CRT effects.
- Wrap ALL animation in `@media (prefers-reduced-motion: reduce)` → none.
  Always show `:focus-visible` rings.

## DON'Ts
1. NO purple, pink, or magenta — anywhere, ever.
2. No glassmorphism: no backdrop-blur, no translucent panels, no glow shadows —
   Carbon is matte (that's obsidian's lane; stay out of it).
3. No large radii or pill buttons; 6px/3px are the maximums.
4. No gradients on buttons or panels (the ≤2% scanline texture on the page
   background is the only exception).
5. No hover lift/translateY on panels — hardware doesn't float.
6. No emoji as UI icons — inline SVG (15–20px, 2px stroke) only.
7. No lime `--accent` on buttons or as a link color; it is telemetry, not a control.
8. No zebra striping or vertical rules in tables.
9. No proportional-font numbers — every figure is tabular mono.
10. No external scripts, CDN CSS, or images — self-contained HTML with the
    Google Fonts `@import` as the only external reference.

## Starting point
Always start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`.
Keep its `<style>` foundation and `:root` tokens, restyle/replace the content
for your task — never bolt a different theme back on, and route every color
through the `--token` variables.
