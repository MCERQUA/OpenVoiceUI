/* WEBSITE — the client's digital HQ: pages, blog engine, topic map, forms. */

import { pageHead, tiles, block, table, rows, pill, filterbar, note, btn,
         notConnected, progress, stepper, esc, grid, icon, liveBody } from '../ui.js';

export async function overview({ api }) {
  const res = await api.get('website.builds');
  const d = res.data || {};
  return `
  ${pageHead({ eyebrow: 'Website', title: 'Your website',
    sub: 'Your digital headquarters — every page, whether it is healthy, and what we are adding next.',
    actions: [{ label: 'Visit your site', kind: 'primary', icon: 'open_in_new' }] })}
  ${tiles([
    ...(res.ok ? [
      api.metric({ value: d.pages, display: String(d.pages ?? ''), state: 'ok',
        icon: 'description', label: 'Pages live', delta: { dir: 'up', label: '+3' } }),
      api.metric({ value: d.speed, display: String(d.speed ?? ''), state: 'ok',
        icon: 'speed', label: 'Speed score' }),
      api.metric({ value: d.forms, display: String(d.forms ?? ''), state: 'ok',
        icon: 'contact_page', label: 'Working contact forms',
        link: { href: '#/website/forms', label: 'Check' } })
    ] : [
      api.unknownMetric('Pages live', res, { icon: 'description' }),
      api.unknownMetric('Speed score', res, { icon: 'speed' }),
      api.unknownMetric('Working contact forms', res, { icon: 'contact_page', link: { href: '#/website/forms', label: 'Check' } })
    ]),
    api.unknownMetric('Visitors this month', { note: 'Analytics isn\'t connected yet' }, { icon: 'group' })
  ])}
  ${grid(`
    ${block({ title: 'Recently added', icon: 'new_releases', flush: true,
      body: rows((d.recent || []).map(r => ({
        icon: 'article', title: r.title, meta: r.when, side: pill('Live', 'ok') }))) })}
    ${block({ title: 'Being built now', icon: 'engineering',
      body: (d.building || []).map(b => `<div style="margin-bottom:16px">
        <div style="font-weight:650;margin-bottom:8px">${esc(b.title)}</div>
        ${progress(b.pct, b.stage, `${b.pct}%`)}</div>`).join('') || note('Nothing in the build queue.') })}
  `)}`;
}

export async function pages({ api }) {
  const res = await api.get('website.builds');
  return `
  ${pageHead({ eyebrow: 'Website', title: 'Pages',
    sub: 'Every page on your site and how it is doing in search.' })}
  ${filterbar([{ label: 'All pages' }, { label: 'Services' }, { label: 'Locations' },
               { label: 'Blog' }, { label: 'Needs work', count: api.isLive ? undefined : 2 }])}
  ${liveBody(res, data => block({ flush: true, body: table({
    cols: [
      { label: 'Page', key: 'title', strong: true },
      { label: 'Address', key: 'url', mute: true },
      { label: 'Health', html: true, get: r => pill(r.health, r.health === 'Good' ? 'ok' : 'warn') },
      { label: 'Ranks for', key: 'kws', num: true },
      { label: '', html: true, get: () => btn({ label: 'View', small: true }) }
    ], rows: data?.pageList || []
  }) }), { what: 'your pages', icon: 'description' })}`;
}

