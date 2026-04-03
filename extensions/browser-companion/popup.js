/**
 * JamBot Browser Companion — Settings Popup
 *
 * Bug #22 fix: storage.sync -> storage.local (per-machine, not cross-device)
 * Bug #23 fix: health check sends auth headers
 */

const $ = (id) => document.getElementById(id);

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

function updateVoiceSelect(provider, savedVoice) {
  const sel = $('voice');
  const voices = VOICE_OPTIONS[provider] || VOICE_OPTIONS.groq;
  sel.innerHTML = '';
  for (const { value, label } of voices) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    sel.appendChild(opt);
  }
  const valid = savedVoice && voices.some(v => v.value === savedVoice);
  sel.value = valid ? savedVoice : (DEFAULT_VOICE[provider] || voices[0].value);
}

async function load() {
  // Bug #22 fix: use storage.local instead of storage.sync
  const prefs = await chrome.storage.local.get(['domain', 'ttsProvider', 'ttsVoice', 'ttsEnabled', 'recordingEnabled']);
  if (prefs.domain)   $('domain').value       = prefs.domain;
  const provider = prefs.ttsProvider || 'groq';
  $('tts-provider').value = provider;
  updateVoiceSelect(provider, prefs.ttsVoice);
  if (prefs.ttsEnabled !== undefined)       $('tts-enabled').checked = prefs.ttsEnabled;
  if (prefs.recordingEnabled !== undefined) $('recording').checked   = prefs.recordingEnabled;
}

async function save() {
  const domain = $('domain').value.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
  if (!domain) {
    showMsg('error', 'Please enter your JamBot domain (e.g. yourname.jam-bot.com)');
    return;
  }

  // Bug #22 fix: use storage.local instead of storage.sync
  await chrome.storage.local.set({
    domain,
    ttsProvider:      $('tts-provider').value,
    ttsVoice:         $('voice').value,
    ttsEnabled:       $('tts-enabled').checked,
    recordingEnabled: $('recording').checked,
  });

  showMsg('ok', 'Saved! Open the sidebar to start chatting.');
  setStatus('idle', 'Saved -- open sidebar to connect');
}

async function testConnection() {
  const domain = $('domain').value.trim().replace(/^https?:\/\//, '').replace(/\/$/, '');
  if (!domain) { showMsg('error', 'Enter a domain first.'); return; }

  setStatus('idle', 'Testing...');
  $('test-btn').disabled = true;

  try {
    // Bug #23 fix: send auth headers with health check
    let authHeaders = {};
    try {
      const cookie = await chrome.cookies.get({ url: `https://${domain}`, name: '__session' });
      if (cookie?.value) authHeaders['Authorization'] = `Bearer ${cookie.value}`;
    } catch (_) {}

    if (!Object.keys(authHeaders).length) {
      setStatus('error', 'Not logged in');
      showMsg('error', `Open https://${domain} in Chrome and log in first, then test again.`);
      $('test-btn').disabled = false;
      return;
    }

    const r = await fetch(`https://${domain}/health/live`, {
      headers: authHeaders,
      signal: AbortSignal.timeout(6000),
    });

    if (r.ok) {
      const data = await r.json().catch(() => ({}));
      setStatus('ok', `Connected -- ${data.status || 'healthy'}`);
      showMsg('ok', `Connected to ${domain}`);
    } else {
      setStatus('error', `Server returned ${r.status}`);
      showMsg('error', `Server returned ${r.status}. Make sure you're logged in at https://${domain} first.`);
    }
  } catch (e) {
    if (e.name === 'TimeoutError') {
      setStatus('error', 'Connection timed out');
      showMsg('error', 'Connection timed out. Check the domain and try again.');
    } else {
      setStatus('error', 'Could not connect');
      showMsg('error', `Could not connect: ${e.message}`);
    }
  } finally {
    $('test-btn').disabled = false;
  }
}

function setStatus(state, text) {
  const dot = $('status-dot');
  dot.className = 'status-dot ' + ({ ok: 'dot-ok', error: 'dot-error', idle: 'dot-idle' }[state] || 'dot-idle');
  $('status-text').textContent = text || '';
}

function showMsg(type, text) {
  const ok  = $('msg-ok');
  const err = $('msg-error');
  ok.style.display = err.style.display = 'none';
  const el = type === 'ok' ? ok : err;
  el.textContent   = text;
  el.style.display = 'block';
  setTimeout(() => { el.style.display = 'none'; }, 5000);
}

// Boot
load();
$('save-btn').addEventListener('click', save);
$('test-btn').addEventListener('click', testConnection);
$('tts-provider').addEventListener('change', () => updateVoiceSelect($('tts-provider').value, null));
