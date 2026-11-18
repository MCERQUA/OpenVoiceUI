/* SUPPORT — a real ticket system, distinct from Ask (which is the agent
   chat / requests feed). A ticket system already exists elsewhere in
   JamBot's infrastructure but has never been surfaced into JamDash. */

import { pageHead, tiles, block, notConnected } from '../ui.js';

export async function overview({ api }) {
  return `
  ${pageHead({ eyebrow: 'Support', title: 'Support',
    sub: 'Open tickets and how quickly they’re handled.' })}
  ${tiles([
    api.unknownMetric('Open tickets', null, { icon: 'confirmation_number' }),
    api.unknownMetric('Avg response time', null, { icon: 'schedule' }),
    api.unknownMetric('Satisfaction', null, { icon: 'sentiment_satisfied' })
  ])}
  ${block({ title: 'Recent tickets', icon: 'confirmation_number', body:
    notConnected('Tickets', 'A ticket system already exists in JamBot’s infrastructure. Once it’s wired into JamDash, your tickets will appear here.') })}`;
}

export async function tickets() {
  return `
  ${pageHead({ eyebrow: 'Support', title: 'Tickets',
    sub: 'Every support request, open and closed.',
    actions: [{ label: 'New ticket', kind: 'primary', icon: 'add' }] })}
  ${block({ body: notConnected('Support tickets',
    'A ticket system already exists in JamBot’s infrastructure. Once it’s wired into JamDash, every ticket will be listed here.') })}`;
}
