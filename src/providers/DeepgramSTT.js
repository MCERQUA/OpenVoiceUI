/**
 * DeepgramSTT — Server-side speech recognition via Deepgram Nova-2 API.
 * Captures audio with MediaRecorder, uses VAD to detect speech/silence,
 * sends audio chunks to /api/stt/deepgram for transcription.
 *
 * Drop-in replacement for WebSpeechSTT / GroqSTT with built-in PTT support.
 *
 * Usage:
 *   import { DeepgramSTT, DeepgramWakeWordDetector } from './DeepgramSTT.js';
 *
 *   const stt = new DeepgramSTT();
 *   stt.onResult = (text) => console.log('Heard:', text);
 *   await stt.start();
 */

// ===== DEEPGRAM STT =====
// Server-side speech recognition via Deepgram Nova-2 API
class DeepgramSTT {
    constructor(config = {}) {
        this.serverUrl = (config.serverUrl || window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, '');
        this.isListening = false;
        this.onResult = null;
        this.onError = null;
        this.onListenFinal = null;   // Listen panel hook — called with each transcript
        this.onInterim = null;       // Not used (pre-recorded mode has no interim results)
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.isProcessing = false;
        this.accumulatedText = '';   // PTT compatibility — last transcript

        // PTT support (built-in, no monkey-patching needed)
        this._micMuted = false;
        this._pttHolding = false;
        this._muteActive = false;   // Set by mute(), cleared by resume()

        // VAD (Voice Activity Detection) settings
        this.silenceTimer = null;
        this.silenceDelayMs = 800;      // 0.8s silence = end of speech
        this.accumulationDelayMs = config.accumulationDelayMs || 0;
        this.vadThreshold = 25;         // FFT average amplitude threshold
        this.minSpeechMs = 300;         // Must sustain above threshold before counting
        this.maxRecordingMs = 45000;    // 45s max before auto-chunk
        this.maxRecordingTimer = null;
        this.isSpeaking = false;
        this.stoppingRecorder = false;
        this.hadSpeechInChunk = false;
        this._speechStartTime = 0;
        this._resumedSpeechStart = 0;

        // Audio analysis for VAD
        this._audioCtx = null;
        this._analyser = null;
        this._vadAnimFrame = null;
        this._accumulationTimer = null;
    }

    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async start() {
        if (this.isListening) return true;
        if (this._micMuted) return false;

        try {
            if (!this.stream || !this.stream.active) {
                this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            }

            this._setupRecorder();
            this._startVAD();

            this.mediaRecorder.start();
            this.isListening = true;
            console.log('Deepgram STT started');
            return true;
        } catch (error) {
            console.error('Failed to start Deepgram STT:', error);
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
            const chunks = this.audioChunks;
            const hadSpeech = this.hadSpeechInChunk;
            this.audioChunks = [];
            this.hadSpeechInChunk = false;
            this.stoppingRecorder = false;

            // Restart recording immediately to minimize audio gap
            if (this.isListening && !this._micMuted && !this._muteActive && !this._pttHolding) {
                this.isSpeaking = false;
                this.mediaRecorder.start();
            }

            if (chunks.length === 0) return;

            // Discard audio if muted (TTS playing)
            if ((this.isProcessing || this._muteActive) && !this._pttHolding) {
                return;
            }

            this.isProcessing = true;

            if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
            if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }

            const audioBlob = new Blob(chunks, { type: 'audio/webm' });

            // Skip if no speech and small audio — prevents hallucinations
            if (!hadSpeech && audioBlob.size < 50000) {
                console.log('Deepgram STT: skipping - no speech detected (' + audioBlob.size + ' bytes)');
                this.isProcessing = false;
                return;
            }

            try {
                console.log('Deepgram STT: sending audio (' + audioBlob.size + ' bytes)');
                const formData = new FormData();
                formData.append('audio', audioBlob, 'audio.webm');

                const response = await fetch(`${this.serverUrl}/api/stt/deepgram`, {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (data.transcript && data.transcript.trim()) {
                    console.log('Deepgram STT transcript:', data.transcript);
                    if (this.onListenFinal) this.onListenFinal(data.transcript);

                    // PTT mode: send immediately
                    if (this._micMuted) {
                        this.accumulatedText = data.transcript.trim();
                        if (this.onResult) this.onResult(this.accumulatedText);
                        this.accumulatedText = '';
                    } else {
                        // Listen mode: accumulate across chunks, send after silence
                        this.accumulatedText = this.accumulatedText
                            ? this.accumulatedText + ' ' + data.transcript.trim()
                            : data.transcript.trim();

                        if (this._accumulationTimer) {
                            clearTimeout(this._accumulationTimer);
                            this._accumulationTimer = null;
                        }
                        this._accumulationTimer = setTimeout(() => {
                            this._accumulationTimer = null;
                            const fullText = this.accumulatedText.trim();
                            if (fullText && this.onResult) {
                                console.log('Deepgram STT accumulated result:', fullText);
                                this.onResult(fullText);
                            }
                            this.accumulatedText = '';
                        }, this.accumulationDelayMs);
                    }
                }
            } catch (error) {
                console.error('Deepgram STT error:', error);
                if (this.onError) this.onError(error);
            } finally {
                this.isProcessing = false;
            }
        };
    }

    _startVAD() {
        if (this._audioCtx && this._audioCtx.state !== 'closed') {
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

            // Skip VAD while muted (TTS playing)
            if (this._muteActive) {
                this._vadAnimFrame = requestAnimationFrame(checkLevel);
                return;
            }

            if (isSpeakingNow && !this.isSpeaking) {
                const now = Date.now();
                if (!this._speechStartTime) this._speechStartTime = now;
                if (now - this._speechStartTime < this.minSpeechMs) {
                    this._vadAnimFrame = requestAnimationFrame(checkLevel);
                    return;
                }

                this.isSpeaking = true;
                this.hadSpeechInChunk = true;
                this._speechStartTime = 0;

                if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }

                if (!this.maxRecordingTimer && !this.isProcessing && !this.stoppingRecorder) {
                    this.maxRecordingTimer = setTimeout(() => {
                        this.maxRecordingTimer = null;
                        this.isSpeaking = false;
                        this.stoppingRecorder = true;
                        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
                        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                            this.mediaRecorder.stop();
                        }
                    }, this.maxRecordingMs);
                }
            } else if (isSpeakingNow && this.isSpeaking) {
                const now = Date.now();
                if (!this._resumedSpeechStart) this._resumedSpeechStart = now;
                if (now - this._resumedSpeechStart >= this.minSpeechMs && this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                    this._resumedSpeechStart = 0;
                }
            } else if (!isSpeakingNow && !this.isSpeaking) {
                this._speechStartTime = 0;
                this._resumedSpeechStart = 0;
            } else if (!isSpeakingNow && this.isSpeaking && !this.isProcessing && !this.stoppingRecorder) {
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

        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
            this.stream = null;
        }

        if (this._audioCtx) {
            this._audioCtx.close().catch(() => {});
            this._audioCtx = null;
            this._analyser = null;
        }

        console.log('Deepgram STT stopped');
    }

