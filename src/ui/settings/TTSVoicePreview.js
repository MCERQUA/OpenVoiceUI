/**
 * TTSVoicePreview — Voice picker with live audio preview (P4-T5)
 *
 * Fetches available TTS providers + voices from /api/tts/providers,
 * renders a voice grid with play buttons, and calls /api/tts/preview
 * to generate short audio samples played via TTSPlayer.
 *
 * Usage (standalone):
 *   import { TTSVoicePreview } from './settings/TTSVoicePreview.js';
 *   const preview = new TTSVoicePreview();
 *   preview.mount(document.getElementById('my-container'));
 *
 * Usage (via SettingsPanel):
 *   SettingsPanel.open('voice');
 */

export class TTSVoicePreview {
    constructor() {
        this._player = null;
        this._root = null;
        this._activeBtn = null;
        this._loading = false;
    }

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------

    /**
     * Mount the voice preview widget into `container` (replaces contents).
     * @param {HTMLElement} container
     */
    async mount(container) {
        this._root = container;
        this._root.innerHTML = '<div class="tts-preview-loading">Loading voices\u2026</div>';

        let providers;
        try {
            const resp = await fetch('/api/tts/providers');
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            providers = await resp.json();
        } catch (err) {
            this._root.innerHTML = `<div class="tts-preview-error">Failed to load voices: ${err.message}</div>`;
            return;
        }

        this._root.innerHTML = this._render(providers);
        this._attachListeners();
    }

    destroy() {
        if (this._player) {
            this._player.stop();
            this._player = null;
        }
        if (this._root) {
            this._root.innerHTML = '';
            this._root = null;
        }
    }

    // -------------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------------

    _render(providersData) {
        // /api/tts/providers returns providers as an ARRAY of objects (each with
        // provider_id); older builds returned an object keyed by id. Normalize both.
        const raw = providersData.providers || {};
        const entries = Array.isArray(raw)
            ? raw.map(p => [p.provider_id || p.name, p])
            : Object.entries(raw);
        const defaultProvider = providersData.default_provider || 'supertonic';

        const sections = entries
            .filter(([, p]) => p.mode !== 'full-voice' && Array.isArray(p.voices) && p.voices.length > 0)
            .map(([id, p]) => this._renderProvider(id, p, id === defaultProvider))
            .join('');

        if (!sections) {
            return '<div class="tts-preview-empty">No previewable TTS voices found.</div>';
        }

        return `<div class="tts-voice-preview">${sections}</div>`;
    }

    _renderProvider(id, provider, isDefault) {
        const voices = provider.voices || [];
        const voiceCards = voices.map(v => this._renderVoiceCard(id, v)).join('');
        return `
            <div class="tts-provider-section">
                <div class="tts-provider-header">
                    <span class="tts-provider-name">${this._esc(provider.name || id)}</span>
                    ${isDefault ? '<span class="tts-provider-badge">default</span>' : ''}
                    <span class="tts-provider-meta">${this._esc(provider.description || '')}</span>
                </div>
                <div class="tts-voice-grid">${voiceCards}</div>
            </div>
        `;
    }

    _renderVoiceCard(providerId, voice) {
        return `
            <button
                class="tts-voice-card"
                data-provider="${this._esc(providerId)}"
                data-voice="${this._esc(voice)}"
                title="Preview voice ${this._esc(voice)}"
            >
                <span class="tts-voice-play-icon">&#9654;</span>
                <span class="tts-voice-name">${this._esc(voice)}</span>
            </button>
        `;
    }

    // -------------------------------------------------------------------------
    // Event handling
    // -------------------------------------------------------------------------

    _attachListeners() {
        if (!this._root) return;
        this._root.addEventListener('click', (e) => {
            const card = e.target.closest('.tts-voice-card');
            if (card && !this._loading) {
                this._previewVoice(card);
            }
        });
    }

    async _previewVoice(btn) {
        const provider = btn.dataset.provider;
        const voice = btn.dataset.voice;

        // Stop any running preview
        if (this._player) {
            this._player.stop();
        }

        // Visual: mark active
        if (this._activeBtn) {
            this._activeBtn.classList.remove('tts-voice-card--playing', 'tts-voice-card--loading');
        }
        this._activeBtn = btn;
        btn.classList.add('tts-voice-card--loading');
        this._loading = true;

        try {
            const resp = await fetch('/api/tts/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider, voice }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }

            const { audio_b64 } = await resp.json();

            btn.classList.remove('tts-voice-card--loading');
            btn.classList.add('tts-voice-card--playing');

            await this._playAudio(audio_b64);

        } catch (err) {
            console.error('[TTSVoicePreview] Preview failed:', err);
            this._showError(btn, err.message);
        } finally {
            btn.classList.remove('tts-voice-card--loading', 'tts-voice-card--playing');
            this._loading = false;
        }
    }

    async _playAudio(audio_b64) {
        // Use TTSPlayer if available (imported or on window), else HTMLAudio fallback
        const TTSPlayerClass = window.TTSPlayer;
        if (TTSPlayerClass) {
            if (!this._player) {
                this._player = new TTSPlayerClass();
            }
            await this._player.init();
            await this._player.play(audio_b64);
        } else {
            // Fallback: HTMLAudioElement
            const binary = atob(audio_b64);
            const bytes = new Uint8Array(binary.length);
            for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
            const blob = new Blob([bytes], { type: 'audio/wav' });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            await new Promise((resolve) => {
                audio.onended = () => { URL.revokeObjectURL(url); resolve(); };
                audio.onerror = () => { URL.revokeObjectURL(url); resolve(); };
                audio.play().catch(resolve);
            });
        }
    }

    _showError(btn, message) {
        btn.title = `Error: ${message}`;
        btn.classList.add('tts-voice-card--error');
        setTimeout(() => btn.classList.remove('tts-voice-card--error'), 3000);
    }

    _esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}

export default TTSVoicePreview;
