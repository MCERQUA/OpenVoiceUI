/* LIVE HOME — a real business on its own address (https://<tenant>.jam-bot.com/hub/).
   Every number comes from GET /api/biz/v1/hub/summary (server-side counts over this business's
   own records) or the plan feed. A section with nothing recorded says "Nothing here yet"; a
   section that can't be read says so with the reason. No demo names, no demo deltas, no fixture
   numbers. The fixture Home (home.js) is untouched and still drives jamdash.jam-bot.com. */

import { pageHead, tiles, block, rows, approval, pill, note,
         progress, icon, esc, calendar as calGrid, liveBody, verdictState, empty } from '../ui.js';

const DOW_NAMES = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const MONTH_NAMES = ['January','February','March','April','May','June','July',
  'August','September','October','November','December'];

/** a dollar figure; null / non-finite (unknown) -> '—', never "$NaN" or a confident "$0". */
export const money = n => (n === null || n === undefined || n === '' || !Number.isFinite(Number(n))
  ? '—' : `$${Number(n).toLocaleString('en-CA', { maximumFractionDigits: 0 })}`);
const greeting = d => (d.getHours() < 12 ? 'Good morning' : d.getHours() < 18 ? 'Good afternoon' : 'Good evening');

/** one hub section -> a tile. empty -> calm "Nothing here yet"; unknown -> grey with the reason. */
export function sectionTile(api, sec, { label, icon: ic, value, display, link, note }) {
  if (!sec) return api.unknownMetric(label, { note: 'Not part of this summary yet' }, { icon: ic, link });
  if (sec.state === 'empty') {
    return { state: 'empty', display: 'Nothing here yet', label, icon: ic, note: sec.reason, link };
  }
  if (sec.verdict === 'unknown' || value === null || value === undefined) {
    return api.unknownMetric(label, { note: sec.reason || 'Not measured yet' }, { icon: ic, link });
  }
  return api.metric({ value, display: display ?? String(value), state: verdictState(sec.verdict),
    label, icon: ic, note: note ?? sec.reason, link });
}

export function sectionPill(sec) {
  if (!sec) return pill('Not connected', 'unknown');
  if (sec.state === 'empty') return pill('Nothing yet', 'neutral');
  if (sec.verdict === 'unknown') return pill(sec.state === 'not_built' ? 'Not measured yet' : 'Can’t tell', 'unknown');
  return pill(sec.verdict === 'ok' ? 'All good' : sec.verdict === 'warn' ? 'Needs a look' : 'Needs you', sec.verdict);
}

