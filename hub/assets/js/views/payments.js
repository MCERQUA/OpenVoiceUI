/* PAYMENTS — the "get paid" surface: money in, invoices people can pay,
   payment links, payouts to the bank. The Stripe-shaped section (Danielle's
   ask 2026-09-30). Distinct from Finances, which is the accounting view —
   this is the money-moving side. Fixture data until a processor is wired. */

import { pageHead, block, rows, pill, btn, table, note, filterbar,
         esc, grid, icon, tiles, tile } from '../ui.js';

const money = n => `$${Number(n).toLocaleString('en-CA')}`;

const statusPill = s =>
  s === 'paid' ? pill('Paid', 'ok')
  : s === 'overdue' ? pill('Overdue', 'bad')
  : s === 'pending' || s === 'sent' ? pill('Awaiting payment', 'warn')
  : s === 'scheduled' ? pill('Scheduled', 'info')
  : pill(s, 'neutral');

export async function overview({ api }) {
  const res = await api.get('payments.summary');
  const d = res.ok ? res.data : { summary: {}, recent: [], links: [] };
  const s = d.summary || {};

  return `
  ${pageHead({ eyebrow: 'Payments', title: 'Getting paid',
    sub: 'Cards, invoices and payment links — every dollar coming in, and when it lands in your bank.',
    actions: [{ label: 'New payment link', kind: 'primary', icon: 'add_link' }] })}
  ${tiles([
    { state: 'ok', display: money(s.collectedThisMonth), label: 'Collected this month',
      icon: 'payments', note: 'Cards, links and e-transfers', link: null },
    { state: s.overdue ? 'warn' : 'ok', display: money(s.pending), label: 'Awaiting payment',
      icon: 'hourglass_top', note: s.overdue ? `${money(s.overdue)} of it overdue` : 'All current',
      link: { href: '#/payments/invoices', label: 'Invoices' } },
    { state: 'ok', display: money(s.nextPayout?.amount), label: 'Next payout',
      icon: 'account_balance', note: `${s.nextPayout?.when || '—'} · ${s.nextPayout?.bank || ''}` },
    { state: 'ok', display: `${s.avgDaysToPay} days`, label: 'Average time to pay',
      icon: 'schedule', note: `${s.sharePaidByCard}% pay by card` }
  ])}
  ${grid(`
    ${block({ title: 'Latest payments', icon: 'credit_card',
       action: { label: 'All invoices', href: '#/payments/invoices' }, flush: true, body: rows(
      (d.recent || []).map(r => ({
        icon: r.method.includes('transfer') ? 'swap_horiz' : 'credit_card',
        title: `${r.name} — ${money(r.amount)}`,
        meta: `${r.for} · ${r.method} · ${r.when}`,
        side: statusPill(r.status)
      }))
    ) })}
    ${block({ title: 'Your payment links', icon: 'add_link',
       action: { label: 'All links', href: '#/payments/links' }, body: `
      ${(d.links || []).filter(l => l.active).map(l => `
        <div class="tfile">
          ${icon('link')}
          <div style="min-width:0">
            <div class="row__title" style="font-size:14px">${esc(l.name)} — ${money(l.amount)}</div>
            <div class="row__meta">${esc(l.url)} · used ${l.uses} times</div>
          </div>
          ${btn({ label: 'Copy', icon: 'content_copy', small: true })}
        </div>`).join('')}
    ` })}
  `, 'sidebar')}
  ${note('Payments are a preview on sample data. When your payment processor (Stripe or similar) is connected, this becomes real money — links charge real cards and payouts match your bank.', 'warn')}`;
}

