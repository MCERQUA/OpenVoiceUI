/**
 * GroqSTT — Server-side speech recognition via Groq Whisper API.
 * Captures audio with MediaRecorder, uses VAD to detect speech/silence,
 * sends audio chunks to /api/stt/groq for transcription.
 *
 * Drop-in replacement for WebSpeechSTT with built-in PTT support.
 *
 * Usage:
 *   import { GroqSTT, GroqWakeWordDetector } from './GroqSTT.js';
 *
 *   const stt = new GroqSTT();
 *   stt.onResult = (text) => console.log('Heard:', text);
 *   await stt.start();
 */

// ===== GROQ STT =====
// Server-side speech recognition via Groq Whisper API
class GroqSTT {
    constructor(config = {}) {
        this.serverUrl = (config.serverUrl || window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, '');
        this.isListening = false;
        this.onResult = null;
        this.onError = null;
        this.onListenFinal = null;   // Listen panel hook — called with each transcript
        this.onInterim = null;       // Not used (Groq has no interim results)
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.isProcessing = false;
        this.accumulatedText = '';   // PTT compatibility — last transcript

        // PTT support (built-in, no monkey-patching needed)
        this._micMuted = false;
        this._pttHolding = false;
        this._muteActive = false;   // Set by mute(), cleared by resume() — survives API call finally blocks

        // VAD (Voice Activity Detection) settings
        this.silenceTimer = null;
        this.silenceDelayMs = 2500;     // 2.5s silence = end of speech (profile can override)
        this.accumulationDelayMs = 2000; // Window to merge consecutive chunks before sending to AI
        this.vadThreshold = 50;         // FFT average amplitude threshold (profile can override)
        this.minSpeechMs = 300;         // Must sustain above threshold for this long before counting as speech
        this.maxRecordingMs = 45000;    // 45s max before auto-chunk (profile can override)
        this.maxRecordingTimer = null;
        this.isSpeaking = false;
        this.stoppingRecorder = false;
        this.hadSpeechInChunk = false;
        this._speechStartTime = 0;     // When sustained speech started
        this._resumedSpeechStart = 0;  // When resumed speech started (for clearing silence timer)

        // Audio analysis for VAD
        this._audioCtx = null;
        this._analyser = null;
        this._vadAnimFrame = null;
        this._accumulationTimer = null; // Accumulate transcripts across chunks before sending
    }

    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async start() {
        if (this.isListening) return true;
        if (this._micMuted) return false;

        try {
            // Get mic stream (reuse existing if available)
            if (!this.stream || !this.stream.active) {
                this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }

            this._setupRecorder();
            this._startVAD();

            this.mediaRecorder.start();
            this.isListening = true;
            console.log('Groq STT started');
            return true;
        } catch (error) {
            console.error('Failed to start Groq STT:', error);
            if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
                if (this.onError) this.onError('no-device');
            } else if (error.name === 'NotAllowedError') {
                if (this.onError) this.onError('not-allowed');
            } else {
                if (this.onError) this.onError(error);
            }
            return false;
        }
    }

    _setupRecorder() {
        const options = { mimeType: 'audio/webm;codecs=opus' };
        this.mediaRecorder = new MediaRecorder(this.stream, options);
        this.audioChunks = [];

        this.mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                this.audioChunks.push(event.data);
            }
        };

        this.mediaRecorder.onstop = async () => {
            // Snapshot and clear chunks immediately
            const chunks = this.audioChunks;
            const hadSpeech = this.hadSpeechInChunk;
            this.audioChunks = [];
            this.hadSpeechInChunk = false;
            this.stoppingRecorder = false;

            // Restart recording IMMEDIATELY to minimize the gap where audio is lost.
            // The API call below runs in parallel — no words dropped at chunk boundaries.
            if (this.isListening && !this._micMuted && !this._muteActive && !this._pttHolding) {
                this.isSpeaking = false;
                this.mediaRecorder.start();
            }

            if (chunks.length === 0) return;

            // If muted (TTS playing), discard audio
            if ((this.isProcessing || this._muteActive) && !this._pttHolding) {
                return;
            }

            this.isProcessing = true;

            // Clear timers
            if (this.silenceTimer) {
                clearTimeout(this.silenceTimer);
                this.silenceTimer = null;
            }
            if (this.maxRecordingTimer) {
                clearTimeout(this.maxRecordingTimer);
                this.maxRecordingTimer = null;
            }

            const audioBlob = new Blob(chunks, { type: 'audio/webm' });

            // Skip if no speech detected or audio too small (10KB min filters out noise bursts)
            if (!hadSpeech || audioBlob.size < 10000) {
                console.log('Groq STT: skipping - no speech or too small (' + audioBlob.size + ' bytes)');
                this.isProcessing = false;
                return;
            }

            try {
                console.log('Groq STT: sending audio (' + audioBlob.size + ' bytes)');
                const formData = new FormData();
                formData.append('audio', audioBlob, 'audio.webm');

                const response = await fetch(`${this.serverUrl}/api/stt/groq`, {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (data.transcript && data.transcript.trim()) {
                    console.log('Groq STT transcript:', data.transcript);
                    if (this.onListenFinal) this.onListenFinal(data.transcript);

                    // PTT mode: send immediately (user released button = done talking)
                    if (this._micMuted) {
                        this.accumulatedText = data.transcript.trim();
                        if (this.onResult) this.onResult(this.accumulatedText);
                        this.accumulatedText = '';
                    } else {
                        // Listen mode: accumulate across chunks, send after silence
                        this.accumulatedText = this.accumulatedText
                            ? this.accumulatedText + ' ' + data.transcript.trim()
                            : data.transcript.trim();

                        // Clear any existing accumulation timer
                        if (this._accumulationTimer) {
                            clearTimeout(this._accumulationTimer);
                            this._accumulationTimer = null;
                        }
                        // Short window to merge consecutive chunks, then send
                        this._accumulationTimer = setTimeout(() => {
                            this._accumulationTimer = null;
                            const fullText = this.accumulatedText.trim();
                            if (fullText && this.onResult) {
                                console.log('Groq STT accumulated result:', fullText);
                                this.onResult(fullText);
                            }
                            this.accumulatedText = '';
                        }, this.accumulationDelayMs);
                    }
                }
            } catch (error) {
                console.error('Groq STT error:', error);
                if (this.onError) this.onError(error);
            } finally {
                this.isProcessing = false;
            }
        };
    }

    _startVAD() {
        // Only create AudioContext once per stream
        if (this._audioCtx && this._audioCtx.state !== 'closed') {
            // VAD already running, just restart the animation frame loop
            if (!this._vadAnimFrame) this._runVADLoop();
            return;
        }

        this._audioCtx = new AudioContext();
        const source = this._audioCtx.createMediaStreamSource(this.stream);
        this._analyser = this._audioCtx.createAnalyser();
        this._analyser.fftSize = 512;
        source.connect(this._analyser);

        this._runVADLoop();
    }

    _runVADLoop() {
        const bufferLength = this._analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        const checkLevel = () => {
            if (!this.isListening) {
                this._vadAnimFrame = null;
                return;
            }

            this._analyser.getByteFrequencyData(dataArray);
            const average = dataArray.reduce((a, b) => a + b) / bufferLength;
            const isSpeakingNow = average > this.vadThreshold;

            // Skip VAD processing while muted (TTS playing) — prevents speaker
            // audio from being detected as speech and queuing phantom transcripts
            if (this._muteActive) {
                this._vadAnimFrame = requestAnimationFrame(checkLevel);
                return;
            }

            if (isSpeakingNow && !this.isSpeaking) {
                // Potential speech — check minimum duration before confirming
                const now = Date.now();
                if (!this._speechStartTime) {
                    this._speechStartTime = now;
                }
                if (now - this._speechStartTime < this.minSpeechMs) {
                    // Still below minimum — don't confirm yet, just keep checking
                    this._vadAnimFrame = requestAnimationFrame(checkLevel);
                    return;
                }

                // Speech confirmed (sustained above threshold for minSpeechMs)
                this.isSpeaking = true;
                this.hadSpeechInChunk = true;
                this._speechStartTime = 0;

                if (this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }

                // Max recording safety timer
                if (!this.maxRecordingTimer && !this.isProcessing && !this.stoppingRecorder) {
                    this.maxRecordingTimer = setTimeout(() => {
                        this.maxRecordingTimer = null;
                        this.isSpeaking = false;
                        this.stoppingRecorder = true;
                        if (this.silenceTimer) {
                            clearTimeout(this.silenceTimer);
                            this.silenceTimer = null;
                        }
                        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                            this.mediaRecorder.stop();
                        }
                    }, this.maxRecordingMs);
                }
            } else if (isSpeakingNow && this.isSpeaking) {
                // Continued speech — user still talking after a brief dip.
                // Only clear silence timer after sustained speech (minSpeechMs) to
                // prevent ambient noise blips from keeping the recording open forever.
                const now = Date.now();
                if (!this._resumedSpeechStart) {
                    this._resumedSpeechStart = now;
                }
                if (now - this._resumedSpeechStart >= this.minSpeechMs && this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                    this._resumedSpeechStart = 0;
                }
            } else if (!isSpeakingNow && !this.isSpeaking) {
                // Below threshold and not yet confirmed — reset speech start timer
                this._speechStartTime = 0;
                this._resumedSpeechStart = 0;
            } else if (!isSpeakingNow && this.isSpeaking && !this.isProcessing && !this.stoppingRecorder) {
                // Silence after confirmed speech — start silence timer
                this._resumedSpeechStart = 0;
                if (!this.silenceTimer) {
                    this.silenceTimer = setTimeout(() => {
                        this.isSpeaking = false;
                        this.stoppingRecorder = true;
                        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                            this.mediaRecorder.stop();
                        }
                    }, this.silenceDelayMs);
                }
            }

            this._vadAnimFrame = requestAnimationFrame(checkLevel);
        };

        this._vadAnimFrame = requestAnimationFrame(checkLevel);
    }

    stop() {
        this.isListening = false;
        this.stoppingRecorder = false;
        this._micMuted = false;
        this._muteActive = false;

        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }
        if (this._vadAnimFrame) { cancelAnimationFrame(this._vadAnimFrame); this._vadAnimFrame = null; }

        if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
        }

        // Release mic stream
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }

        // Close audio context
        if (this._audioCtx) {
            this._audioCtx.close().catch(() => {});
            this._audioCtx = null;
            this._analyser = null;
        }

        console.log('Groq STT stopped');
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
     * Mute STT — called when TTS starts speaking.
     * Stops recording and discards any pending audio to prevent echo.
     * Does NOT release the mic stream or change isListening state.
     */
    mute() {
        this._muteActive = true;
        this.isProcessing = true;
        this.hadSpeechInChunk = false;
        this.accumulatedText = '';
        if (this.silenceTimer) {
            clearTimeout(this.silenceTimer);
            this.silenceTimer = null;
        }
        if (this.maxRecordingTimer) {
            clearTimeout(this.maxRecordingTimer);
            this.maxRecordingTimer = null;
        }
        if (this._accumulationTimer) {
            clearTimeout(this._accumulationTimer);
            this._accumulationTimer = null;
        }
        // Stop recording but keep stream alive
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
    }

    /**
     * Resume STT after TTS finishes.
     * Restarts recording from clean state.
     */
    resume() {
        this._muteActive = false;
        this.isProcessing = false;
        this.stoppingRecorder = false;
        this.hadSpeechInChunk = false;
        this.isSpeaking = false;
        this.audioChunks = [];

        // Restart recording if session is active and not muted
        if (this.isListening && !this._micMuted) {
            if (this.stream && this.stream.active) {
                // MediaRecorder may need to be recreated if stream changed
                if (!this.mediaRecorder || this.mediaRecorder.stream !== this.stream) {
                    this._setupRecorder();
                }
                if (this.mediaRecorder.state === 'inactive') {
                    this.mediaRecorder.start();
                }
                // Restart VAD loop if it stopped
                if (!this._vadAnimFrame) {
                    this._startVAD();
                }
            }
        }
    }

    // --- PTT helpers (called from PTT code in app.js) ---

    /**
     * PTT activate — start recording for push-to-talk.
     * Called when user presses the PTT button.
     */
    pttActivate() {
        this._pttHolding = true;
        this._micMuted = false;
        this.isProcessing = false;
        this.accumulatedText = '';
        this.hadSpeechInChunk = false;
        this.audioChunks = [];
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }

        // Start recording
        if (this.mediaRecorder && this.mediaRecorder.state === 'inactive') {
            this.mediaRecorder.start();
        }
    }

    /**
     * PTT release — stop recording and force transcription.
     * Called when user releases the PTT button.
     * Unlike mute(), this DOES process the captured audio.
     */
    pttRelease() {
        this._pttHolding = false;
        this._micMuted = true;
        this.hadSpeechInChunk = true; // Force transcription regardless
        this.stoppingRecorder = true;

        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }

        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
            // onstop handler will send to Groq and call onResult
        }
    }

    /**
     * PTT mute — stop recording and discard audio.
     * Called when PTT mode is toggled ON (mic off by default).
     */
    pttMute() {
        this._pttHolding = false;
        this._micMuted = true;
        this.hadSpeechInChunk = false;
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }
        this.isProcessing = true; // Prevents onstop from transcribing
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
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
        this.stoppingRecorder = false;
        this.hadSpeechInChunk = false;
        this.audioChunks = [];

        if (this.isListening && this.mediaRecorder && this.mediaRecorder.state === 'inactive') {
            this.mediaRecorder.start();
        }
    }
}


