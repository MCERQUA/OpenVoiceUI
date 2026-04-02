/**
 * WebSpeechSTT — Browser-native speech recognition provider (Web Speech API)
 * Free, no API keys needed.
 *
 * Usage:
 *   import { WebSpeechSTT, WakeWordDetector } from './WebSpeechSTT.js';
 *
 *   const stt = new WebSpeechSTT();
 *   stt.onResult = (text) => console.log('Heard:', text);
 *   await stt.start();
 */

// Detect iOS — affects mic stream lifetime and recognition restart timing
const _isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

// Post real STT errors to the server so session monitoring can track them.
// no-speech and aborted are normal Chrome behaviour — don't report those.
function _reportSTTError(error, message, source = 'stt') {
    try {
        fetch('/api/stt-events', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ error, message, provider: 'webspeech', source }),
        }).catch(() => {}); // fire-and-forget, never block STT
    } catch (_) {}
}

// ===== WEB SPEECH STT =====
// Browser-native speech recognition (free, no API keys needed)
class WebSpeechSTT {
    constructor() {
        this.recognition = null;
        this.isListening = false;
        this.onResult = null;
        this.onError = null;
        this.onListenFinal = null;   // Listen panel hook — called with each final transcript
        this.onInterim = null;       // Listen panel hook — interim text

        // Silence detection for continuous listening
        this.silenceTimer = null;
        this.silenceDelayMs = 1500; // 1.5s — balanced: 3.5s was too sluggish, 0ms cut off mid-sentence
        this.accumulatedText = '';
        this.isProcessing = false;

        // PTT support
        this._micMuted = false;
        this._pttHolding = false;

        // Keep mic stream alive during active listening (critical on iOS —
        // releasing and re-acquiring the stream can re-trigger permission prompts)
        this._micStream = null;

        // Store constructor ref — recognition instance is created on first start(),
        // NOT in constructor. Having two SpeechRecognition instances (even if only
        // one is started) causes Chrome to route audio incorrectly, breaking wake word.
        this._SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!this._SpeechRecognition) {
            console.warn('Web Speech API not supported in this browser');
        }
    }

    // Create the recognition instance on first use and wire up all handlers.
    // Called once from start(), then the instance persists forever.
    // Monkey-patches in app.js poll for stt.recognition and apply within 200ms.
    _ensureRecognition() {
        if (this.recognition) return true;
        if (!this._SpeechRecognition) return false;

        this.recognition = new this._SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';
        this.recognition.maxAlternatives = 1;

        this.recognition.onresult = (event) => {
            if (this.isProcessing) return;
            if (this._micMuted) return;  // PTT mode — mic should be silent

            // ANY result (interim or final) means the user is still speaking.
            // Reset the silence timer on every event so we never cut off mid-speech.
            if (this.silenceTimer) {
                clearTimeout(this.silenceTimer);
                this.silenceTimer = null;
            }

            let finalTranscript = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                }
            }

            if (finalTranscript.trim()) {
                // APPEND — user can speak across multiple Chrome final results
                this.accumulatedText = this.accumulatedText
                    ? this.accumulatedText + ' ' + finalTranscript.trim()
                    : finalTranscript.trim();
                console.log('STT Final:', finalTranscript, '| Accumulated:', this.accumulatedText);
                // Listen panel hook
                if (this.onListenFinal) this.onListenFinal(finalTranscript.trim());
            }

            // Start/restart silence timer — only fires when Chrome stops sending ANY results
            if (this.accumulatedText) {
                this.silenceTimer = setTimeout(() => {
                    const text = this.accumulatedText.trim();
                    // Filter out garbage: punctuation-only, single words under 3 chars
                    const meaningful = text.replace(/[^a-zA-Z0-9]/g, '');
                    if (text && meaningful.length >= 2 && !this.isProcessing) {
                        console.log('Sending to AI:', text);
                        this.isProcessing = true;
                        if (this.onResult) this.onResult(text);
                        this.accumulatedText = '';
                    } else if (text) {
                        console.log('STT filtered garbage:', text);
                        this.accumulatedText = '';
                    }
                }, this.silenceDelayMs);
            }
        };

        this.recognition.onerror = (event) => {
            if (event.error === 'no-speech' || event.error === 'aborted') {
                console.log('STT:', event.error, '(normal, will auto-restart)');
                return;
            }
            if (event.error === 'audio-capture') {
                console.error('STT: audio-capture — microphone hardware unavailable');
                _reportSTTError('audio-capture', 'Microphone hardware unavailable', 'stt');
                if (this.onError) this.onError('audio-capture');
                return;
            }
            console.error('STT Error:', event.error);
            _reportSTTError(event.error, `STT recognition error: ${event.error}`, 'stt');
            if (this.onError) this.onError(event.error);
        };

        this.recognition.onend = () => {
            if (this.isListening && !this.isProcessing && !this._micMuted) {
                const restartDelay = _isIOS ? 500 : 300;
                setTimeout(() => {
                    if (this.isListening && !this.isProcessing && !this._micMuted) {
                        try {
                            this.recognition.start();
                        } catch (e) {
                            // Already started
                        }
                    }
                }, restartDelay);
            }
        };

        console.log('STT: SpeechRecognition instance created');
        return true;
    }

    isSupported() {
        return !!this._SpeechRecognition;
    }

    async start() {
        if (this._micMuted) return false;
        if (!this._ensureRecognition()) {
            console.error('Speech recognition not supported');
            return false;
        }

        // Request mic permission and keep the stream alive.
        try {
            if (!this._micStream) {
                this._micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }
        } catch (e) {
            console.error('Mic access failed:', e.name, e.message);
            if (e.name === 'NotFoundError' || e.name === 'DevicesNotFoundError') {
                if (this.onError) this.onError('no-device');
            } else {
                if (this.onError) this.onError('not-allowed');
            }
            return false;
        }

        try {
            this.isListening = true;
            this.recognition.start();
            console.log('STT started');
            return true;
        } catch (e) {
            console.error('Failed to start STT:', e);
            this.isListening = false;
            return false;
        }
    }

    stop() {
        if (this.silenceTimer) {
            clearTimeout(this.silenceTimer);
            this.silenceTimer = null;
        }
        if (this.recognition) {
            this.isListening = false;
            this.isProcessing = false;
            this._micMuted = false;
            this._pttHolding = false;
            this.recognition.stop();
            console.log('STT stopped');
        }
        // Release the mic stream when fully stopped
        if (this._micStream) {
            this._micStream.getTracks().forEach(t => t.stop());
            this._micStream = null;
        }
    }

    resetProcessing() {
        this.isProcessing = false;
        this.accumulatedText = '';
    }

    /** Alias for mute() — VoiceConversation calls pause() during greeting. */
    pause() {
        this.mute();
    }

    /**
     * Mute STT immediately — called when TTS starts speaking.
     * Aborts the recognition engine entirely so it stops capturing mic audio.
     * This prevents speaker echo from being transcribed during TTS playback.
     * onend fires after abort but won't restart (isProcessing=true blocks it).
     * resume() will call recognition.start() when TTS is done.
     *
     * NOTE: abort() vs stop() — abort() discards in-flight results,
     * stop() finalizes them. We use abort() to discard TTS echo.
     * isProcessing=true alone is not enough — recognition keeps running
     * and physically captures speaker audio via mic when only flagged.
     */
    mute() {
        this.isProcessing = true;
        if (this.silenceTimer) {
            clearTimeout(this.silenceTimer);
            this.silenceTimer = null;
        }
        this.accumulatedText = '';
        if (this.recognition) {
            try { this.recognition.abort(); } catch (e) {}
        }
    }

    /**
     * Resume STT after TTS finishes — clears mute flag and explicitly
     * restarts the recognition engine (which may have stopped during mute).
     * Called by VoiceSession._resumeListening() after the settling delay.
     */
    resume() {
        this.isProcessing = false;
        this.accumulatedText = '';
        if (this.silenceTimer) {
            clearTimeout(this.silenceTimer);
            this.silenceTimer = null;
        }
        if (this.isListening && !this._micMuted) {
            try {
                this.recognition.start();
            } catch (e) {
                // Already running — fine
            }
        }
    }

    // --- PTT helpers (called from PTT code in app.js) ---

    /**
     * PTT activate — start listening for push-to-talk.
     * Called when user presses the PTT button.
     */
    pttActivate() {
        this._pttHolding = true;
        this._micMuted = false;
        this.isProcessing = false;
        this.accumulatedText = '';
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }

        // Start recognition fresh
        if (!this._ensureRecognition()) return;
        try {
            this.recognition.start();
        } catch (e) {
            // Already running — fine
        }
    }

    /**
     * PTT release — stop listening and force-send transcript.
     * Called when user releases the PTT button.
     */
    pttRelease() {
        this._pttHolding = false;
        this._micMuted = true;
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }

        // Check if Chrome already finalized text during the hold
        const immediate = this.accumulatedText.trim();
        if (immediate && this.onResult) {
            console.log('PTT release — sending:', immediate);
            this.isProcessing = true;
            this.onResult(immediate);
            this.accumulatedText = '';
        }

        // Stop recognition — Chrome finalizes any pending speech as isFinal
        // (muted state prevents onend restart)
        if (this.recognition) {
            try { this.recognition.stop(); } catch (e) {}
        }

        // If nothing was finalized during hold, wait for Chrome's post-stop results.
        // Chrome fires onresult with isFinal=true when recognition.stop() is called,
        // but the event is async. Give it time to arrive, then send.
        if (!immediate) {
            setTimeout(() => {
                if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
                const text = this.accumulatedText.trim();
                if (text && this.onResult) {
                    console.log('PTT release (delayed) — sending:', text);
                    this.isProcessing = true;
                    this.onResult(text);
                }
                this.accumulatedText = '';
            }, 400);
        }
    }

    /**
     * PTT mute — stop recognition and discard.
     * Called when PTT mode is toggled ON (mic off by default).
     */
    pttMute() {
        this._pttHolding = false;
        this._micMuted = true;
        this.isProcessing = true;
        this.accumulatedText = '';
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }

        if (this.recognition) {
            try { this.recognition.stop(); } catch (e) {}
        }
    }

    /**
     * PTT unmute — resume continuous listening.
     * Called when PTT mode is toggled OFF.
     */
    pttUnmute() {
        this._micMuted = false;
        this._pttHolding = false;
        this.isProcessing = false;
        this.accumulatedText = '';

        // Restore listening state — stop()/pttRelease() may have set isListening=false
        this.isListening = true;
        if (this.recognition) {
            try { this.recognition.start(); } catch (e) {}
        }
    }
}

