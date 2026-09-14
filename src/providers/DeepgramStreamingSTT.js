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
        this._pttRestoreMuted = false; // mute state to return to when a press ends
        this._pttFlushing = false;     // released; waiting for Deepgram's last finals
        this._pttFlushTimer = null;
        this._flushWs = null;          // the socket that was sent CloseStream
        this._flushText = '';          // the released press's text, incl. finals from that socket
        this._openingStream = false;   // a no-call press is waiting on getUserMedia

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
        this._connectPromise = null;   // in-flight _connectWebSocket()

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
        // PTT mode (_micMuted) does not block start(). The mic stream, socket and
        // audio pipeline come up muted — onaudioprocess drops audio while
        // _micMuted — so a PTT hold streams at once. Refusing here left no mic
        // stream for the whole call when PTT mode was switched on before the call
        // started: every hold then opened a socket and sent zero audio.

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
        // Remember the mute state to return to on release — muted in PTT mode,
        // unmuted when a hotkey press interrupts hands-free listening. While a
        // previous release is still flushing, _micMuted has not been restored
        // yet, so keep the value recorded by that press. (That flush keeps
        // running on its own socket; closing it here lost its last finals.)
        if (!this._pttHolding && !this._pttFlushing) this._pttRestoreMuted = this._micMuted;

        this._pttHolding = true;
        this._micMuted = false;
        this._muteActive = false;
        this.isProcessing = false;
        this.accumulatedText = '';
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }

        // No mic stream means no call is running (start() never ran, or stop()
        // released it). Open the mic for this press; the app releases it again
        // with stop() once the message is sent.
        if (!this._stream || !this._stream.active) {
            this._openStreamForPress();
            return;
        }

        // Ensure WebSocket and audio pipeline are active
        if (!this._ws || this._ws.readyState !== WebSocket.OPEN) {
            this._connectWebSocket().then(ok => {
                if (ok) this._startAudioPipeline();
            });
        }
    }

    /** PTT with no call running: get the mic, then the socket and audio pipeline, for this press. */
    async _openStreamForPress() {
        if (this._openingStream) return;   // a press moments ago is already opening it
        this._openingStream = true;
        try {
            this._stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    sampleRate: 16000,
                }
            });
        } catch (error) {
            console.error('PTT: microphone unavailable:', error);
            if (this.onError) this.onError(error.name === 'NotAllowedError' ? 'not-allowed' : 'no-device');
            return;
        } finally {
            this._openingStream = false;
        }
        // Released before the mic came up: there is nothing to stream, so give it back.
        if (!this._pttHolding) {
            this._stream.getTracks().forEach(t => t.stop());
            this._stream = null;
            return;
        }
        const ok = await this._connectWebSocket();
        if (ok) this._startAudioPipeline();
    }

    pttRelease() {
        if (this._usingFallback && this._fallback) { this._fallback.pttRelease(); return; }
        if (!this._pttHolding) return;
        // A second release before the previous flush finished (a very fast double
        // press): fold that flush into this one rather than juggling two.
        if (this._pttFlushing) this._finishPttFlush();
        this._pttHolding = false;
        // _micMuted stays false until the transcript is delivered: ClawdbotMode's
        // stt.onResult checks `if (this.stt._micMuted) return;` and would drop it.
        this._pttFlushing = true;
        // This press's text, plus finals still to come on the socket being
        // flushed — kept apart from a new press that starts before the flush ends.
        this._flushText = this.accumulatedText;
        this.accumulatedText = '';

        const ws = this._ws;
        if (ws && ws.readyState === WebSocket.OPEN) {
            // CloseStream makes Deepgram send its remaining finals, then Metadata,
            // then close the socket — the close is the "everything delivered"
            // signal. A fixed 300ms wait raced it: on a short press the final only
            // exists after CloseStream, so a slow round trip lost the transcript.
            // Detach the socket so the next press opens a fresh one instead of
            // streaming into this closing one.
            this._flushWs = ws;
            this._ws = null;
            this._stopKeepAlive();
            ws.addEventListener('close', () => {
                if (this._flushWs === ws) this._finishPttFlush();
            }, { once: true });
            try { ws.send(JSON.stringify({ type: 'CloseStream' })); } catch (_) {}
            // Safety cap in case the close never arrives.
            this._pttFlushTimer = setTimeout(() => this._finishPttFlush(), 2000);
        } else {
            // No open socket (still connecting, or no call): nothing more will arrive.
            this._finishPttFlush();
        }
    }

    /** Deliver the flushed PTT transcript, restore the pre-press mute state, re-open the socket. */
    _finishPttFlush() {
        if (!this._pttFlushing) return;
        this._pttFlushing = false;
        if (this._pttFlushTimer) { clearTimeout(this._pttFlushTimer); this._pttFlushTimer = null; }
        const flushWs = this._flushWs;
        this._flushWs = null;
        if (flushWs && flushWs.readyState < WebSocket.CLOSING) {
            try { flushWs.close(); } catch (_) {}
        }

        const text = this._flushText.trim();
        this._flushText = '';
        if (this._pttHolding) {
            // Already holding again: send both utterances together on that
            // release instead of starting a request mid-press.
            if (text) this.accumulatedText = this.accumulatedText ? text + ' ' + this.accumulatedText : text;
            return;
        }
        if (text && this.onResult) {
            console.log('PTT release — sending:', text);
            this.isProcessing = true;
            this.onResult(text);   // call BEFORE restoring mute so the gate is open
        }
        // Back to the pre-press state: muted in PTT mode, listening after a hotkey
        // press during hands-free mode (always muting here left hands-free dead).
        this._micMuted = !!this._pttRestoreMuted;

        // Re-open the socket now so the next press (or hands-free listening)
        // streams at once instead of losing its first words to the handshake.
        if (this.isListening && !this._ws) {
            this._connectWebSocket().then(ok => {
                if (ok) this._startAudioPipeline();
            });
        }
    }

    pttMute() {
        if (this._usingFallback && this._fallback) { this._fallback.pttMute(); return; }
        this._pttHolding = false;
        this._micMuted = true;
        this._pttRestoreMuted = true;
        this.isProcessing = true;
        this.accumulatedText = '';
        if (this._accumulationTimer) { clearTimeout(this._accumulationTimer); this._accumulationTimer = null; }
    }

    pttUnmute() {
        if (this._usingFallback && this._fallback) { this._fallback.pttUnmute(); return; }
        this._micMuted = false;
        this._pttRestoreMuted = false;
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

    _connectWebSocket() {
        // One attempt at a time: a PTT press during the token fetch would
        // otherwise open a second socket and orphan the first.
        if (!this._connectPromise) {
            this._connectPromise = this._openWebSocket().finally(() => { this._connectPromise = null; });
        }
        return this._connectPromise;
    }

    async _openWebSocket() {
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
                endpointing: '500',
                encoding: 'linear16',
                sample_rate: '16000',
                channels: '1',
            });

            const url = `wss://api.deepgram.com/v1/listen?${params}`;
            this._intentionalClose = false;

            let ws;
            try {
                ws = new WebSocket(url, ['token', apiKey]);
            } catch (err) {
                console.error('Deepgram WebSocket creation failed:', err);
                resolve(false);
                return;
            }
            this._ws = ws;

            const timeout = setTimeout(() => {
                if (ws.readyState === WebSocket.CONNECTING) {
                    console.error('Deepgram WebSocket connection timeout');
                    ws.close();
                    resolve(false);
                }
            }, 5000);

            ws.onopen = () => {
                clearTimeout(timeout);
                console.log('Deepgram WebSocket connected');
                this._reconnectFailures = 0;
                this._startKeepAlive();
                resolve(true);
            };

            ws.onmessage = (event) => {
                // A replaced socket is ignored; the one flushing a PTT release
                // still delivers its last finals.
                if (ws !== this._ws && ws !== this._flushWs) return;
                this._handleMessage(event, ws);
            };

            ws.onerror = (event) => {
                clearTimeout(timeout);
                console.error('Deepgram WebSocket error:', event);
            };

            ws.onclose = (event) => {
                clearTimeout(timeout);
                console.log(`Deepgram WebSocket closed (code: ${event.code})`);
                // A replaced or flushing socket closing must not stop the current
                // socket's KeepAlive or trigger a reconnect.
                if (ws !== this._ws) {
                    resolve(false);
                    return;
                }
                this._stopKeepAlive();

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

                resolve(false);
            };
        });
    }

    _closeWebSocket() {
        this._stopKeepAlive();
        // Abandon an in-progress PTT flush (call ended, or falling back).
        this._pttFlushing = false;
        if (this._pttFlushTimer) { clearTimeout(this._pttFlushTimer); this._pttFlushTimer = null; }
        if (this._flushWs) {
            try { this._flushWs.close(); } catch (_) {}
            this._flushWs = null;
        }
        this._flushText = '';
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
        if (!this._stream || !this._stream.active) return;

        // Idempotent — reuse the existing audio context across WebSocket reconnects.
        //
        // CRITICAL: do NOT tear down and recreate the AudioContext on every reconnect.
        // Modern Chrome auto-suspends new AudioContexts unless they're created from a
        // user-gesture call stack. Subsequent reconnects (e.g. from pttActivate after
        // an idle WS close) call this method from a Promise.then() callback which has
        // lost the user-gesture context — so a freshly-created context starts in
        // 'suspended' state and the ScriptProcessor never fires audioprocess events,
        // meaning zero PCM bytes get sent to Deepgram. Symptom: PTT button activates,
        // WebSocket connects, but no transcripts ever come back.
        if (this._audioCtx && this._processorNode && this._sourceNode) {
            // Already running — just unsuspend if needed (free if already running)
            if (this._audioCtx.state === 'suspended') {
                this._audioCtx.resume().catch(() => {});
            }
            return;
        }

        // First-time setup (called from start() under user-gesture context)
        this._audioCtx = new AudioContext({ sampleRate: 16000 });
        // Resume in case the constructor returned a suspended context anyway
        // (e.g. browser autoplay policy variations)
        this._audioCtx.resume().catch(() => {});
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

    _handleMessage(event, ws) {
        let data;
        try {
            data = JSON.parse(event.data);
        } catch (_) {
            return;
        }
        // Sent by the socket flushing a released PTT press (after CloseStream)
        const fromFlush = !!ws && ws === this._flushWs;

        // Speech started event (Deepgram VAD)
        if (data.type === 'SpeechStarted') {
            // Could emit event for UI feedback
            return;
        }

        // UtteranceEnd — Deepgram detected end of utterance (silence after speech)
        // Use same accumulation window as speech_final so pauses don't split mid-sentence
        if (data.type === 'UtteranceEnd') {
            // PTT delivers on release, never on a pause mid-hold.
            if (this.accumulatedText.trim() && !this._pttHolding && !fromFlush) {
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

            // Held, or released and waiting for the last finals after CloseStream
            const ptt = this._pttHolding || fromFlush;
            if (fromFlush && !isFinal) return;

            // Ignore during mute (TTS playing)
            if (this._muteActive || (this.isProcessing && !ptt)) return;

            if (isFinal) {
                // Filter hallucinations
                if (this._isHallucination(transcript)) {
                    console.log('Deepgram Streaming: filtered hallucination:', transcript);
                    return;
                }

                console.log('Deepgram Streaming final:', transcript);
                if (this.onListenFinal) this.onListenFinal(transcript.trim());

                if (fromFlush) {
                    this._flushText = this._flushText
                        ? this._flushText + ' ' + transcript.trim()
                        : transcript.trim();
                    return;
                }

                // PTT mode: accumulate and wait for pttRelease to send
                if (ptt) {
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
                // Interim result — user is STILL speaking. Cancel any pending
                // accumulation timer so we don't fire it mid-sentence.
                //
                // Deepgram's endpointing=300 emits speech_final after just 300ms
                // of silence (a normal mid-sentence pause). That schedules the
                // 1.5s flush timer. Without this cancellation, if the user
                // resumes speaking, the timer keeps counting down and fires
                // while they're still mid-thought — chopping the transcript.
                if (this._accumulationTimer) {
                    clearTimeout(this._accumulationTimer);
                    this._accumulationTimer = null;
                }
                // Show live feedback
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
