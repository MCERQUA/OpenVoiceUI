/* LIVE SECTIONS — Customers, Payments and Team for a real business on its own address
   (https://<tenant>.jam-bot.com/hub/). Reads /api/biz/v1 collections (contact, job, invoice,
   payment, person) and the hub summary, same-origin with the owner's Clerk session.

   Three states only, everywhere: real data · "Nothing here yet" (measured, none recorded) ·
   "Can't show this right now" with the reason (could not read) — plus "isn't connected yet" for
   parts with no system behind them (payment links, payouts, team chat, shared files).
   The fixture views in customers.js / payments.js / team.js are unchanged and still drive the
   jamdash.jam-bot.com design sandbox. Buttons that would write are not shown here: the Hub
   reads; changes are made through the business's agent until write flows are built. */

import { pageHead, tiles, block, table, rows, pill, filterbar, note, esc, grid, icon,
         notConnected, empty, liveBody } from '../ui.js';
import { sectionTile, money } from './home-live.js';

const nothing = (what, note) => empty({ icon: 'inbox', title: 'Nothing here yet', note: note || `No ${what} recorded yet.` });

/* ============================== CUSTOMERS ============================== */
export const customers = {
  async overview({ api }) {
    const [hub, crm] = await Promise.all([api.get('hub.summary'), api.get('crm.pipeline')]);
    const S = hub.ok ? hub.data.sections : {};
    const mc = S.money?.counts || {};
    return `
    ${pageHead({ eyebrow: 'Customers', title: 'Your customers',
      sub: 'Everyone who has reached out, everyone you have worked with, and what is in flight.' })}
    ${hub.ok ? tiles([
      sectionTile(api, S.leads, { label: 'New leads', icon: 'contact_phone', value: S.leads?.counts?.new,
        link: { href: '#/customers/leads', label: 'Open' } }),
      sectionTile(api, S.customers, { label: 'Customers', icon: 'groups', value: S.customers?.counts?.customers,
        link: { href: '#/customers/contacts', label: 'Open' } }),
      sectionTile(api, S.jobs, { label: 'Open jobs', icon: 'handshake', value: S.jobs?.counts?.open,
        link: { href: '#/customers/jobs', label: 'Open' } }),
      sectionTile(api, S.money, { label: 'Open estimates', icon: 'request_quote',
        value: mc.estimates_open_value, display: money(mc.estimates_open_value) })
    ]) : block({ body: liveBody(hub, () => '', { what: 'your customer numbers' }) })}
    ${block({ title: 'Recently updated customers', icon: 'groups', flush: true,
      action: { label: 'All contacts', href: '#/customers/contacts' },
      body: liveBody(crm, d => d.contacts_list.length
        ? rows(d.contacts_list.slice(0, 6).map(r => ({
            icon: 'person', title: r.name,
            meta: [r.company !== '—' ? r.company : '', r.last !== '—' ? `updated ${r.last}` : ''].filter(Boolean).join(' · '),
            side: r.jobs ? pill(`${r.jobs} job${r.jobs === 1 ? '' : 's'}`, 'info') : '' })))
        : nothing('customers'), { what: 'your customers' }) })}`;
  },

  async leads({ api }) {
    const [res, st] = await Promise.all([api.get('leads.list'), api.get('leads.stats')]);
    const s = st.ok ? st.data.stats : null;
    return `
    ${pageHead({ eyebrow: 'Customers', title: 'Leads',
      sub: 'Every enquiry from your website forms, in one list.' })}
    ${s ? filterbar([
      { label: 'All', count: s.total }, { label: 'New', count: s.new_count },
      { label: 'Contacted', count: s.contacted }, { label: 'Quoted', count: s.quoted },
      { label: 'Won', count: s.won }
    ]) : ''}
    ${block({ flush: true, body: liveBody(res, d => table({
      cols: [
        { label: 'Name', key: 'name', strong: true },
        { label: 'What they want', key: 'what' },
        { label: 'From', key: 'site', mute: true },
        { label: 'When', key: 'when', mute: true },
        { label: 'Stage', html: true, get: r => pill(r.stage, r.stage === 'New' ? 'info' : 'ok') }
      ], rows: d.leads,
      empty: { icon: 'inbox', title: 'Nothing here yet', note: 'New enquiries from your website appear here as they arrive.' }
    }), { what: 'your leads' }) })}`;
  },

  async contacts({ api }) {
    const res = await api.get('crm.pipeline');
    const n = res.ok ? res.data.contacts : null;
    return `
    ${pageHead({ eyebrow: 'Customers', title: 'Contacts',
      sub: n !== null && n !== undefined ? `Your customer book — ${n} ${n === 1 ? 'person' : 'people'} on file.`
                                         : 'Your customer book — everyone you have quoted, worked for, or spoken to.' })}
    ${block({ flush: true, body: liveBody(res, d => table({
      cols: [
        { label: 'Name', key: 'name', strong: true },
        { label: 'Company', key: 'company' },
        { label: 'Phone', key: 'phone', mute: true },
        { label: 'Last update', key: 'last', mute: true },
        { label: 'Jobs', key: 'jobs', num: true }
      ], rows: d.contacts_list,
      empty: { icon: 'groups', title: 'Nothing here yet', note: 'Customers you or your agent add will appear here.' }
    }), { what: 'your contacts' }) })}`;
  },

  async pipeline({ api }) {
    const res = await api.get('crm.pipeline');
    return `
    ${pageHead({ eyebrow: 'Customers', title: 'Pipeline',
      sub: 'Your jobs by stage, from first enquiry to paid.' })}
    ${block({ title: 'Stages', icon: 'view_kanban', body: liveBody(res, d => d.stages.length ? `
      <div class="grid grid--3">
        ${d.stages.map(s => `<div class="card" style="padding:16px">
          <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
            <span class="pill pill--info">${esc(s.label)}</span>
          </div>
          <div style="font-family:var(--font-display);font-size:26px;font-weight:700;color:var(--primary)">${esc(s.count)}</div>
          <div style="font-size:12.5px;color:var(--outline);margin-top:5px">job${s.count === 1 ? '' : 's'}</div>
        </div>`).join('')}
      </div>` : nothing('jobs', 'Jobs appear here by stage once they are recorded.'), { what: 'your pipeline' }) })}`;
  },

  async jobs({ api }) {
    const res = await api.get('jobs.list');
    return `
    ${pageHead({ eyebrow: 'Customers', title: 'Jobs & quotes',
      sub: 'Every job, who it is for, where, and what stage it is at.' })}
    ${block({ flush: true, body: liveBody(res, d => table({
      cols: [
        { label: 'Job', key: 'id', strong: true },
        { label: 'What', key: 'title' },
        { label: 'Customer', key: 'customer' },
        { label: 'Site', key: 'site', mute: true },
        { label: 'Scheduled', key: 'when', mute: true },
        { label: 'Stage', html: true, get: r => pill(r.status, 'info') }
      ], rows: d.jobs,
      empty: { icon: 'construction', title: 'Nothing here yet', note: 'Jobs you or your agent record will appear here.' }
    }), { what: 'your jobs' }) })}`;
  }
};

