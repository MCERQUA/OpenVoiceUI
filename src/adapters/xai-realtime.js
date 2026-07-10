/**
 * xAI Grok Realtime Adapter
 *
 * Connects to /ws/xai-realtime (server-side proxy to xAI's Grok Voice API).
 * Uses the OpenAI Realtime API protocol: PCM16 mono 24 kHz audio, JSON events.
 *
 * Audio pipeline:
 *   MIC:  getUserMedia → ScriptProcessorNode → Float32 → resample to 24 kHz
 *         → Int16 (PCM16) → base64 → input_audio_buffer.append JSON event
 *
 *   TTS:  response.audio.delta base64 → Int16Array → Float32Array
 *         → AudioBuffer (24 kHz) → AudioBufferSourceNode → destination
 *         (AudioContext auto-resamples the 24 kHz buffer to native context rate)
 *
 * Server-side VAD is used — xAI detects speech start/stop automatically.
 *
 * Note: ScriptProcessorNode is deprecated in favour of AudioWorklet but has
 * universal support and avoids shipping a separate worker file. Migration to
 * AudioWorklet is straightforward when a bundler is adopted.
 *
 * Ref: future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md
 */

import { AgentEvents, AgentActions } from '../core/EventBridge.js';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const XAI_SAMPLE_RATE   = 24000;   // Required by xAI Realtime API
const SCRIPT_PROC_SIZE  = 4096;    // ScriptProcessorNode buffer (≈ 85ms @ 48kHz)
const MAX_RECONNECT_MS  = 30_000;
const RECV_TIMEOUT_MS   = 300_000; // 5-min idle guard (kept on the server side)

// ─────────────────────────────────────────────────────────────────────────────
// PCM helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Linear-interpolation resample Float32 audio from inputRate → outputRate.
 * @param {Float32Array} input
 * @param {number} inputRate
 * @param {number} outputRate
 * @returns {Float32Array}
 */
function resampleF32(input, inputRate, outputRate) {
    if (inputRate === outputRate) return input;
    const ratio        = inputRate / outputRate;
    const outputLength = Math.round(input.length / ratio);
    const output       = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
        const srcIdx = i * ratio;
        const lo     = Math.floor(srcIdx);
        const hi     = Math.min(lo + 1, input.length - 1);
        const frac   = srcIdx - lo;
        output[i]    = input[lo] * (1 - frac) + input[hi] * frac;
    }
    return output;
}

/**
 * Convert Float32Array (−1 … 1) to Int16Array (PCM16).
 * @param {Float32Array} f32
 * @returns {Int16Array}
 */
function f32ToI16(f32) {
    const i16 = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
        const s  = Math.max(-1, Math.min(1, f32[i]));
        i16[i]   = s < 0 ? s * 32768 : s * 32767;
    }
    return i16;
}

/**
 * Convert Int16Array (PCM16) back to Float32Array.
 * @param {Int16Array} i16
 * @returns {Float32Array}
 */
function i16ToF32(i16) {
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) {
        f32[i] = i16[i] / 32768.0;
    }
    return f32;
}

/**
 * Base64-encode a typed array's underlying buffer.
 * @param {Int16Array} int16arr
 * @returns {string}
 */
function int16ToBase64(int16arr) {
    const uint8   = new Uint8Array(int16arr.buffer);
    let   binary  = '';
    const len     = uint8.byteLength;
    for (let i = 0; i < len; i++) {
        binary += String.fromCharCode(uint8[i]);
    }
    return btoa(binary);
}

/**
 * Decode a base64 string to Int16Array (PCM16).
 * @param {string} b64
 * @returns {Int16Array}
 */
function base64ToI16(b64) {
    const binary = atob(b64);
    const len    = binary.length;
    const uint8  = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
        uint8[i] = binary.charCodeAt(i);
    }
    return new Int16Array(uint8.buffer);
}

/**
 * RMS of a Float32 sample block, normalised to 0–1.
 * @param {Float32Array} samples
 * @returns {number}
 */
function rms(samples) {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return Math.sqrt(sum / samples.length);
}

// ─────────────────────────────────────────────────────────────────────────────
// XAIRealtimeAdapter
// ─────────────────────────────────────────────────────────────────────────────

