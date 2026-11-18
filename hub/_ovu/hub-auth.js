/* hub-auth.js — keeps the JamBot Hub signed in on the business's own hostname (OpenVoiceUI-owned, not vendored).
 *
 * The Hub reads /api/biz/v1 with the same-origin Clerk `__session` cookie. Clerk session tokens live ~60 s,
 * so, exactly like the OpenVoiceUI shell (src/app.js _syncSessionCookie), this loads Clerk on the page and
 * re-writes the cookie every 50 s while the Hub is open. It also offers window.authFetch (Bearer token) for
 * code that wants it. No session -> back to the sign-in shell, which returns here (/?redirect=/hub/).
 * Nothing is stored in the browser beyond the session cookie Clerk already uses.
 */
(function () {
  'use strict';
  var cfg = globalThis.JAMBOT_HUB || {};
  if (!cfg.auth || !cfg.clerkPublishableKey) return;          // local install: auth is off, nothing to keep fresh

  function backToSignIn() {
    var back = location.pathname + location.search + location.hash;
    location.replace('/?redirect=' + encodeURIComponent(back.indexOf('/hub/') === 0 ? back : '/hub/'));
  }
  function writeCookie(token) {
    document.cookie = '__session=' + token + '; path=/; Max-Age=2592000; SameSite=Lax; Secure';
  }
  async function token() {
    try { return (globalThis.Clerk && Clerk.session) ? await Clerk.session.getToken() : null; } catch (e) { return null; }
  }
  async function sync() {
    var t = await token();
    if (t) writeCookie(t);
    return t;
  }
  globalThis.authFetch = async function (url, opts) {
    opts = Object.assign({}, opts || {});
    opts.credentials = 'same-origin';
    var t = await token();
    if (t) opts.headers = Object.assign({}, opts.headers || {}, { Authorization: 'Bearer ' + t });
    return fetch(url, opts);
  };

  var s = document.createElement('script');
  s.async = true;
  s.crossOrigin = 'anonymous';
  s.dataset.clerkPublishableKey = cfg.clerkPublishableKey;
  s.src = 'https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js';
  s.onload = async function () {
    try {
      await Clerk.load();
    } catch (e) {
      console.warn('[hub] sign-in service did not load; the page keeps the session it opened with');
      return;
    }
    if (!Clerk.session) { backToSignIn(); return; }
    await sync();
    setInterval(sync, 50000);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) sync(); });
  };
  s.onerror = function () {
    console.warn('[hub] sign-in service unreachable; the page keeps the session it opened with');
  };
  document.head.appendChild(s);
})();
