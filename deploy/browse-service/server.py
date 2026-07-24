"""
jambot-browse — server-side co-browsing service (issue #154, Phase 1).

A single shared headless-Chromium pool for the whole JamBot fleet. Each tenant
gets its own Playwright BrowserContext (cookie / storage isolation). The agent
drives a page via an HTTP action API and "sees" it via screenshots + a
simplified DOM extract; a CDP `Page.startScreencast` feed is relayed to any
connected WebSocket viewer so the human can watch every action live.

Why a separate container (not baked into OVU):
  - Chromium + libs is ~450MB; 26 OVU copies would be wasteful.
  - One process owns the browser pool → clean session accounting, one memory
    guard, one idle reaper. Mirrors the supertonic-tts shared-container pattern.
  - Lives on the jambot-shared docker network; OVU's routes/browse.py (Phase 2)
    is the only thing that talks to it, proxying to the user's canvas viewer.

Security posture (Phase 1):
  - Shared-secret auth (BROWSE_SERVICE_KEY) on every endpoint — defense in depth
    even though the port is internal-only to jambot-shared.
  - SSRF guard: every navigation target is resolved and refused if it points at
    a private / loopback / link-local / cloud-metadata address. The agent must
    never be able to make the browser hit 169.254.169.254 or a sibling container.
  - Per-tenant context isolation; a hard cap on concurrent sessions; a system
    memory guard that refuses new sessions under pressure.

Endpoints (all require X-Browse-Key):
  POST /session/start       {tenant, url, [width,height], [proxy]}  → start/replace a session
  POST /session/action      {tenant, action, ...}                   → click/type/scroll/goto/back/forward/reload/wait
  GET  /session/screenshot?tenant=..&[full=1]                       → PNG bytes (agent vision)
  GET  /session/dom?tenant=..                                       → {url,title,text,links,inputs,buttons}
  GET  /session/status?tenant=..                                    → session metadata
  POST /session/stop        {tenant}                                → close + free
  WS   /session/stream?tenant=..                                    → live screencast frames (base64 JPEG) + events
  GET  /health                                                      → {ok, sessions, mem_percent}  (no auth)

The `proxy` param on /session/start is wired but optional — it is the seam the
IP-masking phase plugs a residential proxy into (see the phased doc). Phase 1
ships it as a pass-through to Playwright's per-context proxy so nothing has to
change in this file later.
"""
import asyncio
import base64
import ipaddress
import json
import logging
import os
import socket
import time
import uuid
from urllib.parse import urlparse, urlsplit

import psutil
from aiohttp import WSMsgType, web
from playwright.async_api import async_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [browse] %(message)s",
)
logger = logging.getLogger("browse")

