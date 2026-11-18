/* ==========================================================================
   ui.js — the component vocabulary. Views compose from these ONLY, so every
   screen is consistent by construction rather than by discipline.
   All helpers return HTML strings; the router injects them.
   ========================================================================== */

export const esc = s => String(s ?? '').replace(/[&<>"']/g,
  c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

export const icon = n => `<span class="material-symbols-outlined">${esc(n)}</span>`;

/* ---------- page header ---------- */
export function pageHead({ eyebrow, title, sub, actions = [] }) {
  return `<div class="page__head">
    <div class="page__titles">
      ${eyebrow ? `<div class="page__eyebrow">${icon('bolt')}${esc(eyebrow)}</div>` : ''}
      <h1 class="page__title">${esc(title)}</h1>
      ${sub ? `<p class="page__sub">${esc(sub)}</p>` : ''}
    </div>
    ${actions.length ? `<div class="page__actions">${actions.map(btn).join('')}</div>` : ''}
  </div>`;
}

export const btn = a => `<${a.href ? 'a' : 'button'}${a.href ? ` href="${esc(a.href)}"` : ''}
  class="btn btn--${a.kind || 'ghost'}${a.small ? ' btn--sm' : ''}${a.full ? ' btn--full' : ''}"
  ${a.act ? `data-act="${esc(a.act)}"` : ''}>${a.icon ? icon(a.icon) : ''}${esc(a.label)}</${a.href ? 'a' : 'button'}>`;

/* ---------- the four-verdict tile ----------
   An `unknown` tile is grey, dashed, shows an em-dash and states WHY.
   It must never render as 0 and never as green. */
export function tile(m) {
  const state = m.state || 'ok';
  const d = m.delta;
  const dir = !d ? null : d.dir || (d.value > 0 ? 'up' : d.value < 0 ? 'down' : 'flat');
  return `<div class="card tile lift is-${esc(state)}">
    <div class="tile__top">
      <div class="tile__icon">${icon(m.icon || 'insights')}</div>
      ${d ? `<span class="tile__delta tile__delta--${dir}">
        ${icon(dir === 'up' ? 'trending_up' : dir === 'down' ? 'trending_down' : 'trending_flat')}
        ${esc(d.label)}</span>` : ''}
    </div>
    <div class="tile__value">${esc(m.display ?? '—')}</div>
    <div class="tile__label">${esc(m.label)}</div>
    ${m.note || m.link ? `<div class="tile__foot">
      ${m.note ? `<span>${esc(m.note)}</span>` : ''}
      ${m.link ? `<a class="tile__link" href="${esc(m.link.href)}">${esc(m.link.label)}${icon('chevron_right')}</a>` : ''}
    </div>` : ''}
  </div>`;
}

export const tiles = list => `<div class="tiles">${list.map(tile).join('')}</div>`;

/* ---------- block (titled card) ---------- */
export function block({ title, icon: ic, action, body, flush }) {
  return `<div class="card block">
    ${title ? `<div class="block__head">
      ${ic ? `<span class="block__icon">${icon(ic)}</span>` : ''}
      <span class="block__title">${esc(title)}</span>
      ${action ? `<a class="block__action" href="${esc(action.href || '#')}">${esc(action.label)}${icon('chevron_right')}</a>` : ''}
    </div>` : ''}
    <div class="block__body${flush ? ' block__body--flush' : ''}">${body}</div>
  </div>`;
}

/* ---------- pills, chips ---------- */
export const pill = (label, kind = 'neutral') =>
  `<span class="pill pill--${esc(kind)}">${esc(label)}</span>`;

export const filterbar = (items, active = 0) =>
  `<div class="filterbar">${items.map((c, i) => `<button class="chip${i === active ? ' is-active' : ''}">
    ${c.icon ? icon(c.icon) : ''}${esc(c.label)}
    ${c.count != null ? `<span class="chip__count">${esc(c.count)}</span>` : ''}
  </button>`).join('')}</div>`;

/* ---------- rows ---------- */
export const row = r => `<div class="row">
  ${r.icon ? `<div class="row__icon">${icon(r.icon)}</div>` : ''}
  <div class="row__main">
    <div class="row__title">${esc(r.title)}</div>
    ${r.meta ? `<div class="row__meta">${esc(r.meta)}</div>` : ''}
  </div>
  ${r.side ? `<div class="row__side">${r.side}</div>` : ''}
</div>`;

export const rows = list => list.length ? list.map(row).join('')
  : empty({ icon: 'inbox', title: 'Nothing here yet' });

/* ---------- table ---------- */
export function table({ cols, rows: data, empty: emptyOpts }) {
  if (!data || !data.length) return empty(emptyOpts || { icon: 'table_rows', title: 'No rows yet' });
  return `<div class="tablewrap"><table>
    <thead><tr>${cols.map(c =>
      `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('')}</tr></thead>
    <tbody>${data.map(r => `<tr>${cols.map(c => {
      const v = typeof c.get === 'function' ? c.get(r) : r[c.key];
      return `<td class="${c.num ? 'num' : ''}${c.strong ? ' cell-strong' : ''}${c.mute ? ' cell-mute' : ''}">${
        c.html ? v : esc(v)}</td>`;
    }).join('')}</tr>`).join('')}</tbody>
  </table></div>`;
}

/* ---------- states ---------- */
export const empty = ({ icon: ic = 'inbox', title = 'Nothing here yet', note = '' } = {}) =>
  `<div class="empty">${icon(ic)}<div class="empty__title">${esc(title)}</div>
   ${note ? `<div class="empty__note">${esc(note)}</div>` : ''}</div>`;

/** The honest empty state for a section with no backing system. */
export const notConnected = (what, why) => empty({
  icon: 'power_off',
  title: `${what} isn't connected yet`,
  note: why || 'When the system behind this is wired up, it will appear here automatically.'
});