// ===== WAKE WORD DETECTOR =====
// Listens for wake words in passive mode.
// Uses getUserMedia() before recognition.start() — without an active mic stream,
// Chrome's SpeechRecognition immediately aborts every cycle and never captures speech.
class WakeWordDetector {
    constructor() {
        this.recognition = null;
        this.isListening = false;
        this.onWakeWordDetected = null;
        this._micPermissionGranted = false;

        // Wake words to listen for (overridden per-profile via applyProfile)
        this.wakeWords = ['wake up'];

        // Check browser support
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('Web Speech API not supported in this browser - wake word detection unavailable');
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;   // Must be true — Chrome produces nothing without it
        this.recognition.lang = 'en-US';

        this.recognition.onresult = (event) => {
            // Check ALL results (interim + final) for wake words
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript.toLowerCase();
                console.log(`Wake word detector heard (${event.results[i].isFinal ? 'final' : 'interim'}):`, transcript);

                if (this.wakeWords.some(wakeWord => transcript.includes(wakeWord))) {
                    console.log('Wake word detected!');
                    if (this.onWakeWordDetected) {
                        this.onWakeWordDetected();
                    }
                    return; // Stop checking once detected
                }
            }
        };

        this.recognition.onerror = (event) => {
            if (event.error === 'no-speech' || event.error === 'aborted') {
                return; // Normal during passive listening
            }
            console.warn('Wake word detector error:', event.error);
            _reportSTTError(event.error, `Wake word error: ${event.error}`, 'wake_word');
        };