# ── Config (env-overridable) ────────────────────────────────────────────────
PORT = int(os.getenv("BROWSE_PORT", "8712"))
SERVICE_KEY = os.getenv("BROWSE_SERVICE_KEY", "").strip()
MAX_SESSIONS = int(os.getenv("BROWSE_MAX_SESSIONS", "6"))
IDLE_TIMEOUT_S = int(os.getenv("BROWSE_IDLE_TIMEOUT_S", "300"))   # 5 min
MEM_GUARD_PCT = float(os.getenv("BROWSE_MEM_GUARD_PCT", "85"))
DEFAULT_W = int(os.getenv("BROWSE_WIDTH", "1280"))
DEFAULT_H = int(os.getenv("BROWSE_HEIGHT", "800"))
NAV_TIMEOUT_MS = int(os.getenv("BROWSE_NAV_TIMEOUT_MS", "30000"))
SCREENCAST_QUALITY = int(os.getenv("BROWSE_SCREENCAST_QUALITY", "60"))
USER_AGENT = os.getenv(
    "BROWSE_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)

# ── IP masking / residential egress (#154 Phase 4) ───────────────────────────
# When BROWSE_PROXY_SERVER is set, every session egresses through it — the fix
# for the #1 server-side-browser blocker (datacenter IP → Cloudflare/retail
# captchas). Provider-agnostic: point it at DataImpulse / IPRoyal / Decodo /
# Bright Data. Sticky sessions keep one exit IP for a session's lifetime so a
# multi-step flow doesn't trip a mid-task IP change — most providers do this by
# embedding a session token in the username; put "{session}" in
# BROWSE_PROXY_USERNAME and we template it per tenant-session.
PROXY_SERVER = os.getenv("BROWSE_PROXY_SERVER", "").strip()
PROXY_USERNAME = os.getenv("BROWSE_PROXY_USERNAME", "").strip()
PROXY_PASSWORD = os.getenv("BROWSE_PROXY_PASSWORD", "")
PROXY_STICKY = os.getenv("BROWSE_PROXY_STICKY", "1").strip() not in ("0", "", "false")

# Where captured downloads land (#154 Phase 5). CONTAINER-LOCAL on purpose: the
# browse container runs arbitrary websites' JS in Chromium (--no-sandbox), so it
# is NOT given write access to /mnt/clients. Files stage here per-tenant; OVU
# (the per-tenant trust boundary, which already mounts the tenant's uploads)
# pulls them into the real uploads dir via /api/browse/download.
import pathlib as _pathlib
DOWNLOADS_ROOT = _pathlib.Path(os.getenv("BROWSE_DOWNLOADS_DIR", "/tmp/browse-downloads"))


def _downloads_dir(tenant: str) -> _pathlib.Path:
    # basename() the tenant to keep it a single path segment (no traversal).
    return DOWNLOADS_ROOT / os.path.basename(tenant or "unknown")


def _build_proxy(sticky_session: str) -> dict | None:
    """Build the Playwright proxy object from env, or None if unconfigured.

    ``sticky_session`` is a per-tenant-session token; when the username template
    contains "{session}" it is substituted so the provider pins one exit IP for
    the session (sticky). Without the placeholder the proxy still works but the
    exit IP may rotate per request (provider-dependent).
    """
    if not PROXY_SERVER:
        return None
    user = PROXY_USERNAME
    if PROXY_STICKY and "{session}" in user:
        user = user.replace("{session}", sticky_session)
    proxy = {"server": PROXY_SERVER}
    if user:
        proxy["username"] = user
    if PROXY_PASSWORD:
        proxy["password"] = PROXY_PASSWORD
    return proxy

# ── SSRF guard ──────────────────────────────────────────────────────────────
_BLOCKED_NETS = [
    ipaddress.ip_network(n) for n in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16",
        "198.18.0.0/15", "::1/128", "fc00::/7", "fe80::/10",
    )
]


def _host_is_blocked(host: str) -> bool:
    """True if `host` resolves to any non-public address (SSRF defense)."""
    if not host:
        return True
    # Resolve every A/AAAA record; block if ANY is private/loopback/link-local.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # unresolvable → refuse
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str.split("%")[0])
        except ValueError:
            return True
        if any(ip in net for net in _BLOCKED_NETS) or ip.is_multicast or ip.is_reserved:
            return True
    return False


def _validate_url(url: str) -> tuple[bool, str]:
    """Return (ok, normalized_or_reason). Only http/https to public hosts."""
    if not url:
        return False, "empty url"
    parsed = urlparse(url if "://" in url else f"https://{url}")
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme not allowed: {parsed.scheme}"
    if not parsed.hostname:
        return False, "no host"
    if _host_is_blocked(parsed.hostname):
        return False, f"host blocked (private/internal): {parsed.hostname}"
    return True, parsed.geturl()


async def _safe_title(page) -> str:
    """`page.title()` races with in-flight navigation on SPA sites (e.g. reddit
    client-side redirects): Playwright raises "Execution context was destroyed,
    most likely because of a navigation" and a bare read 500s the whole request.
    Wait for the DOM to settle and retry once; never let a title read fail a
    request — return "" if it can't be read."""
    for attempt in range(2):
        try:
            return await page.title()
        except Exception:
            if attempt == 0:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
                continue
            return ""
    return ""


