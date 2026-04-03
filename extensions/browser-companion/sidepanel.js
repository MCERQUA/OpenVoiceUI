/**
 * JamBot Browser Companion -- Side Panel
 *
 * Condensed OpenVoiceUI for the browser sidebar.
 * - Vanilla JS, no dependencies, no build step
 * - STT ported from OpenVoiceUI/src/providers/WebSpeechSTT.js
 * - HTTP POST streaming (NDJSON) to /api/conversation?stream=1
 * - Command parsing via JamBotCommandParser (lib/command-parser.js)
 * - Semantic snapshot context (compact @e1 refs instead of raw DOM)
 * - Individual command routing through background.js with result feedback
 *
 * Phase 1-3 bug fixes applied:
 *   #2  Command result feedback from background
 *   #3  Context explosion -- semantic snapshots (~500 tokens vs 5000+)
 *   #5  Retry logic with exponential backoff
 *   #6  Per-chunk 30s streaming timeout, partial text preservation
 *   #9  NDJSON parse error logging with line preview
 *   #10 Stall detection: threshold 8, time-based, ignores auto-scroll
 *   #11 Scroll completion via command_result scrolledBy/atBottom
 *   #12 Auth retry cooldown (10s minimum between retries)
 *   #13 sendMessage reentrancy -- save partial text before abort
 *   #16 Waveform speaking detection via this._isSpeaking flag
 *   #17 Waveform RAF cleanup via stopWaveform()
 *   #18 Unified this.ttsEnabled (no this._ttsEnabled)
 *   #22 chrome.storage.local everywhere (not sync)
 */

// Voices per TTS provider
const VOICE_OPTIONS = {
  groq: [
    { value: 'autumn',  label: 'Autumn (F)' },
    { value: 'diana',   label: 'Diana (F)' },
    { value: 'hannah',  label: 'Hannah (F)' },
    { value: 'troy',    label: 'Troy (M)' },
    { value: 'austin',  label: 'Austin (M)' },
    { value: 'daniel',  label: 'Daniel (M)' },
  ],
  supertonic: [
    { value: 'F1', label: 'F1 (Female)' },
    { value: 'F2', label: 'F2 (Female)' },
    { value: 'F3', label: 'F3 (Female)' },
    { value: 'F4', label: 'F4 (Female)' },
    { value: 'F5', label: 'F5 (Female)' },
    { value: 'M1', label: 'M1 (Male)' },
    { value: 'M2', label: 'M2 (Male)' },
    { value: 'M3', label: 'M3 (Male)' },
    { value: 'M4', label: 'M4 (Male)' },
    { value: 'M5', label: 'M5 (Male)' },
  ],
  browser: [
    { value: 'default', label: 'System default' },
  ],
};

const DEFAULT_VOICE = { groq: 'autumn', supertonic: 'F1', browser: 'default' };

class JamBotPanel {
  constructor() {
    this.domain = null;
    this.ttsVoice = 'autumn';
    this.ttsProvider = 'groq';
    this.ttsEnabled = true;        // Bug #18: single source of truth
    this.recordingEnabled = true;

    // STT state (mirrors WebSpeechSTT.js structure)
    this.stt = {
      recognition: null,
      SpeechRecognition: window.SpeechRecognition || window.webkitSpeechRecognition || null,
      isListening: false,
      isProcessing: false,
      accumulatedText: '',
      silenceTimer: null,
      _micStream: null,
    };

    // Streaming fetch abort controller
    this.abortController = null;

    // Page context -- semantic snapshot from content script (via background)
    // Bug #3: compact snapshot replaces raw DOM + CSS selectors
    this.currentSnapshot = null;   // serialized semantic tree string
    this.currentUrl = null;
    this.currentTitle = null;

    // Inline action history (pushed from background via action_recorded)
    this.localActionHistory = [];

    // Background port for push messages
    this.bgPort = null;

    // Track URL changes for navigation notices
    this._lastKnownUrl = null;

    // Waveform state -- Bug #16, #17
    this._isSpeaking = false;
    this._waveAnimId = null;

    // Auth retry cooldown -- Bug #12
    this._lastAuthRetryAt = 0;

    // Task loop state
    this._taskActive = false;
    this._taskStopped = false;
    this._taskStart = 0;
    this._taskMaxMs = 0;
    this._taskText = '';
    this._taskStep = 0;
    this._taskNoCommandCount = 0;
    this._taskLastCommandAt = 0;     // Bug #10: time-based stall detection
    this._taskPageSnapshots = [];
    this._taskAgentNotes = [];
    this._taskNavigating = false;
    this._selfScrolling = false;
    this._commentedPosts = new Set();
    this._lastUserMessage = '';

    // Active audio handle for stop-on-mute
    this._activeAudio = null;

    this.init();
  }

  // -- Initialization -----------------------------------------------------------

