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
/** money/number field -> a finite number, else null (unknown). Never NaN, never Infinity, and never a
    confident 0 for '', true/false, an object or text like "n/a" (Number('') is 0, Number('x') is NaN). */
const finiteNum = v => {
  if (v === null || v === undefined || v === '' || typeof v === 'boolean' || typeof v === 'object') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};
/** a served boolean flag -> true/false; absent or null -> null (unknown, never a default). */
const flagOrNull = v => (v === undefined || v === null ? null : !!v);
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

/* Real field names (OVU default-schemas/core/*.json, read 2026-11-18 from the backend source):
     contact: name phone email address city state zip type company_name lead_source tags notes
     job:     job_number contact_id(ref) title address service_type status scheduled_at completed_at crew
              status enum: lead quoted scheduled in_progress completed invoiced paid cancelled on_hold
     invoice: number job_id contact_id(ref) amount issued_on due_on status payment_link notes
              status enum: draft sent partial paid void overdue
     payment: invoice_id job_id amount paid_at method reference notes
     person:  name role phone email skills schedule active notes
   `?include=contact` puts the referenced record at rec.included.<ref field> (e.g. included.contact_id).
   Older guessed names stay as fallbacks after the real one, so nothing that worked stops working. */
const includedName = (rec, field = 'contact_id') => {
  const inc = rec && rec.included && rec.included[field];
  return inc && inc.data ? firstOf(inc.data, ['name', 'company_name']) : null;
};
const JOB_STAGE_LABEL = {
  lead: 'New enquiry', quoted: 'Quoted', scheduled: 'Scheduled', in_progress: 'In progress',
  on_hold: 'On hold', completed: 'Completed', invoiced: 'Invoiced', paid: 'Paid', cancelled: 'Cancelled'
};
const JOB_STAGE_ORDER = ['lead', 'quoted', 'scheduled', 'in_progress', 'on_hold', 'completed', 'invoiced', 'paid', 'cancelled'];
const JOB_OPEN = new Set(['lead', 'quoted', 'scheduled', 'in_progress', 'on_hold']);
const stageLabel = raw => JOB_STAGE_LABEL[raw] || (raw ? cap(String(raw).replace(/_/g, ' ')) : 'No stage set');
/** an /aggregate answer {count} -> integer, else null (unknown, never a page-length guess) */
const aggCount = a => (a && Number.isInteger(a.count) ? a.count : null);

/** crm.pipeline: raws = [contact list, job list, contact aggregate?, job aggregate?] (all /api/biz/v1).
    Counts come from the server-side aggregate when present (a list page can undercount); the list
    length is used only when the aggregate was not fetched. Kanban stages = job status buckets in the
    schema's own order. UNBACKED: pipeline dollar value (lives on estimates, shown from the hub
    summary on the live Customers page) and follow-up staleness (nothing tracks it yet). */
