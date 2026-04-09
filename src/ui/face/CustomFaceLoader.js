/**
 * CustomFaceLoader — iframe bridge for custom HTML faces.
 *
 * Loads a custom face HTML page into an iframe inside .face-box,
 * and bridges mood/amplitude/theme/speaking data via postMessage.
 *
 * Custom faces can't access window.audioAnalyser (different browsing context),
 * so this loader runs a rAF loop to sample audio and forward amplitude.
 *
 * Usage (called by FaceRenderer handler):
 *   CustomFaceLoader.start(container, 'my-robot');
 *   CustomFaceLoader.setMood('happy');
 *   CustomFaceLoader.stop();
 */

window.CustomFaceLoader = {
    _iframe: null,
    _currentFaceId: null,
    _ampRaf: null,
    _messageHandler: null,
    _ready: false,
    _container: null,

    /**
     * Load a custom face into the face-box container.
     * @param {HTMLElement} container  .face-box element
     * @param {string} faceId  face slug (without custom: prefix)
     * @param {object} [config]  optional face-specific config
     */
    start(container, faceId, config) {
        this.stop();
        this._container = container;
        this._currentFaceId = faceId;
        this._ready = false;

        // Hide built-in face elements
        container.classList.add('custom-face-mode');

        // Create iframe
        const iframe = document.createElement('iframe');
        iframe.className = 'custom-face-iframe';
        iframe.src = `/faces/custom/${encodeURIComponent(faceId)}.html`;
        iframe.sandbox = 'allow-scripts allow-same-origin';
        iframe.setAttribute('loading', 'eager');
        iframe.setAttribute('title', 'Custom face: ' + faceId);
        container.appendChild(iframe);
        this._iframe = iframe;

        // Listen for messages from the face iframe
        this._messageHandler = (e) => {
            // Only accept messages from our iframe
            if (!this._iframe || e.source !== this._iframe.contentWindow) return;
            const d = e.data;
            if (!d || !d.type) return;

            switch (d.type) {
                case 'face:ready':
                    this._ready = true;
                    this._sendTheme();
                    this._sendMood();
                    console.log(`[CustomFaceLoader] Face "${faceId}" ready`);
                    break;
                case 'face:meta':
                    // Face can report its own metadata
                    if (d.name || d.description) {
                        console.log(`[CustomFaceLoader] Face meta:`, d.name, d.description);
                    }
                    break;
            }
        };
        window.addEventListener('message', this._messageHandler);

        // Start amplitude polling loop
        this._startAmplitudeLoop();

        // Listen for theme changes
        this._themeHandler = (e) => this._sendTheme(e.detail);
        window.addEventListener('themeChanged', this._themeHandler);
    },

    /**
     * Stop and remove the custom face iframe.
     */
    stop() {
        if (this._ampRaf) {
            cancelAnimationFrame(this._ampRaf);
            this._ampRaf = null;
        }
        if (this._messageHandler) {
            window.removeEventListener('message', this._messageHandler);
            this._messageHandler = null;
        }
        if (this._themeHandler) {
            window.removeEventListener('themeChanged', this._themeHandler);
            this._themeHandler = null;
        }
        if (this._iframe && this._iframe.parentNode) {
            this._iframe.parentNode.removeChild(this._iframe);
        }
        this._iframe = null;
        this._currentFaceId = null;
        this._ready = false;

        // Restore built-in face elements
        if (this._container) {
            this._container.classList.remove('custom-face-mode');
            this._container = null;
        }
    },

    /**
     * Forward a mood change to the face iframe.
     * @param {string} mood
     */
    setMood(mood) {
        this._post({ type: 'face:mood', mood });
    },

    /**
     * Forward amplitude to the face iframe.
     * @param {number} value  0.0 to 1.0
     */
    setAmplitude(value) {
        this._post({ type: 'face:amplitude', value });
    },

    /**
     * Forward speaking state to the face iframe.
     * @param {boolean} speaking
     */
    setSpeaking(speaking) {
        this._post({ type: 'face:speaking', speaking });
    },

    /**
     * Forward agent state to the face iframe.
     * @param {string} agentState  speaking|listening|thinking|idle
     */
    setState(agentState) {
        this._post({ type: 'face:state', state: agentState });
    },

    // ── Internal ─────────────────────────────────────────────────────────────

    _post(msg) {
        if (this._iframe && this._ready) {
            try {
                this._iframe.contentWindow.postMessage(msg, '*');
            } catch (e) {
                // iframe may have been removed
            }
        }
    },

    _sendTheme(colors) {
        const theme = colors || window.ThemeManager?.getCurrentTheme() || {};
        this._post({
            type: 'face:theme',
            primary: theme.primary || '#0088ff',
            accent: theme.accent || '#00ffff',
        });
    },

    _sendMood() {
        const mood = window._serverProfile?.ui?.face_mood || 'neutral';
        this._post({ type: 'face:mood', mood });
    },

    /**
     * rAF loop that samples window.audioAnalyser and sends amplitude.
     * Runs at display refresh rate for smooth audio reactivity.
     */
    _startAmplitudeLoop() {
        const tick = () => {
            if (!this._iframe) return;

            if (window.audioAnalyser) {
                try {
                    const data = new Uint8Array(window.audioAnalyser.frequencyBinCount);
                    window.audioAnalyser.getByteFrequencyData(data);
                    // Average the lower frequencies (voice range)
                    const voiceRange = Math.min(data.length, Math.floor(data.length * 0.3));
                    let sum = 0;
                    for (let i = 0; i < voiceRange; i++) sum += data[i];
                    const amp = sum / voiceRange / 255;
                    // Only send if there's meaningful audio (reduces noise)
                    if (amp > 0.01) {
                        this._post({ type: 'face:amplitude', value: Math.min(1, amp * 2) });
                    } else {
                        this._post({ type: 'face:amplitude', value: 0 });
                    }
                } catch (e) {
                    // Analyser may not be ready
                }
            }

            this._ampRaf = requestAnimationFrame(tick);
        };
        this._ampRaf = requestAnimationFrame(tick);
    },
};
