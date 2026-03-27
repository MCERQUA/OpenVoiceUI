/**
 * UpdateBanner — Checks for app updates and shows a dismissible banner.
 *
 * On init, fetches /api/version to compare the running build against
 * the latest release on GitHub. If an update is available, shows a
 * floating notification with a one-click update button.
 */

const UPDATE_CHECK_INTERVAL = 30 * 60 * 1000; // Re-check every 30 min

let _banner = null;
let _dismissed = false;
let _updating = false;

function createBanner(versionData) {
    if (_banner) _banner.remove();

    const version = versionData.latest_version || 'update';
    const el = document.createElement('div');
    el.id = 'update-banner';
    el.innerHTML = `
        <div class="update-banner-status">
            <span class="update-banner-title">OpenVoiceUI ${version} is available</span>
        </div>
        <div class="update-banner-actions">
            <button class="update-banner-btn update-btn" id="update-apply-btn">Update</button>
            <button class="update-banner-btn dismiss-btn" id="update-dismiss-btn">&times;</button>
        </div>
    `;
    document.body.appendChild(el);
    _banner = el;

    // Force reflow then animate in
    el.offsetHeight;
    el.classList.add('visible');

    el.querySelector('#update-dismiss-btn').addEventListener('click', () => {
        if (_updating) return; // Can't dismiss during update
        el.classList.remove('visible');
        setTimeout(() => el.remove(), 300);
        _banner = null;
        _dismissed = true;
    });

    el.querySelector('#update-apply-btn').addEventListener('click', () => {
        if (!_updating) applyUpdate(el);
    });
}

function setStatus(bannerEl, message, showSpinner) {
    const status = bannerEl.querySelector('.update-banner-status');
    if (!status) return;
    status.innerHTML = `
        ${showSpinner ? '<span class="update-spinner"></span>' : ''}
        <span class="update-banner-title">${message}</span>
    `;
}

async function applyUpdate(bannerEl) {
    _updating = true;
    const btn = bannerEl.querySelector('#update-apply-btn');
    const dismiss = bannerEl.querySelector('#update-dismiss-btn');
    btn.style.display = 'none';
    dismiss.style.opacity = '0.2';
    dismiss.style.pointerEvents = 'none';

    setStatus(bannerEl, 'Downloading update...', true);

    try {
        const resp = await fetch('/api/version/update', { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'updating') {
            setStatus(bannerEl, 'Installing update — restarting app...', true);
            pollForRestart(bannerEl);
        } else if (data.status === 'current') {
            setStatus(bannerEl, 'Already up to date — reloading...', true);
            setTimeout(() => window.location.reload(), 1000);
        } else if (data.status === 'manual') {
            _updating = false;
            btn.style.display = '';
            btn.textContent = 'How to update';
            dismiss.style.opacity = '';
            dismiss.style.pointerEvents = '';
            btn.onclick = () => showUpdateInstructions();
            setStatus(bannerEl, 'Automatic update not available', false);
        } else {
            setStatus(bannerEl, data.error || 'Update failed — try again', false);
            btn.style.display = '';
            btn.textContent = 'Retry';
            dismiss.style.opacity = '';
            dismiss.style.pointerEvents = '';
            _updating = false;
        }
    } catch (e) {
        // Server likely restarting already
        setStatus(bannerEl, 'Installing update — restarting app...', true);
        pollForRestart(bannerEl);
    }
}

function pollForRestart(bannerEl) {
    let attempts = 0;
    const maxAttempts = 90; // 90 seconds max
    const stages = [
        { at: 0, msg: 'Restarting app...' },
        { at: 10, msg: 'Still restarting — almost there...' },
        { at: 30, msg: 'Taking a bit longer than usual...' },
        { at: 60, msg: 'Still waiting for server...' },
    ];

    const interval = setInterval(async () => {
        attempts++;

        // Update status message at milestones
        for (const stage of stages) {
            if (attempts === stage.at) {
                setStatus(bannerEl, stage.msg, true);
            }
        }

        if (attempts > maxAttempts) {
            clearInterval(interval);
            _updating = false;
            setStatus(bannerEl, 'Update may have completed — reload to check', false);
            const actions = bannerEl.querySelector('.update-banner-actions');
            if (actions) {
                actions.innerHTML = `<button class="update-banner-btn update-btn" onclick="window.location.reload()">Reload</button>`;
            }
            return;
        }
        try {
            const resp = await fetch('/health/live', { signal: AbortSignal.timeout(2000) });
            if (resp.ok) {
                clearInterval(interval);
                setStatus(bannerEl, 'Updated — reloading...', true);
                setTimeout(() => window.location.reload(), 500);
            }
        } catch (_) {
            // Server still restarting
        }
    }, 1000);
}

async function checkForUpdate() {
    if (_dismissed || _updating) return;
    try {
        const resp = await fetch('/api/version');
        if (!resp.ok) return;
        const data = await resp.json();
        window.__APP_VERSION = data;

        if (data.update_available) {
            createBanner(data);
        }
    } catch (_) {
        // Silently fail — not critical
    }
}

function showUpdateInstructions() {
    if (_banner) { _banner.remove(); _banner = null; }

    const overlay = document.createElement('div');
    overlay.id = 'update-instructions-overlay';
    overlay.innerHTML = `
        <div class="update-instructions-modal">
            <h3>Update OpenVoiceUI</h3>
            <p>Run these commands where you installed OpenVoiceUI:</p>
            <pre><code>git pull origin main
docker compose build
docker compose up -d</code></pre>
            <p class="update-instructions-note">This will pull the latest version, rebuild, and restart your app.</p>
            <button id="update-instructions-close">Got it</button>
        </div>
    `;
    document.body.appendChild(overlay);
    overlay.querySelector('#update-instructions-close').addEventListener('click', () => {
        overlay.remove();
        _dismissed = true;
    });
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) { overlay.remove(); _dismissed = true; }
    });
}

/**
 * Initialize update checker. Call once on app startup.
 */
export function initUpdateChecker() {
    setTimeout(checkForUpdate, 3000);
    setInterval(() => {
        if (!_dismissed && !_updating) checkForUpdate();
    }, UPDATE_CHECK_INTERVAL);
}
