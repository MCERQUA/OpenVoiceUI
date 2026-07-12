# Preset Authoring Playbook — the full loop for adding new canvas styles

Read `SPEC.md` first (file format + hard rules). This file is the OPERATIONAL
loop proven on 2026-07-10 when meridian/atelier/obsidian were built.
System overview: `/home/mike/MIKE-AI/docs/jambot/canvas-style-system.md`.

## Session bootstrap (fresh context)

1. Repo: `/mnt/system/base/OpenVoiceUI/`, branch `feat/canvas-style-system`
   (check `git branch --show-current` — after merge, work on a new branch off main).
2. Existing presets: `default-styles/src/<id>/` — 13 as of 2026-07-11:
   meridian = SaaS light (default recommendation), atelier = editorial warm
   light (Fraunces serif/teal), obsidian = premium dark glass (blue/cyan),
   plus the 2026-07-11 Fable batch: ledger (financial serif light, forest
   green/gold), carbon (industrial dark, Chakra Petch, orange/lime), terra
   (organic earthen light, Epilogue, terracotta/olive), fjord (Scandinavian
   minimal light, Outfit, petrol/evergreen), noir (monochrome editorial
   light, Archivo, black/white/crimson), verdant (dark botanical, Cormorant,
   emerald/brass), admiralty (navy+gold heritage light, Playfair), helios
   (sun-gold energetic light, Bricolage Grotesque), foundry (dark copper
   luxe, Big Shoulders), atlas (vintage cartography light, Crimson Pro,
   sepia/sienna), and the 2026-07-11 flashy batch: velocity (motorsport dark,
   Saira italic, racing red/silver), stadium (sports-broadcast dark, Anton,
   turf green/scoreboard gold), voltage (extreme-sports dark, Bebas Neue,
   volt/hazard), arcade (retro 8-bit dark, Press Start 2P, coin red/pixel
   yellow), aurora (northern-lights dark, Unbounded, arctic green/ice cyan,
   animated sky). New styles must be DISTINCT from all existing ones —
   taken accent families: blue, sky, teal, amber, forest green/gold, orange/
   lime, terracotta/olive, petrol, crimson-on-mono, emerald/brass, navy/gold,
   marigold, copper/ember, sepia/sienna.
3. Test tenant: test-dev, OVU on host port 5001, containers
   `openvoiceui-test-dev` / `openclaw-test-dev`.

## The loop (per style)

1. **Author via subagent fan-out** — one agent per style, parallel. The prompt
   that worked: name the style, 5-8 sentences of aesthetic direction (reference
   real products), point at SPEC.md as law, require the agent to run build.py
   + JSON-parse check + self-review (tokens flow through :root vars, no
   purple/pink, no external scripts, viewport meta, reduced-motion guards).
2. **Build:** `python3 default-styles/build.py <id>` (assembles src/<id>/ → <id>.json).
   NEVER edit the assembled JSON directly.
3. **Server-validate:** load with `services.canvas_styles._validate` (see below).
4. **Deploy to test-dev (hotfix path):**
   `sg docker -c "docker cp default-styles openvoiceui-test-dev:/app/ && docker restart openvoiceui-test-dev"`
5. **Visual QA (non-negotiable — LOOK at the screenshots with the Read tool):**
   - Desktop 1280×900 via `GET /api/canvas/styles/<id>/preview` (Playwright,
     python3, installed on host).
   - Mobile 375×800: load the template with
     `<style>html,body{padding:25px!important;box-sizing:border-box!important}</style>`
     injected before `</head>` (mirrors the host wrapper), assert
     `document.scrollWidth == 375`, eyeball single-gutter text width.
6. **Commit to the feature branch** — one commit per style or per fix, push.

## Validation snippet (step 3)

```bash
cd /mnt/system/base/OpenVoiceUI && python3 - <<'EOF'
import json, sys
sys.path.insert(0, '.')
from services.canvas_styles import _validate
s = json.load(open('default-styles/NEWID.json'))
print(_validate(s) or 'VALID')
EOF
```

## Field-learned rules not obvious from SPEC.md

- **Serve-time injection:** real pages get 25px padding + zero-specificity
  `:where()` color fallbacks injected. Templates must NOT add body side
  padding; pages relying on color *inheritance* still get fallback colors, so
  templates must declare explicit h1/a colors (learned via picker bug 19c93b6).
- **Mobile width law:** host 25px is the ONLY horizontal inset ≤640px —
  wrappers zero side padding, cards 16-20px internal, `minmax(0,…)` on any
  grid holding a min-width table (obsidian 724px overflow bug, 4d6b66f).
- **Preview endpoint already mirrors padding** (c77337a) — don't add padding
  to templates to "fix" edge-to-edge previews.
- **Picker card previews** render the template scaled — a style must read well
  at thumbnail size (strong hero + KPI row up top).
- **Do NOT script `POST /styles/<id>/activate` on a tenant** — it stomps the
  user's chosen style. Test activation via a preview/clone, or ask Mike.
- **Icons:** stroke/currentColor inline SVG only (library at
  `services/canvas_styles.py::_ICON_PATHS`, rendered to canvas-styles/icons.html
  on activation). No emoji — lint rejects them.
- **Lint check on demo content:** `GET /api/canvas/lint/<page-id>` flags
  emoji/banned-purple/CDN/viewport — the template itself should pass all rules
  except page-icon (templates aren't manifest pages).
- **Distinctness:** every new preset needs its own typographic identity
  (different display face), surface treatment, and accent family vs the
  existing three. Blue #3b82f6 is taken (obsidian/meridian family), teal
  (atelier). No purple/pink ever.

## Style ideas queue (Mike-approved direction: polished, professional, distinct)

Candidates discussed/not yet built: none committed — propose 3-5 with one-line
concepts before fanning out, get Mike's pick.
