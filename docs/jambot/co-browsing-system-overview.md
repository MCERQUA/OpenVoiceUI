# Co-Browsing System — Overview & Phased Plan (issue #154)

**Status (2026-07-19):** Phases 1–5 built — co-browsing is feature-complete. Browse service + OVU wiring + live viewer + user input passthrough + IP-masking plumbing + multi-tab + download capture + fleet health monitoring. Turning residential masking ON is the one operator step (add provider creds).

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

## Phase 2 — OVU wiring + viewer page (agent browses, user watches) ✅ BUILT (2026-07-19)

**Goal:** end-to-end — user says "go to X", agent browses on the VPS, user watches live.

Files:
- **`routes/browse.py`** (new blueprint) — thin proxy to `jambot-browse`:
  - `POST /api/browse/start|action|stop`, `GET /api/browse/screenshot|dom|status`, `GET /browse-viewer`.
  - Injects THIS tenant (from `JAMBOT_TENANT`/`TENANT_NAME`) so a browser client can only ever watch its own tenant's session. Rides the existing `before_request` gate (Clerk or agent key). Adds the service key server-side (never sent to the browser).
  - Keeps a per-process **latest browse state** (`get_browse_state()`) refreshed on every start/action — the source for `[BROWSE_STATE]`.
- **`server.py` `/ws/browse-stream`** (flask-sock) — bridges the browser viewer to the browse service's CDP screencast WS (`websockets.connect` upstream). Frames down, viewer events up (Phase 3 consumes them). Clerk-gated; tenant + key injected server-side.
- **`default-pages/browse-viewer.html`** — canvas viewer (inline CSS/JS, no CDN): `<canvas>` renders base64 JPEG frames, URL bar + back/forward/reload/go/stop, live/loading/offline dot, fps, agent-cursor overlay, and a **stubbed** user-click capture (`USER_INPUT_ENABLED=false`) that Phase 3 flips on. Served at `/browse-viewer`.
- **`src/app.js`** — `[BROWSE:url]` (resolve → `POST /api/browse/start` → open `/browse-viewer` in the canvas iframe) and `[BROWSE_ACTION:{…}]` (brace-walk extract → `POST /api/browse/action`). Both stripped from displayed text (regex chain + shared `strip()`).
- **`routes/conversation.py`** — injects `[BROWSE_STATE: …]` (url, title, visible-text digest, links, buttons) into the agent's per-turn context **only while a session is active** — text-sight so the agent navigates without a screenshot round-trip. Tag docs added to `voice-system-prompt.md` + the fallback constant.
- **`templates/docker-compose.yml`** — `BROWSE_SERVICE_URL` + `BROWSE_SERVICE_KEY` on the OVU service (OVU is already on `jambot-shared` for TTS). No per-tenant container.

**Agent loop:** user asks → agent emits `[BROWSE:url]` (+ words) → viewer opens, user watches → next turn the agent reads `[BROWSE_STATE]` → emits `[BROWSE_ACTION:{…}]` to click/type/scroll → repeat. Full screenshot→vision (image to the LLM) is the Phase 3 upgrade; Phase 2 sight is the DOM digest.

**Deliverable met:** "go to X and find Y" → live stream in the canvas, agent drives, user watches. Verify on test-dev first (needs `jambot-browse` running + OVU env wired).

---

## Phase 3 — User input passthrough (user acts in the stream) ✅ BUILT (2026-07-19)

**Goal:** the human can click/type/scroll into the live view; the agent sees the result.