// ===== GROQ WAKE WORD DETECTOR =====
// Listens for wake words using Groq Whisper API.
// Continuously records, transcribes, and checks for wake phrases.
class GroqWakeWordDetector {
    constructor() {
        this.isListening = false;
        this.onWakeWordDetected = null;
        this.wakeWords = ['wake up'];
        this._stt = null;
    }

    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async start() {
        if (this.isListening) return true;

        this._stt = new GroqSTT();
        // Faster settings for wake word detection
        this._stt.silenceDelayMs = 1500;    // 1.5s silence (faster response)
        this._stt.maxRecordingMs = 10000;   // 10s max chunks
        this._stt.vadThreshold = 40;        // Sensitive but not noise-triggering

        this._stt.onResult = (transcript) => {
            const lower = transcript.toLowerCase();
            console.log(`Wake word detector heard: "${transcript}"`);
            if (this.wakeWords.some(ww => lower.includes(ww))) {
                console.log('Wake word detected!');
                if (this.onWakeWordDetected) {
                    this.onWakeWordDetected();
                }
            }
        };

        this._stt.onError = (error) => {
            console.warn('Wake word detector error:', error);
        };

        this.isListening = true;
        const ok = await this._stt.start();
        if (!ok) {
            this.isListening = false;
            return false;
        }

        console.log('Groq wake word detector started');
        return true;
    }

    stop() {
        this.isListening = false;
        if (this._stt) {
            this._stt.stop();
            this._stt = null;
        }
        console.log('Groq wake word detector stopped');
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

export { GroqSTT, GroqWakeWordDetector };