function crmPipeline(raws) {
  const K = 'crm.pipeline';
  const arr = Array.isArray(raws) ? raws : [raws, { records: [] }];
  const [contactsRaw, jobsRaw, contactAgg, jobAgg] = arr;
  const contacts = bizRecords(K, contactsRaw);
  const jobs = bizRecords(K, jobsRaw || { records: [] });

  const contactIdOf = j => {
    const jd = bizData(j);
    return firstOf(jd, ['contact_id', 'customer_id']) ||
      (jd.contact && (jd.contact.id || (typeof jd.contact === 'string' ? jd.contact : null)));
  };
  const jobsPerContact = new Map();
  for (const j of jobs) { const cid = contactIdOf(j); if (cid) jobsPerContact.set(cid, (jobsPerContact.get(cid) || 0) + 1); }

  const byStatus = new Map();
  for (const j of jobs) {
    const raw = String(firstOf(bizData(j), ['status', 'stage']) || '');
    byStatus.set(raw, (byStatus.get(raw) || 0) + 1);
  }
  const ordered = [...byStatus.keys()].sort((a, b) => {
    const ia = JOB_STAGE_ORDER.indexOf(a), ib = JOB_STAGE_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  const contactsN = aggCount(contactAgg) ?? (contactsRaw && contactsRaw.next_cursor ? null : contacts.length);
  const jobsN = aggCount(jobAgg) ?? (jobsRaw && jobsRaw.next_cursor ? null : jobs.length);

  return {
    contacts: contactsN,
    deals: jobsN,
    open_jobs: jobs.length && !(jobsRaw && jobsRaw.next_cursor)
      ? jobs.filter(j => JOB_OPEN.has(String(firstOf(bizData(j), ['status', 'stage']) || ''))).length : null,
    value: null, value_display: '—',   // UNBACKED here: money lives on estimates (hub summary)
    stale: [],                          // UNBACKED: no follow-up/staleness tracking on contact/job
    contacts_list: contacts.map(c => ({
      name: firstOf(bizData(c), ['name']) || '—',
      company: firstOf(bizData(c), ['company_name', 'company']) || '—',
      phone: firstOf(bizData(c), ['phone']) || '—',
      last: c.updated_at ? when(c.updated_at) : '—',
      jobs: jobsPerContact.get(c.id) || 0
    })),
    stages: ordered.map(raw => ({ label: stageLabel(raw), count: byStatus.get(raw), value: '', tracked: true }))
  };
}

/** jobs.list: raw = job list (?include=contact). Customer name comes from the included contact.
    Quoted amount is not a job field (it lives on estimate/line_item) -> null (blank), never 0. */
function jobsList(raw) {
  const K = 'jobs.list';
  const recs = bizRecords(K, raw);
  return {
    jobs: recs.map(j => {
      const jd = bizData(j);
      const legacyName = jd.contact && jd.contact.data && firstOf(jd.contact.data, ['name']);
      const status = firstOf(jd, ['status', 'stage']);
      return {
        id: firstOf(jd, ['job_number', 'number']) || '—',
        title: firstOf(jd, ['title', 'service_type']) || '—',
        customer: includedName(j) || legacyName || firstOf(jd, ['customer_name']) || '—',
        site: firstOf(jd, ['address', 'site']) || '—',
        when: firstOf(jd, ['scheduled_at']) ? dateLabel(jd.scheduled_at) : '—',
        quoted: firstOf(jd, ['quoted_amount', 'estimate_total', 'amount']),
        status: status ? stageLabel(String(status)) : '—'
      };
    })
  };
}

/** payments.invoices: raw = invoice list (?include=contact). amount and status are STRUCTURAL to an
    invoice (ShapeError when absent, never a guessed $0 or status). Overdue is derived from due_on vs
    now for unpaid statuses; void/draft are shown as such. */
const INV_UNPAID = new Set(['sent', 'partial', 'overdue']);
function paymentsInvoices(raw, ctx = {}) {
  const K = 'payments.invoices';
  const now = ctx.now || new Date();
  const recs = bizRecords(K, raw);
  return {
    invoices: recs.map(r => {
      const rd = bizData(r);
      const amount = firstOf(rd, ['amount', 'total', 'total_amount']);
      if (amount === null) throw new ShapeError(K, 'data.amount');
      const rawStatus = String(firstOf(rd, ['status']) || '').toLowerCase();
      if (!rawStatus) throw new ShapeError(K, 'data.status');
      const dueDate = firstOf(rd, ['due_on', 'due_date', 'due_at']);
      const overdue = INV_UNPAID.has(rawStatus) && dueDate && new Date(dueDate) < now;
      const status = overdue ? 'overdue' : rawStatus;
      const legacyName = rd.contact && rd.contact.data && firstOf(rd.contact.data, ['name']);
      const due = status === 'paid'
        ? `Paid ${dateLabel(firstOf(rd, ['paid_at']) || r.updated_at || r.created_at)}`
        : (dueDate ? dueLabel(dueDate, now) : '—');
      return {
        number: firstOf(rd, ['number', 'invoice_number']) || '—',
        to: includedName(r) || legacyName || firstOf(rd, ['customer_name']) || '—',
        for: firstOf(rd, ['notes', 'description', 'memo']) || '—',
        amount: finiteNum(amount),   // non-finite -> null: shown as '—', never "$NaN"
        due, status,
        paid: status === 'paid' ? (firstOf(rd, ['payment_method', 'paid_via']) || '—') : null
      };
    })
  };
}

/** payments.recent: raw = payment list (newest first). method/amount/paid_at are payment fields. */
function paymentsRecent(raw) {
  const K = 'payments.recent';
  return {
    recent: bizRecords(K, raw).map(p => {
      const pd = bizData(p);
      const amount = firstOf(pd, ['amount']);
      if (amount === null) throw new ShapeError(K, 'data.amount');
      return {
        amount: finiteNum(amount),   // non-finite -> null: shown as '—', never "$NaN"
        method: firstOf(pd, ['method']) || 'Payment',
        reference: firstOf(pd, ['reference']) || '',
        when: when(firstOf(pd, ['paid_at']) || p.created_at)
      };
    })
  };
}

/** team.roster: raw = person list. threads/files stay {} / [] (no messaging or file-store
    collection exists); unread is null (unbacked), never a confident 0. status: active flag. */
function teamRoster(raw) {
  const K = 'team.roster';
  const recs = bizRecords(K, raw);
  return {
    roster: recs.map(p => {
      const pd = bizData(p);
      const name = firstOf(pd, ['name']) || '—';
      const active = flagOrNull(pd.active);
      return {
        id: p.id,
        name,
        role: firstOf(pd, ['role', 'title']) || '—',
        initials: initialsOf(name),
        status: firstOf(pd, ['status']) || (active === null ? '—' : active ? 'Active' : 'Not active'),
        phone: firstOf(pd, ['phone']) || null,
        unread: null
      };
    }),
    threads: {},
    files: []
  };
}

/* ---------- the hub summary (GET /api/biz/v1/hub/summary, OVU services/biz/hub.py) ---------- */
const HUB_SECTIONS = ['customers', 'jobs', 'money', 'team', 'inventory', 'leads', 'seo', 'site'];
const VERDICTS = new Set(['ok', 'warn', 'bad', 'unknown']);
const STATES = new Set(['live', 'empty', 'unreachable', 'not_built', 'sandbox']);   // HUB-SUMMARY-CONTRACT v1
/** Validates the envelope and every section it promises; a section missing its verdict is a
    ShapeError (the whole Home then says "can't read right now"), never a guessed tile.
    A section with no state (or one the contract does not name) is NOT treated as measured:
    state 'unknown' and verdict 'unknown', so it renders grey ("Can't tell"), never as a live tile. */
function hubSummary(raw) {
  const K = 'hub.summary';
  const sections = need(K, raw, 'sections');
  const order = Array.isArray(raw.order) && raw.order.length ? raw.order : HUB_SECTIONS;
  const out = {};
  for (const name of order) {
    const sct = sections[name];
    if (!sct || typeof sct !== 'object') throw new ShapeError(K, `sections.${name}`);
    const served = need(K, sct, 'verdict');
    if (!VERDICTS.has(served)) throw new ShapeError(K, `sections.${name}.verdict`);
    const state = STATES.has(sct.state) ? sct.state : 'unknown';
    const verdict = state === 'unknown' ? 'unknown' : served;
    out[name] = {
      label: sct.label || cap(name), verdict, state,
      reason: sct.reason || (state === 'unknown' ? 'Not confirmed as measured' : ''), counts: (sct.counts && typeof sct.counts === 'object') ? sct.counts : {},
      last_activity: sct.last_activity || null, domains: Array.isArray(sct.domains) ? sct.domains : []
    };
  }
  const overall = raw.overall && typeof raw.overall === 'object' ? raw.overall : {};
  return {
    tenant: raw.tenant || null, sandbox: raw.sandbox || null, generated_at: raw.generated_at || null,
    order, sections: out,
    overall: { verdict: VERDICTS.has(overall.verdict) ? overall.verdict : 'unknown', reason: overall.reason || '' }
  };
}

/** payments.summary (live) = the hub summary's money section. Server-side sums over every invoice
    and payment. nextPayout/avgDaysToPay/sharePaidByCard: no backing collection -> null. */
function paymentsSummary(raw) {
  const K = 'payments.summary';
  const hub = hubSummary(raw);
  const m = hub.sections.money;
  if (!m) throw new ShapeError(K, 'sections.money');
  const c = m.counts || {};
  const num = v => (typeof v === 'number' && isFinite(v) ? v : null);
  const empty = m.state === 'empty';
  return {
    state: m.state, verdict: m.verdict, reason: m.reason, empty,
    summary: {
      collectedThisMonth: num(c.payments_30d_value), collectedCount: num(c.payments_30d),
      pending: num(c.invoices_unpaid_value), pendingCount: num(c.invoices_unpaid),
      overdue: num(c.invoices_overdue_value), overdueCount: num(c.invoices_overdue),
      estimatesOpen: num(c.estimates_open), estimatesOpenValue: num(c.estimates_open_value),
      nextPayout: null, avgDaysToPay: null, sharePaidByCard: null
    },
    recent: [], links: []
  };
}

/** business.profile: raw = company list (limit 1). The business's own record, shown read-only on
    the live Account page and used for the shell name. Absent fields stay null (shown as "Not set"). */
function businessProfile(raw) {
  const K = 'business.profile';
  const rec = bizRecords(K, raw)[0];
  const d = bizData(rec);
  const name = firstOf(d, ['name', 'legal_name', 'trading_name', 'company_name']);
  const list = v => (Array.isArray(v) ? v.filter(x => typeof x === 'string' && x.trim()) : (typeof v === 'string' && v.trim() ? [v] : []));
  return {
    name: name || null, initials: name ? initialsOf(name) : null,
    phone: firstOf(d, ['phone']), email: firstOf(d, ['email']), address: firstOf(d, ['address']),
    website: firstOf(d, ['website']), gbp_url: firstOf(d, ['gbp_url']), license_no: firstOf(d, ['license_no']),
    timezone: firstOf(d, ['timezone']), services: list(d.services), service_area: list(d.service_area),
    updated: rec && rec.updated_at ? when(rec.updated_at) : null
  };
}

/** inventory.products: raw = product list. unit_cost is money when present, else null (blank). */
function inventoryProducts(raw) {
  const K = 'inventory.products';
  return {
    products: bizRecords(K, raw).map(r => {
      const d = bizData(r);
      const cost = firstOf(d, ['unit_cost']);
      return {
        name: firstOf(d, ['name']) || '—', category: firstOf(d, ['category']) || '—',
        sku: firstOf(d, ['sku']) || '—', unit: firstOf(d, ['unit', 'size']) || '—',
        supplier: firstOf(d, ['supplier']) || '—',
        unit_cost: finiteNum(cost),
        active: flagOrNull(d.active)   // absent -> null (unknown), same as team.roster; never a guessed 'Active'
      };
    })
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
  'payments.summary':   { fn: paymentsSummary,  provides: ['summary'], unbacked: ['recent','links'] },
  'payments.recent':    { fn: paymentsRecent,   provides: ['recent'], unbacked: [] },
  'team.roster':        { fn: teamRoster,       provides: ['roster'], unbacked: ['threads','files'] },
  'hub.summary':        { fn: hubSummary,       provides: ['sections','order','overall'], unbacked: [] },
  'business.profile':   { fn: businessProfile,  provides: ['name','initials','phone','email','address','website','gbp_url','license_no','timezone','services','service_area','updated'], unbacked: [] },
  'inventory.products': { fn: inventoryProducts, provides: ['products'], unbacked: [] }
};

/** normalize(key, raw|raws, ctx) -> view model. Throws ShapeError / Error('no adapter'). */
export function normalize(key, raw, ctx = {}) {
  const a = ADAPTERS[key];
  if (!a) throw new Error(`no adapter for ${key}`);
  return a.fn(raw, ctx);
}
