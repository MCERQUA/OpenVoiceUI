/**
 * JamBot Browser Companion — Action Executor
 *
 * Atomic action execution with verification. Every action:
 * 1. Takes a before-snapshot
 * 2. Executes the action (click, fill, select, scroll, etc.)
 * 3. Waits for DOM to settle (MutationObserver-based, not fixed timeout)
 * 4. Takes an after-snapshot
 * 5. Diffs before/after to report what changed
 * 6. Returns an ActionResult with ok/fail + detail + new snapshot + changes
 *
 * Depends on: lib/semantic-tree.js (window._JamBot)
 */
(function () {
  'use strict';

  const JB = window._JamBot;

  // ── DOM Settle Detection ───────────────────────────────────────────────────
  // Waits for DOM mutations to stop. Resolves when 300ms pass with no mutations,
  // or at max timeout. Much more reliable than fixed setTimeout.

  function waitForDOMSettle(minMs, maxMs) {
    minMs = minMs || 800;
    maxMs = maxMs || 3000;

    return new Promise(resolve => {
      let settled = false;
      let quietTimer = null;
      const QUIET_PERIOD = 300; // ms of silence to consider settled

      const observer = new MutationObserver(() => {
        // Reset quiet timer on each mutation
        if (quietTimer) clearTimeout(quietTimer);
        quietTimer = setTimeout(() => {
          if (!settled) {
            settled = true;
            observer.disconnect();
            resolve();
          }
        }, QUIET_PERIOD);
      });

      observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
      });

      // Minimum wait before we can resolve
      setTimeout(() => {
        if (!settled) {
          // Start the quiet period check
          if (!quietTimer) {
            quietTimer = setTimeout(() => {
              if (!settled) {
                settled = true;
                observer.disconnect();
                resolve();
              }
            }, QUIET_PERIOD);
          }
        }
      }, minMs);

      // Hard max timeout
      setTimeout(() => {
        if (!settled) {
          settled = true;
          observer.disconnect();
          resolve();
        }
      }, maxMs);
    });
  }

  // ── Visual Feedback Banner ─────────────────────────────────────────────────

  function showBanner(text, color) {
    const existing = document.getElementById('__jb_banner');
    if (existing) existing.remove();

    const b = document.createElement('div');
    b.id = '__jb_banner';
    b.style.cssText = [
      'position:fixed', 'bottom:24px', 'left:50%', 'transform:translateX(-50%)',
      `background:${color}`, 'color:#000', 'padding:8px 18px', 'border-radius:20px',
      'font-size:13px', 'font-weight:600', 'z-index:2147483647',
      'font-family:-apple-system,BlinkMacSystemFont,sans-serif',
      'pointer-events:none', 'box-shadow:0 3px 16px rgba(0,0,0,0.35)',
      'transition:opacity 0.3s', 'white-space:nowrap',
    ].join(';');
    b.textContent = text;
    document.body.appendChild(b);
    setTimeout(() => { b.style.opacity = '0'; setTimeout(() => b.remove(), 350); }, 1800);
  }

  // ── Element Highlight ──────────────────────────────────────────────────────

  function highlightElement(el, color) {
    el.style.outline = `3px solid ${color}`;
    el.style.outlineOffset = '2px';
    setTimeout(() => {
      el.style.outline = '';
      el.style.outlineOffset = '';
    }, 2000);
  }

  // ── Resolve Target (ref or CSS selector) ───────────────────────────────────

  function resolveTarget(target) {
    if (!target) return null;

    // Ref-based: @e1, @e2, etc.
    if (target.startsWith('@e')) {
      return JB.resolveRef(target);
    }

    // CSS selector fallback
    return JB.resolveSelector(target);
  }

  // ── Action: Click ──────────────────────────────────────────────────────────

  function doClick(el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    highlightElement(el, '#00d2ff');

    return new Promise(resolve => {
      setTimeout(() => {
        const rect = el.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const opts = { bubbles: true, cancelable: true, clientX: x, clientY: y, view: window };

        el.dispatchEvent(new MouseEvent('mousedown', opts));
        el.dispatchEvent(new MouseEvent('mouseup', opts));
        el.dispatchEvent(new MouseEvent('click', opts));

        const label = (el.getAttribute('aria-label') || el.textContent?.trim() || '').slice(0, 40);
        showBanner(`Clicking: ${label}`, '#00d2ff');
        resolve({ ok: true, detail: `Clicked "${label}"` });
      }, 300);
    });
  }

  // ── Action: Fill ───────────────────────────────────────────────────────────

  function doFill(el, value) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    highlightElement(el, '#22c55e');

    return new Promise(resolve => {
      // Find the actual contenteditable element
      const ceTarget = el.contentEditable === 'true' ? el
        : el.closest('[contenteditable="true"]')
        || el.querySelector('[contenteditable="true"]')
        || el;
      const isContentEditable = ceTarget.contentEditable === 'true'
        || ceTarget.getAttribute('contenteditable') === 'true';

      if (isContentEditable) {
        // Contenteditable (Facebook Lexical, rich text editors)
        const rect = ceTarget.getBoundingClientRect();
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;

        // Click to place cursor
        ceTarget.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: cx, clientY: cy, view: window }));
        ceTarget.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: cx, clientY: cy, view: window }));
        ceTarget.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: cx, clientY: cy, view: window }));
        ceTarget.focus();

        setTimeout(() => {
          // Try execCommand first
          document.execCommand('selectAll', false, null);
          const ok = document.execCommand('insertText', false, value);

          if (!ok || !ceTarget.textContent || ceTarget.textContent.trim().length < 3) {
            // Try beforeinput event (Lexical)
            ceTarget.dispatchEvent(new InputEvent('beforeinput', {
              bubbles: true, cancelable: true,
              inputType: 'insertText', data: value,
            }));

            // Direct DOM fallback
            if (!ceTarget.textContent || ceTarget.textContent.trim().length < 3) {
              const p = ceTarget.querySelector('p') || ceTarget;
              p.textContent = value;
              ceTarget.dispatchEvent(new InputEvent('input', {
                bubbles: true, inputType: 'insertText', data: value,
              }));
            }
          }

          showBanner(`Filled: "${value.slice(0, 30)}${value.length > 30 ? '...' : ''}"`, '#4ade80');
          resolve({ ok: true, detail: `Filled with "${value.slice(0, 50)}"` });
        }, 200);
      } else {
        // Standard input/textarea — native value setter for React compatibility
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set
          || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
        if (setter) setter.call(el, value); else el.value = value;

        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
        el.focus();

        showBanner(`Filled: "${value.slice(0, 30)}${value.length > 30 ? '...' : ''}"`, '#4ade80');
        resolve({ ok: true, detail: `Filled with "${value.slice(0, 50)}"` });
      }
    });
  }

  // ── Action: Select ─────────────────────────────────────────────────────────

  function doSelect(el, value) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    highlightElement(el, '#22c55e');

    // Find matching option
    const options = Array.from(el.options || []);
    const match = options.find(o =>
      o.value === value || o.textContent.trim().toLowerCase() === value.toLowerCase()
    );

    if (match) {
      el.value = match.value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('input', { bubbles: true }));
      showBanner(`Selected: "${match.textContent.trim()}"`, '#4ade80');
      return { ok: true, detail: `Selected "${match.textContent.trim()}"` };
    }

    showBanner(`Option not found: "${value}"`, '#ef4444');
    return { ok: false, detail: `Option "${value}" not found. Available: ${options.slice(0, 5).map(o => o.textContent.trim()).join(', ')}` };
  }

  // ── Action: Check/Uncheck ──────────────────────────────────────────────────

  function doCheck(el, shouldCheck) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    highlightElement(el, '#22c55e');

    if (el.checked !== shouldCheck) {
      el.click(); // Most reliable for checkboxes/radios
    }

    const label = (el.getAttribute('aria-label') || el.name || 'checkbox').slice(0, 40);
    showBanner(`${shouldCheck ? 'Checked' : 'Unchecked'}: ${label}`, '#4ade80');
    return { ok: true, detail: `${shouldCheck ? 'Checked' : 'Unchecked'} "${label}"` };
  }

  // ── Action: Hover ──────────────────────────────────────────────────────────

  function doHover(el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const rect = el.getBoundingClientRect();
    const opts = { bubbles: true, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2, view: window };

    el.dispatchEvent(new MouseEvent('mouseenter', opts));
    el.dispatchEvent(new MouseEvent('mouseover', opts));
    el.dispatchEvent(new MouseEvent('mousemove', opts));

    const label = (el.getAttribute('aria-label') || el.textContent?.trim() || '').slice(0, 40);
    showBanner(`Hovering: ${label}`, '#00d2ff');
    return { ok: true, detail: `Hovered over "${label}"` };
  }

  // ── Action: Scroll ─────────────────────────────────────────────────────────

  function doScroll(target) {
    const sc = document.scrollingElement || document.documentElement;
    const beforeTop = sc.scrollTop;

    if (target === 'top') {
      sc.scrollTop = 0;
      window.scrollTo({ top: 0 });
      showBanner('Scrolled to top', '#00d2ff');
    } else if (target === 'bottom') {
      sc.scrollTop = sc.scrollHeight;
      showBanner('Scrolled to bottom', '#00d2ff');
    } else if (/^[+-]\d+$/.test(target)) {
      const px = parseInt(target, 10);
      sc.scrollTop += px;
      window.scrollBy({ top: px });
      showBanner(`Scrolled ${px > 0 ? 'down' : 'up'} ${Math.abs(px)}px`, '#00d2ff');
    } else if (target.startsWith('@e')) {
      // Scroll element into view
      const el = JB.resolveRef(target);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        showBanner('Scrolled to element', '#00d2ff');
      } else {
        return { ok: false, detail: `Element ${target} not found` };
      }
    } else {
      // CSS selector — scroll into view
      const el = JB.resolveSelector(target);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        showBanner('Scrolled to element', '#00d2ff');
      }
    }

    const afterTop = sc.scrollTop;
    const atBottom = afterTop + sc.clientHeight >= sc.scrollHeight - 10;

    return {
      ok: true,
      detail: atBottom ? 'Scrolled — at bottom of page' : `Scrolled ${afterTop - beforeTop}px`,
      scrolledBy: afterTop - beforeTop,
      atBottom,
      newHeight: sc.scrollHeight,
    };
  }

  // ── Action: Press Key ──────────────────────────────────────────────────────

  function doPress(key) {
    const target = document.activeElement || document.body;
    const opts = { key, code: `Key${key.toUpperCase()}`, bubbles: true, cancelable: true };

    target.dispatchEvent(new KeyboardEvent('keydown', opts));
    target.dispatchEvent(new KeyboardEvent('keypress', opts));
    target.dispatchEvent(new KeyboardEvent('keyup', opts));

    showBanner(`Pressed: ${key}`, '#00d2ff');
    return { ok: true, detail: `Pressed ${key}` };
  }

  // ── Action: Highlight ──────────────────────────────────────────────────────

  function doHighlight(target) {
    // Clear previous highlights
    document.querySelectorAll('[data-jb-hl]').forEach(el => {
      el.style.outline = el.dataset.jbOrig || '';
      el.removeAttribute('data-jb-hl');
      el.removeAttribute('data-jb-orig');
    });

    const els = target === '*'
      ? [document.body]
      : target.startsWith('@e')
        ? [JB.resolveRef(target)].filter(Boolean)
        : Array.from(document.querySelectorAll(target)).slice(0, 10);

    els.forEach(el => {
      el.dataset.jbOrig = el.style.outline || '';
      el.dataset.jbHl = '1';
      el.style.outline = '3px solid #00d2ff';
      el.style.outlineOffset = '2px';
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });

    showBanner(`Highlighted ${els.length} element(s)`, '#00d2ff');
    return { ok: true, detail: `Highlighted ${els.length} element(s)` };
  }

  // ── Action: Read Full Page Text ────────────────────────────────────────────

  function doReadPage(maxChars) {
    maxChars = maxChars || 15000;
    const clone = document.body.cloneNode(true);
    ['script', 'style', 'noscript', 'svg', 'iframe'].forEach(
      t => clone.querySelectorAll(t).forEach(el => el.remove())
    );
    const text = (clone.innerText || '').replace(/\s{3,}/g, '\n\n').trim().slice(0, maxChars);
    showBanner('Reading full page...', '#00d2ff');
    return { ok: true, detail: `Read ${text.length} chars`, text };
  }

  // ── Execute Action (main entry point) ──────────────────────────────────────
  // Takes before-snapshot, executes action, waits for settle, takes after-snapshot,
  // diffs, returns ActionResult.

  async function executeAction(action) {
    // Before snapshot
    const beforeSnapshot = JB.takeSnapshot();
    const beforeSerialized = JB.serializeSnapshot(beforeSnapshot);

    let result;

    try {
      switch (action.type) {
        case 'click': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = await doClick(el);
          break;
        }

        case 'fill': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = await doFill(el, action.value);
          break;
        }

        case 'select': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = doSelect(el, action.value);
          break;
        }

        case 'check': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = doCheck(el, true);
          break;
        }

        case 'uncheck': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = doCheck(el, false);
          break;
        }

        case 'hover': {
          const el = resolveTarget(action.ref || action.selector);
          if (!el) {
            return makeResult(action, false, `Element ${action.ref || action.selector} not found`, beforeSnapshot);
          }
          result = doHover(el);
          break;
        }

        case 'scroll':
          result = doScroll(action.target);
          break;

        case 'press':
          result = doPress(action.key);
          break;

        case 'highlight':
          result = doHighlight(action.ref || action.selector || action.target);
          break;

        case 'read_page':
          result = doReadPage(action.maxChars);
          // Read page doesn't need DOM settle — return immediately with text
          return {
            action: action.type,
            ref: action.ref,
            ok: true,
            detail: result.detail,
            text: result.text,
            snapshot: beforeSerialized,
            changes: [],
          };

        default:
          return makeResult(action, false, `Unknown action type: ${action.type}`, beforeSnapshot);
      }
    } catch (e) {
      return makeResult(action, false, `Action failed: ${e.message}`, beforeSnapshot);
    }

    if (!result.ok) {
      return makeResult(action, false, result.detail, beforeSnapshot);
    }

    // Wait for DOM to settle after action
    await waitForDOMSettle(800, 3000);

    // After snapshot
    const afterSnapshot = JB.takeSnapshot();
    const afterSerialized = JB.serializeSnapshot(afterSnapshot);

    // Diff
    const changes = JB.diffSnapshots(beforeSnapshot, afterSnapshot);

    return {
      action: action.type,
      ref: action.ref || action.selector,
      ok: true,
      detail: result.detail,
      snapshot: afterSerialized,
      changes,
      ...(result.scrolledBy !== undefined ? { scrolledBy: result.scrolledBy, atBottom: result.atBottom } : {}),
    };
  }

  function makeResult(action, ok, detail, snapshot) {
    return {
      action: action.type,
      ref: action.ref || action.selector,
      ok,
      detail,
      snapshot: JB.serializeSnapshot(snapshot),
      changes: [],
    };
  }

  // ── Expose API ─────────────────────────────────────────────────────────────

  JB.executeAction = executeAction;
  JB.waitForDOMSettle = waitForDOMSettle;

})();
