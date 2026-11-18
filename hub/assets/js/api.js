/* ==========================================================================
   api.js — THE DATA ADAPTER. This is the plug-in point.

   Every view asks for data by a LOGICAL KEY ('leads.stats'), never by URL.
   Each key declares both a fixture path (today) and the real endpoint
   (tomorrow). Flipping MODE to 'live' switches the whole app over; no view
   changes. Keys whose `live` is null are the ones with no backing system yet —
   they are honestly marked and will render as `unknown`, not as zero.

   Backing-system status as measured 2026-08-25 — see
   docs/jambot/client-dashboard-architecture.md for the evidence behind each.
   ========================================================================== */

import { normalize, ADAPTERS } from './adapters.js';

/* MODE — 'fixture' | 'live', decided ONCE at load from where the app is served:
     - live    on a tenant's own address under /hub/ (https://<tenant>.jam-bot.com/hub/), where the
               OVU container serves the Hub and /api/biz/v1 is same-origin with the owner's Clerk session.
     - fixture everywhere else: jamdash.jam-bot.com (the design sandbox), localhost, file previews.
   globalThis.JAMDASH_MODE ('live'|'fixture') exists ONLY so tests/tools (node harness, Playwright
   init script) can drive a mode explicitly; the browser itself never sets it. No URL parameter can
   switch a real page into live mode or out of it. */
const SANDBOX_HOSTS = new Set(['jamdash.jam-bot.com', 'www.jam-bot.com', 'jam-bot.com']);
export function detectMode(loc = (typeof location !== 'undefined' ? location : null)) {
  const forced = globalThis.JAMDASH_MODE;
  if (forced === 'live' || forced === 'fixture') return forced;
  if (!loc) return 'fixture';
  const host = String(loc.hostname || '').toLowerCase();
  const path = String(loc.pathname || '');
  const tenantHost = /^[a-z0-9-]{1,40}\.jam-bot\.com$/.test(host) && !SANDBOX_HOSTS.has(host);
  const underHub = path === '/hub' || path.startsWith('/hub/');
  return tenantHost && underHub ? 'live' : 'fixture';
}
export const MODE = detectMode();
export const isLive = MODE === 'live';
const BASE = globalThis.JAMDASH_API_BASE || '/social-api/api';   // :6350 behind nginx, same-origin
// Separate business-record backend (jb.js / /jb/v1/sdk.md): Clerk-session auth, same-origin,
// company/contact/lead/job/estimate/line_item/invoice/payment/cost_entry/time_entry/product/
// stock_move/document/event/task/person. Distinct from BASE above — leave BASE alone.
const BIZ_BASE = globalThis.JAMDASH_BIZ_API_BASE || '/api/biz/v1';
const FIXTURES = 'data';

/* tenant: in live mode the server derives it from the HOSTNAME and rejects a
   mismatched ?tenant= (requireTenant). We never send it as a trust token —
   it is display-only here. */
export function tenant() {
  if (MODE !== 'live') return 'demo';
  // explicit tenant for tests / tools only; the browser derives it from the host label
  const forced = globalThis.JAMDASH_TENANT;
  if (forced && /^[a-z0-9-]{1,32}$/.test(forced)) return forced;
  const host = typeof location !== 'undefined' ? location.hostname.split('.')[0] : '';
  return host && host !== 'localhost' ? host : 'demo';
}

/* --------------------------------------------------------------------------
   THE REGISTRY. ready:
     'live'    backing system verified serving per-tenant JSON today
     'partial' real data exists but coverage is thin or needs an indexer
     'none'    nothing behind it yet — must render unknown, never a number
   -------------------------------------------------------------------------- */
