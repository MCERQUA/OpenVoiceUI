/* ASK — the agent surface. Chat, requests, history.
   Deliberately NOT the centre of the product: the thesis is that the owner
   shouldn't have to ask for things. This is the escape hatch when they want to. */

import { pageHead, block, rows, pill, btn, table, note, progress,
         stepper, filterbar, esc, grid, icon } from '../ui.js';

export async function chat() {
  const suggestions = [
    { icon: 'lightbulb', text: 'What should I post about this week?' },
    { icon: 'campaign',  text: 'Write a promotion for a slow month' },
    { icon: 'insights',  text: 'How did we do last month?' },
    { icon: 'edit_note', text: 'Reply to my newest Google review' },
    { icon: 'photo_camera', text: 'Turn these job photos into a post' },
    { icon: 'call',      text: 'Who should I follow up with today?' }
  ];
  return `
  ${pageHead({ eyebrow: 'Ask', title: 'Ask your agent',
    sub: 'It already knows your business, your customers, your brand and everything we have built. Ask in plain words.' })}
  ${block({ body: `
    <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
      <textarea rows="3" placeholder="Ask anything, or tell it what you want done…"
        style="flex:1;min-width:240px;border:1px solid var(--outline-variant);border-radius:14px;
        padding:14px 16px;resize:vertical;background:rgba(255,255,255,.8);outline:none;
        font-size:15px;line-height:1.5"></textarea>
      <div style="display:flex;gap:8px">
        ${btn({ label: 'Speak', icon: 'mic' })}
        ${btn({ label: 'Send', kind: 'primary', icon: 'arrow_upward' })}
      </div>
    </div>` })}
  ${block({ title: 'Things people ask', icon: 'auto_awesome', body: `
    <div class="grid grid--3">
      ${suggestions.map(s => `<button class="card lift" style="padding:15px;text-align:left;
        display:flex;gap:11px;align-items:flex-start">
        <span class="row__icon" style="width:34px;height:34px">${icon(s.icon)}</span>
        <span style="font-size:14px;color:var(--on-surface-variant);line-height:1.45">${esc(s.text)}</span>
      </button>`).join('')}
    </div>` })}
  ${block({ title: 'What it knows about you', icon: 'storefront', flush: true, body: rows([
    { icon: 'check_circle', title: 'Your services and pricing', meta: 'From your setup answers', side: pill('Loaded', 'ok') },
    { icon: 'check_circle', title: 'Your brand voice', meta: 'Warm, straightforward, no jargon', side: pill('Loaded', 'ok') },
    { icon: 'check_circle', title: 'Your customers and jobs', meta: 'Synced continuously', side: pill('Loaded', 'ok') },
    { icon: 'check_circle', title: 'Everything we have published', meta: 'Site, posts, articles, media', side: pill('Loaded', 'ok') }
  ]) })}`;
}

export async function requests() {
  const stages = ['Asked', 'Started', 'In review', 'Your approval', 'Done'];
  const list = [
    { title: 'Autumn promotion across social', note: 'Graphics and copy for a two-week push on attic top-ups.',
      due: 'Due 5 Sept', pct: 60, step: 1, status: 'In progress' },
    { title: 'New service page: crawl spaces', note: 'Full page, photos, schema and internal links.',
      due: 'Due 12 Sept', pct: 90, step: 3, status: 'Needs you' },
    { title: 'Update the team photos on About', note: 'Waiting on new headshots from you.',
      due: 'No date yet', pct: 10, step: 0, status: 'Waiting on you' }
  ];
  return `
  ${pageHead({ eyebrow: 'Ask', title: 'Requests',
    sub: 'Ask for anything — a page, a campaign, a video, a change. Track it from here.',
    actions: [{ label: 'New request', kind: 'primary', icon: 'add' }] })}
  ${filterbar([{ label: 'All', count: 3 }, { label: 'In progress', count: 1 },
               { label: 'Needs you', count: 2 }, { label: 'Done', count: 14 }])}
  ${list.map(r => block({
    title: r.title,
    icon: 'assignment',
    action: { label: r.due },
    body: `<p style="margin:0 0 16px;color:var(--on-surface-variant);font-size:14.5px;max-width:74ch">
        ${esc(r.note)}</p>
      ${progress(r.pct, r.status, `${r.pct}%`)}
      <div style="margin-top:20px">${stepper(stages, r.step)}</div>`
  })).join('')}`;
}

export async function history() {
  return `
  ${pageHead({ eyebrow: 'Ask', title: 'History',
    sub: 'Every conversation, text and email between you and your agent.' })}
  ${filterbar([{ label: 'Everything' }, { label: 'Chat' }, { label: 'Texts' }, { label: 'Email' }, { label: 'Calls' }])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'When', key: 'when', mute: true },
      { label: 'How', html: true, get: r => pill(r.how, 'neutral') },
      { label: 'About', key: 'about', strong: true },
      { label: '', html: true, get: () => btn({ label: 'Read', small: true }) }
    ],
    rows: [
      { when: 'Today, 8:12am', how: 'Text', about: 'Approved the autumn promo graphics' },
      { when: 'Yesterday', how: 'Chat', about: 'Asked for post ideas for September' },
      { when: '22 Aug', how: 'Email', about: 'Monthly report — August' },
      { when: '20 Aug', how: 'Text', about: 'New enquiry from the quote form: R. Mendez' },
      { when: '18 Aug', how: 'Chat', about: 'Reviewed the crawl space page draft' }
    ]
  }) })}`;
}
