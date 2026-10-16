---
name: canvas-web-design
description: Premium design system for OpenVoiceUI canvas pages (iframe-sandboxed HTML) when no Canvas Style is active - layered backgrounds, motion, glassmorphism, gradient text, KPI and dashboard patterns, all vanilla inline CSS. Ships canvas-template.html. Read before building a polished canvas page.
---

# Canvas Web Design: premium design system

Canvas pages run inside an iframe in the OpenVoiceUI voice assistant. They are not regular
websites: they must work with zero build step and still look like shipped product software.
This skill is the craft layer; the `canvas-pages` skill is the workflow (write, open, verify).

---

## Active style override: check first

If `/app/runtime/canvas-pages/canvas-styles/ACTIVE-STYLE.md` exists (Docker path; on native
installs it is `canvas-styles/ACTIVE-STYLE.md` inside your canvas pages dir), the user picked
their own design system on the Canvas Styles page. That file overrides the COLOR SYSTEM and the
template below: follow its rules and start from `canvas-styles/active-template.html` instead.
The craft standards in this skill (hierarchy, motion, depth, real content, no purple/pink, no
emoji icons) still apply on top of any style.

---

## The environment (non-negotiable)

- **Sandboxed iframe:** no npm, no bundler, no Tailwind/Bootstrap CDN. `cdn.jsdelivr.net` is
  allowed by the canvas CSP for real libraries (Three.js and similar) when you need one.
- **Google Fonts `@import` in `<style>` is OK**; always fall back to `sans-serif`.
- **All CSS inline** in one `<style>` in `<head>`; **all JS inline** in a `<script>` at body end.
- **Desktop icon (required):** `<meta name="page-icon" content="TYPE">` in `<head>` (types are
  listed in the `canvas-pages` skill: `dashboard`, `game`, `music`, `tools`, `document`, ...).
- **Interactivity only via postMessage:** the template defines `nav(page)`, `speak(text)` and
  `menu()`. Call `speak()` only from a user action (every speak is a paid assistant turn).
- **No browser storage:** persist page state with the canvas data API
  (`/api/canvas/data/<name>.json`, see `canvas-pages`), never `localStorage`.
- **Always start from the template:** copy `canvas-template.html` from this skill's folder
  (Docker install: `~/.openclaw/skills/openvoiceui/canvas-web-design/canvas-template.html`) to
  `/app/runtime/canvas-pages/<name>.html`, keep its FULL `<style>` block, then replace the demo
  content between the marked comments.

---

## DESIGN PHILOSOPHY

Every canvas page must feel like **premium enterprise software**. Not a template, not a demo — something a Fortune 500 company would actually ship.

### The Standard
- Multi-layered visual depth: ambient orbs, border gradients, card shadows, glows
- Meaningful animation: staggered entrance, hover micro-interactions, living status indicators
- Strong typography hierarchy: massive numbers, bold labels, readable secondary text
- Color with purpose: semantic colors signal meaning, not just decoration
- Real content: every value, label, and button reflects the actual task — no placeholders

### What "Bad" Looks Like — Never Do This
- White text on dark background, zero hierarchy — "flat and forgettable"
- Static pages with no animation — "it just... appeared"
- Cards that are just a border around text
- Buttons with no hover state
- Purple/pink gradients — the most overused AI default in existence
- Emoji as decorative icons

---

## COLOR SYSTEM

Use CSS variables from the design system. **Never hardcode hex values** that duplicate the vars.

```css
/* Backgrounds */
--bg: #0d0f17          /* page background */
--surface: #141620     /* card fills */
--surface-2: #1a1d2e   /* deeper card fills */
--surface-hover: #1f2235  /* hover state */

/* Brand */
--brand: #3b82f6       /* primary blue */
--brand-light: #60a5fa /* lighter blue for text */
--cyan: #22d3ee        /* accent cyan */
--amber: #f59e0b       /* energy, warnings */

/* Semantic */
--success: #34d399     /* green — positive */
--warning: #fbbf24     /* yellow — caution */
--danger: #f87171      /* red — alerts */

/* Text */
--text: #e8eaf0        /* primary */
--text-secondary: #9498ab  /* secondary */
--text-muted: #5f6380  /* labels, hints */
```

