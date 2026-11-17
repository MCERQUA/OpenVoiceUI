/*
 * shell.js — the shared company-system menu, contract v1.
 *
 * One menu for every page a business registers in its company system
 * (POST /api/biz/v1/registry/pages). OpenVoiceUI injects this file at serve time into
 * registered pages, so page files are never edited; a page may also include it itself:
 *
 *   <script src="/jb/v1/jb.js"></script>
 *   <script src="/jb/v1/shell.js" data-variant="office"></script>
 *
 * Variants: office (left rail + top bar on desktop; bottom tab bar + "More" drawer on
 * phones) and field (slim top bar with back + menu, for crews on phones).
 * The look follows the business's canvas style and company colours (theme tokens from
 * the server); structure is the same for every business, so menus never drift.
 * No browser storage: the menu is fetched from the server on every page load.
 * The stylesheet (/jb/v1/shell.css) is fetched and added as a <style> element, because the
 * canvas page CSP allows inline styles but not stylesheet links.
 */
(function (global) {
  'use strict';
  if (global.jbShell && global.jbShell.contract === 'v1') return;

  var SECTION_ORDER = ['home', 'customers', 'sales', 'jobs', 'money', 'stock', 'team', 'files', 'reports'];

  /** Pure: what to draw for this page. payload = GET /api/biz/v1/registry/shell. */
  function buildModel(payload, pageId) {
    payload = payload || {};
    var sections = (payload.sections || []).slice().sort(function (a, b) {
      return SECTION_ORDER.indexOf(a.id) - SECTION_ORDER.indexOf(b.id);
    });
    var byId = {}, current = null, currentSection = null;
    sections.forEach(function (s) {
      (s.pages || []).forEach(function (p) {
        byId[p.id] = p;
        if (p.id === pageId) { current = p; currentSection = s; }
      });
    });
    var tabs = (payload.tabs || []).map(function (id) { return byId[id]; }).filter(Boolean).slice(0, 5);
    var home = sections.length && sections[0].id === 'home' ? sections[0].pages[0] : null;
    return {
      sections: sections,
      tabs: tabs,
      more: !!payload.more,
      current: current,
      section: currentSection,
      title: current ? current.label : null,
      eyebrow: currentSection ? currentSection.label : null,
      verb: current && current.verb ? { label: current.verb, file: current.verb_href || current.file } : null,
      home: home,
      company: payload.company || {},
      theme: payload.theme || {},
      icons: payload.icons || {},
      base: typeof payload.base === 'string' ? payload.base : '/pages/',
      count: payload.count || 0
    };
  }

  var api = { contract: 'v1', buildModel: buildModel };
  global.jbShell = api;
  if (!global.document) return;          // loaded for tests: model only

  var doc = global.document;
  var script = doc.currentScript || doc.getElementById('jb-shell-js');
  var data = (script && script.dataset) || {};
  var pageId = data.page || (function () {
    var m = /([^\/]+)\.html$/.exec(global.location.pathname);
    return m ? decodeURIComponent(m[1]) : null;
  })();
  var variant = data.variant === 'field' ? 'field' : 'office';
  var sandbox = data.sandbox || null;
  var inFrame = false;
  try { inFrame = global.parent && global.parent !== global; } catch (e) { inFrame = true; }

  function el(tag, cls, text) {
    var n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }

  function icon(model, name) {
    var body = model.icons[name];
    var span = el('span', 'jb-ico');
    span.setAttribute('aria-hidden', 'true');
    if (body) {
      // bodies come from the server's fixed icon set (never from page or user data)
      span.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>';
    }
    return span;
  }

  function applyTheme(model, root) {
    var map = { 'bg': '--jb-bg', 'surface': '--jb-surface', 'surface-2': '--jb-surface-2', 'border': '--jb-border',
      'text': '--jb-text', 'text-secondary': '--jb-text-2', 'text-muted': '--jb-muted', 'heading': '--jb-heading',
      'brand': '--jb-brand', 'brand-contrast': '--jb-on-brand', 'accent': '--jb-accent', 'radius': '--jb-radius',
      'font-body': '--jb-font', 'font-display': '--jb-font-display' };
    Object.keys(map).forEach(function (k) {
      var v = model.theme[k];
      if (typeof v === 'string' && v) root.style.setProperty(map[k], v);
    });
  }

  function link(model, page, cls) {
    var a = el('a', cls);
    a.href = model.base + page.file;
    a.dataset.page = page.id;
    if (model.current && page.id === model.current.id) a.setAttribute('aria-current', 'page');
    // Inside the OpenVoiceUI canvas frame, ask the app to open the page (keeps its history and auth);
    // a sandbox page is not in the app's page list, so it navigates directly.
    if (inFrame && !sandbox && typeof global.nav === 'function') {
      a.addEventListener('click', function (ev) {
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button) return;
        ev.preventDefault();
        global.nav(page.id);
      });
    }
    return a;
  }

  function brandBlock(model) {
    var b = el('div', 'jb-brand');
    var c = model.company || {};
    if (c.logo) {
      var img = el('img', 'jb-logo');
      img.src = c.logo;
      img.alt = '';
      b.appendChild(img);
    } else {
      b.appendChild(el('span', 'jb-mark', (c.name || 'Company').trim().charAt(0).toUpperCase()));
    }
    b.appendChild(el('span', 'jb-company', c.name || 'Company system'));
    return b;
  }

  function groupedMenu(model) {
    var nav = el('nav', 'jb-menu');
    nav.setAttribute('aria-label', 'Company system');
    model.sections.forEach(function (s) {
      var g = el('div', 'jb-group');
      var h = el('div', 'jb-group-label');
      h.appendChild(icon(model, s.icon));
      h.appendChild(el('span', null, s.label));
      g.appendChild(h);
      s.pages.forEach(function (p) {
        var a = link(model, p, 'jb-item');
        a.appendChild(icon(model, p.icon));
        a.appendChild(el('span', 'jb-item-label', p.label));
        g.appendChild(a);
      });
      nav.appendChild(g);
    });
    return nav;
  }

  function drawer(model, opener) {
    var wrap = el('div', 'jb-drawer');
    wrap.hidden = true;
    var scrim = el('div', 'jb-scrim');
    var panel = el('div', 'jb-drawer-panel');
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    panel.setAttribute('aria-label', 'Menu');
    var head = el('div', 'jb-drawer-head');
    head.appendChild(brandBlock(model));
    var close = el('button', 'jb-btn jb-icon-btn');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close menu');
    close.appendChild(icon(model, 'close'));
    head.appendChild(close);
    panel.appendChild(head);
    panel.appendChild(groupedMenu(model));
    wrap.appendChild(scrim);
    wrap.appendChild(panel);
    function hide() { wrap.hidden = true; doc.documentElement.classList.remove('jb-drawer-open'); if (opener) opener.focus(); }
    function show() {
      wrap.hidden = false;
      doc.documentElement.classList.add('jb-drawer-open');
      var first = panel.querySelector('[aria-current="page"]') || panel.querySelector('a');
      if (first) first.focus();
    }
    scrim.addEventListener('click', hide);
    close.addEventListener('click', hide);
    doc.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !wrap.hidden) hide(); });
    return { node: wrap, show: show, hide: hide };
  }

  function verbButton(model) {
    if (!model.verb) return null;
    var a = el('a', 'jb-btn jb-verb');
    a.href = model.base + model.verb.file;
    a.appendChild(icon(model, 'plus'));
    a.appendChild(el('span', null, model.verb.label));
    return a;
  }

  function renderOffice(model, root) {
    var rail = el('aside', 'jb-rail');
    rail.appendChild(brandBlock(model));
    rail.appendChild(groupedMenu(model));
    root.appendChild(rail);

    var top = el('header', 'jb-top');
    var titles = el('div', 'jb-titles');
    if (model.eyebrow) titles.appendChild(el('div', 'jb-eyebrow', model.eyebrow));
    titles.appendChild(el('div', 'jb-title', model.title || (model.company.name || 'Company system')));
    top.appendChild(titles);
    var verb = verbButton(model);
    if (verb) top.appendChild(verb);
    root.appendChild(top);

    var bar = el('nav', 'jb-tabbar');
    bar.setAttribute('aria-label', 'Main');
    model.tabs.forEach(function (p) {
      var a = link(model, p, 'jb-tab');
      a.appendChild(icon(model, p.icon));
      a.appendChild(el('span', 'jb-tab-label', p.label));
      bar.appendChild(a);
    });
    var d = null;
    if (model.more) {
      var more = el('button', 'jb-tab jb-tab-more');
      more.type = 'button';
      more.setAttribute('aria-label', 'More pages');
      more.appendChild(icon(model, 'more'));
      more.appendChild(el('span', 'jb-tab-label', 'More'));
      d = drawer(model, more);
      more.addEventListener('click', d.show);
      bar.appendChild(more);
    }
    root.appendChild(bar);
    if (d) root.appendChild(d.node);
  }

  function renderField(model, root) {
    var top = el('header', 'jb-field-bar');
    var back = el('button', 'jb-btn jb-icon-btn');
    back.type = 'button';
    back.setAttribute('aria-label', 'Back');
    back.appendChild(icon(model, 'back'));
    back.addEventListener('click', function () {
      if (global.history.length > 1) global.history.back();
      else if (model.home) global.location.href = model.base + model.home.file;
    });
    top.appendChild(back);
    top.appendChild(el('div', 'jb-title', model.title || 'Company system'));
    var menuBtn = el('button', 'jb-btn jb-icon-btn');
    menuBtn.type = 'button';
    menuBtn.setAttribute('aria-label', 'Menu');
    menuBtn.appendChild(icon(model, 'menu'));
    var d = drawer(model, menuBtn);
    menuBtn.addEventListener('click', d.show);
    top.appendChild(menuBtn);
    root.appendChild(top);
    root.appendChild(d.node);
  }

  function hideLegacyNav() {
    if (!data.legacyNav) return;
    try {
      Array.prototype.forEach.call(doc.querySelectorAll(data.legacyNav), function (n) {
        n.setAttribute('data-jb-hidden-nav', '');
      });
    } catch (e) { if (global.console) console.warn('jb shell: legacy_nav is not a valid selector'); }
  }

  function render(payload) {
    var model = buildModel(payload, pageId);
    api.model = model;
    if (!model.sections.length) return;      // nothing registered yet: leave the page as it is
    var old = doc.getElementById('jb-shell');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var root = el('div', 'jb-shell jb-' + variant);
    root.id = 'jb-shell';
    applyTheme(model, doc.documentElement);
    if (variant === 'field') renderField(model, root); else renderOffice(model, root);
    doc.body.appendChild(root);
    doc.documentElement.classList.add('jb-shell-on', 'jb-shell-' + variant);
    if (model.tabs.length || model.more) doc.documentElement.classList.add('jb-has-tabbar');
    hideLegacyNav();
  }

  function ensureCss() {
    if (doc.getElementById('jb-shell-style')) return Promise.resolve();
    return fetch('/jb/v1/shell.css', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error('shell.css ' + r.status);
      return r.text();
    }).then(function (css) {
      if (doc.getElementById('jb-shell-style')) return;
      var st = el('style');
      st.id = 'jb-shell-style';
      st.textContent = css;
      (doc.head || doc.documentElement).appendChild(st);
    });
  }

  function load() {
    if (!global.jb || typeof global.jb.shell !== 'function') {
      if (global.console) console.warn('jb shell: /jb/v1/jb.js is not loaded; the menu needs it');
      return;
    }
    Promise.all([global.jb.shell(pageId), ensureCss()]).then(function (r) { render(r[0]); }).catch(function (e) {
      if (global.console) console.warn('jb shell: menu unavailable', e && (e.code || e.message));
    });
  }

  api.refresh = load;
  api.page = pageId;
  api.variant = variant;
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', load); else load();
})(typeof window !== 'undefined' ? window : this);