export const SOURCES = {
  /* THE HUB SUMMARY (OVU services/biz/hub.py, GET /api/biz/v1/hub/summary): one call, a tile per
     section (customers jobs money team inventory leads seo site), each with counts + verdict
     ok|warn|bad|unknown + state live|empty|unreachable|not_built|sandbox + a plain reason.
     Live mode only: the fixture Home keeps its demo layout (fixture:null). */
  'hub.summary':     { live:`${BIZ_BASE}/hub/summary`,    fixture:null,                 ready:'live' },
  // the business's own name + brand colours (biz/v1 company record); display only
  'business.profile':{ live:[[`${BIZ_BASE}/company`, { limit: 1 }]], fixture:null,    ready:'live' },
  'account.state':   { live:null,                         fixture:'account-state.json', ready:'partial',
                       note:'ACCOUNT-STATE.md is markdown; needs a JSON emitter (Phase 0)' },
  'course.state':    { live:`${BASE}/course/state`,       fixture:'course.json',        ready:'live' },
  'course.next':     { live:`${BASE}/course/next`,        fixture:'course-next.json',   ready:'live' },
  'seo.snapshots':   { live:`${BASE}/seo/snapshots`,      fixture:'seo.json',           ready:'live',
                       needsDomain:true },
  'seo.reports':     { live:`${BASE}/seo/reports`,        fixture:'seo-reports.json',   ready:'live' },
  // leads.stats = stats + the newest 4 rows (the view's "recent" list); two real endpoints, one view model
  'leads.stats':     { live:[`${BASE}/leads/stats`, [`${BASE}/leads`, { limit: 4 }]],
                                                          fixture:'leads.json',         ready:'live' },
  'leads.list':      { live:[[`${BASE}/leads`, { limit: 50 }]],
                                                          fixture:'leads-list.json',    ready:'live' },
  // kanban pipeline, built from two biz/v1 collections (contact for names, job for stage/status)
  'crm.pipeline':    { live:[[`${BIZ_BASE}/contact`, { limit: 200, sort: '-updated_at' }],
                             [`${BIZ_BASE}/job`, { limit: 200, sort: '-updated_at' }],
                             `${BIZ_BASE}/contact/aggregate`, `${BIZ_BASE}/job/aggregate`],
                                                          fixture:'crm.json',         ready:'live' },
  'social.pending':  { live:`${BASE}/social/pending`,     fixture:'social.json',        ready:'partial',
                       note:'queue is real; publishing is down (OneUp key absent)' },
  'media.state':     { live:null,                         fixture:'media.json',         ready:'partial',
                       note:'media-studio/state.json per tenant; 9 of 18 have a manifest' },
  'brand.kit':       { live:null,                         fixture:'brand.json',         ready:'partial',
                       note:'4 fragmented stores; needs a unifier' },
  'articles.index':  { live:null,                         fixture:'articles.json',      ready:'none',
                       note:'~92 files on disk, no index exists' },
  'website.builds':  { live:`${BASE}/canvas/data/builds`, fixture:'website.json',       ready:'partial',
                       note:'only 2 tenants have build state' },
  'calendar.items':  { live:null,                         fixture:'calendar.json',      ready:'none',
                       note:'NO calendar exists anywhere — zero future-dated items fleet-wide' },
  'jobs.list':       { live:[[`${BIZ_BASE}/job`, { limit: 200, sort: '-updated_at', include: 'contact' }]],
                                                          fixture:'jobs.json',          ready:'live' },
  'billing.summary': { live:null,                         fixture:null,                 ready:'none',
                       note:'no client billing system exists' },
  'traffic.gsc':     { live:null,                         fixture:null,                 ready:'none',
                       note:'Search Console / Analytics were never wired — no credentials' },

  /* general-business additions (2026-09-28) — every key below is ready:'none'
     because nothing on the box backs any of them yet. Added so the structure
     is visible and views never need to change when a real system lands. */
  'ads.spend':        { live:null, fixture:null, ready:'none', note:'no ad-platform integration exists (this is paid spend, distinct from Get Found, which is organic/SEO)' },
  'ads.google':        { live:null, fixture:null, ready:'none', note:'no Google Ads API connection exists' },
  'ads.facebook':      { live:null, fixture:null, ready:'none', note:'no Meta Ads API connection exists' },
  'ads.instagram':     { live:null, fixture:null, ready:'none', note:'no Meta Ads API connection exists (Instagram ads run through the same Meta Ads platform as Facebook)' },
  'finance.pnl':      { live:null, fixture:null, ready:'none', note:'no accounting/POS integration exists' },
  'finance.invoices': { live:null, fixture:null, ready:'none', note:'no client-side invoicing system exists (distinct from Account > Billing, which is JamBot billing the client)' },
  'finance.expenses': { live:null, fixture:null, ready:'none', note:'no expense tracking system exists' },
  'finance.vendors':  { live:null, fixture:null, ready:'none', note:'no vendor/supplier system exists' },
  'finance.tax':      { live:null, fixture:null, ready:'none', note:'no tax/compliance tracking exists' },
  'inventory.products':{ live:[[`${BIZ_BASE}/product`, { limit: 200, sort: 'name' }]], fixture:null, ready:'live',
                         note:'fixture mode: no product catalog in the demo; live: the business\'s own product records' },
  'inventory.stock':  { live:null, fixture:null, ready:'none', note:'no stock-level tracking exists' },
  'schedule.appointments': { live:null, fixture:null, ready:'none', note:'no booking/appointments system exists (distinct from Content > Calendar, which is the marketing content calendar)' },
  'support.tickets':  { live:null, fixture:null, ready:'none', note:'a ticket system exists elsewhere in JamBot infra but is not wired into JamDash yet' },
  'account.staffSchedule': { live:null, fixture:null, ready:'none', note:'no staff scheduling/payroll system exists' },
  'account.locations':{ live:null, fixture:null, ready:'none', note:'business profile models exactly one location today; multi-location is not built' },

  /* team comms (2026-09-30, Danielle's ask) — fixtures show the structure:
     roster, employer↔employee threads with attachments, shared-file index.
     A real build needs message persistence, file storage and an
     employee-facing surface (login or SMS/email bridge) — host wires it. */
  'team.roster':  { live:[[`${BIZ_BASE}/person`, { limit: 200 }]], fixture:'team.json', ready:'live' },
  'team.threads': { live:null, fixture:'team.json', ready:'none', note:'no team messaging collection in the biz/v1 SDK; chat is an in-session demo until a messaging backend is wired' },
  'team.files':   { live:null, fixture:'team.json', ready:'none', note:'no shared file-store collection in the biz/v1 SDK (document exists but is not a chat-attachment store); uploads are local-preview only until wired' },

  /* payments (2026-09-30, Danielle's ask) — the "get paid" surface, distinct
     from Finances (the accounting view). A Stripe-class processor is the
     intended backing: links charge cards, invoices carry Pay Now, payouts
     batch to the bank. Host wires it when the client picks a processor. */
  /* payments.summary: live = the hub summary's money section (server-side sums over EVERY invoice
     and payment, not a page of rows). nextPayout/avgDaysToPay/sharePaidByCard have no backing
     (no payout/banking collection) and come back null -> rendered "not connected". */
  'payments.summary':  { live:`${BIZ_BASE}/hub/summary`, fixture:'payments.json', ready:'partial',
                       note:'collected/awaiting/overdue come from your invoices and payments; payouts and ' +
                            'card share need a payment processor, which is not connected yet' },
  'payments.recent':   { live:[[`${BIZ_BASE}/payment`, { limit: 6, sort: '-paid_at' }]], fixture:null, ready:'live' },
  'payments.invoices': { live:[[`${BIZ_BASE}/invoice`, { limit: 200, sort: '-updated_at', include: 'contact' }]],
                       fixture:'payments.json', ready:'live' },
  'payments.links':    { live:null, fixture:'payments.json', ready:'none',
                       note:'no payment-link collection in the biz/v1 SDK (company/contact/lead/job/estimate/line_item/' +
                            'invoice/payment/cost_entry/time_entry/product/stock_move/document/event/task/person)' },
  'payments.payouts':  { live:null, fixture:'payments.json', ready:'none',
                       note:'no payout/banking collection in the biz/v1 SDK — payment records exist but nothing models a bank deposit batch' }
};

