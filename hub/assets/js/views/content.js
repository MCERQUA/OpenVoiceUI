/* CONTENT — calendar, approvals, social, articles, media.
   The calendar is the section with NO backing system anywhere on the box today
   (zero future-dated items fleet-wide), so it is drawn as the target design and
   labelled honestly rather than faked into looking connected. */

import { pageHead, tiles, block, table, rows, pill, filterbar, note, btn,
         approval, calendar as calGrid, mediaCard, notConnected, esc, grid, icon, liveBody } from '../ui.js';

export async function overview({ api }) {
  const [soc, med] = await Promise.all([api.get('social.pending'), api.get('media.state')]);
  const s = soc.data || {}, m = med.data || {};
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Everything we make for you',
    sub: 'Posts, articles, videos and images — what is coming, what needs your nod, and everything ever made.',
    actions: [
      { label: 'Ask for something', kind: 'primary', icon: 'auto_awesome', href: '#/ask' },
      { label: 'Calendar', icon: 'calendar_month', href: '#/content/calendar' }] })}
  ${tiles([
    api.metric({ value: s.pending, display: String(s.pending ?? ''), state: 'warn',
      icon: 'rate_review', label: 'Waiting for your nod',
      link: { href: '#/content/approvals', label: 'Review' } }),
    api.metric({ value: s.scheduled, display: String(s.scheduled ?? ''), state: 'ok',
      icon: 'schedule_send', label: 'Scheduled to go out' }),
    api.metric({ value: s.published, display: String(s.published ?? ''), state: 'ok',
      icon: 'send', label: 'Published this quarter' }),
    api.metric({ value: m.total, display: String(m.total ?? ''), state: 'ok',
      icon: 'perm_media', label: 'Videos, images & audio',
      link: { href: '#/content/media', label: 'Library' } })
  ])}
  ${grid(`
    ${block({ title: 'Needs you first', icon: 'pan_tool_alt', flush: true,
      action: { label: 'All', href: '#/content/approvals' },
      body: (s.queue || []).slice(0, 2).map(approval).join('') })}
    ${block({ title: 'Going out next', icon: 'upcoming', flush: true,
      action: { label: 'Calendar', href: '#/content/calendar' },
      body: rows((s.upcoming || []).map(u => ({
        icon: u.icon, title: u.title, meta: u.meta,
        side: pill(u.status, u.status === 'Scheduled' ? 'ok' : 'neutral') }))) })}
  `)}`;
}

export async function calendar({ api }) {
  const res = await api.get('calendar.items');
  const items = res.data?.items || [];
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Content calendar',
    sub: 'Everything planned for the month ahead, across every channel.',
    actions: [{ label: 'Add something', kind: 'primary', icon: 'add' }] })}
  ${filterbar([{ label: 'Month' }, { label: 'Week' }, { label: 'List' }])}
  ${note('This is the one screen with nothing behind it yet — no scheduling system on our side currently holds future-dated content. The design is here so it can be wired up; the entries below are illustrative.', 'warn')}
  ${block({ title: 'August 2026', icon: 'calendar_month', body: calGrid({
    year: 2026, month: 7, today: 25, items
  }) })}
  ${block({ title: 'Legend', icon: 'info', body: `
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <span class="calitem calitem--sched">${icon('share')}Scheduled</span>
      <span class="calitem calitem--pending">${icon('pending')}Needs approval</span>
      <span class="calitem calitem--done">${icon('check')}Published</span>
      <span class="calitem calitem--draft">${icon('edit')}Draft</span>
    </div>` })}`;
}

export async function approvals({ api }) {
  // liveBody distinguishes "fetched fine, queue is genuinely empty" (render below) from
  // "the fetch failed/unknown" (shows "Can't show this right now" with the reason) — a failed
  // social.pending read must never render as "you're all caught up" (host-measured 2026-11-18:
  // content.js was claiming done when the truth was unknown, the exact thing this honesty
  // discipline exists to prevent).
  const res = await api.get('social.pending');
  const q = res.data?.queue || [];
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Waiting for you',
    sub: 'Nothing here goes out until you say so. Approve, ask for changes, or leave it — nothing expires.' })}
  ${filterbar([{ label: 'All', count: res.ok ? q.length : undefined }, { label: 'Social' },
               { label: 'Articles' }, { label: 'Images' }, { label: 'Video' }])}
  ${liveBody(res, data => {
    const items = data?.queue || [];
    return items.length
      ? items.map(a => block({ flush: true, body: approval(a) })).join('')
      : `<div class="empty">${icon('done_all')}
          <div class="empty__title">You're all caught up</div>
          <div class="empty__note">Anything new will show up here and reach you by text.</div></div>`;
  }, { what: 'your approvals queue', icon: 'pending_actions' })}`;
}

export async function social({ api }) {
  const res = await api.get('social.pending');
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Social posts',
    sub: 'What has gone out, what is queued, and how each post did.' })}
  ${filterbar([{ label: 'All' }, { label: 'Awaiting you', count: res.data?.pending },
               { label: 'Scheduled' }, { label: 'Published' }])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Post', key: 'title', strong: true },
      { label: 'Where', key: 'channel' },
      { label: 'When', key: 'when', mute: true },
      { label: 'Stage', html: true, get: r => pill(r.status,
          r.status === 'Published' ? 'ok' : r.status === 'Needs you' ? 'warn' : 'info') },
      { label: 'Seen by', key: 'reach', num: true, mute: true }
    ], rows: res.data?.posts || []
  }) })}`;
}

export async function articles({ api }) {
  const res = await api.get('articles.index');
  if (!res.ok || !res.data?.articles?.length) {
    return `${pageHead({ eyebrow: 'Content', title: 'Articles',
      sub: 'Long-form pieces written for your site.' })}
      ${block({ body: notConnected('Your article library',
        'Articles are being written and published to your site, but nothing indexes them into a list yet. That index is a known piece of work.') })}`;
  }
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Articles',
    sub: 'Long-form pieces written for your site, and how they are performing.' })}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Title', key: 'title', strong: true },
      { label: 'Topic', key: 'topic', mute: true },
      { label: 'Published', key: 'when', mute: true },
      { label: 'Words', key: 'words', num: true },
      { label: 'Visits', key: 'visits', num: true }
    ], rows: res.data.articles
  }) })}`;
}

export async function media({ api }) {
  const res = await api.get('media.state');
  const d = res.data || {};
  return `
  ${pageHead({ eyebrow: 'Content', title: 'Media library',
    sub: 'Every video, image, song and voiceover we have made for you — all in one place, all yours to download.',
    actions: [{ label: 'Ask for something new', kind: 'primary', icon: 'auto_awesome', href: '#/ask' }] })}
  ${filterbar([
    { label: 'Everything', count: d.total }, { label: 'Video', count: d.videos?.length, icon: 'movie' },
    { label: 'Images', count: d.images?.length, icon: 'image' },
    { label: 'Audio', count: d.audio?.length, icon: 'graphic_eq' },
    { label: 'Songs', count: d.songs?.length, icon: 'music_note' }])}
  ${block({ title: 'Recent', icon: 'perm_media', body:
    `<div class="media">${(d.videos || []).map(mediaCard).join('')}</div>` })}
  ${block({ title: 'Images', icon: 'image', body:
    `<div class="media">${(d.images || []).map(mediaCard).join('')}</div>` })}`;
}