    resetProcessing() {
        this.isProcessing = false;
        this.accumulatedText = '';
    }

    pause() { this.mute(); }

    mute() {
        this._muteActive = true;
        this.isProcessing = true;
        this.hadSpeechInChunk = false;
        this.accumulatedText = '';
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
    }

    resume() {
        this._muteActive = false;
        this.isProcessing = false;
        this.stoppingRecorder = false;
        this.hadSpeechInChunk = false;
        this.isSpeaking = false;
        this.audioChunks = [];

        if (this.isListening && !this._micMuted) {
            if (this.stream && this.stream.active) {
                if (!this.mediaRecorder || this.mediaRecorder.stream !== this.stream) {
                    this._setupRecorder();
                }
                if (this.mediaRecorder.state === 'inactive') {
                    this.mediaRecorder.start();
                }
                if (!this._vadAnimFrame) {
                    this._startVAD();
                }
            }
        }
    }

    // --- PTT helpers ---

    pttActivate() {
        this._pttHolding = true;
        this._micMuted = false;
        this._muteActive = false; // Clear stale TTS mute — PTT overrides
        this.isProcessing = false;
        this.accumulatedText = '';
        this.hadSpeechInChunk = false;
        this.audioChunks = [];
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }

        if (this.mediaRecorder && this.mediaRecorder.state === 'inactive') {
            this.mediaRecorder.start();
        }
    }

    pttRelease() {
        this._pttHolding = false;
        this._micMuted = true;
        this.hadSpeechInChunk = true;
        this.stoppingRecorder = true;

        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }

        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
    }

    pttMute() {
        this._pttHolding = false;
        this._micMuted = true;
        this.hadSpeechInChunk = false;
        if (this.silenceTimer) { clearTimeout(this.silenceTimer); this.silenceTimer = null; }
        if (this.maxRecordingTimer) { clearTimeout(this.maxRecordingTimer); this.maxRecordingTimer = null; }
        this.isProcessing = true;
        if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
            this.mediaRecorder.stop();
        }
    }

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


// ===== DEEPGRAM WAKE WORD DETECTOR =====
class DeepgramWakeWordDetector {
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

        this._stt = new DeepgramSTT();
        this._stt.silenceDelayMs = 1500;
        this._stt.maxRecordingMs = 10000;
        this._stt.vadThreshold = 40;

        this._stt.onResult = (transcript) => {
            const lower = transcript.toLowerCase();
            console.log(`Wake word detector heard: "${transcript}"`);
            if (this.wakeWords.some(ww => lower.includes(ww))) {
                console.log('Wake word detected!');
                if (this.onWakeWordDetected) this.onWakeWordDetected();
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

        console.log('Deepgram wake word detector started');
        return true;
    }

    stop() {
        this.isListening = false;
        if (this._stt) {
            this._stt.stop();
            this._stt = null;
        }
        console.log('Deepgram wake word detector stopped');
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

export { DeepgramSTT, DeepgramWakeWordDetector };