export async function liveHome({ api }) {
  const [hub, course, profile] = await Promise.all([
    api.get('hub.summary'), api.get('course.next'), api.get('business.profile')
  ]);
  const now = new Date();
  const todayStr = `${DOW_NAMES[now.getDay()]}, ${now.getDate()} ${MONTH_NAMES[now.getMonth()]}`;
  const name = profile.ok ? profile.data.name : null;
  const S = hub.ok ? hub.data.sections : {};
  const c = course.ok ? course.data : null;

  const m = S.money, mc = (m && m.counts) || {};
  const kpis = hub.ok ? [
    c ? api.metric({ value: c.awaiting_client, display: String(c.awaiting_client ?? ''),
          state: c.awaiting_client > 10 ? 'warn' : 'ok', icon: 'task_alt',
          label: 'Waiting on you', note: 'Things only you can do',
          link: { href: '#/plan/your-turn', label: 'Do them' } })
      : api.unknownMetric('Waiting on you', course, { icon: 'task_alt' }),
    sectionTile(api, m, { label: 'Awaiting payment', icon: 'payments',
      value: mc.invoices_unpaid_value, display: money(mc.invoices_unpaid_value),
      link: { href: '#/payments', label: 'Payments' } }),
    sectionTile(api, S.jobs, { label: 'Open jobs', icon: 'construction',
      value: S.jobs?.counts?.open, link: { href: '#/customers/jobs', label: 'Jobs' } }),
    sectionTile(api, S.leads, { label: 'New leads', icon: 'contact_phone',
      value: S.leads?.counts?.new, link: { href: '#/customers/leads', label: 'See leads' } }),
    sectionTile(api, S.customers, { label: 'Customers', icon: 'groups',
      value: S.customers?.counts?.customers, link: { href: '#/customers/contacts', label: 'Contacts' } }),
    sectionTile(api, S.seo, { label: 'SEO reports', icon: 'trending_up',
      value: S.seo?.counts?.reports, link: { href: '#/found', label: 'Get found' } })
  ] : [];

  const turn = c ? (c.client_actions || []).slice(0, 2) : [];
  const across = [
    { href: '#/payments',  icn: 'account_balance', title: 'Money',       sec: S.money, hub: true },
    { href: '#/team',      icn: 'diversity_3',     title: 'Team',        sec: S.team, hub: true },
    { href: '#/inventory', icn: 'inventory_2',     title: 'Inventory',   sec: S.inventory, hub: true },
    { href: '#/website',   icn: 'language',        title: 'Website',     sec: S.site, hub: true },
    { href: '#/ads',       icn: 'campaign',        title: 'Advertising', sec: null, meta: 'Google, Facebook, Instagram' },
    { href: '#/support',   icn: 'support_agent',   title: 'Support',     sec: null, meta: 'Open tickets' }
  ];

  return `
  ${pageHead({
    eyebrow: todayStr,
    title: name ? `${greeting(now)}, ${name}` : greeting(now),
    sub: hub.ok && hub.data.overall.verdict !== 'unknown' && hub.data.overall.reason
      ? hub.data.overall.reason : 'Here is where your business stands right now.',
    actions: [
      { label: 'Ask your agent', kind: 'primary', icon: 'auto_awesome', href: '#/ask' },
      { label: 'Request work', icon: 'add', href: '#/ask/requests' }
    ]
  })}

  ${hub.ok ? tiles(kpis) : block({ body: liveBody(hub, () => '', { what: 'your business summary' }) })}

  <div class="grid grid--sidebar">
    <div>
      ${block({
        title: 'Your turn', icon: 'pan_tool_alt', flush: true,
        action: c ? { label: `All ${c.awaiting_client ?? ''}`, href: '#/plan/your-turn' } : null,
        body: liveBody(course, () => turn.length
          ? turn.map(a => approval({
              title: a.title, excerpt: a.why, channel: a.module,
              status: 'Needs you', icon: a.icon || 'flag', when: a.when
            })).join('<div style="height:1px;background:rgba(196,198,207,.25)"></div>')
          : empty({ icon: 'celebration', title: 'Nothing needs you right now',
                    note: 'Anything that needs your call will show up right here.' }),
          { what: 'your to-do list' })
      })}

      ${block({
        title: 'What we did lately', icon: 'bolt', flush: true,
        action: { label: 'Everything', href: '#/plan/wins' },
        body: liveBody(course, d => rows((d.recent || []).map(r => ({
          icon: r.icon || 'check_circle', title: r.title, meta: r.when,
          side: pill(r.kind || 'done', r.kind === 'live' ? 'ok' : 'info')
        }))), { what: 'recent work' })
      })}
    </div>

    <div>
      ${block({
        title: 'Across your business', icon: 'dashboard', flush: true,
        body: across.map(r => `<a class="row" href="${esc(r.href)}">
            <div class="row__icon">${icon(r.icn)}</div>
            <div class="row__main">
              <div class="row__title">${esc(r.title)}</div>
              <div class="row__meta">${esc(r.sec ? r.sec.reason : (r.hub && !hub.ok ? 'Can’t read this right now' : (r.meta || '')))}</div>
            </div>
            <div class="row__side">${r.hub && !hub.ok ? pill('Can’t tell', 'unknown') : sectionPill(r.sec)}${icon('chevron_right')}</div>
          </a>`).join('')
      })}

      ${block({
        title: 'Your plan', icon: 'route',
        action: { label: 'Open', href: '#/plan' },
        body: liveBody(course, d => `${d.progress_pct == null
                  ? empty({ icon: 'route', title: 'Progress not available yet' })
                  : progress(d.progress_pct, `Step ${d.module_no ?? '–'} of 12`, `${d.progress_pct}% complete`)}
          <p style="margin:14px 0 0;font-size:14px;color:var(--on-surface-variant)">
            <strong style="color:var(--primary)">${esc(d.module_title || '')}</strong></p>`,
          { what: 'your plan' })
      })}
    </div>
  </div>

  ${block({
    title: 'Your schedule', icon: 'calendar_month',
    action: { label: 'Open Calendar', href: '#/schedule' },
    body: `${note('No booking system is connected yet, so this calendar is empty rather than filled with made-up appointments.', 'warn')}
      ${calGrid({ year: now.getFullYear(), month: now.getMonth(), today: now.getDate(), items: [], clickable: false })}`
  })}
  `;
}
