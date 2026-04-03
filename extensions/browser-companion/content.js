/**
 * JamBot Browser Companion -- Content Script (v2)
 *
 * Message router between the background service worker and the lib layer.
 * Runs on every page AFTER lib/semantic-tree.js and lib/action-executor.js
 * have loaded and populated window._JamBot.
 *
 * Three responsibilities:
 * 1. MESSAGE ROUTER  -- handle get_snapshot, execute_action, get_full_text,
 *                       get_context from background.js
 * 2. ACTION RECORDER -- lightweight click/change/submit tracking, pushed
 *                       to background for context
 * 3. TEXT EXTRACTION -- clean body text for get_full_text and legacy get_context
 *
 * Does NOT contain command execution code (that lives in lib/action-executor.js).
 * Does NOT contain page reading code (that lives in lib/semantic-tree.js).
 * Does NOT monkey-patch history.pushState (Bug #21 -- handled by webNavigation
 * API in background.js instead).
 */

// ── Action Recorder ──────────────────────────────────────────────────────────
// Lightweight user interaction tracking. Rolling buffer of the last N actions,
// pushed to background.js for agent context.

const MAX_ACTIONS = 20;
const actionHistory = [];

/**
 * Build the best CSS selector for an element.
 * Priority: #id > [aria-label] > [data-testid] > [name] > tag.class1.class2 > tag
 */
function getBestSelector(el) {
  if (!el || !el.tagName) return '';

  // id is the most specific and stable
  if (el.id) return `#${el.id}`;

  // aria-label -- good specificity, often stable across deploys
  const ariaLabel = el.getAttribute('aria-label');
  if (ariaLabel) return `[aria-label="${ariaLabel}"]`;

  // data-testid -- explicitly designed as a stable selector
  const testId = el.getAttribute('data-testid');
  if (testId) return `[data-testid="${testId}"]`;

  // name attribute -- common on form elements
  const name = el.getAttribute('name');
  if (name) return `[name="${name}"]`;

  // tag + first two classes
  const tag = el.tagName.toLowerCase();
  const classes = Array.from(el.classList).slice(0, 2);
  if (classes.length > 0) return `${tag}.${classes.join('.')}`;

  // Bare tag as last resort
  return tag;
}

/**
 * Extract a short visible text label from an element.
 * Used for human-readable action descriptions.
 */
function getVisibleText(el) {
  if (!el) return '';
  const raw = el.textContent
    || el.value
    || el.placeholder
    || el.getAttribute('aria-label')
    || '';
  return raw.trim().slice(0, 80);
}

/**
 * Record a user action to the rolling buffer and push to background.
 */
function recordAction(action) {
  const entry = { ...action, timestamp: Date.now() };
  actionHistory.push(entry);
  if (actionHistory.length > MAX_ACTIONS) actionHistory.shift();

  // Push to background service worker (fire-and-forget)
  chrome.runtime.sendMessage({ type: 'action_recorded', action: entry }).catch(() => {});
}

// -- Click events (skip trivial elements) --
document.addEventListener('click', (e) => {
  const el = e.target;
  if (!el || !el.tagName) return;
  if (['HTML', 'BODY', 'SCRIPT', 'STYLE'].includes(el.tagName)) return;

  recordAction({
    type: 'click',
    tag: el.tagName.toLowerCase(),
    text: getVisibleText(el),
    selector: getBestSelector(el),
    href: el.tagName === 'A' ? el.href : undefined,
  });
}, { capture: false, passive: true });

