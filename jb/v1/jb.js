/*
 * jb.js — the JamBot client-backend SDK, contract v1.
 *
 *   <script src="/jb/v1/jb.js"></script>
 *
 * Served from the OpenVoiceUI image (routes/biz.py), never copied into a tenant's
 * canvas-pages folder, so one improvement reaches every page. A page that shows or
 * saves business data (customers, jobs, quotes, invoices, costs, materials, crew,
 * documents) reads and writes it ONLY through this SDK: never /api/canvas/data,
 * never _data/ JSON, never localStorage / sessionStorage / IndexedDB.
 *
 *   const jobs = await jb.list('job', { where: { status: 'scheduled' }, sort: '-scheduled_at' });
 *   const j    = await jb.create('job', { title: 'Attic re-blow', address: '...' });
 *   await jb.update('job', j.id, { crew: ['Sam'] }, j.version);   // stale version -> JbError code 'version_conflict'
 *   jb.on('job', ev => render());                                  // change feed (polling)
 *   const n    = await jb.nextNumber('job_number', { prefix: '26', pad: 3 });
 *
 * Company system (the shared menu): register a page once and it joins the menu on every page.
 *   await jb.pages.register({ file: 'quote-tool.html', label: 'Quotes', section: 'sales', kind: 'tool',
 *                             collections: ['estimate', 'contact'], verb: 'New quote' });
 *   const menu = await jb.shell();      // what /jb/v1/shell.js draws
 *
 * Full reference: /jb/v1/sdk.md
 * Auth: the page's own signed-in session. Inside OpenVoiceUI canvas pages the
 * injected window.authFetch adds the Clerk token; otherwise the __session cookie
 * goes with the same-origin request.
 */
