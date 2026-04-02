/**
 * ExternalSTT — Server-side speech recognition via any external Whisper-compatible API.
 * Captures audio with MediaRecorder, uses VAD to detect speech/silence,
 * sends audio chunks to /api/stt/external for transcription.
 *
 * The server-side provider forwards to whatever URL is configured in STT_API_URL.
 * Supports OpenAI-compatible and generic Whisper ASR formats.
 *
 * Drop-in replacement for GroqSTT / WebSpeechSTT with built-in PTT support.
 *
 * Usage:
 *   import { ExternalSTT, ExternalWakeWordDetector } from './ExternalSTT.js';
 *
 *   const stt = new ExternalSTT();
 *   stt.onResult = (text) => console.log('Heard:', text);
 *   await stt.start();
 */

// ===== EXTERNAL STT =====
// Server-side speech recognition via user-provided external API
class ExternalSTT {
    constructor(config = {}) {
        this.serverUrl = (config.serverUrl || window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, '');
        this.isListening = false;
        this.onResult = null;
        this.onError = null;
        this.onListenFinal = null;
        this.onInterim = null;
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.stream = null;
        this.isProcessing = false;
        this.accumulatedText = '';

        // PTT support
        this._micMuted = false;
        this._pttHolding = false;
        this._muteActive = false;

        // VAD settings
        this.silenceTimer = null;
        this.silenceDelayMs = 1500;      // 1.5s silence — gives users time to pause/think
        this.accumulationDelayMs = config.accumulationDelayMs || 0;
        this.vadThreshold = 25;
        this.minSpeechMs = 300;
        this.maxRecordingMs = 45000;
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
            console.log('External STT started');
            return true;
        } catch (error) {
            console.error('Failed to start External STT:', error);
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

            // Restart recording immediately to minimize gap
            if (this.isListening && !this._micMuted && !this._muteActive && !this._pttHolding) {
                this.isSpeaking = false;
                this.mediaRecorder.start();
            }

            if (chunks.length === 0) return;

            if ((this.isProcessing || this._muteActive) && !this._pttHolding) {
                return;
            }

            this.isProcessing = true;

            if (this.silenceTimer) {
                clearTimeout(this.silenceTimer);
                this.silenceTimer = null;
            }
            if (this.maxRecordingTimer) {
                clearTimeout(this.maxRecordingTimer);
                this.maxRecordingTimer = null;
            }

            const audioBlob = new Blob(chunks, { type: 'audio/webm' });

            if (!hadSpeech && audioBlob.size < 50000) {
                console.log('External STT: skipping - no speech detected (' + audioBlob.size + ' bytes)');
                this.isProcessing = false;
                return;
            }

            try {
                console.log('External STT: sending audio (' + audioBlob.size + ' bytes)');
                const formData = new FormData();
                formData.append('audio', audioBlob, 'audio.webm');

                const response = await fetch(`${this.serverUrl}/api/stt/external`, {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                if (data.transcript && data.transcript.trim()) {
                    console.log('External STT transcript:', data.transcript);
                    if (this.onListenFinal) this.onListenFinal(data.transcript);

                    if (this._micMuted) {
                        this.accumulatedText = data.transcript.trim();
                        if (this.onResult) this.onResult(this.accumulatedText);
                        this.accumulatedText = '';
                    } else {
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
                                console.log('External STT accumulated result:', fullText);
                                this.onResult(fullText);
                            }
                            this.accumulatedText = '';
                        }, this.accumulationDelayMs);
                    }
                }
            } catch (error) {
                console.error('External STT error:', error);
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

            if (this._muteActive) {
                this._vadAnimFrame = requestAnimationFrame(checkLevel);
                return;
            }

            if (isSpeakingNow && !this.isSpeaking) {
                const now = Date.now();
                if (!this._speechStartTime) {
                    this._speechStartTime = now;
                }
                if (now - this._speechStartTime < this.minSpeechMs) {
                    this._vadAnimFrame = requestAnimationFrame(checkLevel);
                    return;
                }

                this.isSpeaking = true;
                this.hadSpeechInChunk = true;
                this._speechStartTime = 0;

                if (this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }

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

        console.log('External STT stopped');
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
        this._muteActive = false;
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


// ===== EXTERNAL WAKE WORD DETECTOR =====
class ExternalWakeWordDetector {
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

        this._stt = new ExternalSTT();
        this._stt.silenceDelayMs = 1500;
        this._stt.maxRecordingMs = 10000;
        this._stt.vadThreshold = 40;

        this._stt.onResult = (transcript) => {
            const lower = transcript.toLowerCase();
            console.log(`External wake word detector heard: "${transcript}"`);
            if (this.wakeWords.some(ww => lower.includes(ww))) {
                console.log('Wake word detected!');
                if (this.onWakeWordDetected) {
                    this.onWakeWordDetected();
                }
            }
        };

        this._stt.onError = (error) => {
            console.warn('External wake word detector error:', error);
        };

        this.isListening = true;
        const ok = await this._stt.start();
        if (!ok) {
            this.isListening = false;
            return false;
        }

        console.log('External wake word detector started');
        return true;
    }

    stop() {
        this.isListening = false;
        if (this._stt) {
            this._stt.stop();
            this._stt = null;
        }
        console.log('External wake word detector stopped');
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

export { ExternalSTT, ExternalWakeWordDetector };