// -- Input changes (record selector and label, never the raw value for privacy) --
document.addEventListener('change', (e) => {
  const el = e.target;
  if (!el || !['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return;

  recordAction({
    type: 'input',
    tag: el.tagName.toLowerCase(),
    inputType: el.type || undefined,
    selector: getBestSelector(el),
    label: getVisibleText(el),
  });
}, { capture: false, passive: true });

// -- Form submissions --
document.addEventListener('submit', (e) => {
  const el = e.target;
  if (!el) return;

  recordAction({
    type: 'submit',
    tag: 'form',
    selector: getBestSelector(el),
    action: el.action || window.location.href,
  });
}, { capture: false, passive: true });


// ── Text Extraction ──────────────────────────────────────────────────────────
// Clean body text for get_full_text and legacy get_context.

/**
 * Extract clean body text, stripping script/style/noscript/svg/iframe elements.
 * Returns trimmed text with collapsed whitespace.
 */
function extractCleanText(maxChars) {
  const clone = document.body.cloneNode(true);
  const stripTags = ['script', 'style', 'noscript', 'svg', 'iframe'];
  stripTags.forEach(tag => {
    clone.querySelectorAll(tag).forEach(el => el.remove());
  });

  const raw = clone.innerText || clone.textContent || '';
  return raw
    .replace(/\s{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
    .slice(0, maxChars);
}


// ── Legacy Page Context ──────────────────────────────────────────────────────
// For backward compatibility with older background.js / panel code that uses
// the get_context message. Will be phased out in favor of get_snapshot.

function getLegacyPageContext() {
  const title = document.title || '';
  const url = window.location.href;

  const metaDesc = document.querySelector('meta[name="description"]')?.content
    || document.querySelector('meta[property="og:description"]')?.content
    || '';

  const selectedText = window.getSelection()?.toString()?.trim() || '';
  const bodyText = extractCleanText(5000);

  return {
    url,
    title,
    description: metaDesc,
    bodyText,
    selectedText,
    actionHistory: [...actionHistory],
  };
}


// ── Message Listener ─────────────────────────────────────────────────────────
// All messages come from background.js. Each handler uses sendResponse() and
// returns true to indicate async response.

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {

  // ---- get_snapshot ----
  // Returns both the structured snapshot object and the compact serialized text.
  // Background uses the serialized text to send to the agent (token-efficient).
  // The structured object is available for programmatic inspection.
  if (msg.type === 'get_snapshot') {
    try {
      const snapshot = window._JamBot.takeSnapshot();
      const serialized = window._JamBot.serializeSnapshot(snapshot);
      sendResponse({ snapshot: serialized, structured: snapshot });
    } catch (e) {
      sendResponse({ error: e.message });
    }
    return true;
  }

  // ---- execute_action ----
  // Delegates to _JamBot.executeAction() from lib/action-executor.js.
  // That function handles: before-snapshot, execution, DOM settle, after-snapshot, diff.
  // Returns an ActionResult: { ok, detail, snapshot, changes, ... }
  if (msg.type === 'execute_action') {
    const action = msg.action;
    if (!action || !action.type) {
      sendResponse({ ok: false, detail: 'Missing action or action.type' });
      return true;
    }

    // executeAction is async -- wrap in a promise chain
    window._JamBot.executeAction(action)
      .then(result => sendResponse(result))
      .catch(e => sendResponse({
        ok: false,
        detail: `Action execution failed: ${e.message}`,
        action: action.type,
        ref: action.ref || action.selector,
        snapshot: '',
        changes: [],
      }));
    return true;
  }

  // ---- get_full_text ----
  // Returns clean body text up to maxChars (default 15000).
  // Strips script/style/noscript/svg/iframe elements.
  if (msg.type === 'get_full_text') {
    const maxChars = msg.maxChars || 15000;
    try {
      const text = extractCleanText(maxChars);
      sendResponse({ text });
    } catch (e) {
      sendResponse({ text: '', error: e.message });
    }
    return true;
  }

  // ---- get_context ----
  // Legacy handler for backward compatibility during transition.
  // Returns: { url, title, description, bodyText, selectedText, actionHistory }
  if (msg.type === 'get_context') {
    try {
      sendResponse(getLegacyPageContext());
    } catch (e) {
      sendResponse({ url: window.location.href, title: document.title, error: e.message });
    }
    return true;
  }

  // Unknown message type -- ignore (don't respond, let other listeners handle)
  return false;
});