        this.recognition.onend = () => {
            // Auto-restart if we're supposed to be listening.
            // 300ms delay gives Chrome time to release the speech service connection.
            if (this.isListening) {
                setTimeout(() => {
                    if (this.isListening) {
                        try {
                            this.recognition.start();
                        } catch (e) {
                            // Already started
                        }
                    }
                }, 300);
            }
        };
    }

    isSupported() {
        return this.recognition !== null;
    }

    async start() {
        if (!this.recognition) {
            console.error('Speech recognition not supported');
            return false;
        }

        // Ensure mic permission is granted before recognition.start().
        // Without this, Chrome aborts every cycle. We release the stream
        // immediately — we just need the permission grant, not the raw audio.
        // Holding the stream can starve SpeechRecognition of mic access.
        if (!this._micPermissionGranted) {
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                stream.getTracks().forEach(t => t.stop()); // Release immediately
                this._micPermissionGranted = true;
                console.log('Wake word: mic permission granted');
            } catch (e) {
                console.error('Wake word: mic access failed:', e.name, e.message);
                return false;
            }
        }

        try {
            this.isListening = true;
            this.recognition.start();
            console.log('Wake word detector started');
            return true;
        } catch (e) {
            console.error('Failed to start wake word detector:', e);
            this.isListening = false;
            return false;
        }
    }

    stop() {
        if (this.recognition) {
            this.isListening = false;
            this.recognition.stop();
            console.log('Wake word detector stopped');
        }
    }

    async toggle() {
        if (this.isListening) {
            this.stop();
            return false;
        } else {
            return await this.start();
        }
    }
}

export { WebSpeechSTT, WakeWordDetector };
