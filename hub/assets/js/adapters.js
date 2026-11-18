/* ==========================================================================
   adapters.js — LIVE RESPONSE -> VIEW MODEL, one normalize() per logical key.

   The views were written against the view-model shapes in public/data/*.json.
   The real per-tenant API (:6350, behind /social-api/) serves raw records in a
   different shape (measured: docs/jambot/jamdash-fixture-audit-2026-11-07.md,
   0 of 9 live keys matched). These adapters are the ONLY place that knows both
   shapes, so a real client plugs in with zero section (view) changes.

   Rules (host, 2026-11-08, pledge 9d8637cd):
   - Pure functions, no fetch, no DOM, no browser storage. Testable in node.
   - Never invent a number. A field the API does not serve is UNBACKED: lists
     come back [], objects {}, scalars null (metric() renders those as unknown),
     and a row label with no source is '—' (the unknown glyph).
   - A raw response missing a field the adapter REQUIRES throws ShapeError;
     api.get() turns that into ok:false (rendered unknown with the reason),
     never a confident wrong value. This is the negative control.
   - Each adapter declares `provides` and `unbacked` so a test can prove
     provides ∪ unbacked == the fixture's key set (positive control).
   ========================================================================== */

export class ShapeError extends Error {
  constructor(key, path) { super(`shape: ${key} missing ${path}`); this.name = 'ShapeError'; }
}

/** Read a dotted path; throw ShapeError when absent (null is allowed: it is a served value). */
function need(key, obj, path) {
  let cur = obj;
  for (const p of path.split('.')) {
    if (cur === null || typeof cur !== 'object' || !(p in cur)) throw new ShapeError(key, path);
    cur = cur[p];
  }
  return cur;
}
function needArray(key, obj, path) {
  const v = need(key, obj, path);
  if (!Array.isArray(v)) throw new ShapeError(key, `${path} (array)`);
  return v;
}

/* presentation only: short stepper labels + icons per curriculum module id.
   Labels/icons are UI copy, not data, so they live here, not in the API. */
const MODULE_UI = {
  m0: ['Identity', 'badge'], m1: ['Website', 'language'], m2: ['Domain', 'lock'],
  m3: ['Google', 'storefront'], m4: ['Listings', 'list_alt'], m5: ['Trust', 'verified_user'],
  m6: ['Reviews', 'reviews'], m7: ['Social', 'share'], m8: ['Content', 'auto_stories'],
  m9: ['AI', 'smart_toy'], m10: ['Numbers', 'query_stats'], m11: ['Flywheel', 'autorenew']
};
const ui = id => MODULE_UI[id] || [String(id || ''), 'check_circle'];

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const MONTHS_LONG = ['January','February','March','April','May','June','July','August',
  'September','October','November','December'];