# ── Session model ───────────────────────────────────────────────────────────
class Tab:
    """One page/tab within a session's browser context (#154 Phase 5)."""
    __slots__ = ("page", "cdp", "id")

    def __init__(self, page, cdp, tab_id):
        self.page = page
        self.cdp = cdp
        self.id = tab_id


class Session:
    def __init__(self, tenant: str, context, page, cdp, proxied=False):
        self.tenant = tenant
        self.context = context
        # Multi-tab (#154 Phase 5): a session owns N tabs; `active` indexes the
        # one being screencast + acted on. sess.page/sess.cdp transparently
        # resolve to the active tab so all the single-tab code paths work as-is.
        self.tabs = [Tab(page, cdp, 0)]
        self.active = 0
        self._next_tab_id = 1
        self.proxied = proxied     # egressing through a residential proxy (#154 P4)
        self.egress_ip = None      # cached egress IP from the last /session/ip probe
        self.last_download = None  # filename of the most recent captured download (P5)
        self.last_activity = time.time()
        self.viewers: set[web.WebSocketResponse] = set()
        self.screencasting = False
        self.lock = asyncio.Lock()  # serialize actions per session

    @property
    def page(self):
        return self.tabs[self.active].page

    @property
    def cdp(self):
        return self.tabs[self.active].cdp

    def touch(self):
        self.last_activity = time.time()

    @property
    def idle_s(self) -> float:
        return time.time() - self.last_activity


