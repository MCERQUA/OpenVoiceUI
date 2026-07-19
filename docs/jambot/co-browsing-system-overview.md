# Co-Browsing System — Overview & Phased Plan (issue #154)

**Status (2026-07-19):** Phase 1 built (server-side browse service). Phases 2–5 designed, not yet built.

Server-side co-browsing: a headless Chromium runs **on the VPS**, the agent drives it, and the user watches a **live video stream** of it inside a canvas page — voice stays active throughout. Because it streams frames (CDP screencast) instead of embedding the site, `X-Frame-Options`/CSP frame-blocking (which kills `[CANVAS_URL:]` on Amazon/Google/Facebook/etc.) does not apply. Any site works.

## Why this exists — the three browsing lanes

| Lane | Where the browser is | Who drives | Who watches | Needs install | Frame-block immune |
|---|---|---|---|---|---|
| **Browser Companion** (`[NAVIGATE:]`, `[CLICK:]`, `[START_TASK:]`) | user's **real** Chrome | agent, in the user's session | user (their own tabs) | Chrome extension | n/a (real browser) |
| **`[CANVAS_URL:]` iframe** | user's browser iframe | user | user | none | ❌ blocked by most sites |
| **Co-browsing (#154)** | **VPS** headless Chromium | agent | user (live stream) | **none** | ✅ yes |

Co-browsing is the only lane that is both **zero-install for the user** (works from a phone) and **immune to frame-blocking**. It complements the extension (agent in *your* browser) by giving the agent *its own* browser you can watch from anywhere.

Related upstream research (see "Alternatives considered" below): Steel Browser, Neko, Browserless — all self-hostable; we build a thin purpose-fit service instead of adopting a heavier framework, but the seams are there to swap later.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  User's browser (phone/desktop, no install)  │
│   OpenVoiceUI — voice active                  │
│   Canvas page: browse-viewer.html   [Phase 2] │
│    <canvas> ← live JPEG frames                │
│    URL bar · back/fwd/reload · agent cursor   │
└───────────────┬─────────────────────────────┘
                │ WSS  (frames ↓ · user events ↑)
                ▼
┌─────────────────────────────────────────────┐
│  OVU container  routes/browse.py   [Phase 2]  │
│   proxies /api/browse/* → jambot-browse       │
│   injects tenant, enforces Clerk auth         │
└───────────────┬─────────────────────────────┘
                │ http (jambot-shared net)
                ▼
┌─────────────────────────────────────────────┐
│  jambot-browse  container        [Phase 1 ✅] │
│   aiohttp :8712                               │
│   Playwright → headless Chromium              │
│   1 BrowserContext per tenant (isolation)     │
│   CDP Page.startScreencast → viewers          │
│   SSRF guard · idle reaper · mem guard        │
│   per-context proxy seam →  [Phase 4 masking] │
└─────────────────────────────────────────────┘
```

**Deployment model:** singleton shared container on `jambot-shared`, exactly like `supertonic-tts` — NOT baked into each OVU image (Chromium is ~450MB; 26 copies is waste). Per-tenant isolation is by Playwright `BrowserContext` (separate cookies/storage), not separate containers.

---

## Phase 1 — Browse service container ✅ BUILT (2026-07-19)

Files (in the OVU repo):
- `deploy/browse-service/Dockerfile` — `mcr.microsoft.com/playwright/python:v1.48.0-noble` base (Chromium + libs bundled).
- `deploy/browse-service/server.py` — aiohttp service, the whole Phase-1 surface.
- `deploy/browse-service/requirements.txt`
- `scripts/jambot-browse-service.sh` — `build|start|stop|restart|status|logs|smoke` (mirrors `jambot-supertonic.sh`).

**API (all endpoints require `X-Browse-Key`; `/health` is open):**

| Method | Path | Purpose |
|---|---|---|
| POST | `/session/start` | `{tenant,url,[width,height],[proxy]}` — start/replace a tenant's session |
| POST | `/session/action` | `{tenant,action,...}` — `goto·click·type·key·scroll·back·forward·reload·wait` |
| GET | `/session/screenshot?tenant=&[full=1]` | PNG bytes for agent vision |
| GET | `/session/dom?tenant=` | `{url,title,text,links,inputs,buttons}` simplified page model |
| GET | `/session/status?tenant=` | url, title, idle_s, viewers, screencasting |
| POST | `/session/stop` | `{tenant}` — close context, free memory |
| WS | `/session/stream?tenant=&key=` | live base64-JPEG frames + events (auth via `?key=` since WS can't set headers) |
| GET | `/health` | `{ok,sessions,max_sessions,mem_percent}` |

**Guards baked in Phase 1:**
- **SSRF** — every navigation URL is DNS-resolved; refused if any A/AAAA record is private/loopback/link-local/reserved/multicast (blocks `169.254.169.254` cloud-metadata and sibling containers). http/https only.
- **Session cap** (`BROWSE_MAX_SESSIONS`, default 6) — new session over cap evicts the most-idle one.
- **Idle reaper** (`BROWSE_IDLE_TIMEOUT_S`, default 300s) — background task closes stale contexts.
- **Memory guard** (`BROWSE_MEM_GUARD_PCT`, default 85) — refuses *new* tenants under system memory pressure.
- **Auth** — shared secret `BROWSE_SERVICE_KEY` (from `.platform-keys.env`); runs OPEN only if unset (dev).
- **Per-tenant lock** — actions on one session serialize; one session per tenant (starting a new one replaces the old).

**The `proxy` seam:** `/session/start` accepts an optional `proxy` object `{server,username,password}` passed straight to `browser.new_context(proxy=…)`. Phase 4 (IP masking) populates it per-tenant; **nothing in `server.py` changes** — the seam already exists.

**Run it:**
```bash
bash scripts/jambot-browse-service.sh build
bash scripts/jambot-browse-service.sh start
bash scripts/jambot-browse-service.sh smoke     # start example.com, dom, screenshot, stop
```

**Not in Phase 1 (by design):** no OVU wiring, no canvas page, no `[BROWSE:]` tag, no user-input-into-stream, no multi-tab, no proxy provider. Those are Phases 2–5. Phase 1 is independently testable via the `smoke` subcommand.

---

## Phase 2 — OVU wiring + viewer page (agent browses, user watches)

**Goal:** end-to-end demo — user says "go to X", agent browses on the VPS, user watches live.

1. **`routes/browse.py`** (new OVU blueprint) — thin proxy to `jambot-browse`:
   - `POST /api/browse/start|action|stop`, `GET /api/browse/screenshot|dom|status`, `WS /api/browse/stream`.
   - Injects the tenant name (from `JAMBOT_TENANT`/`TENANT_NAME`) so the browser client never chooses its own tenant. Enforces Clerk auth via the existing `before_request` gate (agent key allowed for non-admin, like `/api/conversation`).
   - Reads `BROWSE_SERVICE_URL=http://jambot-browse:8712` + `BROWSE_SERVICE_KEY` from env.
2. **`default-pages/browse-viewer.html`** — canvas page (inline CSS, no CDN, dual-layout, auth bridge per canvas rules):
   - `<canvas>` renders base64 JPEG frames from `/api/browse/stream`.
   - URL bar (current page + title), back/forward/reload/stop controls.
   - "Agent is browsing…" / "Loading…" / connection-health indicators.
   - Agent-cursor overlay: draw a marker at the last agent click coords (from action echoes).
3. **`[BROWSE:url]` tag** in `app.js` — parsed alongside `[CANVAS_URL:]`; resolves via existing IP-block helper, `POST /api/browse/start`, then opens `browse-viewer` in the canvas.
4. **Agent context** — voice-system-prompt section documenting `[BROWSE:url]`: after starting, the agent works the page via a tool loop calling `/api/browse/screenshot` (vision) + `/api/browse/action`. Mirror the browser-companion tag discipline (words with every tag).
5. **Compose + provisioning** — add `BROWSE_SERVICE_URL`/`BROWSE_SERVICE_KEY` to the OVU service env in `templates/docker-compose.yml`; ensure OVU is on `jambot-shared` (it already is for TTS). No per-tenant container.

**Deliverable:** "Open Amazon and find a blue widget" → live stream in canvas, agent clicks/types, user watches. One tenant (test-dev) first.

---

## Phase 3 — User input passthrough (user acts in the stream)

**Goal:** user can click/type into the live view; agent sees the result.

- `browse-viewer.html`: capture canvas clicks → map display coords → true page coords (account for scale) → send `{type:"user_click",x,y}` up the WS. Capture keystrokes when focused → `{type:"user_type",text}`.
- `server.py` `h_stream`: parse those viewer events (Phase 1 already accepts+ignores them) → apply via `page.mouse.click` / `page.keyboard.type`. Rate-limit + same per-session lock so agent + user actions don't interleave mid-action.
- Turn-taking indicator in the viewer ("You have control" / "Agent is acting").
- Agent is notified of user-initiated navigation on its next turn (URL/title delta in context).

---

## Phase 4 — IP masking / residential egress (anti-block)

**Goal:** the VPS's datacenter IP is the #1 reason real sites (Cloudflare, retail, social) block or captcha a server-side browser. Route egress through a residential/ISP IP so co-browsing behaves like a real user.

The Phase-1 `proxy` seam makes this a **config + provider** change, not a rewrite. See the dedicated research section below for provider choice. Plan:
- Add `BROWSE_PROXY_*` config; when set, `routes/browse.py` passes a per-tenant `proxy` object to `/session/start`.
- Prefer **sticky sessions** (same exit IP for a session's lifetime) so multi-step flows don't trip mid-task IP changes.
- Pair with a stealth profile (see research) — a clean residential IP with stock headless Chromium still fails automation-protocol fingerprint checks on the hardest targets. Realistic scope: works everywhere for "watch the agent read a page"; the hardest bot-walled flows (login-gated retail checkout) remain best-effort.
- **Directory/citation policy still applies** (memory `feedback_never_touch_google_business_profiles`): masking is for *browsing/rendering*, never for touching banned identity platforms (GMB, Facebook, Yelp, BBB…).
- Cost control: residential proxies bill per-GB; screencast frames are server-side only (they do NOT traverse the proxy — only the page's own network egress does), so a browsing session is ~the page's real byte weight. Add a per-tenant GB budget + the existing memory/idle guards.

---

## Phase 5 — Multi-tab + polish

- Server browser supports multiple pages per context; viewer gets a tab strip; `[BROWSE_TAB:]` / action `{action:"new_tab"|"switch_tab"|"close_tab"}`.
- Download capture (agent grabs a file the page serves) → land in the tenant's uploads, same as extension `[DOWNLOAD_IMAGE]`.
- Screencast tuning: adaptive quality/FPS on the memory guard; pause screencast when no viewer is attached (Phase 1 already stops the cast when the last viewer disconnects).
- Health wired into JamFlow + `jambot-health-monitor.sh` (container up + `/health` sessions count), and a node on the JamFlow board.

---

## Alternatives considered (research 2026-07-19)

Instead of building `server.py` we could adopt an existing self-hostable browser-as-a-service. Evaluated:

| Option | What it is | Fit | Why we didn't adopt (yet) |
|---|---|---|---|
| **[Steel Browser](https://github.com/steel-dev/steel-browser)** (Apache-2.0) | Batteries-included browser API for AI agents: session mgmt, CDP over WS, `/scrape`·`/screenshot`·`/pdf`, **built-in live session viewer**, stealth/fingerprint options, self-host Docker (`:3000` API, `:5173` UI, `:9223` CDP). | **Strong.** Closest to #154 out of the box — the live viewer + CDP + anti-detect are exactly Phases 1/3/4. | Heavier surface than we need per-tenant; its viewer is a debug UI, not our canvas UX. **Revisit as the Phase-1 engine swap** if our thin service hits limits — our `routes/browse.py` proxy boundary means we could point it at Steel with little churn. |
| **[Neko](https://github.com/m1k1o/neko)** | Self-hosted virtual browser streamed via **WebRTC**, multi-user shared control. | Good for *human* watch-party co-browsing. | WebRTC is heavier than CDP JPEG for our "agent drives, user watches" model; agent-control API is not its focus. |
| **Browserless** | Headless Chrome farm, CDP/WS, connection pooling. | Good engine. | No live viewer; more scraping-farm than co-browse; commercial tilt. |
| **Custom (chosen)** | Thin aiohttp + Playwright, CDP screencast. | Exact-fit, ~450 LOC, no extra moving parts, our auth/SSRF/tenant model native. | We own it — but the proxy + engine seams keep Steel/Neko swappable. |

**Recommendation:** ship the custom service (done, Phase 1). If Phase 4 anti-detection proves hard, adopt **Steel Browser** as the engine behind the same `routes/browse.py` proxy rather than hand-rolling stealth.

### IP-masking / residential-proxy research (for Phase 4)

Datacenter IP is the core blocker. Pay-as-you-go residential proxy pricing, May–July 2026:

| Provider | PAYG $/GB | Notes |
|---|---|---|
| **[DataImpulse](https://dataimpulse.com)** | **~$1/GB** | cheapest first-party residential, non-expiring traffic — best budget entry |
| **[IPRoyal](https://iproyal.com)** | ~$1.75/GB (sub) | efficient for bursty mid-volume |
| **[Decodo](https://decodo.com)** (ex-Smartproxy) | ~$4/GB | best price/quality in benchmarks, rivals top tier |
| **Bright Data** | $4/GB (promo, list $8) | largest pool, SLAs, enterprise add-ons |
| **Oxylabs** | $6→$2.50/GB (tiered) | enterprise, big pool |

**Stealth layer** (IP alone isn't enough — clean IP + stock headless still fails the hardest checks). 2026 benchmark leaders:
- **[Camoufox](https://camoufox.com)** — Firefox fork, C++-level stealth patches, ~0% headless-detection on standard tests. Best fingerprint stealth; but it's Firefox (not our Chromium/Playwright-Chromium path).
- **[Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright)** — undetected Playwright/Chromium fork, CDP-leak patches; **drop-in for our Playwright code**. Best fit for a Chromium-based swap.
- **rebrowser-playwright** — Playwright fork with CDP-leak patches (older Chromium).

Key finding: *"a rebrowser/Patchright session from a clean residential IP passes more checks than a stock session from a datacenter IP"* — so **residential proxy first, stealth-fork second**. For our use (agent reads/browses a page the user watches) residential egress alone clears the vast majority; reserve Patchright/Camoufox for specific bot-walled targets.

**Phase-4 starter recommendation:** DataImpulse or IPRoyal PAYG (~$1–1.75/GB) with **sticky sessions**, wired through the existing `proxy` seam; add Patchright only if stock Chromium + residential IP still trips a target we need.

---

## Operational notes

- **Container:** `jambot-browse` on `jambot-shared`, `jambot/browse:latest`, 3GB / 1.5 CPU / 1GB shm cap.
- **Reach:** fleet → `http://jambot-browse:8712` (internal only; not nginx-exposed).
- **Restart/manage:** `bash scripts/jambot-browse-service.sh {start|stop|restart|status|logs|smoke}`.
- **Auth key:** `BROWSE_SERVICE_KEY` in `/mnt/system/base/.platform-keys.env` (add before any non-dev exposure).
- **Disk:** image is ~1.8GB (Playwright base). Counts toward `/mnt/system` — prune build cache after building (`docker builder prune -af`, playbook pb-20260715-001).
- **Update this doc** in the same commit as any co-browsing change (per the overview-doc rule in CLAUDE.md).
