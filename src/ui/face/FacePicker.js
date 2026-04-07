/**
 * FacePicker — gallery UI for selecting the active face/avatar.
 *
 * Reads face definitions from /src/face/manifest.json, renders a card grid,
 * and delegates activation to faceManager (ES module) or FaceRenderer (legacy).
 *
 * Usage (standalone):
 *   const picker = new FacePicker();
 *   await picker.mount(document.getElementById('face-picker-root'));
 *
 * Usage (embedded in SettingsPanel):
 *   const html = await FacePicker.renderHTML();
 *   container.innerHTML = html;
 *   FacePicker.attachListeners(container);
 */

const MANIFEST_URL = '/src/face/manifest.json';
const STORAGE_KEY  = 'ai-face-active';

// ── helpers ────────────────────────────────────────────────────────────────

function getActiveFaceId() {
    // ES module faceManager takes priority
    try {
        if (window._faceManager?.activeFaceId) return window._faceManager.activeFaceId;
    } catch (_) {}
    // Server profile is source of truth
    try {
        if (window._serverProfile?.ui?.face_mode) return window._serverProfile.ui.face_mode;
    } catch (_) {}
    return 'eyes';
}

function persistFaceId(id) {
    // Save to server profile so all devices see the same face
    // Use _activeProfileData.id as canonical source — _activeProfileId can go stale on profile switch
    const profileId = window._activeProfileData?.id || window.providerManager?._activeProfileId || 'default';
    fetch('/api/profiles/' + profileId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui: { face_mode: id } })
    }).catch(e => console.warn('Failed to save face to profile:', e));
    // Update cached profile
    if (window._serverProfile) {
        if (!window._serverProfile.ui) window._serverProfile.ui = {};
        window._serverProfile.ui.face_mode = id;
    }
}

async function loadManifest() {
    try {
        const res = await fetch(MANIFEST_URL);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (err) {
        console.warn('[FacePicker] Could not load manifest, using defaults:', err.message);
        return {
            default: 'eyes',
            faces: [
                { id: 'eyes', name: 'AI Eyes',   description: 'Classic animated eyes', preview: null, moods: [], features: [] },
                { id: 'orb',  name: 'Sound Orb', description: 'Audio-reactive orb',   preview: null, moods: [], features: [] }
            ]
        };
    }
}

async function activateFace(id) {
    persistFaceId(id);

    // Try ES module faceManager first
    if (window._faceManager && typeof window._faceManager.load === 'function') {
        await window._faceManager.load(id);
        return;
    }

    // Fall back to legacy FaceRenderer
    if (window.FaceRenderer && typeof window.FaceRenderer.setMode === 'function') {
        window.FaceRenderer.setMode(id);
        return;
    }

    console.warn('[FacePicker] No face system available to activate face:', id);
}

// ── rendering ──────────────────────────────────────────────────────────────

function renderFeatureTags(features) {
    if (!features || features.length === 0) return '';
    return `<div class="face-card-features">
        ${features.map(f => `<span class="face-feature-tag">${f}</span>`).join('')}
    </div>`;
}

function renderMoodDots(moods) {
    if (!moods || moods.length === 0) return '';
    const dots = moods.map(m => `<span class="face-mood-dot" title="${m}" data-mood="${m}"></span>`).join('');
    return `<div class="face-card-moods" title="Supported moods">${dots}</div>`;
}

function renderCard(face, isActive) {
    const previewSrc  = face.preview || '';
    const previewHtml = previewSrc
        ? `<img class="face-card-preview" src="${previewSrc}" alt="${face.name} preview" loading="lazy">`
        : `<div class="face-card-preview face-card-preview--placeholder">
               <span>${face.name.charAt(0)}</span>
           </div>`;

    return `
        <div class="face-card ${isActive ? 'face-card--active' : ''}" data-face-id="${face.id}" role="button" tabindex="0"
             aria-label="Select ${face.name}${isActive ? ' (active)' : ''}">
            <div class="face-card-media">
                ${previewHtml}
                ${isActive ? '<div class="face-card-active-badge">Active</div>' : ''}
            </div>
            <div class="face-card-info">
                <div class="face-card-name">${face.name}</div>
                <div class="face-card-desc">${face.description}</div>
                ${renderMoodDots(face.moods)}
                ${renderFeatureTags(face.features)}
            </div>
        </div>
    `;
}

function renderGallery(manifest, activeFaceId) {
    const cards = manifest.faces.map(f => renderCard(f, f.id === activeFaceId)).join('');
    return `
        <div class="face-picker-gallery" role="radiogroup" aria-label="Face picker">
            ${cards}
        </div>
    `;
}

// ── event attachment ───────────────────────────────────────────────────────

function attachListeners(root) {
    root.querySelectorAll('.face-card').forEach(card => {
        const activate = async () => {
            const id = card.dataset.faceId;
            if (!id) return;

            // Update UI immediately
            root.querySelectorAll('.face-card').forEach(c => {
                c.classList.remove('face-card--active');
                c.setAttribute('aria-label', c.querySelector('.face-card-name')?.textContent || '');
                const badge = c.querySelector('.face-card-active-badge');
                if (badge) badge.remove();
            });
            card.classList.add('face-card--active');
            card.setAttribute('aria-label', `${card.querySelector('.face-card-name')?.textContent || id} (active)`);
            const media = card.querySelector('.face-card-media');
            if (media && !media.querySelector('.face-card-active-badge')) {
                const badge = document.createElement('div');
                badge.className = 'face-card-active-badge';
                badge.textContent = 'Active';
                media.appendChild(badge);
            }

            await activateFace(id);
        };

        card.addEventListener('click', activate);
        card.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate(); }
        });
    });
}

// ── public API ─────────────────────────────────────────────────────────────

/**
 * Render gallery HTML string (async, loads manifest).
 * @returns {Promise<string>}
 */
async function renderHTML() {
    const manifest = await loadManifest();
    const activeFaceId = getActiveFaceId();
    return renderGallery(manifest, activeFaceId);
}

/**
 * Mount the face picker into a container element.
 * @param {HTMLElement} container
 * @returns {Promise<void>}
 */
async function mount(container) {
    container.innerHTML = '<div class="face-picker-loading">Loading faces...</div>';
    container.innerHTML = await renderHTML();
    attachListeners(container);
}

// Expose as window global for SettingsPanel
const FacePicker = { renderHTML, mount, attachListeners };
if (typeof window !== 'undefined') {
    window.FacePicker = FacePicker;
}
