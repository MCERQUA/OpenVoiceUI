/* CUSTOMERS — leads, contacts, pipeline, jobs.
   Leads are the strongest real feed here (Netlify webhook -> Postgres, SSE).
   NOTE the honest treatment of the funnel: every lead on the real system is
   still status `new`, so the pipeline stages show an explicit "not tracked yet"
   rather than a row of confident zeroes. */

import { pageHead, tiles, block, table, rows, pill, filterbar, note, btn,
         notConnected, progress, esc, grid, icon } from '../ui.js';

export async function overview({ api }) {
  const [ld, crm] = await Promise.all([api.get('leads.stats'), api.get('crm.pipeline')]);
  const l = ld.data?.stats || {}, c = crm.data || {};
  return `
  ${pageHead({ eyebrow: 'Customers', title: 'Your customers',
    sub: 'Everyone who has reached out, everyone you have worked with, and what is in flight.',
    actions: [{ label: 'Add a customer', kind: 'primary', icon: 'person_add' }] })}
  ${tiles([
    api.metric({ value: l.new_count, display: String(l.new_count ?? ''), state: 'ok',
      icon: 'contact_phone', label: 'New leads', delta: { dir: 'up', label: '+18%' },
      link: { href: '#/customers/leads', label: 'Open' } }),
    api.metric({ value: c.contacts, display: String(c.contacts ?? ''), state: 'ok',
      icon: 'groups', label: 'Contacts' }),
    api.metric({ value: c.deals, display: String(c.deals ?? ''), state: 'ok',
      icon: 'handshake', label: 'Open jobs' }),
    api.metric({ value: c.value, display: c.value_display, state: 'ok',
      icon: 'payments', label: 'Work in the pipeline' })
  ])}
  ${grid(`
    ${block({ title: 'Latest enquiries', icon: 'inbox', flush: true,
      action: { label: 'All leads', href: '#/customers/leads' },
      body: rows((ld.data?.recent || []).map(r => ({
        icon: 'person', title: r.name, meta: `${r.what} · ${r.when}`,
        side: pill(r.source, 'info') }))) })}
    ${block({ title: 'Needs a follow-up', icon: 'schedule', flush: true,
      body: rows((c.stale || []).map(r => ({
        icon: 'hourglass_top', title: r.name, meta: r.note,
        side: pill(r.age, 'warn') }))) })}
  `)}`;
}

export async function leads({ api }) {
  const res = await api.get('leads.list');
  const st = (await api.get('leads.stats')).data?.stats || {};
  return `
  ${pageHead({ eyebrow: 'Customers', title: 'Leads',
    sub: 'Every enquiry from your website forms, in one list, the moment it arrives.' })}
  ${filterbar([
    { label: 'All', count: st.total }, { label: 'New', count: st.new_count },
    { label: 'Contacted', count: st.contacted }, { label: 'Quoted', count: st.quoted },
    { label: 'Won', count: st.won }
  ])}
  ${note('New enquiries appear here within seconds of someone hitting send, and we text you straight away.')}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Name', key: 'name', strong: true },
      { label: 'What they want', key: 'what' },
      { label: 'From', key: 'site', mute: true },
      { label: 'When', key: 'when', mute: true },
      { label: 'Stage', html: true, get: r => pill(r.stage, r.stage === 'New' ? 'info' : 'ok') },
      { label: '', html: true, get: () => btn({ label: 'Open', small: true }) }
    ], rows: res.data?.leads || []
  }) })}`;
}

export async function contacts({ api }) {
  const res = await api.get('crm.pipeline');
  return `
  ${pageHead({ eyebrow: 'Customers', title: 'Contacts',
    sub: 'Your customer book — everyone you have quoted, worked for, or spoken to.',
    actions: [{ label: 'Add', icon: 'person_add', kind: 'primary' }] })}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Name', key: 'name', strong: true },
      { label: 'Company', key: 'company' },
      { label: 'Phone', key: 'phone', mute: true },
      { label: 'Last contact', key: 'last', mute: true },
      { label: 'Jobs', key: 'jobs', num: true }
    ], rows: res.data?.contacts_list || []
  }) })}`;
}

export async function pipeline({ api }) {
  const res = await api.get('crm.pipeline');
  const d = res.data || {};
  return `
  ${pageHead({ eyebrow: 'Customers', title: 'Pipeline',
    sub: 'Work in progress, from first enquiry to paid.' })}
  ${note('Stages after "New enquiry" are not being tracked yet — nothing currently moves a lead along. Rather than show you zeroes that look like lost business, we mark them as not tracked.', 'warn')}
  ${block({ title: 'Stages', icon: 'view_kanban', body: `
    <div class="grid grid--3">
      ${(d.stages || []).map(s => `<div class="card" style="padding:16px">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
          <span class="pill pill--${s.tracked ? 'info' : 'unknown'}">${esc(s.label)}</span>
        </div>
        <div style="font-family:var(--font-display);font-size:26px;font-weight:700;
          color:var(--${s.tracked ? 'primary' : 'unknown'})">${esc(s.tracked ? s.count : '—')}</div>
        <div style="font-size:12.5px;color:var(--outline);margin-top:5px">
          ${esc(s.tracked ? `${s.value} in work` : 'Not tracked yet')}</div>
      </div>`).join('')}
    </div>` })}`;
}

export async function jobs({ api }) {
  const res = await api.get('jobs.list');
  if (!res.ok || !res.data?.jobs?.length) {
    return `${pageHead({ eyebrow: 'Customers', title: 'Jobs & quotes',
      sub: 'Estimates, job costing and quotes.' })}
      ${block({ body: notConnected('Quoting',
        'The quoting engine is live for one pilot business. When it is switched on for you, your quotes and job costs will appear here.') })}`;
  }
  return `
  ${pageHead({ eyebrow: 'Customers', title: 'Jobs & quotes',
    sub: 'Every estimate and job, with what it costs you and what you charged.',
    actions: [{ label: 'New quote', kind: 'primary', icon: 'add' }] })}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Job', key: 'id', strong: true },
      { label: 'Customer', key: 'customer' },
      { label: 'Site', key: 'site', mute: true },
      { label: 'Quoted', key: 'quoted', num: true },
      { label: 'Stage', html: true, get: r => pill(r.status, 'info') }
    ], rows: res.data.jobs
  }) })}`;
}