class BrowseService:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.sessions: dict[str, Session] = {}
        self._start_lock = asyncio.Lock()

    async def startup(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        logger.info("chromium launched; max_sessions=%d idle_timeout=%ds",
                    MAX_SESSIONS, IDLE_TIMEOUT_S)

    async def shutdown(self):
        for tenant in list(self.sessions):
            await self.stop_session(tenant)
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()

    # ── lifecycle ───────────────────────────────────────────────────────────
    async def start_session(self, tenant, url, width, height, proxy):
        async with self._start_lock:
            # Replace any existing session for this tenant (one per tenant).
            if tenant in self.sessions:
                await self.stop_session(tenant)

            if len(self.sessions) >= MAX_SESSIONS:
                # Evict the most-idle session to make room.
                victim = max(self.sessions.values(), key=lambda s: s.idle_s)
                logger.info("session cap hit — evicting idle tenant=%s (%.0fs)",
                            victim.tenant, victim.idle_s)
                await self.stop_session(victim.tenant)

            ctx_kwargs = dict(
                viewport={"width": width, "height": height},
                user_agent=USER_AGENT,
                accept_downloads=True,   # capture downloads → tenant uploads (#154 P5)
            )
            # Proxy precedence: an explicit per-request proxy (from OVU) wins;
            # otherwise fall back to the service-level env proxy (#154 Phase 4).
            # Sticky session token pins one exit IP for this session's lifetime.
            sticky = f"{tenant}-{uuid.uuid4().hex[:10]}"
            eff_proxy = proxy or _build_proxy(sticky)
            if eff_proxy:
                ctx_kwargs["proxy"] = eff_proxy

            context = await self.browser.new_context(**ctx_kwargs)
            context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
            page = await context.new_page()
            cdp = await context.new_cdp_session(page)
            sess = Session(tenant, context, page, cdp, proxied=bool(eff_proxy))
            self.sessions[tenant] = sess
            self._wire_frame_handler(sess, cdp)
            self._wire_downloads(sess, page)
            await self._navigate(sess, url)
            return sess

    def _wire_frame_handler(self, sess, cdp):
        """Relay a tab's CDP screencast frames to the session's viewers. Only the
        active tab is ever screencasting, so registering on every tab is safe."""
        def _on_frame(params):
            asyncio.create_task(self._dispatch_frame(sess, params))
        cdp.on("Page.screencastFrame", _on_frame)

    def _wire_downloads(self, sess, page):
        """Capture files the page downloads into the tenant's uploads (#154 P5),
        same destination as the browser-extension [DOWNLOAD_IMAGE]. Fail-soft."""
        def _on_download(download):
            asyncio.create_task(self._save_download(sess, download))
        page.on("download", _on_download)

    async def _save_download(self, sess, download):
        try:
            ddir = _downloads_dir(sess.tenant)
            ddir.mkdir(parents=True, exist_ok=True)
            name = os.path.basename(download.suggested_filename or "download.bin")
            # namespace by tenant + time so shared uploads don't collide
            dest = ddir / f"browse-{int(time.time())}-{name}"
            await download.save_as(str(dest))
            try:
                os.chmod(dest, 0o664)
            except OSError:
                pass
            sess.last_download = dest.name
            logger.info("download captured tenant=%s → %s", sess.tenant, dest.name)
        except Exception as e:
            logger.warning("download capture failed tenant=%s: %s", sess.tenant, e)

    # ── tabs (#154 Phase 5) ───────────────────────────────────────────────────
    async def new_tab(self, sess, url=None, switch=True):
        async with sess.lock:
            page = await sess.context.new_page()
            cdp = await sess.context.new_cdp_session(page)
            tab = Tab(page, cdp, sess._next_tab_id)
            sess._next_tab_id += 1
            sess.tabs.append(tab)
            self._wire_frame_handler(sess, cdp)
            self._wire_downloads(sess, page)
            if url:
                ok, norm = _validate_url(url)
                if ok:
                    await page.goto(norm, wait_until="domcontentloaded")
            if switch:
                await self._activate(sess, len(sess.tabs) - 1)
            sess.touch()
            return {"index": sess.active, "tabs": len(sess.tabs)}

    async def switch_tab(self, sess, index):
        async with sess.lock:
            if not (0 <= index < len(sess.tabs)) or index == sess.active:
                return await self.tabs_info(sess)
            await self._activate(sess, index)
            sess.touch()
            return await self.tabs_info(sess)

    async def close_tab(self, sess, index):
        async with sess.lock:
            if not (0 <= index < len(sess.tabs)) or len(sess.tabs) <= 1:
                return await self.tabs_info(sess)  # never close the last tab
            closing_active = index == sess.active
            tab = sess.tabs.pop(index)
            try:
                await tab.page.close()
            except Exception:
                pass
            # Fix up the active index after the removal.
            if closing_active:
                sess.active = min(index, len(sess.tabs) - 1)
                sess.screencasting = False  # old cast died with the page
                await self._activate(sess, sess.active, force=True)
            elif index < sess.active:
                sess.active -= 1
            sess.touch()
            return await self.tabs_info(sess)

    async def _activate(self, sess, index, force=False):
        """Make `index` the active tab and move the screencast to it."""
        if not force and index == sess.active:
            return
        if sess.screencasting:
            await self.stop_screencast(sess)  # stops the OLD active tab's cast
        sess.active = index
        try:
            await sess.page.bring_to_front()
        except Exception:
            pass
        if sess.viewers:
            await self.start_screencast(sess)  # starts the NEW active tab's cast

    async def tabs_info(self, sess):
        out = []
        for i, t in enumerate(sess.tabs):
            try:
                title = await t.page.title()
                url = t.page.url
            except Exception:
                title, url = "", ""
            out.append({"index": i, "title": title[:60], "url": url,
                        "active": i == sess.active})
        return {"active": sess.active, "count": len(sess.tabs), "tabs": out}

    async def stop_session(self, tenant):
        sess = self.sessions.pop(tenant, None)
        if not sess:
            return
        for ws in list(sess.viewers):
            try:
                await ws.close()
            except Exception:
                pass
        try:
            await sess.context.close()
        except Exception as e:
            logger.debug("context close error tenant=%s: %s", tenant, e)
        logger.info("session stopped tenant=%s", tenant)

    # ── navigation + actions ──────────────────────────────────────────────────
    async def _navigate(self, sess: Session, url: str):
        ok, norm = _validate_url(url)
        if not ok:
            raise web.HTTPBadRequest(reason=f"navigation refused: {norm}")
        await sess.page.goto(norm, wait_until="domcontentloaded")
        sess.touch()

    async def do_action(self, sess: Session, body: dict):
        action = (body.get("action") or "").lower()
        # Tab actions (#154 P5) manage their own per-session lock — dispatch them
        # BEFORE taking the page lock below (asyncio.Lock isn't reentrant).
        if action == "new_tab":
            return await self.new_tab(sess, body.get("url"),
                                      switch=body.get("switch", True))
        if action == "switch_tab":
            return await self.switch_tab(sess, int(body.get("index", 0)))
        if action == "close_tab":
            return await self.close_tab(sess, int(body.get("index", 0)))
        async with sess.lock:
            page = sess.page
            if action == "goto":
                await self._navigate(sess, body.get("url", ""))
            elif action == "click":
                if body.get("selector"):
                    await page.click(body["selector"], timeout=NAV_TIMEOUT_MS)
                elif "x" in body and "y" in body:
                    await page.mouse.click(float(body["x"]), float(body["y"]))
                else:
                    raise web.HTTPBadRequest(reason="click needs selector or x/y")
            elif action == "type":
                sel = body.get("selector")
                text = body.get("text", "")
                if sel:
                    if body.get("clear"):
                        await page.fill(sel, "")
                    await page.type(sel, text, delay=20)
                else:
                    await page.keyboard.type(text, delay=20)
            elif action == "key":
                await page.keyboard.press(body.get("key", "Enter"))
            elif action == "scroll":
                amount = int(body.get("amount", 500))
                if body.get("direction") == "up":
                    amount = -amount
                await page.mouse.wheel(0, amount)
            elif action == "back":
                await page.go_back(wait_until="domcontentloaded")
            elif action == "forward":
                await page.go_forward(wait_until="domcontentloaded")
            elif action == "reload":
                await page.reload(wait_until="domcontentloaded")
            elif action == "wait":
                if body.get("selector"):
                    await page.wait_for_selector(
                        body["selector"], timeout=int(body.get("timeout", 10000)))
                else:
                    await page.wait_for_timeout(int(body.get("ms", 1000)))
            else:
                raise web.HTTPBadRequest(reason=f"unknown action: {action}")
            sess.touch()
        return {"ok": True, "url": page.url, "title": await _safe_title(page)}

    async def extract_dom(self, sess: Session):
        """Simplified page model for the agent: text + links + inputs + buttons."""
        sess.touch()
        return await sess.page.evaluate(
            """() => {
                const clip = (s, n) => (s || '').trim().slice(0, n);
                const vis = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                const links = [...document.querySelectorAll('a[href]')]
                    .filter(vis).slice(0, 60).map((a, i) => ({
                        text: clip(a.innerText, 80), href: a.href, index: i + 1,
                    })).filter(l => l.text);
                const inputs = [...document.querySelectorAll('input,textarea,select')]
                    .filter(vis).slice(0, 40).map(el => ({
                        type: el.type || el.tagName.toLowerCase(),
                        id: el.id || null, name: el.name || null,
                        placeholder: el.placeholder || null,
                    }));
                const btnSel = el => el.id ? '#' + CSS.escape(el.id)
                    : (el.name ? `[name="${el.name}"]` : null);
                const buttons = [...document.querySelectorAll(
                        'button,[role=button],input[type=submit],input[type=button]')]
                    .filter(vis).slice(0, 40).map(el => ({
                        text: clip(el.innerText || el.value, 60),
                        selector: btnSel(el),
                    })).filter(b => b.text);
                return {
                    url: location.href,
                    title: document.title,
                    text: clip(document.body ? document.body.innerText : '', 8000),
                    links, inputs, buttons,
                };
            }"""
        )

    async def screenshot(self, sess: Session, full: bool):
        sess.touch()
        return await sess.page.screenshot(full_page=full, type="png")

    async def egress_ip(self, sess: Session) -> dict:
        """Probe the session's real egress IP (#154 Phase 4 verification).

        Uses the context's own APIRequestContext, so the request goes out the
        SAME path (and proxy) the live browser uses — this is the proof the
        residential proxy is actually masking. Does NOT touch the user's page.
        """
        sess.touch()
        info = {"proxied": sess.proxied, "ip": None, "country": None, "error": None}
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        # ipify is header-permissive; ipwho.is enriches with geo. Try both.
        try:
            resp = await sess.context.request.get(
                "https://api.ipify.org?format=json", headers=headers, timeout=15000)
            if resp.ok:
                info["ip"] = (await resp.json()).get("ip")
                sess.egress_ip = info["ip"]
            else:
                info["error"] = f"probe status {resp.status}"
        except Exception as e:
            info["error"] = str(e)[:200]
        # Best-effort geo enrichment (never fatal).
        if info["ip"]:
            try:
                geo = await sess.context.request.get(
                    f"https://ipwho.is/{info['ip']}", headers=headers, timeout=10000)
                if geo.ok:
                    g = await geo.json()
                    info["country"] = g.get("country_code") or g.get("country")
                    info["city"] = g.get("city")
            except Exception:
                pass
        return info

    # ── user input passthrough (#154 Phase 3) ─────────────────────────────────
    async def apply_user_event(self, sess: Session, evt: dict):
        """Apply a viewer-originated input event to the page.

        The human watching the live stream can click/type/scroll into it; those
        events arrive on the viewer WS and land here. Runs under the SAME
        per-session lock as agent actions so a user click and an agent action
        never interleave mid-operation. Coordinates are already page-space
        (the viewer maps canvas→page using the CDP screencast metadata).
        Rate-limited by the caller. Fail-soft: a bad event never kills the WS.
        """
        etype = evt.get("type", "")
        page = sess.page
        async with sess.lock:
            if etype == "user_click":
                x, y = float(evt.get("x", 0)), float(evt.get("y", 0))
                await page.mouse.click(x, y)
            elif etype == "user_move":
                await page.mouse.move(float(evt.get("x", 0)), float(evt.get("y", 0)))
            elif etype == "user_type":
                await page.keyboard.type(str(evt.get("text", ""))[:2000], delay=10)
            elif etype == "user_key":
                await page.keyboard.press(str(evt.get("key", "Enter"))[:24])
            elif etype == "user_scroll":
                await page.mouse.wheel(0, int(evt.get("dy", 0)))
            else:
                return
            sess.touch()

    # ── screencast relay ──────────────────────────────────────────────────────
    async def _dispatch_frame(self, sess: Session, params: dict):
        data = params.get("data")
        session_id = params.get("sessionId")
        # ACK so Chromium keeps sending frames.
        try:
            await sess.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:
            return
        if not sess.viewers:
            return
        payload = json.dumps({
            "type": "frame", "data": data,
            "meta": params.get("metadata", {}),
        })
        dead = []
        for ws in sess.viewers:
            try:
                await ws.send_str(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            sess.viewers.discard(ws)

    async def start_screencast(self, sess: Session):
        if sess.screencasting:
            return
        await sess.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": SCREENCAST_QUALITY,
            "maxWidth": DEFAULT_W, "maxHeight": DEFAULT_H, "everyNthFrame": 1,
        })
        sess.screencasting = True

    async def stop_screencast(self, sess: Session):
        if not sess.screencasting:
            return
        try:
            await sess.cdp.send("Page.stopScreencast")
        except Exception:
            pass
        sess.screencasting = False

    # ── reaper ────────────────────────────────────────────────────────────────
    async def reaper_loop(self):
        while True:
            await asyncio.sleep(30)
            for tenant, sess in list(self.sessions.items()):
                if sess.idle_s > IDLE_TIMEOUT_S:
                    logger.info("reaping idle session tenant=%s idle=%.0fs",
                                tenant, sess.idle_s)
                    await self.stop_session(tenant)


