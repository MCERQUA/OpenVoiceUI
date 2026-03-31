/**
 * DeepgramStreamingSTT — Real-time streaming speech recognition via Deepgram WebSocket.
 *
 * Instead of recording a blob, stopping, uploading, and waiting (like DeepgramSTT),
 * this streams raw audio directly to Deepgram's WebSocket API as the user speaks.
 * Transcripts come back in real-time — no batch upload delay.
 *
 * Falls back to WebSpeechSTT automatically if Deepgram is unavailable (bad key,
 * network issue, outage). The fallback is transparent — all callbacks and PTT
 * methods are proxied through, so callers don't need to know which engine is active.
 *
 * Drop-in replacement for DeepgramSTT / GroqSTT / WebSpeechSTT.
 *
 * Usage:
 *   import { DeepgramStreamingSTT } from './DeepgramStreamingSTT.js';
 *
 *   const stt = new DeepgramStreamingSTT();
 *   stt.onResult = (text) => console.log('Heard:', text);
 *   await stt.start();
 */

import { WebSpeechSTT } from './WebSpeechSTT.js';

class DeepgramStreamingSTT {
    constructor(config = {}) {
        this.serverUrl = (config.serverUrl || window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, '');
        this.isListening = false;
        this.onResult = null;
        this.onError = null;
        this.onListenFinal = null;   // Listen panel hook — called with each final transcript
        this.onInterim = null;       // Called with interim text as user speaks
        this.isProcessing = false;
        this.accumulatedText = '';

        // PTT support
        this._micMuted = false;
        this._pttHolding = false;
        this._muteActive = false;

        // Profile-overridable settings (same interface as DeepgramSTT)
        this.silenceDelayMs = 800;       // Not used for VAD (Deepgram handles it), but kept for profile compat
        this.accumulationDelayMs = config.accumulationDelayMs || 1500;
        this.vadThreshold = 25;          // Not used (Deepgram server-side VAD), kept for profile compat
        this.minSpeechMs = 300;          // Not used (Deepgram server-side VAD), kept for profile compat
        this.maxRecordingMs = 45000;     // Not used (streaming is continuous), kept for profile compat

        // Deepgram WebSocket state
        this._ws = null;
        this._stream = null;
        this._audioCtx = null;
        this._processorNode = null;
        this._sourceNode = null;
        this._accumulationTimer = null;
        this._keepAliveInterval = null;
        this._reconnecting = false;
        this._intentionalClose = false;
        this._reconnectFailures = 0;

        // Deepgram model config
        this._model = config.model || 'nova-2';
        this._language = config.language || 'en';

        // Fallback: WebSpeechSTT when Deepgram is unavailable
        this._fallback = null;       // lazily created WebSpeechSTT
        this._usingFallback = false; // true when actively using fallback

        // Hallucination filtering (same set as server-side)
        this._hallucinations = new Set([
            'thank you', 'thanks for watching', 'thanks for listening',
            'subscribe', 'please subscribe', 'like and subscribe',
            'the end', 'subtitles by', 'translated by', 'closed captioning',
            'voice command for ai assistant', 'voice command for ai',
            'thanks', 'thank you so much',
        ]);
        this._hallucinationSubstrings = [
            'voice command for ai', 'thanks for watching', 'thanks for listening',
            'like and subscribe', 'please subscribe',
            'subtitles by', 'translated by', 'closed captioning',
        ];
    }

    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async start() {
        if (this.isListening) return true;
        if (this._micMuted) return false;

        // If already in fallback mode, delegate
        if (this._usingFallback && this._fallback) {
            return this._fallback.start();
        }

        try {
            // Get mic stream
            if (!this._stream || !this._stream.active) {
                this._stream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 16000,
                    }
                });
            }

            // Connect to Deepgram WebSocket
            const connected = await this._connectWebSocket();
            if (!connected) {
                console.warn('Deepgram unavailable — falling back to WebSpeech');
                return this._activateFallback();
            }

            // Start streaming audio
            this._startAudioPipeline();

            this.isListening = true;
            this._reconnectFailures = 0;
            console.log('Deepgram Streaming STT started');
            return true;
        } catch (error) {
            console.error('Failed to start Deepgram Streaming STT:', error);
            // Mic errors should not trigger fallback — they'd fail on WebSpeech too
            if (error.name === 'NotFoundError' || error.name === 'DevicesNotFoundError') {
                if (this.onError) this.onError('no-device');
                return false;
            } else if (error.name === 'NotAllowedError') {
                if (this.onError) this.onError('not-allowed');
                return false;
            }
            // Network / Deepgram error — try fallback
            console.warn('Deepgram error — falling back to WebSpeech');
            return this._activateFallback();
        }
    }

    stop() {
        if (this._usingFallback && this._fallback) {
            this._fallback.stop();
            this.isListening = false;
            return;
        }

        this.isListening = false;
        this._micMuted = false;
        this._muteActive = false;
        this._intentionalClose = true;

        this._stopAudioPipeline();
        this._closeWebSocket();
        this._clearTimers();

        // Release mic stream
        if (this._stream) {
            this._stream.getTracks().forEach(t => t.stop());
            this._stream = null;
        }

        console.log('Deepgram Streaming STT stopped');
    }

    resetProcessing() {
        if (this._usingFallback && this._fallback) {
            this._fallback.resetProcessing();
            return;
        }
        this.isProcessing = false;
        this.accumulatedText = '';
    }

    /** Alias for mute() — VoiceConversation calls pause() during greeting. */
    pause() {
        this.mute();
    }

    /**
     * Mute STT — called when TTS starts speaking.
     * Sends KeepAlive to Deepgram to pause without disconnecting,
     * and ignores any incoming transcripts.
     */
    mute() {
        if (this._usingFallback && this._fallback) {
            this._fallback.mute();
            return;
        }
        this._muteActive = true;
        this.isProcessing = true;
        this.accumulatedText = '';
        if (this._accumulationTimer) {
            clearTimeout(this._accumulationTimer);
            this._accumulationTimer = null;
        }
        // Don't close the WebSocket — just stop sending audio.
        // Deepgram's KeepAlive keeps the connection alive without audio.
        this._sendKeepAlive();
    }

    /**
     * Resume STT after TTS finishes.
     * Audio pipeline is still running, just start paying attention again.
     */
    resume() {
        if (this._usingFallback && this._fallback) {
            this._fallback.resume();
            return;
        }
        this._muteActive = false;
        this.isProcessing = false;
        this.accumulatedText = '';

        // If WebSocket died during mute, reconnect
        if (this.isListening && !this._micMuted && (!this._ws || this._ws.readyState !== WebSocket.OPEN)) {
            this._connectWebSocket().then(ok => {
                if (ok) {
                    this._startAudioPipeline();
                } else {
                    // Reconnect failed — fall back
                    console.warn('Deepgram reconnect failed on resume — falling back to WebSpeech');
                    this._activateFallback();
                }
            }).catch(err => {
                console.error('Deepgram Streaming STT: reconnect on resume failed:', err);
                this._activateFallback();
            });
        }
    }

    // --- PTT helpers (proxy to fallback when active) ---

    pttActivate() {
        if (this._usingFallback && this._fallback) { this._fallback.pttActivate(); return; }
        this._pttHolding = true;
        this._micMuted = false;
        this._muteActive = false;
        this.isProcessing = false;
        this.accumulatedText = '';
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }

        // Ensure WebSocket and audio pipeline are active
        if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
            this._connectWebSocket().then(ok => {
                if (ok) this._startAudioPipeline();
            });
        }
    }

    pttRelease() {
        if (this._usingFallback && this._fallback) { this._fallback.pttRelease(); return; }
        this._pttHolding = false;
        this._micMuted = true;

        // Tell Deepgram we're done speaking — triggers final transcript
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            this._ws.send(JSON.stringify({ type: 'CloseStream' }));
        }

        // Wait briefly for final transcript, then send accumulated
        setTimeout(() => {
            const text = this.accumulatedText.trim();
            if (text && this.onResult) {
                console.log('PTT release — sending:', text);
                this.isProcessing = true;
                this.onResult(text);
            }
            this.accumulatedText = '';
        }, 300);
    }

    pttMute() {
        if (this._usingFallback && this._fallback) { this._fallback.pttMute(); return; }
        this._pttHolding = false;
        this._micMuted = true;
        this.isProcessing = true;
        this.accumulatedText = '';
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }
    }

    pttUnmute() {
        if (this._usingFallback && this._fallback) { this._fallback.pttUnmute(); return; }
        this._micMuted = false;
        this._pttHolding = false;
        this.isProcessing = false;
        this._muteActive = false;  // clear stuck TTS mute from when TTS played during PTT mode
        this.accumulatedText = '';

        if (this.isListening && (!this._ws || this._ws.readyState !== WebSocket.OPEN)) {
            this._connectWebSocket().then(ok => {
                if (ok) this._startAudioPipeline();
            });
        }
    }

    // ---- Fallback ----

    /**
     * Activate WebSpeech fallback. Tears down any Deepgram state, creates a
     * WebSpeechSTT instance, wires all callbacks through, and starts it.
     */
    _activateFallback() {
        // Clean up Deepgram state
        this._stopAudioPipeline();
        this._closeWebSocket();
        this._clearTimers();
        // Release mic stream — WebSpeech manages its own
        if (this._stream) {
            this._stream.getTracks().forEach(t => t.stop());
            this._stream = null;
        }

        this._usingFallback = true;

        if (!this._fallback) {
            this._fallback = new WebSpeechSTT();
        }

        // Wire callbacks through so callers see the same interface
        this._syncFallbackCallbacks();

        console.warn('[STT] Now using WebSpeech fallback');
        // Report so the UI can show a notice if desired
        try {
            fetch('/api/stt-events', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    error: 'deepgram-fallback',
                    message: 'Deepgram unavailable — using WebSpeech fallback',
                    provider: 'deepgram-streaming',
                    source: 'stt',
                }),
            }).catch(() => {});
        } catch (_) {}

        return this._fallback.start().then(ok => {
            this.isListening = ok;
            return ok;
        });
    }

    /**
     * Sync current callback references to the fallback instance.
     * Called when fallback activates and whenever callbacks might have changed.
     */
    _syncFallbackCallbacks() {
        if (!this._fallback) return;
        this._fallback.onResult = (...args) => { if (this.onResult) this.onResult(...args); };
        this._fallback.onError = (...args) => { if (this.onError) this.onError(...args); };
        this._fallback.onListenFinal = (...args) => { if (this.onListenFinal) this.onListenFinal(...args); };
        // WebSpeechSTT has onInterim — proxy it
        this._fallback.onInterim = (...args) => { if (this.onInterim) this.onInterim(...args); };
    }

    // ---- WebSocket Connection ----

    async _connectWebSocket() {
        // Get a temporary API key from our server (don't expose the real key to the browser)
        let apiKey;
        try {
            const resp = await fetch(`${this.serverUrl}/api/stt/deepgram/token`);
            if (!resp.ok) {
                console.error('Deepgram token endpoint failed:', resp.status);
                return false;
            }
            const data = await resp.json();
            apiKey = data.token;
            if (!apiKey) {
                console.error('Deepgram token endpoint returned no token');
                return false;
            }
        } catch (err) {
            console.error('Failed to get Deepgram token:', err);
            return false;
        }

        return new Promise((resolve) => {
            const params = new URLSearchParams({
                model: this._model,
                language: this._language,
                smart_format: 'true',
                punctuate: 'true',
                interim_results: 'true',
                utterance_end_ms: '1000',
                vad_events: 'true',
                endpointing: '300',
                encoding: 'linear16',
                sample_rate: '16000',
                channels: '1',
            });

            const url = `wss://api.deepgram.com/v1/listen?${params}`;
            this._intentionalClose = false;

            try {
                this._ws = new WebSocket(url, ['token', apiKey]);
            } catch (err) {
                console.error('Deepgram WebSocket creation failed:', err);
                resolve(false);
                return;
            }

            const timeout = setTimeout(() => {
                if (this._ws && this._ws.readyState === WebSocket.CONNECTING) {
                    console.error('Deepgram WebSocket connection timeout');
                    this._ws.close();
                    resolve(false);
                }
            }, 5000);

            this._ws.onopen = () => {
                clearTimeout(timeout);
                console.log('Deepgram WebSocket connected');
                this._reconnectFailures = 0;
                this._startKeepAlive();
                resolve(true);
            };

            this._ws.onmessage = (event) => {
                this._handleMessage(event);
            };

            this._ws.onerror = (event) => {
                clearTimeout(timeout);
                console.error('Deepgram WebSocket error:', event);
            };

            this._ws.onclose = (event) => {
                clearTimeout(timeout);
                this._stopKeepAlive();
                console.log(`Deepgram WebSocket closed (code: ${event.code})`);

                // Auto-reconnect if not intentional and still supposed to be listening
                if (!this._intentionalClose && this.isListening && !this._micMuted && !this._reconnecting) {
                    this._reconnectFailures++;

                    // After 3 failed reconnects, give up and fall back to WebSpeech
                    if (this._reconnectFailures >= 3) {
                        console.warn(`Deepgram: ${this._reconnectFailures} reconnect failures — falling back to WebSpeech`);
                        this._activateFallback();
                        return;
                    }

                    this._reconnecting = true;
                    const delay = Math.min(1000 * Math.pow(2, this._reconnectFailures - 1), 5000);
                    console.log(`Deepgram: reconnecting in ${delay}ms (attempt ${this._reconnectFailures}/3)...`);
                    setTimeout(() => {
                        this._reconnecting = false;
                        if (this.isListening && !this._intentionalClose) {
                            this._connectWebSocket().then(ok => {
                                if (ok) {
                                    this._startAudioPipeline();
                                } else {
                                    // Connection failed — count as another failure and maybe fallback
                                    this._reconnectFailures++;
                                    if (this._reconnectFailures >= 3) {
                                        console.warn('Deepgram: reconnect failed — falling back to WebSpeech');
                                        this._activateFallback();
                                    }
                                }
                            });
                        }
                    }, delay);
                }

                if (this._ws === null) return; // already cleaned up
                resolve(false);
            };
        });
    }

    _closeWebSocket() {
        this._stopKeepAlive();
        if (this._ws) {
            this._intentionalClose = true;
            // Send CloseStream to get final transcript before closing
            if (this._ws.readyState === WebSocket.OPEN) {
                try {
                    this._ws.send(JSON.stringify({ type: 'CloseStream' }));
                } catch (_) {}
            }
            this._ws.close();
            this._ws = null;
        }
    }

    _sendKeepAlive() {
        if (this._ws && this._ws.readyState === WebSocket.OPEN) {
            try {
                this._ws.send(JSON.stringify({ type: 'KeepAlive' }));
            } catch (_) {}
        }
    }

    _startKeepAlive() {
        this._stopKeepAlive();
        // Send KeepAlive every 8 seconds to prevent timeout
        this._keepAliveInterval = setInterval(() => {
            this._sendKeepAlive();
        }, 8000);
    }

    _stopKeepAlive() {
        if (this._keepAliveInterval) {
            clearInterval(this._keepAliveInterval);
            this._keepAliveInterval = null;
        }
    }

    // ---- Audio Pipeline ----

    _startAudioPipeline() {
        // Clean up existing pipeline
        this._stopAudioPipeline();

        if (!this._stream || !this._stream.active) return;

        this._audioCtx = new AudioContext({ sampleRate: 16000 });
        this._sourceNode = this._audioCtx.createMediaStreamSource(this._stream);

        // ScriptProcessorNode for raw PCM access (AudioWorklet would be better
        // but requires a separate file and HTTPS — this works everywhere)
        const bufferSize = 4096;
        this._processorNode = this._audioCtx.createScriptProcessor(bufferSize, 1, 1);

        this._processorNode.onaudioprocess = (event) => {
            if (this._muteActive || this._micMuted) return;
            if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return;

            const inputData = event.inputBuffer.getChannelData(0);

            // Convert Float32 [-1, 1] to Int16 PCM
            const pcm16 = new Int16Array(inputData.length);
            for (let i = 0; i < inputData.length; i++) {
                const s = Math.max(-1, Math.min(1, inputData[i]));
                pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            // Send raw PCM bytes to Deepgram
            this._ws.send(pcm16.buffer);
        };

        this._sourceNode.connect(this._processorNode);
        this._processorNode.connect(this._audioCtx.destination);
    }

    _stopAudioPipeline() {
        if (this._processorNode) {
            this._processorNode.disconnect();
            this._processorNode = null;
        }
        if (this._sourceNode) {
            this._sourceNode.disconnect();
            this._sourceNode = null;
        }
        if (this._audioCtx) {
            this._audioCtx.close().catch(() => {});
            this._audioCtx = null;
        }
    }

    // ---- Message Handling ----

    _handleMessage(event) {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (_) {
            return;
        }

        // Speech started event (Deepgram VAD)
        if (data.type === 'SpeechStarted') {
            // Could emit event for UI feedback
            return;
        }

        // UtteranceEnd — Deepgram detected end of utterance (silence after speech)
        // Use same accumulation window as speech_final so pauses don't split mid-sentence
        if (data.type === 'UtteranceEnd') {
            if (this.accumulatedText.trim()) {
                if (this._accumulationTimer) clearTimeout(this._accumulationTimer);
                this._accumulationTimer = setTimeout(() => {
                    this._accumulationTimer = null;
                    this._flushAccumulated();
                }, this.accumulationDelayMs);
            }
            return;
        }

        // Transcript results
        if (data.type === 'Results') {
            const channel = data.channel;
            if (!channel || !channel.alternatives || !channel.alternatives.length) return;

            const transcript = channel.alternatives[0].transcript || '';
            const isFinal = data.is_final;
            const speechFinal = data.speech_final;

            if (!transcript.trim()) return;

            // Ignore during mute (TTS playing)
            if (this._muteActive || (this.isProcessing && !this._pttHolding)) return;

            if (isFinal) {
                // Filter hallucinations
                if (this._isHallucination(transcript)) {
                    console.log('Deepgram Streaming: filtered hallucination:', transcript);
                    return;
                }

                console.log('Deepgram Streaming final:', transcript);
                if (this.onListenFinal) this.onListenFinal(transcript.trim());

                // PTT mode: accumulate and wait for pttRelease to send
                if (this._pttHolding) {
                    this.accumulatedText = this.accumulatedText
                        ? this.accumulatedText + ' ' + transcript.trim()
                        : transcript.trim();
                    return;
                }

                // Accumulate finals
                this.accumulatedText = this.accumulatedText
                    ? this.accumulatedText + ' ' + transcript.trim()
                    : transcript.trim();

                // If speech_final (Deepgram's endpointing), flush after short accumulation window
                if (speechFinal) {
                    if (this._accumulationTimer) {
                        clearTimeout(this._accumulationTimer);
                    }
                    this._accumulationTimer = setTimeout(() => {
                        this._accumulationTimer = null;
                        this._flushAccumulated();
                    }, this.accumulationDelayMs);
                }
            } else {
                // Interim result — show live feedback
                if (this.onInterim) {
                    const preview = this.accumulatedText
                        ? this.accumulatedText + ' ' + transcript.trim()
                        : transcript.trim();
                    this.onInterim(preview);
                }
            }
        }
    }

    _flushAccumulated() {
        if (this._accumulationTimer) {
            clearTimeout(this._accumulationTimer);
            this._accumulationTimer = null;
        }

        const text = this.accumulatedText.trim();
        if (!text) return;

        // Filter garbage
        const meaningful = text.replace(/[^a-zA-Z0-9]/g, '');
        if (meaningful.length < 2) {
            console.log('Deepgram Streaming: filtered too short:', text);
            this.accumulatedText = '';
            return;
        }

        if (this._isHallucination(text)) {
            console.log('Deepgram Streaming: filtered hallucination:', text);
            this.accumulatedText = '';
            return;
        }

        console.log('Deepgram Streaming result:', text);
        this.isProcessing = true;
        if (this.onResult) this.onResult(text);
        this.accumulatedText = '';
    }

    _isHallucination(text) {
        const lower = text.toLowerCase().replace(/[.!?,;:]+$/, '');
        if (this._hallucinations.has(lower)) return true;

        const meaningful = text.replace(/[^a-zA-Z0-9]/g, '');
        if (meaningful.length < 3) return true;

        for (const sub of this._hallucinationSubstrings) {
            if (lower.includes(sub)) return true;
        }

        // Repetitive pattern check
        const words = text.match(/[a-zA-Z]+/g);
        if (words && words.length >= 4) {
            const counts = {};
            for (const w of words) {
                const wl = w.toLowerCase();
                counts[wl] = (counts[wl] || 0) + 1;
            }
            const max = Math.max(...Object.values(counts));
            if (max / words.length >= 0.5) return true;
        }

        return false;
    }

    _clearTimers() {
        if (this._accumulationTimer) {
            clearTimeout(this._accumulationTimer);
            this._accumulationTimer = null;
        }
        this._stopKeepAlive();
    }
}


// ===== DEEPGRAM STREAMING WAKE WORD DETECTOR =====
class DeepgramStreamingWakeWordDetector {
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

        this._stt = new DeepgramStreamingSTT();

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

        console.log('Deepgram Streaming wake word detector started');
        return true;
    }

    stop() {
        this.isListening = false;
        if (this._stt) {
            this._stt.stop();
            this._stt = null;
        }
        console.log('Deepgram Streaming wake word detector stopped');
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

export { DeepgramStreamingSTT, DeepgramStreamingWakeWordDetector };