export const XAIRealtimeAdapter = {
    name: 'Grok Voice',

    /**
     * Capabilities reported to the UI shell.
     * xAI handles STT + LLM + TTS internally — no separate STT provider needed.
     */
    capabilities: [
        'canvas',
    ],

    // ── Private state ─────────────────────────────────────────────────────────
    _bridge:          null,
    _config:          null,
    _socket:          null,          // WebSocket to /ws/xai-realtime
    _audioContext:    null,          // For mic capture and TTS playback
    _mediaStream:     null,          // Raw mic stream
    _sourceNode:      null,          // MediaStreamSourceNode
    _scriptProcessor: null,          // ScriptProcessorNode (mic → PCM16)
    _silentGain:      null,          // Mutes mic audio output (prevent feedback)
    _nativeRate:      48000,         // AudioContext native sample rate (detected)
    _audioQueue:      [],            // Queued PCM Float32Array chunks for playback
    _isPlaying:       false,         // TTS playback in progress
    _currentSource:   null,          // Active AudioBufferSourceNode
    _nextPlayAt:      0,             // Scheduled play time for gapless queuing
    _reconnectTimer:  null,
    _reconnectDelay:  1000,
    _destroyed:       false,
    _unsubscribers:   [],
    _sessionReady:    false,         // True after session.created received

    // ─────────────────────────────────────────────────────────────────────────
    // INIT
    // ─────────────────────────────────────────────────────────────────────────

    async init(bridge, config) {
        this._bridge    = bridge;
        this._config    = config || {};
        this._destroyed = false;
        this._audioQueue      = [];
        this._isPlaying       = false;
        this._reconnectDelay  = 1000;
        this._sessionReady    = false;

        console.log('[xAI-Realtime] Initializing adapter');

        // Check server-side config availability
        try {
            const res  = await fetch('/api/xai/config');
            const data = await res.json();
            if (!data.available) {
                console.warn('[xAI-Realtime] XAI_API_KEY not configured on server');
                // Don't throw — let start() fail with a clear error
            }
        } catch (err) {
            console.warn('[xAI-Realtime] Config check failed:', err.message);
        }

        // Subscribe to UI → Agent actions
        this._unsubscribers.push(
            bridge.on(AgentActions.END_SESSION,    () => this.stop()),
            bridge.on(AgentActions.CONTEXT_UPDATE, (d) => this._sendContextUpdate(d.text)),
            bridge.on(AgentActions.FORCE_MESSAGE,  (d) => this._sendForceMessage(d.text)),
        );

        console.log('[xAI-Realtime] Adapter initialized — call start() to connect');
    },

    // ─────────────────────────────────────────────────────────────────────────
    // START
    // ─────────────────────────────────────────────────────────────────────────

    async start() {
        if (this._destroyed) return;

        try {
            // AudioContext must be created inside a user-gesture handler.
            this._audioContext = new (window.AudioContext || window.webkitAudioContext)();
            if (this._audioContext.state === 'suspended') {
                await this._audioContext.resume();
            }
            this._nativeRate = this._audioContext.sampleRate;
            this._nextPlayAt = this._audioContext.currentTime;

            console.log(`[xAI-Realtime] AudioContext @ ${this._nativeRate} Hz`);

            await this._connect();

        } catch (err) {
            console.error('[xAI-Realtime] Start failed:', err);
            this._bridge.emit(AgentEvents.ERROR,   { message: err.message });
            this._bridge.emit(AgentEvents.MOOD,    { mood: 'sad' });
        }
    },

    // ─────────────────────────────────────────────────────────────────────────
    // STOP
    // ─────────────────────────────────────────────────────────────────────────

    async stop() {
        clearTimeout(this._reconnectTimer);
        this._stopMicrophone();
        this._stopAudioPlayback();

        if (this._socket) {
            try {
                if (this._socket.readyState === WebSocket.OPEN) {
                    this._socket.close(1000, 'User ended session');
                }
            } catch (_) {}
            this._socket       = null;
            this._sessionReady = false;
        }

        this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'idle' });
        this._bridge.emit(AgentEvents.DISCONNECTED);
        this._bridge.emit(AgentEvents.MOOD,          { mood: 'neutral' });
        console.log('[xAI-Realtime] Session stopped');
    },

    // ─────────────────────────────────────────────────────────────────────────
    // DESTROY
    // ─────────────────────────────────────────────────────────────────────────

    async destroy() {
        this._destroyed = true;
        await this.stop();

        this._unsubscribers.forEach(unsub => unsub());
        this._unsubscribers = [];

        if (this._audioContext) {
            try { await this._audioContext.close(); } catch (_) {}
            this._audioContext = null;
        }

        console.log('[xAI-Realtime] Adapter destroyed');
    },

    // ─────────────────────────────────────────────────────────────────────────
    // PRIVATE — WebSocket connection to server proxy
    // ─────────────────────────────────────────────────────────────────────────

    async _connect() {
        if (this._destroyed) return;

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl    = `${protocol}//${window.location.host}/ws/xai-realtime`;

        console.log('[xAI-Realtime] Connecting to proxy...');
        this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'idle' });

        this._socket                = new WebSocket(wsUrl);
        this._socket.onopen         = () => this._onOpen();
        this._socket.onmessage      = (evt) => this._onMessage(evt);
        this._socket.onclose        = (evt) => this._onClose(evt);
        this._socket.onerror        = (evt) => this._onError(evt);
    },

    _onOpen() {
        console.log('[xAI-Realtime] Proxy WebSocket opened — awaiting session.created');
        this._reconnectDelay = 1000;
        // session.update is sent when we receive session.created from xAI
    },

    _onClose(evt) {
        console.log(`[xAI-Realtime] WebSocket closed: ${evt.code} ${evt.reason}`);
        this._stopMicrophone();
        this._sessionReady = false;

        if (!this._destroyed && evt.code !== 1000) {
            console.log(`[xAI-Realtime] Reconnecting in ${this._reconnectDelay}ms...`);
            this._reconnectTimer = setTimeout(async () => {
                if (!this._destroyed) {
                    try { await this._connect(); }
                    catch (err) {
                        console.error('[xAI-Realtime] Reconnect failed:', err);
                        this._bridge.emit(AgentEvents.ERROR, { message: 'Reconnect failed' });
                    }
                }
            }, this._reconnectDelay);

            this._reconnectDelay = Math.min(this._reconnectDelay * 2, MAX_RECONNECT_MS);
            this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'idle' });
            this._bridge.emit(AgentEvents.MOOD,          { mood: 'sad' });

        } else if (!this._destroyed) {
            this._bridge.emit(AgentEvents.DISCONNECTED);
            this._bridge.emit(AgentEvents.MOOD,  { mood: 'neutral' });
        }
    },

    _onError(evt) {
        console.error('[xAI-Realtime] WebSocket error:', evt);
        this._bridge.emit(AgentEvents.ERROR, { message: 'xAI Realtime WebSocket error' });
        this._bridge.emit(AgentEvents.MOOD,  { mood: 'sad' });
    },

    // ─────────────────────────────────────────────────────────────────────────
    // PRIVATE — Inbound event routing
    // ─────────────────────────────────────────────────────────────────────────

    _onMessage(evt) {
        let msg;
        try {
            msg = JSON.parse(evt.data);
        } catch (e) {
            console.warn('[xAI-Realtime] Non-JSON frame — ignoring');
            return;
        }

        const type = msg.type || '';
        // console.debug('[xAI-Realtime] ←', type);

        switch (type) {

            case 'session.created':
                this._onSessionCreated(msg);
                break;

            case 'session.updated':
                console.log('[xAI-Realtime] Session configured');
                break;

            case 'input_audio_buffer.speech_started':
                this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'listening' });
                this._bridge.emit(AgentEvents.MOOD,          { mood: 'listening' });
                break;

            case 'input_audio_buffer.speech_stopped':
                this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'thinking' });
                this._bridge.emit(AgentEvents.MOOD,          { mood: 'thinking' });
                break;

            case 'conversation.item.input_audio_transcription.completed': {
                const userText = msg.transcript || '';
                if (userText) {
                    this._bridge.emit(AgentEvents.TRANSCRIPT, { text: userText, partial: false });
                    this._bridge.emit(AgentEvents.MESSAGE,    { role: 'user', text: userText, final: true });
                }
                break;
            }

            case 'response.audio_transcript.delta': {
                const partial = msg.delta || '';
                if (partial) {
                    this._bridge.emit(AgentEvents.TRANSCRIPT, { text: partial, partial: true });
                }
                break;
            }

            case 'response.audio_transcript.done': {
                const fullText = msg.transcript || '';
                if (fullText) {
                    this._bridge.emit(AgentEvents.MESSAGE, { role: 'assistant', text: fullText, final: true });
                }
                break;
            }

            case 'response.audio.delta':
                if (msg.delta) {
                    this._queueAudioDelta(msg.delta);
                }
                break;

            case 'response.audio.done':
                // Audio stream for this response is complete — queue will drain naturally
                break;

            case 'response.done':
                // Full response turn complete
                // Don't stop playback here — let the queued audio drain first
                break;

            case 'response.created':
                this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'speaking' });
                this._bridge.emit(AgentEvents.MOOD,          { mood: 'happy' });
                break;

            case 'error': {
                const errMsg = msg.error?.message || msg.message || 'Unknown xAI error';
                const errCode = msg.error?.code   || msg.code    || '';
                console.error(`[xAI-Realtime] Server error [${errCode}]: ${errMsg}`);
                this._bridge.emit(AgentEvents.ERROR, { message: errMsg, code: errCode });
                this._bridge.emit(AgentEvents.MOOD,  { mood: 'sad' });
                break;
            }

            default:
                // Silently ignore unknown event types (forward-compat)
                break;
        }
    },

    _onSessionCreated(msg) {
        console.log('[xAI-Realtime] Session created — configuring session');
        this._sessionReady = true;

        // Get system_prompt from profile config
        const instructions = this._config.system_prompt
            || 'You are Grok, a witty and helpful voice assistant created by xAI. Be conversational, clear, and concise.';

        this._sendJSON({
            type: 'session.update',
            session: {
                modalities:   ['audio', 'text'],
                instructions,
                voice:        'Celeste',
                input_audio_format:  'pcm16',
                output_audio_format: 'pcm16',
                input_audio_transcription: {
                    model: 'whisper-1',
                },
                turn_detection: {
                    type:                 'server_vad',
                    threshold:            0.5,
                    prefix_padding_ms:    300,
                    silence_duration_ms:  600,
                },
            },
        });

        this._bridge.emit(AgentEvents.CONNECTED);
        this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'listening' });
        this._bridge.emit(AgentEvents.MOOD,          { mood: 'happy' });

        // Start mic now that session is ready
        this._startMicrophone();
    },

    // ─────────────────────────────────────────────────────────────────────────
    // PRIVATE — Outbound messages
    // ─────────────────────────────────────────────────────────────────────────

    _sendJSON(payload) {
        if (this._socket && this._socket.readyState === WebSocket.OPEN) {
            this._socket.send(JSON.stringify(payload));
        }
    },

    /**
     * Silently update the session instructions with new context.
     * Does NOT trigger a response — purely injects background info.
     */
    _sendContextUpdate(text) {
        if (!text) return;
        const instructions = this._config.system_prompt
            ? `${this._config.system_prompt}\n\n[Context update]: ${text}`
            : text;

        this._sendJSON({
            type: 'session.update',
            session: { instructions },
        });
    },

    /**
     * Inject a user-turn message that the agent MUST respond to.
     * Creates a conversation item then triggers a response.
     */
    _sendForceMessage(text) {
        if (!text) return;

        this._sendJSON({
            type: 'conversation.item.create',
            item: {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text }],
            },
        });

        this._sendJSON({ type: 'response.create' });
    },

    // ─────────────────────────────────────────────────────────────────────────
    // PRIVATE — Microphone capture → PCM16 24 kHz → base64 → WebSocket
    // ─────────────────────────────────────────────────────────────────────────

    async _startMicrophone() {
        if (!this._audioContext) return;

        try {
            this._mediaStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    channelCount:     1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl:  true,
                },
            });

            this._sourceNode = this._audioContext.createMediaStreamSource(this._mediaStream);

            // ScriptProcessorNode — fires onaudioprocess with Float32 samples.
            // bufferSize=4096 → ~85ms at 48 kHz, ≈ 100ms target chunk.
            // Note: ScriptProcessorNode is deprecated but universally supported.
            this._scriptProcessor = this._audioContext.createScriptProcessor(
                SCRIPT_PROC_SIZE, 1, 1
            );

            this._scriptProcessor.onaudioprocess = (event) => {
                this._onMicAudio(event.inputBuffer.getChannelData(0));
            };

            // Route: source → processor → silentGain → destination
            // The silentGain at 0 prevents mic audio from being heard while
            // keeping the node alive in the audio graph.
            this._silentGain = this._audioContext.createGain();
            this._silentGain.gain.value = 0;

            this._sourceNode.connect(this._scriptProcessor);
            this._scriptProcessor.connect(this._silentGain);
            this._silentGain.connect(this._audioContext.destination);

            console.log(`[xAI-Realtime] Microphone started @ ${this._nativeRate} Hz`);

        } catch (err) {
            console.error('[xAI-Realtime] Microphone access denied:', err);
            this._bridge.emit(AgentEvents.ERROR, {
                message: 'Microphone access denied. Please allow microphone access.',
            });
        }
    },

    _onMicAudio(float32Samples) {
        if (!this._socket || this._socket.readyState !== WebSocket.OPEN) return;
        if (!this._sessionReady) return;

        // Emit audio level for mouth animation
        const level = Math.min(rms(float32Samples) * 4, 1);  // scale up — mic RMS is small
        this._bridge.emit(AgentEvents.AUDIO_LEVEL, { level });

        // Resample to XAI_SAMPLE_RATE if needed
        const resampled = resampleF32(float32Samples, this._nativeRate, XAI_SAMPLE_RATE);

        // Convert Float32 → Int16 → base64
        const int16  = f32ToI16(resampled);
        const b64    = int16ToBase64(int16);

        this._sendJSON({
            type:  'input_audio_buffer.append',
            audio: b64,
        });
    },

    _stopMicrophone() {
        if (this._scriptProcessor) {
            try {
                this._scriptProcessor.disconnect();
                this._scriptProcessor.onaudioprocess = null;
            } catch (_) {}
            this._scriptProcessor = null;
        }
        if (this._sourceNode) {
            try { this._sourceNode.disconnect(); } catch (_) {}
            this._sourceNode = null;
        }
        if (this._silentGain) {
            try { this._silentGain.disconnect(); } catch (_) {}
            this._silentGain = null;
        }
        if (this._mediaStream) {
            this._mediaStream.getTracks().forEach(t => t.stop());
            this._mediaStream = null;
        }
    },

    // ─────────────────────────────────────────────────────────────────────────
    // PRIVATE — TTS audio playback (PCM16 24 kHz from xAI → AudioContext)
    //
    // Chunks arrive as base64-encoded Int16 at 24 kHz. We decode each to a
    // Float32Array, create an AudioBuffer at 24 kHz (the AudioContext auto-
    // resamples to its native rate when playing), and schedule chunks back-to-
    // back for gapless playback.
    // ─────────────────────────────────────────────────────────────────────────

    _queueAudioDelta(b64) {
        if (!this._audioContext) return;

        const i16   = base64ToI16(b64);
        const f32   = i16ToF32(i16);

        this._audioQueue.push(f32);

        if (!this._isPlaying) {
            this._isPlaying  = true;
            this._nextPlayAt = this._audioContext.currentTime + 0.05; // 50ms startup buffer
            this._bridge.emit(AgentEvents.TTS_PLAYING);
            this._drainAudioQueue();
        }
    },

    _drainAudioQueue() {
        while (this._audioQueue.length > 0) {
            const f32 = this._audioQueue.shift();
            this._scheduleChunk(f32);
        }
        // Check for queue exhaustion periodically
        this._drainTimer = setTimeout(() => this._checkPlaybackDone(), 200);
    },

    _scheduleChunk(f32Samples) {
        if (!this._audioContext) return;

        const numFrames  = f32Samples.length;
        const buffer     = this._audioContext.createBuffer(
            1,              // mono
            numFrames,
            XAI_SAMPLE_RATE // AudioContext will auto-resample on playback
        );
        buffer.getChannelData(0).set(f32Samples);

        const source = this._audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(this._audioContext.destination);

        // Schedule gapless: play at _nextPlayAt
        const startAt = Math.max(this._nextPlayAt, this._audioContext.currentTime);
        source.start(startAt);

        // Duration in seconds at 24 kHz
        const duration    = numFrames / XAI_SAMPLE_RATE;
        this._nextPlayAt  = startAt + duration;
        this._currentSource = source;

        source.onended = () => {
            if (this._currentSource === source) {
                this._currentSource = null;
            }
        };
    },

    _checkPlaybackDone() {
        // If queue is empty and scheduled play time has passed, signal TTS done
        if (this._audioQueue.length > 0) {
            this._drainAudioQueue();
            return;
        }

        const now = this._audioContext ? this._audioContext.currentTime : 0;
        if (now >= this._nextPlayAt || !this._isPlaying) {
            this._isPlaying = false;
            this._bridge.emit(AgentEvents.TTS_STOPPED);
            this._bridge.emit(AgentEvents.STATE_CHANGED, { state: 'listening' });
            this._bridge.emit(AgentEvents.MOOD,          { mood: 'listening' });
        } else {
            // Still draining — check again after remaining scheduled duration
            const remaining = (this._nextPlayAt - now) * 1000 + 50;
            this._drainTimer = setTimeout(() => this._checkPlaybackDone(), remaining);
        }
    },

    _stopAudioPlayback() {
        clearTimeout(this._drainTimer);
        this._audioQueue = [];

        if (this._currentSource) {
            try { this._currentSource.stop(); } catch (_) {}
            this._currentSource = null;
        }

        this._isPlaying  = false;
        this._nextPlayAt = this._audioContext ? this._audioContext.currentTime : 0;
    },
};

export default XAIRealtimeAdapter;
