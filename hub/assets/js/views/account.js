/* ACCOUNT — business profile, connected apps, AUTONOMY, team, billing, help.
   The autonomy screen is the most important control in the product: it is the
   setting that makes "the owner doesn't have to use this" literally true.
   Lifted from the Stitch reference's content-preferences onboarding step. */

import { pageHead, block, rows, pill, btn, table, note, notConnected,
         filterbar, esc, grid, icon, tiles } from '../ui.js';

const field = (label, value, hint) => `<div style="margin-bottom:16px">
  <label style="display:block;font-size:12.5px;font-weight:650;color:var(--on-surface-variant);
    margin-bottom:6px">${esc(label)}</label>
  <input value="${esc(value)}" style="width:100%;padding:11px 14px;border-radius:11px;
    border:1px solid var(--outline-variant);background:rgba(255,255,255,.8);outline:none;font-size:14.5px">
  ${hint ? `<div style="font-size:12px;color:var(--outline);margin-top:5px">${esc(hint)}</div>` : ''}
</div>`;

export async function business() {
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Your business',
    sub: 'What we know about you. Everything we write, build and publish uses this.',
    actions: [{ label: 'Save changes', kind: 'primary', icon: 'check' }] })}
  ${block({ title: 'The basics', icon: 'storefront', body: `
    <div class="grid grid--2">
      <div>${field('Business name', 'Demo Insulation Co.')}
        ${field('Website', 'https://demoinsulation.com')}
        ${field('Phone', '(555) 123-4567')}</div>
      <div>${field('Email', 'hello@demoinsulation.com')}
        ${field('Industry', 'Insulation & building envelope')}
        ${field('Service area', 'Greater Phoenix, 120km radius',
          'Where you take jobs — this drives every local ranking decision we make.')}</div>
    </div>` })}
  ${block({ title: 'What you sell', icon: 'sell', flush: true, body: rows([
    { icon: 'star', title: 'Spray foam insulation', meta: 'Main offering · has its own page', side: pill('Primary', 'info') },
    { icon: 'check', title: 'Attic top-ups', meta: 'Has its own page', side: pill('Live', 'ok') },
    { icon: 'check', title: 'Crawl space encapsulation', meta: 'Page being built', side: pill('Building', 'warn') },
    { icon: 'add', title: 'Add a service', meta: 'Tell us and we build the page for it', side: btn({ label: 'Add', small: true }) }
  ]) })}
  ${block({ title: 'Who you want', icon: 'groups', body: `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px">
      ${['Homeowners', 'Property managers', 'Builders', 'Commercial'].map(t => pill(t, 'info')).join('')}
    </div>
    <div style="font-size:12.5px;font-weight:650;color:var(--on-surface-variant);margin-bottom:8px">
      What you want more of</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap">
      ${['More leads', 'More phone calls', 'Quote requests', 'Local visibility'].map(t => pill(t, 'ok')).join('')}
    </div>` })}`;
}

export async function platforms() {
  const apps = [
    { name: 'Your website', icon: 'language', on: true, note: 'Forms deliver to you and your phone' },
    { name: 'Google Business Profile', icon: 'storefront', on: true, note: 'Reviews, hours, photos' },
    { name: 'Facebook', icon: 'thumb_up', on: true, note: 'Posting and messages' },
    { name: 'Instagram', icon: 'photo_camera', on: true, note: 'Posting' },
    { name: 'LinkedIn', icon: 'work', on: false, note: 'Not connected' },
    { name: 'Google Analytics', icon: 'analytics', on: false, note: 'Not connected — visitor numbers stay blank until it is' }
  ];
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Connected apps',
    sub: 'What we can reach on your behalf. Connect once and we handle it from there.' })}
  ${note('We never post anywhere you have not connected, and you can disconnect anything at any time.')}
  ${block({ flush: true, body: rows(apps.map(a => ({
    icon: a.icon, title: a.name, meta: a.note,
    side: a.on ? `${pill('Connected', 'ok')}${btn({ label: 'Manage', small: true })}`
               : btn({ label: 'Connect', small: true, kind: 'primary' })
  }))) })}`;
}

