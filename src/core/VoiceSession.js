/**
 * VoiceSession — slim orchestrator (replaces monolithic ClawdBotMode)
 *
 * Wires together the extracted modules:
 *   - WebSpeechSTT  (P3-T3) — speech recognition
 *   - TTSPlayer     (P3-T4) — audio playback + waveform
 *   - FaceManager   (P3-T5) — face mood / amplitude
 *   - MusicPlayer   (P3-T6) — music + ducking (auto via EventBus tts:start/tts:stop)
 *   - EmotionEngine (P3-T8) — emotion inference → face mood
 *   - EventBus      (P3-T1) — pub/sub glue
 *
 * VoiceSession is NOT responsible for UI updates (transcript panel, action console,
 * canvas/music commands from AI text). It emits events on eventBus and callers
 * subscribe.  The one exception is the canvas/music command parser which is
 * included here because it is pure logic with no DOM dependency.
 *
 * Usage:
 *   import { VoiceSession } from './core/VoiceSession.js';
 *
 *   const session = new VoiceSession({ serverUrl: 'https://your-server' });
 *   await session.start();
 *
 *   // Subscribe via EventBus:
 *   import { eventBus } from './core/EventBus.js';
 *   eventBus.on('session:message',   ({ role, text }) => { ... });
 *   eventBus.on('session:streaming', ({ text }) => { ... });
 *   eventBus.on('session:thinking',  () => { ... });
 *   eventBus.on('session:listening', () => { ... });
 *   eventBus.on('session:error',     ({ message }) => { ... });
 *   eventBus.on('session:tool',      ({ name }) => { ... });
 *   eventBus.on('tts:start',         () => { ... });
 *   eventBus.on('tts:stop',          () => { ... });
 *
 * EventBus events emitted (inbound — modules listen):
 *   'tts:start'  consumed by MusicPlayer.duck(true)
 *   'tts:stop'   consumed by MusicPlayer.duck(false)
 *
 * ADR-009: simple manager pattern (no framework)
 */

import { eventBus } from './EventBus.js';
import { WebSpeechSTT, WakeWordDetector } from '../providers/WebSpeechSTT.js';
import { DeepgramStreamingSTT } from '../providers/DeepgramStreamingSTT.js';
import { TTSPlayer } from '../providers/TTSPlayer.js';
import { faceManager } from '../face/BaseFace.js';
import { emotionEngine } from './EmotionEngine.js';

/**
 * Create the STT instance based on the server profile setting.
 * Falls back to WebSpeechSTT if no profile or unknown provider.
 */
function _createSTT() {
    const provider = window._serverProfile?.stt?.provider || 'webspeech';
    if (provider === 'deepgram-streaming' || provider === 'deepgram') {
        console.log('[VoiceSession] STT provider: Deepgram Streaming');
        return new DeepgramStreamingSTT();
    }
    // webspeech / groq / other — use WebSpeechSTT as before
    console.log('[VoiceSession] STT provider: Chrome Web Speech');
    return new WebSpeechSTT();
}

export class VoiceSession {
    /**
     * @param {object} opts
     * @param {string} opts.serverUrl          — base URL of the Flask server
     * @param {WakeWordDetector} [opts.wakeDetector] — shared wake detector (optional)
     * @param {MusicPlayer}      [opts.musicPlayer]  — shared music player (optional)
     */
    constructor({ serverUrl = '', wakeDetector = null, musicPlayer = null } = {}) {
        this.serverUrl = serverUrl;
        this.musicPlayer = musicPlayer;

        // Sub-modules
        this.stt = _createSTT();
        this.tts = new TTSPlayer();
        this.wakeDetector = wakeDetector;

        // Session state
        this.sessionId = null;
        this._ttsPlaying = false;
        this._sessionGreeted = false;
        this._pendingGreeting = null;
        this._lastResponse = null;
        this._restartWakeAfter = false;
        this._active = false;
    }

    // ── Lifecycle ────────────────────────────────────────────────────────────