/* ============================== PAYMENTS ============================== */
const statusPill = s =>
  s === 'paid' ? pill('Paid', 'ok')
  : s === 'overdue' ? pill('Overdue', 'bad')
  : s === 'sent' || s === 'partial' ? pill(s === 'partial' ? 'Part paid' : 'Awaiting payment', 'warn')
  : s === 'draft' ? pill('Draft', 'neutral')
  : s === 'void' ? pill('Void', 'neutral')
  : pill(s, 'neutral');

export const payments = {
  async overview({ api }) {
    const [sum, recent] = await Promise.all([api.get('payments.summary'), api.get('payments.recent')]);
    const d = sum.ok ? sum.data : null, s = d ? d.summary : {};
    const sec = d ? { state: d.state, verdict: d.verdict, reason: d.reason } : null;
    const noProcessor = 'Needs a payment processor — not connected yet';
    return `
    ${pageHead({ eyebrow: 'Payments', title: 'Getting paid',
      sub: 'Every dollar coming in: payments received and invoices still out.' })}
    ${d ? tiles([
      sectionTile(api, sec, { label: 'Collected in the last 30 days', icon: 'payments',
        value: s.collectedThisMonth, display: money(s.collectedThisMonth),
        note: s.collectedCount === null ? undefined : `${s.collectedCount} payment${s.collectedCount === 1 ? '' : 's'} received` }),
      sectionTile(api, sec, { label: 'Awaiting payment', icon: 'hourglass_top',
        value: s.pending, display: money(s.pending), link: { href: '#/payments/invoices', label: 'Invoices' },
        note: s.pendingCount === null ? undefined : `${s.pendingCount} unpaid invoice${s.pendingCount === 1 ? '' : 's'}` }),
      sectionTile(api, sec, { label: 'Overdue', icon: 'warning',
        value: s.overdue, display: money(s.overdue),
        note: s.overdueCount === null ? undefined : (s.overdueCount ? `${s.overdueCount} invoice${s.overdueCount === 1 ? '' : 's'} past due` : 'Nothing past due') }),
      api.unknownMetric('Next payout', { note: noProcessor }, { icon: 'account_balance' })
    ]) : block({ body: liveBody(sum, () => '', { what: 'your payment totals' }) })}
    ${grid(`
      ${block({ title: 'Latest payments', icon: 'credit_card', flush: true,
        action: { label: 'All invoices', href: '#/payments/invoices' },
        body: liveBody(recent, r => r.recent.length ? rows(r.recent.map(p => ({
          icon: /transfer|e-?transfer|bank/i.test(p.method) ? 'swap_horiz' : 'credit_card',
          title: money(p.amount), meta: [p.method, p.when].filter(Boolean).join(' · '),
          side: pill('Received', 'ok') }))) : nothing('payments', 'Payments you receive will appear here.'),
          { what: 'your payments' }) })}
      ${block({ title: 'Payment links', icon: 'add_link',
        body: notConnected('Payment links', 'Pay-by-link needs a payment processor (Stripe or similar). Once one is connected, your links and how often they are used appear here.') })}
    `, 'sidebar')}`;
  },

  async invoices({ api }) {
    const res = await api.get('payments.invoices');
    const inv = res.ok ? res.data.invoices : null;
    return `
    ${pageHead({ eyebrow: 'Payments', title: 'Invoices',
      sub: 'Every invoice you have sent, what is paid, and what is still out.' })}
    ${inv && inv.length ? filterbar([
      { label: 'All', count: inv.length },
      { label: 'Awaiting', count: inv.filter(i => i.status === 'sent' || i.status === 'partial').length },
      { label: 'Overdue', count: inv.filter(i => i.status === 'overdue').length },
      { label: 'Paid', count: inv.filter(i => i.status === 'paid').length }
    ]) : ''}
    ${block({ flush: true, body: liveBody(res, d => table({
      cols: [
        { label: 'Invoice', key: 'number', strong: true },
        { label: 'Customer', key: 'to' },
        { label: 'Notes', key: 'for', mute: true },
        { label: 'Amount', key: 'amount', num: true, html: true, get: r => money(r.amount) },
        { label: 'Status', html: true, get: r => statusPill(r.status) },
        { label: 'Due / paid', key: 'due', mute: true }
      ], rows: d.invoices,
      empty: { icon: 'receipt_long', title: 'Nothing here yet', note: 'Invoices you or your agent create will appear here.' }
    }), { what: 'your invoices' }) })}`;
  },

  async links() {
    return `
    ${pageHead({ eyebrow: 'Payments', title: 'Payment links',
      sub: 'A link that charges a fixed amount — for a text, an email, a post or your website.' })}
    ${block({ body: notConnected('Payment links', 'Pay-by-link needs a payment processor (Stripe or similar). Once one is connected for your business, your links appear here.') })}`;
  },

  async payouts() {
    return `
    ${pageHead({ eyebrow: 'Payments', title: 'Payouts',
      sub: 'Deposits from card payments to your bank.' })}
    ${block({ body: notConnected('Payouts', 'Payouts come from a payment processor. Once one is connected for your business, every deposit to your bank appears here.') })}`;
  }
};

