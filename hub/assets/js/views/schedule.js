/* SCHEDULE ("Calendar" in the nav) — operational appointments/bookings.
   Distinct from Content > Calendar, which is the marketing content calendar.
   No booking/appointments system exists anywhere yet, so — same treatment as
   Content > Calendar — this draws the real, empty month grid (zero
   fabricated events) rather than a notConnected() card, so the structure is
   visible without faking a single booking. */

import { pageHead, block, tiles, note, filterbar, calendar as calGrid, notConnected } from '../ui.js';

const MONTH_NAMES = ['January','February','March','April','May','June','July',
  'August','September','October','November','December'];

export async function overview({ api }) {
  const now = new Date();
  return `
  ${pageHead({ eyebrow: 'Calendar', title: 'Schedule',
    sub: 'Appointments and bookings for the business — not marketing content.',
    actions: [{ label: 'New appointment', kind: 'primary', icon: 'add' }] })}
  ${tiles([
    api.unknownMetric('Today', null, { icon: 'today' }),
    api.unknownMetric('This week', null, { icon: 'date_range' }),
    api.unknownMetric('No-shows this month', null, { icon: 'event_busy' })
  ])}
  ${filterbar([{ label: 'Month' }, { label: 'Week' }, { label: 'List' }])}
  ${note('No booking or appointments system is connected yet, so this calendar is genuinely empty rather than filled with placeholder bookings. The design is ready to be wired up.', 'warn')}
  ${block({ title: `${MONTH_NAMES[now.getMonth()]} ${now.getFullYear()}`, icon: 'calendar_month', body: calGrid({
    year: now.getFullYear(), month: now.getMonth(), today: now.getDate(), items: [], clickable: true
  }) })}`;
}

export async function appointments() {
  return `
  ${pageHead({ eyebrow: 'Calendar', title: 'Appointments',
    sub: 'Every booking, past and upcoming, in one list.' })}
  ${block({ body: notConnected('Appointments',
    'Once a booking or scheduling system is connected, every appointment will be listed here.') })}`;
}