svc = BrowseService()


# ── HTTP layer ──────────────────────────────────────────────────────────────
def _auth_ok(request) -> bool:
    if not SERVICE_KEY:
        return True  # unset → open (single-node/dev); prod sets the key
    return request.headers.get("X-Browse-Key", "") == SERVICE_KEY


def _require_auth(request):
    if not _auth_ok(request):
        raise web.HTTPUnauthorized(reason="bad or missing X-Browse-Key")


def _get_session(tenant: str) -> Session:
    sess = svc.sessions.get(tenant)
    if not sess:
        raise web.HTTPNotFound(reason=f"no active session for tenant {tenant}")
    return sess


async def h_health(request):
    return web.json_response({
        "ok": True,
        "sessions": len(svc.sessions),
        "max_sessions": MAX_SESSIONS,
        "mem_percent": psutil.virtual_memory().percent,
        "proxy_configured": bool(PROXY_SERVER),
        "proxy_sticky": PROXY_STICKY if PROXY_SERVER else None,
    })


async def h_start(request):
    _require_auth(request)
    body = await request.json()
    tenant = (body.get("tenant") or "").strip()
    if not tenant:
        raise web.HTTPBadRequest(reason="tenant required")
    if psutil.virtual_memory().percent > MEM_GUARD_PCT and tenant not in svc.sessions:
        raise web.HTTPServiceUnavailable(reason="server memory pressure — try later")
    ok, norm = _validate_url(body.get("url", "about:blank")
                             if body.get("url") else "about:blank")
    url = body.get("url") or "about:blank"
    width = int(body.get("width", DEFAULT_W))
    height = int(body.get("height", DEFAULT_H))
    proxy = body.get("proxy") or None
    sess = await svc.start_session(tenant, url, width, height, proxy)
    return web.json_response({
        "ok": True, "tenant": tenant,
        "url": sess.page.url, "title": await _safe_title(sess.page),
    })