export const note = (text, kind = '') =>
  `<div class="note${kind ? ` note--${kind}` : ''}">${icon(kind === 'warn' ? 'warning' : 'lightbulb')}
   <span>${esc(text)}</span></div>`;

export const demobar = text =>
  `<div class="demobar">${icon('science')}<span>${esc(text)}</span></div>`;

/* ---------- progress / stepper ---------- */
export const progress = (pct, left = '', right = '') => `<div>
  <div class="progress"><div class="progress__fill" style="width:${Math.max(0, Math.min(100, pct))}%"></div></div>
  ${left || right ? `<div class="progress__meta"><span>${esc(left)}</span><span>${esc(right)}</span></div>` : ''}
</div>`;

export const stepper = (steps, current) => `<div class="stepper">${steps.map((s, i) => {
  const cls = i < current ? 'is-done' : i === current ? 'is-current' : '';
  return `<div class="step ${cls}">
    <div class="step__dot">${i < current ? '<span class="material-symbols-outlined" style="font-size:15px">check</span>' : i + 1}</div>
    <div class="step__label">${esc(s)}</div></div>`;
}).join('')}</div>`;

/* ---------- approval card ---------- */
export const approval = a => `<div class="approval">
  <div class="approval__thumb">${icon(a.icon || 'image')}</div>
  <div class="approval__body">
    <div class="approval__meta">${pill(a.status || 'Awaiting you', 'warn')}
      <span>${esc(a.channel || '')}</span>${a.when ? `<span>· ${esc(a.when)}</span>` : ''}</div>
    <h3 class="approval__title">${esc(a.title)}</h3>
    <p class="approval__excerpt">${esc(a.excerpt)}</p>
    <div class="approval__actions">
      ${btn({ label: 'Approve', kind: 'primary', icon: 'check', small: true, act: 'approve' })}
      ${btn({ label: 'Ask for changes', icon: 'edit_note', small: true, act: 'changes' })}
      ${btn({ label: 'See it', icon: 'visibility', small: true, act: 'view' })}
    </div>
  </div>
</div>`;

/* ---------- calendar ---------- */
const MONTH_NAMES = ['January','February','March','April','May','June','July',
  'August','September','October','November','December'];

/** clickable is a click-to-add affordance (data-act="add-appt"), not a link —
    the demo handler in app.js acknowledges it without wiping the cell. */
export function calendar({ year, month, today, items = [], clickable = false }) {
  const first = new Date(year, month, 1);
  const start = first.getDay();
  const days = new Date(year, month + 1, 0).getDate();
  const monthName = MONTH_NAMES[month] || '';
  const byDay = {};
  items.forEach(i => (byDay[i.day] ||= []).push(i));
  const cells = [];
  for (let i = 0; i < start; i++) cells.push('<div class="cal__day cal__day--out"></div>');
  for (let d = 1; d <= days; d++) {
    const its = byDay[d] || [];
    // days with nothing on them collapse on mobile, so the month reads as an agenda
    cells.push(`<div class="cal__day${d === today ? ' cal__day--today' : ''}${
        its.length ? '' : ' cal__day--empty'}">
      <div class="cal__num" data-month="${esc(monthName)}">${d}</div>
      ${its.map(i => `<button class="calitem calitem--${esc(i.kind || 'sched')}">
        ${icon(i.icon || 'circle')}<span>${esc(i.label)}</span></button>`).join('')}
      ${clickable ? `<button type="button" class="cal__add" data-act="add-appt"
        aria-label="Add an appointment on ${esc(monthName)} ${d}">${icon('add')}</button>` : ''}
    </div>`);
  }
  return `<div class="cal">
    ${['Sun','Mon','Tue','Wed','Thu','Fri','Sat'].map(d => `<div class="cal__dow">${d}</div>`).join('')}
    ${cells.join('')}
  </div>`;
}

/* ---------- media + swatches ---------- */
export const mediaCard = m => `<div class="card mediacard lift">
  <div class="mediacard__thumb">${icon(m.icon || 'movie')}
    ${m.dur ? `<span class="mediacard__dur">${esc(m.dur)}</span>` : ''}</div>
  <div class="mediacard__body">
    <div class="mediacard__name">${esc(m.name)}</div>
    <div class="mediacard__meta">${esc(m.meta || '')}</div>
  </div></div>`;

export const swatch = s => `<div class="swatch">
  <div class="swatch__chip" style="background:${esc(s.hex)}"></div>
  <div class="swatch__body"><div class="swatch__name">${esc(s.name)}</div>
  <div class="swatch__hex">${esc(s.hex)}</div></div></div>`;

export const grid = (html, kind = '2') => `<div class="grid grid--${kind}">${html}</div>`;
