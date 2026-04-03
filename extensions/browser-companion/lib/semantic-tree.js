/**
 * JamBot Browser Companion — Semantic Page Intelligence
 *
 * Builds a compact semantic tree of interactive elements with versioned refs (@e1, @e2...).
 * Replaces the old raw DOM dump + CSS selector approach with ~80-90% token reduction.
 *
 * Loaded as content script (no ES modules in MV3). Exposes functions on window._JamBot.
 *
 * Core concepts:
 * - ElementRef: structured representation of an interactive element with a deterministic ref
 * - PageSnapshot: full page state including elements, forms, text summary, scroll position
 * - RefMap: in-memory map from ref string (@e1) to live DOM element for action execution
 */
(function () {
  'use strict';

  window._JamBot = window._JamBot || {};

  // ── Ref Map (stateful — persists between snapshots within a page) ──────────
  const _refMap = new Map(); // '@e1' -> HTMLElement
  let _snapshotVersion = 0;

  // ── Interactive Element Selectors ──────────────────────────────────────────
  const INTERACTIVE_SELECTORS = [
    'a[href]',
    'button', '[role="button"]',
    'input:not([type="hidden"])',
    'textarea', '[contenteditable="true"]',
    'select',
    '[role="link"]', '[role="tab"]', '[role="menuitem"]',
    '[role="checkbox"]', '[role="radio"]',
    '[role="textbox"]', '[role="combobox"]', '[role="searchbox"]',
    '[tabindex]:not([tabindex="-1"])',
  ];

  const MAX_ELEMENTS = 50;
  const MAX_LABEL_LEN = 80;
  const MAX_TEXT_WORDS = 200;
  const MAX_OPTIONS = 20;
  const MAX_OPTION_LEN = 40;
  const MAX_VALUE_LEN = 100;
  const VIEWPORT_RANGE = 3; // collect elements within N viewports of current scroll

  // ── Semantic Tag Classification ────────────────────────────────────────────

  function classifyElement(el) {
    const tag = el.tagName.toUpperCase();
    const role = el.getAttribute('role');

    if (tag === 'A' || role === 'link') return 'LINK';
    if (tag === 'BUTTON' || role === 'button') return 'BUTTON';
    if (tag === 'INPUT') return 'INPUT';
    if (tag === 'TEXTAREA' || role === 'textbox' || el.contentEditable === 'true') return 'TEXTAREA';
    if (tag === 'SELECT' || role === 'combobox') return 'SELECT';
    if (role === 'checkbox') return 'CHECKBOX';
    if (role === 'radio') return 'RADIO';
    if (role === 'tab') return 'TAB';
    if (role === 'menuitem') return 'MENUITEM';
    if (role === 'searchbox') return 'INPUT';
    return 'INTERACTIVE';
  }

  // ── Label Extraction ───────────────────────────────────────────────────────

  function getLabel(el, semanticTag) {
    // Priority: aria-label > visible text (for buttons/links) > placeholder > name > title
    const ariaLabel = el.getAttribute('aria-label');
    if (ariaLabel) return ariaLabel.trim().slice(0, MAX_LABEL_LEN);

    // For buttons, links, tabs, menu items — use visible text content
    if (['BUTTON', 'LINK', 'TAB', 'MENUITEM'].includes(semanticTag)) {
      const text = el.textContent?.trim();
      if (text && text.length <= 60) return text.slice(0, MAX_LABEL_LEN);
    }

    // Placeholder or aria-placeholder
    const ph = el.placeholder || el.getAttribute('aria-placeholder');
    if (ph) return ph.trim().slice(0, MAX_LABEL_LEN);

    // Name attribute
    const name = el.getAttribute('name');
    if (name) return name.slice(0, MAX_LABEL_LEN);

    // Title attribute
    const title = el.title;
    if (title) return title.trim().slice(0, MAX_LABEL_LEN);

    // Associated label element
    if (el.id) {
      const labelEl = document.querySelector(`label[for="${el.id}"]`);
      if (labelEl) return labelEl.textContent?.trim().slice(0, MAX_LABEL_LEN) || '';
    }

    return '';
  }

  // ── Build Single Element Ref ───────────────────────────────────────────────

  function buildElementRef(el, index) {
    const ref = `@e${index}`;
    const semanticTag = classifyElement(el);
    const label = getLabel(el, semanticTag);

    // Skip unlabeled generic interactive elements (noise)
    if (!label && semanticTag === 'INTERACTIVE') return null;

    const result = {
      ref,
      tag: semanticTag,
      label,
      _domNode: el, // kept for ref map, stripped before serialization
    };

    // Type-specific properties
    if (semanticTag === 'INPUT') {
      result.type = el.type || 'text';
      if (result.type !== 'password') {
        result.value = (el.value || '').slice(0, MAX_VALUE_LEN);
      }
    }

    if (semanticTag === 'TEXTAREA') {
      result.value = (el.value || el.textContent || '').slice(0, MAX_VALUE_LEN);
    }

    if (semanticTag === 'SELECT') {
      result.options = Array.from(el.options || [])
        .slice(0, MAX_OPTIONS)
        .map(o => o.textContent.trim().slice(0, MAX_OPTION_LEN));
      result.selected = el.options?.[el.selectedIndex]?.textContent?.trim() || '';
    }

    if (semanticTag === 'LINK') {
      result.href = (el.href || '').replace(window.location.origin, '') || '';
    }

    if (semanticTag === 'CHECKBOX' || semanticTag === 'RADIO') {
      result.checked = !!el.checked;
    }

    if (el.disabled) {
      result.disabled = true;
    }

    return result;
  }

  // ── Text Summary Extraction ────────────────────────────────────────────────

  function extractTextSummary() {
    const clone = document.body.cloneNode(true);
    const removeTags = ['script', 'style', 'noscript', 'svg', 'iframe', 'link', 'meta'];
    removeTags.forEach(t => clone.querySelectorAll(t).forEach(el => el.remove()));

    const raw = (clone.innerText || '')
      .replace(/\s{3,}/g, '\n\n')
      .replace(/[ \t]{2,}/g, ' ')
      .trim();

    const words = raw.split(/\s+/).filter(w => w.length > 0);
    return {
      text: words.slice(0, MAX_TEXT_WORDS).join(' '),
      wordCount: words.length,
    };
  }

  // ── Form Grouping ─────────────────────────────────────────────────────────

  function groupIntoForms(refs) {
    const formMap = new Map();
    for (const ref of refs) {
      if (!ref._domNode) continue;
      const form = ref._domNode.closest('form');
      if (form) {
        if (!formMap.has(form)) {
          formMap.set(form, {
            action: form.action?.replace(window.location.origin, '') || '',
            elements: [],
          });
        }
        formMap.get(form).elements.push(ref);
      }
    }
    return Array.from(formMap.values());
  }

  // ── Build Semantic Tree ────────────────────────────────────────────────────

  function buildSemanticTree() {
    const refs = [];
    let refCounter = 1;
    const viewH = window.innerHeight;
    const scrollTop = (document.scrollingElement || document.documentElement).scrollTop;

    // Collect all interactive elements
    const candidates = document.querySelectorAll(INTERACTIVE_SELECTORS.join(','));
    const seen = new WeakSet();

    // Score elements by viewport proximity for prioritization
    const scored = [];

    for (const el of candidates) {
      if (seen.has(el)) continue;
      seen.add(el);

      // Visibility check: must have non-zero bounding rect
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;

      // Range check: within N viewports of current scroll position
      if (rect.top > viewH * VIEWPORT_RANGE || rect.bottom < -viewH) continue;

      // Compute distance from viewport center for prioritization
      const centerY = rect.top + rect.height / 2;
      const viewCenter = viewH / 2;
      const distance = Math.abs(centerY - viewCenter);

      scored.push({ el, distance });
    }

    // Sort by proximity to viewport center (most visible first)
    scored.sort((a, b) => a.distance - b.distance);

    // Build refs for top N elements
    for (const { el } of scored) {
      if (refs.length >= MAX_ELEMENTS) break;
      const ref = buildElementRef(el, refCounter);
      if (ref) {
        refs.push(ref);
        refCounter++;
      }
    }

    // Build text summary
    const textSummary = extractTextSummary();

    // Group elements into forms
    const forms = groupIntoForms(refs);

    // Scroll position
    const scrollEl = document.scrollingElement || document.documentElement;

    return {
      version: ++_snapshotVersion,
      url: window.location.href,
      title: document.title,
      elements: refs,
      text: textSummary.text,
      textWordCount: textSummary.wordCount,
      forms,
      scrollPosition: {
        top: Math.round(scrollEl.scrollTop),
        height: scrollEl.scrollHeight,
        viewHeight: viewH,
      },
      timestamp: Date.now(),
    };
  }

  // ── Take Snapshot (builds tree + populates ref map) ────────────────────────

  function takeSnapshot() {
    _refMap.clear();
    const snapshot = buildSemanticTree();

    // Populate ref map and strip _domNode from serializable output
    for (const el of snapshot.elements) {
      if (el._domNode) {
        _refMap.set(el.ref, el._domNode);
        delete el._domNode;
      }
    }

    // Also strip _domNode from form elements
    for (const form of snapshot.forms) {
      for (const el of form.elements) {
        delete el._domNode;
      }
    }

    return snapshot;
  }

  // ── Resolve Ref to DOM Element ─────────────────────────────────────────────

  function resolveRef(ref) {
    const el = _refMap.get(ref);
    if (!el || !el.isConnected) return null;

    // Verify still visible
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;

    return el;
  }

  // ── Serialize Snapshot to Compact Text ─────────────────────────────────────

  function serializeSnapshot(snapshot) {
    let out = `PAGE: "${snapshot.title}" -- ${snapshot.url}\n`;

    const scrollPct = snapshot.scrollPosition.height > 0
      ? Math.round((snapshot.scrollPosition.top / snapshot.scrollPosition.height) * 100)
      : 0;
    out += `SCROLL: ${snapshot.scrollPosition.top}/${snapshot.scrollPosition.height} (${scrollPct}%)\n`;

    // Forms
    if (snapshot.forms.length > 0) {
      out += '\n';
      for (const form of snapshot.forms) {
        out += `FORM${form.action ? ' -> ' + form.action : ''}:\n`;
        for (const el of form.elements) {
          out += serializeElement(el, '  ');
        }
      }
    }

    // Non-form elements grouped by type
    const formRefs = new Set(snapshot.forms.flatMap(f => f.elements.map(e => e.ref)));
    const loose = snapshot.elements.filter(e => !formRefs.has(e.ref));

    // Actions (buttons, tabs, menu items)
    const actions = loose.filter(e => ['BUTTON', 'TAB', 'MENUITEM'].includes(e.tag));
    if (actions.length > 0) {
      out += '\nACTIONS:\n';
      for (const el of actions) {
        out += `  ${el.ref} ${el.tag} "${el.label}"${el.disabled ? ' [disabled]' : ''}\n`;
      }
    }

    // Links (cap at 15)
    const links = loose.filter(e => e.tag === 'LINK').slice(0, 15);
    if (links.length > 0) {
      out += '\nLINKS:\n';
      for (const el of links) {
        out += `  ${el.ref} LINK "${el.label}" -> ${el.href || ''}\n`;
      }
    }

    // Inputs/textareas not in forms
    const inputs = loose.filter(e => ['INPUT', 'TEXTAREA', 'SELECT', 'CHECKBOX', 'RADIO'].includes(e.tag));
    if (inputs.length > 0) {
      out += '\nINPUTS:\n';
      for (const el of inputs) {
        out += serializeElement(el, '  ');
      }
    }

    // Text summary
    out += `\nTEXT: [${snapshot.textWordCount} words] ${snapshot.text.slice(0, 500)}\n`;

    return out;
  }

  function serializeElement(el, indent) {
    let line = `${indent}${el.ref} ${el.tag}`;
    if (el.type) line += `[${el.type}]`;
    line += ` "${el.label}"`;
    if (el.placeholder) line += ` placeholder="${el.placeholder}"`;
    if (el.value) line += ` value="${el.value}"`;
    if (el.options) {
      const shown = el.options.slice(0, 5).map(o => `"${o}"`).join(',');
      line += ` options=[${shown}]`;
      if (el.options.length > 5) line += `+${el.options.length - 5}`;
    }
    if (el.selected) line += ` selected="${el.selected}"`;
    if (el.checked) line += ' [checked]';
    if (el.disabled) line += ' [disabled]';
    line += '\n';
    return line;
  }

  // ── Diff Two Snapshots ─────────────────────────────────────────────────────

  function diffSnapshots(before, after) {
    const changes = [];

    // URL change
    if (before.url !== after.url) {
      changes.push(`URL changed: ${after.url}`);
    }

    // Title change
    if (before.title !== after.title) {
      changes.push(`Title changed: "${after.title}"`);
    }

    // Scroll change
    if (before.scrollPosition.top !== after.scrollPosition.top) {
      const delta = after.scrollPosition.top - before.scrollPosition.top;
      changes.push(`Scrolled ${delta > 0 ? 'down' : 'up'} ${Math.abs(delta)}px`);
    }

    // Text word count change
    const wordDelta = after.textWordCount - before.textWordCount;
    if (Math.abs(wordDelta) > 10) {
      changes.push(`Text: ${wordDelta > 0 ? '+' : ''}${wordDelta} words`);
    }

    // New elements
    const beforeRefs = new Set(before.elements.map(e => `${e.tag}:${e.label}`));
    const newEls = after.elements.filter(e => !beforeRefs.has(`${e.tag}:${e.label}`));
    if (newEls.length > 0) {
      const desc = newEls.slice(0, 5).map(e => `${e.ref} ${e.tag} "${e.label}"`).join(', ');
      changes.push(`New elements: ${desc}${newEls.length > 5 ? ` +${newEls.length - 5} more` : ''}`);
    }

    // Gone elements
    const afterRefs = new Set(after.elements.map(e => `${e.tag}:${e.label}`));
    const goneEls = before.elements.filter(e => !afterRefs.has(`${e.tag}:${e.label}`));
    if (goneEls.length > 0) {
      const desc = goneEls.slice(0, 3).map(e => `${e.tag} "${e.label}"`).join(', ');
      changes.push(`Removed: ${desc}${goneEls.length > 3 ? ` +${goneEls.length - 3} more` : ''}`);
    }

    return changes;
  }

  // ── CSS Selector Fallback (backward compat) ────────────────────────────────

  function resolveSelector(selector) {
    try {
      return document.querySelector(selector);
    } catch {
      return null;
    }
  }

  // ── Expose API ─────────────────────────────────────────────────────────────

  window._JamBot.takeSnapshot = takeSnapshot;
  window._JamBot.resolveRef = resolveRef;
  window._JamBot.resolveSelector = resolveSelector;
  window._JamBot.serializeSnapshot = serializeSnapshot;
  window._JamBot.diffSnapshots = diffSnapshots;
  window._JamBot.getRefMap = () => _refMap;

})();
