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

export const MODE = 'fixture';            // 'fixture' | 'live'
const BASE = '/social-api/api';           // :6350 behind nginx, same-origin
const FIXTURES = 'data';

/* tenant: in live mode the server derives it from the HOSTNAME and rejects a
   mismatched ?tenant= (requireTenant). We never send it as a trust token —
   it is display-only here. */
export function tenant() {
  const host = location.hostname.split('.')[0];
  return (MODE === 'live' && host && host !== 'localhost') ? host : 'demo';
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
  'seo.snapshots':   { live:`${BASE}/seo/snapshots`,      fixture:'seo.json',           ready:'live' },
  'seo.reports':     { live:`${BASE}/seo/reports`,        fixture:'seo-reports.json',   ready:'live' },
  'leads.stats':     { live:`${BASE}/leads/stats`,        fixture:'leads.json',         ready:'live' },
  'leads.list':      { live:`${BASE}/leads`,              fixture:'leads-list.json',    ready:'live' },
  'crm.pipeline':    { live:`${BASE}/crm/pipeline`,       fixture:'crm.json',           ready:'partial',
                       note:'15 of 24 tenants keyed; 3 hold real data' },
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
  'jobs.list':       { live:null,                         fixture:'jobs.json',          ready:'none',
                       note:'quoting is live for one tenant with 2 jobs' },
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
  'team.roster':  { live:null, fixture:'team.json', ready:'none', note:'no team/employee directory exists yet (Account > Staff logins holds the login table)' },
  'team.threads': { live:null, fixture:'team.json', ready:'none', note:'no team messaging backend exists; chat is an in-session demo until wired' },
  'team.files':   { live:null, fixture:'team.json', ready:'none', note:'no shared file store exists; uploads are local-preview only until wired' },

  /* payments (2026-09-30, Danielle's ask) — the "get paid" surface, distinct
     from Finances (the accounting view). A Stripe-class processor is the
     intended backing: links charge cards, invoices carry Pay Now, payouts
     batch to the bank. Host wires it when the client picks a processor. */
  'payments.summary':  { live:null, fixture:'payments.json', ready:'none', note:'no payment processor connected yet' },
  'payments.invoices': { live:null, fixture:'payments.json', ready:'none', note:'no payable-invoice system exists (Finances > Invoices is read-only accounting)' },
  'payments.links':    { live:null, fixture:'payments.json', ready:'none', note:'no hosted-payment-link system exists' },
  'payments.payouts':  { live:null, fixture:'payments.json', ready:'none', note:'no payout/banking integration exists' }
};

const cache = new Map();

/** Fetch a logical key. Never throws — returns {ok,data,ready,note,error}. */
export async function get(key, params = {}) {
  const src = SOURCES[key];
  if (!src) return { ok:false, data:null, ready:'none', note:`unknown source "${key}"` };

  const ck = key + JSON.stringify(params);
  if (cache.has(ck)) return cache.get(ck);

  let result;
  if (src.ready === 'none' && !src.fixture) {
    result = { ok:false, data:null, ready:'none', note:src.note || 'no backing system' };
  } else {
    const url = MODE === 'live' && src.live
      ? `${src.live}?${new URLSearchParams({ tenant: tenant(), ...params })}`
      : `${FIXTURES}/${src.fixture}`;
    try {
      const res = await fetch(url, { credentials: MODE === 'live' ? 'same-origin' : 'omit' });
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

export function clearCache() { cache.clear(); }
