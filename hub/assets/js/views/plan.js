/* YOUR PLAN — the Brand Launch Course, in client language.
   This is the section with the strongest real backing today: 167 items per
   tenant, live on 11 tenants, with a working Clerk-authenticated confirm API. */

import { pageHead, tiles, block, rows, approval, pill, btn, progress,
         stepper, table, filterbar, note, icon, esc, grid } from '../ui.js';

export async function overview({ api }) {
  const res = await api.get('course.state');
  const d = res.data || {};
  return `
  ${pageHead({
    eyebrow: 'Your plan',
    title: 'Getting your business found',
    sub: 'Twelve steps from "nobody can find us" to a complete online presence. We do most of it. You confirm the few things only an owner can.',
    actions: [{ label: 'What needs me', kind: 'primary', icon: 'pan_tool_alt', href: '#/plan/your-turn' }]
  })}
  ${tiles([
    api.metric({ value: d.progress_pct, display: `${d.progress_pct ?? ''}%`, state: 'ok',
      icon: 'donut_large', label: 'Overall progress', delta: { dir: 'up', label: '+4 this month' } }),
    api.metric({ value: d.done, display: String(d.done ?? ''), state: 'ok',
      icon: 'check_circle', label: 'Steps completed' }),
    api.metric({ value: d.awaiting_client, display: String(d.awaiting_client ?? ''),
      state: 'warn', icon: 'pan_tool_alt', label: 'Waiting on you',
      link: { href: '#/plan/your-turn', label: 'Open' } }),
    api.metric({ value: d.brand_score, display: String(d.brand_score ?? ''), state: 'ok',
      icon: 'verified', label: 'Brand health score', delta: { dir: 'up', label: '+14 since day one' } })
  ])}

  ${block({
    title: 'Where you are', icon: 'route',
    body: `${stepper((d.modules || []).map(m => m.short), d.module_index ?? 0)}
      <div style="margin-top:20px">${progress(d.progress_pct ?? 0,
        `Step ${d.module_no ?? '–'} of 12 · ${esc(d.module_title || '')}`,
        `${d.done ?? 0} of ${d.total ?? 0} tasks done`)}</div>`
  })}

  ${grid(`
    ${block({
      title: 'Happening now', icon: 'bolt', flush: true,
      body: rows((d.in_flight || []).map(i => ({
        icon: i.icon, title: i.title, meta: i.meta, side: pill('We\'re on it', 'info')
      })))
    })}
    ${block({
      title: 'Recently finished', icon: 'task_alt', flush: true,
      action: { label: 'All wins', href: '#/plan/wins' },
      body: rows((d.recent_done || []).map(i => ({
        icon: 'check_circle', title: i.title, meta: i.when, side: pill('Done', 'ok')
      })))
    })}
  `)}`;
}

export async function yourTurn({ api }) {
  const res = await api.get('course.next');
  const items = res.data?.client_actions || [];
  return `
  ${pageHead({
    eyebrow: 'Your turn',
    title: 'Only you can do these',
    sub: 'Small things, big effect. Each one takes a few minutes and unlocks work we can then do for you.'
  })}
  ${note('We deliberately show you two at a time. The rest stay queued so this never feels like homework.')}
  ${items.length ? items.map(a => block({
      title: a.module, icon: a.icon || 'flag',
      body: `<h3 style="font-size:17px;margin-bottom:8px">${esc(a.title)}</h3>
        <p style="font-size:14.5px;color:var(--on-surface-variant);margin-bottom:8px;max-width:70ch">
          ${esc(a.why)}</p>
        ${a.how ? `<p style="font-size:14px;color:var(--on-surface-variant);margin-bottom:16px;max-width:70ch">
          <strong style="color:var(--primary)">How:</strong> ${esc(a.how)}</p>` : ''}
        <div class="approval__actions">
          ${btn({ label: 'I did this', kind: 'primary', icon: 'check', act: 'approve' })}
          ${btn({ label: 'Do it for me', icon: 'support_agent', act: 'changes' })}
          ${btn({ label: 'Not now', icon: 'schedule', act: 'view' })}
        </div>`
    })).join('')
    : block({ body: `<div class="empty">${icon('celebration')}
        <div class="empty__title">You're all caught up</div></div>` })}`;
}

export async function modules({ api }) {
  const res = await api.get('course.state');
  const mods = res.data?.modules || [];
  return `
  ${pageHead({ eyebrow: 'Your plan', title: 'The twelve steps',
    sub: 'The whole map, so you always know what is coming and why it matters.' })}
  ${mods.map(m => block({
    title: `${m.no}. ${m.title}`, icon: m.icon || 'check_circle',
    action: { label: m.pct === 100 ? 'Complete' : `${m.pct}%`, href: '#/plan' },
    body: `<p style="margin:0 0 14px;color:var(--on-surface-variant);font-size:14.5px;max-width:74ch">
        ${esc(m.why)}</p>
      ${progress(m.pct, `${m.done} of ${m.total} done`,
        m.awaiting ? `${m.awaiting} waiting on you` : '')}`
  })).join('')}`;
}

export async function wins({ api }) {
  const res = await api.get('course.state');
  const w = res.data?.wins || [];
  return `
  ${pageHead({ eyebrow: 'Your plan', title: 'Wins',
    sub: 'Everything we have finished for you, newest first. Click any of them through to the real page or file.' })}
  ${filterbar([{ label: 'Everything' }, { label: 'Website' }, { label: 'Google' },
               { label: 'Reviews' }, { label: 'Content' }])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'What we did', key: 'title', strong: true },
      { label: 'Area', key: 'area' },
      { label: 'When', key: 'when', mute: true },
      { label: 'Proof', html: true, get: r => `<a class="block__action" href="#">${r.proof} ›</a>` }
    ],
    rows: w
  }) })}`;
}
