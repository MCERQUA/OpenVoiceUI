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

// 'fixture' | 'live'. globalThis.JAMDASH_MODE exists ONLY so tests/tools (node harness, capture
// mode) can drive the production code path; the browser never sets it, so the app stays 'fixture'.
export const MODE = globalThis.JAMDASH_MODE === 'live' ? 'live' : 'fixture';
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
  // explicit tenant (tests / the internal lane / a host-rewritten vhost) wins over the host label
  const forced = globalThis.JAMDASH_TENANT ||
    (typeof location !== 'undefined' && new URLSearchParams(location.search).get('tenant'));
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
  // job fetch carries include=contact: the server resolves the join server-side, which is the
  // only reliable way to match a job's contact_id to a contact record (host-measured 2026-11-18:
  // without it, every contact showed jobs:0 despite real contact_id values on all jobs).
  'crm.pipeline':    { live:[`${BIZ_BASE}/contact`, [`${BIZ_BASE}/job`, { include: 'contact' }]], fixture:'crm.json', ready:'live' },
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
  // include=contact for the same reason as crm.pipeline above (host-measured 2026-11-18: every
  // job showed customer "—" with no include, even though contact_id pointed at a real contact).
  'jobs.list':       { live:[[`${BIZ_BASE}/job`, { include: 'contact' }]], fixture:'jobs.json', ready:'live' },
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
  'inventory.products':{ live:null, fixture:null, ready:'none', note:'no product/inventory catalog exists' },
  'inventory.stock':  { live:null, fixture:null, ready:'none', note:'no stock-level tracking exists' },
  'schedule.appointments': { live:null, fixture:null, ready:'none', note:'no booking/appointments system exists (distinct from Content > Calendar, which is the marketing content calendar)' },
  'support.tickets':  { live:null, fixture:null, ready:'none', note:'a ticket system exists elsewhere in JamBot infra but is not wired into JamDash yet' },
  'account.staffSchedule': { live:null, fixture:null, ready:'none', note:'no staff scheduling/payroll system exists' },
  'account.locations':{ live:null, fixture:null, ready:'none', note:'business profile models exactly one location today; multi-location is not built' },

  /* team comms (2026-09-30, Danielle's ask) — fixtures show the structure:
     roster, employer↔employee threads with attachments, shared-file index.
     A real build needs message persistence, file storage and an
     employee-facing surface (login or SMS/email bridge) — host wires it. */
  'team.roster':  { live:`${BIZ_BASE}/person`, fixture:'team.json', ready:'live' },
  'team.threads': { live:null, fixture:'team.json', ready:'none', note:'no team messaging collection in the biz/v1 SDK; chat is an in-session demo until a messaging backend is wired' },
  'team.files':   { live:null, fixture:'team.json', ready:'none', note:'no shared file-store collection in the biz/v1 SDK (document exists but is not a chat-attachment store); uploads are local-preview only until wired' },

  /* payments (2026-09-30, Danielle's ask) — the "get paid" surface, distinct
     from Finances (the accounting view). A Stripe-class processor is the
     intended backing: links charge cards, invoices carry Pay Now, payouts
     batch to the bank. Host wires it when the client picks a processor. */
  'payments.summary':  { live:null, fixture:'payments.json', ready:'partial',
                       note:'derived from invoice/payment, not yet wired — collectedThisMonth/pending/overdue could ' +
                            'be aggregated from those two biz/v1 collections, but nextPayout/avgDaysToPay/sharePaidByCard ' +
                            'have no backing (no payout/banking collection), so the tile set stays fixture-only for now' },
  'payments.invoices': { live:`${BIZ_BASE}/invoice`, fixture:'payments.json', ready:'live' },
  'payments.links':    { live:null, fixture:'payments.json', ready:'none',
                       note:'no payment-link collection in the biz/v1 SDK (company/contact/lead/job/estimate/line_item/' +
                            'invoice/payment/cost_entry/time_entry/product/stock_move/document/event/task/person)' },
  'payments.payouts':  { live:null, fixture:'payments.json', ready:'none',
                       note:'no payout/banking collection in the biz/v1 SDK — payment records exist but nothing models a bank deposit batch' }
};

const cache = new Map();

async function liveJson(url, params = {}) {
  // /api/biz/v1 derives tenant from the container (JAMBOT_TENANT) and REFUSES any request
  // naming one (400 tenant_param_refused, routes/biz.py L559) — a jail-cell rule, not a bug.
  // The :6350 BASE still wants ?tenant= (it is shared infra, not per-container-scoped).
  const isBiz = url.startsWith(BIZ_BASE);
  const qp = isBiz ? { ...params } : { tenant: tenant(), ...params };
  const res = await fetch(`${url}?${new URLSearchParams(qp)}`,
                          { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
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
    return { ok:false, data:null, ready:src.ready === 'live' ? 'partial' : src.ready,
             note: src.note || 'no live source connected yet' };
  }
  if (!ADAPTERS[key]) {
    return { ok:false, data:null, ready:src.ready,
             note: `${src.note ? src.note + ' · ' : ''}live endpoint exists but has no adapter yet` };
  }
  try {
    const extra = {};
    if (src.needsDomain) {
      const d = params.domain || await tenantDomain();
      if (!d) throw new Error('no site domain known for this tenant');
      extra.domain = d;
    }
    const specs = (Array.isArray(src.live) ? src.live : [src.live])
      .map(s => (Array.isArray(s) ? s : [s, {}]));
    const raws = await Promise.all(specs.map(([u, p]) => liveJson(u, { ...p, ...extra, ...params })));
    const data = normalize(key, raws.length === 1 ? raws[0] : raws);
    return { ok:true, data, ready:src.ready, note:src.note };
  } catch (err) {
    // HTTP failure or a ShapeError from the adapter: UNKNOWN with the reason, never a wrong number.
    return { ok:false, data:null, ready:src.ready, note:src.note, error:String(err.message || err) };
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
  } else if (src.ready === 'none' && !src.fixture) {
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
  return {
    value: known ? value : null,
    display: known ? (display ?? String(value)) : '—',
    state: known ? (state || 'ok') : 'unknown',
    label, icon, delta: known ? delta : null, link,
    note: known ? note : (note || 'No data source connected yet'),
    source
  };
}

/** Build an unknown metric straight from a failed/absent source. */
export function unknownMetric(label, res, extra = {}) {
  return metric({
    value: null, state: 'unknown', label,
    note: res?.error ? `Can't read this right now (${res.error})` : (res?.note || 'Not connected yet'),
    ...extra
  });
}

export function badges() {
  return { yourTurn: 25, approvals: 3, newLeads: 12 };   // fixture; live = counts from the feeds
}

export function clearCache() { cache.clear(); domainP = null; }