  async init() {
    // Bug #22: chrome.storage.local everywhere
    const prefs = await chrome.storage.local.get([
      'domain', 'ttsProvider', 'ttsVoice', 'ttsEnabled', 'recordingEnabled',
    ]);

    if (!prefs.domain) {
      this.showSetupScreen();
      return;
    }

    this.domain = prefs.domain;
    this.ttsProvider = prefs.ttsProvider || 'groq';
    this.ttsVoice = prefs.ttsVoice || 'autumn';
    this.ttsEnabled = prefs.ttsEnabled !== false;
    this.recordingEnabled = prefs.recordingEnabled !== false;

    this.bindUI();
    this.connectBackground();
    this.testConnection();
    this.startWaveform();

    // If domain changes in settings popup, reload panel
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.domain) window.location.reload();
    });
  }

  // -- UI Setup -----------------------------------------------------------------

  showSetupScreen() {
    document.getElementById('setup-screen').style.display = 'flex';
    document.getElementById('main-screen').style.display = 'none';
  }

  bindUI() {
    document.getElementById('setup-screen').style.display = 'none';
    document.getElementById('main-screen').style.display = 'flex';

    // Cache element references
    this.$micBtn    = document.getElementById('mic-btn');
    this.$statusDot = document.getElementById('status-dot');
    this.$messages  = document.getElementById('messages');
    this.$wave      = document.getElementById('wave-canvas');
    this.$waveCtx   = this.$wave.getContext('2d');
    this.$pill      = document.getElementById('context-pill');
    this.$pillText  = document.getElementById('pill-text');
    this.$pillThumb = document.getElementById('pill-thumb');
    this.$textInput = document.getElementById('text-input');
    this.$micLabel  = document.getElementById('mic-label');
    this.$errBanner = document.getElementById('error-banner');

    document.getElementById('domain-display').textContent = this.domain;

    // Mic button
    this.$micBtn.addEventListener('click', () => this.toggleListening());

    // Text input
    document.getElementById('send-btn').addEventListener('click', () => this.sendTextMessage());
    this.$textInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendTextMessage();
      }
    });

    // Settings drawer
    this.$settingsDrawer   = document.getElementById('settings-drawer');
    this.$panelTtsProvider = document.getElementById('panel-tts-provider');
    this.$panelVoice       = document.getElementById('panel-voice');

    // Seed drawer with current provider and voice list
    if (this.$panelTtsProvider) this.$panelTtsProvider.value = this.ttsProvider;
    this._updateVoiceOptions(this.ttsProvider, false);
    if (this.$panelVoice && this.ttsVoice) {
      const voices = VOICE_OPTIONS[this.ttsProvider] || VOICE_OPTIONS.groq;
      if (voices.some(v => v.value === this.ttsVoice)) {
        this.$panelVoice.value = this.ttsVoice;
      }
    }

    document.getElementById('settings-btn').addEventListener('click', () => {
      const open = this.$settingsDrawer.style.display !== 'none';
      this.$settingsDrawer.style.display = open ? 'none' : 'flex';
    });

    // TTS provider change -- rebuild voice list
    this.$panelTtsProvider?.addEventListener('change', () => {
      this.ttsProvider = this.$panelTtsProvider.value;
      chrome.storage.local.set({ ttsProvider: this.ttsProvider });
      this._updateVoiceOptions(this.ttsProvider, false);
    });

    // Voice change
    this.$panelVoice?.addEventListener('change', () => {
      this.ttsVoice = this.$panelVoice.value;
      chrome.storage.local.set({ ttsVoice: this.ttsVoice });
    });

    // Mute FAB -- next to mic button, always visible
    this.$muteBtn = document.getElementById('mute-btn');
    this._syncMuteUI();
    this.$muteBtn?.addEventListener('click', () => this._toggleMute());

    // "More settings" link -> full popup
    document.getElementById('full-settings-link')?.addEventListener('click', () => {
      chrome.tabs.create({ url: chrome.runtime.getURL('popup.html') });
    });
  }

  // -- Mute Controls ------------------------------------------------------------

  _toggleMute() {
    // Bug #18: single this.ttsEnabled -- no this._ttsEnabled
    this.ttsEnabled = !this.ttsEnabled;
    chrome.storage.local.set({ ttsEnabled: this.ttsEnabled });
    this._syncMuteUI();
    // If muting while audio is playing, stop it immediately
    if (!this.ttsEnabled) {
      this.stopActiveAudio();
      this.setMicState(this.stt.isListening ? 'listening' : 'idle');
    }
  }

  _updateVoiceOptions(provider, keepCurrent = false) {
    if (!this.$panelVoice) return;
    const voices = VOICE_OPTIONS[provider] || VOICE_OPTIONS.groq;
    const current = keepCurrent ? this.$panelVoice.value : null;
    this.$panelVoice.innerHTML = '';
    for (const { value, label } of voices) {
      const opt = document.createElement('option');
      opt.value = value;
      opt.textContent = label;
      this.$panelVoice.appendChild(opt);
    }
    const valid = voices.some(v => v.value === current);
    this.$panelVoice.value = (keepCurrent && valid)
      ? current
      : (DEFAULT_VOICE[provider] || voices[0].value);
    this.ttsVoice = this.$panelVoice.value;
    chrome.storage.local.set({ ttsVoice: this.ttsVoice });
  }

  _syncMuteUI() {
    const on = this.ttsEnabled !== false;
    if (this.$muteBtn) {
      this.$muteBtn.textContent = on ? '🔊' : '🔇';
      this.$muteBtn.title       = on ? 'Voice on -- tap to mute' : 'Muted -- tap to unmute';
      this.$muteBtn.className   = on ? 'mute-fab' : 'mute-fab mute-fab--muted';
    }
  }

  // -- Background Connection ----------------------------------------------------

  connectBackground() {
    this.bgPort = chrome.runtime.connect({ name: 'sidepanel' });

    this.bgPort.onMessage.addListener((msg) => {
      // New protocol: semantic snapshot from background
      if (msg.type === 'page_snapshot') {
        const prevUrl = this._lastKnownUrl;
        const newUrl = msg.url || null;

        // Update snapshot state -- Bug #3: compact semantic tree
        if (msg.snapshot != null) {
          if (typeof msg.snapshot === 'string') {
            this.currentSnapshot = msg.snapshot;
          } else if (typeof msg.snapshot === 'object') {
            // Legacy or fallback format -- store as-is for buildUIContext
            this.currentSnapshot = msg.snapshot;
          }
        }
        if (msg.url) this.currentUrl = msg.url;
        if (msg.title) this.currentTitle = msg.title;

        const isFirst = prevUrl === null && !!newUrl;
        const urlChanged = !isFirst && newUrl && newUrl !== prevUrl;
        if (newUrl) this._lastKnownUrl = newUrl;

        this.updateContextPill(isFirst, urlChanged);

        // Resume task loop if navigation was pending
        if (this._taskNavigating && this._taskActive && !this._taskStopped) {
          this._taskNavigating = false;
          setTimeout(() => this._continueTask(), 500);
        }
      }

      // Navigation notice
      if (msg.type === 'navigating') {
        this.addSystemMsg('Navigating to ' + msg.url);
        this._taskNavigating = true;
      }

      // Bug #2: Process individual command results from background
      if (msg.type === 'command_result') {
        const label = msg.action || 'command';
        const ref = msg.ref || '';
        if (msg.ok) {
          console.log('[JamBot] Command OK:', label, ref, msg.detail || '');
        } else {
          console.warn('[JamBot] Command failed:', label, ref, msg.detail || '');
          this.addSystemMsg('Action failed: ' + label + (ref ? ' ' + ref : '') +
            (msg.detail ? ' -- ' + msg.detail : ''));
        }
        // Update snapshot if the command returned a fresh one
        if (msg.snapshot) {
          if (typeof msg.snapshot === 'string') {
            this.currentSnapshot = msg.snapshot;
          } else if (typeof msg.snapshot === 'object') {
            this.currentSnapshot = msg.snapshot;
          }
        }
        // Bug #11: Scroll completion data
        if (msg.action === 'scroll' && msg.ok) {
          if (msg.changes) {
            const scrollInfo = msg.changes.find(c => c.scrolledBy !== undefined);
            if (scrollInfo && scrollInfo.atBottom) {
              console.log('[JamBot] Reached bottom of page');
            }
          }
        }
        // READ_PAGE result: store the full text and auto-feed it back to the agent
        if (msg.action === 'read_page' && msg.ok && msg.text) {
          this._lastReadPageText = msg.text;
          console.log('[JamBot] read_page captured ' + msg.text.length + ' chars, auto-feeding to agent');
          // If task is active, the loop will pick it up. If not, send it now.
          if (!this._taskActive) {
            this.sendMessage(
              '[Page content read (' + msg.text.length + ' chars)]\n\n' +
              msg.text.slice(0, 12000)
            );
          }
        }
      }

      // Legacy batch command results
      if (msg.type === 'command_results') {
        for (const r of (msg.results || [])) {
          if (!r.ok) {
            console.warn('[JamBot] Batch command failed:', r.type, r.detail || '');
          }
        }
      }

      // User action recorded by content script
      if (msg.type === 'action_recorded') {
        this.localActionHistory.push(msg.action);
        if (this.localActionHistory.length > 20) this.localActionHistory.shift();
      }

      // Full page text injection
      if (msg.type === 'full_page_text' && msg.text) {
        this.addSystemMsg('Full page read: ' + msg.text.length + ' chars');
      }
    });

    this.bgPort.onDisconnect.addListener(() => {
      // Background service worker restarted -- reconnect after a short delay
      setTimeout(() => this.connectBackground(), 1000);
    });

    // Request current page snapshot
    chrome.runtime.sendMessage({ type: 'request_page_snapshot' }).catch(() => {});
  }

  updateContextPill(isFirst = false, urlChanged = false) {
    if (!this.$pill) return;

    const title = this.currentTitle;
    const url = this.currentUrl;

    if (!title && !url) {
      this.$pill.style.display = 'none';
      return;
    }

    const label = title || url;
    this.$pill.style.display = 'flex';
    this.$pillText.textContent = label;

    // Thumbnail is not used with semantic snapshots (Bug #14: no auto-screenshot)
    if (this.$pillThumb) this.$pillThumb.style.display = 'none';

    if (isFirst && (title || url)) {
      this.showPageGreeting({ title, url });
    } else if (urlChanged && (title || url)) {
      this.showNavigationNotice({ title, url });
    }
  }

  showPageGreeting(ctx) {
    document.getElementById('page-greeting')?.remove();
    const el = document.createElement('div');
    el.id = 'page-greeting';
    el.className = 'message message-assistant';
    const titleText = this.escHtml(ctx.title || ctx.url || 'this page');
    const domain = ctx.url ? (() => {
      try { return new URL(ctx.url).hostname; } catch { return ''; }
    })() : '';
    const domainNote = domain ? ' <span class="msg-domain">(' + this.escHtml(domain) + ')</span>' : '';
    el.innerHTML = 'I can see you\'re on <strong>' + titleText + '</strong>' + domainNote +
      '. Tap the mic or type to chat about it.';
    this.$messages.appendChild(el);
    this.$messages.scrollTop = this.$messages.scrollHeight;
  }

  showNavigationNotice(ctx) {
    const el = document.createElement('div');
    el.className = 'message message-system';
    el.textContent = 'Navigated to: ' + (ctx.title || ctx.url);
    this.$messages.appendChild(el);
    this.$messages.scrollTop = this.$messages.scrollHeight;
  }

  escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // -- Auth ---------------------------------------------------------------------

  async getAuthHeaders() {
    try {
      const cookie = await chrome.cookies.get({
        url: 'https://' + this.domain,
        name: '__session',
      });
      if (cookie?.value) {
        return { 'Authorization': 'Bearer ' + cookie.value };
      }
    } catch (_) {}
    return {};
  }

  async testConnection() {
    try {
      const r = await this._fetchWithRetry(
        'https://' + this.domain + '/health/live',
        { signal: AbortSignal.timeout(5000) },
        1  // single retry for health check
      );
      this.setStatus(r.ok ? 'active' : 'error');
    } catch {
      this.setStatus('error');
    }
  }

  setStatus(state) {
    this.$statusDot.className = 'status-dot status-dot--' + state;
  }

  // -- Fetch with Retry (Bug #5) ------------------------------------------------

  async _fetchWithRetry(url, options, maxRetries = 2) {
    let lastError;
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const resp = await fetch(url, options);
        return resp;
      } catch (e) {
        lastError = e;
        // Never retry on intentional abort
        if (e.name === 'AbortError') throw e;
        if (attempt < maxRetries) {
          // Exponential backoff: 1s, 2s
          const delay = Math.pow(2, attempt) * 1000;
          console.warn('[JamBot] Fetch attempt ' + (attempt + 1) + ' failed, retrying in ' + delay + 'ms:', e.message);
          await new Promise(r => setTimeout(r, delay));
        }
      }
    }
    throw lastError;
  }

  // -- STT (ported from WebSpeechSTT.js) ----------------------------------------

  _ensureRecognition() {
    if (this.stt.recognition) return true;
    if (!this.stt.SpeechRecognition) return false;

    const r = new this.stt.SpeechRecognition();
    r.continuous = true;
    r.interimResults = true;
    r.lang = 'en-US';
    r.maxAlternatives = 1;

    r.onresult = (event) => {
      if (this.stt.isProcessing) return;

      // Any result resets the silence timer
      if (this.stt.silenceTimer) {
        clearTimeout(this.stt.silenceTimer);
        this.stt.silenceTimer = null;
      }

      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) final += event.results[i][0].transcript;
      }

      if (final.trim()) {
        this.stt.accumulatedText = this.stt.accumulatedText
          ? this.stt.accumulatedText + ' ' + final.trim()
          : final.trim();
        this.showInterim(this.stt.accumulatedText);
      }

      if (this.stt.accumulatedText) {
        this.stt.silenceTimer = setTimeout(() => {
          const text = this.stt.accumulatedText.trim();
          const meaningful = text.replace(/[^a-zA-Z0-9]/g, '');
          if (text && meaningful.length >= 2 && !this.stt.isProcessing) {
            this.stt.isProcessing = true;
            this.clearInterim();
            this.sendMessage(text);
            this.stt.accumulatedText = '';
          } else {
            this.stt.accumulatedText = '';
            this.clearInterim();
          }
        }, 3500);
      }
    };

    r.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      console.error('STT error:', e.error);
      this.setMicState('error');
    };

    r.onend = () => {
      // Auto-restart while listening (Chrome aborts every ~30s normally)
      if (this.stt.isListening && !this.stt.isProcessing) {
        setTimeout(() => {
          if (this.stt.isListening && !this.stt.isProcessing) {
            try { this.stt.recognition.start(); } catch (_) {}
          }
        }, 300);
      }
    };

    this.stt.recognition = r;
    return true;
  }

  async toggleListening() {
    if (this.stt.isListening) {
      this.stopListening();
    } else {
      await this.startListening();
    }
  }

  async startListening() {
    if (!this._ensureRecognition()) {
      this.showError('Speech recognition not supported in this browser.');
      return;
    }
    // Acquire mic stream (keeps permission alive)
    if (!this.stt._micStream) {
      try {
        const perm = await navigator.permissions.query({ name: 'microphone' });
        if (perm.state === 'denied') {
          this.showError('Mic blocked. Go to chrome://settings/content/microphone, remove this extension from the blocked list, then try again.');
          return;
        }
      } catch (_) {}

      try {
        this.stt._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        if (e.name === 'NotAllowedError') {
          this.showError('Mic access denied. Go to chrome://settings/content/microphone and allow this extension, then try again.');
        } else if (e.name === 'NotFoundError') {
          this.showError('No microphone found. Connect a mic and try again.');
        } else {
          this.showError('Mic error: ' + e.message);
        }
        return;
      }
    }
    this.stt.isListening = true;
    try { this.stt.recognition.start(); } catch (_) {}
    this.setMicState('listening');
  }

  stopListening() {
    if (this.stt.silenceTimer) { clearTimeout(this.stt.silenceTimer); this.stt.silenceTimer = null; }
    this.stt.isListening = false;
    this.stt.isProcessing = false;
    this.stt.accumulatedText = '';
    if (this.stt.recognition) { try { this.stt.recognition.stop(); } catch (_) {} }
    if (this.stt._micStream) {
      this.stt._micStream.getTracks().forEach((t) => t.stop());
      this.stt._micStream = null;
    }
    this.clearInterim();
    this.setMicState('idle');
  }

  muteMic() {
    // Called when TTS audio starts -- abort STT to prevent echo capture
    this.stt.isProcessing = true;
    if (this.stt.silenceTimer) { clearTimeout(this.stt.silenceTimer); this.stt.silenceTimer = null; }
    this.stt.accumulatedText = '';
    if (this.stt.recognition) { try { this.stt.recognition.abort(); } catch (_) {} }
  }

  resumeMic() {
    // Called after TTS audio finishes
    this.stt.isProcessing = false;
    this.stt.accumulatedText = '';
    if (this.stt.isListening) {
      try { this.stt.recognition.start(); } catch (_) {}
    }
    this.setMicState(this.stt.isListening ? 'listening' : 'idle');
  }

  // -- Direct Snapshot Fetch ----------------------------------------------------
  // Fetches a fresh page snapshot synchronously by injecting executeScript into
  // the active tab. This is critical: the port-based push (request_page_snapshot)
  // is async and may not arrive in time. This method AWAITS the result.

  async _fetchSnapshotNow() {
    // Direct executeScript into the active tab — always works, no content script needed.
    // Returns raw bodyText (15000 chars) + interactive elements with CSS selectors.
    // This is the v1 approach that agents actually understand.
    try {
      const { tabId } = await chrome.runtime.sendMessage({ type: 'get_active_tab_id' });
      if (!tabId) {
        console.warn('[JamBot] No tracked web tab -- cannot fetch snapshot');
        return;
      }

      const results = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          try {
            const clone = document.body.cloneNode(true);
            ['script', 'style', 'noscript', 'iframe']
              .forEach(t => clone.querySelectorAll(t).forEach(el => el.remove()));
            const bodyText = (clone.innerText || clone.textContent || '')
              .replace(/\s{3,}/g, '\n\n')
              .replace(/[ \t]{2,}/g, ' ')
              .trim()
              .slice(0, 15000);

            // Interactive elements with CSS selectors the agent can target
            const interactive = [];
            const seen = new Set();
            function buildSel(el) {
              if (el.id) return '#' + el.id;
              const aria = el.getAttribute('aria-label');
              if (aria) return '[aria-label="' + aria.slice(0, 60).replace(/"/g, "'") + '"]';
              const testId = el.getAttribute('data-testid');
              if (testId) return '[data-testid="' + testId + '"]';
              const ph = el.placeholder || el.getAttribute('aria-placeholder');
              if (ph) return '[placeholder="' + ph.slice(0, 40).replace(/"/g, "'") + '"]';
              const name = el.getAttribute('name');
              if (name) return el.tagName.toLowerCase() + '[name="' + name + '"]';
              return el.tagName.toLowerCase();
            }

            // Inputs + contenteditable
            const inputEls = [
              ...document.querySelectorAll('input:not([type="hidden"]):not([type="submit"]):not([type="button"])'),
              ...document.querySelectorAll('textarea'),
              ...document.querySelectorAll('[contenteditable="true"]'),
            ];
            for (const el of inputEls) {
              const r = el.getBoundingClientRect();
              if (r.width === 0 && r.height === 0) continue;
              const sel = buildSel(el);
              if (seen.has(sel)) continue;
              seen.add(sel);
              const hint = (el.placeholder || el.getAttribute('aria-label') || el.name || '').slice(0, 50);
              const isEditable = el.contentEditable === 'true';
              interactive.push({
                t: isEditable ? 'textarea' : (el.tagName === 'TEXTAREA' ? 'textarea' : 'input[' + (el.type || 'text') + ']'),
                sel, hint,
              });
            }

            // Buttons
            const allBtns = Array.from(document.querySelectorAll('button,[role="button"],div[role="button"]'))
              .filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.top < window.innerHeight * 2;
              }).slice(0, 30);
            for (const el of allBtns) {
              const text = (el.textContent?.trim() || el.getAttribute('aria-label') || '').slice(0, 50);
              if (!text) continue;
              const sel = buildSel(el);
              if (seen.has(sel)) continue;
              seen.add(sel);
              interactive.push({ t: 'button', text, sel });
            }

            // Links
            const linkEls = Array.from(document.querySelectorAll('a[href]'))
              .filter(el => {
                const r = el.getBoundingClientRect();
                const text = el.textContent?.trim();
                return r.width > 0 && r.height > 0 && r.top < window.innerHeight * 2 && text && text.length > 2 && text.length < 60;
              }).slice(0, 10);
            for (const el of linkEls) {
              const text = el.textContent?.trim().slice(0, 40);
              if (!text) continue;
              interactive.push({ t: 'link', text, href: el.href?.replace(window.location.origin, '') || '' });
            }

            return {
              url: window.location.href,
              title: document.title || '',
              description: document.querySelector('meta[name="description"]')?.content
                || document.querySelector('meta[property="og:description"]')?.content || '',
              bodyText,
              selectedText: window.getSelection()?.toString()?.trim() || '',
              interactive,
            };
          } catch (e) {
            return { url: window.location.href, title: document.title || '', error: e.message };
          }
        },
      });

      const ctx = results?.[0]?.result;
      if (ctx?.url) {
        this.currentSnapshot = ctx;
        this.currentUrl = ctx.url;
        this.currentTitle = ctx.title || this.currentTitle;
        this._lastKnownUrl = ctx.url;
        this.updateContextPill(false, true);
      }
    } catch (e) {
      console.warn('[JamBot] Could not fetch snapshot:', e.message);
    }
  }

  // -- Conversation -------------------------------------------------------------

  sendTextMessage() {
    const text = this.$textInput.value.trim();
    if (!text) return;
    this.$textInput.value = '';
    this.sendMessage(text);
  }

  async sendMessage(text) {
    // Bug #13: Save partial text BEFORE aborting, so it is not lost
    if (this.abortController) {
      // If there is a streaming assistant element, preserve its content
      const existingStream = this.$messages?.querySelector('.message-assistant:last-child');
      if (existingStream && existingStream._streamText) {
        console.log('[JamBot] Preserving partial response (' +
          existingStream._streamText.length + ' chars) before abort');
      }
      this.abortController.abort();
    }
    this.abortController = new AbortController();

    // Always fetch fresh page context before sending -- critical for tab switches
    await this._fetchSnapshotNow();

    this.addMessage('user', text);
    // Track last user message for auto-task labeling (skip internal step/reminder messages)
    if (!text.startsWith('[Step ') && !text.startsWith('[REMINDER')) {
      this._lastUserMessage = text.slice(0, 80);
    }
    this.setMicState('thinking');

    const uiCtx = this.buildUIContext();
    const streamEl = this.addMessage('assistant', '');
    let streamText = '';
    streamEl._streamText = '';  // Bug #13: track partial text on the element

    // Bug #6: Per-chunk 30s timeout (not just 60s overall)
    let chunkTimer = null;
    const CHUNK_TIMEOUT_MS = 30000;
    const resetChunkTimer = () => {
      if (chunkTimer) clearTimeout(chunkTimer);
      chunkTimer = setTimeout(() => {
        console.warn('[JamBot] Stream chunk timeout (30s). Saving partial text and aborting.');
        // Preserve partial streamText before abort
        if (streamText) {
          streamEl.innerHTML = this.renderText(JamBotCommandParser.stripTags(streamText));
        }
        this.abortController?.abort();
      }, CHUNK_TIMEOUT_MS);
    };

    try {
      const authHeaders = await this.getAuthHeaders();
      if (!Object.keys(authHeaders).length) {
        streamEl.textContent = 'Not logged in. Open https://' + this.domain +
          ' in a Chrome tab and log in, then try again.';
        this.setMicState(this.stt.isListening ? 'listening' : 'idle');
        return;
      }

      // In task mode: longer responses, skip TTS on intermediate steps
      const isTaskStep = this._taskActive && this._taskStep > 0;
      const maxChars = this._taskActive ? 4000 : 1500;

      const _doFetch = async (headers) => {
        return this._fetchWithRetry(
          'https://' + this.domain + '/api/conversation?stream=1',
          {
            method: 'POST',
            signal: this.abortController.signal,
            headers: { 'Content-Type': 'application/json', ...headers },
            body: JSON.stringify({
              message: text,
              tts_provider: this.ttsProvider || 'groq',
              skip_tts: isTaskStep,
              voice: this.ttsVoice,
              ui_context: uiCtx,
              max_response_chars: maxChars,
            }),
          },
          0  // No retry on conversation POST (abort would invalidate)
        );
      };

      let resp = await _doFetch(authHeaders);

      // Bug #12: Auth retry with cooldown -- skip if we retried less than 10s ago
      if (resp.status === 401) {
        const now = Date.now();
        if (now - this._lastAuthRetryAt > 10000) {
          this._lastAuthRetryAt = now;
          console.warn('[JamBot] 401 -- retrying with fresh auth cookie');
          const freshHeaders = await this.getAuthHeaders();
          if (Object.keys(freshHeaders).length) {
            resp = await _doFetch(freshHeaders);
          }
        } else {
          console.warn('[JamBot] 401 -- skipping retry (last retry was <10s ago)');
          streamEl.textContent = 'Session expired. Open https://' + this.domain +
            ' and log in again, then come back here.';
          this.setMicState(this.stt.isListening ? 'listening' : 'idle');
          return;
        }
      }

      if (!resp.ok) {
        const body = await resp.text().catch(() => '');
        throw new Error('Server returned ' + resp.status + (body ? ': ' + body.slice(0, 120) : ''));
      }

      resetChunkTimer();

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      const audioChunks = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        resetChunkTimer();  // Bug #6: reset per-chunk timer on each read
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop(); // Keep incomplete line

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const d = JSON.parse(line);
            if (d.type === 'delta' && d.text) {
              streamText += d.text;
              streamEl._streamText = streamText;  // Bug #13: keep element in sync
              streamEl.innerHTML = this.renderText(JamBotCommandParser.stripTags(streamText));
              this.$messages.scrollTop = this.$messages.scrollHeight;
            }
            if (d.type === 'audio' && d.audio) audioChunks.push(d.audio);
          } catch (parseErr) {
            // Bug #9: NDJSON parse error logging with line preview
            const linePreview = line.length > 80 ? line.slice(0, 80) + '...' : line;
            console.warn('[JamBot] NDJSON parse error:', linePreview, parseErr.message);
          }
        }
      }

      // Clear chunk timer after stream completes
      if (chunkTimer) { clearTimeout(chunkTimer); chunkTimer = null; }

      // -- Task activation --------------------------------------------------------
      const startTaskDesc = JamBotCommandParser.extractStartTask(streamText);
      if (startTaskDesc && !this._taskActive) {
        this._activateTask(startTaskDesc);
      }

      // Auto-activate task loop for scroll commands
      if (!this._taskActive) {
        const cmds = JamBotCommandParser.parseCommands(streamText);
        if (cmds.some(c => c.type === 'scroll')) {
          this._activateTask(this._lastUserMessage || 'Browsing page');
        }
      }

      // Capture initial page snapshot for canvas creation
      if (this._taskActive && this._taskPageSnapshots?.length === 0 && this.currentSnapshot) {
        this._taskPageSnapshots.push(
          typeof this.currentSnapshot === 'string'
            ? this.currentSnapshot
            : JSON.stringify(this.currentSnapshot)
        );
      }

      // -- TASK_COMPLETE ----------------------------------------------------------
      const taskCompleteSummary = JamBotCommandParser.extractTaskComplete(streamText);
      if (taskCompleteSummary !== null) {
        this.processCommands(streamText);
        if (/canvas|page/i.test(taskCompleteSummary) || /canvas|page/i.test(this._taskText || '')) {
          await this._createCanvasFromTask(null, taskCompleteSummary);
        }
        this._stopTask('Task complete: ' + taskCompleteSummary);
      } else {
        // Execute browser commands
        const hadCommands = this.processCommands(streamText);

        // Strip [START_TASK:] -- if that is ALL the agent said, it is a stall
        const textWithoutStartTask = streamText.replace(/\[START_TASK:[^\]]*\]/gi, '').trim();
        const reallyHadCommands = hadCommands && textWithoutStartTask.length > 0;

        // Auto-activate task when agent emits action commands without explicit [START_TASK:]
        // The agent clearly intends to keep working — don't make the user follow up
        if (!this._taskActive && reallyHadCommands) {
          const cmds = JamBotCommandParser.parseCommands(streamText);
          const hasActionCmd = cmds.some(c =>
            ['click', 'fill', 'scroll', 'read_page', 'navigate', 'select', 'open_tab'].includes(c.type)
          );
          if (hasActionCmd) {
            this._activateTask(this._lastUserMessage || 'Browser action');
          }
        }

        if (this._taskActive && !this._taskStopped) {
          if (reallyHadCommands) {
            this._taskNoCommandCount = 0;
            this._taskLastCommandAt = Date.now();  // Bug #10: timestamp
            if (this._taskAgentNotes) this._taskAgentNotes.push(streamText);
            // Wait for page to settle, then continue
            setTimeout(() => this._continueTask(), 1800);
          } else {
            // Bug #10: Only count as stall if >30s since last command AND not auto-scroll
            const timeSinceLastCmd = Date.now() - (this._taskLastCommandAt || this._taskStart);
            if (timeSinceLastCmd < 30000) {
              // Too soon to count as stall -- just continue
              setTimeout(() => this._continueTask(), 1800);
            } else {
              this._taskNoCommandCount = (this._taskNoCommandCount || 0) + 1;
              // Bug #10: threshold 8 (up from 5)
              if (this._taskNoCommandCount >= 8) {
                if (/canvas|page/i.test(this._taskText || '')) {
                  await this._createCanvasFromTask(null, this._taskText);
                }
                this._stopTask('Task finished (auto-completed after agent stalled)');
              } else {
                console.log('[JamBot] Agent stalled (' + this._taskNoCommandCount + '/8) -- auto-scrolling');
                this.addSystemMsg('Auto-scrolling... (' + this._taskNoCommandCount + ')');
                this._autoScrollAndContinue();
              }
            }
          }
        }
      }

      // Play TTS audio -- skip during active task steps
      const skipAudio = this._taskActive && this._taskStep > 0;
      if (audioChunks.length > 0 && this.ttsEnabled && !skipAudio) {
        this.stopActiveAudio();
        this.muteMic();
        this.setMicState('speaking');
        for (const chunk of audioChunks) {
          if (!this.ttsEnabled) break;
          await this.playAudio(chunk);
        }
        setTimeout(() => this.resumeMic(), 500);
      } else {
        this.setMicState(this.stt.isListening ? 'listening' : 'idle');
      }

    } catch (e) {
      // Clear chunk timer on error
      if (chunkTimer) { clearTimeout(chunkTimer); chunkTimer = null; }

      if (e.name !== 'AbortError') {
        console.error('[JamBot] Conversation error:', e);
        streamEl.textContent = 'Error: ' + e.message;
        this.setMicState('error');
      } else {
        // Bug #13: On abort, preserve whatever text was streamed
        if (streamText) {
          streamEl.innerHTML = this.renderText(JamBotCommandParser.stripTags(streamText));
        }
        this.setMicState(this.stt.isListening ? 'listening' : 'idle');
      }
    } finally {
      if (chunkTimer) clearTimeout(chunkTimer);
      this.stt.isProcessing = false;
    }
  }

  // -- UI Context Builder -------------------------------------------------------

  buildUIContext() {
    // Bug #3: Send semantic snapshot instead of raw DOM text
    const ctx = { source: 'jambot_extension' };

    if (this.currentUrl) ctx.page_url = this.currentUrl;
    if (this.currentTitle) ctx.page_title = this.currentTitle;

    if (this.currentSnapshot) {
      if (typeof this.currentSnapshot === 'string') {
        // New semantic tree format -- single compact string with @e refs
        ctx.page_snapshot = this.currentSnapshot;
      } else if (typeof this.currentSnapshot === 'object') {
        // Fallback format -- send bodyText + interactive
        if (this.currentSnapshot.bodyText) ctx.page_text = this.currentSnapshot.bodyText;
        if (this.currentSnapshot.selectedText) ctx.selected_text = this.currentSnapshot.selectedText;
        if (this.currentSnapshot.description) ctx.description = this.currentSnapshot.description;
        if (this.currentSnapshot.interactive?.length) {
          ctx.interactive = this.currentSnapshot.interactive;
        }
      }
    } else if (this._lastKnownUrl) {
      ctx.page_url = this._lastKnownUrl;
    }

    // Include full page text from last read_page if available
    if (this._lastReadPageText) {
      ctx.page_text = this._lastReadPageText;
      this._lastReadPageText = null;  // consume once
    }

    if (this.recordingEnabled && this.localActionHistory.length > 0) {
      ctx.action_history = this.localActionHistory.slice(-10);
    }

    console.debug('[JamBot] ui_context:', JSON.stringify({
      url: ctx.page_url,
      title: ctx.page_title,
      hasSnapshot: !!ctx.page_snapshot,
      hasText: !!ctx.page_text,
    }));
    return ctx;
  }

  // -- Command Processing -------------------------------------------------------

  processCommands(text) {
    const commands = JamBotCommandParser.parseCommands(text);
    if (commands.length === 0) {
      const snippet = text.slice(0, 200).replace(/\n/g, ' ');
      console.log('[JamBot] No commands found in response. Preview:', snippet);
      return false;
    }

    console.log('[JamBot] Routing ' + commands.length + ' commands');

    for (const cmd of commands) {
      const classification = JamBotCommandParser.classifyCommand(cmd);

      if (classification === 'local') {
        // Handle locally in sidepanel (e.g., NOTE)
        if (cmd.type === 'note') {
          console.log('[JamBot] Note:', cmd.text);
          this.addSystemMsg('Note: ' + cmd.text);
        }
        continue;
      }

      if (classification === 'background') {
        // Background-handled commands: navigate, open_tab, wait
        chrome.runtime.sendMessage({
          type: cmd.type,
          url: cmd.url,
          ms: cmd.ms,
        }).catch((e) => {
          console.error('[JamBot] Failed to send background command:', cmd.type, e);
        });
        continue;
      }

      // Content script commands: route via execute_action for individual result feedback
      chrome.runtime.sendMessage({
        type: 'execute_action',
        action: cmd,
      }).catch((e) => {
        console.error('[JamBot] Failed to route action:', cmd.type, e);
      });
    }

    // Track commented posts when FILL fires
    const fillCmd = commands.find(c => c.type === 'fill');
    if (fillCmd && this._commentedPosts && this.currentSnapshot) {
      const snapshotText = typeof this.currentSnapshot === 'string'
        ? this.currentSnapshot
        : (this.currentSnapshot.bodyText || '');
      const bodyLines = snapshotText.split('\n');
      for (const line of bodyLines) {
        if (/looking for|need |anyone know|recommend|in need of/i.test(line) && line.length > 15) {
          this._commentedPosts.add(line.slice(0, 80));
          console.log('[JamBot] Marked as commented:', line.slice(0, 80));
        }
      }
    }

    return true;
  }

  // -- Autonomous Task Loop -----------------------------------------------------

  _activateTask(taskText, maxMinutes = 60) {
    this._taskActive         = true;
    this._taskStopped        = false;
    this._taskStart          = Date.now();
    this._taskMaxMs          = maxMinutes * 60 * 1000;
    this._taskText           = taskText;
    this._taskStep           = 0;
    this._taskNoCommandCount = 0;
    this._taskLastCommandAt  = Date.now();  // Bug #10
    this._taskPageSnapshots  = [];
    this._taskAgentNotes     = [];
    if (!this._commentedPosts) this._commentedPosts = new Set();
    this._showTaskBar(taskText, maxMinutes);
  }

  startTask(taskText, maxMinutes = 60) {
    this._activateTask(taskText, maxMinutes);
    this.sendMessage(
      'AUTONOMOUS TASK: ' + taskText + '\n\n' +
      'You are controlling a real Chrome browser. Output command tags to act on the page.\n' +
      'After each command executes, I will send you the updated page state automatically.\n' +
      'RULES:\n' +
      '- Every response MUST contain at least one command tag\n' +
      '- Do NOT describe what you will do -- just output the tag and do it\n' +
      '- Use [TASK_COMPLETE:summary] when finished (max ' + maxMinutes + ' min)\n\n' +
      'Begin now. Output your FIRST command tag.'
    );
  }

  async _continueTask() {
    if (!this._taskActive || this._taskStopped) return;
    if (Date.now() - this._taskStart > this._taskMaxMs) {
      this._stopTask('Time limit reached');
      return;
    }
    this._taskStep++;
    this._updateTaskBar();

    // Fetch fresh snapshot directly (awaited, not fire-and-forget)
    await this._fetchSnapshotNow();

    // Collect snapshot for canvas creation
    if (this.currentSnapshot && this._taskPageSnapshots) {
      const snapText = typeof this.currentSnapshot === 'string'
        ? this.currentSnapshot
        : JSON.stringify(this.currentSnapshot);
      this._taskPageSnapshots.push(snapText);
    }

    // Bug #3: After step 5, send ONLY the snapshot (not full page text)
    // The snapshot is compact (~500 tokens) and already has interactive elements with refs
    let instruction;
    let snapshotText = '';
    if (typeof this.currentSnapshot === 'string') {
      snapshotText = this.currentSnapshot;
    } else if (this.currentSnapshot && typeof this.currentSnapshot === 'object') {
      // Fallback object from executeScript — use bodyText
      snapshotText = this.currentSnapshot.bodyText || '';
    }

    if (snapshotText) {
      // Check for lead signals in the snapshot
      let leadAlert = '';
      const leadPatterns = [
        /looking for[\w\s]{0,30}(insurance|contractor|plumber|electrician|roofer|hvac|agent|quote)/i,
        /need[\w\s]{0,30}(insurance|quote|estimate|help|recommendation|coverage)/i,
        /anyone know[\w\s]{0,30}(good|reliable|affordable)/i,
        /can anyone recommend/i,
        /does anyone have[\w\s]{0,20}recommendation/i,
        /who do you (?:use|recommend)/i,
        /looking for someone to/i,
        /in need of[\w\s]{0,20}(insurance|coverage)/i,
      ];
      for (const pat of leadPatterns) {
        const m = pat.exec(snapshotText);
        if (m) {
          const start = Math.max(0, m.index - 50);
          const end = Math.min(snapshotText.length, m.index + m[0].length + 100);
          const snippet = snapshotText.slice(start, end).replace(/\s+/g, ' ').trim();
          if (this._commentedPosts?.has(snippet.slice(0, 80))) continue;
          leadAlert += '\nLEAD ON SCREEN: "...' + snippet + '..."\n';
        }
      }

      if (leadAlert) {
        instruction = leadAlert + '\nACT ON THIS LEAD NOW. Click the comment button or input to respond.';
      } else {
        instruction = 'No leads visible. [SCROLL:+1200] to see more.';
      }
    } else {
      instruction = '(page snapshot not available)';
    }

    // Build a compact step message
    const pageHeader = this.currentTitle
      ? '"' + this.currentTitle + '" -- ' + (this.currentUrl || '')
      : (this.currentUrl || '(unknown page)');

    // Bug #3: After step 5, send only snapshot (not page_text via ui_context)
    // The sendMessage call will include ui_context with page_snapshot automatically
    this.sendMessage(
      '[Step ' + this._taskStep + ' of task: ' + this._taskText + ']\n' +
      'Page: ' + pageHeader + '\n' +
      instruction
    );
  }

  _autoScrollAndContinue() {
    // Route scroll command through background for result feedback
    chrome.runtime.sendMessage({
      type: 'execute_action',
      action: { type: 'scroll', target: '+1200' },
    }).catch(() => {});

    setTimeout(async () => {
      if (!this._taskActive || this._taskStopped) return;

      // Fetch fresh snapshot directly (awaited)
      await this._fetchSnapshotNow();

      if (this.currentSnapshot && this._taskPageSnapshots) {
        const snapText = typeof this.currentSnapshot === 'string'
          ? this.currentSnapshot
          : JSON.stringify(this.currentSnapshot);
        this._taskPageSnapshots.push(snapText);
      }
      this._taskStep = (this._taskStep || 0) + 1;
      this._updateTaskBar();
      this._continueTask();
    }, 2000);
  }

  // -- Self-driving scroll loop -------------------------------------------------

  async _runSelfScroll(maxScrolls, createCanvas) {
    this._selfScrolling = true;
    this._taskPageSnapshots = [];
    this._showTaskBar(this._lastUserMessage || 'Scrolling...', 10);

    // Capture initial page state
    await this._fetchSnapshotNow();
    if (this.currentSnapshot) {
      const snapText = typeof this.currentSnapshot === 'string'
        ? this.currentSnapshot
        : JSON.stringify(this.currentSnapshot);
      this._taskPageSnapshots.push(snapText);
    }

    for (let i = 0; i < maxScrolls; i++) {
      if (!this._selfScrolling) break;

      this._taskStep = i + 1;
      this._updateTaskBar();

      chrome.runtime.sendMessage({
        type: 'execute_action',
        action: { type: 'scroll', target: '+1200' },
      }).catch(() => {});

      await new Promise(r => setTimeout(r, 2000));

      // Read and save snapshot
      await this._fetchSnapshotNow();
      if (this.currentSnapshot) {
        const snapText = typeof this.currentSnapshot === 'string'
          ? this.currentSnapshot
          : JSON.stringify(this.currentSnapshot);
        this._taskPageSnapshots.push(snapText);
      }
    }

    this._selfScrolling = false;

    if (createCanvas) {
      this.addSystemMsg('Scrolling complete. Creating canvas page...');
      await this._createCanvasViaAgent();
    } else {
      this._hideTaskBar();
      this.addSystemMsg('Scrolling complete. ' + this._taskPageSnapshots.length + ' snapshots collected.');
    }
  }

  // -- Canvas Creation ----------------------------------------------------------

  async _createCanvasViaAgent() {
    const allText = this._dedupeSnapshots(this._taskPageSnapshots);
    const slug = (this._lastUserMessage || 'scroll-results')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
    const title = this._lastUserMessage || 'Collected Data';

    const html = this._buildCanvasHTML(title, allText);

    try {
      const authHeaders = await this.getAuthHeaders();
      const resp = await this._fetchWithRetry(
        'https://' + this.domain + '/api/canvas/pages',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          body: JSON.stringify({ filename: slug + '.html', title, html }),
        },
        1
      );
      if (resp.ok) {
        const data = await resp.json();
        console.log('[JamBot] Canvas page created:', data.url);
        this.addSystemMsg('Canvas page created: ' + title);
        this._hideTaskBar();
      } else {
        const err = await resp.text().catch(() => '');
        console.error('[JamBot] Canvas create failed:', resp.status, err);
        this.addSystemMsg('Failed to create canvas page (' + resp.status + ')');
        this._hideTaskBar();
      }
    } catch (e) {
      console.error('[JamBot] Canvas create error:', e);
      this.addSystemMsg('Canvas creation error: ' + e.message);
      this._hideTaskBar();
    }
  }

  async _createCanvasFromTask(pageId, summary) {
    const slug = pageId || (this._taskText || 'collected-data')
      .toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
    const title = summary || this._taskText || 'Collected Data';
    const snapshots = this._taskPageSnapshots || [];

    const allText = this._dedupeSnapshots(snapshots);
    const html = this._buildCanvasHTML(title, allText);

    try {
      const authHeaders = await this.getAuthHeaders();
      const resp = await this._fetchWithRetry(
        'https://' + this.domain + '/api/canvas/pages',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          body: JSON.stringify({ filename: slug + '.html', title, html }),
        },
        1
      );
      if (resp.ok) {
        const data = await resp.json();
        console.log('[JamBot] Canvas page created:', data.url);
        this.addSystemMsg('Canvas page created: ' + title);
        // Open it in the JamBot app
        chrome.runtime.sendMessage({
          type: 'navigate',
          url: 'https://' + this.domain + data.url,
        }).catch(() => {});
      } else {
        const err = await resp.text().catch(() => '');
        console.error('[JamBot] Canvas create failed:', resp.status, err);
        this.addSystemMsg('Failed to create canvas page (' + resp.status + ')');
      }
    } catch (e) {
      console.error('[JamBot] Canvas create error:', e);
      this.addSystemMsg('Canvas creation error: ' + e.message);
    }
  }

  _dedupeSnapshots(snapshots) {
    const seen = new Set();
    const lines = [];
    for (const snap of snapshots) {
      for (const line of snap.split('\n')) {
        const trimmed = line.trim();
        if (trimmed.length > 20 && !seen.has(trimmed)) {
          seen.add(trimmed);
          lines.push(trimmed);
        }
      }
    }
    return lines.join('\n');
  }

  _buildCanvasHTML(title, content) {
    const posts = [];
    const postPattern = /([A-Za-z0-9_ .]+)@(\w+)\xB7([^\n]*)\n?([\s\S]*?)(?=(?:[A-Za-z0-9_ .]+@\w+\xB7)|$)/g;
    let match;
    const rawContent = content;
    while ((match = postPattern.exec(rawContent)) !== null) {
      const name = match[1].trim();
      const handle = match[2];
      const time = match[3].trim();
      const text = match[4].trim().slice(0, 300);
      if (text.length > 10) {
        posts.push({ name, handle, time, text });
      }
    }

    const postsHtml = posts.length > 0
      ? posts.map((p) =>
          '<div class="post">' +
            '<div class="post-header">' +
              '<strong>' + this.escHtml(p.name) + '</strong> ' +
              '<a href="https://x.com/' + this.escHtml(p.handle) + '" target="_blank">@' + this.escHtml(p.handle) + '</a> ' +
              '<span class="time">' + this.escHtml(p.time) + '</span>' +
            '</div>' +
            '<div class="post-body">' + this.escHtml(p.text) + '</div>' +
          '</div>'
        ).join('')
      : '<pre>' + this.escHtml(content.slice(0, 30000)) + '</pre>';

    return '<!DOCTYPE html>\n' +
      '<html><head><meta charset="utf-8"><title>' + this.escHtml(title) + '</title>\n' +
      '<style>\n' +
      'body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;padding:24px;max-width:900px;margin:0 auto}\n' +
      'h1{color:#58a6ff;font-size:22px;border-bottom:1px solid #30363d;padding-bottom:12px}\n' +
      '.meta{color:#8b949e;font-size:13px;margin-top:4px;margin-bottom:20px}\n' +
      '.post{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px;margin:10px 0}\n' +
      '.post-header{margin-bottom:8px;font-size:14px}\n' +
      '.post-header a{color:#58a6ff;text-decoration:none;margin:0 6px}\n' +
      '.post-header .time{color:#8b949e;font-size:12px}\n' +
      '.post-body{line-height:1.5;font-size:14px}\n' +
      'pre{background:#161b22;padding:12px;border-radius:6px;overflow-x:auto;white-space:pre-wrap;font-size:13px;border:1px solid #30363d}\n' +
      'a{color:#58a6ff}\n' +
      '</style></head><body>\n' +
      '<h1>' + this.escHtml(title) + '</h1>\n' +
      '<div class="meta">' + posts.length + ' items collected by JamBot -- ' + new Date().toLocaleString() + '</div>\n' +
      postsHtml + '\n' +
      '</body></html>';
  }

  // -- Task UI ------------------------------------------------------------------

  _stopTask(reason) {
    this._taskActive    = false;
    this._taskStopped   = true;
    this._selfScrolling = false;
    this._hideTaskBar();
    this.addSystemMsg('Task stopped: ' + reason);
  }

  _showTaskBar(taskText, maxMinutes) {
    let bar = document.getElementById('task-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'task-bar';
      bar.className = 'task-bar';
      const header = document.querySelector('.header');
      header?.insertAdjacentElement('afterend', bar);
    }
    bar.innerHTML =
      '<span class="task-status">' +
        '<span id="task-label">' + this.escHtml(taskText.slice(0, 50)) + '</span>' +
      '</span>' +
      '<span class="task-step" id="task-step">Step 0</span>' +
      '<button class="task-stop-btn" id="task-stop-btn">Stop</button>';
    bar.style.display = 'flex';
    document.getElementById('task-stop-btn')?.addEventListener('click', () => this._stopTask('Stopped by user'));
  }

  _updateTaskBar() {
    const el = document.getElementById('task-step');
    if (el) el.textContent = 'Step ' + this._taskStep;
  }

  _hideTaskBar() {
    const bar = document.getElementById('task-bar');
    if (bar) bar.style.display = 'none';
  }

  // -- Audio Playback -----------------------------------------------------------

  playAudio(base64) {
    return new Promise((resolve) => {
      const audio = new Audio('data:audio/mp3;base64,' + base64);
      this._activeAudio = audio;
      audio.onended  = () => { this._activeAudio = null; resolve(); };
      audio.onerror  = () => { this._activeAudio = null; resolve(); };
      audio.play().catch(() => { this._activeAudio = null; resolve(); });
    });
  }

  stopActiveAudio() {
    if (this._activeAudio) {
      try {
        this._activeAudio.pause();
        this._activeAudio.currentTime = 0;
      } catch (_) {}
      this._activeAudio = null;
    }
  }

  // -- Text Rendering -----------------------------------------------------------

  renderText(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
      .replace(/`([^`\n]+)`/g, '<code style="background:#1e2a3a;padding:1px 4px;border-radius:3px;font-size:11px">$1</code>')
      .replace(/\n/g, '<br>');
  }

  // -- Message Display ----------------------------------------------------------

  addMessage(role, text) {
    const el = document.createElement('div');
    el.className = 'message message-' + role;
    if (role === 'user') {
      el.textContent = text;
    } else {
      el.innerHTML = this.renderText(JamBotCommandParser.stripTags(text));
    }
    this.$messages.appendChild(el);
    this.$messages.scrollTop = this.$messages.scrollHeight;
    return el;
  }

  addSystemMsg(text) {
    const el = document.createElement('div');
    el.className = 'message message-interim';
    el.textContent = text;
    this.$messages.appendChild(el);
    this.$messages.scrollTop = this.$messages.scrollHeight;
  }

  showInterim(text) {
    let el = document.getElementById('interim-display');
    if (!el) {
      el = document.createElement('div');
      el.id = 'interim-display';
      el.className = 'message message-interim';
      this.$messages.appendChild(el);
    }
    el.textContent = text + '...';
    this.$messages.scrollTop = this.$messages.scrollHeight;
  }

  clearInterim() {
    document.getElementById('interim-display')?.remove();
  }

  showError(msg) {
    this.$errBanner.textContent = msg;
    this.$errBanner.style.display = 'block';
    setTimeout(() => { this.$errBanner.style.display = 'none'; }, 6000);
  }

  // -- Mic State UI -------------------------------------------------------------

  setMicState(state) {
    this.$micBtn.className = 'mic-btn mic-btn--' + state;
    // Bug #16: Set _isSpeaking flag for waveform detection
    this._isSpeaking = (state === 'speaking');
    const labels = {
      idle:      'Tap to speak',
      listening: 'Listening...',
      thinking:  'Thinking...',
      speaking:  'Speaking...',
      error:     'Error -- tap to retry',
    };
    this.$micLabel.textContent = labels[state] || '';
  }

  // -- Waveform -----------------------------------------------------------------

  startWaveform() {
    const canvas = this.$wave;
    const ctx    = this.$waveCtx;
    const w = canvas.width;
    const h = canvas.height;
    let t = 0;

    const draw = () => {
      ctx.clearRect(0, 0, w, h);

      // Bug #16: Use this._isSpeaking flag (not this.setMicState._speaking)
      const active = this.stt.isListening || this._isSpeaking;
      const amp    = active ? 13 : 3;
      const speed  = active ? 0.055 : 0.015;
      const color  = this.stt.isListening ? '#00d2ff' : '#2a2a3a';

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth   = 1.5;
      ctx.shadowBlur  = active ? 8 : 0;
      ctx.shadowColor = '#00d2ff';

      for (let x = 0; x <= w; x++) {
        const y = h / 2
          + Math.sin((x / w) * Math.PI * 5 + t) * amp * Math.sin((x / w) * Math.PI);
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      t += speed;

      // Bug #17: Store RAF ID for cleanup
      this._waveAnimId = requestAnimationFrame(draw);
    };

    draw();
  }

  // Bug #17: Cancel waveform animation to prevent memory leak
  stopWaveform() {
    if (this._waveAnimId) {
      cancelAnimationFrame(this._waveAnimId);
      this._waveAnimId = null;
    }
  }
}

// -- Boot -----------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  // Setup screen button -- must be wired before JamBotPanel.init() runs
  // (inline onclick is blocked by MV3 Content Security Policy)
  document.getElementById('setup-open-btn')?.addEventListener('click', () => {
    chrome.tabs.create({ url: chrome.runtime.getURL('popup.html') });
  });

  window.jambot = new JamBotPanel();
});