/* ============================== TEAM ============================== */
export const team = {
  async overview({ api }) {
    const [res, hub] = await Promise.all([api.get('team.roster'), api.get('hub.summary')]);
    const sec = hub.ok ? hub.data.sections.team : null;
    return `
    ${pageHead({ eyebrow: 'Team', title: 'Your team',
      sub: sec && sec.state === 'live' ? sec.reason + '.' : 'Everyone on your crew, in one place.' })}
    ${block({ title: 'Everyone', icon: 'diversity_3', flush: true, body: liveBody(res, d => d.roster.length
      ? d.roster.map(p => `<div class="row">
          <div class="tavatar tavatar--row">${esc(p.initials)}</div>
          <div class="row__main">
            <div class="row__title">${esc(p.name)}</div>
            <div class="row__meta">${esc([p.role, p.phone].filter(x => x && x !== '—').join(' · ') || '—')}</div>
          </div>
          <div class="row__side">${pill(p.status, p.status === 'Active' ? 'ok' : 'neutral')}</div>
        </div>`).join('')
      : nothing('team members', 'People you add to your team will appear here.'), { what: 'your team' }) })}
    ${grid(`
      ${block({ title: 'Team chat', icon: 'forum',
        body: notConnected('Team chat', 'Messaging your crew from here is not connected yet. When it is, every job conversation lands here.') })}
      ${block({ title: 'Shared files', icon: 'folder_shared',
        body: notConnected('Shared files', 'Photos and documents your crew shares will collect here once team chat is connected.') })}
    `)}`;
  },

  async chat() {
    return `
    ${pageHead({ eyebrow: 'Team', title: 'Chat', sub: 'Back and forth with your crew.' })}
    ${block({ body: notConnected('Team chat', 'Messaging your crew from here is not connected yet. When it is, every job conversation lands here and reaches their phones.') })}`;
  },

  async files() {
    return `
    ${pageHead({ eyebrow: 'Team', title: 'Shared files', sub: 'Every photo and document the crew has shared.' })}
    ${block({ body: notConnected('Shared files', 'Shared files arrive with team chat. Once it is connected, job photos, sign-offs and receipts collect here.') })}`;
  }
};