/** "today, 8:04am" | "yesterday" | "3 days ago" | "1 week ago" | "24 Aug". '—' when absent. */
export function when(iso, now = new Date()) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const day = 86400000;
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startToday) {
    const h = d.getHours(), m = String(d.getMinutes()).padStart(2, '0');
    return `today, ${((h + 11) % 12) + 1}:${m}${h < 12 ? 'am' : 'pm'}`;
  }
  const days = Math.ceil((startToday - t) / day);
  if (days <= 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  if (days < 14) return '1 week ago';
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}
const dateLabel = iso => { const d = new Date(iso); return isNaN(d) ? '—' : `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; };
const cap = s => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '—');
const fill = (text, vars) => String(text || '').replace(/\{\{\s*([A-Z_]+)\s*\}\}/g,
  (_, k) => (vars && vars[k] != null && vars[k] !== '' ? String(vars[k]) : 'your business'));
const firstPara = s => String(s || '').split(/\n\s*\n/)[0].trim();

/* ---------- course ---------- */

/** progress.modules (object keyed m0..mN) -> ordered [{id, ...counts}] following curriculum order. */
function moduleProgress(key, progress, order) {
  const pm = need(key, progress, 'modules');
  const ids = order && order.length ? order : Object.keys(pm);
  return ids.filter(id => pm[id]).map(id => ({ id, ...pm[id] }));
}
/** first non-recurring module that is not complete; falls back to the last one. */
function currentModule(mods) {
  const idx = mods.findIndex(m => !m.recurring && (m.pct ?? 0) < 100);
  return idx === -1 ? Math.max(0, mods.length - 1) : idx;
}
const sum = (mods, f) => mods.filter(m => !m.recurring).reduce((a, m) => a + (Number(m[f]) || 0), 0);

function courseState(raw, ctx = {}) {
  const K = 'course.state';
  const now = ctx.now || new Date();
  const modulesRaw = needArray(K, raw, 'modules');
  const progress = need(K, raw, 'progress');
  const overall = need(K, progress, 'overall_pct');
  const stats = need(K, raw, 'stats');
  const vars = raw.tpl_vars || {};
  const pm = moduleProgress(K, progress, modulesRaw.map(m => m.id));
  const byId = Object.fromEntries(pm.map(m => [m.id, m]));
  const cur = currentModule(pm);

  const modules = modulesRaw.map((m, i) => {
    const p = byId[m.id] || {};
    const [short, icon] = ui(m.id);
    return {
      no: i, short, title: fill(String(need(K, m, 'title')).split(':')[0], vars), icon,
      pct: p.pct == null ? null : Math.round(p.pct), done: p.done ?? null, total: p.total ?? null,
      awaiting: p.awaiting_client ?? null, why: fill(firstPara(m.lesson), vars)
    };
  });

  const items = [];
  for (const m of modulesRaw) for (const it of needArray(K, m, 'items')) items.push({ m, it, st: it.state || {} });
  const label = it => fill(it.client_title || it.title, vars);
  const inFlight = items.filter(x => ['in_progress', 'awaiting_verify'].includes(x.st.status))
    .map(x => ({ icon: ui(x.m.id)[1], title: label(x.it), meta: `${ui(x.m.id)[0]} · started ${when(x.st.updated_at, now)}` }));
  const done = items.filter(x => x.st.status === 'done')
    .sort((a, b) => String(b.st.updated_at || '').localeCompare(String(a.st.updated_at || '')));

  return {
    progress_pct: overall == null ? null : Math.round(overall),
    done: sum(pm, 'done'),
    total: sum(pm, 'total'),
    awaiting_client: sum(pm, 'awaiting_client'),
    // stats.<metric> = {latest, points, delta_7d, delta_30d} (course-routes computeStats), not a bare number
    brand_score: typeof stats.brand_score === 'number' ? stats.brand_score
      : (stats.brand_score && typeof stats.brand_score.latest === 'number' ? stats.brand_score.latest : null),
    module_no: pm.length ? cur + 1 : null,   // human step number ("Step N of 12")
    module_index: pm.length ? cur : null,
    module_title: modules[cur] ? modules[cur].title : '',
    modules,
    in_flight: inFlight,
    recent_done: done.slice(0, 5).map(x => ({ title: label(x.it), when: when(x.st.updated_at, now) })),
    wins: done.map(x => ({
      title: label(x.it), area: ui(x.m.id)[0], when: when(x.st.updated_at, now),
      proof: x.st.evidence && x.st.evidence.url ? 'See it' : '—'
    }))
  };
}

function courseNext(raw, ctx = {}) {
  const K = 'course.next';
  const now = ctx.now || new Date();
  const actions = needArray(K, raw, 'client_actions');
  const events = needArray(K, raw, 'recent_events');
  const progress = need(K, raw, 'progress');
  const overall = need(K, progress, 'overall_pct');
  const pm = moduleProgress(K, progress);
  const cur = currentModule(pm);
  const curId = pm[cur] && pm[cur].id;
  // module titles only come with client actions on this endpoint; the stepper label is the fallback.
  const titled = actions.find(a => a.module && a.module.id === curId);
  return {
    awaiting_client: sum(pm, 'awaiting_client'),
    progress_pct: overall == null ? null : Math.round(overall),
    module_no: pm.length ? cur + 1 : null,   // human step number ("Step N of 12")
    module_title: titled ? String(titled.module.title).split(':')[0] : (curId ? ui(curId)[0] : ''),
    module_note: '',
    client_actions: actions.map(a => ({
      title: need(K, a, 'title'),
      module: a.module ? String(a.module.title).split(':')[0] : '—',
      icon: ui(a.module && a.module.id)[1],
      why: a.why || '',
      how: a.micro_lesson || '',
      when: a.updated_at ? `Waiting since ${when(a.updated_at, now)}` : '—'
    })),
    recent: events.map(e => ({
      icon: e.type === 'achievement' ? 'emoji_events' : (e.to === 'done' ? 'check_circle' : 'sync'),
      title: e.title || e.note || e.item || '—',
      when: when(e.at, now),
      kind: e.to === 'done' || e.type === 'achievement' ? 'done' : 'live'
    })),
    upcoming: []   // UNBACKED: no scheduled-content endpoint exists yet
  };
}

/* ---------- seo ---------- */

function seoSnapshots(raw) {
  const K = 'seo.snapshots';
  const snaps = needArray(K, raw, 'snapshots');
  const last = snaps.length ? snaps[snaps.length - 1] : null;
  const pick = f => (last ? need(K, last, f) : null);
  const num = v => (v == null ? null : Number(v));
  return {
    latest: last ? {
      total_keywords: num(pick('total_keywords')), top3_count: num(pick('top3_count')),
      top10_count: num(pick('top10_count')), top20_count: num(pick('top20_count')),
      est_traffic: num(pick('est_traffic')), backlinks_count: num(pick('backlinks_count'))
    } : {},
    // UNBACKED on this endpoint (need keyword-tracker / accumulated/* / GBP / AI-mention sources):
    movers: [], working: [], keywords: [], gbp: {}, ai: {}, backlinks: []
  };
}

function seoReports(raw) {
  const K = 'seo.reports';
  const reps = needArray(K, raw, 'reports').slice()
    .sort((a, b) => String(b.generatedAt || '').localeCompare(String(a.generatedAt || '')));
  return {
    reports: reps.map(r => {
      const d = new Date(need(K, r, 'generatedAt'));
      const hs = r.kpis && r.kpis.healthScore;
      return {
        title: isNaN(d) ? 'Brand report' : `${MONTHS_LONG[d.getMonth()]} brand report`,
        date: dateLabel(r.generatedAt),
        pages: '—',                                  // UNBACKED: the index carries no page count
        score: hs == null ? '—' : String(hs)         // health score as served (0-100), never a made-up grade
      };
    })
  };
}

/* ---------- leads ---------- */

const leadWhat = l => l.service_type || (l.message ? String(l.message).slice(0, 60) : '—');

function leadsStats(raws) {
  const K = 'leads.stats';
  const [statsRaw, listRaw] = Array.isArray(raws) ? raws : [raws, null];
  const st = need(K, statsRaw, 'stats');
  const stats = {};
  for (const f of ['total', 'new_count', 'contacted', 'quoted', 'won', 'lost', 'spam']) stats[f] = need(K, st, f);
  const recent = listRaw ? needArray(K, listRaw, 'leads').map(l => ({
    name: l.name || '—', what: leadWhat(l), when: when(l.created_at),
    source: l.form_type ? `${cap(l.form_type)} form` : (l.source_site || '—')
  })) : [];
  return { stats, recent };
}

function leadsList(raw, ctx = {}) {
  const K = 'leads.list';
  const now = ctx.now || new Date();
  // PII (email, phone, address, fein, raw_data) is deliberately NOT carried into the view model.
  return {
    leads: needArray(K, raw, 'leads').map(l => ({
      name: l.name || '—', what: leadWhat(l), site: l.source_site || '—',
      when: when(need(K, l, 'created_at'), now), stage: cap(need(K, l, 'status'))
    }))
  };
}

/* ---------- biz/v1 (jb.js business-record collections, see /jb/v1/sdk.md) ----------
   Generic list envelope: GET /<collection> -> { records: [{ id, version, data:{...},
   created_at, ... }], next_cursor }. The SDK exposes the ENVELOPE, not a per-collection
   field schema (field names live in that business's own jb.schema(), custom fields are
   x_<name>) — so only the envelope shape is load-bearing here (ShapeError on a missing
   `records` array). Individual business fields (contact.name, job.status, invoice.total...)
   are read defensively through firstOf() and degrade to '—'/null rather than throwing,
   EXCEPT invoice amount/status, which are treated as structural to an invoice (see below).
   This field-name mapping is a judgment call pending the real jb.schema() — see delivery
   notes for the ones most likely to need a follow-up pass once real data is visible. */
const bizRecords = (key, raw) => needArray(key, raw, 'records');
const bizData = rec => (rec && rec.data) || {};
/** first present, non-empty value among candidate data-field names; else null (never invented). */
const firstOf = (obj, candidates) => {
  for (const k of candidates) { const v = obj[k]; if (v !== undefined && v !== null && v !== '') return v; }
  return null;
};
const initialsOf = name => String(name || '').trim().split(/\s+/).filter(Boolean)
  .slice(0, 2).map(w => w[0].toUpperCase()).join('') || '—';
function dueLabel(due, now) {
  const dt = new Date(due);
  if (isNaN(dt)) return '—';
  const days = Math.round((dt.getTime() - now.getTime()) / 86400000);
  if (days > 0) return `Due in ${days} day${days === 1 ? '' : 's'}`;
  if (days === 0) return 'Due today';
  return `Overdue ${-days} day${days === -1 ? '' : 's'}`;
}

/** crm.pipeline: raws = [contact list, job list] (both from /api/biz/v1). Kanban stages come
    from each job's stage/status field; contacts_list.jobs is a real count of jobs that name
    that contact (contact_id/customer_id/contact.id — whichever the job record carries).
    UNBACKED: per-stage/pipeline dollar value and "needs a follow-up" staleness — no confirmed
    money field on job/contact and no staleness tracking exists in this collection set yet. */
function crmPipeline(raws) {
  const K = 'crm.pipeline';
  const [contactsRaw, jobsRaw] = Array.isArray(raws) ? raws : [raws, { records: [] }];
  const contacts = bizRecords(K, contactsRaw);
  const jobs = bizRecords(K, jobsRaw);

  const contactIdOf = j => {
    const jd = bizData(j);
    // Prefer the server-resolved include=contact join (jd.contact.id) over the raw foreign key:
    // the raw contact_id value is not guaranteed to be in the same format as the contact record's
    // own id (host-measured 2026-11-18 — matching on the raw value alone produced jobs:0 for
    // every contact despite real contact_id values on all jobs).
    return (jd.contact && (jd.contact.id || (typeof jd.contact === 'string' ? jd.contact : null))) ||
      firstOf(jd, ['contact_id', 'customer_id']);
  };
  const jobsPerContact = new Map();
  for (const j of jobs) { const cid = contactIdOf(j); if (cid) jobsPerContact.set(cid, (jobsPerContact.get(cid) || 0) + 1); }

  const stageBuckets = new Map();
  for (const j of jobs) {
    const raw = firstOf(bizData(j), ['status', 'stage']);   // 'status' is the confirmed real field (host-measured 2026-11-18); 'stage' kept as a harmless fallback
    const label = raw ? cap(String(raw).replace(/_/g, ' ')) : 'Unspecified';
    stageBuckets.set(label, (stageBuckets.get(label) || 0) + 1);
  }

  return {
    contacts: contacts.length,
    deals: jobs.length,
    value: null, value_display: '—',   // UNBACKED: no confirmed money field on job/contact
    stale: [],                          // UNBACKED: no follow-up/staleness tracking on contact/job
    contacts_list: contacts.map(c => ({
      name: firstOf(bizData(c), ['name']) || '—',
      company: firstOf(bizData(c), ['company']) || '—',
      phone: firstOf(bizData(c), ['phone']) || '—',
      last: c.updated_at ? when(c.updated_at) : '—',
      jobs: jobsPerContact.get(c.id) || 0
    })),
    stages: [...stageBuckets.entries()].map(([label, count]) => ({ label, count, value: '', tracked: true }))
  };
}

/** jobs.list: raw = job list. customer name prefers an included contact (?include=contact),
    else a plain customer_name field. UNBACKED per row where the field truly isn't present:
    quoted amount (no confirmed estimate/amount field on job itself — that lives on estimate/
    line_item, not fetched here) renders null (blank cell), never a fabricated 0. */
function jobsList(raw) {
  const K = 'jobs.list';
  const recs = bizRecords(K, raw);
  return {
    jobs: recs.map(j => {
      const jd = bizData(j);
      const contactName = jd.contact && jd.contact.data && firstOf(jd.contact.data, ['name']);
      return {
        id: firstOf(jd, ['job_number', 'number']) || j.id,
        customer: contactName || firstOf(jd, ['customer_name']) || '—',
        site: firstOf(jd, ['address', 'site']) || '—',
        quoted: firstOf(jd, ['quoted_amount', 'estimate_total', 'amount']),
        status: cap((firstOf(jd, ['status', 'stage']) || '').replace(/_/g, ' '))
      };
    })
  };
}

/** payments.invoices: raw = invoice list. amount and status are treated as STRUCTURAL to an
    invoice (ShapeError, not a guess, when neither is present) — unlike the defensive fields
    above, a confident "$0" or an invented status here is exactly the wrong-number risk the
    no-invented-number rule exists for. due/paid are derived from due_date vs now and from
    status, never fabricated. */
function paymentsInvoices(raw, ctx = {}) {
  const K = 'payments.invoices';
  const now = ctx.now || new Date();
  const recs = bizRecords(K, raw);
  return {
    invoices: recs.map(r => {
      const rd = bizData(r);
      const amount = firstOf(rd, ['total', 'amount', 'total_amount']);
      if (amount === null) throw new ShapeError(K, 'data.total|amount|total_amount');
      const rawStatus = String(firstOf(rd, ['status']) || '').toLowerCase();
      if (!rawStatus) throw new ShapeError(K, 'data.status');
      const dueDate = firstOf(rd, ['due_date', 'due_at']);
      const overdue = rawStatus !== 'paid' && dueDate && new Date(dueDate) < now;
      const status = overdue ? 'overdue' : rawStatus;
      const contactName = rd.contact && rd.contact.data && firstOf(rd.contact.data, ['name']);
      const due = status === 'paid'
        ? `Paid ${dateLabel(firstOf(rd, ['paid_at']) || r.updated_at || r.created_at)}`
        : (dueDate ? dueLabel(dueDate, now) : '—');
      return {
        number: firstOf(rd, ['invoice_number', 'number']) || r.id,
        to: contactName || firstOf(rd, ['customer_name']) || '—',
        for: firstOf(rd, ['description', 'memo']) || '—',
        amount: Number(amount),
        due, status,
        paid: status === 'paid' ? (firstOf(rd, ['payment_method', 'paid_via']) || '—') : null
      };
    })
  };
}

/** team.roster: raw = person list. `threads`/`files` are included empty (not undefined) because
    the fixture-shaped view model for this key shares those keys with team.threads/team.files
    (team.js overview() reads data.files and data.threads off THIS key too) — both stay
    UNBACKED/live:null (no messaging or file-store collection exists), so they must still be
    present as {}/[] or the view throws on an undefined spread/index. `unread` is UNBACKED
    (no messaging system) and deliberately null, not 0 — falsy either way in the view's
    `p.unread ? ... : ''` check, but null never reads as a confident "zero unread". */
function teamRoster(raw) {
  const K = 'team.roster';
  const recs = bizRecords(K, raw);
  return {
    roster: recs.map(p => {
      const pd = bizData(p);
      const name = firstOf(pd, ['name']) || '—';
      return {
        id: p.id,
        name,
        role: firstOf(pd, ['role', 'title']) || '—',
        initials: initialsOf(name),
        status: firstOf(pd, ['status']) || '—',
        unread: null
      };
    }),
    threads: {},
    files: []
  };
}

/* ---------- registry ---------- */

export const ADAPTERS = {
  'course.state':      { fn: courseState,      provides: ['progress_pct','done','total','awaiting_client','brand_score','module_no','module_index','module_title','modules','in_flight','recent_done','wins'], unbacked: [] },
  'course.next':        { fn: courseNext,       provides: ['awaiting_client','progress_pct','module_no','module_title','client_actions','recent'], unbacked: ['module_note','upcoming'] },
  'seo.snapshots':      { fn: seoSnapshots,     provides: ['latest'], unbacked: ['movers','working','keywords','gbp','ai','backlinks'] },
  'seo.reports':        { fn: seoReports,       provides: ['reports'], unbacked: [] },
  'leads.stats':        { fn: leadsStats,       provides: ['stats','recent'], unbacked: [] },
  'leads.list':         { fn: leadsList,        provides: ['leads'], unbacked: [] },
  'crm.pipeline':       { fn: crmPipeline,      provides: ['contacts','deals','contacts_list','stages'], unbacked: ['value','value_display','stale'] },
  'jobs.list':          { fn: jobsList,         provides: ['jobs'], unbacked: [] },
  'payments.invoices':  { fn: paymentsInvoices, provides: ['invoices'], unbacked: [] },
  'team.roster':        { fn: teamRoster,       provides: ['roster'], unbacked: ['threads','files'] }
};

/** normalize(key, raw|raws, ctx) -> view model. Throws ShapeError / Error('no adapter'). */
export function normalize(key, raw, ctx = {}) {
  const a = ADAPTERS[key];
  if (!a) throw new Error(`no adapter for ${key}`);
  return a.fn(raw, ctx);
}
