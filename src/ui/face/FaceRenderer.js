/**
 * FaceRenderer — Modular face/avatar plugin system.
 *
 * Face plugins self-register at load time via registerFace(id, handler, meta).
 * Profiles store face_mode + face_config, and applyProfile() calls
 * setMode(mode, config) to switch faces with their per-agent config.
 *
 * Built-in faces: 'eyes' (DOM), 'orb' (inline canvas).
 * Plugin faces: 'halo-smoke', 'bighead', and any future additions.
 */

window.FaceRenderer = {
    // Metadata for display (populated by registerFace or built-in)
    modes: {
        'eyes': { name: 'AI Eyes', description: 'Classic animated eyes' },
        'orb':  { name: 'Sound Orb', description: 'Audio-reactive particle orb' }
    },

    // Plugin registry: id → { start(container, config), stop(), setMood?, setThinking?, detectMood? }
    _registry: {},

    currentMode: 'eyes',
    _defaultMode: 'halo-smoke',
    _currentConfig: null,
    container: null,
    audioContext: null,
    analyser: null,
    animationFrame: null,

    // Orb-specific state (built-in, not a plugin)
    orb: {
        canvas: null,
        ctx: null,
        particles: [],
        baseRadius: 80,
        pulsePhase: 0
    },

    // ── Plugin API ───────────────────────────────────────────────────────

    /**
     * Register a face plugin. Called by face scripts at load time.
     * @param {string} id       Unique face ID (matches manifest.json)
     * @param {object} handler  { start(container, config), stop(), setMood?, setThinking?, detectMood? }
     * @param {object} [meta]   { name, description } for display
     */
    registerFace(id, handler, meta) {
        this._registry[id] = handler;
        if (meta) {
            this.modes[id] = { name: meta.name || id, description: meta.description || '' };
        } else if (!this.modes[id]) {
            this.modes[id] = { name: id, description: '' };
        }
        console.log(`[FaceRenderer] Registered face plugin: ${id}`);
    },

    /** Check if a face plugin is installed/registered. */
    hasFace(id) {
        return id in this._registry || id === 'eyes' || id === 'orb';
    },

    // ── Lifecycle ────────────────────────────────────────────────────────

    init() {
        this.container = document.querySelector('.face-box');
        if (!this.container) {
            console.warn('FaceRenderer: .face-box container not found');
            return;
        }

        // Register built-in eyes face
        this._registry['eyes'] = {
            start: (container) => {
                const eyesEl = container.querySelector('.eyes-container');
                if (eyesEl) eyesEl.style.display = 'flex';
            },
            stop: () => {}
        };

        // Register built-in orb face
        this._registry['orb'] = {
            start: (container) => this.renderOrb(),
            stop: () => {}
        };

        // Load saved mode from server profile
        const savedMode = window._serverProfile?.ui?.face_mode;
        const savedConfig = window._serverProfile?.ui?.face_config || null;
        if (savedMode && this.hasFace(savedMode)) {
            this.currentMode = savedMode;
            this._currentConfig = savedConfig;
        } else if (savedMode && !this.hasFace(savedMode)) {
            // Face plugin not installed — fall back gracefully
            console.warn(`[FaceRenderer] Face "${savedMode}" not installed, using "${this._defaultMode}"`);
            this.currentMode = this.hasFace(this._defaultMode) ? this._defaultMode : 'eyes';
        }

        // Listen for theme changes (orb only)
        window.addEventListener('themeChanged', (e) => {
            if (this.currentMode === 'orb') {
                this.updateOrbColors(e.detail);
            }
        });

        // Initial render
        this.render();
    },

    /**
     * Switch face mode. Optionally pass face-specific config (e.g. BigHead character traits).
     * @param {string} modeName  Face plugin ID
     * @param {object} [config]  Face-specific config from profile.ui.face_config
     * @param {object} [options] { persist: false } to skip saving to server
     */
    setMode(modeName, config, options) {
        if (!this.hasFace(modeName)) {
            console.warn(`[FaceRenderer] Face "${modeName}" not installed, falling back to "${this._defaultMode}"`);
            modeName = this.hasFace(this._defaultMode) ? this._defaultMode : 'eyes';
            config = null;
        }

        this.cleanup();
        this.currentMode = modeName;
        this._currentConfig = config || null;

        // Persist to server profile (unless explicitly skipped, e.g. when called from applyProfile)
        if (!options?.skipPersist) {
            const profileId = window._activeProfileData?.id || window.providerManager?._activeProfileId || 'default';
            const update = { ui: { face_mode: modeName } };
            if (config) update.ui.face_config = config;
            fetch('/api/profiles/' + profileId, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(update)
            }).catch(e => console.warn('Failed to save face mode:', e));
        }

        if (window._serverProfile) {
            if (!window._serverProfile.ui) window._serverProfile.ui = {};
            window._serverProfile.ui.face_mode = modeName;
            if (config) window._serverProfile.ui.face_config = config;
        }

        this.render();
        window.dispatchEvent(new CustomEvent('faceModeChanged', { detail: modeName }));
    },

    cleanup() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }

        // Remove orb canvas if exists (built-in)
        if (this.orb.canvas && this.orb.canvas.parentNode) {
            this.orb.canvas.parentNode.removeChild(this.orb.canvas);
            this.orb.canvas = null;
            this.orb.ctx = null;
        }

        // Stop all registered face plugins
        for (const handler of Object.values(this._registry)) {
            if (typeof handler.stop === 'function') {
                try { handler.stop(); } catch (e) { /* ignore */ }
            }
        }
    },

    render() {
        const handler = this._registry[this.currentMode];
        if (handler && typeof handler.start === 'function') {
            handler.start(this.container, this._currentConfig);
            return;
        }
        console.warn(`[FaceRenderer] No handler for mode "${this.currentMode}"`);
    },

    // ── Built-in Orb (too small to be a separate plugin file) ────────────

    renderOrb() {
        const eyesContainer = this.container.querySelector('.eyes-container');
        if (eyesContainer) eyesContainer.style.display = 'none';

        if (!this.orb.canvas) {
            this.orb.canvas = document.createElement('canvas');
            this.orb.canvas.id = 'orb-canvas';
            this.orb.canvas.style.cssText = `
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                pointer-events: none;
            `;
            this.container.appendChild(this.orb.canvas);
            this.orb.ctx = this.orb.canvas.getContext('2d');
        }

        const size = Math.min(this.container.offsetWidth, this.container.offsetHeight) * 0.6;
        this.orb.canvas.width = size;
        this.orb.canvas.height = size;
        this.initOrbParticles();
        this.animateOrb();
    },

    initOrbParticles() {
        this.orb.particles = [];
        const count = 30;
        for (let i = 0; i < count; i++) {
            this.orb.particles.push({
                angle: (Math.PI * 2 / count) * i,
                radius: this.orb.baseRadius + Math.random() * 20,
                speed: 0.01 + Math.random() * 0.02,
                size: 2 + Math.random() * 4,
                alpha: 0.3 + Math.random() * 0.7
            });
        }
    },

    animateOrb() {
        if (this.currentMode !== 'orb') return;

        const ctx = this.orb.ctx;
        const canvas = this.orb.canvas;
        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        let audioLevel = 0;
        if (window.audioAnalyser) {
            const dataArray = new Uint8Array(window.audioAnalyser.frequencyBinCount);
            window.audioAnalyser.getByteFrequencyData(dataArray);
            audioLevel = dataArray.slice(0, 10).reduce((a, b) => a + b, 0) / 10 / 255;
        }

        const theme = window.ThemeManager?.getCurrentTheme() || {};
        const primaryColor = theme.primary || '#0088ff';
        const accentColor = theme.accent || '#00ffff';

        this.orb.pulsePhase += 0.02;
        const pulse = Math.sin(this.orb.pulsePhase) * 0.1 + 1;
        const audioPulse = 1 + audioLevel * 0.5;
        const baseRadius = this.orb.baseRadius * pulse * audioPulse;

        const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 1.5);
        gradient.addColorStop(0, this.hexToRgba(primaryColor, 0.3));
        gradient.addColorStop(0.5, this.hexToRgba(accentColor, 0.1));
        gradient.addColorStop(1, 'transparent');
        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();

        const coreGradient = ctx.createRadialGradient(
            centerX - baseRadius * 0.3, centerY - baseRadius * 0.3, 0,
            centerX, centerY, baseRadius
        );
        coreGradient.addColorStop(0, this.hexToRgba(accentColor, 0.8));
        coreGradient.addColorStop(0.5, this.hexToRgba(primaryColor, 0.5));
        coreGradient.addColorStop(1, this.hexToRgba(primaryColor, 0.2));
        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius, 0, Math.PI * 2);
        ctx.fillStyle = coreGradient;
        ctx.fill();
        ctx.strokeStyle = this.hexToRgba(accentColor, 0.6);
        ctx.lineWidth = 2;
        ctx.stroke();

        this.orb.particles.forEach(p => {
            p.angle += p.speed * (1 + audioLevel * 2);
            const wobble = Math.sin(this.orb.pulsePhase * 2 + p.angle) * 10 * audioLevel;
            const x = centerX + Math.cos(p.angle) * (p.radius + wobble);
            const y = centerY + Math.sin(p.angle) * (p.radius + wobble);
            ctx.beginPath();
            ctx.arc(x, y, p.size * audioPulse, 0, Math.PI * 2);
            ctx.fillStyle = this.hexToRgba(accentColor, p.alpha);
            ctx.fill();
        });

        const highlightGradient = ctx.createRadialGradient(
            centerX - baseRadius * 0.4, centerY - baseRadius * 0.4, 0,
            centerX - baseRadius * 0.4, centerY - baseRadius * 0.4, baseRadius * 0.5
        );
        highlightGradient.addColorStop(0, 'rgba(255, 255, 255, 0.4)');
        highlightGradient.addColorStop(1, 'transparent');
        ctx.beginPath();
        ctx.arc(centerX - baseRadius * 0.3, centerY - baseRadius * 0.3, baseRadius * 0.4, 0, Math.PI * 2);
        ctx.fillStyle = highlightGradient;
        ctx.fill();

        this.animationFrame = requestAnimationFrame(() => this.animateOrb());
    },

    // ── Utilities ────────────────────────────────────────────────────────

    hexToRgba(hex, alpha) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        if (!result) return `rgba(0, 136, 255, ${alpha})`;
        return `rgba(${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}, ${alpha})`;
    },

    updateOrbColors(colors) {
        // Colors picked up in next animation frame
    },

    getCurrentMode() {
        return this.currentMode;
    },

    getAvailableModes() {
        return Object.keys(this.modes).map(key => ({
            id: key,
            ...this.modes[key],
            installed: this.hasFace(key)
        }));
    }
};