### BANNED Colors — Instant Rejection
- Any purple: `#764ba2`, `#667eea`, `#8b5cf6`, `#a855f7`
- Pink / magenta as primary
- `linear-gradient(135deg, #667eea, #764ba2)` — the world's most overused AI gradient

---

## TYPOGRAPHY

```css
/* Hero KPI — massive, bold */
font-size: 36px; font-weight: 900; letter-spacing: -2px;

/* Section headings */
font-size: 18px; font-weight: 800; letter-spacing: -0.4px;

/* Card titles */
font-size: 14px; font-weight: 700;

/* Body / description text */
font-size: 13px; color: var(--text-secondary); line-height: 1.6;

/* Uppercase labels */
font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-muted);
```

---

## ANIMATION LIBRARY

The v2.0 template includes all these keyframes. Use the utility classes — don't rewrite them.

### Entrance Animations — Apply to Every Major Section

```html
<!-- Stagger sections for a fluid cascade effect -->
<div class="page-header anim-fade"></div>
<div class="card-hero anim-scale"></div>
<div class="kpi-grid anim-slide-up delay-1"></div>
<div class="section-label anim-fade delay-2"></div>
<div class="card anim-slide-up delay-3"></div>
<div class="card anim-slide-up delay-4"></div>
<div class="action-grid anim-slide-up delay-5"></div>
```

Available classes: `anim-fade`, `anim-slide-up`, `anim-slide-left`, `anim-scale`
Delay classes: `delay-1` through `delay-8` (0.1s increments)

### Hover Micro-Interactions — Every Clickable Element

```css
/* Standard lift + shadow */
.my-element { transition: all 0.25s ease; }
.my-element:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 25px rgba(0,0,0,0.3);
  border-color: var(--border-light);
}

/* Horizontal slide (status lists, links) */
.my-item:hover { transform: translateX(3px); }

/* Scale down on click */
.my-btn:active { transform: translateY(0) scale(0.98); }

/* Glow on hover */
.my-cta:hover { box-shadow: 0 0 30px var(--brand-glow); }
```

### Continuous Animations

```html
<!-- Floating element (icons, avatars, hero graphics) -->
<div class="anim-float">...</div>

<!-- Pulsing glow (call-to-action cards) -->
<div class="anim-glow card-glow">...</div>

<!-- Living status dot — already built in -->
<div class="status-dot dot-running"></div>
```

### Animated Progress Bar

```html
<div class="progress-track">
  <!-- Set --fill-w to match the percentage. CSS animates from 0 to that value. -->
  <div class="progress-fill" style="width: 72%; --fill-w: 72%;"></div>
</div>
```

---

## LAYERED BACKGROUND SYSTEM

Wrap ALL body content in `.bg-orbs` to add animated ambient orbs behind everything:

```html
<body>
<div class="bg-orbs">
  <!-- ALL page content here — cards, grids, header, everything -->
</div>
</body>
```

The orbs use `position: fixed` with `filter: blur(90px)` at 11% opacity — atmospheric, not distracting.

**Custom orb colors** for non-blue themes:
```css
.bg-orbs::before { background: var(--success); }  /* green orb */
.bg-orbs::after  { background: var(--amber); }    /* amber orb */
```

---

## GLASSMORPHISM

Use `.card-glass` inside `.bg-orbs` wrappers for the frosted-glass effect:

```html
<div class="bg-orbs">
  <div class="card-glass anim-scale">
    <h3>Glass Card</h3>
    <p>Semi-transparent, blurred background, subtle border.</p>
  </div>
</div>
```

Custom glassmorphism for hero sections:
```css
.glass-hero {
  background: rgba(20, 22, 32, 0.55);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 20px;
  padding: 28px;
}
```

---

## GRADIENT TEXT

Make primary headings and KPI values pop:

```html
<h1 class="gradient-text">$48,750</h1>              <!-- blue → cyan -->
<h2 class="gradient-text-warm">Action Required</h2> <!-- amber → red -->
<h2 class="gradient-text-success">Goal Achieved</h2><!-- green → cyan -->
```

Custom gradients:
```css
.my-gradient {
  background: linear-gradient(135deg, #60a5fa, #22d3ee);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
```

---

## HERO CARD PATTERN

For dashboards, always open with a hero card showing the primary metric:

```html
<div class="card-hero anim-scale">
  <div style="font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text-muted); margin-bottom: 10px;">Monthly Revenue</div>
  <div style="font-size: 44px; font-weight: 900; letter-spacing: -2.5px; line-height: 1;" class="gradient-text">$48,750</div>
  <div style="font-size: 12px; color: var(--success); font-weight: 600; margin-top: 8px;">▲ +12.4% vs last month</div>
  <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">23 jobs completed this month</div>
</div>
```

---

## ICON SYSTEM — No Emoji

Use Unicode symbols or inline SVG. Never emoji.

```html
<!-- Direction / navigation -->
▲ ▼ → ← ↑ ↓ ↗ ↙

<!-- Status indicators -->
● ○ ◆ ◇ ■ □

<!-- Actions -->
+ − × ÷ ✓ ✗

<!-- SVG checkmark (inline, no external file) -->
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
  <polyline points="20 6 9 17 4 12"></polyline>
</svg>

<!-- SVG arrow right -->
<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
  <line x1="5" y1="12" x2="19" y2="12"></line>
  <polyline points="12 5 19 12 12 19"></polyline>
</svg>

<!-- Single-glyph icons (text inside an agent-icon div) -->
<div class="agent-icon">$</div>    <!-- finance -->
<div class="agent-icon">+</div>    <!-- sales, new -->
<div class="agent-icon">&#9654;</div>  <!-- dispatch, ops -->
<div class="agent-icon">&#9733;</div>  <!-- marketing, highlight -->
<div class="agent-icon">&#9998;</div>  <!-- builder, creative -->
```

---

## INTERACTIVE BUTTONS (postMessage)

**Every button must use `nav()`, `speak()` or `menu()`. Never `href="#"`.** The page runs in an iframe — `#` does nothing.

```html
<!-- Navigate to another canvas page -->
<button class="action-btn primary" onclick="nav('sales-pipeline')">
  <span class="label">Sales Pipeline</span>
  <span class="desc">View all active estimates</span>
</button>

<!-- Trigger an AI voice response -->
<button class="btn btn-primary" onclick="speak('Show me this week\'s revenue breakdown')">
  Revenue Report
</button>

<!-- Glowing CTA -->
<button class="btn btn-glow" onclick="speak('Create a new estimate')">
  + New Estimate
</button>

<!-- Open the page menu -->
<button class="back-btn" onclick="menu()">Pages</button>
```

---

## COMPLETE DASHBOARD PATTERN

Full example combining all patterns for a business dashboard:

```html
<body>
<div class="bg-orbs">

  <!-- Header -->
  <div class="page-header anim-fade">
    <div>
      <h1 class="gradient-text">Revenue Dashboard</h1>
      <div class="subtitle">Real-time business performance</div>
    </div>
    <button class="back-btn" onclick="menu()">Pages</button>
  </div>

  <!-- Hero metric -->
  <div class="card-hero anim-scale">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--text-muted);margin-bottom:8px;">Month-to-Date Revenue</div>
    <div style="font-size:44px;font-weight:900;letter-spacing:-2px;" class="gradient-text">$48,750</div>
    <div style="font-size:12px;color:var(--success);font-weight:600;margin-top:6px;">▲ +12.4% vs last month</div>
  </div>

  <!-- KPI grid -->
  <div class="kpi-grid anim-slide-up delay-2">
    <div class="kpi kpi-success">
      <div class="kpi-label">Jobs Complete</div>
      <div class="kpi-value">23</div>
      <div class="kpi-sub">This month</div>
    </div>
    <div class="kpi kpi-warning">
      <div class="kpi-label">Open Estimates</div>
      <div class="kpi-value">7</div>
      <div class="kpi-sub">Awaiting decision</div>
    </div>
  </div>

  <div class="section-label anim-fade delay-3">Quick Actions</div>

  <!-- Action grid -->
  <div class="action-grid anim-slide-up delay-4">
    <button class="action-btn primary" onclick="speak('Show me aging estimates that need follow-up')">
      <span class="label">Follow-Up Queue</span>
      <span class="desc">3 estimates need attention</span>
    </button>
    <button class="action-btn" onclick="nav('invoices')">
      <span class="label">Invoices</span>
      <span class="desc">Invoices & expenses</span>
    </button>
  </div>

  <!-- Status list with glass cards -->
  <div class="section-label anim-fade delay-5">Jobs In Progress</div>
  <div class="status-list anim-slide-up delay-6">
    <div class="status-item" onclick="speak('Tell me about the Johnson AC install job')">
      <div class="status-dot dot-running"></div>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:700;">AC Install — Johnson</div>
        <div style="font-size:11px;color:var(--text-muted);">Tech: Alex · Est. completion 2:30 PM</div>
      </div>
      <span class="status-badge badge-running">In Progress</span>
    </div>
  </div>

</div><!-- end bg-orbs -->
</body>
```

---

## Build brief for a coding sub-agent

When you delegate a large page to a coding sub-agent, give it a brief in this shape:

```
ROLE: You are a premium web designer building an OpenVoiceUI canvas page.
The page runs inside an iframe: no CDN frameworks, no npm, all CSS/JS inline.

READ FIRST:
1. <skills dir>/canvas-web-design/canvas-template.html  (copy its <style> block verbatim)
2. <skills dir>/canvas-web-design/SKILL.md              (patterns and examples)
   If canvas-styles/ACTIVE-STYLE.md exists in the canvas pages dir, follow it and start from
   canvas-styles/active-template.html instead.

TASK: [purpose, the real data to show, the actions, the desired feel]

OUTPUT FILE: /app/runtime/canvas-pages/[filename].html

REQUIRED:
- <meta name="page-icon" content="TYPE"> in <head>
- the full <style> block from the template (not trimmed, not rewritten)
- entrance animations on major sections, staggered with delay-1..delay-5
- hover micro-interactions on every clickable element
- gradient text on the primary heading or KPI value
- real, specific content; no placeholder text
- buttons use nav(), speak() or menu(); never href="#"
- a page header with a descriptive title and a menu() back button
- at least 3 distinct content sections
FOR DASHBOARDS also: bg-orbs wrapper, card-hero with the primary KPI, kpi-grid with 2-4 real
metrics, at least one card-glass, an action-grid with 2-4 speak() buttons.

BANNED: purple/pink/magenta, emoji as icons, CDN frameworks, href="#", placeholder text,
localStorage/sessionStorage, inline values that duplicate existing CSS variables.

When finished, output exactly:
FILE_WRITTEN: /app/runtime/canvas-pages/[filename].html
```

Then run the `canvas-pages` verification steps (open, context check, lint, three widths) on the
result yourself before telling the user it is ready.

---

## Quality checklist

**Every page:**
- [ ] `<meta name="page-icon" content="...">` in `<head>`
- [ ] Full template `<style>` block present (or the active style's template)
- [ ] Page header with title and a back button (`onclick="menu()"`)
- [ ] Entrance animations on the header and at least 2 more sections
- [ ] Hover state on every clickable element (card, button, status item)
- [ ] No placeholder text; all content is real and task-specific
- [ ] No purple/pink/magenta, no emoji icons, no CDN frameworks, no `href="#"`
- [ ] `nav()`, `speak()` and `menu()` defined in the `<script>` at body end
- [ ] Readable at 375px with no horizontal page scroll

**Dashboards additionally:**
- [ ] `.bg-orbs` wrapper around all content
- [ ] `card-hero` with a gradient-text primary metric
- [ ] `kpi-grid` with real semantic values
- [ ] At least one `card-glass` element
- [ ] `action-grid` with contextual `speak()` buttons