export async function invoices({ api }) {
  const res = await api.get('payments.invoices');
  const d = res.ok ? res.data : { invoices: [] };

  return `
  ${pageHead({ eyebrow: 'Payments', title: 'Invoices',
    sub: 'Send an invoice with a Pay Now button — customers pay by card and it lands in your payouts.',
    actions: [{ label: 'New invoice', kind: 'primary', icon: 'receipt_long' }] })}
  ${filterbar([
    { label: 'All', count: d.invoices.length },
    { label: 'Awaiting', count: d.invoices.filter(i => i.status === 'sent').length },
    { label: 'Overdue', count: d.invoices.filter(i => i.status === 'overdue').length },
    { label: 'Paid', count: d.invoices.filter(i => i.status === 'paid').length }
  ])}
  ${block({ flush: true, body: table({
    cols: [
      { label: 'Invoice', key: 'number', strong: true },
      { label: 'Customer', key: 'to' },
      { label: 'For', key: 'for', mute: true },
      { label: 'Amount', key: 'amount', num: true, html: true, get: r => money(r.amount) },
      { label: 'Status', html: true, get: r => statusPill(r.status) },
      { label: 'Due / paid', key: 'due', mute: true },
      { label: '', html: true, get: r => r.status === 'paid'
        ? `<span class="muted" style="font-size:13px">via ${esc(r.paid)}</span>`
        : btn({ label: 'Send reminder', icon: 'notifications', small: true }) }
    ],
    rows: d.invoices
  }) })}`;
}

export async function links({ api }) {
  const res = await api.get('payments.links');
  const d = res.ok ? res.data : { links: [] };

  return `
  ${pageHead({ eyebrow: 'Payments', title: 'Payment links',
    sub: 'A link that charges a fixed amount — put it in a text, an email, a post or your website. No invoice needed.',
    actions: [{ label: 'New link', kind: 'primary', icon: 'add_link' }] })}
  ${grid((d.links || []).map(l => `
    <div class="card block lift">
      <div class="block__head">
        ${l.active ? '' : icon('visibility_off')}
        <span class="block__title">${esc(l.name)}</span>
        <span style="margin-left:auto">${money(l.amount)}</span>
      </div>
      <div class="block__body">
        <div class="paylink">${icon('link')}<span>${esc(l.url)}</span></div>
        <div class="row__meta" style="margin-top:9px">Used ${l.uses} times · ${l.active ? 'active' : 'paused'}</div>
        <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
          ${btn({ label: 'Copy link', icon: 'content_copy', small: true })}
          ${btn({ label: l.active ? 'Pause' : 'Turn on', icon: l.active ? 'pause' : 'play_arrow', small: true })}
        </div>
      </div>
    </div>`).join(''), '3')}
  ${note('Every active link has a Pay Now page behind it — card, Apple Pay and Google Pay. Connect a processor to take real payments.', 'warn')}`;
}

export async function payouts({ api }) {
  const res = await api.get('payments.payouts');
  const d = res.ok ? res.data : { payouts: [], summary: {} };
  const next = d.summary?.nextPayout || {};

  return `
  ${pageHead({ eyebrow: 'Payments', title: 'Payouts',
    sub: 'Money moves itself — every payment batches into a weekly deposit to your bank.' })}
  ${block({ title: 'Next deposit', icon: 'account_balance', body: `
    <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
      <div class="tile__icon" style="width:44px;height:44px">${icon('savings')}</div>
      <div>
        <div class="tile__value" style="font-size:26px">${money(next.amount)}</div>
        <div class="row__meta">${esc(next.when || '—')} · ${esc(next.bank || '')}</div>
      </div>
      <span style="margin-left:auto">${statusPill('scheduled')}</span>
    </div>
  ` })}
  ${block({ title: 'Deposit history', icon: 'history', flush: true, body: table({
    cols: [
      { label: 'When', key: 'when', strong: true },
      { label: 'Items', key: 'items', num: true },
      { label: 'Amount', key: 'amount', num: true, html: true, get: r => money(r.amount) },
      { label: 'Bank', key: 'bank', mute: true },
      { label: 'Status', html: true, get: r => statusPill(r.status) }
    ],
    rows: d.payouts
  }) })}
  ${note('Payouts arrive automatically — no button to press. Fees show before they land when a processor is connected.', 'warn')}`;
}