async def h_action(request):
    _require_auth(request)
    body = await request.json()
    sess = _get_session((body.get("tenant") or "").strip())
    return web.json_response(await svc.do_action(sess, body))


async def h_screenshot(request):
    _require_auth(request)
    sess = _get_session(request.query.get("tenant", "").strip())
    png = await svc.screenshot(sess, full=request.query.get("full") == "1")
    return web.Response(body=png, content_type="image/png")


async def h_dom(request):
    _require_auth(request)
    sess = _get_session(request.query.get("tenant", "").strip())
    dom = await svc.extract_dom(sess)
    # Session-level context so a single /session/dom call gives the agent
    # everything for [BROWSE_STATE] (#154 P5): tabs + last captured download.
    dom["tab_count"] = len(sess.tabs)
    dom["active_tab"] = sess.active
    dom["last_download"] = sess.last_download
    return web.json_response(dom)


async def h_status(request):
    _require_auth(request)
    sess = _get_session(request.query.get("tenant", "").strip())
    return web.json_response({
        "tenant": sess.tenant, "url": sess.page.url,
        "title": await _safe_title(sess.page),
        "idle_s": round(sess.idle_s, 1),
        "viewers": len(sess.viewers),
        "screencasting": sess.screencasting,
        "proxied": sess.proxied,
        "egress_ip": sess.egress_ip,
        "tab_count": len(sess.tabs),
        "active_tab": sess.active,
        "last_download": sess.last_download,
    })


