# Canvas Style Preset — Authoring Spec (v1)

Each preset lives in `default-styles/src/<id>/` as three files, assembled into
`default-styles/<id>.json` by `default-styles/build.py`:

- `style.json` — metadata + design tokens
- `instructions.md` — the design-system rules an AI agent follows when building pages in this style
- `template.html` — a COMPLETE demo/starter page (doubles as the picker preview and the copy-source for agents)

## style.json shape

```json
{
  "id": "<slug>",
  "name": "Display Name",
  "tagline": "One line, evocative",
  "description": "2-3 sentences for the picker card",
  "base": "light" | "dark",
  "version": 1,
  "tokens": {
    "bg": "#rrggbb", "surface": "#rrggbb", "surface-2": "#rrggbb",
    "border": "#rrggbb", "text": "#rrggbb", "text-secondary": "#rrggbb",
    "text-muted": "#rrggbb", "heading": "#rrggbb",
    "brand": "#rrggbb", "brand-contrast": "#rrggbb", "accent": "#rrggbb",
    "success": "#rrggbb", "warning": "#rrggbb", "danger": "#rrggbb",
    "scrollbar": "#rrggbb",
    "radius": "<px>", "radius-sm": "<px>",
    "shadow": "<css box-shadow>",
    "font-display": "<css font-family>", "font-body": "<css font-family>",
    "font-mono": "<css font-family>",
    "font-import": "<full Google Fonts css2 URL>"
  }
}
```

All color tokens are `#rrggbb` hex (the serve-time injector and picker swatches
parse them). Extra tokens are allowed; the listed set is required.

## template.html rules

- Complete document: `<!doctype html><html><head>…</head><body>…</body></html>`,
  `<meta name="viewport" content="width=device-width, initial-scale=1">`.
- **All CSS inline in one `<style>` block.** Google Fonts via `@import` at the
  top of that block is the ONLY external reference. NO CDN scripts, no Tailwind,
  no external images (inline SVG only).
- Define `:root{ --bg: …; --surface: …; }` custom properties whose names match
  the token keys exactly — every color used in the page flows through them.
- The host injects 25px body padding — do NOT add large body padding on top.
- Mobile-first: flawless at 375px, graceful to 1440px.
- **Mobile width rule (hard):** the host's 25px safe area is the ONLY horizontal
  inset. At ≤640px, wrappers/sections must have zero side padding (cards keep
  only their internal 16-20px). Text takes the full remaining width; decorative
  backgrounds may bleed edge-to-edge. Never nest padded containers on mobile.
- Demo content = a believable small-business dashboard/report ("Meridian Roofing
  Co." style fictional data) exercising ALL of: page header w/ title+subtitle+action
  button, 4 KPI stat cards, a data table (5+ rows, status badges), primary/secondary/
  ghost buttons, a form row (input + select), progress bars, an activity/timeline
  list, card grid, footer note. This page IS the style's showroom.
- Polish is the point: real typographic hierarchy, consistent spacing scale,
  hover states, subtle entrance animation (CSS only, respect
  `prefers-reduced-motion`), focus-visible states.
- BANNED: purple/pink/magenta anywhere, `linear-gradient(135deg,#667eea,#764ba2)`,
  emoji as UI icons (inline SVG or Unicode glyphs only), lorem ipsum.
- Size: aim 600–1000 lines. No JavaScript required; a tiny inline script for
  demo-only interactivity is allowed but not needed.

## instructions.md rules

Written TO the page-building agent ("you"). 80–150 lines. Must cover:
1. The mood/personality of the style in 3 sentences.
2. Typography system (faces, weights, sizes, letter-spacing, casing rules).
3. Color usage rules (where brand vs accent vs semantic colors go; contrast rules).
4. Surface & depth system (cards, borders, shadows, radii — exact recipes).
5. Spacing scale and layout grids.
6. Component recipes: buttons, badges, tables, forms, KPI tiles, lists, empty states.
7. Motion rules (durations, easings, what animates, reduced-motion).
8. 8-10 hard DON'Ts for this style.
9. Reminder: start from `/app/runtime/canvas-pages/canvas-styles/active-template.html`,
   keep its `<style>` foundation, restyle content — never bolt the old dark theme back on.