    /**
     * Initialize audio, wire up callbacks, send greeting, start STT.
     */
    async start() {
        if (this._active) return;
        this._active = true;

        // Start emotion engine (wires session:message → faceManager.setMood)
        emotionEngine.start();

        // Init TTS audio context (requires user gesture — caller must ensure this)
        await this.tts.init();

        // Wire TTS → face amplitude
        this.tts.onAmplitude = (value) => faceManager.setAmplitude(value);

        // Wire TTS speaking state → EventBus (MusicPlayer auto-ducks on these)
        this.tts.onSpeakingChange = (isSpeaking) => {
            this._ttsPlaying = isSpeaking;
            if (isSpeaking) {
                eventBus.emit('tts:start', {});
                // Mute STT immediately when TTS starts — clears any queued echo text
                // and blocks onresult until TTS finishes. PTT/text interrupts bypass this.
                if (this.stt.mute) this.stt.mute();
            } else {
                eventBus.emit('tts:stop', {});
                // After TTS ends, signal STT can resume
                this._resumeListening();
            }
        };

        // Wire STT results → sendMessage
        this.stt.onResult = (transcript) => {
            if (this._ttsPlaying) {
                console.log('[VoiceSession] Ignoring transcript during TTS:', transcript);
                return;
            }
            if (transcript && transcript.trim()) {
                this.sendMessage(transcript.trim());
            }
        };

        this.stt.onError = (error) => {
            console.error('[VoiceSession] STT error:', error);
            eventBus.emit('session:error', { message: `Microphone: ${error}` });
        };

        // Stop wake detector before starting STT (both use Web Speech API)
        if (this.wakeDetector?.isListening) {
            this.wakeDetector.stop();
            this._restartWakeAfter = true;
        }

        // Generate session ID
        this.sessionId = `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
        console.log('[VoiceSession] Session started:', this.sessionId);

        // Send greeting first (awaits TTS playback before starting STT)
        if (!this._sessionGreeted) {
            this._sessionGreeted = true;
            await this._sendGreeting();
        }

        // Start listening
        const started = await this.stt.start();
        if (started) {
            console.log('[VoiceSession] Listening started');
            eventBus.emit('session:listening', {});
        } else {
            eventBus.emit('session:error', { message: 'Failed to start microphone' });
        }

        eventBus.emit('session:start', { sessionId: this.sessionId });
    }

    /**
     * Stop STT, stop TTS, restore wake detector.
     */
    stop() {
        if (!this._active) return;
        this._active = false;

        this.stt.stop();
        this.tts.stop();
        emotionEngine.stop();

        this._sessionGreeted = false;
        this._pendingGreeting = null;

        // Restore wake detector
        if (this._restartWakeAfter && this.wakeDetector?.isSupported()) {
            this.wakeDetector.start();
            this._restartWakeAfter = false;
        }

        console.log('[VoiceSession] Session stopped');
        eventBus.emit('session:stop', {});
    }

    /**
     * Destroy resources (TTSPlayer AudioContext).
     */
    destroy() {
        this.stop();
        this.tts.destroy();
    }

    // ── Message sending ──────────────────────────────────────────────────────

    /**
     * Send a user message to the server, stream the response.
     * @param {string} text
     */
    async sendMessage(text) {
        if (!text?.trim()) return;

        // Prepend pending greeting context if first user reply
        let messageToSend = text.trim();
        if (this._pendingGreeting) {
            messageToSend = `[You just greeted with: "${this._pendingGreeting}"] User replied: ${messageToSend}`;
            this._pendingGreeting = null;
        }

        eventBus.emit('session:message', { role: 'user', text: text.trim() });
        eventBus.emit('session:thinking', {});
        faceManager.setMood('thinking');

        const provider = localStorage.getItem('voice_provider') || 'groq';
        const voice = localStorage.getItem('voice_voice') || 'M1';

        try {
            const response = await fetch(`${this.serverUrl}/api/conversation?stream=1`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: messageToSend,
                    tts_provider: provider,
                    voice: voice,
                    session_id: this.sessionId,
                    ui_context: this._getUIContext()
                })
            });

            if (!response.ok) {
                throw new Error(`API error: ${response.status}`);
            }

            await this._processStream(response.body);

        } catch (error) {
            console.error('[VoiceSession] sendMessage error:', error);
            faceManager.setMood('sad');
            eventBus.emit('session:error', { message: error.message });
            setTimeout(() => faceManager.setMood('neutral'), 2000);
        } finally {
            // Safety net: if no TTS played, re-enable STT after a delay
            setTimeout(() => {
                if (!this._ttsPlaying && this._active && !this.stt.isListening) {
                    console.log('[VoiceSession] Safety net: restarting STT');
                    if (this.stt.resetProcessing) this.stt.resetProcessing();
                    this.stt.start();
                    eventBus.emit('session:listening', {});
                }
            }, 2000);
        }
    }

    // ── Stream processing ────────────────────────────────────────────────────

    /**
     * Read and process NDJSON stream from /api/conversation.
     * @param {ReadableStream} body
     */
    async _processStream(body) {
        const reader = body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let streamingText = '';
        let firstDelta = false;
        const processedCmds = new Set();

        try {
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });

                let newlineIdx;
                while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
                    const line = buffer.slice(0, newlineIdx).trim();
                    buffer = buffer.slice(newlineIdx + 1);
                    if (!line) continue;

                    let data;
                    try {
                        data = JSON.parse(line);
                    } catch (_) {
                        console.warn('[VoiceSession] Failed to parse stream line:', line);
                        continue;
                    }

                    if (data.type === 'delta') {
                        streamingText += data.text;
                        if (!firstDelta) {
                            firstDelta = true;
                            faceManager.setMood('neutral');
                            eventBus.emit('session:streaming', { text: this._stripCmdTags(streamingText), started: true });
                        } else {
                            eventBus.emit('session:streaming', { text: this._stripCmdTags(streamingText) });
                        }
                        this._checkCmdsInStream(streamingText, processedCmds);
                    }

                    if (data.type === 'action') {
                        eventBus.emit('session:action', { action: data.action });
                        if (data.action?.type === 'tool' && data.action?.phase === 'start') {
                            eventBus.emit('session:tool', { name: data.action.name });
                        }
                    }

                    if (data.type === 'text_done') {
                        const fullText = data.response || streamingText;
                        const cleanText = this._stripReasoningTokens(fullText);
                        const displayText = this._stripCmdTags(cleanText);

                        if (displayText === this._lastResponse) {
                            console.log('[VoiceSession] Skipping duplicate response');
                            reader.cancel();
                            return;
                        }
                        this._lastResponse = displayText;

                        // Forward server-provided emotion state to EmotionEngine (ADR-004)
                        if (data.emotion_state) {
                            eventBus.emit('session:emotion', data.emotion_state);
                        }

                        eventBus.emit('session:message', { role: 'assistant', text: displayText });
                        this._handleCmds(cleanText, processedCmds);

                        if (data.actions) {
                            eventBus.emit('session:actions', { actions: data.actions });
                        }
                    }

                    if (data.type === 'audio') {
                        if (data.audio) {
                            console.log(`[VoiceSession] TTS ready (tts:${data.timing?.tts_ms}ms)`);
                            // Mute STT before queuing audio — onSpeakingChange(true) will
                            // also mute, but muting here ensures no echo from audio buffering lag
                            if (this.stt.mute) this.stt.mute();
                            this.tts.queue(data.audio, data.audio_format === 'mp3' ? 'audio/mpeg' : 'audio/wav', data.gap_ms || 0);
                        } else {
                            console.warn('[VoiceSession] Audio event had no audio data');
                        }
                    }

                    if (data.type === 'session_reset') {
                        console.warn('[VoiceSession] Server session reset:', data.old, '→', data.new);
                        eventBus.emit('session:reset', { old: data.old, new: data.new, reason: data.reason });
                    }

                    if (data.type === 'error') {
                        console.error('[VoiceSession] Stream error:', data.error);
                        eventBus.emit('session:error', { message: data.error });
                    }
                }
            }
        } finally {
            try { reader.cancel(); } catch (_) {}
        }
    }

    // ── Greeting ─────────────────────────────────────────────────────────────

    /**
     * Play a random greeting via TTS before starting STT.
     * Returns a Promise that resolves when TTS finishes.
     */
    async _sendGreeting() {
        const greetings = [
            "Hey there! What can I help you with?",
            "Ready when you are. What's up?",
            "Voice assistant online. What do you need?",
            "I'm listening. Go ahead.",
            "Hello! What would you like to do?",
            "Standing by. What can I do for you?",
            "At your service. What's on your mind?",
            "Hey! What are we working on?",
            "Online and ready. Fire away.",
            "What's up? I'm all ears."
        ];

        const greeting = greetings[Math.floor(Math.random() * greetings.length)];
        this._pendingGreeting = greeting;

        eventBus.emit('session:message', { role: 'assistant', text: greeting });

        return new Promise(async (resolve) => {
            try {
                const provider = localStorage.getItem('voice_provider') || 'groq';
                const voice = localStorage.getItem('voice_voice') || 'M1';

                const response = await fetch(`${this.serverUrl}/api/tts/generate`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text: greeting, provider, voice })
                });

                if (!response.ok) {
                    resolve();
                    return;
                }

                const blob = await response.blob();
                const base64 = await this._blobToBase64(blob);
                await this.tts.play(base64);
                resolve();

            } catch (error) {
                console.error('[VoiceSession] Greeting TTS error:', error);
                resolve();
            }
        });
    }

    // ── Command parsing ──────────────────────────────────────────────────────

    /**
     * Strip [CANVAS:...], [MUSIC_PLAY], etc. tags from display text.
     * @param {string} text
     * @returns {string}
     */
    _stripCmdTags(text) {
        if (!text) return '';
        return text
            .replace(/```html[\s\S]*?```/gi, '')
            .replace(/```[\s\S]*?```/g, '')
            .replace(/```html[\s\S]*/gi, '')
            .replace(/```[\s\S]*/g, '')
            .replace(/\[CANVAS_MENU\]/gi, '')
            .replace(/\[CANVAS:[^\]]*\]/gi, '')
            .replace(/\[MUSIC_PLAY(?::[^\]]*)?\]/gi, '')
            .replace(/\[MUSIC_STOP\]/gi, '')
            .replace(/\[MUSIC_NEXT\]/gi, '')
            .replace(/\[SESSION_RESET\]/gi, '')
            .replace(/\[SUNO_GENERATE:[^\]]*\]/gi, '')
            .replace(/\[SPOTIFY:[^\]]*\]/gi, '')
            .replace(/\[SLEEP\]/gi, '')
            .replace(/\[REGISTER_FACE:[^\]]*\]/gi, '')
            .replace(/\[SOUND:[^\]]*\]/gi, '')
            .replace(/\[CANVAS_SCREENSHOT\]/gi, '')
            .trim();
    }

    /**
     * Check for commands while stream is in progress — emit events, don't block.
     * Callers (UI) handle actual DOM/control actions.
     */
    _checkCmdsInStream(text, seen) {
        if (!text) return;

        if (/\[CANVAS_MENU\]/i.test(text) && !seen.has('CANVAS_MENU')) {
            seen.add('CANVAS_MENU');
            eventBus.emit('cmd:canvas_menu', {});
        }

        const canvasMatch = text.match(/\[CANVAS:([^\]]+)\]/i);
        if (canvasMatch && !seen.has('CANVAS_PAGE')) {
            seen.add('CANVAS_PAGE');
            eventBus.emit('cmd:canvas_page', { page: canvasMatch[1].trim() });
        }

        const musicPlay = text.match(/\[MUSIC_PLAY(?::([^\]]+))?\]/i);
        if (musicPlay && !seen.has('MUSIC_PLAY')) {
            seen.add('MUSIC_PLAY');
            const track = musicPlay[1]?.trim() || null;
            eventBus.emit('cmd:music_play', { track });
            if (this.musicPlayer) {
                track ? this.musicPlayer.play(track) : this.musicPlayer.play();
            }
        }

        if (/\[MUSIC_STOP\]/i.test(text) && !seen.has('MUSIC_STOP')) {
            seen.add('MUSIC_STOP');
            eventBus.emit('cmd:music_stop', {});
            if (this.musicPlayer) this.musicPlayer.stop();
        }

        if (/\[MUSIC_NEXT\]/i.test(text) && !seen.has('MUSIC_NEXT')) {
            seen.add('MUSIC_NEXT');
            eventBus.emit('cmd:music_next', {});
            if (this.musicPlayer) this.musicPlayer.next();
        }

        const sunoMatch = text.match(/\[SUNO_GENERATE:([^\]]+)\]/i);
        if (sunoMatch && !seen.has('SUNO_GENERATE')) {
            seen.add('SUNO_GENERATE');
            eventBus.emit('cmd:suno_generate', { prompt: sunoMatch[1].trim() });
        }

        const spotifyMatch = text.match(/\[SPOTIFY:([^|\]]+)(?:\|([^\]]+))?\]/i);
        if (spotifyMatch && !seen.has('SPOTIFY')) {
            seen.add('SPOTIFY');
            eventBus.emit('cmd:spotify', { track: spotifyMatch[1].trim(), artist: spotifyMatch[2]?.trim() || '' });
        }

        const soundMatch = text.match(/\[SOUND:([^\]]+)\]/i);
        if (soundMatch && !seen.has('SOUND')) {
            seen.add('SOUND');
            eventBus.emit('cmd:sound', { sound: soundMatch[1].trim() });
        }

        const faceMatch = text.match(/\[REGISTER_FACE:([^\]]+)\]/i);
        if (faceMatch && !seen.has('REGISTER_FACE')) {
            seen.add('REGISTER_FACE');
            eventBus.emit('cmd:register_face', { name: faceMatch[1].trim() });
        }

        if (/\[CANVAS_SCREENSHOT\]/i.test(text) && !seen.has('CANVAS_SCREENSHOT')) {
            seen.add('CANVAS_SCREENSHOT');
            eventBus.emit('cmd:canvas_screenshot', {});
        }

        if (/\[SLEEP\]/i.test(text) && !seen.has('SLEEP')) {
            seen.add('SLEEP');
            eventBus.emit('cmd:sleep', {});
        }
    }

    /**
     * Final command pass after text_done (catches anything missed during stream).
     */
    _handleCmds(text, seen) {
        this._checkCmdsInStream(text, seen);
        // AI music trigger scanning (phrase-based, not tag-based)
        if (this.musicPlayer?.checkTriggers) {
            this.musicPlayer.checkTriggers(text);
        }
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    /**
     * Resume STT after TTS playback ends.
     * Called from tts.onSpeakingChange(false).
     */
    _resumeListening() {
        if (!this._active) return;
        // 250ms settling delay: lets audio tail-off clear the mic before re-enabling
        // STT so the last fragment of TTS audio isn't captured as user speech.
        // (Reduced from 600ms — DeepgramStreamingSTT mutes its audio pipeline during
        // TTS so echo is already suppressed; 250ms is enough for speaker decay.)
        setTimeout(() => {
            if (!this._active) return;
            // resume() clears the mute flag AND restarts the engine — the engine
            // may have stopped during TTS because onend no longer auto-restarts
            // while muted. resetProcessing() alone won't restart a dead engine.
            if (this.stt.resume) {
                this.stt.resume();
            } else if (this.stt.resetProcessing) {
                this.stt.resetProcessing();
            }
            eventBus.emit('session:listening', {});
        }, 250);
    }

    /**
     * Strip LLM reasoning tokens (e.g., chain-of-thought preamble).
     * @param {string} text
     * @returns {string}
     */
    _stripReasoningTokens(text) {
        if (!text) return text;
        const patterns = [
            /^.*?I should.*?\./s,
            /^.*?NO_REPLY.*?\./s,
            /^.*?The user.*?\./s,
            /^.*?They say.*?\./s
        ];
        let cleaned = text;
        for (const p of patterns) {
            cleaned = cleaned.replace(p, '');
        }
        return cleaned.trim();
    }

    /**
     * Gather UI context for the conversation API.
     * @returns {object}
     */
    _getUIContext() {
        const ctx = {
            canvasDisplayed: window.canvasContext?.current_page ?? null,
            canvasVisible: false,
            musicPlaying: this.musicPlayer?.isPlaying ?? false,
            musicTrack: this.musicPlayer?.currentMetadata?.title ?? null,
            musicPanelOpen: this.musicPlayer ? this.musicPlayer.panelState !== 'closed' : false
        };
        return ctx;
    }

    /**
     * Convert a Blob to a base64 string.
     * @param {Blob} blob
     * @returns {Promise<string>}
     */
    _blobToBase64(blob) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => {
                const dataUrl = reader.result;
                // Strip the data URL prefix (e.g., "data:audio/wav;base64,")
                const base64 = dataUrl.split(',')[1];
                resolve(base64);
            };
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    }
}

export default VoiceSession;