async def h_tabs(request):
    """List the session's tabs (for the viewer tab strip, #154 P5)."""
    _require_auth(request)
    sess = _get_session(request.query.get("tenant", "").strip())
    return web.json_response(await svc.tabs_info(sess))


async def h_download(request):
    """Stream a captured download so OVU can save it into the tenant's uploads
    (#154 P5). Serves the session's most recent download by default; a `name`
    query selects a specific file. Path-guarded to the tenant's staging dir."""
    _require_auth(request)
    tenant = request.query.get("tenant", "").strip()
    sess = _get_session(tenant)
    ddir = _downloads_dir(tenant).resolve()
    name = request.query.get("name") or sess.last_download
    if not name:
        raise web.HTTPNotFound(reason="no download captured")
    target = (ddir / os.path.basename(name)).resolve()
    if ddir not in target.parents or not target.is_file():
        raise web.HTTPNotFound(reason="download not found")
    return web.FileResponse(target, headers={
        "Content-Disposition": f'attachment; filename="{target.name}"'})


async def h_ip(request):
    """Return the session's real egress IP — proof the proxy is (or isn't)
    masking. Also usable as a light 'is the residential proxy working' check."""
    _require_auth(request)
    sess = _get_session(request.query.get("tenant", "").strip())
    return web.json_response(await svc.egress_ip(sess))


