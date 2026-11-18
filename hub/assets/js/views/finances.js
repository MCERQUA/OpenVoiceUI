/* FINANCES — revenue & P&L, invoices, expenses, vendors, tax & compliance.
   Nothing here has a backing system yet (no accounting/POS integration, no
   client-side invoicing). Drawn as the target design, labelled honestly,
   same treatment Content > Calendar gets for the same reason. Distinct from
   Account > Billing, which is JamBot's own subscription billing OF the
   client, not the client's own books. */

import { pageHead, tiles, block, notConnected } from '../ui.js';

export async function overview({ api }) {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Your money, in one place',
    sub: 'Revenue, expenses, profit and cash position — once connected to your books.' })}
  ${tiles([
    api.unknownMetric('Revenue this month', null, { icon: 'payments' }),
    api.unknownMetric('Expenses this month', null, { icon: 'receipt_long' }),
    api.unknownMetric('Net profit', null, { icon: 'trending_up' }),
    api.unknownMetric('Cash position', null, { icon: 'account_balance_wallet' })
  ])}
  ${block({ title: 'Recent transactions', icon: 'receipt', body:
    notConnected('Transactions', 'Connect your accounting software or bank feed and your recent activity will appear here.') })}`;
}

export async function pnl() {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Revenue & P&L',
    sub: 'What came in, what went out, and what you kept — month by month.' })}
  ${block({ body: notConnected('Profit & loss',
    'Once your accounting or POS system is connected, your revenue, cost of goods and profit will appear here broken down by month.') })}`;
}

export async function invoices() {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Invoices',
    sub: 'Send invoices, track who has paid, and chase who hasn’t.',
    actions: [{ label: 'New invoice', kind: 'primary', icon: 'add' }] })}
  ${block({ body: notConnected('Invoicing',
    'This is your own invoicing of your customers — separate from your JamBot subscription, which lives under Account › Billing. Once switched on, you’ll be able to send, track and get paid right here.') })}`;
}

export async function expenses() {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Expenses',
    sub: 'Everything spent running the business, categorised automatically.' })}
  ${block({ body: notConnected('Expense tracking',
    'Connect a bank feed or receipt-scanning source and your expenses will be categorised and shown here automatically.') })}`;
}

export async function vendors() {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Vendors',
    sub: 'Everyone you buy from — contact details, terms, and what you owe.',
    actions: [{ label: 'Add a vendor', icon: 'add_business' }] })}
  ${block({ body: notConnected('Vendors & suppliers',
    'Once vendor tracking is switched on, your supplier list, payment terms and open bills will appear here.') })}`;
}

export async function tax() {
  return `
  ${pageHead({ eyebrow: 'Finances', title: 'Tax & compliance',
    sub: 'Sales tax collected, filing deadlines, and what’s due.' })}
  ${block({ body: notConnected('Tax & compliance',
    'Once connected to your books, sales tax collected and upcoming filing deadlines will be tracked here. This never files or pays anything on its own.') })}`;
}