/* ============================== ACCOUNT ==============================
   The fixture Account pages are written inline for the demo business. Live: "Your business"
   reads the business's own company record (read-only); pages with no system behind them yet say so. */
const kv = (label, value) => `<div class="row">
  <div class="row__main"><div class="row__meta">${esc(label)}</div>
  <div class="row__title" style="word-break:break-word">${value ? esc(value) : '<span class="muted">Not set</span>'}</div></div></div>`;

export const account = {
  async business({ api }) {
    const res = await api.get('business.profile');
    return `
    ${pageHead({ eyebrow: 'Account', title: 'Your business',
      sub: 'What your system knows about your business. Ask your agent to change anything here.' })}
    ${block({ title: 'The basics', icon: 'storefront', flush: true, body: liveBody(res, d => d.name === null && !d.phone && !d.email
      ? empty({ icon: 'storefront', title: 'Nothing here yet', note: 'Your business details have not been filled in yet. Your agent can do it from a quick chat.' })
      : [kv('Business name', d.name), kv('Phone', d.phone), kv('Email', d.email), kv('Address', d.address),
         kv('Website', d.website), kv('Google Business Profile', d.gbp_url), kv('Licence number', d.license_no)].join(''),
      { what: 'your business details' }) })}
    ${res.ok ? grid(`
      ${block({ title: 'What you sell', icon: 'sell', body: res.data.services.length
        ? res.data.services.map(t => pill(t, 'info')).join(' ') : empty({ icon: 'sell', title: 'Nothing here yet', note: 'No services listed yet.' }) })}
      ${block({ title: 'Where you work', icon: 'map', body: res.data.service_area.length
        ? res.data.service_area.map(t => pill(t, 'ok')).join(' ') : empty({ icon: 'map', title: 'Nothing here yet', note: 'No service area listed yet.' }) })}
    `) : ''}`;
  },
  async platforms() {
    return `${pageHead({ eyebrow: 'Account', title: 'Connected apps', sub: 'The accounts your system works with on your behalf.' })}
    ${block({ body: notConnected('This list', 'Which of your accounts are connected is not shown on this page yet. Your agent can tell you exactly what is connected right now.') })}`;
  },
  async autonomy() {
    return `${pageHead({ eyebrow: 'Account', title: 'How much we do', sub: 'What your system does on its own, and what waits for you.' })}
    ${note('Whatever the setting, three things always wait for you: spending money, signing anything, and messaging a customer directly.')}
    ${block({ body: notConnected('Your setting', 'Your current setting is not shown on this page yet. Ask your agent to read it or change it.') })}`;
  },
  async team() {
    return `${pageHead({ eyebrow: 'Account', title: 'Logins', sub: 'Who can sign in to your system.' })}
    ${block({ body: notConnected('The login list', 'Who can sign in is not shown on this page yet. Your crew is listed under Team.') })}`;
  }
};

/* ============================== ASK ============================== */
export const ask = {
  async chat() {
    return `${pageHead({ eyebrow: 'Ask', title: 'Ask your agent',
      sub: 'Your agent lives on your JamBot home screen. Talk or type to it there.',
      actions: [{ label: 'Open my agent', kind: 'primary', icon: 'auto_awesome', href: '/' }] })}
    ${block({ title: 'Things people ask', icon: 'lightbulb', flush: true, body: rows([
      { icon: 'lightbulb', title: 'What should I post about this week?' },
      { icon: 'call', title: 'Who should I follow up with today?' },
      { icon: 'receipt_long', title: 'Make an invoice for my last job' },
      { icon: 'photo_camera', title: 'Turn these job photos into a post' }
    ]) })}`;
  },
  async requests() {
    return `${pageHead({ eyebrow: 'Ask', title: 'Requests', sub: 'Work you have asked for, and where it is.' })}
    ${block({ body: notConnected('Your requests', 'Requests are not listed on this page yet. Ask your agent what is in progress.') })}`;
  },
  async history() {
    return `${pageHead({ eyebrow: 'Ask', title: 'History', sub: 'Every conversation with your system.' })}
    ${block({ body: notConnected('Conversation history', 'Your past conversations are not listed on this page yet. Your agent keeps them; ask it anything you said before.') })}`;
  }
};