async def h_stop(request):
    _require_auth(request)
    body = await request.json()
    await svc.stop_session((body.get("tenant") or "").strip())
    return web.json_response({"ok": True})


async def h_stream(request):
    # Auth via header OR ?key= (WebSocket clients can't always set headers).
    if SERVICE_KEY and request.query.get("key", "") != SERVICE_KEY \
            and request.headers.get("X-Browse-Key", "") != SERVICE_KEY:
        raise web.HTTPUnauthorized(reason="bad or missing key")
    tenant = request.query.get("tenant", "").strip()
    sess = _get_session(tenant)
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    sess.viewers.add(ws)
    await svc.start_screencast(sess)
    logger.info("viewer connected tenant=%s viewers=%d", tenant, len(sess.viewers))
    # Rate-limit user input so a mash of events can't peg the browser or starve
    # the agent's own actions (they share the per-session lock). ~25 events/s.
    _last_evt = 0.0
    _MIN_EVT_GAP = 0.04
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                # Viewer events: ping (keepalive) + user input passthrough (#154 P3).
                try:
                    evt = json.loads(msg.data)
                except Exception:
                    continue
                etype = evt.get("type", "")
                if etype == "ping":
                    await ws.send_str(json.dumps({"type": "pong"}))
                elif etype.startswith("user_"):
                    now = time.time()
                    # Always allow type/key (never drop keystrokes); throttle
                    # only high-frequency pointer events.
                    if etype in ("user_move", "user_scroll") and now - _last_evt < _MIN_EVT_GAP:
                        continue
                    _last_evt = now
                    try:
                        await svc.apply_user_event(sess, evt)
                    except Exception as e:
                        logger.debug("user event %s failed: %s", etype, e)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        sess.viewers.discard(ws)
        if not sess.viewers:
            await svc.stop_screencast(sess)
        logger.info("viewer gone tenant=%s viewers=%d", tenant, len(sess.viewers))
    return ws


async def on_startup(app):
    await svc.startup()
    app["reaper"] = asyncio.create_task(svc.reaper_loop())


async def on_cleanup(app):
    app["reaper"].cancel()
    await svc.shutdown()


def make_app():
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.add_routes([
        web.get("/health", h_health),
        web.post("/session/start", h_start),
        web.post("/session/action", h_action),
        web.get("/session/screenshot", h_screenshot),
        web.get("/session/dom", h_dom),
        web.get("/session/status", h_status),
        web.get("/session/tabs", h_tabs),
        web.get("/session/download", h_download),
        web.get("/session/ip", h_ip),
        web.post("/session/stop", h_stop),
        web.get("/session/stream", h_stream),
    ])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    if not SERVICE_KEY:
        logger.warning("BROWSE_SERVICE_KEY unset — running OPEN (dev mode)")
    web.run_app(make_app(), host="0.0.0.0", port=PORT)