(function (global) {
  'use strict';
  if (global.jb && global.jb.contract === 'v1') return;   // loaded twice: keep the first

  var BASE = '/api/biz/v1';
  // A page published into a dev sandbox (/pages/sandbox/<name>/...) reads that sandbox's
  // own database. The server accepts this only on the dev server; everywhere else the
  // path does not exist, so production pages never send it.
  var SANDBOX = (function () {
    try {
      var m = /^\/pages\/sandbox\/([a-z][a-z0-9-]{1,30})\//.exec(global.location.pathname);
      return m ? m[1] : null;
    } catch (e) { return null; }
  })();
  // Host rulings H4 + R1: a PLATFORM staff page is served from the image on the business's separate STAFF
  // ORIGIN (BIZ_STAFF_ORIGIN) and given a per-render token by the server. It goes with every call, so the
  // server knows a staff change came from the platform's own page. A canvas page never has one (and the
  // server honours it only on the staff origin), so where a pack needs it, its writes are refused.
  var SURFACE = (function () {
    var s = global.__JB_SURFACE__;
    try { delete global.__JB_SURFACE__; } catch (e) { global.__JB_SURFACE__ = undefined; }
    try {                                    // the boot script's text held the token: take it out of the DOM
      var boot = global.document && global.document.getElementById('jb-surface-boot');
      if (boot && boot.parentNode) boot.parentNode.removeChild(boot);
    } catch (e) { /* no DOM (tests) */ }
    return (s && typeof s.token === 'string') ? { token: s.token, sandbox: s.sandbox || null, base: s.base || null } : null;
  })();
  if (SURFACE && SURFACE.sandbox) SANDBOX = SURFACE.sandbox;

  function JbError(status, body) {
    body = body || {};
    this.name = 'JbError';
    this.status = status;
    this.code = body.code || 'http_' + status;
    this.message = body.error || ('Request failed (' + status + ')');
    this.body = body;
    this.current = body.current || null;   // set on version_conflict: the record as saved now
  }
  JbError.prototype = Object.create(Error.prototype);
  JbError.prototype.constructor = JbError;

  function doFetch(url, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    opts.cache = 'no-store';
    if (typeof global.authFetch === 'function') return global.authFetch(url, opts);
    return fetch(url, opts);
  }

  function call(method, path, body, headers) {
    var opts = { method: method, headers: Object.assign({ 'Accept': 'application/json' }, headers || {}) };
    if (SANDBOX) opts.headers['X-Biz-Sandbox'] = SANDBOX;
    if (SURFACE) opts.headers['X-JB-Staff-Surface'] = SURFACE.token;
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    return doFetch(BASE + path, opts).then(function (resp) {
      return resp.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
        if (!resp.ok) throw new JbError(resp.status, data);
        return data;
      });
    });
  }

  function enc(s) { return encodeURIComponent(String(s)); }

  function qs(params) {
    var parts = [];
    Object.keys(params).forEach(function (k) {
      var v = params[k];
      if (v === undefined || v === null || v === '' || v === false) return;
      parts.push(enc(k) + '=' + enc(v));
    });
    return parts.length ? '?' + parts.join('&') : '';
  }

  var jb = {
    contract: 'v1',
    JbError: JbError,
    sandbox: SANDBOX,
    /** On a platform staff page: the base URL of the staff pages (link to them, never to /pages/). Else null. */
    staffBase: SURFACE ? SURFACE.base : null,

    health: function () { return call('GET', '/health'); },
    schema: function (collection) { return call('GET', '/schema' + (collection ? '/' + enc(collection) : '')); },
    library: function (trade) { return call('GET', '/fields/library' + qs({ trade: trade })); },

    /** List records. opts: where (object), sort ('-field'), limit, cursor, include (['contact']), archived (bool) */
    list: function (collection, opts) {
      opts = opts || {};
      return call('GET', '/' + enc(collection) + qs({
        where: opts.where ? JSON.stringify(opts.where) : undefined,
        sort: opts.sort, limit: opts.limit, cursor: opts.cursor,
        include: opts.include ? [].concat(opts.include).join(',') : undefined,
        archived: opts.archived ? '1' : undefined,
        flagged: opts.flagged ? '1' : undefined
      }));
    },
    /**
     * Counted by the server over EVERY matching record (a page of jb.list undercounts).
     * opts: where, sum (['amount']), group_by ('status'), flagged (only "needs check"), archived
     *   -> { count, sum: {amount: 1234.5}, groups: [{key, count, sum}] }
     */
    aggregate: function (collection, opts) {
      opts = opts || {};
      return call('GET', '/' + enc(collection) + '/aggregate' + qs({
        where: opts.where ? JSON.stringify(opts.where) : undefined,
        sum: opts.sum ? [].concat(opts.sum).join(',') : undefined,
        group_by: opts.group_by, flagged: opts.flagged ? '1' : undefined, archived: opts.archived ? '1' : undefined
      }));
    },
    /** The number of matching records, counted by the server. */
    count: function (collection, opts) {
      return jb.aggregate(collection, { where: (opts || {}).where, flagged: (opts || {}).flagged })
        .then(function (r) { return r.count; });
    },
    /** A person confirms values an agent flagged "needs check" (record.source.flags). Owner sessions only. */
    confirm: function (collection, id, fields, note) {
      return call('POST', '/' + enc(collection) + '/' + enc(id) + '/confirm', { fields: [].concat(fields), note: note });
    },
    /**
     * Deep link to a record: '/pages/<file>?record=<collection>:<id>' on the registered page that opens it.
     * opts: { job_number: '26107' } instead of an id for jobs.
     */
    recordUrl: function (collection, id, opts) {
      return call('GET', '/registry/resolve' + qs({ collection: collection, id: id, job_number: (opts || {}).job_number }))
        .then(function (r) { return r.url; });
    },
    /** On a page opened from a deep link: { collection, id } from ?record=<collection>:<id>, else null. */
    currentRecord: function () {
      try {
        var v = new URLSearchParams(global.location.search).get('record') || '';
        var m = /^([a-z][a-z0-9_]{0,40}):([A-Za-z0-9-]{1,64})$/.exec(v);
        return m ? { collection: m[1], id: m[2] } : null;
      } catch (e) { return null; }
    },
    /** Every page of a list, concatenated (stops at opts.max records, default 2000). */
    listAll: function (collection, opts) {
      opts = Object.assign({}, opts || {});
      var max = opts.max || 2000, out = [];
      opts.limit = opts.limit || 500;
      function step(cursor) {
        opts.cursor = cursor;
        return jb.list(collection, opts).then(function (page) {
          out = out.concat(page.records);
          if (page.next_cursor && out.length < max) return step(page.next_cursor);
          return out;
        });
      }
      return step(undefined);
    },
    get: function (collection, id) { return call('GET', '/' + enc(collection) + '/' + enc(id)); },
    timeline: function (collection, id) { return call('GET', '/' + enc(collection) + '/' + enc(id) + '/timeline'); },

    /** Create. opts.source = {channel, ref, confidence, words} (required for agent calls). */
    create: function (collection, data, opts) {
      return call('POST', '/' + enc(collection), { data: data, source: (opts || {}).source });
    },
    /** Partial update; version = the record.version you read. opts: clear ([field]), source. */
    update: function (collection, id, patch, version, opts) {
      opts = opts || {};
      if (version === undefined || version === null) {
        return Promise.reject(new JbError(428, { code: 'if_match_required',
          error: 'jb.update needs the version you read (record.version)' }));
      }
      return call('PATCH', '/' + enc(collection) + '/' + enc(id),
        { data: patch || {}, clear: opts.clear, source: opts.source }, { 'If-Match': String(version) });
    },
    archive: function (collection, id, opts) {
      return call('POST', '/' + enc(collection) + '/' + enc(id) + '/archive', { source: (opts || {}).source });
    },
    unarchive: function (collection, id, opts) {
      return call('POST', '/' + enc(collection) + '/' + enc(id) + '/unarchive', { source: (opts || {}).source });
    },
    nextNumber: function (name, opts) {
      opts = opts || {};
      return call('POST', '/counters/' + enc(name) + '/next', { prefix: opts.prefix || '', pad: opts.pad || 0 });
    },
    events: function (since) { return call('GET', '/events' + qs({ since: since === undefined ? 0 : since })); },

    /** Additive schema change: jb.addFields('job', { x_gate_code: { type: 'string', label: 'Gate code' } }) */
    addFields: function (collection, fields, extra) {
      return call('POST', '/schema/custom', Object.assign({ collection: collection, fields: fields }, extra || {}));
    },
    /** Adopt a shared-library field: jb.adopt('job', 'sf_set_yield') */
    adopt: function (collection, key) { return call('POST', '/schema/adopt', { collection: collection, key: key }); },

    /**
     * The company system: the pages registered for this business, which the shared menu
     * shell draws. Register a page once, after the file exists; it then shows in the menu
     * of every registered page. Sections: home customers sales jobs money stock team files reports.
     */
    pages: {
      list: function (opts) { return call('GET', '/registry/pages' + qs({ archived: (opts || {}).archived ? '1' : undefined })); },
      get: function (id) { return call('GET', '/registry/pages/' + enc(id)); },
      register: function (page, opts) {
        return call('POST', '/registry/pages', Object.assign({}, page, { source: (opts || {}).source }));
      },
      update: function (id, patch, version, opts) {
        if (version === undefined || version === null) {
          return Promise.reject(new JbError(428, { code: 'if_match_required',
            error: 'jb.pages.update needs the version you read (page.version)' }));
        }
        return call('PATCH', '/registry/pages/' + enc(id),
          Object.assign({}, patch || {}, { source: (opts || {}).source }), { 'If-Match': String(version) });
      },
      archive: function (id, opts) { return call('POST', '/registry/pages/' + enc(id) + '/archive', { source: (opts || {}).source }); },
      unarchive: function (id, opts) { return call('POST', '/registry/pages/' + enc(id) + '/unarchive', { source: (opts || {}).source }); }
    },
    /**
     * Packs: collections a business turns on (e.g. 'risk-transfer'). jb.pack('crt').get('book')
     * calls the turned-on pack's own routes under /api/biz/v1/<prefix>/...
     */
    packs: {
      list: function () { return call('GET', '/packs'); },
      enable: function (name, opts) { return call('POST', '/packs/' + enc(name) + '/enable', { source: (opts || {}).source }); }
    },
    pack: function (prefix) {
      if (!/^[a-z]{2,8}$/.test(String(prefix))) throw new JbError(400, { code: 'bad_pack', error: 'pack prefix is 2-8 letters' });
      function p(path) {
        path = String(path || '');
        if (!/^[A-Za-z0-9_\-\/]*$/.test(path) || path.indexOf('..') !== -1) {
          throw new JbError(400, { code: 'bad_path', error: 'pack paths are letters, digits, - _ and /' });
        }
        return '/' + prefix + (path ? '/' + path : '');
      }
      return {
        get: function (path, params) { return call('GET', p(path) + qs(params || {})); },
        post: function (path, body) { return call('POST', p(path), body || {}); }
      };
    },

    /**
     * The backend-private file store. upload(file, {source, name}) -> {id, url: 'biz-file:<id>', ...}.
     * Files are never public: open(id) fetches the bytes with the person's session and returns a
     * blob: URL for this page only (nothing is kept in the browser).
     */
    files: {
      upload: function (file, opts) {
        opts = opts || {};
        var fd = new FormData();
        fd.append('file', file, opts.name || (file && file.name) || 'file');
        if (opts.source) fd.append('source', JSON.stringify(opts.source));
        var headers = { 'Accept': 'application/json' };
        if (SANDBOX) headers['X-Biz-Sandbox'] = SANDBOX;
        if (SURFACE) headers['X-JB-Staff-Surface'] = SURFACE.token;
        return doFetch(BASE + '/files', { method: 'POST', headers: headers, body: fd }).then(function (resp) {
          return resp.text().then(function (text) {
            var data = null;
            try { data = text ? JSON.parse(text) : null; } catch (e) { data = { error: text }; }
            if (!resp.ok) throw new JbError(resp.status, data);
            return data;
          });
        });
      },
      meta: function (id) { return call('GET', '/files/' + enc(id) + '/meta'); },
      idOf: function (url) { var m = /^biz-file:([0-9a-f]{64})$/.exec(String(url || '')); return m ? m[1] : null; },
      open: function (id) {
        var headers = {};
        if (SANDBOX) headers['X-Biz-Sandbox'] = SANDBOX;
        if (SURFACE) headers['X-JB-Staff-Surface'] = SURFACE.token;
        return doFetch(BASE + '/files/' + enc(id), { method: 'GET', headers: headers }).then(function (resp) {
          if (!resp.ok) return resp.text().then(function (t) { var d = null; try { d = JSON.parse(t); } catch (e) { d = { error: t }; } throw new JbError(resp.status, d); });
          return resp.blob().then(function (b) { return global.URL.createObjectURL(b); });
        });
      }
    },

    /** The menu model the shell draws: {sections, tabs, more, company, theme, icons}. */
    shell: function (pageId) { return call('GET', '/registry/shell' + qs({ page: pageId })); },

    /** Company styling from the company record: {name, logo, colors, fonts} (null fields when unset). */
    brand: function () {
      return jb.list('company', { limit: 1 }).then(function (page) {
        var c = (page.records[0] || {}).data || {};
        var b = c.brand || {};
        return { name: c.name || null, logo: b.logo || null, colors: b.colors || null, fonts: b.fonts || null };
      });
    },

    /**
     * Subscribe to changes. collection = name or '*'. Polls the change feed (default every 5 s)
     * and stops while the page is hidden. Returns an unsubscribe function.
     */
    on: function (collection, cb, opts) {
      opts = opts || {};
      var interval = Math.max(2000, opts.interval || 5000);
      var since = null, stopped = false, timer = null;
      function tick() {
        if (stopped) return;
        if (global.document && global.document.hidden) { timer = setTimeout(tick, interval); return; }
        var first = since === null;
        // first tick asks for the latest id only (since=-1): history is skipped, new changes delivered
        jb.events(first ? -1 : since).then(function (res) {
          if (first) { since = res.last_id; return; }
          since = res.last_id;
          res.events.forEach(function (ev) {
            if (collection === '*' || ev.collection === collection) {
              try { cb(ev); } catch (e) { if (global.console) console.error('jb.on handler', e); }
            }
          });
        }).catch(function (e) {
          if (global.console) console.warn('jb.on: change feed unavailable', e && e.code);
        }).then(function () { if (!stopped) timer = setTimeout(tick, interval); });
      }
      tick();
      return function () { stopped = true; if (timer) clearTimeout(timer); };
    }
  };

  // Gate-3b/5 (L1, rules 1.5.0): every platform staff page carries a visible "Sign out" control. It ends the
  // httpOnly staff cookie on the staff origin (POST <staff base>signout, same-origin) and returns to sign-in.
  jb.staffSignOut = function () {
    if (!SURFACE || !SURFACE.base) return Promise.resolve(false);
    return fetch(SURFACE.base + 'signout', { method: 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Accept': 'application/json' } }).then(function () {
      global.location.replace(SURFACE.base + 'signin');
      return true;
    });
  };
  if (SURFACE && SURFACE.base && global.document) {
    var addSignOut = function () {
      try {
        if (global.document.getElementById('jb-staff-signout')) return;
        var b = global.document.createElement('button');
        b.id = 'jb-staff-signout';
        b.type = 'button';
        b.textContent = 'Sign out';
        b.setAttribute('style', 'position:fixed;top:8px;right:8px;z-index:50;min-height:44px;padding:0 14px;' +
          'border-radius:8px;border:1px solid #94a3b8;background:#fff;color:#0f172a;font:inherit;cursor:pointer');
        b.addEventListener('click', function () { jb.staffSignOut(); });
        global.document.body.appendChild(b);
      } catch (e) { /* no DOM */ }
    };
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', addSignOut);
    else addSignOut();
  }

  global.jb = jb;
})(typeof window !== 'undefined' ? window : this);
