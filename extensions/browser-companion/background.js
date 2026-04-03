/**
 * JamBot Browser Companion -- Background Service Worker
 *
 * Message router between the side panel and content scripts. Handles:
 * - Tab tracking with persistent lastWebTabId (survives SW restart)
 * - Page snapshot delivery (content script -> side panel)
 * - Action execution routing (side panel -> content script -> result -> side panel)
 * - Navigation with page-load wait (15s timeout)
 * - Screenshot capture on explicit request only
 * - SPA navigation detection via webNavigation API
 *
 * Bug fixes from audit:
 * - #1:  lastWebTabId persisted to chrome.storage.local, restored on startup
 * - #4:  Navigation waits for tabs.onUpdated status:'complete' + 15s timeout
 * - #14: Screenshot removed from automatic context push, only on explicit request
 * - #15: executeScript fallback for tabs without content script
 * - #21: SPA detection via webNavigation.onHistoryStateUpdated (no pushState monkey-patch)
 *
 * Design principles:
 * - All state that must survive SW restart -> chrome.storage.local
 * - Never inject large functions via executeScript -- talk to content.js via messages
 * - Content script messages: get_snapshot (new semantic tree) or get_context (legacy)
 * - Minimal executeScript fallback for chrome:// tabs or pre-install tabs
 */

// -- Keepalive ----------------------------------------------------------------
// Chrome terminates idle service workers after ~30s. This alarm fires every 24s
// to keep the worker alive while the side panel is open.
chrome.alarms.create('keepalive', { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => {
  // No-op handler -- the alarm itself keeps the SW alive.
});

// -- Side Panel: open on extension icon click ---------------------------------
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

// -- State --------------------------------------------------------------------
let sidePanelPort = null;
let lastWebTabId = null;

// Restore lastWebTabId from storage on service worker startup.
// This fixes Bug #1: SW restarts lose the in-memory tab ID.
chrome.storage.local.get(['lastWebTabId'], (result) => {
  if (result.lastWebTabId) {
    // Verify the tab still exists before using the stored ID.
    chrome.tabs.get(result.lastWebTabId).then((tab) => {
      if (tab && tab.url && !tab.url.startsWith('chrome://') && !tab.url.startsWith('chrome-extension://')) {
        lastWebTabId = tab.id;
      }
    }).catch(() => {
      // Tab no longer exists -- clear the stale ID.
      lastWebTabId = null;
      chrome.storage.local.remove('lastWebTabId');
    });
  }
});

// -- Helpers ------------------------------------------------------------------

/**
 * Persist lastWebTabId to chrome.storage.local. Called on every change
 * so the value survives service worker restarts (Bug #1).
 */
function persistTabId(tabId) {
  lastWebTabId = tabId;
  chrome.storage.local.set({ lastWebTabId: tabId });
}

/**
 * Send a message to the side panel port. Silently swallows errors
 * (panel may have disconnected).
 */
function pushToPanel(msg) {
  if (!sidePanelPort) return;
  try {
    sidePanelPort.postMessage(msg);
  } catch (_e) {
    sidePanelPort = null;
  }
}

/**
 * Check whether a tab ID points to a real web page (not chrome:// or extension pages).
 */
async function isWebTab(tabId) {
  try {
    const tab = await chrome.tabs.get(tabId);
    return (
      tab &&
      tab.url &&
      !tab.url.startsWith('chrome://') &&
      !tab.url.startsWith('chrome-extension://') &&
      !tab.url.startsWith('devtools://')
    );
  } catch (_e) {
    return false;
  }
}

/**
 * Wait for a tab to finish loading. Returns true if the page completed,
 * false if the timeout expired. Fixes Bug #4: no page-load wait after navigate.
 */
function waitForPageLoad(tabId, timeoutMs) {
  timeoutMs = timeoutMs || 15000;

  return new Promise((resolve) => {
    let resolved = false;

    const listener = (tid, info) => {
      if (tid === tabId && info.status === 'complete' && !resolved) {
        resolved = true;
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(true);
      }
    };

    chrome.tabs.onUpdated.addListener(listener);

    setTimeout(() => {
      if (!resolved) {
        resolved = true;
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(false);
      }
    }, timeoutMs);
  });
}

/**
 * Get a page snapshot from a tab. Tries the content script first via
 * tabs.sendMessage. If the content script is not loaded (tabs opened before
 * extension install, chrome:// pages, etc.), falls back to a minimal
 * executeScript that returns basic page info. Fixes Bug #15.
 *
 * The content script may respond with either:
 * - New semantic tree format (get_snapshot): { snapshot, serialized }
 * - Legacy format (get_context): { url, title, bodyText, ... }
 *
 * Both are normalized into a consistent shape for the side panel.
 */
async function getSnapshotFromTab(tabId) {
  if (!tabId) return null;

  // Attempt 1: Ask the content script for a semantic snapshot (new protocol).
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'get_snapshot' });
    if (response && (response.snapshot || response.serialized)) {
      return {
        source: 'semantic',
        snapshot: response.serialized || response.snapshot,
        url: response.url || '',
        title: response.title || '',
      };
    }
  } catch (_e) {
    // Content script not available or does not handle get_snapshot yet.
  }

  // Attempt 2: Ask the content script for legacy context (current content.js).
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'get_context' });
    if (response && response.url) {
      return {
        source: 'legacy',
        snapshot: response,
        url: response.url,
        title: response.title || '',
      };
    }
  } catch (_e) {
    // Content script not available at all.
  }

  // Attempt 3: Minimal executeScript fallback (Bug #15).
  // This runs on tabs where the content script was never injected -- e.g. tabs
  // opened before the extension was installed. It does NOT inject the 100-line
  // DOM scraper from the old background.js (Bug #8). Just basic page info.
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        try {
          const clone = document.body.cloneNode(true);
          ['script', 'style', 'noscript', 'nav', 'footer', 'aside', 'iframe']
            .forEach((t) => clone.querySelectorAll(t).forEach((el) => el.remove()));
          const bodyText = (clone.innerText || clone.textContent || '')
            .replace(/\s{3,}/g, '\n\n')
            .replace(/[ \t]{2,}/g, ' ')
            .trim()
            .slice(0, 2000);

          return {
            url: window.location.href,
            title: document.title || '',
            bodyText: bodyText,
          };
        } catch (_e) {
          return {
            url: window.location.href,
            title: document.title || '',
            bodyText: '',
          };
        }
      },
    });

    const data = results && results[0] && results[0].result;
    if (data) {
      return {
        source: 'fallback',
        snapshot: data,
        url: data.url || '',
        title: data.title || '',
      };
    }
  } catch (_e) {
    // Tab is not scriptable (chrome://, PDF, etc.). Expected.
  }

  return null;
}