- **`browse-viewer.html`** — `USER_INPUT_ENABLED=true`. Canvas is focusable; click → map display→canvas→page coords (`/ pageScaleFactor` from CDP metadata) → `{type:"user_click",x,y}`; wheel → `{type:"user_scroll",dy}` (40ms throttle, `preventDefault`); keydown → named keys (`Enter`, arrows, `Backspace`…) as `{type:"user_key",key}`, printable chars as `{type:"user_type",text}`. Turn indicator pill flips **agent → you** for 2.5s on any user input.
- **`deploy/browse-service/server.py`** — `h_stream` parses `user_*` events → `apply_user_event()` runs them (`page.mouse.click` / `keyboard.type` / `keyboard.press` / `mouse.wheel`) under the **same per-session lock** as agent actions, so a user click and an agent action never interleave. Rate-limited (~25/s; pointer events throttled, keystrokes never dropped). Fail-soft.
- **`routes/browse.py`** — `get_browse_state()` refreshes the DOM digest when the cache is >4s stale, so **user-driven navigation** (which goes straight to the service over the viewer WS, bypassing the OVU action proxy) is reflected in the agent's `[BROWSE_STATE]` on its next turn. Bounded: ≤1 extra DOM call per agent turn, only during an active session.
- The OVU `/ws/browse-stream` proxy already relays viewer events upstream (Phase 2's `_up()` coroutine) — no change needed.

**Result:** agent and user share one browser. Agent drives via `[BROWSE_ACTION]`; user clicks/types/scrolls directly in the stream; both see the same live view; the agent picks up whatever the user did on its next turn.

---

## Phase 4 — IP masking / residential egress (anti-block) ✅ BUILT (2026-07-19)

**Goal:** the VPS's datacenter IP is the #1 reason real sites (Cloudflare, retail, social) block or captcha a server-side browser. Route egress through a residential/ISP IP so co-browsing behaves like a real user.

The Phase-1 `proxy` seam made this **config + provider**, not a rewrite. Built:
- **`deploy/browse-service/server.py`** — `BROWSE_PROXY_SERVER/USERNAME/PASSWORD/STICKY` env → `_build_proxy()` → passed to `browser.new_context(proxy=…)`. An explicit per-request proxy (from OVU) still wins; otherwise the service-level env proxy applies to every session. **Sticky sessions:** each session gets a `<tenant>-<rand>` token; if the username contains `{session}` it's templated in, so the provider pins one exit IP for the session's lifetime (multi-step flows don't trip a mid-task IP change). `Session.proxied` flag tracks it.
- **`/session/ip`** (+ OVU `/api/browse/ip`) — probes the session's **real egress IP** via the context's own `APIRequestContext` (same path/proxy the live browser uses; doesn't touch the user's page). This is the proof-of-masking + a "is the residential proxy working" check. `/health` reports `proxy_configured`; `/session/status` reports `proxied` + `egress_ip`.
- **`browse-viewer.html`** — footer pill shows the exit IP + location: `🛡 <ip> · <city, country>` green when masked, grey `<ip>` when direct.
- **`scripts/jambot-browse-service.sh`** — reads `BROWSE_PROXY_*` from `.platform-keys.env` and passes them to the container; prints `egress: residential proxy …` or `egress: DIRECT`.

**Turning it on (operator step):** add to `/mnt/system/base/.platform-keys.env`, then `bash scripts/jambot-browse-service.sh restart`:
```
BROWSE_PROXY_SERVER=http://gate.example-provider.com:PORT
BROWSE_PROXY_USERNAME=<user>-sessid-{session}      # {session} → sticky per session
BROWSE_PROXY_PASSWORD=<pass>
```
Verify with `GET /api/browse/ip` (or the viewer pill) — a residential IP means it's live.

**Provider pick (research below):** start with **DataImpulse** (~$1/GB, non-expiring) or **IPRoyal** (~$1.75/GB), PAYG, sticky sessions. Only add a stealth fork (**Patchright** — drop-in undetected Playwright/Chromium) if a target still blocks with a clean residential IP.

**Verified end-to-end 2026-07-19:** baseline probe returns the Hetzner IP (178.156.162.212, `proxied:false`); with a proxy configured, `proxied:true` and the proxy log shows **every** browser CONNECT (`example.com:443`, `api.ipify.org:443`, `ipwho.is:443`) tunneling through it — so a real provider URL yields a residential exit IP with no code change. Real residential-IP confirmation needs provider creds (operator step).

**Guards that still apply:**
- **Directory/citation policy** (memory `feedback_never_touch_google_business_profiles`): masking is for *browsing/rendering*, never for touching banned identity platforms (GMB, Facebook, Yelp, BBB…). The SSRF guard stays on regardless of proxy.
- **Cost:** residential proxies bill per-GB; screencast frames are server-side only (they do NOT traverse the proxy — only the page's own egress does), so a session ≈ the page's real byte weight. A per-tenant GB budget is a Phase-5 add.
- Residential egress does not defeat automation-protocol fingerprinting on the hardest targets; realistic scope is "watch the agent read/browse a page" everywhere, with login-gated retail checkout best-effort (add Patchright there).

---

## Phase 5 — Multi-tab + downloads + health ✅ BUILT (2026-07-19)

- **Multi-tab** — `Session` now owns N `Tab`s; `sess.page`/`sess.cdp` resolve to the active tab (so all single-tab code paths work unchanged). Actions `new_tab`/`switch_tab`/`close_tab` via `[BROWSE_ACTION:{…}]`; the screencast **follows the active tab** (stops the old tab's cast, starts the new). `/session/tabs` (+ OVU `/api/browse/tabs`) lists them; the viewer renders a **tab strip** (click to switch, × to close, + for new). Never closes the last tab.
- **Download capture** — `accept_downloads=True`; a page download is auto-saved to a **container-local** per-tenant staging dir and `last_download` is recorded. Security: the browse container runs arbitrary sites' JS (`--no-sandbox`), so it is **NOT** given `/mnt/clients` access. OVU (the per-tenant trust boundary) pulls the file via `/api/browse/download` → `/session/download` (path-guarded stream) and writes it into the tenant's real uploads, returning `/uploads/<name>`. The agent sees `download: <file>` in `[BROWSE_STATE]` and tells the user it's saved.
- **Health** — `jambot-health-monitor.sh` checks `jambot-browse` (container up + `/health`) and alerts if the shared service is down (all fleet `[BROWSE:url]` sessions depend on it). Screencast already pauses when the last viewer disconnects (Phase 1).
- **`[BROWSE_STATE]`** now includes `tabs open: N (active #i)` and `download captured: <file>` alongside url/title/links/buttons.

**Verified live 2026-07-19:** new_tab→2 tabs (Example Domain + IANA), switch_tab(0), close_tab(1)→1 tab; screencast delivered frames after a tab switch (follows active); a page download was captured and OVU pulled a real 131-byte file into `test-dev`'s uploads with the browse container having no `/mnt/clients` mount.

**Remaining polish (optional, not blocking):** adaptive screencast quality/FPS under memory pressure; per-tenant GB budget when residential masking is on; a node on the JamFlow board.

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