export async function blog({ api }) {
  return `
  ${pageHead({ eyebrow: 'Website', title: 'Blog engine',
    sub: 'We research and publish articles that answer what your customers actually search for.' })}
  ${api.isLive
    // No blog-engine source is wired into /api/biz/v1 yet — this whole page was hardcoded
    // sample content that would look like a real publishing schedule to a client (host-measured
    // 2026-11-18). Show the honest "not connected" state instead; fixture mode is unchanged.
    ? block({ body: notConnected('Your blog engine', 'Your blog engine isn’t connected yet.') })
    : `${block({ title: 'How it works', icon: 'conveyor_belt', body:
    stepper(['Pick the topic', 'Research it', 'Write it', 'You review', 'Publish', 'Track it'], 3) })}
  ${note('Set to publish two articles a month. You can change the pace, or ask for a specific topic any time.')}
  ${grid(`
    ${block({ title: 'Publishing schedule', icon: 'event_repeat', body: `
      <div class="stack">
        <div class="row" style="padding-left:0;padding-right:0"><div class="row__main">
          <div class="row__title">How often</div>
          <div class="row__meta">Two articles per month</div></div>
          <div class="row__side">${btn({ label: 'Change', small: true })}</div></div>
        <div class="row" style="padding-left:0;padding-right:0;border:0"><div class="row__main">
          <div class="row__title">Before publishing</div>
          <div class="row__meta">We show you a draft first</div></div>
          <div class="row__side">${pill('On', 'ok')}</div></div>
      </div>` })}
    ${block({ title: 'Coming up', icon: 'upcoming', flush: true, body: rows([
      { icon: 'edit_note', title: 'How much does attic insulation cost?', meta: 'Researching · due 2 Sept', side: pill('In progress', 'info') },
      { icon: 'schedule', title: 'Signs your insulation has failed', meta: 'Queued · due 16 Sept', side: pill('Queued', 'neutral') }
    ]) })}
  `)}`}`;
}

export async function map({ api }) {
  return `
  ${pageHead({ eyebrow: 'Website', title: 'Topic map',
    sub: 'The full set of questions your customers ask, and which ones your site already answers.' })}
  ${api.isLive
    // Same reasoning as blog() above: no topic-map source is wired yet, and these were
    // hardcoded sample topics/counts that would read as real client data.
    ? block({ body: notConnected('Your topic map', 'Your topic map isn’t connected yet.') })
    : `${tiles([
    api.metric({ value: 48, display: '48', state: 'ok', icon: 'account_tree', label: 'Topics identified' }),
    api.metric({ value: 19, display: '19', state: 'ok', icon: 'check_circle', label: 'Already covered' }),
    api.metric({ value: 29, display: '29', state: 'warn', icon: 'pending', label: 'Still to write' })
  ])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Topic', key: 'topic', strong: true },
      { label: 'Cluster', key: 'cluster', mute: true },
      { label: 'Searches / mo', key: 'vol', num: true },
      { label: 'Status', html: true, get: r => pill(r.status, r.status === 'Live' ? 'ok' : 'neutral') }
    ],
    rows: [
      { topic: 'Attic insulation cost', cluster: 'Cost', vol: 1300, status: 'Live' },
      { topic: 'Spray foam vs fibreglass', cluster: 'Comparison', vol: 880, status: 'Live' },
      { topic: 'Closed cell vs open cell', cluster: 'Comparison', vol: 720, status: 'Planned' },
      { topic: 'Insulation rebates', cluster: 'Money', vol: 590, status: 'Planned' },
      { topic: 'Crawl space insulation', cluster: 'Service', vol: 480, status: 'Live' },
      { topic: 'How long does insulation last', cluster: 'Education', vol: 320, status: 'Planned' }
    ]
  }) })}`}`;
}

export async function forms({ api }) {
  return `
  ${pageHead({ eyebrow: 'Website', title: 'Forms & enquiries',
    sub: 'Every contact form on your site, and proof that enquiries are actually reaching you.' })}
  ${api.isLive
    // Same reasoning again: no form-monitoring source is wired yet, and these were hardcoded
    // "Working" rows that would falsely tell a client their forms were tested today.
    ? block({ body: notConnected('Form monitoring', 'Form monitoring isn’t connected yet.') })
    : `${note('We test every form automatically. If one ever stops delivering, you get a text the same day — a silent broken form is the most expensive thing on a website.')}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Form', key: 'form', strong: true },
      { label: 'On page', key: 'page', mute: true },
      { label: 'Delivers to', key: 'to', mute: true },
      { label: 'Last test', key: 'tested', mute: true },
      { label: 'Status', html: true, get: r => pill(r.ok ? 'Working' : 'Broken', r.ok ? 'ok' : 'bad') }
    ],
    rows: [
      { form: 'Get a quote', page: '/contact', to: 'you + your phone', tested: 'today', ok: true },
      { form: 'Free estimate', page: '/', to: 'you + your phone', tested: 'today', ok: true },
      { form: 'Book a callback', page: '/services', to: 'you', tested: 'today', ok: true }
    ]
  }) })}`}`;
}
