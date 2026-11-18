/* ==========================================================================
   app.js — boot, shell rendering, hash router.
   The rail, drawer and tab bar are all GENERATED from nav.js. There is exactly
   one menu definition and one mount point.
   ========================================================================== */

import { NAV, TABS, resolve } from './nav.js';
import * as api from './api.js';
import { icon, esc } from './ui.js';
import { VIEWS } from './views/index.js';

const $ = s => document.querySelector(s);
const railNav = $('#railNav'), viewEl = $('#view'), tabbar = $('#tabbar');
let badges = {};

/* ---------- rail ---------- */
function renderRail(activeSection, activePath) {
  railNav.innerHTML = NAV.map(s => {
    const isActive = s.id === activeSection.id;
    const multi = s.pages.length > 1;
    const count = s.badge ? badges[s.badge] : null;
    const head = `<a class="navlink${isActive && !multi ? ' is-active' : ''}${isActive && multi ? ' is-open' : ''}"
        href="#/${s.id}" data-section="${s.id}">
        ${icon(s.icon)}<span>${esc(s.label)}</span>
        ${count ? `<span class="navlink__badge">${esc(count)}</span>`
                : multi ? `<span class="material-symbols-outlined navlink__chev">expand_more</span>` : ''}
      </a>`;
    const sub = multi ? `<div class="subnav${isActive ? ' is-open' : ''}">
        ${s.pages.map(p => {
          const path = p.id ? `${s.id}/${p.id}` : s.id;
          return `<a class="sublink${path === activePath ? ' is-active' : ''}" href="#/${path}">
            <span class="sublink__dot"></span>${esc(p.label)}</a>`;
        }).join('')}
      </div>` : '';
    return `<div class="navgroup">${head}${sub}</div>`;
  }).join('');
}

function renderTabs(activeSection) {
  tabbar.innerHTML = [...TABS, { id: 'account', label: 'More', icon: 'more_horiz' }]
    .map(s => `<a href="#/${s.id}" class="${s.id === activeSection.id ? 'is-active' : ''}">
      ${icon(s.icon)}<span>${esc(s.label)}</span></a>`).join('');
}

/* ---------- drawer ---------- */
const rail = $('#rail'), scrim = $('#scrim');
const closeDrawer = () => { rail.classList.remove('is-open'); scrim.classList.remove('is-open'); };
$('#menuBtn').addEventListener('click', () => {
  rail.classList.toggle('is-open'); scrim.classList.toggle('is-open');
});
scrim.addEventListener('click', closeDrawer);

/* ---------- router ---------- */
async function route() {
  const r = resolve(location.hash);
  renderRail(r.section, r.path);
  renderTabs(r.section);
  closeDrawer();

  const render = VIEWS[r.view];
  if (!render) {
    viewEl.innerHTML = `<div class="card"><div class="empty">
      ${icon('construction')}<div class="empty__title">This screen isn't drawn yet</div>
      <div class="empty__note">${esc(r.page.label)} is in the menu so the structure is visible,
      but its layout hasn't been built.</div></div></div>`;
    return;
  }

  // Loading state is a first-class state — never a blank frame.
  viewEl.innerHTML = `<div class="card"><div class="empty">${icon('hourglass_top')}
    <div class="empty__title">Loading ${esc(r.page.label)}…</div></div></div>`;

  try {
    viewEl.innerHTML = await render({ api, section: r.section, page: r.page });
  } catch (err) {
    // A view that throws shows an honest failure, not a white screen.
    viewEl.innerHTML = `<div class="card"><div class="empty">${icon('error')}
      <div class="empty__title">This screen couldn't load</div>
      <div class="empty__note">${esc(err.message || err)}</div></div></div>`;
    console.error('[jamdash] view failed:', r.view, err);
  }
  document.querySelector('.main').scrollTo?.(0, 0);
  window.scrollTo(0, 0);
}

/* demo-only affordance: the approve buttons acknowledge, so the flow reads as
   real without pretending to write anywhere. */
document.addEventListener('click', e => {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  e.preventDefault();
  if (b.dataset.act === 'add-appt') {
    // small icon-only button — swap the icon, no label text fits in it
    b.innerHTML = icon('check');
    b.title = 'Added — demo only';
    b.disabled = true;
    return;
  }
  const label = b.dataset.act === 'approve' ? 'Approved'
              : b.dataset.act === 'changes' ? 'Changes requested' : 'Opened';
  b.innerHTML = `${icon('check')}${label} — demo only`;
  b.classList.add('btn--ghost');
  b.disabled = true;
});

window.addEventListener('hashchange', route);

(async function boot() {
  badges = api.badges();
  $('#railTenant').textContent = api.MODE === 'live' ? `${api.tenant()} workspace` : 'demo workspace';
  if (!location.hash) location.hash = '#/home';
  await route();
})();
