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
        <div class="update-banner-inner">
            <div class="update-banner-text">
                <span class="update-banner-title">Update available</span>
                <span class="update-banner-detail">${versionData.latest_message || 'New version ready'}</span>
            </div>
            <div class="update-banner-actions">
                <button class="update-banner-btn update-btn" id="update-apply-btn">Update now</button>
                <button class="update-banner-btn dismiss-btn" id="update-dismiss-btn">Later</button>
            </div>
        </div>
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

    try {
        // Call host-side update service
        const resp = await fetch('/api/version/update', { method: 'POST' });
        const data = await resp.json();

        if (data.status === 'updating') {
            btn.textContent = 'Restarting...';
            // Poll until server comes back, then reload
            pollForRestart();
        } else if (data.status === 'current') {
            // Already up to date — just reload to pick up any cached changes
            window.location.reload();
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