export async function autonomy() {
  const modes = [
    { id: 'all', title: 'Show me everything first',
      note: 'Nothing at all goes out until you have looked at it. Most control, most messages from us.',
      chosen: false },
    { id: 'major', title: 'Just the big things',
      note: 'We handle routine posts and updates ourselves, and check with you on campaigns, new pages and anything customer-facing.',
      chosen: true },
    { id: 'auto', title: 'Run it without me',
      note: 'We act on everything. You still see it all here, and we still stop for anything that spends your money or contacts a customer directly.',
      chosen: false }
  ];
  return `
  ${pageHead({ eyebrow: 'Account', title: 'How much we do',
    sub: 'The single most important setting here. It decides how often we interrupt you.' })}
  ${modes.map(m => `<div class="card block lift" style="padding:20px;display:flex;gap:16px;align-items:flex-start;
      ${m.chosen ? 'border-color:var(--secondary);box-shadow:0 0 0 2px rgba(126,219,255,.45)' : ''}">
    <div class="row__icon" style="background:${m.chosen ? 'var(--secondary)' : 'var(--surface-container)'};
      color:${m.chosen ? '#fff' : 'var(--outline)'}">${icon(m.chosen ? 'radio_button_checked' : 'radio_button_unchecked')}</div>
    <div style="flex:1">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:6px">
        <h3 style="font-size:16.5px">${esc(m.title)}</h3>${m.chosen ? pill('Your setting', 'info') : ''}</div>
      <p style="margin:0;font-size:14.5px;color:var(--on-surface-variant);max-width:74ch">${esc(m.note)}</p>
    </div></div>`).join('')}
  ${note('Whatever you pick, three things always wait for you: spending money, signing anything, and messaging a customer directly.')}
  ${block({ title: 'How we reach you', icon: 'notifications', flush: true, body: rows([
    { icon: 'sms', title: 'Text message', meta: 'For anything that needs you today', side: pill('On', 'ok') },
    { icon: 'mail', title: 'Email', meta: 'Weekly summary and monthly report', side: pill('On', 'ok') },
    { icon: 'dashboard', title: 'Here', meta: 'Everything, always, nothing expires', side: pill('Always', 'info') }
  ]) })}`;
}

export async function team({ api } = {}) {
  // Live mode must never show the fixture staff list on a real business's host
  // (host-measured 2026-11-18: a signed-in render still showed "Alex Demo"/"Sam Office").
  // No real staff-login directory is wired yet, so the honest state is empty, not fabricated.
  const live = api?.MODE === 'live';
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Team',
    sub: 'Give your staff their own login. Everyone sees what they need and nothing they should not.',
    actions: [{ label: 'Invite someone', kind: 'primary', icon: 'person_add' }] })}
  ${note('Roles are not switched on yet — right now every login sees everything. Ask us before adding staff.', 'warn')}
  ${block({ flush: true, body: live
    ? notConnected('Staff directory', 'No staff logins are on file yet. Invite someone to see them here.')
    : table({
    cols: [
      { label: 'Person', key: 'name', strong: true },
      { label: 'Email', key: 'email', mute: true },
      { label: 'Role', html: true, get: r => pill(r.role, r.role === 'Owner' ? 'info' : 'neutral') },
      { label: 'Last seen', key: 'seen', mute: true }
    ],
    rows: [
      { name: 'Alex Demo', email: 'alex@demoinsulation.com', role: 'Owner', seen: 'Now' },
      { name: 'Sam Office', email: 'sam@demoinsulation.com', role: 'Staff', seen: '2 days ago' }
    ]
  }) })}`;
}

export async function schedule({ api }) {
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Staff scheduling',
    sub: 'Who’s working when, and payroll for the period.' })}
  ${tiles([
    api.unknownMetric('On shift today', null, { icon: 'badge' }),
    api.unknownMetric('Hours scheduled this week', null, { icon: 'schedule' }),
    api.unknownMetric('Payroll this period', null, { icon: 'payments' })
  ])}
  ${block({ title: 'This week’s shifts', icon: 'calendar_view_week', body:
    notConnected('Staff scheduling', 'Once a scheduling/payroll system is connected, shifts and hours will appear here for every team member listed on the Team page.') })}`;
}

export async function locations() {
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Locations',
    sub: 'Every place you operate, if you have more than one.' })}
  ${block({ body: notConnected('Multiple locations',
    'Your business profile currently models a single location and service area. Once multi-location support is switched on, you’ll be able to add and manage each location here — hours, address, and its own local presence.') })}`;
}

export async function billing() {
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Billing',
    sub: 'Your plan, invoices and payment method.' })}
  ${block({ body: notConnected('Billing',
    'Billing is handled directly with us at the moment. When self-serve invoices and plan changes are switched on, they will appear here.') })}`;
}

export async function help() {
  return `
  ${pageHead({ eyebrow: 'Account', title: 'Help',
    sub: 'A real person, or your agent — whichever you prefer.' })}
  ${grid(`
    ${block({ title: 'Ask your agent', icon: 'auto_awesome',
      body: `<p style="margin:0 0 14px;color:var(--on-surface-variant);font-size:14.5px">
        It can answer almost anything about your account, and do most of it for you.</p>
        ${btn({ label: 'Open chat', kind: 'primary', icon: 'forum', href: '#/ask' })}` })}
    ${block({ title: 'Talk to us', icon: 'support_agent',
      body: `<p style="margin:0 0 14px;color:var(--on-surface-variant);font-size:14.5px">
        Text or call — we answer during business hours and read everything overnight.</p>
        ${btn({ label: 'Send a message', icon: 'sms' })}` })}
  `)}
  ${block({ title: 'Common questions', icon: 'help', flush: true, body: rows([
    { icon: 'help', title: 'How do I approve something?', meta: 'Anything waiting shows on Home under "Your turn"' },
    { icon: 'help', title: 'How do I ask for a new page or campaign?', meta: 'Ask › Requests, or just tell your agent' },
    { icon: 'help', title: 'Where are my videos and photos?', meta: 'Content › Media library' },
    { icon: 'help', title: 'Can I change how often you contact me?', meta: 'Account › How much we do' }
  ]) })}`;
}
