/**
 * PartyFXVisualizer - Music-reactive party effects visualizer
 * Extracted from index.html with performance optimizations.
 *
 * Effects: center glow, beat flash, screen shake, ripples, oscilloscope,
 *          orbiting particles, disco dots, equalizer bars (top/bottom/left/right)
 *
 * Implements the BaseVisualizer interface (window.VisualizerModule).
 */

window.VisualizerModule = {
    name: 'Party FX',
    description: 'Full party effects with beat detection, particles, disco dots, and equalizer bars',

    // Audio analysis
    audioContext: null,
    analyser: null,
    sourceNode1: null,
    sourceNode2: null,
    frequencyData: null,
    timeDomainData: null,
    animationId: null,

    // State
    enabled: localStorage.getItem('visualizerEnabled') !== 'false',
    autoplayEnabled: localStorage.getItem('musicAutoplay') === 'true',
    currentPlaylist: 'library',

    // Beat detection
    bassHistory: [],
    prevBassLevel: 0,
    lastBeatTime: 0,
    beatCooldown: 50,
    audioSensitivity: 1.5,

    // Frequency band ranges (for 2048 FFT at 44100Hz)
    BANDS: {
        subBass: { start: 0, end: 4 },
        bass: { start: 4, end: 12 },
        lowMid: { start: 12, end: 24 },
        mid: { start: 24, end: 92 },
        highMid: { start: 92, end: 186 },
        treble: { start: 186, end: 1024 }
    },

    // Party effects
    discoDots: [],
    partyParticles: [],

    // Constants
    NUM_BARS: 25,
    RIPPLE_POOL_SIZE: 6,

    // --- Cached DOM refs (populated in init) ---
    _dom: null,
    // Cached bar arrays with pre-parsed multipliers
    _vizBars: null,    // [{el, mult}]
    _sideBars: null,   // [{el, mult}]
    // Cached viz/side container elements for toggling .active
    _vizContainers: null,
    // Oscilloscope canvas cached dimensions
    _oscW: 0,
    _oscH: 0,
    // Ripple pool
    _ripplePool: null,
    _rippleIndex: 0,
    // Track if containers are already active (avoid redundant classList ops)
    _containersActive: false,

    async init() {
        console.log('PartyFXVisualizer initializing...');

        // Cache all DOM element references once
        this._dom = {
            partyContainer: document.getElementById('party-effects-container'),
            centerGlow: document.getElementById('center-glow'),
            beatFlash: document.getElementById('beat-flash'),
            faceBox: document.getElementById('face-box'),
            rippleContainer: document.getElementById('ripple-container'),
            oscContainer: document.getElementById('oscilloscope-container'),
            oscCanvas: document.getElementById('oscilloscope-canvas'),
            oscCtx: null,
            audio1: document.getElementById('music-player'),
            audio2: document.getElementById('music-player-2'),
        };

        // Cache oscilloscope canvas context and size
        if (this._dom.oscCanvas) {
            this._dom.oscCtx = this._dom.oscCanvas.getContext('2d');
            this._updateCanvasSize();
            window.addEventListener('resize', () => this._updateCanvasSize());
        }

        this.createVisualizerBars();
        this.initPartyEffects();
        this._initRipplePool();
        this.updateToggleUI();

        // Cache the viz/side containers for .active toggling
        this._vizContainers = document.querySelectorAll('.visualizer-container, .side-visualizer');

        // Add ended listener for autoplay
        if (this._dom.audio1) this._dom.audio1.addEventListener('ended', () => this.onTrackEnded());
        if (this._dom.audio2) this._dom.audio2.addEventListener('ended', () => this.onTrackEnded());

        // Listen for face mode changes — hide/show square bars dynamically
        window.addEventListener('faceModeChanged', (e) => {
            const isHalo = e.detail === 'halo-smoke';
            if (this._vizContainers) {
                this._vizContainers.forEach(el => {
                    if (isHalo) {
                        el.classList.remove('active');
                    } else if (this._containersActive) {
                        el.classList.add('active');
                    }
                });
            }
        });

        // Expose global toggle functions for HTML onclick handlers
        window.toggleAutoplay = (enabled) => this.setAutoplay(enabled);
        window.toggleVisualizer = (enabled) => this.setEnabled(enabled);
        window.switchPlaylist = (playlist) => {
            this.currentPlaylist = playlist;
            if (window.musicPlayer && window.musicPlayer.switchPlaylist) {
                window.musicPlayer.switchPlaylist(playlist);
            }
        };

        console.log('PartyFXVisualizer ready, enabled:', this.enabled);
    },

    _updateCanvasSize() {
        const canvas = this._dom.oscCanvas;
        if (!canvas) return;
        this._oscW = canvas.offsetWidth * 2;
        this._oscH = canvas.offsetHeight * 2;
        canvas.width = this._oscW;
        canvas.height = this._oscH;
    },

    _initRipplePool() {
        const container = this._dom.rippleContainer;
        if (!container) return;
        this._ripplePool = [];
        for (let i = 0; i < this.RIPPLE_POOL_SIZE; i++) {
            const ripple = document.createElement('div');
            ripple.className = 'sound-ripple';
            ripple.style.left = '50%';
            ripple.style.top = '50%';
            ripple.style.transform = 'translate(-50%, -50%)';
            ripple.style.display = 'none';
            container.appendChild(ripple);
            this._ripplePool.push(ripple);
        }
        this._rippleIndex = 0;
    },

    onTrackEnded() {
        if (this.autoplayEnabled && window.musicPlayer) {
            console.log('Autoplaying next track...');
            window.musicPlayer.play();
        }
    },

    updateToggleUI() {
        const autoplayToggle = document.getElementById('autoplay-toggle');
        const visualizerToggle = document.getElementById('visualizer-toggle');
        const autoplayCheckbox = document.getElementById('autoplay-checkbox');
        const visualizerCheckbox = document.getElementById('visualizer-checkbox');

        if (autoplayCheckbox) autoplayCheckbox.checked = this.autoplayEnabled;
        if (visualizerCheckbox) visualizerCheckbox.checked = this.enabled;
        if (autoplayToggle) autoplayToggle.classList.toggle('enabled', this.autoplayEnabled);
        if (visualizerToggle) visualizerToggle.classList.toggle('enabled', this.enabled);
    },

    async setupAnalyser() {
        if (!this.enabled) return;

        try {
            if (!this.audioContext) {
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }

            if (!this.analyser) {
                this.analyser = this.audioContext.createAnalyser();
                this.analyser.fftSize = 2048;
                this.analyser.smoothingTimeConstant = 0.8;
                this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
                this.timeDomainData = new Uint8Array(this.analyser.fftSize);
            }

            if (!this.sourceNode1 && this._dom.audio1) {
                this.sourceNode1 = this.audioContext.createMediaElementSource(this._dom.audio1);
                this.sourceNode1.connect(this.analyser);
            }
            if (!this.sourceNode2 && this._dom.audio2) {
                this.sourceNode2 = this.audioContext.createMediaElementSource(this._dom.audio2);
                this.sourceNode2.connect(this.analyser);
            }

            this.analyser.connect(this.audioContext.destination);
            console.log('Music analyser connected');
        } catch (e) {
            console.error('Visualizer analyser error:', e.message);
        }
    },

    createVisualizerBars() {
        const topViz = document.getElementById('top-viz');
        const bottomViz = document.getElementById('bottom-viz');
        const leftViz = document.getElementById('left-viz');
        const rightViz = document.getElementById('right-viz');

        if (!topViz || !bottomViz || !leftViz || !rightViz) return;

        topViz.innerHTML = '';
        bottomViz.innerHTML = '';
        leftViz.innerHTML = '';
        rightViz.innerHTML = '';

        this._vizBars = [];
        this._sideBars = [];

        for (let i = 0; i < this.NUM_BARS; i++) {
            const distFromCenter = Math.abs(i - (this.NUM_BARS - 1) / 2) / ((this.NUM_BARS - 1) / 2);
            const centerMultiplier = 1 - (distFromCenter * 0.6);

            const topBar = document.createElement('div');
            topBar.className = 'visualizer-bar';
            topBar.style.height = '10px';
            topViz.appendChild(topBar);
            this._vizBars.push({ el: topBar, mult: centerMultiplier });

            const bottomBar = document.createElement('div');
            bottomBar.className = 'visualizer-bar';
            bottomBar.style.height = '10px';
            bottomViz.appendChild(bottomBar);
            this._vizBars.push({ el: bottomBar, mult: centerMultiplier });

            const leftBar = document.createElement('div');
            leftBar.className = 'side-bar';
            leftBar.style.width = '20px';
            leftViz.appendChild(leftBar);
            this._sideBars.push({ el: leftBar, mult: centerMultiplier });

            const rightBar = document.createElement('div');
            rightBar.className = 'side-bar';
            rightBar.style.width = '20px';
            rightViz.appendChild(rightBar);
            this._sideBars.push({ el: rightBar, mult: centerMultiplier });
        }
    },

    initPartyEffects() {
        const particleContainer = document.getElementById('party-particle-container');
        if (particleContainer) {
            particleContainer.innerHTML = '';
            this.partyParticles = [];
            for (let i = 0; i < 30; i++) {
                const particle = document.createElement('div');
                particle.className = 'party-particle';
                particle.style.left = Math.random() * 100 + '%';
                particle.style.top = Math.random() * 100 + '%';
                particleContainer.appendChild(particle);
                this.partyParticles.push({
                    el: particle,
                    angle: (i / 30) * Math.PI * 2,
                    baseRadius: 100 + i * 10
                });
            }
        }

        const discoContainer = document.getElementById('disco-container');
        if (discoContainer) {
            discoContainer.innerHTML = '';
            this.discoDots = [];
            for (let i = 0; i < 40; i++) {
                const dot = document.createElement('div');
                dot.className = 'disco-dot';
                const baseX = Math.random() * 100;
                const baseY = Math.random() * 100;
                dot.style.left = baseX + '%';
                dot.style.top = baseY + '%';
                discoContainer.appendChild(dot);
                this.discoDots.push({
                    el: dot,
                    baseX: baseX,
                    baseY: baseY,
                    angle: Math.random() * Math.PI * 2,
                    speed: 0.5 + Math.random() * 1.5,
                    orbitRadius: 5 + Math.random() * 15,
                    hueOffset: Math.random() * 360,
                    lastShadowSize: 0
                });
            }
        }
    },

    getBandLevel(band) {
        if (!this.frequencyData) return 0;
        let sum = 0;
        const count = band.end - band.start;
        for (let i = band.start; i < band.end; i++) {
            sum += this.frequencyData[i];
        }
        return (sum / count / 255) * this.audioSensitivity;
    },

    detectBeat(bassLevel) {
        const now = Date.now();
        this.bassHistory.push(bassLevel);
        if (this.bassHistory.length > 8) this.bassHistory.shift();

        const bassAvg = this.bassHistory.reduce((a, b) => a + b, 0) / this.bassHistory.length;
        const peakRatio = bassAvg > 0.05 ? (bassLevel / bassAvg) : 1;
        const isAboveAvg = peakRatio > 1.15;
        const isStrongEnough = bassLevel > 0.12;
        const cooldownPassed = (now - this.lastBeatTime) > this.beatCooldown;
        const spike = bassLevel - this.prevBassLevel;
        const isSharpSpike = spike > 0.05 && bassLevel > 0.1;

        this.prevBassLevel = bassLevel;

        const isBeat = cooldownPassed && ((isAboveAvg && isStrongEnough) || isSharpSpike);
        if (isBeat) this.lastBeatTime = now;
        return isBeat;
    },

    startAnimation() {
        if (this.animationId) return;
        this._containersActive = false;
        this.animate();
    },

    stopAnimation() {
        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
            this.animationId = null;
        }
        this._containersActive = false;
        if (this._dom) {
            if (this._dom.partyContainer) this._dom.partyContainer.classList.remove('active');
            if (this._dom.oscContainer) this._dom.oscContainer.classList.remove('active');
        }
        if (this._vizContainers) {
            this._vizContainers.forEach(el => el.classList.remove('active'));
        }
    },

    animate() {
        if (!this.enabled) {
            this.stopAnimation();
            return;
        }

        const musicPlayer = window.musicPlayer;
        if (!musicPlayer || !musicPlayer.isPlaying) {
            this.stopAnimation();
            return;
        }

        // Show effects containers (once, not every frame)
        if (!this._containersActive) {
            if (this._dom.partyContainer) this._dom.partyContainer.classList.add('active');
            // Only show square-frame bars when NOT in halo-smoke mode
            const isHaloMode = this._dom.faceBox && this._dom.faceBox.classList.contains('halo-smoke-mode');
            if (this._vizContainers && !isHaloMode) {
                this._vizContainers.forEach(el => el.classList.add('active'));
            }
            this._containersActive = true;
        }

        // Setup analyser if needed
        if (!this.sourceNode1 && this.enabled) {
            this.setupAnalyser();
        }

        // Get frequency data (single read, shared by all effects)
        if (this.analyser && this.frequencyData) {
            this.analyser.getByteFrequencyData(this.frequencyData);
            if (this.timeDomainData) {
                this.analyser.getByteTimeDomainData(this.timeDomainData);
            }
        }

        // Extract frequency bands
        const bass = this.getBandLevel(this.BANDS.bass);
        const subBass = this.getBandLevel(this.BANDS.subBass);
        const lowMid = this.getBandLevel(this.BANDS.lowMid);
        const mid = this.getBandLevel(this.BANDS.mid);
        const highMid = this.getBandLevel(this.BANDS.highMid);
        const treble = this.getBandLevel(this.BANDS.treble);
        const fullBass = (bass + subBass) / 2;
        const energy = (bass * 2 + lowMid + mid + highMid + treble) / 6;

        const time = Date.now() / 1000;
        const isBeat = this.detectBeat(fullBass);

        const useBass = fullBass;
        const useMid = mid;
        const useEnergy = energy;
        const useHighMid = highMid;

        // Update all effects
        this.updateCenterGlow(useBass, useEnergy, useMid, isBeat);

        if (isBeat) {
            this.triggerBeatFlash(useBass);
            this.triggerShake(useBass);
            this.triggerSoundRipple(useBass);
        }

        this.updateOscilloscope();
        this.updateParticles(useBass, useEnergy, time);
        this.updateDiscoDots(useEnergy, useHighMid, useBass, useMid, time, isBeat);
        this.updateVisualizerBars();

        this.animationId = requestAnimationFrame(() => this.animate());
    },

    updateCenterGlow(useBass, useEnergy, useMid, isBeat) {
        const glow = this._dom.centerGlow;
        if (!glow) return;

        const size = 600 + useBass * 800;
        const hue = 180 + useEnergy * 60 + useMid * 40;
        const saturation = 80 + useBass * 20;
        const lightness = 50;
        const opacity = 0.3 + useEnergy * 0.7;

        glow.style.width = size + 'px';
        glow.style.height = size + 'px';
        glow.style.background = `radial-gradient(circle,
            hsla(${hue}, ${saturation}%, ${lightness}%, ${opacity * 0.4}) 0%,
            hsla(${hue - 30}, ${saturation}%, ${lightness - 20}%, ${opacity * 0.15}) 40%,
            transparent 70%)`;
        glow.style.opacity = opacity;

        if (isBeat) {
            glow.style.filter = 'blur(40px)';
            setTimeout(() => glow.style.filter = 'blur(60px)', 100);
        }
    },

    triggerBeatFlash(useBass) {
        const flash = this._dom.beatFlash;
        if (!flash) return;
        flash.style.opacity = Math.min(useBass * 0.6, 0.4);
        setTimeout(() => flash.style.opacity = 0, 80);
    },

    triggerShake(useBass) {
        const faceBox = this._dom.faceBox;
        if (!faceBox) return;
        faceBox.classList.add('shake');
        faceBox.style.setProperty('--shake-amount', (useBass * 8) + 'px');
        setTimeout(() => faceBox.classList.remove('shake'), 100);
    },

    triggerSoundRipple(useBass) {
        if (useBass < 0.15) return;
        if (!this._ripplePool) return;

        // Reuse pooled ripple element
        const ripple = this._ripplePool[this._rippleIndex];
        this._rippleIndex = (this._rippleIndex + 1) % this.RIPPLE_POOL_SIZE;

        // Reset animation by removing and re-adding
        ripple.style.display = 'none';
        // Force reflow on single small element (negligible cost)
        ripple.offsetHeight;
        ripple.style.display = '';
    },

    updateOscilloscope() {
        const ctx = this._dom.oscCtx;
        if (!ctx) return;

        const oscContainer = this._dom.oscContainer;
        if (oscContainer && !oscContainer.classList.contains('active')) {
            oscContainer.classList.add('active');
        }

        const w = this._oscW;
        const h = this._oscH;
        if (w === 0 || h === 0) return;

        ctx.clearRect(0, 0, w, h);

        // timeDomainData already read in animate() — no duplicate read
        if (!this.analyser || !this.timeDomainData) return;

        const sampleStep = 16;
        const numSamples = Math.floor(this.timeDomainData.length / sampleStep);
        const sliceWidth = w / numSamples;
        const halfH = h / 2;
        const ampScale = h * 0.35;

        // Draw glow layer
        ctx.beginPath();
        ctx.strokeStyle = 'rgba(0, 255, 255, 0.25)';
        ctx.lineWidth = 20;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';

        let x = 0;
        for (let i = 0; i < numSamples; i++) {
            const v = this.timeDomainData[i * sampleStep] / 128.0;
            const y = halfH + (v - 1) * ampScale;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
            x += sliceWidth;
        }
        ctx.stroke();

        // Draw main bright line
        ctx.beginPath();
        ctx.strokeStyle = '#00ffff';
        ctx.lineWidth = 6;
        x = 0;

        for (let i = 0; i < numSamples; i++) {
            const v = this.timeDomainData[i * sampleStep] / 128.0;
            const y = halfH + (v - 1) * ampScale;
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
            x += sliceWidth;
        }
        ctx.stroke();
    },

    updateParticles(useBass, useEnergy, time) {
        const opacity = 0.3 + useEnergy * 0.7;
        const size = 4 + useEnergy * 8;

        this.partyParticles.forEach((p, i) => {
            const radius = p.baseRadius + useBass * 200;
            const orbitSpeed = 0.3 + (i % 5) * 0.1;
            const px = 50 + Math.cos(time * orbitSpeed + p.angle) * (radius / 10);
            const py = 50 + Math.sin(time * orbitSpeed * 0.7 + p.angle) * (radius / 15);

            const el = p.el;
            el.style.left = px + '%';
            el.style.top = py + '%';
            el.style.width = size + 'px';
            el.style.height = size + 'px';
            el.style.opacity = opacity;
        });
    },

    updateDiscoDots(useEnergy, useHighMid, useBass, useMid, time, isBeat) {
        const centerX = 50, centerY = 50;
        const opacity = 0.3 + useEnergy * 0.7;
        const shadowBase = 10 + useBass * 20;
        // Quantize shadow size to reduce repaint (round to nearest 3px)
        const quantizedShadow = Math.round(shadowBase / 3) * 3;

        this.discoDots.forEach((dot) => {
            const dynamicRadius = dot.orbitRadius * (0.5 + useEnergy * 2);
            const orbitX = Math.cos(time * dot.speed + dot.angle) * dynamicRadius;
            const orbitY = Math.sin(time * dot.speed * 0.8 + dot.angle) * dynamicRadius;

            const pullStrength = 1 - useEnergy;
            const targetX = dot.baseX + orbitX;
            const targetY = dot.baseY + orbitY;
            const x = targetX + (centerX - targetX) * pullStrength * 0.7;
            const y = targetY + (centerY - targetY) * pullStrength * 0.7;

            let size = 0.4 + useHighMid * 0.6 + useBass * 0.4;
            if (isBeat) size += 0.5;

            const hue = (dot.hueOffset + time * 30 + useMid * 60) % 360;
            const hueRound = Math.round(hue);

            const el = dot.el;
            el.style.left = x + '%';
            el.style.top = y + '%';
            el.style.transform = `scale(${size})`;
            el.style.opacity = opacity;
            el.style.background = `hsl(${hueRound}, 100%, 70%)`;

            // Only update expensive boxShadow when size changes meaningfully
            if (quantizedShadow !== dot.lastShadowSize) {
                el.style.boxShadow = `0 0 ${quantizedShadow}px hsl(${hueRound}, 100%, 50%)`;
                dot.lastShadowSize = quantizedShadow;
            }
        });
    },

    updateVisualizerBars() {
        if (!this.frequencyData) return;

        // Skip bar updates when halo-smoke face is active (bars are hidden via CSS)
        if (this._dom.faceBox && this._dom.faceBox.classList.contains('halo-smoke-mode')) return;

        // Use cached bar arrays with pre-parsed multipliers
        const numBars = this.NUM_BARS;
        const sensitivity = this.audioSensitivity;
        const freqData = this.frequencyData;

        if (this._vizBars) {
            for (let i = 0; i < this._vizBars.length; i++) {
                const bar = this._vizBars[i];
                const bandIndex = Math.floor((i % numBars) / numBars * 256);
                const level = freqData[bandIndex] / 255 * sensitivity;
                bar.el.style.height = ((8 + level * 50) * bar.mult) + 'px';
            }
        }

        if (this._sideBars) {
            for (let i = 0; i < this._sideBars.length; i++) {
                const bar = this._sideBars[i];
                const bandIndex = Math.floor((i % numBars) / numBars * 256);
                const level = freqData[bandIndex] / 255 * sensitivity;
                bar.el.style.width = ((15 + level * 70) * bar.mult) + 'px';
            }
        }
    },

    setEnabled(enabled) {
        this.enabled = enabled;
        localStorage.setItem('visualizerEnabled', enabled);
        this.updateToggleUI();
        if (!enabled) this.stopAnimation();
    },

    setAutoplay(enabled) {
        this.autoplayEnabled = enabled;
        localStorage.setItem('musicAutoplay', enabled);
        this.updateToggleUI();
    }
};
