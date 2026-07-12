# Obsidian — Premium Dark Glass · Design System Instructions

You are building a page in the **Obsidian** style: refined enterprise-dark in the
spirit of Linear dark, Vercel dark, and Raycast. The page should feel deep,
layered, and expensive — glass surfaces floating over a blue-slate void with a
soft ambient glow. It is calm and confident, never neon, never a hacker
terminal, never flat "dark mode with gray boxes."

Start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`. Keep
its `<style>` foundation (tokens, ambient layer, glass recipes) and restyle the
content. NEVER bolt the old flat-dark theme back on top.

## 1. Typography

- **Body:** `var(--font-body)` (Inter) 400/500, 14–15px, line-height 1.6, color `var(--text)`.
- **Display / headings:** `var(--font-display)` (Space Grotesk) 600–700, color `var(--heading)`, letter-spacing `-0.02em`. H1 ≈ 28–34px, H2 ≈ 20px, H3 ≈ 16px.
- **Hero numerals (KPIs):** Inter **900**, 32–40px, letter-spacing `-0.03em`, with the gradient-text recipe (§4). Big numbers are the jewelry of this style.
- **Labels / overlines:** 11px, weight 600, `text-transform: uppercase`, letter-spacing `0.08em`, color `var(--text-muted)`.
- **Mono:** `var(--font-mono)` only for IDs, amounts in tables, code. Never for headings.
- Casing: sentence case everywhere except overline labels and table headers.

## 2. Color rules

- Every color flows through the `:root` custom properties. Do not introduce raw hexes outside `:root`.
- `--brand` (#3b82f6 blue) = interactive: primary buttons, links, focus rings, active nav.
- `--accent` (#22d3ee cyan) = highlight only: gradient endpoints, sparkline tips, one accent per view. Never make whole components cyan.
- Semantics: `--success` emerald / `--warning` amber / `--danger` red — badges, deltas, timeline dots only.
- Text hierarchy is three steps: `--heading` → `--text` → `--text-secondary` → `--text-muted`. Never use pure white or pure gray-500.
- **ABSOLUTELY NO purple, pink, or magenta.** Blue→cyan is the only decorative gradient axis. If a gradient looks violet, you drifted — fix it.
- Contrast: body text on `--surface` must stay ≥ 4.5:1 (the provided tokens do; don't lighten surfaces).

## 3. Surfaces & depth

Three layers, always in this order:

1. **Void:** `var(--bg)` page + the fixed ambient layer (two radial washes, blue top-left, cyan top-right, opacity ≤ 0.14). Keep it; never remove it.
2. **Glass card** (the workhorse):
   ```css
   background: linear-gradient(var(--glass), var(--glass)) padding-box,
               linear-gradient(155deg, var(--edge-hi), var(--edge-mid) 40%, var(--edge-lo)) border-box;
   border: 1px solid transparent;
   border-radius: var(--radius);
   backdrop-filter: blur(14px);
   box-shadow: var(--shadow);
   ```
   The 1px gradient border (bright top-left → dim bottom-right) is the signature. Every card gets it.
3. **Inset surface:** `var(--surface-2)` with `border-radius: var(--radius-sm)` for table headers, progress tracks, input fields — no shadow, no gradient border.

Hover lift: `transform: translateY(-2px)` + brand-tinted glow shadow + brighter border. Radii: cards `var(--radius)` (14px), controls/inner `var(--radius-sm)` (8px), badges 999px. Never mix other radii.

## 4. Signature recipes

- **Gradient text** (hero numbers only): `background: linear-gradient(120deg, var(--heading) 30%, var(--brand) 75%, var(--accent)); -webkit-background-clip: text; background-clip: text; color: transparent;`
- **Glow accent:** small elements may carry `box-shadow: 0 0 18px var(--glow-brand)` — sparingly (primary button, active dot, one hero element).

## 5. Spacing & layout

- Scale: **4 / 8 / 12 / 16 / 24 / 32 / 48**. Nothing off-scale.
- Card padding 20–24px; gaps between cards 16–20px; sections separated by 32–48px.
- Grids: `repeat(auto-fit, minmax(220px, 1fr))` for KPI rows, `minmax(280px, 1fr)` for content cards. Max content width 1200px, centered.
- Mobile-first: single column at 375px, tables inside `overflow-x: auto` wrappers. The host injects 25px body padding — do not add large body padding.
- **Mobile width (hard rule):** the host's 25px safe-area padding is the ONLY horizontal inset on phones. At ≤640px, wrappers and sections get ZERO side padding; cards keep only 16–20px internal padding. Text spans the full remaining width; decorative glows/backgrounds may bleed edge-to-edge. Never stack padded containers on mobile.

## 6. Component recipes

- **Primary button:** blue gradient (`--brand-deep`→`--brand`), white text, 600 weight, radius-sm, glow shadow; hover brightens + lifts 1px.
- **Secondary button:** glass background, 1px `--border`, `--text` label; hover → brand-tinted border.
- **Ghost button:** transparent, `--text-secondary`; hover → `--surface-2` bg + `--text`.
- **Badges:** pill, 11–12px 600; color at ~12% alpha bg, ~35% alpha border, full-strength text (see `.badge--success` etc. in template).
- **Tables:** uppercase 11px muted header on `--surface-2`; row borders `--border` at reduced alpha; row hover = faint brand tint. Numbers right-aligned, mono.
- **Forms:** inputs on `--surface-2`, 1px `--border`, radius-sm; focus = brand border + 3px brand ring at 20% alpha. Labels are overlines.
- **KPI tiles:** overline label → 900 gradient numeral → delta badge with ▲/▼ glyph in semantic color.
- **Progress bars:** 8px track `--surface-2`, fill blue→cyan gradient with soft glow, radius 999px.
- **Timeline:** 1px `--border` spine, 10px semantic-colored dots with a faint matching glow ring.
- **Empty states:** centered inside a glass card — muted inline-SVG glyph, one heading, one sentence, one ghost button. Never a bare gray box.

## 7. Motion

- Entrances: fade-up 8px, 480ms `cubic-bezier(0.16, 1, 0.3, 1)`, staggered 60–80ms per sibling. Once, on load.
- Hovers: 160–200ms ease-out; transform + shadow + border-color only. Never animate `filter`, layout, or blur.
- Everything animated sits behind `@media (prefers-reduced-motion: reduce)` — kill transforms and animations there. Non-negotiable.
- No infinite loops except at most one soft status-dot pulse.

## 8. Hard DON'Ts

1. DON'T use purple/pink/magenta anywhere — no `#667eea`, no `#764ba2`, no violet "glow."
2. DON'T use pure black (`#000`) backgrounds or pure white text — always the tokens.
3. DON'T ship a flat card (plain bg + plain border). Every card gets the glass + gradient-border recipe.
4. DON'T use emoji as UI icons — inline SVG or Unicode glyphs (▲ ▼ ●) only.
5. DON'T add external scripts, CDNs, Tailwind, or remote images. Fonts via the one `@import` only.
6. DON'T stack glows — one glowing hero element per view region.
7. DON'T use green/amber/red for decoration — semantic states only.
8. DON'T center-align body copy or left-align numeric table columns.
9. DON'T introduce new radii, off-scale spacing, or letter-spacing wider than 0.1em.
10. DON'T persist anything in localStorage — all state goes to the server.
