/**
 * TTSPlayer — Frontend TTS audio playback with waveform analysis
 *
 * Extracted from index.html (VoiceConversation.playTTS, ClawbotMode.playAudio,
 * startAnalyserAnimation, stopAnalyserAnimation, base64ToArrayBuffer/Blob helpers).
 *
 * Usage:
 *   import { TTSPlayer } from './providers/TTSPlayer.js';
 *   const player = new TTSPlayer();
 *   await player.init();
 *   player.onAmplitude = (value) => waveformModule.setAmplitude(value);
 *   player.onSpeakingChange = (isSpeaking) => { ... };
 *   await player.play(base64Audio);   // AudioContext path
 *   player.queue(base64Audio);        // Queue path (ClawbotMode style)
 *   player.stop();
 */
export class TTSPlayer {
    constructor() {
        // AudioContext-based path (used by VoiceConversation / direct TTS)
        this.audioContext = null;
        this.gainNode = null;
        this.analyser = null;
        this.analyserData = null;
        this.analyserAnimationId = null;
        this.currentSource = null;  // BufferSourceNode

        // Queue-based path (used by ClawbotMode for streamed chunks)
        this.audioQueue = [];
        this.currentAudio = null;   // HTMLAudioElement
        this.isPlaying = false;

        // Volume boost — iOS Safari web audio is quieter than native apps.
        // GainNode values > 1.0 amplify the signal. Default 1.5x on mobile, 1.0x desktop.
        this._isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
        this.gain = this._isMobile ? 1.8 : 1.0;

        // Callbacks
        this.onAmplitude = null;        // (value: 0-1) => void
        this.onSpeakingChange = null;   // (isSpeaking: boolean) => void
    }

    /**
     * Initialize AudioContext and analyser.
     * Must be called after a user gesture on some browsers.
     */
    async init() {
        if (this.audioContext) return;

        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

        // GainNode for volume boost (especially on mobile where web audio is quieter)
        this.gainNode = this.audioContext.createGain();
        this.gainNode.gain.value = this.gain;

        this.analyser = this.audioContext.createAnalyser();
        this.analyser.fftSize = 256;
        this.analyser.smoothingTimeConstant = 0.3;
        this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);

