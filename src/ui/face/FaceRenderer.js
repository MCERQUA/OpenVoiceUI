/**
 * FaceRenderer - Modular face/avatar rendering system
 * Supports multiple face modes: eyes, orb, future options
 */

window.FaceRenderer = {
    // Available face modes
    modes: {
        'eyes': {
            name: 'AI Eyes',
            description: 'Classic animated eyes'
        },
        'halo-smoke': {
            name: 'Halo Smoke Orb',
            description: 'Halo ring + wispy smoke core, reacts to TTS audio'
        },
        'bighead': {
            name: 'BigHead Avatar',
            description: 'Animated BigHead character with lip sync and expressions'
        }
    },

    currentMode: 'eyes',
    container: null,
    audioContext: null,
    analyser: null,
    animationFrame: null,

    // Orb-specific state
    orb: {
        canvas: null,
        ctx: null,
        particles: [],
        baseRadius: 80,
        pulsePhase: 0
    },

    init() {
        this.container = document.querySelector('.face-box');
        if (!this.container) {
            console.warn('FaceRenderer: .face-box container not found');
            return;
        }

        // Load saved mode from server profile (shared across devices)
        const savedMode = window._serverProfile?.ui?.face_mode;
        if (savedMode && this.modes[savedMode]) {
            this.currentMode = savedMode;
        }

        // Listen for theme changes
        window.addEventListener('themeChanged', (e) => {
            if (this.currentMode === 'orb') {
                this.updateOrbColors(e.detail);
            }
        });

        // Initial render
        this.render();
    },

    setMode(modeName) {
        if (!this.modes[modeName]) {
            console.warn('Unknown face mode:', modeName);
            return;
        }

        // Clean up current mode
        this.cleanup();

        this.currentMode = modeName;
        // Persist to server profile
        const profileId = window.providerManager?._activeProfileId || 'default';
        fetch('/api/profiles/' + profileId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ui: { face_mode: modeName } })
        }).catch(e => console.warn('Failed to save face mode:', e));
        if (window._serverProfile) {
            if (!window._serverProfile.ui) window._serverProfile.ui = {};
            window._serverProfile.ui.face_mode = modeName;
        }

        // Re-render
        this.render();

        // Dispatch event
        window.dispatchEvent(new CustomEvent('faceModeChanged', { detail: modeName }));
    },

    cleanup() {
        if (this.animationFrame) {
            cancelAnimationFrame(this.animationFrame);
            this.animationFrame = null;
        }

        // Remove orb canvas if exists
        if (this.orb.canvas && this.orb.canvas.parentNode) {
            this.orb.canvas.parentNode.removeChild(this.orb.canvas);
            this.orb.canvas = null;
            this.orb.ctx = null;
        }

        // Stop halo-smoke face if running
        if (window.HaloSmokeFace) {
            window.HaloSmokeFace.stop();
        }

        // Stop bighead face if running
        if (window.BigHeadFace) {
            window.BigHeadFace.stop();
        }
    },

    render() {
        switch (this.currentMode) {
            case 'eyes':
                this.renderEyes();
                break;
            case 'orb':
                this.renderOrb();
                break;
            case 'halo-smoke':
                this.renderHaloSmoke();
                break;
            case 'bighead':
                this.renderBigHead();
                break;
        }
    },

    renderEyes() {
        // Show existing eyes, hide orb
        const eyesContainer = this.container.querySelector('.eyes-container');
        if (eyesContainer) {
            eyesContainer.style.display = 'flex';
        }

        // Remove orb canvas if present
        this.cleanup();
    },

    renderOrb() {
        // Hide existing eyes
        const eyesContainer = this.container.querySelector('.eyes-container');
        if (eyesContainer) {
            eyesContainer.style.display = 'none';
        }

        // Create orb canvas
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

        // Set canvas size
        const size = Math.min(this.container.offsetWidth, this.container.offsetHeight) * 0.6;
        this.orb.canvas.width = size;
        this.orb.canvas.height = size;

        // Initialize particles
        this.initOrbParticles();

        // Start animation
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

        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Get audio data if available
        let audioLevel = 0;
        if (window.audioAnalyser) {
            const dataArray = new Uint8Array(window.audioAnalyser.frequencyBinCount);
            window.audioAnalyser.getByteFrequencyData(dataArray);
            // Average of bass frequencies
            audioLevel = dataArray.slice(0, 10).reduce((a, b) => a + b, 0) / 10 / 255;
        }

        // Get theme colors
        const theme = window.ThemeManager?.getCurrentTheme() || {};
        const primaryColor = theme.primary || '#0088ff'; // fallback matches --blue
        const accentColor = theme.accent || '#00ffff'; // fallback matches --cyan

        // Pulsing effect
        this.orb.pulsePhase += 0.02;
        const pulse = Math.sin(this.orb.pulsePhase) * 0.1 + 1;
        const audioPulse = 1 + audioLevel * 0.5;
        const baseRadius = this.orb.baseRadius * pulse * audioPulse;

        // Draw outer glow
        const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, baseRadius * 1.5);
        gradient.addColorStop(0, this.hexToRgba(primaryColor, 0.3));
        gradient.addColorStop(0.5, this.hexToRgba(accentColor, 0.1));
        gradient.addColorStop(1, 'transparent');

        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius * 1.5, 0, Math.PI * 2);
        ctx.fillStyle = gradient;
        ctx.fill();

        // Draw core orb
        const coreGradient = ctx.createRadialGradient(
            centerX - baseRadius * 0.3,
            centerY - baseRadius * 0.3,
            0,
            centerX,
            centerY,
            baseRadius
        );
        coreGradient.addColorStop(0, this.hexToRgba(accentColor, 0.8));
        coreGradient.addColorStop(0.5, this.hexToRgba(primaryColor, 0.5));
        coreGradient.addColorStop(1, this.hexToRgba(primaryColor, 0.2));

        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius, 0, Math.PI * 2);
        ctx.fillStyle = coreGradient;
        ctx.fill();

        // Draw border
        ctx.strokeStyle = this.hexToRgba(accentColor, 0.6);
        ctx.lineWidth = 2;
        ctx.stroke();

        // Animate particles
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

        // Draw inner highlight
        const highlightGradient = ctx.createRadialGradient(
            centerX - baseRadius * 0.4,
            centerY - baseRadius * 0.4,
            0,
            centerX - baseRadius * 0.4,
            centerY - baseRadius * 0.4,
            baseRadius * 0.5
        );
        highlightGradient.addColorStop(0, 'rgba(255, 255, 255, 0.4)');
        highlightGradient.addColorStop(1, 'transparent');

        ctx.beginPath();
        ctx.arc(centerX - baseRadius * 0.3, centerY - baseRadius * 0.3, baseRadius * 0.4, 0, Math.PI * 2);
        ctx.fillStyle = highlightGradient;
        ctx.fill();

        // Continue animation
        this.animationFrame = requestAnimationFrame(() => this.animateOrb());
    },

    renderHaloSmoke() {
        if (!window.HaloSmokeFace) {
            console.warn('[FaceRenderer] HaloSmokeFace not loaded — add src/face/HaloSmokeFace.js to index.html');
            return;
        }
        // HaloSmokeFace.start() handles hiding eyes, removing old canvases, etc.
        window.HaloSmokeFace.start(this.container);
    },

    renderBigHead() {
        if (!window.BigHeadFace) {
            console.warn('[FaceRenderer] BigHeadFace not loaded — add src/face/BigHeadFace.js to index.html');
            return;
        }
        window.BigHeadFace.start(this.container);
    },

    hexToRgba(hex, alpha) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        if (!result) return `rgba(0, 136, 255, ${alpha})`;
        return `rgba(${parseInt(result[1], 16)}, ${parseInt(result[2], 16)}, ${parseInt(result[3], 16)}, ${alpha})`;
    },

    updateOrbColors(colors) {
        // Colors will be picked up in next animation frame
    },

    getCurrentMode() {
        return this.currentMode;
    },

    getAvailableModes() {
        return Object.keys(this.modes).map(key => ({
            id: key,
            ...this.modes[key]
        }));
    }
};
