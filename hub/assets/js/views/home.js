/* HOME — the five-second answer: what's happening, and what do you need from me.
   Deliberately opens with YOUR TURN, not with metrics. The whole product
   thesis is that the owner doesn't have to do anything except the few things
   only they can do. */

import { pageHead, tiles, block, rows, approval, pill, btn, note,
         progress, icon, esc, calendar as calGrid } from '../ui.js';
import { liveHome } from './home-live.js';

const DOW_NAMES = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'];
const MONTH_NAMES = ['January','February','March','April','May','June','July',
  'August','September','October','November','December'];

export default async function home({ api }) {
  if (api.isLive) return liveHome({ api });
  const [course, leads, seo] = await Promise.all([
    api.get('course.next'), api.get('leads.stats'), api.get('seo.snapshots')
  ]);
  const now = new Date();
  const todayStr = `${DOW_NAMES[now.getDay()]}, ${now.getDate()} ${MONTH_NAMES[now.getMonth()]}`;

  const c = course.data || {};
  const l = leads.data?.stats || {};
  const s = seo.data?.latest || {};

  const kpis = [
    api.metric({ value: c.awaiting_client, display: String(c.awaiting_client ?? ''),
      state: c.awaiting_client > 10 ? 'warn' : 'ok', icon: 'task_alt',
      label: 'Waiting on you', note: 'Things only you can do',
      link: { href: '#/plan/your-turn', label: 'Do them' } }),
    // Money and today's schedule surface here even though nothing backs them
    // yet — a business owner looks for these first, before marketing metrics.
    api.unknownMetric('Revenue this month', null,
      { icon: 'payments', link: { href: '#/finances', label: 'Finances' } }),
    api.unknownMetric('Today’s appointments', null,
      { icon: 'today', link: { href: '#/schedule', label: 'Schedule' } }),
    api.metric({ value: l.new_count, display: String(l.new_count ?? ''), state: 'ok',
      icon: 'contact_phone', label: 'New leads this month',
      delta: { dir: 'up', label: '+18%' },
      link: { href: '#/customers/leads', label: 'See leads' } }),
    api.metric({ value: s.top10_count, display: String(s.top10_count ?? ''), state: 'ok',
      icon: 'trending_up', label: 'Keywords on page 1',
      delta: { dir: 'up', label: '+6' },
      link: { href: '#/found/rankings', label: 'Rankings' } }),
    // Deliberately unknown — Search Console was never wired. It must not read 0.
    api.unknownMetric('Website visitors', { note: 'Google Analytics isn\'t connected yet' },
      { icon: 'group' })
  ];

  const turn = (c.client_actions || []).slice(0, 2);

  return `
  ${pageHead({
    eyebrow: todayStr,
    // Live mode must never show the fixture name on a real business's host (host-measured
    // 2026-11-18). No real signed-in-owner-name source is wired into this app yet, so the
    // honest greeting drops the name rather than inventing one.
    title: api.MODE === 'live' ? 'Good morning' : 'Good morning, Alex',
    sub: 'Your system worked overnight. Here is what changed, and the few things only you can do.',
    actions: [
      { label: 'Ask your agent', kind: 'primary', icon: 'auto_awesome', href: '#/ask' },
      { label: 'Request work', icon: 'add', href: '#/ask/requests' }
    ]
  })}

  ${tiles(kpis)}

  <div class="grid grid--sidebar">
    <div>
      ${block({
        title: 'Your turn', icon: 'pan_tool_alt',
        action: { label: `All ${c.awaiting_client ?? ''}`, href: '#/plan/your-turn' },
        flush: true,
        body: turn.length
          ? turn.map(a => approval({
              title: a.title, excerpt: a.why, channel: a.module,
              status: 'Needs you', icon: a.icon || 'flag', when: a.when
            })).join('<div style="height:1px;background:rgba(196,198,207,.25)"></div>')
          : `<div class="empty">${icon('celebration')}
             <div class="empty__title">Nothing needs you right now</div>
             <div class="empty__note">We'll put anything that needs your call right here.</div></div>`
      })}

      ${block({
        title: 'What we did while you were away', icon: 'bolt', flush: true,
        action: { label: 'Everything', href: '#/plan/wins' },
        body: rows((c.recent || []).map(r => ({
          icon: r.icon || 'check_circle', title: r.title, meta: r.when,
          side: pill(r.kind || 'done', r.kind === 'live' ? 'ok' : 'info')
        })))
      })}
    </div>

    <div>
      ${block({
        title: 'Across your business', icon: 'dashboard', flush: true,
        body: [
          { href: '#/finances', icn: 'account_balance', title: 'Finances', meta: 'Revenue, invoices, expenses' },
          { href: '#/ads',      icn: 'campaign',        title: 'Advertising', meta: 'Google, Facebook, Instagram' },
          { href: '#/inventory',icn: 'inventory_2',     title: 'Inventory', meta: 'Products and stock levels' },
          { href: '#/support',  icn: 'support_agent',   title: 'Support', meta: 'Open tickets' }
        ].map(r => `<a class="row" href="${esc(r.href)}">
            <div class="row__icon">${icon(r.icn)}</div>
            <div class="row__main">
              <div class="row__title">${esc(r.title)}</div>
              <div class="row__meta">${esc(r.meta)}</div>
            </div>
            <div class="row__side">${pill('Not connected', 'unknown')}${icon('chevron_right')}</div>
          </a>`).join('')
      })}

      ${block({
        title: 'Your plan', icon: 'route',
        action: { label: 'Open', href: '#/plan' },
        body: `${progress(c.progress_pct ?? 0,
                  `Step ${c.module_no ?? '–'} of 12`, `${c.progress_pct ?? 0}% complete`)}
          <p style="margin:14px 0 0;font-size:14px;color:var(--on-surface-variant)">
            <strong style="color:var(--primary)">${esc(c.module_title || '')}</strong><br>
            ${esc(c.module_note || '')}</p>`
      })}

      ${block({
        title: 'Coming up', icon: 'event', flush: true,
        action: { label: 'Calendar', href: '#/content/calendar' },
        body: rows((c.upcoming || []).map(u => ({
          icon: u.icon, title: u.title, meta: u.when,
          side: pill(u.status, u.status === 'Scheduled' ? 'ok' : 'neutral')
        })))
      })}

      ${block({
        title: 'How much we do', icon: 'tune',
        body: `<p style="margin:0 0 12px;font-size:14px;color:var(--on-surface-variant)">
            You're set to <strong style="color:var(--primary)">Approve major things only</strong> —
            we handle routine posts, you review campaigns.</p>
          ${btn({ label: 'Change this', icon: 'settings', small: true, href: '#/account/autonomy' })}`
      })}
    </div>
  </div>

  ${block({
    title: 'Your schedule', icon: 'calendar_month',
    action: { label: 'Open Calendar', href: '#/schedule' },
    body: `${note('No booking or appointments system is connected yet, so this calendar is genuinely empty rather than filled with placeholder bookings. Click the + on any day to add one — full appointment management lives under Calendar.', 'warn')}
      ${calGrid({ year: now.getFullYear(), month: now.getMonth(), today: now.getDate(), items: [], clickable: true })}`
  })}

  ${note('Anything that needs your approval also reaches you by text and email — the dashboard is just the place it never gets lost.')}
  `;
}