        // Chain: source → gainNode → analyser → destination
        this.gainNode.connect(this.analyser);
        this.analyser.connect(this.audioContext.destination);
    }

    /**
     * Set TTS volume gain (1.0 = normal, 2.0 = 2x boost).
     * Values above 1.0 amplify the signal for quieter devices.
     */
    setGain(value) {
        this.gain = Math.max(0, Math.min(3.0, value));
        if (this.gainNode) {
            this.gainNode.gain.value = this.gain;
        }
    }

    /**
     * Ensure AudioContext is resumed (needed after iOS/browser auto-suspend).
     */
    async ensureRunning() {
        if (!this.audioContext) await this.init();
        if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
        }
    }

    // -------------------------------------------------------------------------
    // AudioContext path — play base64 audio directly via decodeAudioData
    // -------------------------------------------------------------------------

    /**
     * Decode and play a base64-encoded WAV/MP3 via AudioContext.
     * Drives the waveform analyser animation while playing.
     * @param {string} audioBase64
     * @returns {Promise<void>} Resolves when playback ends
     */
    async play(audioBase64) {
        await this.ensureRunning();
        this._notifySpeaking(true);

        return new Promise((resolve) => {
            try {
                const arrayBuffer = this._base64ToArrayBuffer(audioBase64);

                this.audioContext.decodeAudioData(arrayBuffer, (audioBuffer) => {
                    // Stop previous source if any
                    if (this.currentSource) {
                        try { this.currentSource.stop(); } catch (_) {}
                    }

                    const source = this.audioContext.createBufferSource();
                    source.buffer = audioBuffer;
                    source.connect(this.gainNode);

                    source.onended = () => {
                        this._stopAnalyserAnimation();
                        if (this.onAmplitude) this.onAmplitude(0);
                        this._notifySpeaking(false);
                        this.currentSource = null;
                        resolve();
                    };

                    this.currentSource = source;
                    source.start(0);
                    this._startAnalyserAnimation();

                }, (err) => {
                    console.error('[TTSPlayer] decodeAudioData failed:', err);
                    this._notifySpeaking(false);
                    resolve();
                });

            } catch (error) {
                console.error('[TTSPlayer] play() failed:', error);
                this._notifySpeaking(false);
                resolve();
            }
        });
    }

    // -------------------------------------------------------------------------
    // Queue path — for streaming/chunked audio (HTMLAudioElement-based)
    // -------------------------------------------------------------------------

    /**
     * Add a base64-encoded audio chunk to the queue and start playing if idle.
     * @param {string} base64Audio
     * @param {string} [mimeType='audio/wav']
     */
    queue(base64Audio, mimeType = 'audio/wav', gapMs = 0) {
        try {
            const blob = this._base64ToBlob(base64Audio, mimeType);
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audio._gapMs = gapMs || 0;  // inter-sentence gap from profile

            audio.onended = () => {
                URL.revokeObjectURL(url);
                if (audio._gapMs > 0 && this.audioQueue.length > 0) {
                    setTimeout(() => this._playNext(), audio._gapMs);
                } else {
                    this._playNext();
                }
            };

            audio.onerror = (e) => {
                console.error('[TTSPlayer] Audio element error:', e);
                URL.revokeObjectURL(url);
                this._playNext();
            };

            this.audioQueue.push(audio);

            if (!this.isPlaying) {
                this._playNext();
            }
        } catch (error) {
            console.error('[TTSPlayer] queue() failed:', error);
        }
    }

    _playNext() {
        if (this.audioQueue.length === 0) {
            this.currentAudio = null;
            this.isPlaying = false;
            if (this.onAmplitude) this.onAmplitude(0);
            this._notifySpeaking(false);
            return;
        }

        this.currentAudio = this.audioQueue.shift();
        this.isPlaying = true;
        this._notifySpeaking(true);

        // Route HTMLAudioElement through AudioContext gain for volume boost
        if (this.gainNode && this.audioContext) {
            try {
                if (!this.currentAudio._mediaSource) {
                    this.currentAudio._mediaSource = this.audioContext.createMediaElementSource(this.currentAudio);
                    this.currentAudio._mediaSource.connect(this.gainNode);
                }
            } catch (e) {
                // Fallback: if AudioContext routing fails, just play directly
                console.warn('[TTSPlayer] MediaElementSource fallback:', e.message);
            }
        }

        const promise = this.currentAudio.play();
        if (promise) {
            promise.catch(err => {
                console.error('[TTSPlayer] Audio play blocked:', err.message);
                this._playNext();
            });
        }
    }

    // -------------------------------------------------------------------------
    // Stop / clear
    // -------------------------------------------------------------------------

    /**
     * Stop all current and queued audio.
     */
    stop() {
        // Stop AudioContext source
        if (this.currentSource) {
            try { this.currentSource.stop(); } catch (_) {}
            this.currentSource = null;
        }
        this._stopAnalyserAnimation();

        // Stop queue-based audio
        if (this.currentAudio) {
            this.currentAudio.pause();
            this.currentAudio = null;
        }
        this.audioQueue = [];
        this.isPlaying = false;

        if (this.onAmplitude) this.onAmplitude(0);
        this._notifySpeaking(false);
    }

    /**
     * Release AudioContext resources.
     */
    destroy() {
        this.stop();
        if (this.audioContext) {
            this.audioContext.close().catch(() => {});
            this.audioContext = null;
            this.analyser = null;
            this.analyserData = null;
        }
    }

    // -------------------------------------------------------------------------
    // Waveform analyser animation
    // -------------------------------------------------------------------------

    _startAnalyserAnimation() {
        if (this.analyserAnimationId) return;

        const tick = () => {
            if (!this.analyser) {
                this.analyserAnimationId = null;
                return;
            }

            this.analyser.getByteFrequencyData(this.analyserData);

            // Average voice-range frequencies (lower 60% of bins)
            const voiceRange = Math.floor(this.analyserData.length * 0.6);
            let sum = 0;
            for (let i = 0; i < voiceRange; i++) {
                sum += this.analyserData[i];
            }
            const average = sum / voiceRange;
            const normalized = average / 255;

            // Boost so the mouth animation is visibly active
            const boosted = normalized > 0.05
                ? Math.max(0.3, normalized * 2.5)
                : 0;

            if (this.onAmplitude) this.onAmplitude(Math.min(1, boosted));

            this.analyserAnimationId = requestAnimationFrame(tick);
        };

        tick();
    }

    _stopAnalyserAnimation() {
        if (this.analyserAnimationId) {
            cancelAnimationFrame(this.analyserAnimationId);
            this.analyserAnimationId = null;
        }
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    _notifySpeaking(isSpeaking) {
        if (this.onSpeakingChange) this.onSpeakingChange(isSpeaking);
    }

    _base64ToArrayBuffer(base64) {
        const binary = atob(base64);
        const bytes = new Uint8Array(binary.length);
        for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
        }
        return bytes.buffer;
    }

    _base64ToBlob(base64, mimeType) {
        const byteCharacters = atob(base64);
        const byteNumbers = new Array(byteCharacters.length);
        for (let i = 0; i < byteCharacters.length; i++) {
            byteNumbers[i] = byteCharacters.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);
        return new Blob([byteArray], { type: mimeType });
    }
}

export default TTSPlayer;