/* ============================== INVENTORY ============================== */
export const inventory = {
  async overview({ api }) {
    const [hub, res] = await Promise.all([api.get('hub.summary'), api.get('inventory.products')]);
    const sec = hub.ok ? hub.data.sections.inventory : null;
    const c = sec?.counts || {};
    return `
    ${pageHead({ eyebrow: 'Inventory', title: 'What you sell and have on hand',
      sub: 'Your products and the stock moving in and out.' })}
    ${hub.ok ? tiles([
      sectionTile(api, sec, { label: 'Active products', icon: 'inventory_2', value: c.products_active,
        link: { href: '#/inventory/products', label: 'Products' } }),
      sectionTile(api, sec, { label: 'Stock moves in 30 days', icon: 'swap_vert', value: c.stock_moves_30d }),
      api.unknownMetric('Low stock', { note: 'Low-stock levels are not worked out yet' }, { icon: 'warning' })
    ]) : block({ body: liveBody(hub, () => '', { what: 'your inventory numbers' }) })}
    ${block({ title: 'Products', icon: 'inventory_2', flush: true, action: { label: 'All products', href: '#/inventory/products' },
      body: liveBody(res, d => d.products.length ? rows(d.products.slice(0, 6).map(p => ({
        icon: 'inventory_2', title: p.name, meta: [p.category, p.unit].filter(x => x !== '—').join(' · '),
        side: pill(p.active ? 'Active' : 'Not active', p.active ? 'ok' : 'neutral') })))
        : nothing('products', 'Products you or your agent add will appear here.'), { what: 'your products' }) })}`;
  },
  async products({ api }) {
    const res = await api.get('inventory.products');
    return `
    ${pageHead({ eyebrow: 'Inventory', title: 'Products', sub: 'Your full catalog — what it costs you and where it comes from.' })}
    ${block({ flush: true, body: liveBody(res, d => table({
      cols: [
        { label: 'Product', key: 'name', strong: true },
        { label: 'Category', key: 'category' },
        { label: 'Unit', key: 'unit', mute: true },
        { label: 'Supplier', key: 'supplier', mute: true },
        { label: 'Unit cost', num: true, get: r => (r.unit_cost === null ? '—' : money(r.unit_cost)) },
        { label: 'Status', html: true, get: r => pill(r.active ? 'Active' : 'Not active', r.active ? 'ok' : 'neutral') }
      ], rows: d.products,
      empty: { icon: 'inventory_2', title: 'Nothing here yet', note: 'Products you or your agent add will appear here.' }
    }), { what: 'your products' }) })}`;
  }
};

/* ============================== WEBSITE / CONTENT (inline-demo pages) ==============================
   These fixture pages carry the demo business's forms, topic map, blog pace and an illustrative
   calendar written into the page. Live: say plainly that they are not shown here yet. */
export const website = {
  async forms() {
    return `${pageHead({ eyebrow: 'Website', title: 'Forms & enquiries', sub: 'Every contact form on your site, and whether enquiries reach you.' })}
    ${block({ body: notConnected('Form checks', 'Your forms and their last test result are not shown on this page yet. New enquiries still appear under Customers › Leads.') })}`;
  },
  async map() {
    return `${pageHead({ eyebrow: 'Website', title: 'Topic map', sub: 'The questions your customers ask, and which ones your site answers.' })}
    ${block({ body: notConnected('Your topic map', 'Your topic map is not shown on this page yet. Your SEO reports under Get Found cover what has been researched.') })}`;
  },
  async blog() {
    return `${pageHead({ eyebrow: 'Website', title: 'Blog engine', sub: 'Articles that answer what your customers search for.' })}
    ${block({ body: notConnected('Your publishing schedule', 'Your article pace and queue are not shown on this page yet. Ask your agent what is planned next.') })}`;
  }
};
export const content = {
  async calendar() {
    return `${pageHead({ eyebrow: 'Content', title: 'Content calendar', sub: 'Everything planned across your channels.' })}
    ${block({ body: notConnected('The content calendar', 'No scheduling system holds your planned posts yet, so this calendar stays empty rather than showing made-up entries.') })}`;
  }
};
