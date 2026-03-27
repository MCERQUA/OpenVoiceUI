/**
 * UpdateBanner — Checks for app updates and shows a dismissible banner.
 *
 * On init, fetches /api/version to compare the running build against
 * the latest commit on main. If an update is available, shows a fixed
 * banner at the top of the viewport with a one-click update button.
 *
 * The update button calls the host-side update service which recreates
 * the user's container with the latest image.
 */

const UPDATE_CHECK_INTERVAL = 30 * 60 * 1000; // Re-check every 30 min

let _banner = null;
let _dismissed = false;

function createBanner(versionData) {
    if (_banner) _banner.remove();

    const el = document.createElement('div');
    el.id = 'update-banner';
    el.innerHTML = `
        <span class="update-banner-title">A new version of OpenVoiceUI is available</span>
        <button class="update-banner-btn update-btn" id="update-apply-btn">Update</button>
        <button class="update-banner-btn dismiss-btn" id="update-dismiss-btn">&times;</button>
    `;
    document.body.appendChild(el);
    _banner = el;

    // Force reflow then animate in
    el.offsetHeight;
    el.classList.add('visible');

    el.querySelector('#update-dismiss-btn').addEventListener('click', () => {
        el.classList.remove('visible');
        setTimeout(() => el.remove(), 300);
        _banner = null;
        _dismissed = true;
    });

    el.querySelector('#update-apply-btn').addEventListener('click', () => {
        applyUpdate(el);
    });
}

async function applyUpdate(bannerEl) {
    const btn = bannerEl.querySelector('#update-apply-btn');
    const origText = btn.textContent;
    btn.textContent = 'Updating...';
    btn.disabled = true;
    // Update the title too
    const title = bannerEl.querySelector('.update-banner-title');
    if (title) title.textContent = 'Updating OpenVoiceUI...';

    try {
        const resp = await fetch('/api/version/update', { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'updating') {
            if (title) title.textContent = 'Restarting — one moment...';
            btn.style.display = 'none';
            pollForRestart();
        } else if (data.status === 'current') {
            window.location.reload();
        } else if (data.status === 'manual') {
            // No host update service — show update instructions inline
            btn.textContent = 'How to update';
            btn.disabled = false;
            btn.onclick = () => showUpdateInstructions();
        } else {
            btn.textContent = data.error || 'Update failed';
            setTimeout(() => { btn.textContent = origText; btn.disabled = false; }, 3000);
        }
    } catch (e) {
        // Server might already be restarting
        btn.textContent = 'Restarting...';
        pollForRestart();
    }
}

function pollForRestart() {
    let attempts = 0;
    const maxAttempts = 60; // 60 seconds max
    const interval = setInterval(async () => {
        attempts++;
        if (attempts > maxAttempts) {
            clearInterval(interval);
            if (_banner) {
                const btn = _banner.querySelector('#update-apply-btn');
                if (btn) { btn.textContent = 'Reload page'; btn.disabled = false; btn.onclick = () => window.location.reload(); }
            }
            return;
        }
        try {
            const resp = await fetch('/health/live', { signal: AbortSignal.timeout(2000) });
            if (resp.ok) {
                clearInterval(interval);
                window.location.reload();
            }
        } catch (_) {
            // Server still restarting — keep polling
        }
    }, 1000);
}

async function checkForUpdate() {
    if (_dismissed) return;
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
    // Remove the banner
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
    // First check after short delay (let app finish loading)
    setTimeout(checkForUpdate, 3000);
    // Periodic re-check
    setInterval(() => {
        if (!_dismissed) checkForUpdate();
    }, UPDATE_CHECK_INTERVAL);
}
