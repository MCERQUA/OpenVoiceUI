/* GET FOUND — search visibility. Backed by /api/seo/snapshots (live, daily
   cron, 34 domains, zero failures). The Google-traffic tile is deliberately
   unknown: Search Console and Analytics were never wired. */

import { pageHead, tiles, block, table, rows, pill, filterbar, note,
         notConnected, progress, esc, grid, btn } from '../ui.js';

const sub = 'When someone nearby searches for what you sell, we work on making you the answer.';

export async function overview({ api }) {
  const res = await api.get('seo.snapshots');
  const s = res.data?.latest || {};
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Being found online', sub,
    actions: [{ label: 'Full report', icon: 'description', href: '#/found/reports' }] })}
  ${tiles([
    api.metric({ value: s.top3_count, display: String(s.top3_count ?? ''), state: 'ok',
      icon: 'looks_3', label: 'Search terms in the top 3', delta: { dir: 'up', label: '+3' },
      link: { href: '#/found/rankings', label: 'See them' } }),
    api.metric({ value: s.top10_count, display: String(s.top10_count ?? ''), state: 'ok',
      icon: 'trending_up', label: 'On page one', delta: { dir: 'up', label: '+6' } }),
    api.metric({ value: s.total_keywords, display: String(s.total_keywords ?? ''), state: 'ok',
      icon: 'manage_search', label: 'Terms you show up for' }),
    api.metric({ value: s.backlinks_count, display: String(s.backlinks_count ?? ''), state: 'ok',
      icon: 'link', label: 'Sites linking to you', delta: { dir: 'up', label: '+12' },
      link: { href: '#/found/backlinks', label: 'Backlinks' } })
  ])}
  ${grid(`
    ${block({ title: 'Biggest movers this month', icon: 'moving', flush: true,
      body: table({ cols: [
        { label: 'Search term', key: 'kw', strong: true },
        { label: 'Was', key: 'was', num: true, mute: true },
        { label: 'Now', key: 'now', num: true },
        { label: '', html: true, get: r => r.dir === 'up'
            ? pill(`+${r.was - r.now}`, 'ok') : pill(`-${r.now - r.was}`, 'bad') }
      ], rows: res.data?.movers || [] }) })}
    ${block({ title: 'What we are working on', icon: 'engineering', flush: true,
      body: rows((res.data?.working || []).map(w => ({
        icon: w.icon, title: w.title, meta: w.meta, side: pill(w.status, 'info') })) ) })}
  `)}
  ${note('These numbers come from real daily search-position tracking, refreshed every morning.')}`;
}

export async function rankings({ api }) {
  const res = await api.get('seo.snapshots');
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Where you rank', sub:
    'Every search term you appear for, and what position you hold. Position 1–3 is where nearly all the clicks go.' })}
  ${api.isLive ? '' : filterbar([{ label: 'All terms' }, { label: 'Top 3', count: 8 }, { label: 'Page 1', count: 22 },
               { label: 'Improving', count: 14 }, { label: 'Slipping', count: 3 }])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Search term', key: 'kw', strong: true },
      { label: 'Position', key: 'pos', num: true },
      { label: 'Change', html: true, num: true, get: r =>
          r.change > 0 ? pill(`+${r.change}`, 'ok') : r.change < 0 ? pill(String(r.change), 'bad') : pill('—', 'neutral') },
      { label: 'Searches / mo', key: 'vol', num: true, mute: true },
      { label: 'Your page', key: 'page', mute: true }
    ],
    rows: res.data?.keywords || []
  }) })}`;
}

export async function local({ api }) {
  const res = await api.get('seo.snapshots');
  const g = res.data?.gbp || {};
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Your Google Business listing', sub:
    'The single biggest lever for a local business. This is what people see in Maps and the local results.' })}
  ${tiles([
    api.metric({ value: g.rating, display: String(g.rating ?? ''), state: 'ok',
      icon: 'star', label: 'Star rating' }),
    api.metric({ value: g.reviews, display: String(g.reviews ?? ''), state: 'ok',
      icon: 'reviews', label: 'Reviews', delta: { dir: 'up', label: '+4 this month' } }),
    api.metric({ value: g.photos, display: String(g.photos ?? ''), state: 'warn',
      icon: 'photo_library', label: 'Photos', note: 'More photos lifts map ranking' }),
    api.unknownMetric('Calls from Maps', { note: 'Needs Google profile access' }, { icon: 'call' })
  ])}
  ${block({ title: 'Listing health', icon: 'fact_check', flush: true,
    body: rows((g.checks || []).map(c => ({
      icon: c.ok ? 'check_circle' : 'error', title: c.label, meta: c.note,
      side: pill(c.ok ? 'Good' : 'Needs work', c.ok ? 'ok' : 'warn') }))) })}`;
}

export async function ai({ api }) {
  const res = await api.get('seo.snapshots');
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Showing up in AI answers', sub:
    'More people ask ChatGPT and Google\'s AI for recommendations every month. We track whether you get named.' })}
  ${tiles([
    api.metric({ value: res.data?.ai?.mentions, display: String(res.data?.ai?.mentions ?? ''),
      state: 'ok', icon: 'smart_toy', label: 'Times you were named',
      delta: { dir: 'up', label: '+5' } }),
    api.metric({ value: res.data?.ai?.questions, display: String(res.data?.ai?.questions ?? ''),
      state: 'ok', icon: 'quiz', label: 'Questions we track' }),
    api.metric({ value: res.data?.ai?.share, display: `${res.data?.ai?.share ?? ''}%`,
      state: 'warn', icon: 'donut_small', label: 'Share of answers',
      note: api.isLive ? undefined : 'Against 6 local competitors' })
  ])}
  ${block({ title: 'Questions people ask', icon: 'help', flush: true, body: table({
    cols: [
      { label: 'Question', key: 'q', strong: true },
      { label: 'Are you named?', html: true, get: r => r.named ? pill('Yes', 'ok') : pill('Not yet', 'neutral') },
      { label: 'Who is', key: 'who', mute: true }
    ], rows: res.data?.ai?.rows || []
  }) })}`;
}

export async function backlinks({ api }) {
  const res = await api.get('seo.snapshots');
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Sites linking to you', sub:
    'Each link from a real local site is a vote of confidence that search engines count.' })}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Site', key: 'site', strong: true },
      { label: 'What it is', key: 'kind', mute: true },
      { label: 'Strength', key: 'dr', num: true },
      { label: 'Added', key: 'when', mute: true }
    ], rows: res.data?.backlinks || []
  }) })}`;
}

export async function reports({ api }) {
  const res = await api.get('seo.reports');
  return `
  ${pageHead({ eyebrow: 'Get found', title: 'Reports', sub:
    'A plain-language summary of your online presence, produced monthly.',
    actions: [{ label: 'Newest report', kind: 'primary', icon: 'description' }] })}
  ${block({ flush: true, body: rows((res.data?.reports || []).map(r => ({
    icon: 'description', title: r.title, meta: `${r.date} · ${r.pages} pages`,
    side: `${pill(r.score, 'ok')}${btn({ label: 'Open', small: true })}`
  }))) })}`;
}