/**
 * Take a snapshot from the current tracked tab and push it to the side panel.
 * This is the common path for all automatic context updates.
 */
async function pushSnapshotToPanel(tabId) {
  if (!tabId || !sidePanelPort) return;

  const result = await getSnapshotFromTab(tabId);
  if (result) {
    pushToPanel({
      type: 'page_snapshot',
      snapshot: result.snapshot,
      url: result.url,
      title: result.title,
      source: result.source,
    });
  } else {
    pushToPanel({
      type: 'page_snapshot',
      snapshot: null,
      url: '',
      title: '',
      source: 'none',
    });
  }
}

// -- Tab Tracking -------------------------------------------------------------
// Track the user's active browsing tab. Updated by onActivated (tab switch) and
// onUpdated (page load complete). The side panel and extension pages are filtered
// out so lastWebTabId always points to a real web page.

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  if (await isWebTab(tabId)) {
    persistTabId(tabId);
    // Push snapshot to panel -- no screenshot (Bug #14 fix).
    await pushSnapshotToPanel(tabId);
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== 'complete') return;
  if (tabId === lastWebTabId) {
    await pushSnapshotToPanel(tabId);
  }
});

// -- SPA Navigation Detection (Bug #21 fix) -----------------------------------
// Detect client-side navigation in SPAs (React Router, Next.js, etc.) via the
// webNavigation API. This replaces the old pushState monkey-patch approach which
// required a content script and broke on tabs opened before install.
if (chrome.webNavigation && chrome.webNavigation.onHistoryStateUpdated) {
  chrome.webNavigation.onHistoryStateUpdated.addListener(async (details) => {
    // Only care about main frame, and only for our tracked tab.
    if (details.frameId !== 0) return;
    if (details.tabId !== lastWebTabId) return;

    // Small delay to let the SPA render settle before snapshotting.
    await new Promise((r) => setTimeout(r, 500));
    await pushSnapshotToPanel(details.tabId);
  });
}

// -- Side Panel Port Connection -----------------------------------------------
// The side panel connects via chrome.runtime.connect with port name 'sidepanel'.
// We push the current page snapshot immediately on connect so the panel has
// context right away.

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== 'sidepanel') return;

  sidePanelPort = port;

  port.onDisconnect.addListener(() => {
    sidePanelPort = null;
  });

  // Push current page context immediately when panel opens.
  if (lastWebTabId) {
    pushSnapshotToPanel(lastWebTabId);
  } else {
    // No tracked tab yet -- query for the active tab as fallback.
    chrome.tabs.query({ active: true, lastFocusedWindow: true }).then(([tab]) => {
      if (tab && tab.id && tab.url && !tab.url.startsWith('chrome://') && !tab.url.startsWith('chrome-extension://')) {
        persistTabId(tab.id);
        pushSnapshotToPanel(tab.id);
      }
    }).catch(() => {});
  }
});