const cache = new Map();
const rawCache = new Map();   // one network read per URL+params per page load (hub summary feeds several keys)

/* Same-origin GET with the owner's Clerk session cookie.
   - /api/biz/v1 is NEVER sent a tenant: the backend takes it from its container and refuses any
     request that names one (400 tenant_param_refused).
   - the :6350 platform API (BASE) gets the display tenant, which its requireTenant checks against
     the hostname (a mismatch is refused there, so it is never a trust token).
   Errors carry a plain reason the views can show; HTTP status is kept on err.status. */
export class LiveError extends Error {
  constructor(message, status) { super(message); this.name = 'LiveError'; this.status = status; }
}
function plainHttp(status) {
  if (status === 401) return 'you are signed out — sign in again to see this';
  if (status === 403) return 'this account is not allowed to see this business';
  if (status === 404) return 'this part of the system is not switched on for your business yet';
  if (status === 501) return 'this part of the system is not built yet';
  if (status >= 500) return 'the server had a problem answering';
  return `the server answered ${status}`;
}
async function liveJson(url, params = {}) {
  const q = { ...params };
  if (url.startsWith(BIZ_BASE)) delete q.tenant; else q.tenant = tenant();
  for (const k of Object.keys(q)) if (q[k] === undefined || q[k] === null || q[k] === '') delete q[k];
  const qs = new URLSearchParams(q).toString();
  let res;
  try {
    res = await fetch(qs ? `${url}?${qs}` : url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  } catch (e) {
    throw new LiveError('the server could not be reached', 0);
  }
  if (!res.ok) throw new LiveError(plainHttp(res.status), res.status);
  try { return await res.json(); }
  catch (e) { throw new LiveError('the server answered with something that is not data', res.status); }
}

/* The tenant's site domain (seo.snapshots requires ?domain=). Same answer a real client gets:
   its active SEO project first, else the newest brand-report index entry. null = unknown. */
let domainP = null;
export function tenantDomain() {
  if (!domainP) domainP = (async () => {
    try {
      const p = await liveJson(`${BASE}/seo/projects`);
      const d = (p.projects || []).map(x => x.domain).find(Boolean);
      if (d) return d;
    } catch (_) { /* fall through to the report index */ }
    try {
      const r = await liveJson(`${BASE}/seo/reports`);
      const d = (r.reports || []).slice().sort((a, b) =>
        String(b.generatedAt || '').localeCompare(String(a.generatedAt || ''))).map(x => x.domain).find(Boolean);
      if (d) return d;
    } catch (_) { /* unknown */ }
    return null;
  })();
  return domainP;
}

/* LIVE MODE NEVER READS data/*.json (fixture-audit finding 5): a key with no live endpoint or no
   adapter renders unknown with its note, so a real client never sees the demo's hand-made data. */
async function getLive(key, src, params) {
  if (!src.live) {
    // live:null = nothing behind this key for a real business yet: "not connected", never fixture data
    return { ok:false, data:null, ready:'none', kind:'not_connected',
             note: src.note || 'Not connected yet' };
  }
  if (!ADAPTERS[key]) {
    return { ok:false, data:null, ready:src.ready, kind:'not_connected',
             note: `${src.note ? src.note + ' · ' : ''}live endpoint exists but has no adapter yet` };
  }
  try {
    const extra = {};
    if (src.needsDomain) {
      const d = params.domain || await tenantDomain();
      if (!d) throw new LiveError('no website is on file for this business yet', 0);
      extra.domain = d;
    }
    const specs = (Array.isArray(src.live) ? src.live : [src.live])
      .map(s => (Array.isArray(s) ? s : [s, {}]));
    const raws = await Promise.all(specs.map(([u, p]) => {
      const q = { ...p, ...extra, ...params };
      const rk = u + JSON.stringify(q);
      if (!rawCache.has(rk)) rawCache.set(rk, liveJson(u, q).catch(e => { rawCache.delete(rk); throw e; }));
      return rawCache.get(rk);
    }));
    const data = normalize(key, raws.length === 1 ? raws[0] : raws);
    return { ok:true, data, ready:src.ready, note:src.note, kind:'live' };
  } catch (err) {
    // HTTP failure or a ShapeError from the adapter: UNKNOWN with the reason, never a wrong number.
    return { ok:false, data:null, ready:src.ready, note:src.note, kind:'unreachable',
             status: err.status ?? null, error:String(err.message || err) };
  }
}

/** Fetch a logical key. Never throws — returns {ok,data,ready,note,error}. */
export async function get(key, params = {}) {
  const src = SOURCES[key];
  if (!src) return { ok:false, data:null, ready:'none', note:`unknown source "${key}"` };

  const ck = key + JSON.stringify(params);
  if (cache.has(ck)) return cache.get(ck);

  let result;
  if (MODE === 'live') {
    result = await getLive(key, src, params);
  } else if (!src.fixture) {   // nothing to show in the demo for this key (live-only keys included)
    result = { ok:false, data:null, ready:'none', note:src.note || 'no backing system' };
  } else {
    const url = `${FIXTURES}/${src.fixture}`;   // fixture mode only (live mode returned above)
    try {
      const res = await fetch(url, { credentials: 'omit' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      result = { ok:true, data: await res.json(), ready:src.ready, note:src.note };
    } catch (err) {
      // A failed read is UNKNOWN, never zero. This is the whole point.
      result = { ok:false, data:null, ready:src.ready, note:src.note, error:String(err.message || err) };
    }
  }
  cache.set(ck, result);
  return result;
}

/* --------------------------------------------------------------------------
   THE FOUR-VERDICT CONTRACT
   Every metric is ok | warn | bad | unknown. `unknown` renders grey WITH A
   REASON — never 0, never green. Carried over from the JamFlow KPI view, where
   the failure mode was a broken feed rendering as a confident zero.
   -------------------------------------------------------------------------- */
export function metric({ value, display, state, label, note, delta, source, link, icon }) {
  const known = value !== null && value !== undefined && state !== 'unknown';
  // A delta (e.g. "+18%") is comparison copy, not fetched data — callers that haven't actually
  // computed it from two real readings were writing it as hardcoded demo flavour. In live mode
  // that renders next to a REAL value as an invented trend, so it is dropped unless the caller
  // marks delta.computed === true (host-measured 2026-11-18, found on 9 call sites across views).
  const showDelta = known && delta && (MODE !== 'live' || delta.computed === true);
  return {
    value: known ? value : null,
    display: known ? (display ?? String(value)) : '—',
    state: known ? (state || 'ok') : 'unknown',
    label, icon, delta: showDelta ? delta : null, link,
    note: known ? note : (note || 'No data source connected yet'),
    source
  };
}

/** Build an unknown metric straight from a failed/absent source.
    A get() result's res.note is an internal SOURCES engineering note (e.g. "only 2 tenants have build
    state"), never client copy: never show it, same reasoning as liveBody()'s not_connected branch
    (danielle-desktop a81053c + host 96f9116). A hand-written { note: '...' } object (no `kind`, e.g.
    'Analytics isn't connected yet' or a hub summary section's reason) is client copy and keeps its words. */
export function unknownMetric(label, res, extra = {}) {
  const fromSource = !!res && typeof res === 'object' && 'kind' in res;
  return metric({
    value: null, state: 'unknown', label,
    note: res?.error ? `Can't read this right now: ${res.error}`
      : (fromSource ? 'Not connected yet' : (res?.note || 'Not connected yet')),
    ...extra
  });
}

/** Rail badges. Fixture: the demo counts. Live: only counts a real feed gave (new leads from the
    hub summary); anything unmeasured shows no badge at all, never a made-up number. */
export async function badges() {
  if (MODE !== 'live') return { yourTurn: 25, approvals: 3, newLeads: 12 };
  const hub = await get('hub.summary');
  const leads = hub.ok ? hub.data.sections.leads : null;
  const n = leads && leads.state === 'live' ? leads.counts.new : null;
  return { newLeads: Number.isInteger(n) && n > 0 ? n : null };
}

export function clearCache() { cache.clear(); rawCache.clear(); domainP = null; }