// -- Message Handling ---------------------------------------------------------
// Messages arrive from the side panel via chrome.runtime.sendMessage (not the port).
// Each message has a `type` field that determines the action.

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  // -- get_active_tab_id: side panel asks which tab we are tracking -----------
  if (msg.type === 'get_active_tab_id') {
    sendResponse({ tabId: lastWebTabId });
    return true;
  }

  // -- request_page_snapshot: side panel wants a fresh snapshot ----------------
  if (msg.type === 'request_page_snapshot' || msg.type === 'request_page_context') {
    if (lastWebTabId) {
      pushSnapshotToPanel(lastWebTabId);
    }
    return false;
  }

  // -- execute_action: route a single action to the content script ------------
  // The side panel sends { type: 'execute_action', action: { type: 'click', ref: '@e4' } }.
  // We forward to the content script and push the ActionResult back to the panel.
  if (msg.type === 'execute_action') {
    (async () => {
      const tabId = await resolveTabId();
      if (!tabId) {
        pushToPanel({ type: 'command_result', ok: false, detail: 'No active web tab' });
        return;
      }

      try {
        const result = await chrome.tabs.sendMessage(tabId, {
          type: 'execute_action',
          action: msg.action,
        });
        pushToPanel({
          type: 'command_result',
          ok: result ? result.ok : false,
          detail: result ? result.detail : 'No response from content script',
          snapshot: result ? result.snapshot : null,
          changes: result ? result.changes : [],
          action: msg.action.type,
          ref: msg.action.ref || msg.action.selector,
        });
      } catch (e) {
        pushToPanel({
          type: 'command_result',
          ok: false,
          detail: 'Content script not available: ' + (e.message || ''),
          action: msg.action.type,
          ref: msg.action.ref || msg.action.selector,
        });
      }
    })();
    return false;
  }

  // -- execute_commands: legacy batch command execution (backward compat) ------
  // The old side panel sends { type: 'execute_commands', commands: [...] }.
  // Route DOM commands to content script, handle navigation commands here.
  if (msg.type === 'execute_commands') {
    (async () => {
      let tabId = await resolveTabId();
      const results = [];

      for (const cmd of (msg.commands || [])) {
        try {
          if (cmd.type === 'navigate' && cmd.url) {
            // Navigate in the current tab and wait for load (Bug #4 fix).
            if (tabId) {
              await chrome.tabs.update(tabId, { url: cmd.url });
              pushToPanel({ type: 'navigating', url: cmd.url });
              const loaded = await waitForPageLoad(tabId, 15000);
              if (loaded) {
                await pushSnapshotToPanel(tabId);
              }
              results.push({ type: 'navigate', ok: true, detail: loaded ? 'Loaded' : 'Timed out' });
            } else {
              results.push({ type: 'navigate', ok: false, detail: 'No tab' });
            }

          } else if (cmd.type === 'open_tab' && cmd.url) {
            // Open a new tab, track it, and wait for load.
            const newTab = await chrome.tabs.create({ url: cmd.url });
            if (newTab && newTab.id) {
              persistTabId(newTab.id);
              tabId = newTab.id;
              pushToPanel({ type: 'navigating', url: cmd.url });
              const loaded = await waitForPageLoad(newTab.id, 15000);
              if (loaded) {
                await pushSnapshotToPanel(newTab.id);
              }
              results.push({ type: 'open_tab', ok: true, detail: loaded ? 'Loaded' : 'Timed out' });
            }

          } else if (cmd.type === 'wait') {
            const ms = Math.min(cmd.ms || 2000, 10000);
            await new Promise((r) => setTimeout(r, ms));
            results.push({ type: 'wait', ok: true });

          } else if (tabId) {
            // DOM commands -- send to content script.
            // Try new protocol first (execute_action), fall back to legacy (execute_commands).
            try {
              const result = await chrome.tabs.sendMessage(tabId, {
                type: 'execute_action',
                action: cmd,
              });
              results.push({
                type: cmd.type,
                ok: result ? result.ok : false,
                detail: result ? result.detail : 'No response',
              });
            } catch (_e) {
              // Content script does not handle execute_action -- try legacy batch.
              try {
                const legacyResult = await chrome.tabs.sendMessage(tabId, {
                  type: 'execute_commands',
                  commands: [cmd],
                });
                const cmdResult = legacyResult && legacyResult.results && legacyResult.results[0];
                results.push({
                  type: cmd.type,
                  ok: cmdResult ? cmdResult.ok : false,
                  detail: cmdResult ? cmdResult.error : 'Unknown',
                });
              } catch (e2) {
                results.push({
                  type: cmd.type,
                  ok: false,
                  detail: 'Content script unavailable: ' + (e2.message || ''),
                });
              }
            }
          } else {
            results.push({ type: cmd.type, ok: false, detail: 'No active tab' });
          }
        } catch (e) {
          results.push({ type: cmd.type, ok: false, detail: e.message || 'Unknown error' });
        }
      }

      // Push combined results to panel.
      pushToPanel({ type: 'command_results', results: results });
    })();
    return false;
  }

  // -- navigate: single navigation request ------------------------------------
  if (msg.type === 'navigate' && msg.url) {
    (async () => {
      const tabId = await resolveTabId();
      if (!tabId) {
        pushToPanel({ type: 'command_result', ok: false, detail: 'No active web tab' });
        return;
      }

      pushToPanel({ type: 'navigating', url: msg.url });

      try {
        await chrome.tabs.update(tabId, { url: msg.url });
        const loaded = await waitForPageLoad(tabId, 15000);
        if (loaded) {
          // Small delay for any post-load JS to settle.
          await new Promise((r) => setTimeout(r, 300));
          await pushSnapshotToPanel(tabId);
        } else {
          // Timed out, still push whatever we have.
          await pushSnapshotToPanel(tabId);
        }
      } catch (e) {
        pushToPanel({
          type: 'command_result',
          ok: false,
          detail: 'Navigation failed: ' + (e.message || ''),
        });
      }
    })();
    return false;
  }

  // -- open_tab: open URL in a new tab ----------------------------------------
  if (msg.type === 'open_tab' && msg.url) {
    (async () => {
      try {
        const tab = await chrome.tabs.create({ url: msg.url });
        if (tab && tab.id) {
          persistTabId(tab.id);
          pushToPanel({ type: 'navigating', url: msg.url });
          const loaded = await waitForPageLoad(tab.id, 15000);
          await new Promise((r) => setTimeout(r, 300));
          await pushSnapshotToPanel(tab.id);
        }
      } catch (e) {
        pushToPanel({
          type: 'command_result',
          ok: false,
          detail: 'Open tab failed: ' + (e.message || ''),
        });
      }
    })();
    return false;
  }

  // -- capture_screenshot: explicit screenshot request (Bug #14) --------------
  // Only captures when explicitly asked, never automatically on tab switch.
  if (msg.type === 'capture_screenshot') {
    (async () => {
      try {
        const dataUrl = await chrome.tabs.captureVisibleTab({ format: 'jpeg', quality: 50 });
        pushToPanel({ type: 'screenshot', data: dataUrl });
      } catch (e) {
        pushToPanel({ type: 'screenshot', data: null, error: e.message || 'Capture failed' });
      }
    })();
    return false;
  }

  // -- read_full_page: request full page text from content script -------------
  if (msg.type === 'read_full_page') {
    (async () => {
      const tabId = lastWebTabId;
      if (!tabId) {
        pushToPanel({ type: 'full_page_text', text: '' });
        return;
      }

      try {
        const response = await chrome.tabs.sendMessage(tabId, { type: 'get_full_text' });
        pushToPanel({ type: 'full_page_text', text: (response && response.text) || '' });
      } catch (_e) {
        // Fallback: executeScript to grab text directly.
        try {
          const results = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => {
              const clone = document.body.cloneNode(true);
              ['script', 'style', 'noscript'].forEach((t) =>
                clone.querySelectorAll(t).forEach((el) => el.remove())
              );
              return (clone.innerText || '').replace(/\s{3,}/g, '\n\n').trim().slice(0, 15000);
            },
          });
          const text = results && results[0] && results[0].result;
          pushToPanel({ type: 'full_page_text', text: text || '' });
        } catch (_e2) {
          pushToPanel({ type: 'full_page_text', text: '' });
        }
      }
    })();
    return false;
  }

  // -- action_recorded: content script reporting a user action -----------------
  if (msg.type === 'action_recorded') {
    pushToPanel({ type: 'action_recorded', action: msg.action });
    return false;
  }

  // -- full_page_text: content script pushing read_page result (legacy) --------
  if (msg.type === 'full_page_text') {
    pushToPanel({ type: 'full_page_text', text: msg.text || '' });
    return false;
  }

  return false;
});

// -- Resolve Tab ID -----------------------------------------------------------
// Common helper to get a usable tab ID. Uses lastWebTabId if available,
// otherwise queries for the active tab in the last focused window.

async function resolveTabId() {
  if (lastWebTabId && (await isWebTab(lastWebTabId))) {
    return lastWebTabId;
  }

  // Fallback: query for the current active tab.
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab && tab.url && !tab.url.startsWith('chrome://') && !tab.url.startsWith('chrome-extension://')) {
      persistTabId(tab.id);
      return tab.id;
    }
  } catch (_e) {
    // No tabs available.
  }

  return null;
}
