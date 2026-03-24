/**
 * app.js — Main application entry point
 *
 * Extracted from index.html inline <script type="module"> (P3-T9: thin shell).
 * Imports AppShell to inject DOM structure, then initializes all modules.
 *
 * Loaded by thin-shell index.html via: <script type="module" src="src/app.js">
 */
import { inject } from './ui/AppShell.js';

// Inject the application DOM structure before any module accesses the DOM
inject();

        import { WebSpeechSTT, WakeWordDetector } from '/src/providers/WebSpeechSTT.js';
        import { GroqSTT, GroqWakeWordDetector } from '/src/providers/GroqSTT.js';
        import { DeepgramSTT, DeepgramWakeWordDetector } from '/src/providers/DeepgramSTT.js';
        import { DeepgramStreamingSTT, DeepgramStreamingWakeWordDetector } from '/src/providers/DeepgramStreamingSTT.js';

        // ===== CONFIGURATION =====
        const CONFIG = {
            // AGENT_CONFIG is injected by Flask from AGENT_SERVER_URL env var.
            // Set AGENT_SERVER_URL in .env to point at a remote backend.
            // Falls back to same origin (correct for standard self-hosted deploys).
            serverUrl: (window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, ''),

            // TTS provider to use: 'supertonic', 'hume', 'elevenlabs', 'openai', etc.
            ttsProvider: 'groq',
            ttsVoice: 'autumn',

            // Hume EVI config (loaded from server)
            hume: {
                configId: null  // Will be fetched from /api/hume/token
            }
        };

        // ===== CANVAS URL RESOLVER =====
        // Rewrites external dev-domain URLs to same-domain /devsite-*/ proxies,
        // bypassing Cloudflare's cross-subdomain iframe blocking.
        // Map is injected by Flask from DEVSITE_MAP env var (set per user in compose/.env).
        // Example: {"dev-nick.jam-bot.com": "https://nick.jam-bot.com/devsite-printguys-web-2/"}
        function resolveCanvasUrl(url) {
            // Block private/internal IPs — browser can never reach Docker internals
            // Matches 10.x, 172.16-31.x, 192.168.x, localhost, 127.x
            const privateIpPattern = /^https?:\/\/(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+)(:\d+)?\//i;
            if (privateIpPattern.test(url)) {
                console.warn('[resolveCanvasUrl] Blocked private/internal URL:', url);
                return null;
            }
            const map = window.AGENT_CONFIG?.devsiteMap || {};
            for (const [from, to] of Object.entries(map)) {
                const fromUrl = from.includes('://') ? from : 'https://' + from;
                if (url.toLowerCase().startsWith(fromUrl.toLowerCase())) {
                    const remainder = url.slice(fromUrl.length).replace(/^\//, '');
                    return remainder ? to + remainder : to;
                }
            }
            return url;
        }

        // ===== PROVIDER MANAGER =====
        // Manages TTS provider selection (Supertonic, Hume, etc.)
        const ProviderManager = {
            selectedProvider: null,
            providers: [],
            currentVoice: 'autumn',
            _activeProfileId: 'default',

            async init() {
                // Load from server profile (shared across all devices for this deployment)
                try {
                    const resp = await fetch(CONFIG.serverUrl + '/api/profiles/active');
                    if (resp.ok) {
                        const profile = await resp.json();
                        this._activeProfileId = profile.id || 'default';
                        this.selectedProvider = profile.voice?.tts_provider || 'groq';
                        this.currentVoice = profile.voice?.voice_id || 'autumn';
                        // Expose profile globally so face/theme modules can read it
                        window._serverProfile = profile;
                    }
                } catch (e) {
                    console.warn('Failed to load server profile, using defaults:', e);
                    this.selectedProvider = 'groq';
                    this.currentVoice = 'autumn';
                }

                await this.loadProviders();
                this.initProviderUI();
            },

            async loadProviders() {
                try {
                    const response = await fetch(CONFIG.serverUrl + '/api/tts/providers');
                    const data = await response.json();
                    this.providers = data.providers || [];
                } catch (error) {
                    console.error('Failed to load providers:', error);
                    // Fallback to hardcoded config
                    this.providers = [
                        {
                            provider_id: 'supertonic',
                            name: 'Supertonic',
                            cost_per_minute: 0,
                            mode: 'tts-only',
                            voices: ['M1', 'M2', 'M3', 'M4', 'M5', 'F1', 'F2', 'F3', 'F4', 'F5'],
                            status: 'active'
                        },
                        {
                            provider_id: 'hume',
                            name: 'Hume EVI',
                            cost_per_minute: 0.032,
                            mode: 'tts-only',
                            status: 'active'
                        }
                    ];
                }
            },

            initProviderUI() {
                const select = document.getElementById('voice-provider-select');
                if (!select) return;

                // Populate select with available providers
                select.innerHTML = '';
                this.providers.forEach(p => {
                    const option = document.createElement('option');
                    option.value = p.provider_id;
                    if (p.status !== 'active') {
                        option.disabled = true;
                        option.textContent = `🗣️ ${p.name} [Unavailable]`;
                    } else {
                        const isFree = p.cost_per_minute === 0;
                        const costText = isFree ? 'Free' : `$${p.cost_per_minute}/min`;
                        option.textContent = `🗣️ ${p.name} (${costText})`;
                    }
                    select.appendChild(option);
                });

                // Set current selection
                select.value = this.selectedProvider;
                this.updateProviderStatus();
                this.updateVoiceUI();
            },

            updateProviderStatus() {
                const provider = this.providers.find(p => p.provider_id === this.selectedProvider);
                const statusEl = document.getElementById('provider-status');
                if (statusEl && provider) {
                    statusEl.textContent = provider.status === 'active' ? '✓ Active' : '✗ Inactive';
                    statusEl.style.color = provider.status === 'active' ? 'var(--green)' : 'var(--red)';
                }
            },

            updateVoiceUI() {
                const voiceSelect = document.getElementById('voice-select');
                const voiceGroup = document.getElementById('voice-select-group');
                if (!voiceSelect) return;

                // Find voices for current provider (builtin + cloned)
                const provider = this.providers.find(p => p.provider_id === this.selectedProvider);
                const voices = provider?.voices || [];
                const clonedVoices = provider?.cloned_voices || [];

                if (voices.length === 0 && clonedVoices.length === 0) {
                    // Hide voice selector if provider has no voice choices
                    if (voiceGroup) voiceGroup.style.display = 'none';
                    return;
                }

                if (voiceGroup) voiceGroup.style.display = '';
                voiceSelect.innerHTML = '';
                voices.forEach(v => {
                    const option = document.createElement('option');
                    option.value = v;
                    option.textContent = v;
                    voiceSelect.appendChild(option);
                });
                // Add cloned voices with visual grouping
                if (clonedVoices.length > 0) {
                    const optgroup = document.createElement('optgroup');
                    optgroup.label = 'Cloned Voices';
                    clonedVoices.forEach(cv => {
                        const option = document.createElement('option');
                        option.value = cv.voice_id;
                        option.textContent = cv.name;
                        optgroup.appendChild(option);
                    });
                    voiceSelect.appendChild(optgroup);
                }
                voiceSelect.value = this.currentVoice;
            },

            switchProvider(providerId) {
                if (providerId === this.selectedProvider) return;

                this.selectedProvider = providerId;
                this._saveVoiceToProfile();

                // Update VoiceConversation if it exists
                if (window.voiceAgent && window.voiceAgent.setTTSProvider) {
                    window.voiceAgent.setTTSProvider(providerId, this.currentVoice);
                }

                // Update UI
                this.updateProviderStatus();
                document.getElementById('voice-provider-select').value = providerId;
                this.updateVoiceUI();
            },

            setVoice(voice) {
                this.currentVoice = voice;
                this._saveVoiceToProfile();

                // Update voice agent if connected
                if (window.voiceAgent && window.voiceAgent.setTTSProvider) {
                    window.voiceAgent.setTTSProvider(this.selectedProvider, voice);
                }
            },

            _saveVoiceToProfile() {
                // Persist to server profile so all devices see the same settings
                fetch(CONFIG.serverUrl + '/api/profiles/' + this._activeProfileId, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ voice: { tts_provider: this.selectedProvider, voice_id: this.currentVoice } })
                }).catch(e => console.warn('Failed to save voice to profile:', e));
            }
        };

        // Expose to window
        window.providerManager = ProviderManager;

        // ===== QUICK SETTINGS — Agent selector in top drawer =====
        window.QuickSettings = {
            _profiles: [],  // cache of all profiles with their adapter_config
            _DEFAULT_PROFILE: 'default',

            async init() {
                try {
                    const res = await fetch('/api/profiles');
                    const data = await res.json();
                    const select = document.getElementById('voice-mode-select');
                    if (!select) return;

                    this._profiles = data.profiles || [];

                    // Determine which profile to use:
                    // 1. localStorage (last explicit user choice)
                    // 2. server's persisted active (survives restarts)
                    // 3. default
                    const savedProfile = localStorage.getItem('active_profile_id');
                    const targetProfile = savedProfile || data.active || this._DEFAULT_PROFILE;

                    // If localStorage says something different from what the server has,
                    // re-activate so the backend is in sync
                    if (targetProfile !== data.active) {
                        const exists = this._profiles.find(p => p.id === targetProfile);
                        if (exists) {
                            await fetch('/api/profiles/activate', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ profile_id: targetProfile })
                            });
                        }
                    }

                    const activeId = this._profiles.find(p => p.id === targetProfile)
                        ? targetProfile
                        : (data.active || this._DEFAULT_PROFILE);

                    select.innerHTML = '';
                    this._profiles.forEach(p => {
                        const opt = document.createElement('option');
                        opt.value = p.id;
                        opt.textContent = p.name;
                        if (p.id === activeId) opt.selected = true;
                        select.appendChild(opt);
                    });

                    const statusEl = document.getElementById('agent-status');
                    if (statusEl) {
                        const active = this._profiles.find(p => p.id === activeId);
                        statusEl.textContent = active ? `✓ ${active.name}` : '✓ Active';
                    }

                    // Sync agentId for the active profile
                    const activeProfile = this._profiles.find(p => p.id === activeId);
                    const agentId = activeProfile?.adapter_config?.agentId || null;
                    localStorage.setItem('gateway_agent_id', agentId || '');
                    if (activeProfile) TranscriptPanel.agentName = activeProfile.name;
                    localStorage.setItem('active_profile_id', activeId);

                    // Fetch full active profile and apply runtime settings
                    fetch('/api/profiles/active')
                        .then(r => r.json())
                        .then(p => { if (p.id) window.applyProfile(p); })
                        .catch(() => {});

                } catch (e) {
                    const select = document.getElementById('voice-mode-select');
                    if (select) {
                        select.innerHTML = '<option value="default">Assistant</option>';
                    }
                }
            },

            async switchAgent(profileId) {
                const statusEl = document.getElementById('agent-status');
                if (statusEl) statusEl.textContent = '⟳ Switching…';
                try {
                    const res = await fetch('/api/profiles/activate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ profile_id: profileId })
                    });
                    const data = await res.json();
                    if (statusEl) statusEl.textContent = data.ok ? `✓ ${profileId}` : '✗ Failed';

                    // Persist selection so it survives page reloads and browser restarts
                    localStorage.setItem('active_profile_id', profileId);

                    // Store the agentId for this profile so conversation.py routes correctly
                    const profile = this._profiles.find(p => p.id === profileId);
                    const agentId = profile?.adapter_config?.agentId || null;
                    localStorage.setItem('gateway_agent_id', agentId || '');
                    if (profile) TranscriptPanel.agentName = profile.name;
                    console.log(`[QuickSettings] switched to ${profileId}, agentId=${agentId || 'main'}`);
                    // Apply full profile settings from activate response
                    if (data.profile) window.applyProfile(data.profile);
                } catch (e) {
                    if (statusEl) statusEl.textContent = '✗ Error';
                }
            }
        };

        // ===== DJ SOUNDBOARD MODULE =====
        const DJSoundboard = {
            sounds: {
                'air_horn': { file: 'air_horn.mp3', triggers: ['air horn', 'airhorn', 'horn', 'bwaaah', 'bwaaa', 'bwah'] },
                'scratch_long': { file: 'scratch_long.mp3', triggers: ['scratch', 'scratching', 'wicka', 'wikka'] },
                'rewind': { file: 'rewind.mp3', triggers: ['rewind', 'pull up', 'pull it back', 'hold up', 'bring it back'] },
                'record_stop': { file: 'record_stop.mp3', triggers: ['record stop', 'stop the record'] },
                'crowd_cheer': { file: 'crowd_cheer.mp3', triggers: ['crowd cheer', 'applause', 'crowd goes wild', 'give it up', 'make some noise'] },
                'crowd_hype': { file: 'crowd_hype.mp3', triggers: ['crowd hype', 'hype them up', 'get hype'] },
                'yeah': { file: 'yeah.mp3', triggers: ['yeah!', 'yeahhh', 'oh yeah', 'yeeah'] },
                'lets_go': { file: 'lets_go.mp3', triggers: ["let's go!", 'lets go!', "let's goooo", 'here we go'] },
                'gunshot': { file: 'gunshot.mp3', triggers: ['gunshot', 'gun shot', 'bang bang', 'shots fired', 'pow pow', 'blat blat'] },
                'bruh': { file: 'bruh.mp3', triggers: ['bruh', 'bruhhh'] },
                'sad_trombone': { file: 'sad_trombone.mp3', triggers: ['sad trombone', 'womp womp', 'fail', 'wah wah'] }
            },
            audioCache: {},
            lastPlayTime: {},

            init() {
                // Preload common sounds
                ['air_horn', 'scratch_long', 'crowd_cheer', 'rewind', 'yeah', 'lets_go'].forEach(name => {
                    this.preload(name);
                });
                console.log('Soundboard initialized with', Object.keys(this.sounds).length, 'sounds');
            },

            preload(soundName) {
                if (!this.sounds[soundName]) return;
                const audio = new Audio(`${CONFIG.serverUrl}/sounds/${this.sounds[soundName].file}`);
                audio.preload = 'auto';
                this.audioCache[soundName] = audio;
            },

            play(soundName) {
                if (!this.sounds[soundName]) {
                    console.warn('Unknown sound:', soundName);
                    return;
                }

                // Debounce - don't play same sound within 500ms
                const now = Date.now();
                if (this.lastPlayTime[soundName] && now - this.lastPlayTime[soundName] < 500) {
                    return;
                }
                this.lastPlayTime[soundName] = now;

                // Use cached audio or create new
                let audio = this.audioCache[soundName];
                if (!audio || !audio.paused) {
                    // Create new audio element if cached one is playing
                    audio = new Audio(`${CONFIG.serverUrl}/sounds/${this.sounds[soundName].file}`);
                }

                audio.currentTime = 0;
                audio.volume = 0.4;  // Lower volume so voice is still audible
                audio.play().catch(e => console.error('Sound play error:', e));
                console.log('🎧 DJ Sound:', soundName);
            },

            // Check text for trigger words and play matching sounds
            checkTriggers(text) {
                if (!text) return;
                const lowerText = text.toLowerCase();

                for (const [soundName, config] of Object.entries(this.sounds)) {
                    for (const trigger of config.triggers) {
                        if (lowerText.includes(trigger)) {
                            this.play(soundName);
                            return; // Only play one sound per message
                        }
                    }
                }
            },

            // Get list of available sounds for AI prompt
            getSoundList() {
                return Object.entries(this.sounds).map(([name, config]) => {
                    return `- Say "${config.triggers[0]}" to play ${name.replace(/_/g, ' ')}`;
                }).join('\n');
            }
        };

        // ===== FACE MODULE =====
        const FaceModule = {
            leftEye: document.getElementById('left-eye'),
            rightEye: document.getElementById('right-eye'),
            leftPupil: document.getElementById('left-pupil-container'),
            rightPupil: document.getElementById('right-pupil-container'),
            currentMood: 'neutral',

            setMood(mood) {
                const validMoods = ['neutral', 'happy', 'sad', 'angry', 'thinking', 'surprised', 'listening'];
                if (!validMoods.includes(mood)) mood = 'neutral';

                // Remove all mood classes
                validMoods.forEach(m => {
                    this.leftEye.classList.remove(m);
                    this.rightEye.classList.remove(m);
                });

                // Add new mood
                if (mood !== 'neutral') {
                    this.leftEye.classList.add(mood);
                    this.rightEye.classList.add(mood);
                }
                this.currentMood = mood;
            },

            blink() {
                this.leftEye.classList.add('blinking');
                this.rightEye.classList.add('blinking');
                setTimeout(() => {
                    this.leftEye.classList.remove('blinking');
                    this.rightEye.classList.remove('blinking');
                }, 150);
            },

            updateEyePosition(x, y) {
                const centerX = window.innerWidth / 2;
                const centerY = window.innerHeight / 2;
                const maxOffset = 15;

                const offsetX = ((x - centerX) / centerX) * maxOffset;
                const offsetY = ((y - centerY) / centerY) * maxOffset;

                const transform = `translate(${offsetX}px, ${offsetY}px)`;
                this.leftPupil.style.transform = transform;
                this.rightPupil.style.transform = transform;
            },

            startRandomBehavior() {
                // Random blinking
                const scheduleBlink = () => {
                    setTimeout(() => {
                        this.blink();
                        scheduleBlink();
                    }, 2000 + Math.random() * 4000);
                };
                scheduleBlink();

                // Random looking
                let lastMouseMove = Date.now();
                document.addEventListener('mousemove', (e) => {
                    lastMouseMove = Date.now();
                    this.updateEyePosition(e.clientX, e.clientY);
                });

                const scheduleRandomLook = () => {
                    setTimeout(() => {
                        if (Date.now() - lastMouseMove > 2000) {
                            const x = window.innerWidth * (0.2 + Math.random() * 0.6);
                            const y = window.innerHeight * (0.15 + Math.random() * 0.5);
                            this.updateEyePosition(x, y);
                        }
                        scheduleRandomLook();
                    }, 1500 + Math.random() * 2500);
                };
                scheduleRandomLook();
            }
        };

        // ===== WAVEFORM MODULE =====
        const WaveformModule = {
            canvas: document.getElementById('waveform-canvas'),
            ctx: null,
            amplitude: 0,
            targetAmplitude: 0,
            animationId: null,
            noiseOffset: 0,
            isSpeaking: false,
            wavePhase: 0,
            _tdBuf: null,

            init() {
                this.ctx = this.canvas.getContext('2d');
                this.canvas.width = 200;
                this.canvas.height = 80;
                this.animate();
            },

            setAmplitude(value) {
                this.targetAmplitude = Math.min(1, Math.max(0, value));
                window.ttsAmplitude = this.targetAmplitude; // expose for audio-reactive faces
            },

            setSpeaking(speaking) {
                this.isSpeaking = speaking;
                if (!speaking) {
                    this.targetAmplitude = 0;
                }
            },

            animate() {
                const an = window.audioAnalyser;

                // Drive amplitude from real analyser data when available
                if (this.isSpeaking && an) {
                    // Compute RMS from time-domain for amplitude
                    if (!this._tdBuf || this._tdBuf.length !== an.fftSize) {
                        this._tdBuf = new Uint8Array(an.fftSize);
                    }
                    an.getByteTimeDomainData(this._tdBuf);
                    let sum = 0;
                    for (let i = 0; i < this._tdBuf.length; i++) {
                        const v = (this._tdBuf[i] - 128) / 128;
                        sum += v * v;
                    }
                    const rms = Math.sqrt(sum / this._tdBuf.length);
                    // Scale RMS up for visible mouth movement (speech RMS is typically 0.05-0.3)
                    this.targetAmplitude = Math.min(1, rms * 4.5);
                } else if (this.isSpeaking) {
                    // Fallback: no analyser — use random (legacy behavior)
                    this.wavePhase += 0.12 + this.amplitude * 0.1;
                    this.targetAmplitude = 0.3 + Math.random() * 0.7;
                }

                this.amplitude += (this.targetAmplitude - this.amplitude) * 0.25;
                this.noiseOffset += 0.3;

                this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

                const centerY = this.canvas.height / 2;
                const time = Date.now() * 0.001;

                // Draw real waveform from analyser when speaking with audio data
                if (this.amplitude > 0.05 && an && this._tdBuf) {
                    const td = this._tdBuf;
                    const w = this.canvas.width;
                    const h = this.canvas.height;
                    const halfH = h * 0.45; // vertical range

                    // Main waveform — draw actual time-domain audio
                    this.ctx.strokeStyle = '#00ffff'; // matches --cyan token
                    this.ctx.lineWidth = 2.5;
                    this.ctx.lineCap = 'round';
                    this.ctx.lineJoin = 'round';
                    this.ctx.beginPath();

                    const step = Math.max(1, Math.floor(td.length / 100));
                    const points = Math.floor(td.length / step);
                    for (let i = 0; i < points; i++) {
                        const x = (i / points) * w;
                        const normalizedX = i / points;
                        const edgeFade = Math.sin(normalizedX * Math.PI);
                        // Map sample: 128 = silence (center), 0/-128 = peaks
                        const sample = (td[i * step] - 128) / 128;
                        const y = centerY + sample * halfH * edgeFade * Math.min(1, this.amplitude * 2.5);

                        if (i === 0) this.ctx.moveTo(x, y);
                        else this.ctx.lineTo(x, y);
                    }
                    this.ctx.stroke();

                    // Glow layer
                    this.ctx.strokeStyle = `rgba(0, 255, 255, ${0.15 + this.amplitude * 0.2})`; // --cyan with dynamic alpha
                    this.ctx.lineWidth = 7;
                    this.ctx.stroke();

                } else if (this.amplitude > 0.1) {
                    // Fallback chaotic waveform (no analyser)
                    this.wavePhase += 0.12 + this.amplitude * 0.1;
                    this.ctx.strokeStyle = '#00ffff'; // matches --cyan token
                    this.ctx.lineWidth = 3;
                    this.ctx.lineCap = 'round';
                    this.ctx.lineJoin = 'round';
                    this.ctx.beginPath();

                    const points = 50;
                    for (let i = 0; i <= points; i++) {
                        const x = (i / points) * this.canvas.width;
                        const normalizedX = i / points;
                        const edgeFade = Math.sin(normalizedX * Math.PI);
                        const wave1 = Math.sin((normalizedX * 4 + this.wavePhase) * Math.PI * 2) * 8;
                        const wave2 = Math.sin((normalizedX * 7 + this.wavePhase * 1.3) * Math.PI * 2) * 12;
                        const wave3 = Math.sin((normalizedX * 13 + this.wavePhase * 0.7) * Math.PI * 2) * 5;
                        const noise = (Math.random() - 0.5) * 8 * this.amplitude;
                        const combined = (wave1 + wave2 + wave3 + noise) * this.amplitude * edgeFade;
                        const y = centerY + combined;
                        if (i === 0) this.ctx.moveTo(x, y);
                        else this.ctx.lineTo(x, y);
                    }
                    this.ctx.stroke();
                    this.ctx.strokeStyle = 'rgba(0, 255, 255, 0.3)'; // --cyan with alpha
                    this.ctx.lineWidth = 8;
                    this.ctx.stroke();

                } else {
                    // Quiet/idle state - gentle flat line with subtle pulse
                    this.ctx.strokeStyle = 'rgba(0, 255, 255, 0.4)'; // --cyan with alpha
                    this.ctx.lineWidth = 2;
                    this.ctx.lineCap = 'round';
                    this.ctx.beginPath();
                    const gentleWave = Math.sin(time * 2) * 2;
                    this.ctx.moveTo(0, centerY + gentleWave);
                    this.ctx.lineTo(this.canvas.width, centerY + gentleWave);
                    this.ctx.stroke();
                }

                this.animationId = requestAnimationFrame(() => this.animate());
            }
        };

        // ===== STATUS MODULE =====
        const StatusModule = {
            dot: document.getElementById('status-dot'),
            text: document.getElementById('status-text'),

            update(status, label) {
                this.dot.className = 'status-dot ' + status;
                this.text.textContent = label || status.toUpperCase();
            }
        };

        // ===== CAMERA MODULE =====
        const CameraModule = {
            video: document.getElementById('camera-video'),
            button: document.getElementById('camera-button'),
            canvas: document.getElementById('capture-canvas'),
            stream: null,
            frameInterval: null,
            faceInterval: null,
            currentIdentity: null,

            async toggle() {
                if (this.stream) {
                    this.stop();
                } else {
                    await this.start();
                }
            },

            async start() {
                try {
                    this.stream = await navigator.mediaDevices.getUserMedia({
                        video: { facingMode: 'user', width: 640, height: 480 }
                    });
                    this.video.srcObject = this.stream;
                    this.button.classList.add('active');
                    console.log('Camera enabled');

                    // Start sending frames to server
                    this.startFrameCapture();

                    // Identify face after a moment (only when not in a live call)
                    setTimeout(() => {
                        if (!window.voiceAgent?.isConnected) this.identifyFace();
                    }, 1000);

                    // Re-identify every 8 seconds while camera is on but call is not active
                    this.faceInterval = setInterval(() => {
                        if (!window.voiceAgent?.isConnected) this.identifyFace();
                    }, 8000);
                } catch (error) {
                    console.error('Camera error:', error);
                    UIModule.showError('Camera access denied');
                }
            },

            stop() {
                if (this.stream) {
                    this.stream.getTracks().forEach(track => track.stop());
                    this.stream = null;
                }
                this.video.srcObject = null;
                this.button.classList.remove('active');
                this.currentIdentity = null;
                if (this.frameInterval) {
                    clearInterval(this.frameInterval);
                    this.frameInterval = null;
                }
                if (this.faceInterval) {
                    clearInterval(this.faceInterval);
                    this.faceInterval = null;
                }
                console.log('Camera disabled');
            },

            startFrameCapture() {
                this.frameInterval = setInterval(() => {
                    if (!this.stream) return;

                    const ctx = this.canvas.getContext('2d');
                    this.canvas.width = 640;
                    this.canvas.height = 480;
                    ctx.drawImage(this.video, 0, 0, 640, 480);

                    const imageData = this.canvas.toDataURL('image/jpeg', 0.7);

                    fetch(`${CONFIG.serverUrl}/api/frame`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: imageData })
                    }).catch(e => console.error('Frame upload error:', e));
                }, 2000);
            },

            async identifyFace() {
                if (!this.stream) return;

                const ctx = this.canvas.getContext('2d');
                this.canvas.width = 640;
                this.canvas.height = 480;
                ctx.drawImage(this.video, 0, 0, 640, 480);

                const imageData = this.canvas.toDataURL('image/jpeg', 0.8);

                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/identify`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: imageData })
                    });
                    const data = await response.json();

                    if (data.name && data.name !== 'unknown') {
                        this.currentIdentity = data;
                        UIModule.showFaceNotification(`Recognized: ${data.name} (${data.confidence}%)`);
                        const statusEl = document.getElementById('face-id-status');
                        if (statusEl) {
                            statusEl.textContent = data.name + ' (' + data.confidence + '%)';
                            statusEl.className = 'face-id-status identified';
                        }
                        // Show in main status bar so it's visible without opening the face panel
                        if (!window.voiceAgent?.isConnected) {
                            StatusModule.update('idle', `👤 ${data.name} (${data.confidence}%)`);
                            setTimeout(() => {
                                if (!window.voiceAgent?.isConnected) StatusModule.update('idle', 'READY');
                            }, 4000);
                        }
                    } else {
                        this.currentIdentity = null;
                        const statusEl = document.getElementById('face-id-status');
                        if (statusEl) {
                            statusEl.textContent = data.message || 'Not recognized';
                            statusEl.className = 'face-id-status';
                        }
                    }
                } catch (error) {
                    console.error('Face identification error:', error);
                }
            }
        };

        // ===== MUSIC MODULE =====
        const MusicModule = {
            // Dual audio elements for crossfade
            audio1: document.getElementById('music-player'),
            audio2: document.getElementById('music-player-2'),
            activeAudio: 1,  // Which audio element is currently playing (1 or 2)
            audio: null,     // Will point to active audio element
            button: document.getElementById('music-button'),
            panel: document.getElementById('music-panel'),
            trackName: document.getElementById('track-name'),
            volumeSlider: document.getElementById('volume-slider'),
            panelState: 'closed',
            lastOpenState: 'full',
            _timelineRAF: null,
            isPlaying: false,
            currentTrack: null,
            currentMetadata: null,
            volume: /iPhone|iPad|iPod|Android/i.test(navigator.userAgent) ? 1.0 : 0.85,
            metadata: null,  // Will be loaded from server
            crossfadeInProgress: false,
            crossfadeDuration: 1500,  // 1.5 second crossfade
            trackHistory: [],  // Previously played tracks for back button

            // Text triggers for AI control
            playTriggers: ['spinning up', 'playing now', 'here comes', 'drop the beat', 'hit it', 'music time', 'lets play', "let's play", 'start the music', 'cue the music'],
            stopTriggers: ['stop the music', 'cut the music', 'kill the music', 'silence', 'music off', 'enough music'],
            skipTriggers: ['next track', 'skip this', 'next song', 'switch it up', 'something else', 'different song'],
            volumeUpTriggers: ['turn it up', 'louder', 'crank it', 'pump it up'],
            volumeDownTriggers: ['turn it down', 'quieter', 'lower the volume', 'too loud'],

            // Track name triggers - AI can request specific tracks by name
            // Populated dynamically from server metadata; add custom mappings here
            trackTriggers: {},

            async init() {
                console.log('MusicModule initializing...');

                // Set initial active audio element
                this.audio = this.audio1;

                // Load metadata from server (non-blocking)
                this.loadMetadata();

                // When track ends, just stop - DJ decides when to play next
                this.audio1.addEventListener('ended', () => {
                    if (this.activeAudio === 1) {
                        console.log('Track ended on audio1 - waiting for DJ to pick next song');
                        this.isPlaying = false;
                        this.panel.classList.remove('playing');
                        this._syncPlayButtons(false);
                    }
                });
                this.audio2.addEventListener('ended', () => {
                    if (this.activeAudio === 2) {
                        console.log('Track ended on audio2 - waiting for DJ to pick next song');
                        this.isPlaying = false;
                        this.panel.classList.remove('playing');
                        this._syncPlayButtons(false);
                    }
                });

                // Initialize timeline seek bar
                this._initTimeline();

                console.log('MusicModule ready with crossfade support');
            },

            // Get the inactive audio element for loading next track
            getInactiveAudio() {
                return this.activeAudio === 1 ? this.audio2 : this.audio1;
            },

            // Crossfade from current to next audio element
            async crossfade(newTrackUrl, newMetadata) {
                if (this.crossfadeInProgress) {
                    console.log('Crossfade already in progress, skipping');
                    return;
                }

                this.crossfadeInProgress = true;
                const outgoing = this.activeAudio === 1 ? this.audio1 : this.audio2;
                const incoming = this.activeAudio === 1 ? this.audio2 : this.audio1;

                console.log('Starting crossfade:', outgoing === this.audio1 ? 'audio1->audio2' : 'audio2->audio1');

                // Set up incoming audio
                incoming.src = newTrackUrl;
                incoming.volume = 0;

                try {
                    // Wait for incoming to be ready to play
                    await new Promise((resolve, reject) => {
                        const onCanPlay = () => {
                            incoming.removeEventListener('canplay', onCanPlay);
                            incoming.removeEventListener('error', onError);
                            resolve();
                        };
                        const onError = (e) => {
                            incoming.removeEventListener('canplay', onCanPlay);
                            incoming.removeEventListener('error', onError);
                            reject(e);
                        };
                        incoming.addEventListener('canplay', onCanPlay);
                        incoming.addEventListener('error', onError);
                        incoming.load();
                    });

                    // Start playing incoming (at volume 0)
                    await incoming.play();

                    // Perform the crossfade
                    const steps = 30;
                    const stepDuration = this.crossfadeDuration / steps;
                    const outgoingStartVolume = outgoing.volume;

                    for (let i = 1; i <= steps; i++) {
                        const progress = i / steps;
                        outgoing.volume = outgoingStartVolume * (1 - progress);
                        incoming.volume = this.volume * progress;
                        await new Promise(r => setTimeout(r, stepDuration));
                    }

                    // Finish up
                    outgoing.pause();
                    outgoing.currentTime = 0;
                    outgoing.volume = this.volume;  // Reset for next use
                    incoming.volume = this.volume;

                    // Switch active audio
                    this.activeAudio = this.activeAudio === 1 ? 2 : 1;
                    this.audio = incoming;

                    // Update state
                    this.currentMetadata = newMetadata;
                    this.currentTrack = newMetadata.filename;
                    this.trackName.textContent = newMetadata.title || newMetadata.filename;
                    this.isPlaying = true;
                    this.button.classList.add('active');
                    this.panel.classList.add('playing');
                    this._syncPlayButtons(true);
                    if (this.panelState === 'closed') this.openPanel();

                    console.log('Crossfade complete, now playing:', newMetadata.title);

                } catch (error) {
                    console.error('Crossfade error:', error);
                    // Fallback: just play the new track directly
                    incoming.volume = this.volume;
                    this.audio = incoming;
                    this.activeAudio = this.activeAudio === 1 ? 2 : 1;
                } finally {
                    this.crossfadeInProgress = false;
                }
            },

            async loadMetadata() {
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/music?action=list`);
                    const data = await response.json();
                    this.metadata = data.tracks || [];
                    console.log('Music metadata loaded:', this.metadata.length, 'tracks');
                } catch (error) {
                    console.warn('Failed to load music metadata:', error);
                    this.metadata = [];
                }
            },

            // --- Panel state management ---
            togglePanel() {
                if (this.panelState === 'closed') {
                    this.openPanel();
                    // If no music playing, start some
                    if (!this.isPlaying) this.play();
                } else {
                    // Stop music and close
                    this.stop();
                }
            },

            // Shift transcript + action console panels up/down when music panel opens/closes
            _adjustOverlayPanels() {
                const mpHeight = (this.panelState !== 'closed' && this.panel)
                    ? this.panel.offsetHeight : 0;
                const offset = mpHeight > 0 ? `${mpHeight + 4}px` : '60px';
                const tp = document.getElementById('transcript-panel');
                const ac = document.getElementById('action-console');
                if (tp) { tp.style.bottom = offset; tp.style.transition = 'bottom 0.3s ease'; }
                if (ac) { ac.style.bottom = offset; ac.style.transition = 'bottom 0.3s ease'; }
            },

            openPanel() {
                this.panel.classList.add('open');
                this.button.classList.add('active');
                if (this.lastOpenState === 'mini') {
                    document.getElementById('mp-full').style.display = 'none';
                    document.getElementById('mp-mini').style.display = 'flex';
                    this.panel.classList.add('state-mini');
                    this.panelState = 'mini';
                } else {
                    document.getElementById('mp-full').style.display = 'flex';
                    document.getElementById('mp-mini').style.display = 'none';
                    this.panel.classList.remove('state-mini');
                    this.panelState = 'full';
                }
                this._startTimeline();
                // Wait for CSS transition to settle before measuring height
                requestAnimationFrame(() => this._adjustOverlayPanels());
            },

            closePanel() {
                this.panel.classList.remove('open');
                this.panel.classList.remove('state-mini');
                if (!this.isPlaying) this.button.classList.remove('active');
                this.panelState = 'closed';
                this._stopTimeline();
                this._adjustOverlayPanels();
            },

            collapsePanel() {
                document.getElementById('mp-full').style.display = 'none';
                document.getElementById('mp-mini').style.display = 'flex';
                this.panel.classList.add('state-mini');
                this.panelState = 'mini';
                this.lastOpenState = 'mini';
                this._syncMiniControls();
                requestAnimationFrame(() => this._adjustOverlayPanels());
            },

            expandPanel() {
                document.getElementById('mp-mini').style.display = 'none';
                document.getElementById('mp-full').style.display = 'flex';
                this.panel.classList.remove('state-mini');
                this.panelState = 'full';
                this.lastOpenState = 'full';
                requestAnimationFrame(() => this._adjustOverlayPanels());
            },

            toggle() {
                this.togglePanel();
            },

            _playId: 0,

            async play(trackName) {
                const playId = ++this._playId;
                try {
                    const url = new URL(`${CONFIG.serverUrl}/api/music`);
                    url.searchParams.set('action', 'play');
                    url.searchParams.set('playlist', this.currentPlaylist || 'generated');
                    if (trackName) url.searchParams.set('track', trackName);

                    const response = await fetch(url);
                    if (playId !== this._playId) return;
                    const data = await response.json();

                    if (data.track) {
                        // Save current track to history before switching
                        if (this.currentTrack && !this._skipHistoryPush) {
                            this.trackHistory.push(this.currentTrack);
                            if (this.trackHistory.length > 20) this.trackHistory.shift();
                        }
                        this._skipHistoryPush = false;
                        const filename = data.track.filename || data.track;
                        const trackUrl = data.url || `${CONFIG.serverUrl}/music/${filename}`;
                        this.audio.pause();
                        this.audio.src = trackUrl;
                        this.audio.volume = this.volume;
                        try {
                            await this.audio.play();
                        } catch (e) {
                            if (e.name === 'AbortError') return;
                            throw e;
                        }
                        if (playId !== this._playId) return;
                        this.isPlaying = true;
                        this.currentTrack = filename;
                        this.currentMetadata = data.track;
                        this.button.classList.add('active');
                        this.panel.classList.add('playing');
                        this.trackName.textContent = data.track.title || filename;
                        this._syncPlayButtons(true);
                        console.log('Now playing:', data.track.title, 'from playlist:', data.playlist);

                        // Open panel if closed
                        if (this.panelState === 'closed') this.openPanel();

                        // Start visualizer
                        if (VisualizerModule.enabled) {
                            await VisualizerModule.setupAnalyser();
                            VisualizerModule.startAnimation();
                        }
                    }
                } catch (error) {
                    console.error('Music play error:', error);
                }
            },

            pause() {
                this.audio.pause();
                this.isPlaying = false;
                this.panel.classList.remove('playing');
                this._syncPlayButtons(false);
                // Stop visualizer
                VisualizerModule.stopAnimation();
                this.closePanel();
            },

            stop() {
                this.audio.pause();
                this.audio.currentTime = 0;
                this.isPlaying = false;
                this.currentTrack = null;
                this.currentMetadata = null;
                this.button.classList.remove('active');
                this.panel.classList.remove('playing');
                this._syncPlayButtons(false);
                // Stop visualizer
                VisualizerModule.stopAnimation();
                this.closePanel();
            },

            togglePlay() {
                if (this.audio.paused) {
                    this.audio.play();
                    this.isPlaying = true;
                    this.button.classList.add('active');
                    this.panel.classList.add('playing');
                    this._syncPlayButtons(true);
                    if (this.panelState === 'closed') this.openPanel();
                    // Start visualizer
                    if (VisualizerModule.enabled) {
                        VisualizerModule.setupAnalyser();
                        VisualizerModule.startAnimation();
                    }
                } else {
                    this.audio.pause();
                    this.isPlaying = false;
                    this.panel.classList.remove('playing');
                    this._syncPlayButtons(false);
                    VisualizerModule.stopAnimation();
                }
            },

            _syncPlayButtons(playing) {
                const icon = playing ? '\u23F8' : '\u25B6';
                const btn = document.getElementById('play-pause-btn');
                const btnMini = document.getElementById('play-pause-btn-mini');
                if (btn) btn.textContent = icon;
                if (btnMini) btnMini.textContent = icon;
            },

            _syncMiniControls() {
                const miniVol = document.querySelector('.mp-mini-vol');
                if (miniVol) miniVol.value = this.volume * 100;
            },

            // --- Timeline ---
            _formatTime(s) {
                if (!s || !isFinite(s)) return '0:00';
                const m = Math.floor(s / 60);
                const sec = Math.floor(s % 60);
                return m + ':' + (sec < 10 ? '0' : '') + sec;
            },

            _startTimeline() {
                if (this._timelineRAF) return;
                const update = () => {
                    const cur = this.audio.currentTime || 0;
                    const dur = this.audio.duration || 0;
                    const pct = dur > 0 ? (cur / dur) * 100 : 0;
                    const curEl = document.getElementById('mp-time-cur');
                    const durEl = document.getElementById('mp-time-dur');
                    const timeline = document.getElementById('mp-timeline');
                    const fill = document.getElementById('mp-timeline-fill');
                    if (curEl) curEl.textContent = this._formatTime(cur);
                    if (durEl) durEl.textContent = this._formatTime(dur);
                    if (timeline && !timeline._dragging) timeline.value = pct;
                    if (fill) fill.style.width = pct + '%';
                    this._timelineRAF = requestAnimationFrame(update);
                };
                this._timelineRAF = requestAnimationFrame(update);
            },

            _stopTimeline() {
                if (this._timelineRAF) {
                    cancelAnimationFrame(this._timelineRAF);
                    this._timelineRAF = null;
                }
            },

            _initTimeline() {
                const timeline = document.getElementById('mp-timeline');
                if (!timeline) return;
                timeline.addEventListener('mousedown', () => { timeline._dragging = true; });
                timeline.addEventListener('touchstart', () => { timeline._dragging = true; }, {passive: true});
                timeline.addEventListener('input', (e) => {
                    const pct = e.target.value / 100;
                    if (this.audio.duration) {
                        this.audio.currentTime = pct * this.audio.duration;
                    }
                    const fill = document.getElementById('mp-timeline-fill');
                    if (fill) fill.style.width = e.target.value + '%';
                });
                timeline.addEventListener('mouseup', () => { timeline._dragging = false; });
                timeline.addEventListener('touchend', () => { timeline._dragging = false; });
            },

            next() {
                this.play(); // Server returns random track
            },

            prev() {
                if (this.trackHistory.length > 0) {
                    const prevTrack = this.trackHistory.pop();
                    this._skipHistoryPush = true;
                    this.play(prevTrack);
                }
            },

            setVolume(value) {
                this.volume = value / 100;
                this.audio.volume = this.volume;
                // Sync all volume sliders
                document.querySelectorAll('.mp-vol').forEach(s => { s.value = value; });
            },

            volumeUp() {
                this.volume = Math.min(1, this.volume + 0.15);
                this.audio.volume = this.volume;
                document.querySelectorAll('.mp-vol').forEach(s => { s.value = this.volume * 100; });
            },

            volumeDown() {
                this.volume = Math.max(0, this.volume - 0.15);
                this.audio.volume = this.volume;
                document.querySelectorAll('.mp-vol').forEach(s => { s.value = this.volume * 100; });
            },

            duck(shouldDuck) {
                // Duck to 40% when DJ talks - apply to currently active audio
                const activeAudioEl = this.activeAudio === 1 ? this.audio1 : this.audio2;
                activeAudioEl.volume = shouldDuck ? this.volume * 0.4 : this.volume;
            },

            // Check AI text for music control triggers
            checkTriggers(text) {
                if (!text) return null;
                const lowerText = text.toLowerCase();
                let action = null;

                // Check for specific track requests first
                for (const [trigger, trackFile] of Object.entries(this.trackTriggers)) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: specific track', trigger, '->', trackFile);
                        this.play(trackFile);
                        action = 'play_specific';
                        return action;
                    }
                }

                // Check for play triggers
                for (const trigger of this.playTriggers) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: play');
                        if (!this.isPlaying) this.play();
                        action = 'play';
                        return action;
                    }
                }

                // Check for stop triggers
                for (const trigger of this.stopTriggers) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: stop');
                        this.stop();
                        action = 'stop';
                        return action;
                    }
                }

                // Check for skip triggers
                for (const trigger of this.skipTriggers) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: skip');
                        this.next();
                        action = 'skip';
                        return action;
                    }
                }

                // Check for volume up
                for (const trigger of this.volumeUpTriggers) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: volume up');
                        this.volumeUp();
                        action = 'volume_up';
                        return action;
                    }
                }

                // Check for volume down
                for (const trigger of this.volumeDownTriggers) {
                    if (lowerText.includes(trigger)) {
                        console.log('Music trigger: volume down');
                        this.volumeDown();
                        action = 'volume_down';
                        return action;
                    }
                }

                return action;
            },

            // Get current track info for AI context
            getCurrentTrackInfo() {
                if (!this.currentMetadata) return null;
                return {
                    title: this.currentMetadata.title,
                    artist: this.currentMetadata.artist,
                    description: this.currentMetadata.description,
                    phone: this.currentMetadata.phone_number,
                    djHints: this.currentMetadata.dj_intro_hints
                };
            },

            // Get list of available tracks for AI prompt
            getTrackList() {
                if (!this.metadata) return 'No tracks loaded';
                return Object.entries(this.metadata).map(([file, info]) => {
                    return `- "${info.title}" (say "${Object.entries(this.trackTriggers).find(([k,v]) => v === file)?.[0] || file}")`;
                }).join('\n');
            },

            // Playlist support
            currentPlaylist: 'generated',

            async switchPlaylist(playlist) {
                console.log('Switching playlist to:', playlist);
                this.currentPlaylist = playlist;
                // Reload metadata for new playlist
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/music?action=list&playlist=${playlist}`);
                    const data = await response.json();
                    this.metadata = data.tracks || [];
                    console.log('Playlist switched, loaded', this.metadata.length, 'tracks');
                } catch (error) {
                    console.warn('Failed to load playlist metadata:', error);
                }
                // If playing, switch to a track from the new playlist
                if (this.isPlaying) {
                    this.play();
                }
            }
        };

        // ===== SUNO AI SONG GENERATION MODULE =====
        const SunoModule = {
            activeJobId: null,
            pollInterval: null,
            statusEl: null,

            init() {
                // Create a status banner element for generation feedback
                const el = document.createElement('div');
                el.id = 'suno-status';
                el.style.cssText = [
                    'display:none', 'position:fixed', 'bottom:80px', 'left:50%',
                    'transform:translateX(-50%)', 'background:var(--bg-elevated)', 'color:var(--blue-bright)',
                    'border:1px solid var(--blue)', 'border-radius:8px', 'padding:8px 16px',
                    'font-size:13px', 'z-index:9999', 'white-space:nowrap',
                    'box-shadow:0 4px 12px rgba(0,136,255,0.3)',
                ].join(';');
                document.body.appendChild(el);
                this.statusEl = el;
            },

            _showStatus(msg) {
                if (!this.statusEl) return;
                this.statusEl.textContent = msg;
                this.statusEl.style.display = 'block';
            },

            _hideStatus() {
                if (this.statusEl) this.statusEl.style.display = 'none';
            },

            async generate(prompt) {
                console.log('[Suno] Generating:', prompt);
                this._showStatus('🎵 Suno: submitting song request...');

                // Parse optional fields from prompt: "prompt | style:hip hop | title:My Song"
                let actualPrompt = prompt;
                let style = '';
                let title = '';
                const parts = prompt.split('|').map(s => s.trim());
                if (parts.length > 1) {
                    actualPrompt = parts[0];
                    for (const part of parts.slice(1)) {
                        if (part.toLowerCase().startsWith('style:')) style = part.slice(6).trim();
                        else if (part.toLowerCase().startsWith('title:')) title = part.slice(6).trim();
                    }
                }

                try {
                    const params = new URLSearchParams({ action: 'generate', prompt: actualPrompt });
                    if (style) params.set('style', style);
                    if (title) params.set('title', title);

                    const resp = await fetch(`${CONFIG.serverUrl}/api/suno?${params}`);
                    const data = await resp.json();

                    if (data.action === 'generating' && data.job_id) {
                        this.activeJobId = data.job_id;
                        this._showStatus('🎵 Suno: cooking your track (~45s)...');
                        this._startPolling(data.job_id);
                    } else {
                        this._showStatus(`🎵 Suno: ${data.response || 'Error starting generation'}`);
                        setTimeout(() => this._hideStatus(), 5000);
                    }
                } catch (err) {
                    console.error('[Suno] generate error:', err);
                    this._showStatus('🎵 Suno: connection error');
                    setTimeout(() => this._hideStatus(), 4000);
                }
            },

            _startPolling(jobId) {
                this._stopPolling();
                let attempts = 0;
                this.pollInterval = setInterval(async () => {
                    attempts++;
                    if (attempts > 30) {  // max ~5 minutes
                        this._stopPolling();
                        this._showStatus('🎵 Suno: timed out — check generated playlist manually');
                        setTimeout(() => this._hideStatus(), 6000);
                        return;
                    }
                    try {
                        const resp = await fetch(`${CONFIG.serverUrl}/api/suno?action=status&job_id=${jobId}`);
                        const data = await resp.json();
                        if (data.action === 'complete' || data.status === 'complete') {
                            this._stopPolling();
                            this._onComplete(data);
                        } else if (data.status === 'not_found' || data.status === 'no_jobs') {
                            this._stopPolling();
                            this._hideStatus();
                        } else {
                            const elapsed = data.elapsed_seconds || 0;
                            this._showStatus(`🎵 Suno: generating... (${elapsed}s)`);
                        }
                    } catch (err) {
                        console.warn('[Suno] poll error:', err);
                    }
                }, 10000);  // poll every 10s
            },

            _stopPolling() {
                if (this.pollInterval) {
                    clearInterval(this.pollInterval);
                    this.pollInterval = null;
                }
            },

            _onComplete(data) {
                const title = data.title || 'your track';
                console.log('[Suno] Complete:', title, data.url);
                this._showStatus(`🎵 "${title}" is ready in the Generated playlist`);
                setTimeout(() => this._hideStatus(), 8000);
                // Refresh music player so the agent and UI see the new track immediately
                window.musicPlayer?.loadMetadata();
                // Notify the agent so it can proactively respond
                ActionConsole?.addEntry('system', `🎵 Song ready: "${title}"`);
                this._notifyAgent(title);
            },

            async _speakCompletion(title) {
                try {
                    const resp = await fetch(`${CONFIG.serverUrl}/api/tts/generate`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ text: `Your track "${title}" is ready in the generated playlist.` }),
                    });
                    if (!resp.ok) return;
                    const blob = await resp.blob();
                    const url = URL.createObjectURL(blob);
                    const audio = new Audio(url);
                    audio.onended = () => URL.revokeObjectURL(url);
                    audio.play().catch(() => {});
                } catch (e) {
                    console.warn('[Suno] TTS completion error:', e);
                }
            },

            async _notifyAgent(title) {
                try {
                    const provider = window.voiceAgent?.selectedProvider || 'groq';
                    const voice = window.voiceAgent?.currentVoice || 'autumn';
                    const sessionId = window._activeSessionId || `suno-${Date.now()}`;

                    const resp = await fetch(`${CONFIG.serverUrl}/api/conversation`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            message: `__suno_complete__:${title}`,
                            tts_provider: provider,
                            voice: voice,
                            session_id: sessionId,
                            ui_context: {},
                        }),
                    });
                    if (!resp.ok) throw new Error('notify failed');
                    const data = await resp.json();

                    if (data.response) {
                        TranscriptPanel?.addMessage('assistant', data.response);
                        ActionConsole?.addEntry('system', `🎵 Agent: ${data.response.substring(0, 80)}`);
                    }
                    if (data.audio) {
                        const binary = atob(data.audio);
                        const bytes = new Uint8Array(binary.length);
                        for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                        const blob = new Blob([bytes], { type: 'audio/mpeg' });
                        const url = URL.createObjectURL(blob);
                        const audio = new Audio(url);
                        audio.onended = () => URL.revokeObjectURL(url);
                        audio.play().catch(() => {});
                    }
                } catch (e) {
                    console.warn('[Suno] Agent notify failed, falling back to TTS:', e);
                    this._speakCompletion(title);
                }
            },
        };

        // ===== VISUALIZER MODULE =====
        const VisualizerModule = {
            // Audio analysis
            audioContext: null,
            analyser: null,
            sourceNode1: null,
            sourceNode2: null,
            frequencyData: null,
            timeDomainData: null,
            animationId: null,

            // State — defaults, overridden from server profile in init()
            enabled: true,
            autoplayEnabled: false,
            currentPlaylist: 'generated',

            // Beat detection - EXACT from ai-eyes
            bassHistory: [],
            prevBassLevel: 0,
            lastBeatTime: 0,
            beatCooldown: 50,
            audioSensitivity: 1.5,

            // Frequency band ranges - EXACT from ai-eyes (for 2048 FFT at 44100Hz)
            BANDS: {
                subBass: { start: 0, end: 4 },      // 0-86Hz
                bass: { start: 4, end: 12 },        // 86-258Hz
                lowMid: { start: 12, end: 24 },     // 258-516Hz
                mid: { start: 24, end: 92 },        // 516-1978Hz
                highMid: { start: 92, end: 186 },   // 1978-4000Hz
                treble: { start: 186, end: 1024 }   // 4000Hz+
            },

            // Party effects
            discoDots: [],
            partyParticles: [],

            // Constants
            NUM_BARS: 25,

            async init() {
                console.log('VisualizerModule initializing...');
                // Load from server profile (set by ProviderManager.init), localStorage fallback
                this.enabled = window._serverProfile?.ui?.visualizer_enabled ?? (localStorage.getItem('visualizerEnabled') !== 'false');
                this.autoplayEnabled = window._serverProfile?.ui?.music_autoplay ?? (localStorage.getItem('musicAutoplay') === 'true');
                this.createVisualizerBars();
                this.initPartyEffects();
                this.updateToggleUI();

                // Add ended listener for autoplay
                const audio1 = document.getElementById('music-player');
                const audio2 = document.getElementById('music-player-2');
                audio1.addEventListener('ended', () => this.onTrackEnded());
                audio2.addEventListener('ended', () => this.onTrackEnded());

                console.log('VisualizerModule ready, enabled:', this.enabled);
            },

            onTrackEnded() {
                if (this.autoplayEnabled && window.musicPlayer) {
                    console.log('Autoplaying next track...');
                    window.musicPlayer.play();
                }
            },

            updateToggleUI() {
                const autoplayToggle = document.getElementById('autoplay-toggle');
                const visualizerToggle = document.getElementById('visualizer-toggle');
                const autoplayCheckbox = document.getElementById('autoplay-checkbox');
                const visualizerCheckbox = document.getElementById('visualizer-checkbox');

                if (autoplayCheckbox) autoplayCheckbox.checked = this.autoplayEnabled;
                if (visualizerCheckbox) visualizerCheckbox.checked = this.enabled;
                if (autoplayToggle) autoplayToggle.classList.toggle('enabled', this.autoplayEnabled);
                if (visualizerToggle) visualizerToggle.classList.toggle('enabled', this.enabled);
            },

            async setupAnalyser() {
                if (!this.enabled) return;

                try {
                    if (!this.audioContext) {
                        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    }
                    if (this.audioContext.state === 'suspended') {
                        await this.audioContext.resume();
                    }

                    if (!this.analyser) {
                        this.analyser = this.audioContext.createAnalyser();
                        this.analyser.fftSize = 2048;
                        this.analyser.smoothingTimeConstant = 0.8;
                        this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);
                        this.timeDomainData = new Uint8Array(this.analyser.fftSize);
                    }

                    const audio1 = document.getElementById('music-player');
                    const audio2 = document.getElementById('music-player-2');

                    if (!this.sourceNode1 && audio1) {
                        this.sourceNode1 = this.audioContext.createMediaElementSource(audio1);
                        this.sourceNode1.connect(this.analyser);
                    }
                    if (!this.sourceNode2 && audio2) {
                        this.sourceNode2 = this.audioContext.createMediaElementSource(audio2);
                        this.sourceNode2.connect(this.analyser);
                    }

                    this.analyser.connect(this.audioContext.destination);
                    console.log('Music analyser connected');
                } catch (e) {
                    console.error('Visualizer analyser error:', e.message);
                }
            },

            // Create visualizer bars - EXACT from ai-eyes
            createVisualizerBars() {
                const topViz = document.getElementById('top-viz');
                const bottomViz = document.getElementById('bottom-viz');
                const leftViz = document.getElementById('left-viz');
                const rightViz = document.getElementById('right-viz');

                if (!topViz || !bottomViz || !leftViz || !rightViz) return;

                topViz.innerHTML = '';
                bottomViz.innerHTML = '';
                leftViz.innerHTML = '';
                rightViz.innerHTML = '';

                for (let i = 0; i < this.NUM_BARS; i++) {
                    // Calculate center curve multiplier (1.0 at center, lower at edges)
                    const distFromCenter = Math.abs(i - (this.NUM_BARS - 1) / 2) / ((this.NUM_BARS - 1) / 2);
                    const centerMultiplier = 1 - (distFromCenter * 0.6); // 1.0 center, 0.4 at edges

                    const topBar = document.createElement('div');
                    topBar.className = 'visualizer-bar';
                    topBar.dataset.centerMult = centerMultiplier;
                    topBar.style.height = '10px';
                    topViz.appendChild(topBar);

                    const bottomBar = document.createElement('div');
                    bottomBar.className = 'visualizer-bar';
                    bottomBar.dataset.centerMult = centerMultiplier;
                    bottomBar.style.height = '10px';
                    bottomViz.appendChild(bottomBar);

                    const leftBar = document.createElement('div');
                    leftBar.className = 'side-bar';
                    leftBar.dataset.centerMult = centerMultiplier;
                    leftBar.style.width = '20px';
                    leftViz.appendChild(leftBar);

                    const rightBar = document.createElement('div');
                    rightBar.className = 'side-bar';
                    rightBar.dataset.centerMult = centerMultiplier;
                    rightBar.style.width = '20px';
                    rightViz.appendChild(rightBar);
                }
            },

            // Init party effects - EXACT from ai-eyes
            initPartyEffects() {
                // Create particles with left/top positioning
                const particleContainer = document.getElementById('party-particle-container');
                if (particleContainer) {
                    particleContainer.innerHTML = '';
                    this.partyParticles = [];
                    for (let i = 0; i < 30; i++) {
                        const particle = document.createElement('div');
                        particle.className = 'party-particle';
                        particle.style.left = Math.random() * 100 + '%';
                        particle.style.top = Math.random() * 100 + '%';
                        particleContainer.appendChild(particle);
                        this.partyParticles.push({
                            el: particle,
                            angle: (i / 30) * Math.PI * 2,
                            baseRadius: 100 + i * 10
                        });
                    }
                }

                // Create disco dots with baseX/baseY positioning
                const discoContainer = document.getElementById('disco-container');
                if (discoContainer) {
                    discoContainer.innerHTML = '';
                    this.discoDots = [];
                    for (let i = 0; i < 40; i++) {
                        const dot = document.createElement('div');
                        dot.className = 'disco-dot';
                        const baseX = Math.random() * 100;
                        const baseY = Math.random() * 100;
                        dot.style.left = baseX + '%';
                        dot.style.top = baseY + '%';
                        discoContainer.appendChild(dot);
                        this.discoDots.push({
                            el: dot,
                            baseX: baseX,
                            baseY: baseY,
                            angle: Math.random() * Math.PI * 2,
                            speed: 0.5 + Math.random() * 1.5,
                            orbitRadius: 5 + Math.random() * 15,
                            hueOffset: Math.random() * 360
                        });
                    }
                }
            },

            // Get frequency band level - EXACT from ai-eyes
            getBandLevel(band) {
                if (!this.frequencyData) return 0;
                let sum = 0;
                const count = band.end - band.start;
                for (let i = band.start; i < band.end; i++) {
                    sum += this.frequencyData[i];
                }
                return (sum / count / 255) * this.audioSensitivity;
            },

            // Beat detection - EXACT from ai-eyes
            detectBeat(bassLevel) {
                const now = Date.now();
                this.bassHistory.push(bassLevel);
                if (this.bassHistory.length > 8) this.bassHistory.shift();

                const bassAvg = this.bassHistory.reduce((a, b) => a + b, 0) / this.bassHistory.length;
                const peakRatio = bassAvg > 0.05 ? (bassLevel / bassAvg) : 1;
                const isAboveAvg = peakRatio > 1.15;
                const isStrongEnough = bassLevel > 0.12;
                const cooldownPassed = (now - this.lastBeatTime) > this.beatCooldown;
                const spike = bassLevel - this.prevBassLevel;
                const isSharpSpike = spike > 0.05 && bassLevel > 0.1;

                this.prevBassLevel = bassLevel;

                const isBeat = cooldownPassed && ((isAboveAvg && isStrongEnough) || isSharpSpike);
                if (isBeat) this.lastBeatTime = now;
                return isBeat;
            },

            startAnimation() {
                if (this.animationId) return;
                this.animate();
            },

            stopAnimation() {
                if (this.animationId) {
                    cancelAnimationFrame(this.animationId);
                    this.animationId = null;
                }
                const container = document.getElementById('party-effects-container');
                if (container) container.classList.remove('active');
                const oscContainer = document.getElementById('oscilloscope-container');
                if (oscContainer) oscContainer.classList.remove('active');
                document.querySelectorAll('.visualizer-container, .side-visualizer').forEach(el => {
                    el.classList.remove('active');
                });
            },

            animate() {
                if (!this.enabled) {
                    this.stopAnimation();
                    return;
                }

                const musicPlayer = window.musicPlayer;
                if (!musicPlayer || !musicPlayer.isPlaying) {
                    this.stopAnimation();
                    return;
                }

                // Show effects container
                const container = document.getElementById('party-effects-container');
                if (container) container.classList.add('active');
                document.querySelectorAll('.visualizer-container, .side-visualizer').forEach(el => {
                    el.classList.add('active');
                });

                // Setup analyser if needed
                if (!this.sourceNode1 && this.enabled) {
                    this.setupAnalyser();
                }

                // Get frequency data
                if (this.analyser && this.frequencyData) {
                    this.analyser.getByteFrequencyData(this.frequencyData);
                    if (this.timeDomainData) {
                        this.analyser.getByteTimeDomainData(this.timeDomainData);
                    }
                }

                // Extract frequency bands - EXACT from ai-eyes
                const bass = this.getBandLevel(this.BANDS.bass);
                const subBass = this.getBandLevel(this.BANDS.subBass);
                const lowMid = this.getBandLevel(this.BANDS.lowMid);
                const mid = this.getBandLevel(this.BANDS.mid);
                const highMid = this.getBandLevel(this.BANDS.highMid);
                const treble = this.getBandLevel(this.BANDS.treble);
                const fullBass = (bass + subBass) / 2;
                const energy = (bass * 2 + lowMid + mid + highMid + treble) / 6;

                const time = Date.now() / 1000;
                const isBeat = this.detectBeat(fullBass);

                // Use the real values
                const useBass = fullBass;
                const useMid = mid;
                const useEnergy = energy;
                const useHighMid = highMid;

                // Update center glow - EXACT from ai-eyes
                this.updateCenterGlow(useBass, useEnergy, useMid, isBeat);

                // Trigger effects on beat
                if (isBeat) {
                    this.triggerBeatFlash(useBass);
                    this.triggerShake(useBass);
                    this.triggerSoundRipple(useBass);
                }

                // Update oscilloscope
                this.updateOscilloscope();

                // Update particles and disco
                this.updateParticles(useBass, useEnergy, time);
                this.updateDiscoDots(useEnergy, useHighMid, useBass, useMid, time, isBeat);

                // Update visualizer bars
                this.updateVisualizerBars();

                this.animationId = requestAnimationFrame(() => this.animate());
            },

            // Update center glow - EXACT from ai-eyes
            updateCenterGlow(useBass, useEnergy, useMid, isBeat) {
                const glow = document.getElementById('center-glow');
                if (!glow) return;

                // Size pulses with bass (600px base, up to 1200px on heavy bass)
                const size = 600 + useBass * 800;

                // Color shifts based on frequencies
                const hue = 180 + useEnergy * 60 + useMid * 40;
                const saturation = 80 + useBass * 20;
                const lightness = 50;

                // Opacity based on energy
                const opacity = 0.3 + useEnergy * 0.7;

                glow.style.width = size + 'px';
                glow.style.height = size + 'px';
                glow.style.background = `radial-gradient(circle,
                    hsla(${hue}, ${saturation}%, ${lightness}%, ${opacity * 0.4}) 0%,
                    hsla(${hue - 30}, ${saturation}%, ${lightness - 20}%, ${opacity * 0.15}) 40%,
                    transparent 70%)`;
                glow.style.opacity = opacity;

                // Extra punch on beat
                if (isBeat) {
                    glow.style.filter = 'blur(40px)';
                    setTimeout(() => glow.style.filter = 'blur(60px)', 100);
                }
            },

            triggerBeatFlash(useBass) {
                const flash = document.getElementById('beat-flash');
                if (!flash) return;
                flash.style.opacity = Math.min(useBass * 0.6, 0.4);
                setTimeout(() => flash.style.opacity = 0, 80);
            },

            triggerShake(useBass) {
                const faceBox = document.getElementById('face-box');
                if (!faceBox) return;
                faceBox.classList.add('shake');
                faceBox.style.setProperty('--shake-amount', (useBass * 8) + 'px');
                setTimeout(() => faceBox.classList.remove('shake'), 100);
            },

            triggerSoundRipple(useBass) {
                if (useBass < 0.15) return;
                const container = document.getElementById('ripple-container');
                if (!container) return;
                const ripple = document.createElement('div');
                ripple.className = 'sound-ripple';
                ripple.style.left = '50%';
                ripple.style.top = '50%';
                ripple.style.transform = 'translate(-50%, -50%)';
                container.appendChild(ripple);
                setTimeout(() => ripple.remove(), 1000);
            },

            // Oscilloscope - FULL SCREEN background effect - EXACT from ai-eyes
            updateOscilloscope() {
                const container = document.getElementById('oscilloscope-container');
                if (!container) return;
                if (!container.classList.contains('active')) {
                    container.classList.add('active');
                }

                const canvas = document.getElementById('oscilloscope-canvas');
                if (!canvas) return;
                const ctx = canvas.getContext('2d');

                // Set canvas size
                canvas.width = canvas.offsetWidth * 2;
                canvas.height = canvas.offsetHeight * 2;

                // Clear
                ctx.clearRect(0, 0, canvas.width, canvas.height);

                // Need time domain data for waveform
                if (!this.analyser || !this.timeDomainData) return;
                this.analyser.getByteTimeDomainData(this.timeDomainData);

                // Sample fewer points for smoother wave (every 16th point)
                const sampleStep = 16;
                const numSamples = Math.floor(this.timeDomainData.length / sampleStep);
                const sliceWidth = canvas.width / numSamples;

                // Draw glow layer first (thicker, more transparent)
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 255, 255, 0.25)'; // --cyan with alpha
                ctx.lineWidth = 20;
                ctx.lineCap = 'round';
                ctx.lineJoin = 'round';

                let x = 0;
                for (let i = 0; i < numSamples; i++) {
                    const dataIndex = i * sampleStep;
                    const v = this.timeDomainData[dataIndex] / 128.0;
                    const y = canvas.height / 2 + (v - 1) * canvas.height * 0.35;

                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                    x += sliceWidth;
                }
                ctx.stroke();

                // Draw main bright line on top
                ctx.beginPath();
                ctx.strokeStyle = '#00ffff'; // matches --cyan token
                ctx.lineWidth = 6;
                x = 0;

                for (let i = 0; i < numSamples; i++) {
                    const dataIndex = i * sampleStep;
                    const v = this.timeDomainData[dataIndex] / 128.0;
                    const y = canvas.height / 2 + (v - 1) * canvas.height * 0.35;

                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                    x += sliceWidth;
                }
                ctx.stroke();
            },

            // Update particles - EXACT from ai-eyes
            updateParticles(useBass, useEnergy, time) {
                this.partyParticles.forEach((p, i) => {
                    const radius = p.baseRadius + useBass * 200;
                    const orbitSpeed = 0.3 + (i % 5) * 0.1;
                    const x = 50 + Math.cos(time * orbitSpeed + p.angle) * (radius / 10);
                    const y = 50 + Math.sin(time * orbitSpeed * 0.7 + p.angle) * (radius / 15);
                    const size = 4 + useEnergy * 8;

                    p.el.style.left = x + '%';
                    p.el.style.top = y + '%';
                    p.el.style.width = size + 'px';
                    p.el.style.height = size + 'px';
                    p.el.style.opacity = 0.3 + useEnergy * 0.7;
                });
            },

            // Update disco dots - EXACT from ai-eyes
            updateDiscoDots(useEnergy, useHighMid, useBass, useMid, time, isBeat) {
                const centerX = 50, centerY = 50;
                this.discoDots.forEach((dot, i) => {
                    const dynamicRadius = dot.orbitRadius * (0.5 + useEnergy * 2);
                    const orbitX = Math.cos(time * dot.speed + dot.angle) * dynamicRadius;
                    const orbitY = Math.sin(time * dot.speed * 0.8 + dot.angle) * dynamicRadius;

                    const pullStrength = 1 - useEnergy;
                    const targetX = dot.baseX + orbitX;
                    const targetY = dot.baseY + orbitY;
                    const x = targetX + (centerX - targetX) * pullStrength * 0.7;
                    const y = targetY + (centerY - targetY) * pullStrength * 0.7;

                    let size = 0.4 + useHighMid * 0.6 + useBass * 0.4;
                    if (isBeat) size += 0.5;

                    const hue = (dot.hueOffset + time * 30 + useMid * 60) % 360;

                    dot.el.style.left = x + '%';
                    dot.el.style.top = y + '%';
                    dot.el.style.transform = `scale(${size})`;
                    dot.el.style.opacity = 0.3 + useEnergy * 0.7;
                    dot.el.style.background = `hsl(${hue}, 100%, 70%)`;
                    dot.el.style.boxShadow = `0 0 ${10 + useBass * 20}px hsl(${hue}, 100%, 50%)`;
                });
            },

            // Update visualizer bars - EXACT from ai-eyes with audioSensitivity
            updateVisualizerBars() {
                if (!this.frequencyData) return;

                // Top and bottom bars - height based on frequency data
                document.querySelectorAll('.visualizer-bar').forEach((bar, i) => {
                    const mult = parseFloat(bar.dataset.centerMult) || 1;
                    const bandIndex = Math.floor(i / this.NUM_BARS * 256);
                    const level = (this.frequencyData[bandIndex] / 255 * this.audioSensitivity);
                    bar.style.height = ((8 + level * 50) * mult) + 'px';
                });

                // Side bars - width based on frequency data
                document.querySelectorAll('.side-bar').forEach((bar, i) => {
                    const mult = parseFloat(bar.dataset.centerMult) || 1;
                    const bandIndex = Math.floor(i / this.NUM_BARS * 256);
                    const level = (this.frequencyData[bandIndex] / 255 * this.audioSensitivity);
                    bar.style.width = ((15 + level * 70) * mult) + 'px';
                });
            },

            setEnabled(enabled) {
                this.enabled = enabled;
                localStorage.setItem('visualizerEnabled', enabled);
                // Persist to server profile
                const pid = window.providerManager?._activeProfileId || 'default';
                fetch('/api/profiles/' + pid, {
                    method: 'PUT', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ ui: { visualizer_enabled: enabled } })
                }).catch(() => {});
                this.updateToggleUI();
                if (!enabled) this.stopAnimation();
            },

            setAutoplay(enabled) {
                this.autoplayEnabled = enabled;
                localStorage.setItem('musicAutoplay', enabled);
                // Persist to server profile
                const pid = window.providerManager?._activeProfileId || 'default';
                fetch('/api/profiles/' + pid, {
                    method: 'PUT', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ ui: { music_autoplay: enabled } })
                }).catch(() => {});
                this.updateToggleUI();
            }
        };

        // Global toggle functions for HTML onclick handlers
        window.toggleAutoplay = (enabled) => VisualizerModule.setAutoplay(enabled);
        window.toggleVisualizer = (enabled) => VisualizerModule.setEnabled(enabled);
        window.switchPlaylist = (playlist) => {
            VisualizerModule.currentPlaylist = playlist;
            if (window.musicPlayer && window.musicPlayer.switchPlaylist) {
                window.musicPlayer.switchPlaylist(playlist);
            }
        };

        // ===== UI MODULE =====
        const UIModule = {
            errorMessage: document.getElementById('error-message'),
            faceNotification: document.getElementById('face-notification'),
            callButton: document.getElementById('call-button'),
            callIcon: document.getElementById('call-icon'),

            showError(message) {
                this.errorMessage.textContent = message;
                this.errorMessage.classList.add('visible');
                setTimeout(() => {
                    this.errorMessage.classList.remove('visible');
                }, 5000);
            },

            showFaceNotification(text) {
                this.faceNotification.textContent = text;
                this.faceNotification.classList.add('visible');
                setTimeout(() => {
                    this.faceNotification.classList.remove('visible');
                }, 3000);
            },

            setCallButtonState(state) {
                this.callButton.classList.remove('active', 'connecting');
                if (state === 'connected') {
                    this.callButton.classList.add('active');
                    this.callIcon.textContent = '📵';
                } else if (state === 'connecting') {
                    this.callButton.classList.add('connecting');
                    this.callIcon.textContent = '⏳';
                } else {
                    this.callIcon.textContent = '📞';
                }
            }
        };

        // ===== AGENT ACTIVITY CHIP =====
        // Cycling activity ticker — queues tool events and cycles through them
        // so the user can read what the agent is actually doing.
        window.AgentActivityChip = {
            _el: null,
            _iconEl: null,
            _textEl: null,
            _hideTimer: null,
            _queue: [],          // [[tag, text], ...] pending items to cycle through
            _cycleTimer: null,
            _cycling: false,
            _endPending: false,  // hide after queue drains
            _CYCLE_MS: 1800,     // ms each item is shown before cycling to next

            init() {
                this._el = document.getElementById('agent-activity-chip');
                this._iconEl = document.getElementById('chip-icon');
                this._textEl = document.getElementById('chip-text');
            },

            // Immediate override — used for speaking state, clears queue
            show(tag, text) {
                if (!this._el) return;
                clearTimeout(this._hideTimer); this._hideTimer = null;
                clearTimeout(this._cycleTimer); this._cycleTimer = null;
                this._queue = [];
                this._cycling = false;
                this._endPending = false;
                this._setContent(tag, text);
            },

            hide(delay = 0) {
                if (!this._el) return;
                if (this._hideTimer) clearTimeout(this._hideTimer);
                this._hideTimer = setTimeout(() => {
                    this._el?.classList.remove('visible');
                    setTimeout(() => {
                        if (this._el && !this._el.classList.contains('visible')) {
                            this._el.style.display = 'none';
                        }
                    }, 250);
                    this._hideTimer = null;
                }, delay);
            },

            _setContent(tag, text) {
                if (!this._el) return;
                this._el.style.display = 'flex';
                if (this._el.classList.contains('visible')) {
                    this._el.classList.remove('updating');
                    void this._el.offsetWidth;
                    this._el.classList.add('updating');
                    setTimeout(() => this._el?.classList.remove('updating'), 220);
                }
                this._iconEl.textContent = tag;
                this._textEl.textContent = text;
                requestAnimationFrame(() => this._el?.classList.add('visible'));
            },

            // Add item to cycle queue, start cycling if idle
            _push(tag, text) {
                if (!this._el) return;
                clearTimeout(this._hideTimer); this._hideTimer = null;
                this._endPending = false;
                this._queue.push([tag, text]);
                if (!this._cycling) {
                    this._cycling = true;
                    this._next();
                }
            },

            _next() {
                clearTimeout(this._cycleTimer);
                if (this._queue.length === 0) {
                    this._cycling = false;
                    if (this._endPending) {
                        this._endPending = false;
                        this.hide(1200);
                    }
                    return;
                }
                const [tag, text] = this._queue.shift();
                this._setContent(tag, text);
                this._cycleTimer = setTimeout(() => this._next(), this._CYCLE_MS);
            },

            // Signal agent work done — let queue drain then hide
            _signalEnd() {
                if (this._cycling) {
                    this._endPending = true; // hide after queue drains
                } else {
                    this.hide(1200);
                }
            },

            // Map OpenClaw stream action → cycling queue entry
            handleAction(action) {
                const { type, phase, name } = action || {};
                const inp = action?.args || action?.input || {};
                const sp = p => { if (!p) return '?'; const s = String(p).split('/'); return s[s.length - 1] || String(p); };
                const su = u => { try { return new URL(u).hostname; } catch { return String(u).substring(0, 28); } };
                const c  = s => s ? String(s).substring(0, 38) : '';

                if (type === 'lifecycle') {
                    if (phase === 'start') this._push('···', 'thinking');
                    else if (phase === 'end' || phase === 'error') this._signalEnd();
                    return;
                }

                if (type === 'subagent') {
                    if (phase === 'spawning') this._push('AGT', 'spawning sub-agent');
                    else if (phase === 'start') this._push('AGT', 'sub-agent active');
                    // subagent end — don't hide; main lifecycle still controls
                    return;
                }

                if (type === 'tool' && phase === 'start') {
                    const n = (name || '').toLowerCase().replace(/[_-]/g, '');
                    const toolMap = {
                        read:            ['RD',   () => sp(inp.file_path || inp.path)],
                        write:           ['WR',   () => sp(inp.file_path || inp.path)],
                        edit:            ['ED',   () => sp(inp.file_path || inp.path)],
                        glob:            ['GLOB', () => c(inp.pattern)],
                        grep:            ['SRC',  () => c(inp.pattern)],
                        bash:            ['RUN',  () => c(inp.command)],
                        exec:            ['RUN',  () => c(inp.command)],
                        webfetch:        ['GET',  () => su(inp.url)],
                        websearch:       ['WEB',  () => c(inp.query)],
                        agent:           ['AGT',  () => 'spawning agent'],
                        task:            ['TSK',  () => c(inp.description)],
                        todowrite:       ['TODO', () => 'updating list'],
                        notebookedit:    ['NB',   () => sp(inp.notebook_path)],
                        askuserquestion: ['ASK',  () => 'waiting for input'],
                    };
                    const lookup = Object.keys(toolMap).find(k => n.includes(k));
                    const [tag, getText] = toolMap[lookup] || ['TOOL', () => name || '?'];
                    const detail = getText();
                    this._push(tag, detail || name || '?');
                }
            },

            // Map response tags → immediate chip update (not queued)
            handleTag(tag, detail) {
                const d = detail ? String(detail) : '';
                const su = u => { try { return new URL(u).hostname; } catch { return String(u).substring(0, 28); } };
                const tagMap = {
                    suno:          ['SND',  d ? `generating: ${d.substring(0, 35)}` : 'generating music'],
                    canvas:        ['CVS',  d ? `opening ${d}` : 'opening canvas'],
                    canvas_url:    ['CVS',  d ? `loading ${su(d)}` : 'loading canvas'],
                    canvas_menu:   ['CVS',  'opening canvas menu'],
                    music_play:    ['▶',    d ? `"${d}"` : 'playing music'],
                    music_stop:    ['■',    'music stopped'],
                    music_next:    ['▶▶',   'next track'],
                    spotify:       ['SPT',  d ? `"${d}"` : 'playing spotify'],
                    sound:         ['SFX',  d || 'playing sound'],
                    register_face: ['FACE', d ? `registering ${d}` : 'registering face'],
                    sleep:         ['ZZZ',  'going to sleep'],
                    session_reset: ['RST',  'resetting session'],
                    html_canvas:   ['CVS',  'building canvas page'],
                };
                const entry = tagMap[tag];
                if (entry) this.show(entry[0], entry[1]);
            },

            // Speaking state — immediate override, clears cycling
            setSpeaking(speaking) {
                if (speaking) this.show('▶', 'speaking');
                else this.hide(0);
            },
        };
        AgentActivityChip.init();

        // ===== ISSUE REPORTER =====
        window.IssueReporter = {
            _modal: null,
            _typeEl: null,
            _descEl: null,
            _statusEl: null,
            _submitBtn: null,
            _contextPreview: null,

            open() {
                if (!this._modal) {
                    this._modal      = document.getElementById('issue-report-modal');
                    this._typeEl     = document.getElementById('irm-type');
                    this._descEl     = document.getElementById('irm-description');
                    this._statusEl   = document.getElementById('irm-status');
                    this._submitBtn  = document.getElementById('irm-submit-btn');
                    this._contextPreview = document.getElementById('irm-context-preview');
                }
                if (!this._modal) return;

                // Reset state
                if (this._descEl) this._descEl.value = '';
                if (this._statusEl) { this._statusEl.textContent = ''; this._statusEl.className = 'irm-status'; }
                if (this._submitBtn) this._submitBtn.disabled = false;

                // Auto-populate context preview
                const ctx = this._gatherContext();
                if (this._contextPreview) {
                    const parts = [];
                    if (ctx.canvas_page) parts.push(`canvas:${ctx.canvas_page}`);
                    if (ctx.session_id)  parts.push(`session:${ctx.session_id.slice(-8)}`);
                    parts.push(ctx.platform || navigator.platform);
                    this._contextPreview.textContent = parts.join(' · ');
                }

                this._modal.style.display = 'flex';
                setTimeout(() => this._descEl?.focus(), 50);
            },

            close() {
                if (this._modal) this._modal.style.display = 'none';
            },

            _gatherContext() {
                return {
                    canvas_page: window._currentCanvasPage || null,
                    session_id: window._activeSessionId || null,
                    platform: navigator.platform,
                    ua: navigator.userAgent.slice(0, 120),
                    voice_mode: localStorage.getItem('voice_mode') || 'supertonic',
                    tts_provider: localStorage.getItem('voice_provider') || null,
                    url: location.pathname,
                };
            },

            async submit() {
                const description = this._descEl?.value.trim();
                if (!description) {
                    if (this._statusEl) { this._statusEl.textContent = 'Please describe the issue.'; this._statusEl.className = 'irm-status error'; }
                    return;
                }

                if (this._submitBtn) this._submitBtn.disabled = true;
                if (this._statusEl) { this._statusEl.textContent = 'Submitting…'; this._statusEl.className = 'irm-status'; }

                try {
                    const res = await fetch('/api/report-issue', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            type: this._typeEl?.value || 'bug',
                            description,
                            context: this._gatherContext(),
                        }),
                    });
                    const data = await res.json();
                    if (data.ok) {
                        if (this._statusEl) { this._statusEl.textContent = '✓ Report submitted. Thank you!'; this._statusEl.className = 'irm-status success'; }
                        setTimeout(() => this.close(), 1800);
                    } else {
                        throw new Error(data.error || 'Server error');
                    }
                } catch (err) {
                    if (this._statusEl) { this._statusEl.textContent = `Error: ${err.message}`; this._statusEl.className = 'irm-status error'; }
                    if (this._submitBtn) this._submitBtn.disabled = false;
                }
            },
        };

        // ===== AUTH MODULE =====
        window.AuthModule = {
            user: null,
            dropdownOpen: false,

            async init() {
                // If no Clerk key configured, show local-mode menu instead of Clerk auth
                if (!window.AGENT_CONFIG?.clerkPublishableKey) {
                    console.log('Auth disabled — no Clerk key configured (local mode)');
                    this._renderLocalMenu();
                    return;
                }

                // Wait for Clerk SDK to load (retry up to 10s)
                for (let i = 0; i < 20; i++) {
                    if (typeof Clerk !== 'undefined') break;
                    await new Promise(resolve => setTimeout(resolve, 500));
                }
                if (typeof Clerk === 'undefined') {
                    console.warn('Clerk SDK failed to load — falling back to local mode');
                    this._renderLocalMenu();
                    return;
                }

                try {
                    await Clerk.load();

                    const _grantAccess = () => {
                        this._hideAuthGate();
                        this.renderUserMenu();
                        document.getElementById('sign-in-button').style.display = 'none';
                        // Sync Clerk token to __session cookie so iframe navigations
                        // and direct page URLs are authenticated (browser only sends
                        // cookies with navigation requests, not Authorization headers).
                        this._syncSessionCookie();
                        // If redirected here from a canvas page (or other protected route), go back there
                        const redirectTo = new URLSearchParams(window.location.search).get('redirect');
                        if (redirectTo && redirectTo.startsWith('/')) {
                            window.location.href = redirectTo;
                        }
                    };

                    if (Clerk.user) {
                        this.user = Clerk.user;
                        TranscriptPanel.userName = Clerk.user.firstName || Clerk.user.username || 'User';
                        this._hideAuthGate();  // hide Clerk spinner immediately
                        const allowed = await this._checkAllowlist();
                        if (!allowed) { this._showWaitlistGate(Clerk.user); return; }
                        _grantAccess();
                    } else {
                        // Show blocking gate — app does NOT continue until signed in
                        this._showAuthGate();
                        await new Promise(resolve => {
                            const unsubscribe = Clerk.addListener(async ({ user }) => {
                                if (user) {
                                    this.user = user;
                                    TranscriptPanel.userName = user.firstName || user.username || 'User';
                                    this._hideAuthGate();  // hide Clerk spinner immediately
                                    const allowed = await this._checkAllowlist();
                                    if (!allowed) {
                                        this._showWaitlistGate(user);
                                        unsubscribe?.();
                                        resolve();
                                        return;
                                    }
                                    _grantAccess();
                                    unsubscribe?.();
                                    resolve();
                                }
                            });
                        });
                    }

                    // Close dropdown when clicking outside
                    document.addEventListener('click', (e) => {
                        const container = document.querySelector('.user-menu-container');
                        if (container && !container.contains(e.target) && this.dropdownOpen) {
                            this.closeDropdown();
                        }
                    });
                } catch (error) {
                    console.error('Auth init error:', error);
                    // If Clerk fails (wrong domain, bad key, network), fall back to local mode
                    // instead of blocking the app forever
                    console.warn('Falling back to local mode due to auth error');
                    this._renderLocalMenu();
                }
            },

            _showAuthGate() {
                let gate = document.getElementById('auth-gate');
                if (!gate) {
                    gate = document.createElement('div');
                    gate.id = 'auth-gate';
                    gate.style.cssText = [
                        'position:fixed;inset:0;z-index:99999',
                        'display:flex;align-items:center;justify-content:center',
                        'background:var(--bg-deep);flex-direction:column;gap:24px',
                    ].join(';');
                    gate.innerHTML = [
                        '<div style="text-align:center;margin-bottom:8px">',
                        '  <div style="font-size:28px;font-weight:700;color:var(--blue-bright);letter-spacing:-0.5px">OpenVoiceUI</div>',
                        '  <div style="color:var(--text-muted);font-size:14px;margin-top:6px">Sign in to continue</div>',
                        '</div>',
                        '<div id="auth-gate-signin"></div>',
                    ].join('');
                    document.body.appendChild(gate);
                }
                gate.style.display = 'flex';
                Clerk.mountSignIn(document.getElementById('auth-gate-signin'));
            },

            _hideAuthGate() {
                const gate = document.getElementById('auth-gate');
                if (gate) gate.style.display = 'none';
            },

            async _checkAllowlist() {
                try {
                    const token = await Clerk.session?.getToken();
                    if (!token) return false;
                    const resp = await fetch(`${CONFIG.serverUrl}/api/auth/check`, {
                        headers: { 'Authorization': `Bearer ${token}` }
                    });
                    return resp.ok;
                } catch (e) {
                    console.warn('Allowlist check failed:', e);
                    return false;
                }
            },

            _showAuthErrorGate(message) {
                let gate = document.getElementById('auth-error-gate');
                if (!gate) {
                    gate = document.createElement('div');
                    gate.id = 'auth-error-gate';
                    gate.style.cssText = [
                        'position:fixed;inset:0;z-index:99999',
                        'display:flex;align-items:center;justify-content:center',
                        'background:var(--bg-deep);flex-direction:column;gap:24px;padding:32px',
                    ].join(';');
                    document.body.appendChild(gate);
                }
                gate.style.display = 'flex';
                gate.innerHTML = `
                    <div style="text-align:center;max-width:440px">
                        <div style="font-size:28px;font-weight:700;color:var(--blue-bright);letter-spacing:-0.5px;margin-bottom:20px">OpenVoiceUI</div>
                        <div style="font-size:15px;font-weight:600;color:var(--red);margin-bottom:12px">Authentication Error</div>
                        <div style="color:var(--text-muted);font-size:14px;line-height:1.7;margin-bottom:24px;white-space:pre-line">${message}</div>
                        <button onclick="location.reload()" style="
                            background:var(--green);color:var(--neutral-900);border:1px solid var(--green);
                            padding:10px 24px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:600;
                        ">Refresh Page</button>
                    </div>
                `;
            },

            _showWaitlistGate(user) {
                const email = user?.primaryEmailAddress?.emailAddress || '';

                let gate = document.getElementById('waitlist-gate');
                if (!gate) {
                    gate = document.createElement('div');
                    gate.id = 'waitlist-gate';
                    gate.style.cssText = [
                        'position:fixed;inset:0;z-index:99999',
                        'display:flex;align-items:center;justify-content:center',
                        'background:var(--bg-deep);flex-direction:column;gap:20px;padding:32px',
                    ].join(';');
                    document.body.appendChild(gate);
                }
                gate.style.display = 'flex';
                gate.innerHTML = `
                    <div style="text-align:center;max-width:440px">
                        <div style="font-size:26px;font-weight:700;color:var(--blue-bright);letter-spacing:-0.5px;margin-bottom:20px">OpenVoiceUI</div>
                        <div style="font-size:15px;font-weight:600;color:var(--text-primary);margin-bottom:12px">We're still in development</div>
                        <div style="color:var(--text-muted);font-size:14px;line-height:1.7;margin-bottom:24px">
                            ${email ? `<strong style="color:var(--text-secondary)">${email}</strong> has been added to the waitlist.<br>` : ''}
                            We'll reach out with updates as we get closer to launch.
                        </div>
                        <button onclick="Clerk.signOut().then(()=>location.reload())" style="
                            background:var(--bg-elevated);color:var(--text-muted);border:1px solid var(--neutral-700);
                            padding:8px 20px;border-radius:6px;cursor:pointer;font-size:13px;
                        ">Sign out</button>
                    </div>
                `;
            },

            async getToken() {
                return Clerk.session?.getToken() ?? null;
            },

            async _pushAuthTokenToCanvas() {
                // Push current Clerk JWT to the active canvas iframe via postMessage.
                // The injected canvas-auth-bridge script stores it as _canvasAuthToken.
                try {
                    const token = await Clerk.session?.getToken();
                    const iframe = document.getElementById('canvas-iframe');
                    if (token && iframe?.contentWindow) {
                        iframe.contentWindow.postMessage({type: 'auth-token', token}, '*');
                    }
                } catch (e) { /* session may be gone */ }
            },

            async _syncSessionCookie() {
                // Clerk tokens expire every ~60s. Sync to __session cookie so
                // iframe navigations and direct /pages/ URLs carry auth.
                const _sync = async () => {
                    try {
                        const token = await Clerk.session?.getToken();
                        if (token) {
                            document.cookie = `__session=${token}; path=/; SameSite=Lax; Secure`;
                            // Also push fresh token to canvas iframe so long-running
                            // uploads/API calls never hit an expired cookie
                            const iframe = document.getElementById('canvas-iframe');
                            if (iframe?.contentWindow) {
                                iframe.contentWindow.postMessage({type: 'auth-token', token}, '*');
                            }
                        }
                    } catch (e) { /* session may be gone */ }
                };
                await _sync();
                // Refresh cookie every 50s (Clerk tokens last ~60s)
                this._sessionCookieInterval = setInterval(_sync, 50000);
            },

            renderUserMenu() {
                const container = document.getElementById('user-button');
                if (!this.user) return;

                const name = this.user.firstName || this.user.username || 'User';
                const email = this.user.primaryEmailAddress?.emailAddress || '';
                const avatarUrl = this.user.imageUrl;
                const initials = name.slice(0, 2).toUpperCase();

                container.innerHTML = `
                    <div class="user-menu-container">
                        <button class="user-avatar-btn" onclick="AuthModule.toggleDropdown()" title="${name}">
                            <img src="${avatarUrl}" alt="${initials}" onerror="this.style.display='none';this.parentElement.querySelector('.avatar-fallback').style.display='flex'" />
                            <span class="avatar-fallback" style="display:none">${initials}</span>
                        </button>
                        <div class="user-dropdown" id="user-dropdown">
                            <div class="user-dropdown-header">
                                <div class="udh-avatar">
                                    <img src="${avatarUrl}" alt="${initials}" onerror="this.style.display='none';this.parentElement.querySelector('.avatar-fallback').style.display='flex'" />
                                    <span class="avatar-fallback" style="display:none">${initials}</span>
                                </div>
                                <div class="user-dropdown-info">
                                    <div class="user-dropdown-name">${name}</div>
                                    <div class="user-dropdown-email">${email}</div>
                                </div>
                            </div>

                            <div class="udm-section-label">Platform</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin()">
                                <span class="udi-icon">⚡</span> Admin Dashboard
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('agents')">
                                <span class="udi-icon">🤖</span> Agent Profiles
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('frameworks')">
                                <span class="udi-icon">🔌</span> Frameworks
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('install')">
                                <span class="udi-icon">📦</span> Install Framework
                            </div>

                            <div class="user-dropdown-divider"></div>
                            <div class="udm-section-label">Appearance</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('themes')">
                                <span class="udi-icon">🎨</span> Themes & Colors
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('face')">
                                <span class="udi-icon">👁️</span> Face Display
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('voice')">
                                <span class="udi-icon">🎙️</span> Voice Preview
                            </div>

                            <div class="user-dropdown-divider"></div>
                            <div class="udm-section-label">System</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('djtools')">
                                <span class="udi-icon">✏️</span> DJ Prompt Editor
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('tests')">
                                <span class="udi-icon">🧪</span> Connector Tests
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('system')">
                                <span class="udi-icon">⚙️</span> System & Health
                            </div>
                            <div class="user-dropdown-divider"></div>
                            <div class="user-dropdown-item danger" onclick="AuthModule.signOut()">
                                <span class="udi-icon">🚪</span> Sign Out
                            </div>
                        </div>
                    </div>
                `;
            },

            _renderLocalMenu() {
                const container = document.getElementById('user-button');
                container.innerHTML = `
                    <div class="user-menu-container">
                        <button class="user-avatar-btn" onclick="AuthModule.toggleDropdown()" title="Menu">
                            <span class="avatar-fallback" style="display:flex">⚙️</span>
                        </button>
                        <div class="user-dropdown" id="user-dropdown">
                            <div class="user-dropdown-header">
                                <div class="user-dropdown-info" style="padding:4px 0">
                                    <div class="user-dropdown-name">Local Mode</div>
                                    <div class="user-dropdown-email">No authentication configured</div>
                                </div>
                            </div>

                            <div class="udm-section-label">Platform</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin()">
                                <span class="udi-icon">⚡</span> Admin Dashboard
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('agents')">
                                <span class="udi-icon">🤖</span> Agent Profiles
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('frameworks')">
                                <span class="udi-icon">🔌</span> Frameworks
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('install')">
                                <span class="udi-icon">📦</span> Install Framework
                            </div>

                            <div class="user-dropdown-divider"></div>
                            <div class="udm-section-label">Appearance</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('themes')">
                                <span class="udi-icon">🎨</span> Themes & Colors
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('face')">
                                <span class="udi-icon">👁️</span> Face Display
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openSettings('voice')">
                                <span class="udi-icon">🎙️</span> Voice Preview
                            </div>

                            <div class="user-dropdown-divider"></div>
                            <div class="udm-section-label">System</div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('djtools')">
                                <span class="udi-icon">✏️</span> DJ Prompt Editor
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('tests')">
                                <span class="udi-icon">🧪</span> Connector Tests
                            </div>
                            <div class="user-dropdown-item" onclick="AuthModule.openAdmin('system')">
                                <span class="udi-icon">⚙️</span> System & Health
                            </div>
                        </div>
                    </div>
                `;
                // Close dropdown when clicking outside
                document.addEventListener('click', (e) => {
                    const c = document.querySelector('.user-menu-container');
                    if (c && !c.contains(e.target) && this.dropdownOpen) {
                        this.closeDropdown();
                    }
                });
            },

            toggleDropdown() {
                const dropdown = document.getElementById('user-dropdown');
                if (!dropdown) return;
                this.dropdownOpen = !this.dropdownOpen;
                dropdown.classList.toggle('open', this.dropdownOpen);
            },

            closeDropdown() {
                const dropdown = document.getElementById('user-dropdown');
                if (dropdown) {
                    this.dropdownOpen = false;
                    dropdown.classList.remove('open');
                }
            },

            toggleQuickSettings() {
                this.closeDropdown();
                // Toggle the existing settings drawer
                const drawer = document.getElementById('settings-drawer');
                if (drawer) {
                    drawer.classList.toggle('open');
                }
            },

            openSettings(panel) {
                this.closeDropdown();
                window.SettingsPanel?.open(panel);
            },

            openAdmin(panel) {
                this.closeDropdown();
                const url = '/admin' + (panel ? '#' + panel : '');
                window.open(url, '_blank');
            },

            openCanvas(page) {
                this.closeDropdown();
                window.CanvasControl?.openPage(page);
            },

            signOut() {
                this.closeDropdown();
                Clerk.signOut();
            },

            isLoggedIn() {
                return !!this.user;
            },

            getUser() {
                return this.user;
            }
        };

        // ===== WEB SPEECH API STT =====
        // Browser-native speech recognition (free, no API keys needed)
        class GroqWhisperSTT {
            constructor(config = {}) {
                this.serverUrl = (config.serverUrl || window.AGENT_CONFIG?.serverUrl || window.location.origin).replace(/\/$/, '');
                this.isListening = false;
                this.onResult = null;
                this.onError = null;
                this.mediaRecorder = null;
                this.audioChunks = [];
                this.stream = null;
                this.isProcessing = false;

                // VAD (Voice Activity Detection) settings
                this.silenceTimer = null;
                this.silenceDelayMs = 3500; // 3.5s silence = end of speech (profile can override)
                this.vadThreshold = 50;     // FFT average amplitude threshold (profile can override)
                this.minSpeechMs = 300;     // Must sustain above threshold for this long before counting as speech
                this.maxRecordingMs = 45000; // 45s max recording before auto-chunk (profile can override)
                this.maxRecordingTimer = null;
                this.isSpeaking = false;
                this.stoppingRecorder = false;  // Flag to prevent duplicate stop attempts
                this.hadSpeechInChunk = false;  // Track if real speech happened in this chunk
                this._speechStartTime = 0; // When sustained speech started
                this._resumedSpeechStart = 0; // When resumed speech started (for clearing silence timer)
            }

            isSupported() {
                return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
            }

            async start() {
                if (this.isListening) return;

                try {
                    // Get microphone access
                    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });

                    // Create MediaRecorder with opus codec for best quality/size
                    const options = { mimeType: 'audio/webm;codecs=opus' };
                    this.mediaRecorder = new MediaRecorder(this.stream, options);

                    this.audioChunks = [];

                    this.mediaRecorder.ondataavailable = (event) => {
                        if (event.data.size > 0) {
                            this.audioChunks.push(event.data);
                        }
                    };

                    this.mediaRecorder.onstop = async () => {
                        if (this.audioChunks.length === 0 || this.isProcessing) return;

                        this.isProcessing = true;

                        // Clear any pending timers to prevent duplicate processing
                        if (this.silenceTimer) {
                            clearTimeout(this.silenceTimer);
                            this.silenceTimer = null;
                        }
                        if (this.maxRecordingTimer) {
                            clearTimeout(this.maxRecordingTimer);
                            this.maxRecordingTimer = null;
                        }

                        // Create audio blob
                        const audioBlob = new Blob(this.audioChunks, { type: 'audio/webm' });
                        this.audioChunks = [];

                        // Skip if no real speech was detected in this chunk or audio too small
                        if (!this.hadSpeechInChunk || audioBlob.size < 10000) {
                            console.log('Skipping Whisper - no speech or audio too small (' + audioBlob.size + ' bytes)');
                            this.isProcessing = false;
                            this.stoppingRecorder = false;
                            this.hadSpeechInChunk = false;
                            if (this.isListening) {
                                this.audioChunks = [];
                                this.mediaRecorder.start();
                            }
                            return;
                        }
                        this.hadSpeechInChunk = false;

                        // Send to local Whisper
                        try {
                            console.log('Sending audio to local Whisper... (' + audioBlob.size + ' bytes)');
                            const formData = new FormData();
                            formData.append('audio', audioBlob, 'audio.webm');

                            const response = await fetch(`${this.serverUrl}/api/stt/local`, {
                                method: 'POST',
                                body: formData
                            });

                            const data = await response.json();

                            if (data.transcript && data.transcript.trim()) {
                                console.log('Whisper transcript:', data.transcript);
                                if (this.onResult) this.onResult(data.transcript);
                            }

                        } catch (error) {
                            console.error('Whisper STT error:', error);
                            if (this.onError) this.onError(error);
                        } finally {
                            this.isProcessing = false;
                            this.stoppingRecorder = false;  // Reset flag after processing

                            // Restart recording if still listening
                            if (this.isListening) {
                                this.audioChunks = [];
                                this.mediaRecorder.start();
                            }
                        }
                    };

                    // Set up audio level monitoring for VAD
                    const audioContext = new AudioContext();
                    const source = audioContext.createMediaStreamSource(this.stream);
                    const analyser = audioContext.createAnalyser();
                    analyser.fftSize = 512;
                    source.connect(analyser);

                    const bufferLength = analyser.frequencyBinCount;
                    const dataArray = new Uint8Array(bufferLength);

                    const checkAudioLevel = () => {
                        if (!this.isListening) return;

                        analyser.getByteFrequencyData(dataArray);
                        const average = dataArray.reduce((a, b) => a + b) / bufferLength;

                        // Voice detection threshold (configurable via profile stt.vad_threshold)
                        const isSpeakingNow = average > this.vadThreshold;

                        if (isSpeakingNow && !this.isSpeaking) {
                            // Potential speech — check minimum duration before confirming
                            const now = Date.now();
                            if (!this._speechStartTime) {
                                this._speechStartTime = now;
                            }
                            if (now - this._speechStartTime < this.minSpeechMs) {
                                // Still below minimum — keep checking
                                requestAnimationFrame(checkAudioLevel);
                                return;
                            }

                            // Speech confirmed (sustained above threshold for minSpeechMs)
                            this.isSpeaking = true;
                            this.hadSpeechInChunk = true;
                            this._speechStartTime = 0;
                            console.log('🎤 Speech detected');

                            // Clear silence timer
                            if (this.silenceTimer) {
                                clearTimeout(this.silenceTimer);
                                this.silenceTimer = null;
                            }

                            // Start max recording safety timer (prevents unbounded audio)
                            if (!this.maxRecordingTimer && !this.isProcessing && !this.stoppingRecorder) {
                                this.maxRecordingTimer = setTimeout(() => {
                                    console.log('⏱️ Max recording duration reached, auto-chunking');
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
                            // Continued speech — user still talking after brief dip.
                            // Only clear silence timer after sustained speech (minSpeechMs) to
                            // prevent ambient noise blips from keeping recording open forever.
                            const now2 = Date.now();
                            if (!this._resumedSpeechStart) {
                                this._resumedSpeechStart = now2;
                            }
                            if (now2 - this._resumedSpeechStart >= this.minSpeechMs && this.silenceTimer) {
                                clearTimeout(this.silenceTimer);
                                this.silenceTimer = null;
                                this._resumedSpeechStart = 0;
                            }
                        } else if (!isSpeakingNow && !this.isSpeaking) {
                            // Below threshold and not yet confirmed — reset speech start timer
                            this._speechStartTime = 0;
                            this._resumedSpeechStart = 0;
                        } else if (!isSpeakingNow && this.isSpeaking && !this.isProcessing && !this.stoppingRecorder) {
                            // Stopped speaking - start silence timer (ONLY if not already processing or stopping)
                            this._resumedSpeechStart = 0;
                            if (!this.silenceTimer) {
                                this.silenceTimer = setTimeout(() => {
                                    console.log('🔇 Silence detected, processing audio');
                                    this.isSpeaking = false;
                                    this.stoppingRecorder = true;  // Set flag immediately to block new timers

                                    // Stop recording to trigger transcription
                                    if (this.mediaRecorder && this.mediaRecorder.state === 'recording') {
                                        this.mediaRecorder.stop();
                                    }
                                }, this.silenceDelayMs);
                            }
                        }

                        requestAnimationFrame(checkAudioLevel);
                    };

                    this.mediaRecorder.start();
                    this.isListening = true;
                    checkAudioLevel();

                    console.log('Local Whisper STT started');

                } catch (error) {
                    console.error('Failed to start local Whisper STT:', error);
                    if (this.onError) this.onError(error);
                }
            }

            stop() {
                this.isListening = false;
                this.stoppingRecorder = false;  // Reset flag when stopping

                if (this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }
                if (this.maxRecordingTimer) {
                    clearTimeout(this.maxRecordingTimer);
                    this.maxRecordingTimer = null;
                }

                if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                    this.mediaRecorder.stop();
                }

                if (this.stream) {
                    this.stream.getTracks().forEach(track => track.stop());
                    this.stream = null;
                }

                console.log('Local Whisper STT stopped');
            }

            resetProcessing() {
                this.isProcessing = false;
            }

            mute() {
                this.isProcessing = true;
                this.hadSpeechInChunk = false;
                if (this.silenceTimer) {
                    clearTimeout(this.silenceTimer);
                    this.silenceTimer = null;
                }
            }

            resume() {
                this.isProcessing = false;
                this.stoppingRecorder = false;
                this.hadSpeechInChunk = false;
                this.audioChunks = [];  // Discard audio captured during mute
            }
        }

        // WebSpeechSTT and WakeWordDetector imported from /src/providers/WebSpeechSTT.js

        // ===== CLAWDBOT MODE =====
        // WebSocket client for Clawdbot integration with chat + TTS
        class ClawdBotMode {
            stripReasoningTokens(text) {
                // NOTE: Only strip NO_REPLY markers. The old GPT-OSS-120B reasoning
                // patterns ("I should", "The user", "They say") were matching normal
                // Z.AI/claude response text and nuking it from the transcript panel.
                if (!text) return text;
                return text.replace(/NO_REPLY/g, '').trim();
            }

            constructor(config, sharedSTT = null) {
                this.config = config;
                this.ws = null;
                this.sessionKey = 'main';
                this.audioQueue = [];       // stores {base64, format} objects
                this.currentAudio = null;   // HTMLAudioElement (fallback path only)
                this.currentSource = null;  // AudioBufferSourceNode (AudioContext path)
                this._audioCtx = null;      // AudioContext — unlocked in startVoiceInput()
                this.isPlaying = false;
                this.isConnected = false;
                this.isConnecting = false;
                this._fetchAbortController = null;

                // Use shared STT instance instead of creating a new one
                // This prevents conflicts with VoiceConversation's STT
                this.stt = sharedSTT || new WebSpeechSTT();

                // Wake detector state
                this.restartWakeAfter = false;

                // Session tracking for conversation logging
                this.sessionId = null;

                this.callbacks = {
                    onConnect: () => {},
                    onDisconnect: () => {},
                    onSpeaking: () => {},
                    onListening: () => {},
                    onMessage: (role, text) => {},
                    onError: (error) => {}
                };
            }

            getUIContext() {
                /** Gather current UI state for context injection */
                const context = {
                    canvasDisplayed: null,
                    canvasVisible: false,
                    canvasMenuOpen: false,
                    componentsOpen: []
                };

                // Check if canvas container is visible
                if (typeof CanvasControl !== 'undefined' && CanvasControl.isVisible) {
                    context.canvasVisible = true;
                }

                // Check what canvas page is displayed
                // Priority: canvasContext (set on navigation) → iframe src → localStorage
                if (window.canvasContext?.current_page) {
                    context.canvasDisplayed = window.canvasContext.current_page;
                } else if (typeof CanvasControl !== 'undefined' && CanvasControl.iframe?.src) {
                    const _iframeSrc = CanvasControl.iframe.src;
                    const _pageMatch = _iframeSrc.match(/\/pages\/([^?#]+)/);
                    if (_pageMatch) context.canvasDisplayed = `/pages/${_pageMatch[1]}`;
                } else {
                    const _lastPage = localStorage.getItem('canvas_last_page');
                    if (_lastPage) context.canvasDisplayed = `/pages/${_lastPage}`;
                }

                // Check if canvas menu is open
                if (typeof CanvasMenu !== 'undefined' && CanvasMenu.isVisible) {
                    context.canvasMenuOpen = true;
                }

                // Music player state
                if (window.musicPlayer) {
                    context.musicPlaying = window.musicPlayer.isPlaying || false;
                    if (window.musicPlayer.currentMetadata) {
                        context.musicTrack = window.musicPlayer.currentMetadata.title || window.musicPlayer.currentTrack || null;
                    }
                    context.musicPanelOpen = window.musicPlayer.panelState !== 'closed';
                }

                // Check for other open panels/modals
                const thoughtBubbles = document.getElementById('thought-bubbles');
                if (thoughtBubbles?.classList.contains('active')) {
                    context.componentsOpen.push('thought-bubbles');
                }

                // Recent canvas JS errors (last 5, auto-cleared after send)
                const _now = Date.now();
                const _recentErrors = (window._canvasErrorBuffer || [])
                    .filter(e => _now - e.ts < 30000)
                    .map(e => `${e.error} (line ${e.line})`);
                if (_recentErrors.length > 0) context.canvasErrors = _recentErrors;
                return context;
            }

            setCallbacks(callbacks) {
                this.callbacks = { ...this.callbacks, ...callbacks };
            }

            connect() {
                if (this.isConnecting || this.isConnected) return;
                this.isConnecting = true;

                // Determine WebSocket URL based on hostname
                // Connect to voice agent (main agent, voice-optimized)
                const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${wsProto}//${window.location.host}/ws/clawdbot?agent=main`;

                console.log('Clawdbot connecting to:', wsUrl);

                try {
                    this.ws = new WebSocket(wsUrl);

                    this.ws.onopen = () => {
                        console.log('Clawdbot connected');
                        this.isConnected = true;
                        this.isConnecting = false;
                        this.updateConnectionStatus('connected');
                        this.addSystemMessage('Connected to Clawdbot');
                        this.enableInput(true);
                        this.callbacks.onConnect();
                    };

                    this.ws.onmessage = (event) => {
                        try {
                            const data = JSON.parse(event.data);
                            this.handleMessage(data);
                        } catch (error) {
                            console.error('Failed to parse Clawdbot message:', error);
                        }
                    };

                    this.ws.onerror = (error) => {
                        console.error('Clawdbot WebSocket error:', error);
                        this.callbacks.onError('Connection error');
                        this.updateConnectionStatus('error');
                        this.addSystemMessage('Connection error');
                    };

                    this.ws.onclose = () => {
                        console.log('Clawdbot disconnected');
                        this.isConnected = false;
                        this.isConnecting = false;
                        this.updateConnectionStatus('disconnected');
                        this.addSystemMessage('Disconnected from Clawdbot');
                        this.enableInput(false);
                        this.callbacks.onDisconnect();
                    };
                } catch (error) {
                    this.isConnecting = false;
                    this.callbacks.onError(`Failed to connect: ${error.message}`);
                    this.updateConnectionStatus('error');
                }
            }

            disconnect() {
                if (this.ws) {
                    this.ws.close();
                    this.ws = null;
                }
                this.stopAudio();
                this.stopVoiceInput();
                this.isConnected = false;
                this.isConnecting = false;
                this._sessionGreeted = false;
                this._pendingGreeting = null;
            }

            async startVoiceInput() {
                // Debounce: prevent rapid start/stop/start cycles that poison the session
                if (this._startingSession) return;
                if (Date.now() - (this._lastCallToggle || 0) < 2000) {
                    console.warn('[Session] Debounce: ignoring rapid call toggle');
                    return;
                }
                this._startingSession = true;
                this._lastCallToggle = Date.now();

                this._voiceActive = true;  // Mark call as active
                this._sessionGreeted = false;  // Reset so every new call gets a greeting
                // Generate new session ID for this call
                this.sessionId = `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
                window._activeSessionId = this.sessionId;
                console.log('New conversation session:', this.sessionId);

                // Show thinking state immediately so user sees feedback before any async work
                FaceModule.setMood('thinking');
                StatusModule.update('thinking', 'CONNECTING...');
                document.getElementById('thought-bubbles')?.classList.add('active');
                window.HaloSmokeFace?.setThinking(true);

                // Guard mic from the start — greeting fetch + TTS play will set this properly
                // via onSpeaking, but setting it here prevents any STT results that arrive
                // between stt.start() and the first onSpeaking callback from slipping through
                this._ttsPlaying = true;

                // Hard safety timeout: if _ttsPlaying stays true for 20s, force-clear it.
                // Prevents permanent muting if greeting hangs or errors silently.
                if (this._ttsGuardTimer) clearTimeout(this._ttsGuardTimer);
                this._ttsGuardTimer = setTimeout(() => {
                    if (this._ttsPlaying && this._voiceActive) {
                        console.warn('[STT] Hard timeout: _ttsPlaying stuck for 20s — force-clearing');
                        this._ttsPlaying = false;
                        if (this.stt?.resume) this.stt.resume();
                        this.callbacks.onListening();
                    }
                }, 20000);

                // Unlock AudioContext for iOS — MUST happen synchronously within the user
                // gesture call stack, before any await. iOS suspends AudioContext by default
                // and blocks async audio.play() calls that arrive via network responses.
                // AudioContext.resume() here "unlocks" it for all subsequent programmatic
                // audio playback in this session.
                try {
                    if (!this._audioCtx) {
                        this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                        // Create analyser for audio-reactive faces (HaloSmokeFace etc.)
                        this._analyser = this._audioCtx.createAnalyser();
                        this._analyser.fftSize = 2048;
                        this._analyser.smoothingTimeConstant = 0.55;
                        this._analyser.minDecibels = -95;
                        this._analyser.maxDecibels = -12;
                        this._analyser.connect(this._audioCtx.destination);
                        window.audioAnalyser = this._analyser;
                    }
                    if (this._audioCtx.state === 'suspended') {
                        await this._audioCtx.resume();
                    }
                } catch (e) {
                    console.warn('[ClawdBot] AudioContext unlock failed:', e);
                    this._audioCtx = null;
                }

                // Stop wake word detector before starting STT (both use Web Speech API)
                if (window.wakeDetector && window.wakeDetector.isListening) {
                    window.wakeDetector.stop();
                    this.restartWakeAfter = true;
                }

                // Set up STT callbacks BEFORE greeting so they're ready when STT starts
                // onListenFinal fires IMMEDIATELY when Groq returns a transcript chunk
                // — show user text right away so they know the system heard them.
                this.stt.onListenFinal = (text) => {
                    if (this._ttsPlaying || !text || !text.trim()) return;
                    if (this.stt._micMuted) return;  // PTT mode — ignore leaked transcripts
                    // Show in transcript immediately + set thinking state
                    this.displayMessage('user', text.trim());
                    this.callbacks.onMessage('user', text.trim());
                    TranscriptPanel.addMessage('user', text.trim());
                    FaceModule.setMood('thinking');
                    StatusModule.update('thinking', 'THINKING');
                    TranscriptPanel.showThinking();
                    document.getElementById('thought-bubbles')?.classList.add('active');
                    window.HaloSmokeFace?.setThinking(true);
                };

                this.stt.onResult = (transcript) => {
                    // Ignore any results that arrive while TTS is playing (leftover audio)
                    if (this._ttsPlaying) {
                        console.log('Ignoring transcript during TTS:', transcript);
                        return;
                    }
                    // Echo cooldown: ignore STT for 2.5s after TTS ends to prevent
                    // TTS audio from being picked up by the mic and re-submitted.
                    if (this._ttsEchoCooldownUntil && Date.now() < this._ttsEchoCooldownUntil) {
                        console.log('Ignoring transcript in echo cooldown:', transcript);
                        return;
                    }
                    if (this.stt._micMuted) return;  // PTT mode — ignore leaked transcripts
                    if (transcript && transcript.trim()) {
                        console.log('Voice input:', transcript);
                        this.sendMessage(transcript.trim(), { skipDisplay: true });
                    }
                };

                this.stt.onError = (error) => {
                    console.error('STT error:', error);
                    if (error === 'not-allowed') {
                        this.callbacks.onError('Microphone access denied');
                        this.addSystemMessage('Microphone access denied — check browser permissions');
                    } else if (error === 'no-device') {
                        this.callbacks.onError('No microphone found');
                        this.addSystemMessage('No microphone detected — plug in a mic and try again');
                    } else if (error === 'network' || error === 'service-not-allowed') {
                        this.callbacks.onError('Speech recognition unavailable');
                        this.addSystemMessage('Speech recognition unavailable — check internet connection');
                    }
                };

                // Auto-send greeting trigger so agent greets immediately
                // AWAIT the greeting so STT doesn't start until TTS finishes
                if (!this._sessionGreeted) {
                    this._sessionGreeted = true;
                    await this._sendGreetingTrigger();
                }

                // Start STT (muted — mute() was called during greeting's onSpeaking)
                if (await this.stt.start()) {
                    console.log('Voice input started');
                    ActionConsole.addEntry('stt', 'Microphone active — listening');
                    // Only fire onListening if no audio is playing and no fetch is in-flight.
                    // If the greeting is still streaming/playing, the audio playback chain
                    // will fire onListening (with echo guard delay) when TTS actually finishes.
                    if (!this.isPlaying && this.audioQueue.length === 0 && !this._fetchAbortController) {
                        this._ttsPlaying = false;
                        if (this._ttsGuardTimer) { clearTimeout(this._ttsGuardTimer); this._ttsGuardTimer = null; }
                        if (this.stt.resume) this.stt.resume();
                        this.callbacks.onListening();
                    }
                } else {
                    console.error('Failed to start voice input');
                    this.callbacks.onError('Failed to start voice input');
                }
                this._startingSession = false;
            }

            stopVoiceInput() {
                this._startingSession = false;
                this._voiceActive = false;  // Mark call as ended — prevents safety-net restart
                this._ttsPlaying = false;
                if (this._ttsGuardTimer) { clearTimeout(this._ttsGuardTimer); this._ttsGuardTimer = null; }
                // Abort any in-flight fetch so streaming stops immediately
                if (this._fetchAbortController) {
                    this._fetchAbortController.abort();
                    this._fetchAbortController = null;
                    // Tell server to abort the openclaw run (fire-and-forget)
                    console.warn('⛔ ABORT source: stopVoiceInput');
                    fetch(`${this.config.serverUrl}/api/conversation/abort`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ source: 'stopVoiceInput' }),
                    }).catch(() => {});
                }
                this.stopAudio();
                if (this.stt) {
                    this.stt.stop();
                    console.log('Voice input stopped');
                }

                // Resume wake word detector after stopping voice input
                if (this.restartWakeAfter && window.wakeDetector && window.wakeDetector.isSupported()) {
                    window.wakeDetector.start();
                    this.restartWakeAfter = false;
                }
            }

            handleMessage(data) {
                switch (data.type) {
                    case 'connected':
                        this.addSystemMessage(`Clawdbot session started: ${data.sessionId || 'main'}`);
                        break;

                    case 'assistant_message':
                        // Ignore - responses handled via HTTP in sendMessage()
                        // WebSocket receives duplicate from Gateway session broadcast
                        console.log('Clawdbot WS: ignoring duplicate assistant_message');
                        break;

                    case 'text_delta':
                        // Streaming text update (optional)
                        if (data.delta) {
                            this.appendTextDelta(data.delta);
                        }
                        break;

                    case 'error':
                        this.addSystemMessage(`Error: ${data.message || 'Unknown error'}`);
                        this.callbacks.onError(data.message || 'Unknown error');
                        break;

                    default:
                        console.log('Clawdbot: Unknown message type:', data.type);
                }
            }

            async sendMessage(text, opts = {}) {
                if (!text || !text.trim()) return;

                // ── Interrupt vs Steer ─────────────────────────────────────
                // When a new message arrives while a request is in-flight:
                //   • TTS playing → ABORT  (stop spoken output, kill run, fresh start)
                //   • Agent working silently (tools/subagents) → STEER
                //     Send the message fire-and-forget; openclaw injects it at the
                //     next tool boundary (messages.queue.mode=steer).  The existing
                //     streaming fetch continues receiving the steered output.
                if (this._fetchAbortController) {
                    if (this._ttsPlaying) {
                        // Agent already responded, TTS playing → ABORT
                        this._fetchAbortController.abort();
                        this._fetchAbortController = null;
                        console.warn(`⛔ ABORT source: ClawdbotMode.sendMessage (TTS playing, new msg: "${text.substring(0,30)}")`);
                        fetch(`${this.config.serverUrl}/api/conversation/abort`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ source: 'clawdbot-sendMessage', text: text.substring(0, 50) }),
                        }).catch(() => {});
                        this.stopAudio();
                    } else {
                        // Agent working silently → STEER (inject at next tool boundary)
                        console.log(`🔀 STEER: injecting "${text.substring(0,50)}" into active run`);
                        ActionConsole.addEntry('system', `Steering: "${text.substring(0, 60)}"`);
                        fetch(`${this.config.serverUrl}/api/conversation/steer`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message: text, source: 'clawdbot-sendMessage' }),
                        }).catch(() => {});
                        // Show the user's steer message in transcript
                        if (!opts.skipDisplay) {
                            this.displayMessage('user', text);
                            this.callbacks.onMessage('user', text);
                            TranscriptPanel.addMessage('user', text, { imageUrl: opts.imageUrl || null });
                        }
                        // Return early — the existing streaming response will receive
                        // the steered output.  Do NOT abort fetch or start a new one.
                        return;
                    }
                } else {
                    this.stopAudio();
                }

                // Wait for previous send to finish unwinding after abort
                if (this._sending) {
                    await new Promise(r => setTimeout(r, 150));
                }
                if (this._sending) {
                    console.warn('sendMessage: previous send still unwinding, forcing reset');
                    this._sending = false;
                }
                this._sending = true;

                // System trigger messages are invisible to the user
                const isSystemTrigger = text.startsWith('__session_start__');
                if (!isSystemTrigger && !opts.skipDisplay) {
                    this.displayMessage('user', text);
                    this.callbacks.onMessage('user', text);
                    TranscriptPanel.addMessage('user', text, { imageUrl: opts.imageUrl || null });
                }

                // Show thinking state while waiting for response
                FaceModule.setMood('thinking');
                StatusModule.update('thinking', 'THINKING');
                TranscriptPanel.showThinking();
                document.getElementById('thought-bubbles')?.classList.add('active');
                window.HaloSmokeFace?.setThinking(true);

                ActionConsole.addEntry('chat', `Sent: "${text.substring(0, 80)}${text.length > 80 ? '...' : ''}"`);
                window._canvasErrorBuffer = []; // clear after send

                let messageToSend = text.trim();

                // Send via HTTP POST - server connects to Gateway, generates TTS, returns response
                let _inactivityTimer = null;  // declared here so finally block can clear it
                try {
                    const provider = window.providerManager?.selectedProvider || 'groq';
                    const voice = window.providerManager?.currentVoice || 'autumn';
                    const uiContext = this.getUIContext();

                    const gatewayAgentId = localStorage.getItem('gateway_agent_id') || null;
                    this._fetchAbortController = new AbortController();
                    const response = await fetch(`${this.config.serverUrl}/api/conversation?stream=1`, {
                        method: 'POST',
                        signal: this._fetchAbortController.signal,
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            message: messageToSend,
                            tts_provider: provider,
                            voice: voice,
                            session_id: this.sessionId,
                            ui_context: uiContext,
                            identified_person: window.cameraModule?.currentIdentity || null,
                            ...(gatewayAgentId ? { agent_id: gatewayAgentId } : {}),
                            ...(window._maxResponseChars ? { max_response_chars: window._maxResponseChars } : {}),
                            ...(opts.image_path ? { image_path: opts.image_path } : {})
                        })
                    });

                    if (!response.ok) {
                        throw new Error(`API error: ${response.status}`);
                    }
                    ActionConsole.addEntry('system', 'Connected to server — waiting for agent...');

                    // Stream mode: read NDJSON lines with real-time deltas
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = '';

                    // Inactivity timeout: abort if no data received for 60s
                    // (heartbeats arrive every 10-15s during tool execution)
                    // _inactivityTimer declared in outer scope so finally{} can clear it
                    const INACTIVITY_TIMEOUT_MS = 60000;
                    const _resetInactivity = () => {
                        if (_inactivityTimer) clearTimeout(_inactivityTimer);
                        _inactivityTimer = setTimeout(() => {
                            console.warn('[Stream] No data for 60s — aborting');
                            this._fetchAbortController?.abort();
                        }, INACTIVITY_TIMEOUT_MS);
                    };
                    _resetInactivity();
                    let streamingText = '';  // Accumulate delta text
                    let firstDeltaReceived = false;
                    let streamingMsgEl = null;  // Reference to the streaming message element
                    let canvasCommandsProcessed = new Set();  // Track processed canvas commands

                    // Helper: escape HTML to prevent XSS (must run before innerHTML)
                    const escapeHtml = (str) => str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

                    // Helper: strip canvas and music tags from display text
                    const stripCanvasTags = (text) => {
                        return text
                            .replace(/```html[\s\S]*?```/gi, '')  // complete html fences
                            .replace(/```[\s\S]*?```/g, '')        // complete generic fences
                            .replace(/```html[\s\S]*/gi, '')       // unclosed html fence (streaming)
                            .replace(/```[\s\S]*/g, '')            // unclosed generic fence (streaming)
                            .replace(/\[CANVAS_MENU\]/gi, '')
                            .replace(/\[CANVAS:[^\]]*\]/gi, '')
                            .replace(/\[CANVAS_URL:[^\]]*\]/gi, '')
                            .replace(/\[MUSIC_PLAY(?::[^\]]*)?\]/gi, '')
                            .replace(/\[MUSIC_STOP\]/gi, '')
                            .replace(/\[MUSIC_NEXT\]/gi, '')
                            .replace(/\[SESSION_RESET\]/gi, '')
                            .replace(/\[SUNO_GENERATE:[^\]]*\]/gi, '')
                            .replace(/\[SPOTIFY:[^\]]*\]/gi, '')
                            .replace(/\[SLEEP\]/gi, '')
                            .replace(/\[REGISTER_FACE:[^\]]*\]/gi, '')
                            .replace(/\[SOUND:[^\]]*\]/gi, '')
                            .trim();
                    };

                    // Helper: check for canvas/music commands in accumulated text
                    const checkCanvasInStream = async (text) => {
                        // Check for [CANVAS_MENU]
                        if (/\[CANVAS_MENU\]/i.test(text) && !canvasCommandsProcessed.has('CANVAS_MENU')) {
                            canvasCommandsProcessed.add('CANVAS_MENU');
                            console.log('[Canvas] CANVAS_MENU trigger detected');
                            ActionConsole.addEntry('system', 'Canvas: opening menu');
                            AgentActivityChip.handleTag('canvas_menu');
                            CanvasControl.showMenu?.() || document.getElementById('canvas-menu-button')?.click();
                        }
                        // Check for [CANVAS:pagename]
                        const canvasMatch = text.match(/\[CANVAS:([^\]]+)\]/i);
                        if (canvasMatch && !canvasCommandsProcessed.has('CANVAS_PAGE')) {
                            canvasCommandsProcessed.add('CANVAS_PAGE');
                            const pageName = canvasMatch[1].trim();
                            console.log('[Canvas] CANVAS page trigger:', pageName);
                            ActionConsole.addEntry('system', `Canvas: opening ${pageName}`);
                            AgentActivityChip.handleTag('canvas', pageName);
                            // Sync manifest first so newly created pages are found
                            try {
                                await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/sync`, { method: 'POST' });
                                await window.CanvasMenu?.loadManifest();
                            } catch (e) { console.warn('[Canvas] manifest sync failed:', e); }
                            CanvasControl.showPage?.(pageName);
                        }
                        // Check for [MUSIC_PLAY] or [MUSIC_PLAY:track]
                        const musicPlay = text.match(/\[MUSIC_PLAY(?::([^\]]+))?\]/i);
                        if (musicPlay && !canvasCommandsProcessed.has('MUSIC_PLAY')) {
                            canvasCommandsProcessed.add('MUSIC_PLAY');
                            const trackName = musicPlay[1]?.trim();
                            ActionConsole.addEntry('system', trackName ? `Music: playing "${trackName}"` : 'Music: playing');
                            AgentActivityChip.handleTag('music_play', trackName);
                            // Always open the panel regardless of whether tracks exist
                            if (window.musicPlayer?.panelState === 'closed') window.musicPlayer.openPanel();
                            if (trackName) {
                                window.musicPlayer?.play(trackName);
                            } else {
                                window.musicPlayer?.play();
                            }
                        }
                        // Check for [MUSIC_STOP]
                        if (/\[MUSIC_STOP\]/i.test(text) && !canvasCommandsProcessed.has('MUSIC_STOP')) {
                            canvasCommandsProcessed.add('MUSIC_STOP');
                            ActionConsole.addEntry('system', 'Music: stopped');
                            AgentActivityChip.handleTag('music_stop');
                            window.musicPlayer?.stop();
                        }
                        // Check for [MUSIC_NEXT]
                        if (/\[MUSIC_NEXT\]/i.test(text) && !canvasCommandsProcessed.has('MUSIC_NEXT')) {
                            canvasCommandsProcessed.add('MUSIC_NEXT');
                            ActionConsole.addEntry('system', 'Music: next track');
                            AgentActivityChip.handleTag('music_next');
                            window.musicPlayer?.next();
                        }
                        [...text.matchAll(/\[SUNO_GENERATE:([^\]]+)\]/gi)].forEach(m => {
                            const sunoPrompt = m[1].trim();
                            ActionConsole?.addEntry('system', `🎵 Suno: generating "${sunoPrompt.substring(0, 60)}${sunoPrompt.length > 60 ? '...' : ''}"`);
                            AgentActivityChip?.handleTag('suno', sunoPrompt);
                            window.sunoModule?.generate(sunoPrompt);
                        });
                        // Check for [SPOTIFY:track name|artist] — switches player to Spotify mode
                        const spotifyMatch = text.match(/\[SPOTIFY:([^|\]]+)(?:\|([^\]]+))?\]/i);
                        if (spotifyMatch && !canvasCommandsProcessed.has('SPOTIFY')) {
                            canvasCommandsProcessed.add('SPOTIFY');
                            const spotifyTrack = spotifyMatch[1].trim();
                            const spotifyArtist = spotifyMatch[2]?.trim() || '';
                            ActionConsole.addEntry('system', `Spotify: "${spotifyTrack}"${spotifyArtist ? ` by ${spotifyArtist}` : ''}`);
                            AgentActivityChip.handleTag('spotify', spotifyTrack);
                            window.musicPlayer?.playSpotify(spotifyTrack, spotifyArtist);
                        }
                        // Check for [REGISTER_FACE:name] — agent registers current camera frame
                        const registerFaceMatch = text.match(/\[REGISTER_FACE:([^\]]+)\]/i);
                        if (registerFaceMatch && !canvasCommandsProcessed.has('REGISTER_FACE')) {
                            canvasCommandsProcessed.add('REGISTER_FACE');
                            const personName = registerFaceMatch[1].trim();
                            ActionConsole.addEntry('system', `Face: registering "${personName}"`);
                            AgentActivityChip.handleTag('register_face', personName);
                            const cam = window.cameraModule;
                            if (cam && cam.stream) {
                                const ctx = cam.canvas.getContext('2d');
                                cam.canvas.width = 640; cam.canvas.height = 480;
                                ctx.drawImage(cam.video, 0, 0, 640, 480);
                                const imageData = cam.canvas.toDataURL('image/jpeg', 0.8);
                                fetch(`${CONFIG.serverUrl}/api/faces/${encodeURIComponent(personName)}`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({ image: imageData })
                                }).then(() => {
                                    console.log(`[FaceReg] Registered face: ${personName}`);
                                    window.FacePanel?.loadFaces();
                                    window.FaceID?.loadKnownFaces();
                                }).catch(e => console.error('[FaceReg] Error:', e));
                            } else {
                                console.warn('[FaceReg] Camera not active — cannot register face');
                            }
                        }
                        // Check for [SOUND:name] — DJ soundboard effect
                        const soundMatch = text.match(/\[SOUND:([^\]]+)\]/i);
                        if (soundMatch && !canvasCommandsProcessed.has('SOUND')) {
                            canvasCommandsProcessed.add('SOUND');
                            const soundName = soundMatch[1].trim();
                            console.log('[Sound] DJ sound trigger:', soundName);
                            ActionConsole.addEntry('system', `Sound: ${soundName}`);
                            AgentActivityChip.handleTag('sound', soundName);
                            DJSoundboard.play(soundName);
                        }
                        // Check for [CANVAS_URL:https://example.com] — load external URL in iframe
                        const canvasUrlMatch = text.match(/\[CANVAS_URL:([^\]]+)\]/i);
                        if (canvasUrlMatch && !canvasCommandsProcessed.has('CANVAS_URL')) {
                            canvasCommandsProcessed.add('CANVAS_URL');
                            const externalUrl = canvasUrlMatch[1].trim();
                            const resolvedUrl = resolveCanvasUrl(externalUrl);
                            if (resolvedUrl) {
                                console.log('[Canvas] External URL trigger:', externalUrl, resolvedUrl !== externalUrl ? `→ ${resolvedUrl}` : '');
                                ActionConsole.addEntry('system', `Canvas: loading ${resolvedUrl}`);
                                AgentActivityChip.handleTag('canvas_url', resolvedUrl);
                                const iframe = document.getElementById('canvas-iframe');
                                if (iframe) { iframe.src = resolvedUrl; CanvasControl.show(); }
                            } else {
                                console.warn('[Canvas] Blocked private/internal URL from agent:', externalUrl);
                                ActionConsole.addEntry('system', `Canvas: blocked private URL — agent should use the public dev URL`);
                            }
                        }
                        // Check for [SLEEP] — agent-initiated return to wake-word mode
                        if (/\[SLEEP\]/i.test(text) && !canvasCommandsProcessed.has('SLEEP')) {
                            canvasCommandsProcessed.add('SLEEP');
                            console.log('[Sleep] Agent requested sleep — will disconnect after audio');
                            ActionConsole.addEntry('system', 'Sleep: going to standby');
                            AgentActivityChip.handleTag('sleep');
                            window._sleepAfterResponse = true;
                        }
                        // Early HTML canvas: as soon as </html> lands in the stream, save and show
                        // Don't wait for text_done — avoids incomplete-response failures
                        if (!canvasCommandsProcessed.has('HTML_CANVAS')) {
                            const htmlEarlyMatch =
                                text.match(/```html\s*(<!DOCTYPE\s+html[\s\S]*?<\/html>)/i) ||
                                text.match(/```html\s*(<html[\s\S]*?<\/html>)/i);
                            if (htmlEarlyMatch) {
                                canvasCommandsProcessed.add('HTML_CANVAS');
                                console.log('[Canvas] Complete HTML detected in stream, saving early...');
                                ActionConsole.addEntry('system', 'Canvas: building page from HTML');
                                AgentActivityChip.handleTag('html_canvas');
                                this._saveAndShowHtml(htmlEarlyMatch[1].trim());
                            }
                        }
                    };

                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        _resetInactivity();
                        buffer += decoder.decode(value, { stream: true });

                        // Process complete NDJSON lines
                        let newlineIdx;
                        while ((newlineIdx = buffer.indexOf('\n')) !== -1) {
                            const line = buffer.slice(0, newlineIdx).trim();
                            buffer = buffer.slice(newlineIdx + 1);
                            if (!line) continue;

                            try {
                                const data = JSON.parse(line);

                                // Filtered: server rejected garbage STT — silently resume
                                if (data.type === 'filtered') {
                                    console.log('[stream] Garbage STT filtered by server:', data.reason);
                                    this._lastStreamFiltered = true;
                                    continue;
                                }

                                // Delta: new text chunk from agent (arrives in real-time)
                                if (data.type === 'delta') {
                                    streamingText += data.text;

                                    if (!firstDeltaReceived) {
                                        firstDeltaReceived = true;
                                        FaceModule.setMood('neutral');
                                        StatusModule.update('speaking', 'SPEAKING');
                                        TranscriptPanel.removeThinking();
                                        TranscriptPanel.startStreaming();
                                        document.getElementById('thought-bubbles')?.classList.remove('active');
                                        window.HaloSmokeFace?.setThinking(false);
                                        // Create streaming message element
                                        streamingMsgEl = this.displayMessage('assistant', stripCanvasTags(streamingText), true);
                                    } else if (streamingMsgEl) {
                                        // Update existing message in-place
                                        const displayText = escapeHtml(stripCanvasTags(streamingText)).replace(/\n/g, '<br>');
                                        const textEl = streamingMsgEl.querySelector('.message-text') || streamingMsgEl;
                                        textEl.innerHTML = displayText;
                                        // Auto-scroll
                                        const historyDiv = document.getElementById('chat-history');
                                        if (historyDiv) historyDiv.scrollTop = historyDiv.scrollHeight;
                                        // Update TranscriptPanel in real-time too
                                        TranscriptPanel.updateStreaming(stripCanvasTags(streamingText));
                                    }

                                    // Check for canvas commands as they stream in
                                    checkCanvasInStream(streamingText);
                                }

                                // Heartbeat: server is alive, agent is working
                                if (data.type === 'heartbeat') {
                                    const secs = data.elapsed || 0;
                                    this._wasAgentic = true;
                                    StatusModule.update('thinking', `WORKING... ${secs}s`);
                                    AgentActivityChip.show('⏳', `Working... ${secs}s`);
                                }

                                // Queued: openclaw is busy, message will be processed after current run
                                if (data.type === 'queued') {
                                    StatusModule.update('thinking', 'QUEUED — agent busy');
                                    ActionConsole.addEntry('system', 'Agent is busy — message queued for next turn');
                                }

                                // Action: tool use or lifecycle event
                                if (data.type === 'action') {
                                    ActionConsole.processActions([data.action]);
                                    AgentActivityChip.handleAction(data.action);
                                    // Show tool use in status bar and transcript
                                    if (data.action.type === 'tool' && data.action.phase === 'start') {
                                        const toolLabel = data.action.name || 'tool';
                                        StatusModule.update('thinking', `TOOL: ${toolLabel}`);
                                        TranscriptPanel.showToolStatus(toolLabel);
                                    }
                                }

                                // Server retrying empty response — keep stream alive, no fallback
                                if (data.type === 'retrying') {
                                    console.log('[Conversation] Server retrying empty response — waiting for result...');
                                    continue;
                                }

                                // Text done: full response finalized
                                if (data.type === 'text_done') {
                                    const fullResponse = data.response || streamingText;
                                    const cleanedResponse = this.stripReasoningTokens(fullResponse);
                                    const displayText = stripCanvasTags(cleanedResponse);

                                    // Empty response fallback — show message and re-enable mic
                                    if (!displayText || !displayText.trim()) {
                                        // If server filtered garbage STT, silently resume mic
                                        if (this._lastStreamFiltered) {
                                            console.log('[text_done] Filtered garbage STT — silent resume');
                                            this._lastStreamFiltered = false;
                                            TranscriptPanel.finalizeStreaming(null);
                                            reader.cancel();
                                            return;
                                        }
                                        // Session start empty response — agent just wasn't ready yet.
                                        // Don't show "Sorry" — user hasn't said anything. Just resume silently.
                                        if (isSystemTrigger) {
                                            console.warn('[text_done] Empty response on session_start — silent resume');
                                            TranscriptPanel.finalizeStreaming(null);
                                            ActionConsole.addEntry('error', 'Empty response from agent');
                                            reader.cancel();
                                            return;
                                        }
                                        console.warn('[text_done] Empty response — showing fallback');
                                        const fallback = "Sorry, I couldn't process that. Could you try again?";
                                        TranscriptPanel.finalizeStreaming(fallback);
                                        ActionConsole.addEntry('error', 'Empty response from agent');
                                        // Don't send fallback to TTS — just re-enable mic
                                        reader.cancel();
                                        return;
                                    }

                                    if (displayText === this._lastResponse) {
                                        console.log('Skipping duplicate response');
                                        TranscriptPanel.finalizeStreaming(null);
                                        reader.cancel();
                                        return;
                                    }
                                    this._lastResponse = displayText;

                                    // Safety net: if user said goodbye but agent forgot [SLEEP], trigger it
                                    const _goodbyeRe = /^(bye|goodbye|good night|goodnight|see you later|see ya|go to sleep|stop listening|later|peace out|night night|i'm out|gotta go|talk to you later|ttyl)\b/i;
                                    if (_goodbyeRe.test(text.trim()) && !/\[SLEEP\]/i.test(fullResponse)) {
                                        console.log('[Sleep] Safety net: user said goodbye but agent forgot [SLEEP] — injecting sleep');
                                        window._sleepAfterResponse = true;
                                    }

                                    // Final update of streaming message or create new if no deltas came
                                    if (streamingMsgEl) {
                                        streamingMsgEl.classList.remove('streaming');
                                        const textEl = streamingMsgEl.querySelector?.('.message-text') || streamingMsgEl;
                                        textEl.innerHTML = escapeHtml(displayText).replace(/\n/g, '<br>');
                                    } else {
                                        this.displayMessage('assistant', displayText);
                                    }

                                    // Process any remaining canvas commands (pass processed set to avoid double-save)
                                    this.handleCanvasCommands(cleanedResponse, canvasCommandsProcessed);

                                    this.callbacks.onMessage('assistant', displayText);
                                    TranscriptPanel.finalizeStreaming(displayText);

                                    this._wasAgentic = false;
                                    if (data.actions) ActionConsole.processActions(data.actions);
                                    ActionConsole.addEntry('system', `Response complete (${fullResponse?.length || 0} chars, LLM: ${data.timing?.llm_ms}ms)`);
                                    AgentActivityChip.show('✅', 'Done');
                                    AgentActivityChip.hide(1500);
                                    console.log(`Text finalized (LLM: ${data.timing?.llm_ms}ms) — waiting for TTS...`);
                                }

                                // Audio: TTS ready to play
                                if (data.type === 'audio') {
                                    if (data.audio) {
                                        console.log(`TTS ready (${data.timing?.tts_ms}ms, total: ${data.timing?.total_ms}ms)`);
                                        ActionConsole.addEntry('tts', `Playing TTS (TTS: ${data.timing?.tts_ms}ms)`);
                                        AgentActivityChip.show('🔊', 'Speaking...');
                                        this.playAudio(data.audio, data.audio_format || 'wav');
                                    } else {
                                        console.warn('No audio in TTS response');
                                        ActionConsole.addEntry('error', 'No audio generated');
                                    }
                                }

                                // Session auto-reset (3 consecutive empty responses)
                                if (data.type === 'session_reset') {
                                    console.warn('Session auto-reset:', data.old, '→', data.new);
                                    ActionConsole.addEntry('system', `Session reset: ${data.old} → ${data.new} (${data.reason})`);
                                    this.addSystemMessage('Session reset — next response may be slow.');
                                }

                                // AI provider rate limited — surface clearly
                                if (data.type === 'rate_limit') {
                                    const provider = data.provider || 'AI provider';
                                    console.warn('[Conversation] Rate limited by', provider, data.message);
                                    ActionConsole.addEntry('error', `⚠️ ${provider} throttled — using fallback model`);
                                    this.addSystemMessage(`⚠️ ${provider} is temporarily throttled. Response came from fallback model.`);
                                }

                                // Generic server error — surface prominently
                                if (data.type === 'error') {
                                    console.error('Stream error:', data.error);
                                    ActionConsole.addEntry('error', data.error);
                                    // Gateway connection errors should be visible to the user
                                    if (data.error?.includes('connect') || data.error?.includes('Gateway') || data.error?.includes('Failed')) {
                                        this.addSystemMessage('Agent connection error — retrying...');
                                        StatusModule.update('thinking', 'RECONNECTING...');
                                    }
                                }

                                // TTS-specific failure — response came through but audio failed
                                if (data.type === 'tts_error') {
                                    const reasonMessages = {
                                        'terms':             `${data.provider} TTS: Terms not accepted — visit console.groq.com to accept`,
                                        'rate_limit':        `${data.provider} TTS: Rate limit hit — try again in a moment`,
                                        'no_credits':        `${data.provider} TTS: Out of credits`,
                                        'bad_key':           `${data.provider} TTS: Invalid API key`,
                                        'agent_tool_misuse': `Agent tried to use its own TTS tool — reply was dropped. Ask again.`,
                                        'error':             `${data.provider} TTS failed: ${data.error}`,
                                    };
                                    const msg = reasonMessages[data.reason] || `TTS failed: ${data.error}`;
                                    console.error('TTS failed:', data);
                                    ActionConsole.addEntry('error', msg);
                                    this.addSystemMessage(`⚠️ ${msg}`);
                                    FaceModule.setMood('sad');
                                    setTimeout(() => FaceModule.setMood('neutral'), 3000);
                                    // Restart STT so the mic comes back (only if call still active)
                                    if (this._voiceActive && this.stt) {
                                        if (this.stt.resume) {
                                            this.stt.resume();
                                        } else {
                                            if (this.stt.resetProcessing) this.stt.resetProcessing();
                                            if (!this.stt.isListening) this.stt.start();
                                        }
                                        this.callbacks.onListening?.();
                                    }
                                }

                                // No audio: response had no speakable text (e.g. only a [CANVAS:] tag).
                                // Server signals this explicitly so we can unblock isProcessing immediately.
                                if (data.type === 'no_audio') {
                                    console.log('No audio for this response — resetting STT');
                                    ActionConsole.addEntry('system', 'Silent response — canvas/action only, mic re-enabled');
                                    FaceModule.setMood('neutral');
                                    if (this._voiceActive && this.stt) {
                                        if (this.stt.resume) {
                                            this.stt.resume();
                                        } else {
                                            if (this.stt.resetProcessing) this.stt.resetProcessing();
                                            if (!this.stt.isListening) this.stt.start();
                                        }
                                        this.callbacks.onListening?.();
                                    }
                                }

                            } catch (parseErr) {
                                console.warn('Failed to parse stream line:', parseErr);
                            }
                        }
                    }
                } catch (error) {
                    if (error.name === 'AbortError') {
                        console.log('Previous request aborted (interrupt)');
                        // Clear thinking state so UI doesn't stay stuck if no
                        // new sendMessage follows (e.g. echo-triggered abort
                        // where the new message was too short/empty).
                        FaceModule.setMood('neutral');
                        StatusModule.update('idle', 'READY');
                        TranscriptPanel.removeThinking();
                        // If agent was mid-task (had heartbeats), note the redirect
                        if (this._wasAgentic) {
                            this._wasAgentic = false;
                            TranscriptPanel.finalizeStreaming('🔀 Redirected.');
                            ActionConsole.addEntry('system', 'Task redirected by user');
                        } else {
                            TranscriptPanel.finalizeStreaming(null);
                        }
                        document.getElementById('thought-bubbles')?.classList.remove('active');
                        window.HaloSmokeFace?.setThinking(false);
                    } else {
                        console.error('Conversation error:', error);
                        // Connection failure auto-retry: if the agent crashed/restarted,
                        // retry the message up to 2 times with backoff instead of dying silently
                        const isNetworkError = (
                            error.message?.includes('fetch') ||
                            error.message?.includes('network') ||
                            error.message?.includes('Failed') ||
                            error.message?.includes('API error: 5') ||  // 5xx server errors
                            error instanceof TypeError  // fetch network failures
                        );
                        const retryCount = opts._retryCount || 0;
                        if (isNetworkError && retryCount < 2 && this._voiceActive) {
                            const delay = retryCount === 0 ? 5000 : 15000;
                            console.warn(`[Conversation] Connection error, retrying in ${delay/1000}s (attempt ${retryCount + 1}/2)...`);
                            this.addSystemMessage('Connection interrupted — reconnecting...');
                            ActionConsole.addEntry('system', `Connection lost — retrying in ${delay/1000}s...`);
                            StatusModule.update('thinking', 'RECONNECTING...');
                            FaceModule.setMood('sad');
                            TranscriptPanel.removeThinking();
                            TranscriptPanel.finalizeStreaming(null);
                            document.getElementById('thought-bubbles')?.classList.remove('active');
                            window.HaloSmokeFace?.setThinking(false);
                            // Clear _sending so the retry can proceed
                            this._sending = false;
                            if (_inactivityTimer) clearTimeout(_inactivityTimer);
                            setTimeout(() => {
                                if (!this._voiceActive) return; // call ended during wait
                                FaceModule.setMood('thinking');
                                StatusModule.update('thinking', 'RECONNECTING...');
                                this.sendMessage(text, { ...opts, _retryCount: retryCount + 1, skipDisplay: true });
                            }, delay);
                            return; // skip finally block's safety net — retry will handle it
                        }
                        const lostMsg = this._wasAgentic
                            ? 'Agent restarted mid-task — your task was interrupted. Try again.'
                            : 'Connection lost. The agent may have restarted. Try talking again in a moment.';
                        this._wasAgentic = false;
                        this.addSystemMessage(lostMsg);
                        ActionConsole.addEntry('error', `Error: ${error.message}`);
                        // Clear thinking state on error
                        FaceModule.setMood('sad');
                        TranscriptPanel.removeThinking();
                        TranscriptPanel.finalizeStreaming('⚠️ Task interrupted — agent restarted.');
                        document.getElementById('thought-bubbles')?.classList.remove('active');
                        window.HaloSmokeFace?.setThinking(false);
                        setTimeout(() => FaceModule.setMood('neutral'), 2000);
                    }
                } finally {
                    if (_inactivityTimer) clearTimeout(_inactivityTimer);
                    this._sending = false;
                    this._fetchAbortController = null;
                    // Safety net: if no audio was queued/played, STT never gets restarted
                    // via onListening callback. Ensure mic comes back after a short delay.
                    // Only fires if call is still active (_voiceActive) — prevents restart after hang-up.
                    setTimeout(() => {
                        if (this._voiceActive && this.stt && !this.isPlaying) {
                            console.log('🎤 Safety net: restarting STT (no audio played)');
                            this._ttsPlaying = false;
                            if (this._ttsGuardTimer) { clearTimeout(this._ttsGuardTimer); this._ttsGuardTimer = null; }
                            if (this.stt.resume) {
                                this.stt.resume();
                            } else {
                                if (this.stt.resetProcessing) this.stt.resetProcessing();
                                if (!this.stt.isListening) this.stt.start();
                            }
                            this.callbacks.onListening();
                        }
                    }, 2000);
                }
            }

            async _sendGreetingTrigger() {
                // Global cooldown: prevent any __session_start__ from firing within 60s of
                // the last one — across ALL modes (ClawdbotMode + VoiceConversation).
                // Without this, a reconnect can cause two simultaneous starts which triggers
                // Z.AI empty_response → double_empty → session crash cascade.
                const now = Date.now();
                if (now - (window._lastSessionStartSentMs || 0) < 60000) {
                    console.warn('[Session] Skipping greeting — global 60s cooldown active');
                    this._greetingSkipped = true;
                    return;
                }
                window._lastSessionStartSentMs = now;
                this._greetingSkipped = false;
                // Send a session start signal to the agent — generates its own greeting
                // based on face recognition, memory (MEMORY.md / clawd/memory/), and GREETINGS.md
                // Face recognition context is automatically included via sendMessage() → identified_person
                // AWAIT so _fetchAbortController is guaranteed set before stt.start() —
                // prevents the safety-net at stt.start() from prematurely unmuting STT.
                await this.sendMessage('__session_start__');
            }

            displayMessage(role, text, streaming = false) {
                const historyDiv = document.getElementById('chat-history');
                if (!historyDiv) return null;

                const messageDiv = document.createElement('div');
                messageDiv.className = `message message-${role}`;
                if (streaming) messageDiv.classList.add('streaming');

                // Create a text span for easy updates during streaming
                const textSpan = document.createElement('span');
                textSpan.className = 'message-text';

                // Escape HTML to prevent XSS, then apply safe markdown formatting
                function escapeHtml(str) {
                    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
                }

                // HTML-escape first, then apply code block and newline formatting
                text = escapeHtml(text);
                if (text.includes('```')) {
                    text = text.replace(/```(\w+)?\n([\s\S]*?)```/g, '<pre style="background:var(--bg-deep);padding:8px;border-radius:4px;overflow-x:auto;"><code>$2</code></pre>');
                }

                // Handle line breaks
                text = text.replace(/\n/g, '<br>');

                textSpan.innerHTML = text;
                messageDiv.appendChild(textSpan);
                historyDiv.appendChild(messageDiv);
                historyDiv.scrollTop = historyDiv.scrollHeight;
                return messageDiv;
            }

            appendTextDelta(delta) {
                const historyDiv = document.getElementById('chat-history');
                if (!historyDiv) return;

                let lastMessage = historyDiv.lastElementChild;

                // If last message isn't an assistant message, create one
                if (!lastMessage || !lastMessage.classList.contains('message-assistant')) {
                    lastMessage = document.createElement('div');
                    lastMessage.className = 'message message-assistant';
                    lastMessage.dataset.streaming = 'true';
                    historyDiv.appendChild(lastMessage);
                }

                // Append the delta text safely (no innerHTML to prevent XSS)
                const lines = delta.split('\n');
                lines.forEach((line, i) => {
                    if (i > 0) lastMessage.appendChild(document.createElement('br'));
                    lastMessage.appendChild(document.createTextNode(line));
                });
                historyDiv.scrollTop = historyDiv.scrollHeight;
            }

            _saveAndShowHtml(html) {
                const titleMatch = html.match(/<title>([^<]*)<\/title>/i) ||
                                   html.match(/<h1[^>]*>([^<]*)<\/h1>/i);
                const title = titleMatch ? titleMatch[1].trim() : 'Canvas Page';
                console.log('Canvas HTML detected, saving to server...');
                fetch(`${CONFIG.serverUrl}/api/canvas/pages`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ html, title }),
                })
                .then(r => r.json())
                .then(result => {
                    if (result.url) {
                        console.log('Canvas page saved:', result.url);
                        const iframe = document.getElementById('canvas-iframe');
                        if (iframe) {
                            iframe.src = result.url;
                            CanvasControl.show();
                            localStorage.setItem('canvas_last_page', result.filename);
                            this.notifyCanvasContext(result.url, title, html.substring(0, 200));
                        }
                        // Reload manifest so new page appears in canvas menu
                        window.CanvasMenu?.loadManifest?.();
                    }
                })
                .catch(err => {
                    console.warn('Failed to save canvas page, using blob fallback:', err);
                    const blob = new Blob([html], { type: 'text/html' });
                    const url = URL.createObjectURL(blob);
                    const iframe = document.getElementById('canvas-iframe');
                    if (iframe) {
                        iframe.src = url;
                        CanvasControl.show();
                        this.notifyCanvasContext('inline-html', title, html.substring(0, 200));
                    }
                });
            }

            handleCanvasCommands(text, processedSet = null) {
                // NOTE: [CANVAS:page-id] and [CANVAS_MENU] tags are already handled
                // by checkCanvasInStream() during streaming. This function only handles
                // legacy JSON/HTML block patterns that aren't checked during streaming.

                // Look for JSON code blocks with canvas actions (present, update, etc.)
                const jsonBlockRegex = /```(?:json)?\s*(\{[\s\S]*?\})\s*```/g;
                let match;
                while ((match = jsonBlockRegex.exec(text)) !== null) {
                    try {
                        const cmd = JSON.parse(match[1]);
                        if (cmd.action === 'present' && cmd.url) {
                            console.log('Canvas command detected: present', cmd.url.substring(0, 80));
                            const iframe = document.getElementById('canvas-iframe');
                            if (iframe) {
                                iframe.src = cmd.url;
                                CanvasControl.show();

                                // Notify server of canvas context so agent knows what's displayed
                                this.notifyCanvasContext(cmd.url, cmd.title);
                            }
                        }
                    } catch (e) {
                        // Not valid JSON or not a canvas command, ignore
                    }
                }

                // Skip HTML canvas if already handled during streaming
                if (processedSet?.has('HTML_CANVAS')) return;

                // Also check for canvas.update API calls in the response
                const htmlRegex = /```html\s*([\s\S]*?)```/g;
                let htmlMatch;
                while ((htmlMatch = htmlRegex.exec(text)) !== null) {
                    const html = htmlMatch[1].trim();
                    if (html.startsWith('<!DOCTYPE') || html.startsWith('<html') || html.startsWith('<h1')) {
                        this._saveAndShowHtml(html);
                        return; // handled
                    }
                }

                // Fallback: catch raw HTML pasted without code fences
                // (agent sometimes outputs HTML directly without ```html``` wrapping)
                const rawHtmlMatch = text.match(/(<!DOCTYPE\s+html[\s\S]*?<\/html>)/i) ||
                                     text.match(/(<html[\s\S]*?<\/html>)/i);
                if (rawHtmlMatch) {
                    this._saveAndShowHtml(rawHtmlMatch[1].trim());
                }
            }

            notifyCanvasContext(page, title, contentSummary = null) {
                // Update global canvas context for voice commands
                window.canvasContext = {
                    current_page: page,
                    current_title: title,
                    page_content: contentSummary,
                    updated_at: new Date().toISOString()
                };

                // Tell server what canvas page is displayed so agent knows context
                fetch(`${CONFIG.serverUrl}/api/canvas/context`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        page: page,
                        title: title || '',
                        content_summary: contentSummary
                    })
                }).catch(e => console.warn('Canvas context notification failed:', e));
            }

            addSystemMessage(text) {
                const historyDiv = document.getElementById('chat-history');
                if (!historyDiv) return;

                const systemDiv = document.createElement('div');
                systemDiv.className = 'message message-system';
                systemDiv.textContent = `[${text}]`;
                historyDiv.appendChild(systemDiv);
                historyDiv.scrollTop = historyDiv.scrollHeight;
            }

            updateConnectionStatus(status) {
                const dot = document.getElementById('clawdbot-status-dot');
                const text = document.getElementById('clawdbot-status-text');

                if (!dot || !text) return;

                dot.className = 'clawdbot-status-dot';

                switch (status) {
                    case 'connected':
                        dot.classList.add('connected');
                        text.textContent = 'Connected';
                        break;
                    case 'connecting':
                        dot.classList.add('connecting');
                        text.textContent = 'Connecting...';
                        break;
                    case 'error':
                    case 'disconnected':
                        text.textContent = status === 'error' ? 'Connection Error' : 'Disconnected';
                        break;
                }
            }

            enableInput(enabled) {
                const input = document.getElementById('clawdbot-text-input');
                const button = document.getElementById('clawdbot-send-btn');

                if (input) input.disabled = !enabled;
                if (button) button.disabled = !enabled;

                if (enabled) {
                    setTimeout(() => input?.focus(), 100);
                }
            }

            playAudio(base64Audio, format = 'wav') {
                try {
                    // Queue audio - if already playing, it will play after current finishes
                    this.audioQueue.push({ base64: base64Audio, format });
                    // Only start playback if nothing is currently playing
                    if (!this.isPlaying) {
                        this.playNextAudio();
                    }
                } catch (error) {
                    console.error('Failed to queue audio:', error);
                }
            }

            async playNextAudio() {
                if (this.audioQueue.length === 0) {
                    // Don't immediately transition to listening — more TTS chunks
                    // may be in-flight from streamed sentences. Wait briefly and
                    // check again so the stop button doesn't flash between sentences.
                    if (!this._drainTimer) {
                        this._drainTimer = setTimeout(() => {
                            this._drainTimer = null;
                            if (this.audioQueue.length > 0) {
                                // New chunk arrived during wait — keep playing
                                this.playNextAudio();
                            } else {
                                // Truly done — transition to listening
                                this.currentAudio = null;
                                this.isPlaying = false;
                                this.callbacks.onListening();
                                WaveformModule.setAmplitude(0);
                                if (window._sleepAfterResponse) {
                                    window._sleepAfterResponse = false;
                                    console.log('[Sleep] Farewell audio done — activating wake word mode');
                                    setTimeout(() => {
                                        ModeManager.clawdbotMode?.stopVoiceInput();
                                        UIModule.setCallButtonState('disconnected');
                                        if (window.ModeSelector?.currentMode !== 'normal') {
                                            window.ModeSelector?.select('normal');
                                        }
                                        if (window.wakeDetector && window.wakeDetector.isSupported()) {
                                            if (!window.wakeDetector.isListening) {
                                                window.wakeDetector.start();
                                            }
                                            const wakeBtn = document.getElementById('wake-button');
                                            if (wakeBtn) wakeBtn.classList.add('listening');
                                            console.log('[Sleep] Wake word detector activated');
                                        }
                                    }, 600);
                                }
                            }
                        }, 800);  // 800ms grace period for next TTS chunk to arrive
                    }
                    return;
                }
                // New chunk available — cancel any pending drain timer
                if (this._drainTimer) {
                    clearTimeout(this._drainTimer);
                    this._drainTimer = null;
                }

                const { base64, format } = this.audioQueue.shift();
                this.isPlaying = true;
                this.callbacks.onSpeaking();

                if (this._audioCtx) {
                    // iOS-safe path: AudioContext was unlocked in startVoiceInput() during
                    // the user gesture, so decoding and playing here is always allowed —
                    // even though this runs asynchronously from the network response.
                    try {
                        if (this._audioCtx.state === 'suspended') {
                            await this._audioCtx.resume();
                        }
                        const mimeType = format === 'mp3' ? 'audio/mpeg' : 'audio/wav';
                        const blob = this.base64ToBlob(base64, mimeType);
                        const arrayBuffer = await blob.arrayBuffer();
                        const audioBuffer = await new Promise((resolve, reject) => {
                            this._audioCtx.decodeAudioData(arrayBuffer, resolve, reject);
                        });

                        // Race condition fix: check if stopAudio was called during decode
                        if (!this.isPlaying) {
                            console.log('[ClawdBot] Audio cancelled during decode, skipping playback');
                            return;
                        }

                        await new Promise((resolve) => {
                            const source = this._audioCtx.createBufferSource();
                            source.buffer = audioBuffer;
                            // Route through analyser for audio-reactive faces
                            source.connect(this._analyser || this._audioCtx.destination);
                            source.onended = resolve;
                            this.currentSource = source;
                            source.start(0);
                        });

                        this.currentSource = null;
                    } catch (err) {
                        console.error('[ClawdBot] AudioContext playback failed:', err);
                        this.currentSource = null;
                    }
                    this.playNextAudio();
                } else {
                    // Fallback: HTMLAudioElement path (non-iOS or AudioContext unavailable)
                    // Race condition fix: check if stopAudio was called
                    if (!this.isPlaying) {
                        console.log('[ClawdBot] Audio cancelled, skipping playback');
                        return;
                    }
                    const mimeType = format === 'mp3' ? 'audio/mpeg' : 'audio/wav';
                    const audioBlob = this.base64ToBlob(base64, mimeType);
                    const audioUrl = URL.createObjectURL(audioBlob);
                    const audio = new Audio(audioUrl);
                    audio.crossOrigin = 'anonymous';

                    // Route HTMLAudioElement through analyser for audio-reactive faces
                    try {
                        if (!this._audioCtx) {
                            this._audioCtx = new (window.AudioContext || window.webkitAudioContext)();
                            this._analyser = this._audioCtx.createAnalyser();
                            this._analyser.fftSize = 2048;
                            this._analyser.smoothingTimeConstant = 0.55;
                            this._analyser.minDecibels = -95;
                            this._analyser.maxDecibels = -12;
                            this._analyser.connect(this._audioCtx.destination);
                            window.audioAnalyser = this._analyser;
                        }
                        if (!this._fallbackSource) {
                            this._fallbackSource = this._audioCtx.createMediaElementSource(audio);
                            this._fallbackSource.connect(this._analyser);
                        }
                    } catch (e) {
                        console.warn('[ClawdBot] Fallback analyser setup failed:', e);
                    }

                    audio.onended = () => {
                        URL.revokeObjectURL(audioUrl);
                        this._fallbackSource = null;
                        this.playNextAudio();
                    };

                    audio.onerror = (e) => {
                        console.error('Audio playback error:', e);
                        URL.revokeObjectURL(audioUrl);
                        this._fallbackSource = null;
                        this.playNextAudio();
                    };

                    this.currentAudio = audio;
                    const playPromise = audio.play();
                    if (playPromise) {
                        playPromise.catch(err => {
                            console.error('Audio play blocked:', err.message);
                            URL.revokeObjectURL(audioUrl);
                            this.playNextAudio();
                        });
                    }
                }
            }

            stopAudio() {
                // Stop AudioContext source if active
                if (this.currentSource) {
                    try { this.currentSource.stop(); } catch (_) {}
                    this.currentSource = null;
                }
                // Stop HTMLAudioElement if active (fallback path)
                if (this.currentAudio) {
                    this.currentAudio.pause();
                    this.currentAudio = null;
                }
                this.audioQueue = [];
                this.isPlaying = false;
            }

            base64ToBlob(base64, mimeType) {
                const byteCharacters = atob(base64);
                const byteNumbers = new Array(byteCharacters.length);
                for (let i = 0; i < byteCharacters.length; i++) {
                    byteNumbers[i] = byteCharacters.charCodeAt(i);
                }
                const byteArray = new Uint8Array(byteNumbers);
                return new Blob([byteArray], { type: mimeType });
            }
        }

        // ===== MODE SWITCHING =====
        // Manages switching between Hume EVI and Clawdbot modes
        window.ModeManager = {
            currentMode: 'supertonic',  // Default mode (Supertonic = STT + TTS, Clawdbot = LLM)
            clawdbotMode: null,
            humeAdapter: null,

            init(sharedSTT) {
                const selector = document.getElementById('voice-mode-select');
                if (selector) {
                    selector.addEventListener('change', (e) => {
                        const profileId = e.target.value;
                        // Map profile IDs to transport modes.
                        // 'hume' / 'hume-evi' → Hume EVI transport.
                        // Everything else (default, developer, etc.) → supertonic/Clawdbot transport.
                        const transportMode = (profileId === 'hume' || profileId === 'hume-evi') ? 'hume' : 'supertonic';
                        this.switchMode(transportMode);
                    });
                }

                // Initialize Clawdbot mode instance with shared STT (but don't connect yet)
                this.clawdbotMode = new ClawdBotMode(CONFIG, sharedSTT);

                // Set up Clawdbot callbacks
                this.clawdbotMode.setCallbacks({
                    onConnect: () => {
                        StatusModule.update('connected', 'CLAWDBOT');
                        FaceModule.setMood('happy');
                        FaceModule.blink();
                        setTimeout(() => FaceModule.setMood('neutral'), 1000);
                    },
                    onDisconnect: () => {
                        StatusModule.update('disconnected', 'OFFLINE');
                        FaceModule.setMood('neutral');
                        WaveformModule.setAmplitude(0);
                    },
                    onSpeaking: () => {
                        StatusModule.update('speaking', 'SPEAKING');
                        FaceModule.setMood('neutral');
                        WaveformModule.setSpeaking(true);  // Start mouth animation
                        MusicModule.duck(true);
                        document.getElementById('stop-button').style.display = '';
                        AgentActivityChip.setSpeaking(true);
                        // Mute mic while agent speaks to prevent echo feedback
                        if (this.clawdbotMode.stt) {
                            console.log('🔇 Muting mic during TTS');
                            if (this.clawdbotMode.stt.mute) {
                                this.clawdbotMode.stt.mute();
                            } else {
                                if (this.clawdbotMode.stt.isListening) this.clawdbotMode.stt.stop();
                                if (this.clawdbotMode.stt.resetProcessing) this.clawdbotMode.stt.resetProcessing();
                            }
                        }
                        this.clawdbotMode._ttsPlaying = true;
                        // Reset guard timer — TTS is now actually playing, give it a fresh 20s window
                        if (this.clawdbotMode._ttsGuardTimer) clearTimeout(this.clawdbotMode._ttsGuardTimer);
                        this.clawdbotMode._ttsGuardTimer = setTimeout(() => {
                            if (this.clawdbotMode._ttsPlaying && this.clawdbotMode._voiceActive) {
                                console.warn('[STT] Hard timeout: _ttsPlaying stuck for 20s — force-clearing');
                                this.clawdbotMode._ttsPlaying = false;
                                if (this.clawdbotMode.stt?.resume) this.clawdbotMode.stt.resume();
                                this.clawdbotMode.callbacks.onListening();
                            }
                        }, 20000);
                    },
                    onListening: () => {
                        StatusModule.update('listening', 'LISTENING');
                        FaceModule.setMood('listening');
                        WaveformModule.setSpeaking(false);  // Stop mouth animation
                        WaveformModule.setAmplitude(0);
                        MusicModule.duck(false);
                        document.getElementById('stop-button').style.display = 'none';
                        AgentActivityChip.setSpeaking(false);
                        // Cancel guard timer — TTS finished normally
                        if (this.clawdbotMode._ttsGuardTimer) { clearTimeout(this.clawdbotMode._ttsGuardTimer); this.clawdbotMode._ttsGuardTimer = null; }
                        // Set echo cooldown: Chrome SpeechRecognition can deliver buffered
                        // results up to 2-3s after TTS ends. Block STT results for 2.5s
                        // to prevent TTS audio from being re-submitted as user speech.
                        this.clawdbotMode._ttsEchoCooldownUntil = Date.now() + 2500;
                        // _ttsPlaying stays true through brief delay to block echo.
                        // 300ms for UI state updates — _ttsEchoCooldownUntil handles
                        // the longer STT delivery lag independently.
                        setTimeout(() => {
                            this.clawdbotMode._ttsPlaying = false;
                            if (this.clawdbotMode._voiceActive && this.clawdbotMode.stt) {
                                // Skip resume if PTT mode is on (mic only opens on hold)
                                if (this.clawdbotMode.stt._micMuted || this.clawdbotMode.stt._pttHolding) return;
                                console.log('🎤 Unmuting mic after TTS');
                                if (this.clawdbotMode.stt.resume) {
                                    this.clawdbotMode.stt.resume();
                                } else {
                                    if (this.clawdbotMode.stt.resetProcessing) this.clawdbotMode.stt.resetProcessing();
                                    if (!this.clawdbotMode.stt.isListening) this.clawdbotMode.stt.start();
                                }
                            }
                        }, 300);
                    },
                    onMessage: (role, text) => {
                        console.log(`Clawdbot ${role}:`, text);
                    },
                    onError: (error) => {
                        UIModule.showError(`Clawdbot error: ${error}`);
                        FaceModule.setMood('sad');
                        setTimeout(() => FaceModule.setMood('neutral'), 2000);
                    }
                });
            },

            async switchMode(mode) {
                if (mode === this.currentMode) return;

                console.log(`Switching from ${this.currentMode} to ${mode}`);

                // Cleanup previous mode
                if (this.currentMode === 'hume') {
                    // Disconnect HumeAdapter if connected
                    if (this.humeAdapter && this.humeAdapter.isConnected) {
                        this.humeAdapter.disconnect();
                    }
                    // Restore original voice agent
                    window.voiceAgent = window.originalVoiceAgent;
                    // Switch provider back to supertonic
                    if (window.providerManager) {
                        window.providerManager.switchProvider('supertonic');
                    }
                    document.querySelectorAll('.controls-left, .controls-right').forEach(el => el.classList.add('hidden-in-clawdbot-mode'));
                } else if (this.currentMode === 'supertonic') {
                    // Disconnect Clawdbot if connected
                    if (this.clawdbotMode) {
                        this.clawdbotMode.disconnect();
                    }
                    // Hide Clawdbot UI
                    document.getElementById('clawdbot-container')?.classList.remove('active');
                }

                this.currentMode = mode;

                // Initialize new mode
                if (mode === 'hume') {
                    // Auto-select Hume TTS provider
                    if (window.providerManager) {
                        window.providerManager.switchProvider('hume');
                    }
                    // Create HumeAdapter if not exists
                    if (!this.humeAdapter) {
                        this.humeAdapter = new HumeAdapter(CONFIG);
                        this.humeAdapter.setCallbacks({
                            onConnect: () => {
                                StatusModule.update('connected', 'HUME EVI');
                                UIModule.setCallButtonState('connected');
                                FaceModule.setMood('happy');
                                FaceModule.blink();
                                setTimeout(() => FaceModule.setMood('neutral'), 1000);
                            },
                            onDisconnect: () => {
                                StatusModule.update('disconnected', 'OFFLINE');
                                UIModule.setCallButtonState('disconnected');
                                FaceModule.setMood('neutral');
                                WaveformModule.setAmplitude(0);
                            },
                            onSpeaking: () => {
                                StatusModule.update('speaking', 'SPEAKING');
                                FaceModule.setMood('neutral');
                                WaveformModule.setSpeaking(true);  // Start mouth animation
                                MusicModule.duck(true);
                            },
                            onListening: () => {
                                StatusModule.update('listening', 'LISTENING');
                                FaceModule.setMood('listening');
                                WaveformModule.setSpeaking(false);  // Stop mouth animation
                                WaveformModule.setAmplitude(0);
                                MusicModule.duck(false);
                            },
                            onTranscript: (text, isUser) => {
                                console.log(`${isUser ? 'User' : 'AI'}: ${text}`);
                                if (!isUser) {
                                    DJSoundboard.checkTriggers(text);
                                }
                            },
                            onError: (error) => {
                                UIModule.showError(error);
                                FaceModule.setMood('sad');
                                setTimeout(() => FaceModule.setMood('neutral'), 2000);
                            }
                        });
                    }
                    // Set HumeAdapter as the active voice agent
                    window.voiceAgent = this.humeAdapter;
                    // Show Hume UI
                    document.querySelectorAll('.controls-left, .controls-right').forEach(el => el.classList.remove('hidden-in-clawdbot-mode'));
                    StatusModule.update('disconnected', 'HUME EVI');
                } else if (mode === 'supertonic') {
                    // Supertonic mode: STT + Clawdbot LLM, TTS = whatever user chose
                    // Restore saved TTS provider (don't hardcode supertonic — user may have chosen Groq)
                    if (window.providerManager) {
                        const savedProvider = window.providerManager.selectedProvider || 'groq';
                        // Provider already set from server profile — just ensure UI is in sync
                        window.providerManager.switchProvider(savedProvider);
                    }
                    // Show Clawdbot UI and connect
                    document.getElementById('clawdbot-container')?.classList.add('active');
                    document.querySelectorAll('.controls-left, .controls-right').forEach(el => el.classList.add('hidden-in-clawdbot-mode'));
                    this.clawdbotMode.connect();
                }

                // Save to localStorage + server profile
                localStorage.setItem('voice_mode', mode);
                const pid = window.providerManager?._activeProfileId || 'default';
                fetch('/api/profiles/' + pid, {
                    method: 'PUT', headers: {'Content-Type':'application/json'},
                    body: JSON.stringify({ ui: { voice_mode: mode } })
                }).catch(() => {});
            },

            getCurrentMode() {
                return this.currentMode;
            },

            toggleVoice() {
                if (this.currentMode === 'hume') {
                    // Hume mode: use HumeAdapter
                    window.voiceAgent?.toggle();
                } else if (this.currentMode === 'supertonic') {
                    // Supertonic mode: toggle voice input
                    if (this.clawdbotMode && this.clawdbotMode.stt) {
                        if (this.clawdbotMode.stt.isListening) {
                            this.clawdbotMode.stopVoiceInput();
                            UIModule.setCallButtonState('disconnected');
                        } else {
                            this.clawdbotMode.startVoiceInput();
                            UIModule.setCallButtonState('connected');
                        }
                    }
                }
            },

            stopAll() {
                // MUTE: Stop TTS audio playback only — do NOT abort the agent's
                // work, kill the call, or disconnect. The agent keeps working
                // silently. The user can still speak new requests.
                console.log('STOP AUDIO - muting TTS (agent keeps working)');
                // Stop TTS audio on both voice modes
                if (window._voiceConversation) {
                    window._voiceConversation.stopAudio?.();
                }
                if (this.clawdbotMode) {
                    this.clawdbotMode.stopAudio();
                    this.clawdbotMode._ttsPlaying = false;
                }
                // Un-duck music if it was ducked for TTS
                MusicModule.duck(false);
                // Hide the stop button since audio is stopped
                document.getElementById('stop-button').style.display = 'none';
                // Face back to neutral (not speaking anymore)
                FaceModule.setMood('neutral');
                WaveformModule.setAmplitude(0);
            }
        };

        // ===== CLAWDBOT MODE STYLING =====
        // Controls remain fully functional in Clawdbot mode
        const style = document.createElement('style');
        style.textContent = `
            .controls-left.hidden-in-clawdbot-mode,
            .controls-right.hidden-in-clawdbot-mode {
                /* Controls remain clickable in Clawdbot mode */
                opacity: 1;
            }
        `;
        document.head.appendChild(style);

        // ===== VOICE CONVERSATION =====
        // Orchestrates STT -> Conversation API -> TTS flow
        class VoiceConversation {
            constructor(config) {
                this.config = config;
                this.isConnected = false;
                this.isProcessing = false;
                // Select STT provider from server profile (default: webspeech)
                const sttProvider = window._serverProfile?.stt?.provider || 'webspeech';
                if (sttProvider === 'deepgram-streaming' || sttProvider === 'deepgram') {
                    this.stt = new DeepgramStreamingSTT();
                    console.log('STT provider: Deepgram Streaming (WebSocket)');
                } else if (sttProvider === 'deepgram-batch') {
                    this.stt = new DeepgramSTT();
                    console.log('STT provider: Deepgram Nova-2 (batch)');
                } else if (sttProvider === 'groq') {
                    this.stt = new GroqSTT();
                    console.log('STT provider: Groq Whisper');
                } else {
                    this.stt = new WebSpeechSTT();
                    console.log('STT provider: Chrome Web Speech');
                }
                this.ttsProvider = 'groq';
                this.ttsVoice = 'F3';
                this.audioContext = null;
                this.analyser = null;
                this.analyserData = null;
                this.analyserAnimationId = null;
                this.restartWakeAfter = false;  // Flag to resume wake detector after conversation
                this.sessionId = null;
                this._fetchAbortController = null;
                this.currentSource = null;

                this.callbacks = {
                    onConnect: () => {},
                    onDisconnect: () => {},
                    onSpeaking: () => {},
                    onListening: () => {},
                    onTranscript: (text, isUser) => {},
                    onError: (error) => {}
                };
            }

            setCallbacks(callbacks) {
                this.callbacks = { ...this.callbacks, ...callbacks };
            }

            setTTSProvider(provider, voice) {
                this.ttsProvider = provider;
                if (voice) this.ttsVoice = voice;
                console.log('TTS Provider set to:', provider, voice);
            }

            async connect() {
                if (this.isConnected || this.isProcessing) return;
                this.isProcessing = true;

                // Generate new session ID for this call
                this.sessionId = `session_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
                window._activeSessionId = this.sessionId;
                console.log('New conversation session:', this.sessionId);

                // Pause wake word detector during conversation
                if (window.wakeDetector && window.wakeDetector.isListening) {
                    window.wakeDetector.stop();
                    this.restartWakeAfter = true;
                }

                if (!this.stt.isSupported()) {
                    this.callbacks.onError('Speech recognition not supported in this browser. Try Chrome!');
                    this.isProcessing = false;
                    return;
                }

                // Initialize audio context for TTS playback
                if (!this.audioContext) {
                    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    this.analyser = this.audioContext.createAnalyser();
                    this.analyser.fftSize = 2048;
                    this.analyser.smoothingTimeConstant = 0.55;
                    this.analyser.minDecibels = -95;
                    this.analyser.maxDecibels = -12;
                    this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
                    this.analyser.connect(this.audioContext.destination);
                    // Expose for audio-reactive face modules (e.g. HaloSmokeFace)
                    window.audioAnalyser = this.analyser;
                }

                // Set up STT callbacks
                this.stt.onResult = (transcript) => this.handleUserTranscript(transcript);
                this.stt.onError = (error) => {
                    console.error('STT error:', error);
                    if (error === 'not-allowed') {
                        this.callbacks.onError('Microphone access denied');
                        this.disconnect();
                    } else if (error === 'no-device') {
                        this.callbacks.onError('No microphone found');
                        this.disconnect();
                    } else if (error === 'network' || error === 'service-not-allowed') {
                        this.callbacks.onError('Speech recognition unavailable');
                        this.disconnect();
                    }
                };

                // Start listening — but MUTED until greeting finishes.
                // STT must not capture speech before the agent greets, otherwise
                // pre-greeting speech gets sent to the LLM and confuses the session.
                if (await this.stt.start()) {
                    this.stt.pause?.();  // Mute immediately — greeting will resume
                    this.isConnected = true;
                    this.isProcessing = false;
                    this.callbacks.onConnect();

                    // Auto-greet — await so STT stays muted until greeting TTS finishes.
                    // _sendSessionGreeting resumes STT + fires onListening in its finally block.
                    await this._sendSessionGreeting();
                } else {
                    this.isProcessing = false;
                    this.callbacks.onError('Failed to start speech recognition');
                }
            }

            async _sendSessionGreeting() {
                // Global cooldown: same 60s guard as ClawdbotMode._sendGreetingTrigger()
                // prevents double __session_start__ when both modes fire on reconnect.
                const now = Date.now();
                if (now - (window._lastSessionStartSentMs || 0) < 60000) {
                    console.warn('[VoiceConversation] Skipping greeting — global 60s cooldown active');
                    return;
                }
                window._lastSessionStartSentMs = now;
                // Pause STT so it doesn't pick up the agent's own greeting audio
                this.stt.pause?.();

                try {
                    FaceModule.setMood('thinking');
                    StatusModule.update('thinking', 'THINKING');

                    const uiContext = ModeManager.clawdbotMode?.getUIContext?.() || {};
                    const gatewayAgentId = localStorage.getItem('gateway_agent_id') || null;
                    const body = {
                        message: '__session_start__',
                        tts_provider: this.ttsProvider,
                        voice: this.ttsVoice,
                        session_id: this.sessionId,
                        ui_context: uiContext,
                        identified_person: window.cameraModule?.currentIdentity || null,
                        ...(gatewayAgentId ? { agent_id: gatewayAgentId } : {}),
                    };

                    const resp = await fetch(`${this.config.serverUrl}/api/conversation`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });

                    if (!resp.ok) throw new Error('Greeting request failed');
                    const data = await resp.json();

                    FaceModule.setMood('neutral');
                    TranscriptPanel.removeThinking();
                    StatusModule.update('listening', 'LISTENING');

                    if (data.response) {
                        await this._processCommandTags(data.response);
                        const displayText = data.response
                            .replace(/\[CANVAS_MENU\]/gi, '')
                            .replace(/\[CANVAS:[^\]]*\]/gi, '')
                            .replace(/\[CANVAS_URL:[^\]]*\]/gi, '')
                            .replace(/\[MUSIC_PLAY(?::[^\]]*)?\]/gi, '')
                            .replace(/\[MUSIC_STOP\]/gi, '')
                            .replace(/\[MUSIC_NEXT\]/gi, '')
                            .replace(/\[SLEEP\]/gi, '')
                            .trim();
                        this.callbacks.onTranscript(displayText, false);
                        TranscriptPanel.addMessage('assistant', displayText);
                    }
                    if (data.audio) {
                        await this.playTTS(data.audio);
                    }
                } catch (err) {
                    console.warn('Session greeting failed:', err);
                    FaceModule.setMood('neutral');
                    StatusModule.update('listening', 'LISTENING');
                } finally {
                    this.stt.resume?.();
                    this.callbacks.onListening();
                }
            }

            disconnect() {
                this.stt.stop();
                this.stopAnalyserAnimation();
                this.isConnected = false;
                this.isProcessing = false;
                this.callbacks.onDisconnect();

                // Resume wake word detector after conversation
                if (this.restartWakeAfter && window.wakeDetector && window.wakeDetector.isSupported()) {
                    window.wakeDetector.start();
                    this.restartWakeAfter = false;
                }
            }

            async toggle() {
                if (this.isConnected) {
                    this.disconnect();
                } else {
                    await this.connect();
                }
            }

            async handleUserTranscript(transcript) {
                if (!transcript || !transcript.trim()) {
                    this.stt.resetProcessing();
                    return;
                }

                // ── Interrupt vs Steer (same logic as ClawdbotMode.sendMessage) ──
                // Check ClawdbotMode's _ttsPlaying to decide abort vs steer.
                if (this._fetchAbortController) {
                    const cm = ModeManager?.clawdbotMode;
                    if (cm?._ttsPlaying) {
                        // TTS playing → ABORT (stop spoken output)
                        this._fetchAbortController.abort();
                        this._fetchAbortController = null;
                        console.warn(`⛔ ABORT source: VoiceConversation.handleUserTranscript (TTS playing)`);
                        fetch(`${this.config.serverUrl}/api/conversation/abort`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ source: 'voice-handleUserTranscript', text: transcript.substring(0, 50) }),
                        }).catch(() => {});
                        this.stopAudio();
                    } else {
                        // Agent working silently → STEER
                        console.log(`🔀 STEER: injecting "${transcript.substring(0,50)}" into active run`);
                        ActionConsole.addEntry('system', `Steering: "${transcript.substring(0, 60)}"`);
                        fetch(`${this.config.serverUrl}/api/conversation/steer`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ message: transcript, source: 'voice-handleUserTranscript' }),
                        }).catch(() => {});
                        // Show user text, reset STT for next input, and return.
                        // The existing streaming response will receive the steered output.
                        this.callbacks.onTranscript(transcript, true);
                        TranscriptPanel.addMessage('user', transcript);
                        this.stt.resetProcessing();
                        return;
                    }
                } else {
                    this.stopAudio();
                }

                console.log('User said:', transcript);
                this.callbacks.onTranscript(transcript, true);
                TranscriptPanel.addMessage('user', transcript);

                // Show thinking state while waiting for response
                FaceModule.setMood('thinking');
                StatusModule.update('thinking', 'THINKING');
                TranscriptPanel.showThinking();
                document.getElementById('thought-bubbles')?.classList.add('active');
                window.HaloSmokeFace?.setThinking(true);

                // NOTE: STT stays running - isProcessing flag blocks new transcripts

                try {
                    // Gather UI context so the LLM knows about canvas pages, music state, etc.
                    const uiContext = ModeManager.clawdbotMode?.getUIContext?.() || {};
                    const gatewayAgentId = localStorage.getItem('gateway_agent_id') || null;

                    // Get AI response from backend
                    this._fetchAbortController = new AbortController();
                    const response = await fetch(`${this.config.serverUrl}/api/conversation`, {
                        method: 'POST',
                        signal: this._fetchAbortController.signal,
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            message: transcript,
                            tts_provider: this.ttsProvider,
                            voice: this.ttsVoice,
                            session_id: this.sessionId,
                            ui_context: uiContext,
                            identified_person: window.cameraModule?.currentIdentity || null,
                            ...(gatewayAgentId ? { agent_id: gatewayAgentId } : {}),
                            ...(window._maxResponseChars ? { max_response_chars: window._maxResponseChars } : {})
                        })
                    });

                    if (!response.ok) {
                        throw new Error('Conversation API failed');
                    }

                    const data = await response.json();

                    if (data.response) {
                        // Clear thinking state
                        FaceModule.setMood('neutral');
                        TranscriptPanel.removeThinking();
                        document.getElementById('thought-bubbles')?.classList.remove('active');
                        window.HaloSmokeFace?.setThinking(false);

                        console.log('AI responded:', data.response);
                        const rawResponse = data.response;

                        // Process command tags from response
                        await this._processCommandTags(rawResponse);

                        // Strip tags from display text
                        const displayText = rawResponse
                            .replace(/```html[\s\S]*?```/gi, '')
                            .replace(/```[\s\S]*?```/g, '')
                            .replace(/\[CANVAS_MENU\]/gi, '')
                            .replace(/\[CANVAS:[^\]]*\]/gi, '')
                            .replace(/\[CANVAS_URL:[^\]]*\]/gi, '')
                            .replace(/\[MUSIC_PLAY(?::[^\]]*)?\]/gi, '')
                            .replace(/\[MUSIC_STOP\]/gi, '')
                            .replace(/\[MUSIC_NEXT\]/gi, '')
                            .replace(/\[SESSION_RESET\]/gi, '')
                            .replace(/\[SUNO_GENERATE:[^\]]*\]/gi, '')
                            .replace(/\[SPOTIFY:[^\]]*\]/gi, '')
                            .replace(/\[REGISTER_FACE:[^\]]*\]/gi, '')
                            .replace(/\[SLEEP\]/gi, '')
                            .trim();

                        this.callbacks.onTranscript(displayText, false);
                        TranscriptPanel.addMessage('assistant', displayText);

                        // Play TTS if audio provided
                        if (data.audio) {
                            await this.playTTS(data.audio);
                        }
                    }

                } catch (error) {
                    if (error.name === 'AbortError') {
                        console.log('Previous request aborted (interrupt)');
                    } else {
                        console.error('Conversation error:', error);
                        this.callbacks.onError(`Failed to get response: ${error.message}`);
                        // Clear thinking on error
                        FaceModule.setMood('sad');
                        setTimeout(() => FaceModule.setMood('neutral'), 2000);
                    }
                } finally {
                    this._fetchAbortController = null;
                    // Reset processing flag to allow new transcripts
                    TranscriptPanel.removeThinking();
                    document.getElementById('thought-bubbles')?.classList.remove('active');
                    window.HaloSmokeFace?.setThinking(false);
                    this.stt.resetProcessing();
                    this.callbacks.onListening();
                }
            }

            async _processCommandTags(text) {
                // [CANVAS_MENU]
                if (/\[CANVAS_MENU\]/i.test(text)) {
                    console.log('[Canvas] CANVAS_MENU trigger detected');
                    CanvasControl.showMenu?.() || document.getElementById('canvas-menu-button')?.click();
                }
                // [CANVAS:pagename]
                const canvasMatch = text.match(/\[CANVAS:([^\]]+)\]/i);
                if (canvasMatch) {
                    const pageName = canvasMatch[1].trim();
                    console.log('[Canvas] CANVAS page trigger:', pageName);
                    ActionConsole.addEntry('system', `Canvas: opening ${pageName}`);
                    try {
                        await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/sync`, { method: 'POST' });
                        await window.CanvasMenu?.loadManifest();
                    } catch (e) { console.warn('[Canvas] manifest sync failed:', e); }
                    CanvasControl.showPage?.(pageName);
                }
                // [CANVAS_URL:https://example.com]
                const canvasUrlMatch = text.match(/\[CANVAS_URL:([^\]]+)\]/i);
                if (canvasUrlMatch) {
                    const externalUrl = canvasUrlMatch[1].trim();
                    const resolvedUrl = resolveCanvasUrl(externalUrl);
                    if (resolvedUrl) {
                        console.log('[Canvas] External URL trigger:', externalUrl, resolvedUrl !== externalUrl ? `→ ${resolvedUrl}` : '');
                        ActionConsole.addEntry('system', `Canvas: loading ${resolvedUrl}`);
                        const iframe = document.getElementById('canvas-iframe');
                        if (iframe) { iframe.src = resolvedUrl; CanvasControl.show(); }
                    } else {
                        console.warn('[Canvas] Blocked private/internal URL from agent:', externalUrl);
                        ActionConsole.addEntry('system', `Canvas: blocked private URL — agent should use the public dev URL`);
                    }
                }
                // [MUSIC_PLAY] or [MUSIC_PLAY:track]
                const musicPlay = text.match(/\[MUSIC_PLAY(?::([^\]]+))?\]/i);
                if (musicPlay) {
                    const trackName = musicPlay[1]?.trim();
                    // Always open the panel regardless of whether tracks exist
                    if (window.musicPlayer?.panelState === 'closed') window.musicPlayer.openPanel();
                    if (trackName) {
                        window.musicPlayer?.play(trackName);
                    } else {
                        window.musicPlayer?.play();
                    }
                }
                // [MUSIC_STOP]
                if (/\[MUSIC_STOP\]/i.test(text)) {
                    window.musicPlayer?.stop();
                }
                // [MUSIC_NEXT]
                if (/\[MUSIC_NEXT\]/i.test(text)) {
                    window.musicPlayer?.next();
                }
                // [SUNO_GENERATE:prompt] — may appear multiple times
                [...text.matchAll(/\[SUNO_GENERATE:([^\]]+)\]/gi)].forEach(m => {
                    window.sunoModule?.generate(m[1].trim());
                });
                // [SPOTIFY:track|artist]
                const spotifyMatch = text.match(/\[SPOTIFY:([^|\]]+)(?:\|([^\]]+))?\]/i);
                if (spotifyMatch) {
                    window.musicPlayer?.playSpotify(spotifyMatch[1].trim(), spotifyMatch[2]?.trim() || '');
                }
                // [REGISTER_FACE:name]
                const registerFaceMatch = text.match(/\[REGISTER_FACE:([^\]]+)\]/i);
                if (registerFaceMatch) {
                    const personName = registerFaceMatch[1].trim();
                    const cam = window.cameraModule;
                    if (cam && cam.stream) {
                        const ctx = cam.canvas.getContext('2d');
                        cam.canvas.width = 640; cam.canvas.height = 480;
                        ctx.drawImage(cam.video, 0, 0, 640, 480);
                        const imageData = cam.canvas.toDataURL('image/jpeg', 0.8);
                        fetch(`${CONFIG.serverUrl}/api/faces/${encodeURIComponent(personName)}`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ image: imageData })
                        }).then(() => {
                            console.log(`[FaceReg] Registered face: ${personName}`);
                            window.FacePanel?.loadFaces();
                            window.FaceID?.loadKnownFaces();
                        }).catch(e => console.error('[FaceReg] Error:', e));
                    }
                }
                // [SLEEP]
                if (/\[SLEEP\]/i.test(text)) {
                    console.log('[Sleep] Agent requested sleep — will disconnect after audio');
                    window._sleepAfterResponse = true;
                }
                // HTML canvas page
                const htmlMatch =
                    text.match(/```html\s*(<!DOCTYPE\s+html[\s\S]*?<\/html>)/i) ||
                    text.match(/```html\s*(<html[\s\S]*?<\/html>)/i);
                if (htmlMatch && ModeManager.clawdbotMode?._saveAndShowHtml) {
                    console.log('[Canvas] Complete HTML detected, saving...');
                    ModeManager.clawdbotMode._saveAndShowHtml(htmlMatch[1].trim());
                }
            }

            async playTTS(audioBase64) {
                // Stop any currently playing audio — only one TTS plays at a time
                this.stopAudio();
                this.callbacks.onSpeaking();

                try {
                    const audioData = this.base64ToArrayBuffer(audioBase64);
                    const audioBuffer = await this.audioContext.decodeAudioData(audioData);

                    // Await audio completion so callers' finally blocks don't fire
                    // onListening() while TTS is still playing (which was restarting
                    // the mic mid-speech and causing echo).
                    await new Promise((resolve) => {
                        const source = this.audioContext.createBufferSource();
                        source.buffer = audioBuffer;
                        source.connect(this.analyser);
                        source.onended = () => {
                            this.stopAnalyserAnimation();
                            WaveformModule.setAmplitude(0);
                            resolve();
                        };
                        this.currentSource = source;
                        source.start(0);
                        this.startAnalyserAnimation();
                    });
                    this.currentSource = null;

                } catch (error) {
                    console.error('TTS playback error:', error);
                    // onListening is called by the caller's finally block
                }
            }

            stopAudio() {
                if (this.currentSource) {
                    try { this.currentSource.stop(); } catch (_) {}
                    this.currentSource = null;
                }
            }

            startAnalyserAnimation() {
                if (this.analyserAnimationId) return;

                const updateAmplitude = () => {
                    if (!this.analyser) {
                        this.analyserAnimationId = null;
                        return;
                    }

                    this.analyser.getByteFrequencyData(this.analyserData);

                    let sum = 0;
                    const voiceRange = Math.floor(this.analyserData.length * 0.6);
                    for (let i = 0; i < voiceRange; i++) {
                        sum += this.analyserData[i];
                    }
                    const average = sum / voiceRange;
                    const normalizedAmplitude = average / 255;

                    // Boost amplitude significantly for visible mouth animation
                    // Minimum 0.3 when any audio detected, max 1.0
                    const boostedAmplitude = normalizedAmplitude > 0.05
                        ? Math.max(0.3, normalizedAmplitude * 2.5)
                        : 0;
                    WaveformModule.setAmplitude(Math.min(1, boostedAmplitude));

                    this.analyserAnimationId = requestAnimationFrame(updateAmplitude);
                };

                updateAmplitude();
            }

            stopAnalyserAnimation() {
                if (this.analyserAnimationId) {
                    cancelAnimationFrame(this.analyserAnimationId);
                    this.analyserAnimationId = null;
                }
            }

            base64ToArrayBuffer(base64) {
                const binary = atob(base64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                return bytes.buffer;
            }
        }

        // ===== VOICE ADAPTER INTERFACE =====
        // This is the interface that different AI providers implement
        class VoiceAdapter {
            constructor(config) {
                this.config = config;
                this.isConnected = false;
                this.isConnecting = false;
                this.callbacks = {
                    onConnect: () => {},
                    onDisconnect: () => {},
                    onSpeaking: () => {},
                    onListening: () => {},
                    onTranscript: (text, isUser) => {},
                    onError: (error) => {}
                };
            }

            setCallbacks(callbacks) {
                this.callbacks = { ...this.callbacks, ...callbacks };
            }

            async connect() {
                throw new Error('connect() must be implemented by adapter');
            }

            disconnect() {
                throw new Error('disconnect() must be implemented by adapter');
            }

            async toggle() {
                if (this.isConnected) {
                    this.disconnect();
                } else {
                    await this.connect();
                }
            }
        }

        // ===== HUME ADAPTER =====
        class HumeAdapter extends VoiceAdapter {
            constructor(config) {
                super(config);
                this.socket = null;
                this.mediaRecorder = null;
                this.audioContext = null;
                this.analyser = null;
                this.analyserData = null;
                this.audioQueue = [];
                this.isPlayingAudio = false;
                this.analyserAnimationId = null;
                // DJ clip tracking - prevent overlapping clips
                this.clipPlaying = false;
                this.currentClipAudio = null;
                // Wake detector state (like VoiceConversation)
                this.restartWakeAfter = false;
            }

            async getAccessToken() {
                const response = await fetch(`${this.config.serverUrl}/api/hume/token`);
                if (!response.ok) throw new Error('Failed to get Hume access token');
                const data = await response.json();
                if (data.config_id) {
                    this.config.hume = this.config.hume || {};
                    this.config.hume.configId = data.config_id;
                }
                return data.access_token;
            }

            async connect() {
                if (this.isConnecting || this.isConnected) return;
                this.isConnecting = true;

                // Pause wake word detector during Hume conversation
                if (window.wakeDetector && window.wakeDetector.isListening) {
                    window.wakeDetector.stop();
                    this.restartWakeAfter = true;
                }

                try {
                    const accessToken = await this.getAccessToken();
                    const wsUrl = `wss://api.hume.ai/v0/evi/chat?config_id=${this.config.hume.configId}&access_token=${accessToken}`;

                    this.socket = new WebSocket(wsUrl);

                    this.socket.onopen = async () => {
                        this.isConnected = true;
                        this.isConnecting = false;
                        this.callbacks.onConnect();
                        await this.startAudioCapture();
                    };

                    this.socket.onmessage = (event) => {
                        const message = JSON.parse(event.data);
                        this.handleMessage(message);
                    };

                    this.socket.onerror = (error) => {
                        console.error('Hume WebSocket error:', error);
                        this.callbacks.onError('Connection error');
                    };

                    this.socket.onclose = () => {
                        this.isConnected = false;
                        this.isConnecting = false;
                        this.stopAudioCapture();
                        this.callbacks.onDisconnect();
                        // Resume wake word detector after unexpected disconnect
                        if (this.restartWakeAfter && window.wakeDetector && window.wakeDetector.isSupported()) {
                            window.wakeDetector.start();
                            this.restartWakeAfter = false;
                        }
                    };
                } catch (error) {
                    this.isConnecting = false;
                    this.callbacks.onError(error.message);
                    throw error;
                }
            }

            disconnect() {
                if (this.socket) {
                    this.socket.close();
                    this.socket = null;
                }
                this.stopAudioCapture();
                this.isConnected = false;

                // Resume wake word detector after Hume conversation
                if (this.restartWakeAfter && window.wakeDetector && window.wakeDetector.isSupported()) {
                    window.wakeDetector.start();
                    this.restartWakeAfter = false;
                }
            }

            handleMessage(message) {
                switch (message.type) {
                    case 'user_message':
                        if (message.message?.content) {
                            this.callbacks.onTranscript(message.message.content, true);
                        }
                        this.callbacks.onListening();
                        break;

                    case 'assistant_message':
                        if (message.message?.content) {
                            this.callbacks.onTranscript(message.message.content, false);
                        }
                        break;

                    case 'audio_output':
                        if (message.data) {
                            this.callbacks.onSpeaking();
                            this.queueAudio(message.data);
                        }
                        break;

                    case 'assistant_end':
                        this.callbacks.onListening();
                        break;

                    case 'user_interruption':
                        this.audioQueue = [];
                        this.isPlayingAudio = false;
                        break;

                    case 'tool_call':
                        // Handle tool calls from the AI
                        this.handleToolCall(message);
                        break;

                    case 'error':
                        this.callbacks.onError(message.message || 'Unknown error');
                        break;
                }
            }

            async startAudioCapture() {
                try {
                    const stream = await navigator.mediaDevices.getUserMedia({
                        audio: { echoCancellation: true, noiseSuppression: true, sampleRate: 16000 }
                    });

                    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                        ? 'audio/webm;codecs=opus' : 'audio/webm';

                    this.mediaRecorder = new MediaRecorder(stream, { mimeType });

                    this.mediaRecorder.ondataavailable = async (event) => {
                        if (event.data.size > 0 && this.socket?.readyState === WebSocket.OPEN) {
                            const arrayBuffer = await event.data.arrayBuffer();
                            const base64 = this.arrayBufferToBase64(arrayBuffer);
                            this.socket.send(JSON.stringify({ type: 'audio_input', data: base64 }));
                        }
                    };

                    this.mediaRecorder.start(100);
                } catch (error) {
                    console.error('Audio capture error:', error);
                    this.callbacks.onError('Microphone access denied');
                }
            }

            stopAudioCapture() {
                if (this.mediaRecorder && this.mediaRecorder.state !== 'inactive') {
                    this.mediaRecorder.stop();
                }
            }

            queueAudio(base64Data) {
                this.audioQueue.push(base64Data);
                if (!this.isPlayingAudio) this.playNextAudio();
            }

            async playNextAudio() {
                if (this.audioQueue.length === 0) {
                    this.isPlayingAudio = false;
                    // Stop analyser animation and reset waveform
                    this.stopAnalyserAnimation();
                    WaveformModule.setAmplitude(0);
                    // Unduck music when done speaking
                    this.callbacks.onListening();
                    return;
                }

                this.isPlayingAudio = true;
                // Duck music when starting to speak
                this.callbacks.onSpeaking();
                const base64Data = this.audioQueue.shift();

                try {
                    if (!this.audioContext) {
                        this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
                    }

                    // Create analyser if not exists
                    if (!this.analyser) {
                        this.analyser = this.audioContext.createAnalyser();
                        this.analyser.fftSize = 2048;
                        this.analyser.smoothingTimeConstant = 0.55;
                        this.analyser.minDecibels = -95;
                        this.analyser.maxDecibels = -12;
                        this.analyserData = new Uint8Array(this.analyser.frequencyBinCount);
                        this.analyser.connect(this.audioContext.destination);
                        // Expose for audio-reactive face modules (e.g. HaloSmokeFace)
                        window.audioAnalyser = this.analyser;
                    }

                    const audioData = this.base64ToArrayBuffer(base64Data);
                    const audioBuffer = await this.audioContext.decodeAudioData(audioData);

                    const source = this.audioContext.createBufferSource();
                    source.buffer = audioBuffer;
                    // Connect through analyser instead of directly to destination
                    source.connect(this.analyser);
                    source.onended = () => this.playNextAudio();
                    source.start(0);

                    // Start real-time amplitude analysis
                    this.startAnalyserAnimation();
                } catch (error) {
                    console.error('Audio playback error:', error);
                    this.playNextAudio();
                }
            }

            startAnalyserAnimation() {
                if (this.analyserAnimationId) return; // Already running

                const updateAmplitude = () => {
                    if (!this.analyser || !this.isPlayingAudio) {
                        this.analyserAnimationId = null;
                        return;
                    }

                    // Get frequency data
                    this.analyser.getByteFrequencyData(this.analyserData);

                    // Calculate average amplitude (focus on voice frequencies)
                    let sum = 0;
                    const voiceRange = Math.floor(this.analyserData.length * 0.6); // Lower 60% = voice
                    for (let i = 0; i < voiceRange; i++) {
                        sum += this.analyserData[i];
                    }
                    const average = sum / voiceRange;
                    const normalizedAmplitude = average / 255;

                    // Boost amplitude significantly for visible mouth animation
                    // Minimum 0.3 when any audio detected, max 1.0
                    const boostedAmplitude = normalizedAmplitude > 0.05
                        ? Math.max(0.3, normalizedAmplitude * 2.5)
                        : 0;
                    WaveformModule.setAmplitude(Math.min(1, boostedAmplitude));

                    this.analyserAnimationId = requestAnimationFrame(updateAmplitude);
                };

                updateAmplitude();
            }

            stopAnalyserAnimation() {
                if (this.analyserAnimationId) {
                    cancelAnimationFrame(this.analyserAnimationId);
                    this.analyserAnimationId = null;
                }
            }

            arrayBufferToBase64(buffer) {
                const bytes = new Uint8Array(buffer);
                let binary = '';
                for (let i = 0; i < bytes.byteLength; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                return btoa(binary);
            }

            base64ToArrayBuffer(base64) {
                const binary = atob(base64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) {
                    bytes[i] = binary.charCodeAt(i);
                }
                return bytes.buffer;
            }

            async handleToolCall(message) {
                const toolName = message.name;
                const toolCallId = message.tool_call_id;
                const parameters = JSON.parse(message.parameters || '{}');

                console.log('Tool call:', toolName, parameters);

                let result = { success: false, error: 'Unknown tool' };

                try {
                    // Map song tool names to track files (populated by music library)
                    const songTools = {};

                    // Map sound tool names to sound files
                    const soundTools = {
                        'play_airhorn': 'air_horn',
                        'play_scratch': 'scratch_long',
                        'play_rewind': 'rewind',
                        'play_crowd_cheer': 'crowd_cheer',
                        'play_yeah': 'yeah',
                        'play_lets_go': 'lets_go',
                        'play_sad_trombone': 'sad_trombone'
                    };

                    // DJ Clips - pre-recorded segments (add custom clips to sounds/DJ-clips/)
                    const djClipTools = {};

                    if (songTools[toolName]) {
                        // Individual song tool - play that specific track
                        result = await this.handleSongTool(toolName, songTools[toolName]);
                    } else if (djClipTools[toolName]) {
                        // DJ Clip - pre-recorded segment, duck music and play clip
                        result = await this.handleDJClipTool(toolName, djClipTools[toolName]);
                    } else if (soundTools[toolName]) {
                        // Individual sound tool - play that sound effect
                        DJSoundboard.play(soundTools[toolName]);
                        result = { success: true, message: `Sound effect played. Keep talking - don't pause or wait!` };
                    } else {
                        switch (toolName) {
                            case 'ask_clawdbot':
                                result = await this.handleClawdbotTool(parameters);
                                break;
                            case 'play_music':
                                result = await this.handleMusicTool(parameters);
                                break;
                            case 'dj_soundboard':
                                result = this.handleSoundboardTool(parameters);
                                break;
                            case 'look_and_see':
                                result = await this.handleVisionTool(parameters);
                                break;
                            default:
                                result = { success: false, error: `Unknown tool: ${toolName}` };
                        }
                    }
                } catch (error) {
                    result = { success: false, error: error.message };
                }

                // Send tool response back to Hume
                this.sendToolResponse(toolCallId, result);
            }

            async handleSongTool(toolName, filename) {
                const musicModule = window.musicPlayer;

                // TWO-PHASE APPROACH:
                // Phase 1: Fetch metadata and return context to DJ IMMEDIATELY
                // Phase 2: Start crossfade in background while DJ talks

                try {
                    const url = `${CONFIG.serverUrl}/api/music?action=play&track=${encodeURIComponent(filename)}`;
                    const response = await fetch(url);
                    const data = await response.json();

                    if (data.track) {
                        // Build intro context for DJ
                        const track = data.track;
                        let introContext = `Coming up next: ${track.title}.`;

                        // Add DJ intro hints if available
                        if (track.dj_intro_hints && track.dj_intro_hints.length > 0) {
                            const randomHint = track.dj_intro_hints[Math.floor(Math.random() * track.dj_intro_hints.length)];
                            introContext += ` ${randomHint}`;
                        }

                        // Add phone number for sponsored tracks
                        if (track.phone_number) {
                            introContext += ` Phone: ${track.phone_number}.`;
                        }

                        // Add description
                        if (track.description) {
                            introContext += ` ${track.description}`;
                        }

                        console.log('Song tool: Returning context to DJ, starting crossfade async');

                        // PHASE 2: Start crossfade in background (don't await!)
                        // This runs while DJ is talking/introducing the song
                        const trackUrl = `${CONFIG.serverUrl}/music/${track.filename || filename}`;

                        // Use setTimeout to ensure response is sent first
                        setTimeout(() => {
                            if (musicModule.isPlaying) {
                                // Crossfade from current to new
                                musicModule.crossfade(trackUrl, track);
                            } else {
                                // Nothing playing, just start the new track
                                musicModule.audio.src = trackUrl;
                                musicModule.audio.volume = musicModule.volume;
                                musicModule.audio.play();
                                musicModule.isPlaying = true;
                                musicModule.currentTrack = track.filename || filename;
                                musicModule.currentMetadata = track;
                                musicModule.button.classList.add('active');
                                musicModule.panel.classList.add('playing');
                                musicModule._syncPlayButtons(true);
                                if (musicModule.panelState === 'closed') musicModule.openPanel();
                                musicModule.trackName.textContent = track.title || filename;
                            }
                        }, 100);  // Small delay to ensure response goes out first

                        // Return intro context immediately so DJ can talk while song loads
                        return {
                            success: true,
                            message: introContext,
                            track: track
                        };
                    }
                } catch (error) {
                    console.error('Song tool error:', error);
                }

                return { success: false, error: 'Failed to play track' };
            }

            async handleDJClipTool(toolName, clipInfo) {
                const musicModule = window.musicPlayer;

                // Prevent multiple clips from playing at once
                if (this.clipPlaying) {
                    console.log('DJ clip already playing, ignoring:', toolName);
                    return {
                        success: false,
                        message: 'A DJ clip is already playing! Wait for it to finish before playing another.'
                    };
                }

                // DJ Clips are pre-recorded segments - DJ should stay silent
                // Duck the music, play the clip, then restore music volume

                try {
                    console.log('Playing DJ clip:', toolName, clipInfo);
                    this.clipPlaying = true;
                    this.currentClipAudio = new Audio(`${CONFIG.serverUrl}/sounds/${clipInfo.file}`);
                    this.currentClipAudio.volume = 0.9;  // Clip at 90% volume

                    // Duck the music significantly for clips (to 20%)
                    if (musicModule.isPlaying) {
                        const activeAudio = musicModule.activeAudio === 1 ? musicModule.audio1 : musicModule.audio2;
                        activeAudio.volume = musicModule.volume * 0.2;
                    }

                    // Play the clip
                    await this.currentClipAudio.play();

                    // Restore music volume and reset flag when clip ends
                    this.currentClipAudio.addEventListener('ended', () => {
                        this.clipPlaying = false;
                        this.currentClipAudio = null;
                        if (musicModule.isPlaying) {
                            const activeAudio = musicModule.activeAudio === 1 ? musicModule.audio1 : musicModule.audio2;
                            activeAudio.volume = musicModule.volume;
                        }
                        console.log('DJ clip ended, music restored');
                    });

                    // Return immediately - tell DJ to stay silent
                    return {
                        success: true,
                        message: `Playing pre-recorded DJ clip (${clipInfo.duration} seconds). STAY COMPLETELY SILENT until the clip finishes. Do not talk over yourself.`,
                        duration: clipInfo.duration
                    };

                } catch (error) {
                    this.clipPlaying = false;
                    this.currentClipAudio = null;
                    console.error('DJ clip error:', error);
                    return { success: false, error: 'Failed to play DJ clip' };
                }
            }

            async handleMusicTool(params) {
                const action = params.action || 'status';
                const musicModule = window.musicPlayer;

                switch (action) {
                    case 'list':
                        const tracks = musicModule.metadata || {};
                        return {
                            success: true,
                            tracks: Object.entries(tracks).map(([file, info]) => ({
                                file,
                                title: info.title,
                                artist: info.artist,
                                description: info.description
                            }))
                        };
                    case 'play':
                        await musicModule.play(params.track);
                        // Small delay to ensure metadata is set
                        await new Promise(r => setTimeout(r, 100));
                        const playingTrack = musicModule.currentMetadata;
                        return {
                            success: true,
                            message: `Now playing: ${playingTrack?.title || 'a track'}. ${playingTrack?.description || ''} ${playingTrack?.phone_number ? 'Call ' + playingTrack.phone_number : ''}`,
                            track: playingTrack
                        };
                    case 'pause':
                        musicModule.pause();
                        return { success: true, message: 'Music paused' };
                    case 'stop':
                        musicModule.stop();
                        return { success: true, message: 'Music stopped' };
                    case 'skip':
                        await musicModule.next();
                        // Small delay to ensure metadata is set
                        await new Promise(r => setTimeout(r, 100));
                        const skippedToTrack = musicModule.currentMetadata;
                        return {
                            success: true,
                            message: `Now playing: ${skippedToTrack?.title || 'next track'}. ${skippedToTrack?.description || ''}`,
                            track: skippedToTrack
                        };
                    case 'volume':
                        musicModule.setVolume(params.volume || 80);
                        return { success: true, message: `Volume set to ${params.volume}%` };
                    case 'status':
                        return {
                            success: true,
                            isPlaying: musicModule.isPlaying,
                            track: musicModule.currentMetadata,
                            volume: Math.round(musicModule.volume * 100)
                        };
                    default:
                        return { success: false, error: `Unknown music action: ${action}` };
                }
            }

            handleSoundboardTool(params) {
                const sound = params.sound;
                if (sound) {
                    DJSoundboard.play(sound);
                    return { success: true, message: `Playing sound: ${sound}` };
                }
                return { success: false, error: 'No sound specified' };
            }

            async handleVisionTool(params) {
                const camera = window.cameraModule;
                if (!camera.stream) {
                    return { success: false, error: 'Camera is not active. Please turn on the camera first.' };
                }

                try {
                    // Capture frame and send to server for analysis
                    const canvas = camera.canvas;
                    const ctx = canvas.getContext('2d');
                    canvas.width = 640;
                    canvas.height = 480;
                    ctx.drawImage(camera.video, 0, 0, 640, 480);

                    const imageData = canvas.toDataURL('image/jpeg', 0.8);

                    const response = await fetch(`${this.config.serverUrl}/api/vision`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            image: imageData,
                            prompt: params.prompt || 'Describe what you see'
                        })
                    });

                    const data = await response.json();
                    return {
                        success: true,
                        description: data.description || data.response || 'Unable to analyze image'
                    };
                } catch (error) {
                    return { success: false, error: `Vision error: ${error.message}` };
                }
            }

            async handleClawdbotTool(parameters) {
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/conversation`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: parameters.message })
                    });
                    const data = await response.json();

                    if (data.error) {
                        return { success: false, error: data.error };
                    }

                    return { success: true, response: data.response };
                } catch (error) {
                    return { success: false, error: `Clawdbot error: ${error.message}` };
                }
            }

            sendToolResponse(toolCallId, result) {
                if (this.socket?.readyState === WebSocket.OPEN) {
                    const response = {
                        type: 'tool_response',
                        tool_call_id: toolCallId,
                        content: JSON.stringify(result)
                    };
                    this.socket.send(JSON.stringify(response));
                    console.log('Tool response sent:', toolCallId, result);
                }
            }
        }

        // ===== WAKE WORD TOGGLE FUNCTION =====
        // Global function for wake button onclick
        window.toggleWakeWord = async function() {
            if (!window.wakeDetector) return;
            if (window.PTTButton?.pttMode) return; // wake word blocked while PTT is active

            const wakeButton = document.getElementById('wake-button');
            const isListening = await window.wakeDetector.toggle();

            if (isListening) {
                wakeButton.classList.add('listening');
                console.log('Wake word detection enabled');
            } else {
                wakeButton.classList.remove('listening');
                console.log('Wake word detection disabled');
            }
        };

        // ===== CLAWDBOT EVENT HANDLERS =====
        // Set up send button and Enter key handlers
        document.addEventListener('DOMContentLoaded', () => {
            const sendBtn = document.getElementById('clawdbot-send-btn');
            const textInput = document.getElementById('clawdbot-text-input');

            if (sendBtn) {
                sendBtn.addEventListener('click', () => {
                    const input = document.getElementById('clawdbot-text-input');
                    const text = input?.value.trim();
                    if (text && ModeManager.clawdbotMode && ModeManager.clawdbotMode.isConnected) {
                        ModeManager.clawdbotMode.sendMessage(text);
                        if (input) input.value = '';
                    }
                });
            }

            if (textInput) {
                textInput.addEventListener('keypress', (e) => {
                    if (e.key === 'Enter') {
                        sendBtn?.click();
                    }
                });
            }
        });

        // ===== MAIN INITIALIZATION =====
        async function init() {
            // One-time migration: clear stale localStorage voice settings (v4)
            // Voice/provider now come from server profile, not localStorage
            if (localStorage.getItem('settings_v') !== '4') {
                localStorage.removeItem('voice_provider');
                localStorage.removeItem('voice_voice');
                localStorage.setItem('voice_mode', 'supertonic');
                localStorage.setItem('settings_v', '4');
            }

            console.log('Initializing OpenVoiceUI...');

            // Auth gate FIRST — blocks until user is signed in
            await AuthModule.init();

            // Initialize core modules (always needed)
            WaveformModule.init();
            FaceModule.startRandomBehavior();
            DJSoundboard.init();
            await MusicModule.init();
            CanvasControl.init();
            TranscriptPanel.init();
            ActionConsole.init();
            await CanvasMenu.init();

            // Initialize Provider Manager — sets window._serverProfile
            // which FaceRenderer, VisualizerModule, etc. read for persisted settings
            await ProviderManager.init();

            // Initialize modules that depend on server profile (after profile is loaded)
            await VisualizerModule.init();
            window.ThemeManager?.init();
            window.FaceRenderer?.init();
            window.SettingsPanel?.init();
            window.QuickSettings?.init();

            // Initialize Voice Conversation system (Web Speech STT + TTS)
            console.log('Initializing VoiceConversation system...');
            const voiceConversation = new VoiceConversation(CONFIG);
            window._voiceConversation = voiceConversation; // expose for stopAll()

            // Initialize Mode Manager with shared STT (handles Hume/Clawdbot switching)
            ModeManager.init(voiceConversation.stt);

            // Restore saved mode (default to supertonic).
            // savedMode may be a profile ID (e.g. 'default') or a transport
            // mode ('supertonic', 'hume'). Map profile IDs → transport mode.
            const savedMode = window._serverProfile?.ui?.voice_mode || localStorage.getItem('voice_mode') || 'supertonic';
            const savedTransport = (savedMode === 'hume' || savedMode === 'hume-evi') ? 'hume' : 'supertonic';
            if (savedTransport !== ModeManager.currentMode) {
                setTimeout(() => {
                    ModeManager.switchMode(savedTransport);
                }, 500);
            }
            // Note: ProviderManager.init() already restored the saved TTS provider from
            // localStorage — do NOT override it here. voiceConversation.setTTSProvider()
            // below will apply it correctly.

            // Initialize Wake Word Detector — matches STT provider
            console.log('Initializing WakeWordDetector...');
            const _sttProv = window._serverProfile?.stt?.provider || 'webspeech';
            const wakeDetector = _sttProv === 'deepgram' ? new DeepgramWakeWordDetector()
                               : _sttProv === 'groq' ? new GroqWakeWordDetector()
                               : new WakeWordDetector();
            window.wakeDetector = wakeDetector;

            // Set up wake word callback to auto-trigger call button
            if (wakeDetector.isSupported()) {
                wakeDetector.onWakeWordDetected = async () => {
                    console.log('Wake word detected!');
                    const profile  = window._activeProfileData || {};
                    const sttCfg   = profile.stt || {};
                    const camAuth  = sttCfg.require_camera_auth === true;
                    const identifyOnWake = sttCfg.identify_on_wake !== false; // default true
                    const camera   = window.cameraModule;
                    const camOn    = camera && camera.stream;

                    const callButton = document.getElementById('call-button');
                    const wakeButton = document.getElementById('wake-button');

                    // Always await face ID on wake so currentIdentity is fresh for greeting
                    if (identifyOnWake && camOn) {
                        StatusModule.update('thinking', 'IDENTIFYING...');
                        await camera.identifyFace().catch(() => {});
                        if (camAuth) {
                            const identity = camera.currentIdentity;
                            if (!identity || identity.name === 'unknown' || identity.confidence < 50) {
                                console.log('[CameraAuth] Face not recognized — wake blocked');
                                StatusModule.update('idle', 'NOT RECOGNIZED');
                                setTimeout(() => StatusModule.update('idle', 'READY'), 2500);
                                return;
                            }
                            console.log('[CameraAuth] Authorized:', identity.name);
                        }
                    }

                    // Show thinking immediately — user gets feedback before async startup
                    FaceModule.setMood('thinking');
                    StatusModule.update('thinking', 'CONNECTING...');
                    document.getElementById('thought-bubbles')?.classList.add('active');
                    window.HaloSmokeFace?.setThinking(true);

                    // Flash buttons and start conversation
                    callButton.classList.add('auto-triggered');
                    wakeButton.classList.add('active');
                    setTimeout(() => { callButton.classList.remove('auto-triggered'); }, 500);

                    ModeManager.toggleVoice();
                    UIModule.setCallButtonState('connected');
                };

                // Sync button visual state with detector state
                setInterval(() => {
                    const wakeButton = document.getElementById('wake-button');
                    if (wakeDetector.isListening) {
                        wakeButton.classList.add('listening');
                    } else {
                        wakeButton.classList.remove('listening');
                    }
                }, 1000);

                console.log('WakeWordDetector ready - say a wake word to start');

                // Wake word detection is manual - user toggles via ear button
                // Auto-start was removed because it conflicts with STT (both use Web Speech API)
            }

            // Set TTS provider from ProviderManager selection
            voiceConversation.setTTSProvider(ProviderManager.selectedProvider, ProviderManager.currentVoice);

            // Set up voice conversation callbacks
            voiceConversation.setCallbacks({
                onConnect: () => {
                    StatusModule.update('connected', 'CONNECTED');
                    UIModule.setCallButtonState('connected');
                    FaceModule.setMood('happy');
                    FaceModule.blink();
                    setTimeout(() => FaceModule.setMood('neutral'), 1000);
                },
                onDisconnect: () => {
                    StatusModule.update('disconnected', 'OFFLINE');
                    UIModule.setCallButtonState('disconnected');
                    FaceModule.setMood('neutral');
                    WaveformModule.setAmplitude(0);
                },
                onSpeaking: () => {
                    StatusModule.update('speaking', 'SPEAKING');
                    FaceModule.setMood('neutral');
                    MusicModule.duck(true);
                    document.getElementById('stop-button').style.display = '';
                    // Clear thinking state when TTS starts — agent is now speaking, not thinking
                    document.getElementById('thought-bubbles')?.classList.remove('active');
                    window.HaloSmokeFace?.setThinking(false);
                    TranscriptPanel.removeThinking?.();
                    if (voiceConversation.stt) {
                        console.log('🔇 Muting mic during TTS');
                        if (voiceConversation.stt.mute) {
                            voiceConversation.stt.mute();
                        } else {
                            if (voiceConversation.stt.isListening) voiceConversation.stt.stop();
                            if (voiceConversation.stt.resetProcessing) voiceConversation.stt.resetProcessing();
                        }
                    }
                    if (ModeManager.clawdbotMode) ModeManager.clawdbotMode._ttsPlaying = true;
                    // Reset guard timer — TTS is now actually playing, give it a fresh 20s window
                    if (ModeManager.clawdbotMode?._ttsGuardTimer) clearTimeout(ModeManager.clawdbotMode._ttsGuardTimer);
                    if (ModeManager.clawdbotMode) {
                        ModeManager.clawdbotMode._ttsGuardTimer = setTimeout(() => {
                            if (ModeManager.clawdbotMode?._ttsPlaying && ModeManager.clawdbotMode?._voiceActive) {
                                console.warn('[STT] Hard timeout: _ttsPlaying stuck for 20s — force-clearing');
                                ModeManager.clawdbotMode._ttsPlaying = false;
                                if (ModeManager.clawdbotMode.stt?.resume) ModeManager.clawdbotMode.stt.resume();
                                ModeManager.clawdbotMode.callbacks.onListening();
                            }
                        }, 20000);
                    }
                },
                onListening: () => {
                    StatusModule.update('listening', 'LISTENING');
                    FaceModule.setMood('listening');
                    WaveformModule.setAmplitude(0);
                    MusicModule.duck(false);
                    document.getElementById('stop-button').style.display = 'none';
                    // Cancel guard timer — TTS finished normally
                    if (ModeManager.clawdbotMode?._ttsGuardTimer) { clearTimeout(ModeManager.clawdbotMode._ttsGuardTimer); ModeManager.clawdbotMode._ttsGuardTimer = null; }
                    // _ttsPlaying stays true through brief delay to block echo.
                    // 300ms is enough — DeepgramStreamingSTT mutes its audio pipeline
                    // during TTS so speaker audio can't be captured. Reduced from 1500ms.
                    setTimeout(() => {
                        if (ModeManager.clawdbotMode) ModeManager.clawdbotMode._ttsPlaying = false;
                        if (voiceConversation.stt) {
                            // Skip resume if PTT is held — user is actively speaking,
                            // don't clear their accumulatedText
                            if (voiceConversation.stt._pttHolding) return;
                            console.log('🎤 Unmuting mic after TTS');
                            if (voiceConversation.stt.resume) {
                                voiceConversation.stt.resume();
                            } else {
                                if (voiceConversation.stt.resetProcessing) voiceConversation.stt.resetProcessing();
                                if (!voiceConversation.stt.isListening) voiceConversation.stt.start();
                            }
                        }
                    }, 300);
                },
                onTranscript: (text, isUser) => {
                    console.log(`${isUser ? 'User' : 'AI'}: ${text}`);
                    // Check AI responses for DJ sound triggers
                    if (!isUser) {
                        DJSoundboard.checkTriggers(text);
                    }
                },
                onError: (error) => {
                    UIModule.showError(error);
                    FaceModule.setMood('sad');
                    setTimeout(() => FaceModule.setMood('neutral'), 2000);
                }
            });

            // Expose voice agent for mic button
            window.voiceAgent = voiceConversation;
            window.originalVoiceAgent = voiceConversation;

            // Intercept call button click to run face ID before starting (if camera on)
            const _callBtn = document.getElementById('call-button');
            if (_callBtn) {
                _callBtn.removeAttribute('onclick');
                _callBtn.addEventListener('click', async () => {
                    // If already listening, toggle off via ModeManager — no need to identify
                    if (ModeManager.clawdbotMode?.stt?.isListening) {
                        ModeManager.toggleVoice();
                        return;
                    }

                    const profile        = window._activeProfileData || {};
                    const identifyOnWake = profile?.stt?.identify_on_wake !== false;
                    const camAuth        = profile?.stt?.require_camera_auth === true;
                    const camera         = window.cameraModule;
                    const camOn          = camera && camera.stream;

                    if (camOn && identifyOnWake) {
                        // Always await so currentIdentity is fresh when greeting fires
                        StatusModule.update('thinking', 'IDENTIFYING...');
                        await camera.identifyFace().catch(() => {});
                        if (camAuth) {
                            const id = camera.currentIdentity;
                            if (!id || id.name === 'unknown' || id.confidence < 50) {
                                StatusModule.update('idle', 'NOT RECOGNIZED');
                                setTimeout(() => StatusModule.update('idle', 'READY'), 2500);
                                return;
                            }
                        }
                    } else {
                        // No camera — show thinking immediately so user gets instant feedback
                        FaceModule.setMood('thinking');
                        StatusModule.update('thinking', 'CONNECTING...');
                        document.getElementById('thought-bubbles')?.classList.add('active');
                        window.HaloSmokeFace?.setThinking(true);
                    }
                    ModeManager.toggleVoice();
                });
            }

            // If mode is hume, initialize HumeAdapter as voice agent
            const activeMode = localStorage.getItem('voice_mode') || 'supertonic';
            if (activeMode === 'hume') {
                // Force switchMode by temporarily setting a different mode
                ModeManager.currentMode = '_init';
                ModeManager.switchMode('hume');
            }

            // Always expose these modules
            window.cameraModule = CameraModule;
            window.musicPlayer = MusicModule;
            window.faceModule = FaceModule;
            window.sunoModule = SunoModule;
            SunoModule.init();

            console.log('OpenVoiceUI initialized!');
            console.log('Mode:', activeMode);
            console.log('TTS Provider:', ProviderManager.selectedProvider);
        }

        // ===== CANVAS SYSTEM INTEGRATION =====
        window.CanvasControl = {
            container: null,
            iframe: null,
            isVisible: false,

            init() {
                this.container = document.getElementById('canvas-container');
                this.iframe = document.getElementById('canvas-iframe');

                // Inject dark scrollbar theme into canvas iframe pages
                if (this.iframe) {
                    this.iframe.addEventListener('load', () => {
                        try {
                            const doc = this.iframe.contentDocument;
                            if (doc) {
                                const style = doc.createElement('style');
                                style.textContent = `
                                    * { scrollbar-width: thin; scrollbar-color: var(--neutral-800) var(--bg-deep); }
                                    ::-webkit-scrollbar { width: 6px; height: 6px; }
                                    ::-webkit-scrollbar-track { background: var(--bg-deep); }
                                    ::-webkit-scrollbar-thumb { background: var(--neutral-800); border-radius: 3px; }
                                    ::-webkit-scrollbar-thumb:hover { background: var(--neutral-700); }
                                    ::-webkit-scrollbar-corner { background: var(--bg-deep); }
                                `;
                                doc.head.appendChild(style);
                            }
                        } catch (e) {
                            // Cross-origin pages can't be styled — that's fine
                        }
                    });
                }

                // postMessage bridge: canvas pages can send actions to the parent app
                // Actions: speak (send text to AI), navigate (open canvas page),
                //          open-url (load URL in iframe), menu (open canvas menu), close (close canvas)
                window.addEventListener('message', (event) => {
                    if (!event.data || event.data.type !== 'canvas-action') return;
                    const { action, text, page, url } = event.data;
                    console.log('[Canvas] postMessage action:', action, event.data);
                    switch (action) {
                        case 'speak':
                            // Send text as if user spoke it — triggers AI response
                            if (text && ModeManager.clawdbotMode) {
                                ModeManager.clawdbotMode.sendMessage(text);
                            }
                            break;
                        case 'navigate':
                            // Navigate to another canvas page
                            if (page) CanvasControl.showPage(page);
                            break;
                        case 'open-url':
                            // Load external URL in the iframe
                            if (url) {
                                const resolvedUrl = resolveCanvasUrl(url) || url;
                                const iframe = document.getElementById('canvas-iframe');
                                const container = document.getElementById('canvas-container');
                                if (iframe) iframe.src = resolvedUrl;
                                if (container && event.data.padded) {
                                    container.classList.add('canvas-padded');
                                } else if (container) {
                                    container.classList.remove('canvas-padded');
                                }
                            }
                            break;
                        case 'request-auth-token':
                            // Canvas page requesting a fresh Clerk JWT for API calls
                            Auth._pushAuthTokenToCanvas();
                            break;
                        case 'menu':
                            CanvasControl.showMenu();
                            break;
                        case 'close':
                            CanvasControl.hide();
                            break;
                    }
                });

                // Canvas error bridge: catch JS errors from sandboxed canvas pages
                window._canvasErrorBuffer = [];
                window.addEventListener('message', (event) => {
                    if (!event.data || event.data.type !== 'canvas-error') return;
                    const { error, source, line, col } = event.data;
                    console.error(`[Canvas JS Error] ${error} at ${source}:${line}:${col}`);
                    ActionConsole.addEntry('error', `Canvas JS: ${error} (line ${line})`);
                    // Buffer for auto-injection into next agent message
                    window._canvasErrorBuffer.push({ error, line, col, ts: Date.now() });
                    if (window._canvasErrorBuffer.length > 5) window._canvasErrorBuffer.shift();
                });

                // Auto-refresh polling: detect when agent edits the current canvas page
                this._pollInterval = null;
                this._lastMtime = null;
                this._startPoll();

                console.log('Canvas Control initialized');
            },

            _startPoll() {
                // Poll every 10 seconds for file changes on the displayed canvas page
                if (this._pollInterval) return;
                this._pollInterval = setInterval(() => this._checkForUpdates(), 10000);
                // Pause polling when tab is hidden to reduce server load
                document.addEventListener('visibilitychange', () => {
                    if (document.hidden) {
                        if (this._pollInterval) { clearInterval(this._pollInterval); this._pollInterval = null; }
                    } else if (this.isVisible) {
                        this._startPoll();
                    }
                });
            },

            async _checkForUpdates() {
                if (!this.isVisible || !this.iframe || document.hidden) return;
                const src = this.iframe.src || '';
                // Extract filename from iframe src like "/pages/voice-app-refactor-plan.html"
                const match = src.match(/\/pages\/([^?#]+)/);
                if (!match) return;
                const filename = match[1];
                try {
                    const resp = await fetch(`${CONFIG.serverUrl}/api/canvas/mtime/${filename}`);
                    if (!resp.ok) return;
                    const data = await resp.json();
                    if (this._lastMtime !== null && data.mtime > this._lastMtime) {
                        console.log('Canvas page updated, refreshing:', filename);
                        this.iframe.src = `/pages/${filename}?t=${Date.now()}`;
                    }
                    this._lastMtime = data.mtime;
                } catch (e) {
                    // Silent fail - server might be busy
                }
            },

            async show() {
                if (this.container) {
                    // Lazy-load canvas iframe on first show
                    if (this.iframe && (!this.iframe.src || this.iframe.src.endsWith('about:blank'))) {
                        // Try to load the last viewed page, fall back to default
                        const lastPage = localStorage.getItem('canvas_last_page');
                        if (lastPage) {
                            this.iframe.src = `/pages/${lastPage}`;
                            console.log('Canvas loading last page:', lastPage);
                            // Notify server of what page is displayed
                            try {
                                await fetch(`${CONFIG.serverUrl}/api/canvas/context`, {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        page: `/pages/${lastPage}`,
                                        title: lastPage.replace('.html', '').replace(/-/g, ' ')
                                    })
                                });
                            } catch (e) {
                                console.warn('Failed to update canvas context:', e);
                            }
                        } else {
                            this.iframe.src = `/pages/desktop.html`;
                        }
                    }
                    this.container.style.display = 'block';
                    this.isVisible = true;
                    document.body.classList.add('canvas-active');
                    document.getElementById('canvas-button')?.classList.add('active');
                    console.log('Canvas shown');
                }
            },

            hide() {
                if (this.container) {
                    this.container.style.display = 'none';
                    this.isVisible = false;
                    document.body.classList.remove('canvas-active');
                    document.getElementById('canvas-button')?.classList.remove('active');
                    console.log('Canvas hidden');
                }
            },

            toggle() {
                if (this.isVisible) {
                    this.hide();
                } else {
                    this.show();
                }
            },

            openPage(page) {
                if (this.iframe) {
                    this.iframe.src = `/pages/${page}?t=${Date.now()}`;
                    localStorage.setItem('canvas_last_page', page);
                    this._lastMtime = null;  // reset mtime tracking on manual navigation
                }
                console.log('Canvas opening:', page);
                this.show();
            },

            showMenu() {
                // Open the Desktop OS page as the canvas menu
                this.openPage('desktop.html');
            },

            showPage(pageName) {
                // Fuzzy-find page by name using CanvasMenu's lookup
                if (!pageName) return;
                const menu = window.CanvasMenu;
                if (!menu?.manifest) {
                    // Manifest not loaded yet — try direct filename
                    const filename = pageName.replace(/\s+/g, '-').toLowerCase() + '.html';
                    console.log('[Canvas] showPage direct:', filename);
                    if (this.iframe) {
                        this.iframe.src = `/pages/${filename}?t=${Date.now()}`;
                        localStorage.setItem('canvas_last_page', filename);
                        this._lastMtime = null;
                        this.show();
                    }
                    return;
                }
                const match = menu.findPageByName(pageName);
                if (match) {
                    console.log('[Canvas] showPage matched:', match.page.display_name);
                    menu.showPage(match.page.filename);
                } else {
                    // Fallback: try as-is with .html
                    const filename = pageName.replace(/\s+/g, '-').toLowerCase() + '.html';
                    console.log('[Canvas] showPage fallback:', filename);
                    if (this.iframe) {
                        this.iframe.src = `/pages/${filename}?t=${Date.now()}`;
                        localStorage.setItem('canvas_last_page', filename);
                        this._lastMtime = null;
                        this.show();
                    }
                }
            },

            async updateDisplay(type, path, title) {
                // Save page for next time if it's a page type
                if (type === 'page' && path) {
                    const filename = path.split('/').pop();
                    localStorage.setItem('canvas_last_page', filename);
                }
                // Send update to Canvas SSE server via our proxy
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/canvas/update`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            displayOutput: {
                                type,      // 'page', 'image', 'status'
                                path,      // '/pages/xyz.html' or '/images/xyz.png'
                                title
                            }
                        })
                    });

                    if (response.ok) {
                        console.log(`Canvas updated: ${type} - ${title}`);
                        this.show(); // Auto-show when content is updated
                        return true;
                    } else {
                        console.error('Canvas update failed:', response.status);
                        return false;
                    }
                } catch (error) {
                    console.error('Canvas update error:', error);
                    return false;
                }
            }
        };

        // ===== CANVAS MENU SYSTEM =====
        window.CanvasMenu = {
            manifest: null,
            currentFilter: 'all',
            isVisible: false,
            editMode: false,
            draggedPage: null,
            draggedCategory: null,

            async init() {
                console.log('CanvasMenu initializing...');
                await this.loadManifest();
                this.setupEvents();
                console.log(`CanvasMenu ready: ${Object.keys(this.manifest?.pages || {}).length} pages`);
            },

            async loadManifest() {
                try {
                    // Add cache-busting param to ensure fresh data
                    const response = await fetch(`${CONFIG.serverUrl}/api/canvas/manifest?t=${Date.now()}`);
                    this.manifest = await response.json();
                    console.log('Loaded manifest:', Object.keys(this.manifest.pages || {}).length, 'pages');
                } catch (e) {
                    console.error('Failed to load canvas manifest:', e);
                    this.manifest = { pages: {}, categories: {}, uncategorized: [], recently_viewed: [] };
                }
            },

            async refresh() {
                await this.loadManifest();
                if (this.isVisible) {
                    this.render();
                }
            },

            show() {
                const modal = document.getElementById('canvas-menu-modal');
                const btn = document.getElementById('canvas-menu-button');
                if (modal) {
                    modal.style.display = 'flex';
                    this.isVisible = true;
                    btn?.classList.add('active');
                    this.render();
                    // Only auto-focus search on desktop — on mobile Safari this triggers
                    // keyboard zoom which looks broken to the user
                    if (!('ontouchstart' in window)) {
                        document.getElementById('canvas-search')?.focus();
                    }
                    // Refresh manifest in background so newly created pages appear
                    this.loadManifest().then(() => {
                        if (this.isVisible) this.render();
                    }).catch(() => {});
                }
            },

            hide() {
                const modal = document.getElementById('canvas-menu-modal');
                const btn = document.getElementById('canvas-menu-button');
                if (modal) {
                    modal.style.display = 'none';
                    this.isVisible = false;
                    btn?.classList.remove('active');
                }
            },

            toggle() {
                if (this.isVisible) {
                    this.hide();
                } else {
                    this.show();
                }
            },

            render() {
                const container = document.getElementById('canvas-categories');
                if (!container) return;

                const pageCount = Object.keys(this.manifest.pages || {}).length;
                const countEl = document.getElementById('cmm-page-count');
                if (countEl) countEl.textContent = `${pageCount} pages`;

                console.log('Rendering canvas menu:', pageCount, 'pages, filter:', this.currentFilter);

                if (pageCount === 0) {
                    container.innerHTML = '<div class="cmm-empty">No canvas pages yet</div>';
                    return;
                }

                let html = '';
                const categories = this.manifest.categories || {};
                const expandedDefault = ['favorites', 'dashboards', 'weather']; // Auto-expand these

                // Collect all starred pages for Favorites category
                const allStarred = Object.values(this.manifest.pages || {})
                    .filter(p => p.starred)
                    .sort((a, b) => (a.display_name || '').localeCompare(b.display_name || ''));

                // Render Favorites category at top (unless filter is 'starred' which would be redundant)
                if (allStarred.length > 0 && this.currentFilter !== 'starred') {
                    html += `
                        <div class="cmm-category expanded" data-category="favorites">
                            <div class="cmm-cat-header" draggable="false">
                                <span class="cmm-cat-icon">⭐</span>
                                <span class="cmm-cat-name">Favorites</span>
                                <span class="cmm-cat-count">(${allStarred.length})</span>
                                <span class="cmm-cat-expand">▼</span>
                            </div>
                            <div class="cmm-cat-pages">
                                ${allStarred.map(p => this.renderPage(p)).join('')}
                            </div>
                        </div>
                    `;
                }

                // Render each category (sorted, with starred pages at top within each)
                const sortedCategories = Object.entries(categories).sort((a, b) => {
                    // Sort categories alphabetically
                    return (a[1].name || a[0]).localeCompare(b[1].name || b[0]);
                });

                for (const [catId, cat] of sortedCategories) {
                    let pages = (cat.pages || [])
                        .map(pid => {
                            const page = this.manifest.pages[pid];
                            if (!page) {
                                console.warn('Page ID in category but not in manifest:', pid);
                            }
                            return page;
                        })
                        .filter(p => p);

                    // Sort: starred pages first, then alphabetically
                    pages.sort((a, b) => {
                        if (a.starred && !b.starred) return -1;
                        if (!a.starred && b.starred) return 1;
                        return (a.display_name || '').localeCompare(b.display_name || '');
                    });

                    // Apply filter
                    if (this.currentFilter === 'starred') {
                        pages = pages.filter(p => p.starred);
                    } else if (this.currentFilter === 'recent') {
                        const recent = this.manifest.recently_viewed || [];
                        pages = pages.filter(p => recent.includes(p.filename?.replace('.html', '')));
                    }

                    if (pages.length === 0) continue;

                    const isExpanded = expandedDefault.includes(catId);
                    const catDraggable = this.editMode ? 'true' : 'false';
                    html += `
                        <div class="cmm-category ${isExpanded ? 'expanded' : ''}" data-category="${catId}">
                            <div class="cmm-cat-header" draggable="${catDraggable}">
                                <span class="cmm-cat-icon">${cat.icon || '📄'}</span>
                                <span class="cmm-cat-name">${cat.name}</span>
                                <span class="cmm-cat-count">(${pages.length})</span>
                                <span class="cmm-cat-expand">▼</span>
                            </div>
                            <div class="cmm-cat-pages">
                                ${pages.map(p => this.renderPage(p)).join('')}
                            </div>
                        </div>
                    `;
                }

                container.innerHTML = html || '<div class="cmm-empty">No pages match filter</div>';
            },

            renderPage(page) {
                if (!page || !page.filename) {
                    console.warn('renderPage called with invalid page:', page);
                    return '';
                }
                const starred = page.starred ? 'active' : '';
                const timeAgo = this.timeAgo(page.modified);
                const pageId = page.filename.replace('.html', '');
                const draggable = this.editMode ? 'true' : 'false';
                const displayName = page.display_name || page.filename;

                return `
                    <div class="cmm-page" data-page-id="${pageId}" data-filename="${page.filename}" draggable="${draggable}">
                        <span class="cmm-page-star ${starred}" data-page-id="${pageId}">⭐</span>
                        <span class="cmm-page-name">${displayName}</span>
                        <span class="cmm-page-time">${timeAgo}</span>
                        <span class="cmm-page-delete" data-page-id="${pageId}" data-page-name="${displayName}" title="Delete page">×</span>
                    </div>
                `;
            },

            timeAgo(timestamp) {
                if (!timestamp) return '';
                try {
                    const date = new Date(timestamp);
                    const now = new Date();
                    const hours = Math.floor((now - date) / (1000 * 60 * 60));
                    if (hours < 1) return 'now';
                    if (hours < 24) return `${hours}h`;
                    const days = Math.floor(hours / 24);
                    if (days < 7) return `${days}d`;
                    const weeks = Math.floor(days / 7);
                    return `${weeks}w`;
                } catch {
                    return '';
                }
            },

            setupEvents() {
                // Menu button click — opens Desktop OS canvas page
                document.getElementById('canvas-menu-button')?.addEventListener('click', () => {
                    CanvasControl.openPage('desktop.html');
                });

                // Close button
                document.querySelector('.cmm-close')?.addEventListener('click', () => this.hide());

                // Backdrop click
                document.querySelector('.cmm-backdrop')?.addEventListener('click', () => this.hide());

                // Search input
                const searchInput = document.getElementById('canvas-search');
                searchInput?.addEventListener('input', (e) => {
                    this.filterPages(e.target.value);
                });

                // Quick filter buttons
                document.querySelectorAll('.cmm-qa').forEach(btn => {
                    btn.addEventListener('click', () => {
                        document.querySelectorAll('.cmm-qa').forEach(b => b.classList.remove('active'));
                        btn.classList.add('active');
                        this.currentFilter = btn.dataset.filter;
                        this.render();
                    });
                });

                // Category expand/collapse and page clicks
                const container = document.getElementById('canvas-categories');
                container?.addEventListener('click', (e) => {
                    // Category header click - toggle expand
                    const header = e.target.closest('.cmm-cat-header');
                    if (header) {
                        const category = header.closest('.cmm-category');
                        category.classList.toggle('expanded');
                        return;
                    }

                    // Delete button click
                    if (e.target.classList.contains('cmm-page-delete')) {
                        e.stopPropagation();
                        this.showDeleteConfirm(e.target.dataset.pageId, e.target.dataset.pageName);
                        return;
                    }

                    // Star click
                    if (e.target.classList.contains('cmm-page-star')) {
                        e.stopPropagation();
                        this.toggleStar(e.target.dataset.pageId);
                        return;
                    }

                    // Page click
                    const pageEl = e.target.closest('.cmm-page');
                    if (pageEl) {
                        this.showPage(pageEl.dataset.filename);
                    }
                });

                // Escape key to close
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape' && this.isVisible) {
                        this.hide();
                    }
                });

                // Edit mode toggle
                document.getElementById('cmm-edit-mode')?.addEventListener('click', () => {
                    this.toggleEditMode();
                });

                // Delete confirmation modal handlers
                document.getElementById('cmm-confirm-cancel')?.addEventListener('click', () => {
                    this.hideDeleteConfirm();
                });

                document.getElementById('cmm-confirm-delete')?.addEventListener('click', () => {
                    if (this.pendingDeletePageId) {
                        this.deletePage(this.pendingDeletePageId);
                        this.hideDeleteConfirm();
                    }
                });

                // Close confirm modal on backdrop click
                document.getElementById('cmm-confirm-modal')?.addEventListener('click', (e) => {
                    if (e.target.id === 'cmm-confirm-modal') {
                        this.hideDeleteConfirm();
                    }
                });
            },

            showDeleteConfirm(pageId, pageName) {
                this.pendingDeletePageId = pageId;
                const modal = document.getElementById('cmm-confirm-modal');
                const nameEl = document.getElementById('cmm-confirm-page-name');
                if (modal && nameEl) {
                    nameEl.textContent = pageName || pageId;
                    modal.style.display = 'flex';
                }
            },

            hideDeleteConfirm() {
                const modal = document.getElementById('cmm-confirm-modal');
                if (modal) {
                    modal.style.display = 'none';
                }
                this.pendingDeletePageId = null;
            },

            async deletePage(pageId) {
                console.log('Archiving page:', pageId);

                // Delete from server FIRST
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/page/${pageId}`, {
                        method: 'DELETE'
                    });
                    if (!response.ok) {
                        throw new Error('Archive failed');
                    }
                    console.log('Server archived page successfully');
                } catch (e) {
                    console.error('Failed to archive page from server:', e);
                    return; // Don't update UI if server failed
                }

                // Reload fresh manifest from server (authoritative)
                await this.loadManifest();

                // Re-render with fresh data
                this.render();
                if (this.editMode) {
                    this.setupDragDrop();
                }
            },

            toggleEditMode() {
                this.editMode = !this.editMode;
                const btn = document.getElementById('cmm-edit-mode');
                const content = document.querySelector('.cmm-content');

                if (this.editMode) {
                    btn?.classList.add('active');
                    btn.textContent = '✓ Done';
                    content?.classList.add('edit-mode');
                } else {
                    btn?.classList.remove('active');
                    btn.textContent = '✏️ Edit';
                    content?.classList.remove('edit-mode');
                }

                // Re-render to update draggable attributes
                this.render();

                // Setup drag-drop handlers after render
                if (this.editMode) {
                    this.setupDragDrop();
                }
            },

            setupDragDrop() {
                const container = document.getElementById('canvas-categories');
                if (!container) return;

                // Page drag handlers
                container.querySelectorAll('.cmm-page').forEach(pageEl => {
                    pageEl.addEventListener('dragstart', (e) => {
                        this.draggedPage = pageEl.dataset.pageId;
                        pageEl.classList.add('dragging');
                        e.dataTransfer.effectAllowed = 'move';
                        e.dataTransfer.setData('text/plain', pageEl.dataset.pageId);
                    });

                    pageEl.addEventListener('dragend', () => {
                        pageEl.classList.remove('dragging');
                        this.draggedPage = null;
                        // Remove all drag-over states
                        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
                    });
                });

                // Category pages drop zone handlers
                container.querySelectorAll('.cmm-cat-pages').forEach(catPages => {
                    catPages.addEventListener('dragover', (e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'move';
                        catPages.classList.add('drag-over');
                    });

                    catPages.addEventListener('dragleave', (e) => {
                        if (!catPages.contains(e.relatedTarget)) {
                            catPages.classList.remove('drag-over');
                        }
                    });

                    catPages.addEventListener('drop', (e) => {
                        e.preventDefault();
                        catPages.classList.remove('drag-over');

                        const pageId = e.dataTransfer.getData('text/plain');
                        const categoryEl = catPages.closest('.cmm-category');
                        const targetCategory = categoryEl?.dataset.category;

                        if (pageId && targetCategory) {
                            this.movePageToCategory(pageId, targetCategory);
                        }
                    });
                });

                // Category header drag handlers (for reordering categories)
                container.querySelectorAll('.cmm-cat-header').forEach(header => {
                    header.addEventListener('dragstart', (e) => {
                        const category = header.closest('.cmm-category');
                        this.draggedCategory = category.dataset.category;
                        category.classList.add('dragging');
                        e.dataTransfer.effectAllowed = 'move';
                        e.dataTransfer.setData('text/plain', category.dataset.category);
                    });

                    header.addEventListener('dragend', () => {
                        const category = header.closest('.cmm-category');
                        category.classList.remove('dragging');
                        this.draggedCategory = null;
                        document.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
                    });

                    // Allow dropping on other category headers
                    header.addEventListener('dragover', (e) => {
                        e.preventDefault();
                        header.style.background = 'rgba(0, 136, 255, 0.1)'; // matches --blue with alpha
                    });

                    header.addEventListener('dragleave', () => {
                        header.style.background = '';
                    });

                    header.addEventListener('drop', (e) => {
                        e.preventDefault();
                        header.style.background = '';

                        const draggedCat = e.dataTransfer.getData('text/plain');
                        const targetCategory = header.closest('.cmm-category');

                        if (draggedCat && targetCategory && this.draggedCategory !== targetCategory.dataset.category) {
                            this.reorderCategory(draggedCat, targetCategory.dataset.category);
                        }
                    });
                });
            },

            async movePageToCategory(pageId, newCategory) {
                console.log(`Moving page ${pageId} to category ${newCategory}`);

                // Update local manifest
                const page = this.manifest.pages[pageId];
                if (!page) return;

                const oldCategory = page.category;

                // Remove from old category
                if (oldCategory && this.manifest.categories[oldCategory]) {
                    const idx = this.manifest.categories[oldCategory].pages.indexOf(pageId);
                    if (idx > -1) {
                        this.manifest.categories[oldCategory].pages.splice(idx, 1);
                    }
                }
                if (this.manifest.uncategorized?.includes(pageId)) {
                    const idx = this.manifest.uncategorized.indexOf(pageId);
                    if (idx > -1) {
                        this.manifest.uncategorized.splice(idx, 1);
                    }
                }

                // Add to new category
                page.category = newCategory;
                if (!this.manifest.categories[newCategory]) {
                    this.manifest.categories[newCategory] = {
                        name: newCategory.charAt(0).toUpperCase() + newCategory.slice(1),
                        icon: '📄',
                        color: '#00aaff', // matches --blue-bright
                        pages: []
                    };
                }
                if (!this.manifest.categories[newCategory].pages.includes(pageId)) {
                    this.manifest.categories[newCategory].pages.push(pageId);
                }

                // Save to server
                try {
                    await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/page/${pageId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ category: newCategory })
                    });
                } catch (e) {
                    console.error('Failed to move page:', e);
                    return;
                }

                // Re-render
                this.render();
                this.setupDragDrop();
            },

            async reorderCategory(draggedCatId, targetCatId) {
                console.log(`Reordering: ${draggedCatId} before ${targetCatId}`);

                // Get category keys in order
                const catKeys = Object.keys(this.manifest.categories);
                const draggedIdx = catKeys.indexOf(draggedCatId);
                const targetIdx = catKeys.indexOf(targetCatId);

                if (draggedIdx === -1 || targetIdx === -1) return;

                // Reorder
                catKeys.splice(draggedIdx, 1);
                catKeys.splice(targetIdx, 0, draggedCatId);

                // Rebuild categories object in new order
                const newCategories = {};
                catKeys.forEach(key => {
                    newCategories[key] = this.manifest.categories[key];
                });
                this.manifest.categories = newCategories;

                // Re-render
                this.render();
                this.setupDragDrop();
            },

            filterPages(query) {
                const q = query.toLowerCase().trim();
                document.querySelectorAll('.cmm-page').forEach(el => {
                    const name = el.querySelector('.cmm-page-name')?.textContent.toLowerCase() || '';
                    el.style.display = !q || name.includes(q) ? 'flex' : 'none';
                });
            },

            async showPage(filename) {
                if (!filename) return;
                console.log('CanvasMenu: Showing page', filename);

                const iframe = document.getElementById('canvas-iframe');
                if (iframe) {
                    iframe.src = `/pages/${filename}?t=${Date.now()}`;
                    // Clear padded mode when navigating to a canvas page
                    const container = document.getElementById('canvas-container');
                    if (container) container.classList.remove('canvas-padded');
                    // Reset mtime tracking so auto-refresh doesn't false-trigger on page switch
                    CanvasControl._lastMtime = null;
                    // Remember this page for next time
                    localStorage.setItem('canvas_last_page', filename);
                    CanvasControl.show();
                    this.hide();

                    // Get the display name from manifest
                    const pageId = filename.replace('.html', '');
                    const pageData = this.manifest.pages[pageId];
                    const displayTitle = pageData?.display_name || filename.replace('.html', '').replace(/-/g, ' ');

                    // Track access
                    try {
                        await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/access/${pageId}`, {
                            method: 'POST'
                        });
                    } catch (e) {
                        console.warn('Failed to track page access:', e);
                    }

                    // Notify canvas context with proper title
                    try {
                        await fetch(`${CONFIG.serverUrl}/api/canvas/context`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                page: `/pages/${filename}`,
                                title: displayTitle
                            })
                        });
                        // Also update local window context
                        window.canvasContext = {
                            current_page: `/pages/${filename}`,
                            current_title: displayTitle,
                            updated_at: new Date().toISOString()
                        };
                        console.log('Canvas context updated:', displayTitle);
                    } catch (e) {
                        console.warn('Failed to update canvas context:', e);
                    }

                    // Sync SSE canvas server so canvas-proxy display stays in sync
                    try {
                        await fetch(`${CONFIG.serverUrl}/api/canvas/update`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                displayOutput: { type: 'page', path: `/pages/${filename}`, title: displayTitle }
                            })
                        });
                    } catch (e) {
                        console.warn('Failed to sync SSE canvas server:', e);
                    }
                }
            },

            async toggleStar(pageId) {
                if (!pageId || !this.manifest.pages[pageId]) return;

                const page = this.manifest.pages[pageId];
                page.starred = !page.starred;

                // Update UI immediately
                const starEl = document.querySelector(`.cmm-page-star[data-page-id="${pageId}"]`);
                if (starEl) {
                    starEl.classList.toggle('active', page.starred);
                }

                // Save to server
                try {
                    await fetch(`${CONFIG.serverUrl}/api/canvas/manifest/page/${pageId}`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ starred: page.starred })
                    });
                } catch (e) {
                    console.error('Failed to save star:', e);
                    // Revert on error
                    page.starred = !page.starred;
                    if (starEl) {
                        starEl.classList.toggle('active', page.starred);
                    }
                }
            },

            // ===== VOICE COMMAND METHODS =====
            findPageByName(query) {
                /** Find a page by name or voice alias (fuzzy match) */
                if (!query || !this.manifest?.pages) return null;

                const q = query.toLowerCase().trim();

                // Exact page ID match (highest priority — agent sends [CANVAS:page-id])
                if (this.manifest.pages[q]) {
                    return { page: this.manifest.pages[q], pageId: q, score: 100 };
                }

                let bestMatch = null;
                let bestScore = 0;

                for (const [pageId, page] of Object.entries(this.manifest.pages)) {
                    // Check voice aliases (exact or partial)
                    const aliases = page.voice_aliases || [];
                    for (const alias of aliases) {
                        if (alias.toLowerCase() === q) {
                            return { page, pageId, score: 100 }; // Exact alias match
                        }
                        if (alias.toLowerCase().includes(q) || q.includes(alias.toLowerCase())) {
                            const score = 80 + Math.min(alias.length, 20);
                            if (score > bestScore) {
                                bestScore = score;
                                bestMatch = { page, pageId, score };
                            }
                        }
                    }

                    // Check display name
                    const displayName = (page.display_name || '').toLowerCase();
                    if (displayName === q) {
                        return { page, pageId, score: 95 }; // Exact name match
                    }
                    if (displayName.includes(q) || q.includes(displayName)) {
                        const score = 70 + Math.min(displayName.length, 25);
                        if (score > bestScore) {
                            bestScore = score;
                            bestMatch = { page, pageId, score };
                        }
                    }

                    // Check individual words in display name
                    const words = displayName.split(/\s+/);
                    for (const word of words) {
                        if (word.length > 3 && (q.includes(word) || word.includes(q))) {
                            const score = 50 + word.length;
                            if (score > bestScore) {
                                bestScore = score;
                                bestMatch = { page, pageId, score };
                            }
                        }
                    }
                }

                return bestMatch;
            },

            async handleVoiceCommand(command) {
                /** Parse and execute voice commands like "show dashboard" */
                if (!command) return null;

                const cmd = command.toLowerCase().trim();
                console.log('CanvasMenu voice command:', cmd);

                // "Show [page name]" or "Open [page name]"
                const showMatch = cmd.match(/^(?:show|open|display|view)\s+(?:me\s+)?(?:the\s+)?(.+?)(?:\s+page)?$/i);
                if (showMatch) {
                    const pageName = showMatch[1].trim();
                    const match = this.findPageByName(pageName);
                    if (match) {
                        console.log(`Voice command: showing page "${match.page.display_name}"`);
                        await this.showPage(match.page.filename);
                        return { action: 'show', page: match.page.display_name };
                    }
                }

                // "Show [category] pages" or "Show [category] category"
                const catMatch = cmd.match(/^(?:show|open|view)\s+(.+?)\s+(?:pages|category|dashboard|panel)$/i);
                if (catMatch) {
                    const categoryName = catMatch[1].trim();
                    const catId = categoryName.toLowerCase().replace(/\s+/g, '');
                    const categories = this.manifest?.categories || {};

                    // Find matching category
                    for (const [cid, cat] of Object.entries(categories)) {
                        if (cid === catId || cat.name.toLowerCase().includes(catId)) {
                            // Find first page in this category and show it
                            const pages = cat.pages || [];
                            if (pages.length > 0 && this.manifest.pages[pages[0]]) {
                                const page = this.manifest.pages[pages[0]];
                                await this.showPage(page.filename);
                                return { action: 'category', category: cat.name, page: page.display_name };
                            }
                        }
                    }
                }

                // "What pages do we have?" / "List pages" / "Show all pages"
                if (cmd.includes('what pages') || cmd.includes('list pages') || cmd.includes('show all pages') || cmd.includes('which pages')) {
                    this.show();
                    // Expand all categories
                    setTimeout(() => {
                        document.querySelectorAll('.cmm-category').forEach(el => {
                            el.classList.add('expanded');
                        });
                    }, 100);
                    return { action: 'list', message: 'Canvas menu opened' };
                }

                // "Star this page" / "Favorite this page"
                if (cmd.includes('star this page') || cmd.includes('favorite this page')) {
                    // Get current page from canvas context
                    const currentPage = window.canvasContext?.current_page;
                    if (currentPage) {
                        const pageId = currentPage.replace('/pages/', '').replace('.html', '');
                        await this.toggleStar(pageId);
                        return { action: 'star', page: pageId };
                    }
                }

                return null;
            },

            listPages() {
                /** Get a formatted list of all pages for voice response */
                const pages = Object.values(this.manifest?.pages || {});
                const categories = this.manifest?.categories || {};

                const result = {
                    total: pages.length,
                    categories: Object.entries(categories).map(([id, cat]) => ({
                        name: cat.name,
                        icon: cat.icon,
                        count: (cat.pages || []).length
                    })).filter(c => c.count > 0),
                    starred: pages.filter(p => p.starred).map(p => p.display_name),
                    recent: (this.manifest?.recently_viewed || []).slice(0, 5).map(pid => {
                        const p = this.manifest?.pages?.[pid];
                        return p?.display_name;
                    }).filter(Boolean)
                };

                return result;
            }
        };

        // ===== TRANSCRIPT PANEL =====
        window.TranscriptPanel = {
            panel: null,
            messages: null,
            button: null,
            unreadDot: null,
            isVisible: false,
            agentName: 'Agent',
            userName: 'User',

            init() {
                this.panel = document.getElementById('transcript-panel');
                this.messages = document.getElementById('transcript-messages');
                this.button = document.getElementById('transcript-button');
                this.unreadDot = document.getElementById('transcript-unread');

                // Paste image support on text input
                const textInput = document.getElementById('tp-text-input');
                if (textInput) {
                    textInput.addEventListener('paste', (e) => {
                        const items = e.clipboardData?.items;
                        if (!items) return;
                        for (const item of items) {
                            if (item.type.startsWith('image/')) {
                                e.preventDefault();
                                const file = item.getAsFile();
                                if (file) this._stageFile(file);
                                return;
                            }
                        }
                    });
                }

                // Drag-and-drop image support on transcript panel
                if (this.panel) {
                    this.panel.addEventListener('dragover', (e) => {
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'copy';
                        this.panel.classList.add('tp-dragover');
                    });
                    this.panel.addEventListener('dragleave', () => {
                        this.panel.classList.remove('tp-dragover');
                    });
                    this.panel.addEventListener('drop', (e) => {
                        e.preventDefault();
                        this.panel.classList.remove('tp-dragover');
                        const files = e.dataTransfer?.files;
                        if (files && files.length > 1) {
                            this._stageBulkFiles(Array.from(files));
                        } else if (files && files.length === 1) {
                            this._stageFile(files[0]);
                        }
                    });
                }

                console.log('Transcript Panel initialized');
            },

            _stageFile(file) {
                this._pendingFile = { file, name: file.name, type: file.type, size: file.size };
                this._pendingBulkFiles = null;
                // Create blob URL for thumbnail preview in chat
                if (file.type.startsWith('image/')) {
                    this._pendingImageThumbUrl = URL.createObjectURL(file);
                }
                const preview = document.getElementById('tp-file-preview');
                const nameEl = document.getElementById('tp-file-name');
                if (preview && nameEl) {
                    nameEl.textContent = file.name.length > 20 ? file.name.substring(0, 17) + '...' : file.name;
                    preview.style.display = 'flex';
                }
                document.getElementById('tp-text-input')?.focus();
                if (!this.isVisible) this.show();
            },

            _stageBulkFiles(files) {
                this._pendingFile = null;
                this._pendingBulkFiles = files;
                const preview = document.getElementById('tp-file-preview');
                const nameEl = document.getElementById('tp-file-name');
                if (preview && nameEl) {
                    nameEl.textContent = `${files.length} files selected`;
                    preview.style.display = 'flex';
                }
                document.getElementById('tp-text-input')?.focus();
                if (!this.isVisible) this.show();
            },

            show() {
                if (this.panel) {
                    this.panel.style.display = 'flex';
                    this.isVisible = true;
                    this.button?.classList.add('active');
                    if (this.unreadDot) this.unreadDot.style.display = 'none';
                    // Scroll to bottom
                    if (this.messages) this.messages.scrollTop = this.messages.scrollHeight;
                }
            },

            hide() {
                if (this.panel) {
                    this.panel.style.display = 'none';
                    this.isVisible = false;
                    this.button?.classList.remove('active');
                }
            },

            toggle() {
                if (this.isVisible) this.hide(); else this.show();
            },

            addMessage(role, text, opts = {}) {
                if (!this.messages || !text) return;

                const msg = document.createElement('div');
                msg.className = `tp-msg ${role === 'user' ? 'user' : 'assistant'}`;

                const now = new Date();
                const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                const name = role === 'user' ? this.userName : this.agentName;

                // Simple markdown: **bold**, `code`, newlines
                let html = text
                    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                    .replace(/`([^`]+)`/g, '<code>$1</code>')
                    .replace(/\n/g, '<br>');

                // Small image icon beside the message
                let imgIcon = '';
                if (opts.imageUrl) {
                    imgIcon = `<img src="${opts.imageUrl}" class="tp-img-icon" alt="📷" title="Image attached">`;
                }

                msg.innerHTML = `<div class="tp-meta">${name} · ${time}</div><div class="tp-text">${imgIcon}${html}</div>`;
                this.messages.appendChild(msg);
                this.messages.scrollTop = this.messages.scrollHeight;

                // Show unread dot if panel is closed
                if (!this.isVisible && this.unreadDot && role === 'assistant') {
                    this.unreadDot.style.display = 'block';
                }
            },

            clear() {
                if (this.messages) this.messages.innerHTML = '';
            },

            showThinking() {
                if (!this.messages) return;
                this.removeThinking();
                const msg = document.createElement('div');
                msg.className = 'tp-msg assistant tp-thinking';
                msg.innerHTML = `<div class="tp-meta">${this.agentName}</div><div class="tp-dots"><span></span><span></span><span></span></div>`;
                this.messages.appendChild(msg);
                this.messages.scrollTop = this.messages.scrollHeight;
                if (!this.isVisible && this.unreadDot) {
                    this.unreadDot.style.display = 'block';
                }
            },

            removeThinking() {
                if (!this.messages) return;
                const existing = this.messages.querySelector('.tp-thinking');
                if (existing) existing.remove();
            },

            showToolStatus(toolName) {
                const existing = this.messages?.querySelector('.tp-thinking');
                if (existing) {
                    const dots = existing.querySelector('.tp-dots');
                    if (dots) dots.textContent = `using tool: ${toolName}…`;
                }
            },

            // Streaming support: create a message element that updates in real-time
            _streamingEl: null,

            startStreaming() {
                if (!this.messages) return;
                this.removeThinking();
                const msg = document.createElement('div');
                msg.className = 'tp-msg assistant tp-streaming';
                const now = new Date();
                const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
                msg.innerHTML = `<div class="tp-meta">${this.agentName} · ${time}</div><div class="tp-text"></div>`;
                this.messages.appendChild(msg);
                this.messages.scrollTop = this.messages.scrollHeight;
                this._streamingEl = msg;
                if (!this.isVisible && this.unreadDot) {
                    this.unreadDot.style.display = 'block';
                }
            },

            updateStreaming(text) {
                if (!this._streamingEl) return;
                const textEl = this._streamingEl.querySelector('.tp-text');
                if (textEl) {
                    let html = text
                        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/`([^`]+)`/g, '<code>$1</code>')
                        .replace(/\n/g, '<br>');
                    textEl.innerHTML = html;
                }
                if (this.messages) this.messages.scrollTop = this.messages.scrollHeight;
            },

            finalizeStreaming(text) {
                if (this._streamingEl) {
                    this._streamingEl.classList.remove('tp-streaming');
                    if (text) this.updateStreaming(text);
                    this._streamingEl = null;
                } else if (text) {
                    this.addMessage('assistant', text);
                }
            },

            // --- Text input + file upload ---
            _pendingFile: null,
            _pendingBulkFiles: null,

            async sendText() {
                const input = document.getElementById('tp-text-input');
                const text = input?.value?.trim() || '';

                // Need either text or a file
                if (!text && !this._pendingFile && !this._pendingBulkFiles) return;

                // Grab and clear input + file immediately so repeat sends have nothing to send
                if (input) input.value = '';
                const stagedFile = this._pendingFile;
                const stagedBulkFiles = this._pendingBulkFiles;
                this._pendingFile = null;
                this._pendingBulkFiles = null;

                let messageToSend = text;

                // Bulk upload — upload all files, send summary to AI
                if (stagedBulkFiles && stagedBulkFiles.length > 0) {
                    try {
                        this.addMessage('assistant', `Uploading ${stagedBulkFiles.length} files...`);
                        const results = await this.uploadBulkFiles(stagedBulkFiles);
                        const succeeded = results.filter(r => !r.error);
                        const failed = results.filter(r => r.error);

                        let parts = [];
                        if (succeeded.length > 0) {
                            parts.push(`[BULK UPLOAD: ${succeeded.length} files uploaded successfully]`);
                            for (const r of succeeded) {
                                if (r.content_preview) {
                                    parts.push(`\n--- ${r.original_name} (${r.url}) ---\n${r.content_preview}\n--- End ${r.original_name} ---`);
                                } else {
                                    parts.push(`\n- ${r.original_name} → ${r.url} (${r.type})`);
                                }
                            }
                        }
                        if (failed.length > 0) {
                            parts.push(`\n[FAILED: ${failed.length} files]`);
                            for (const r of failed) {
                                parts.push(`\n- ${r.name}: ${r.error}`);
                            }
                        }
                        messageToSend = parts.join('') + (text ? `\n\n${text}` : '');
                    } catch (err) {
                        console.error('Bulk upload failed:', err);
                        this.addMessage('assistant', `Bulk upload failed: ${err.message}`);
                        return;
                    }
                    this.clearFile();
                }
                // Single file upload
                else if (stagedFile) {
                    try {
                        const result = await this.uploadFile(stagedFile.file);
                        if (result.type === 'image') {
                            // Pass image path so server can run vision analysis
                            this._pendingImagePath = result.path;
                            messageToSend = text || `What do you see in this image? (${result.original_name})`;
                        } else if (result.content_preview) {
                            messageToSend = `[USER ATTACHED FILE: ${result.original_name}, saved at ${result.path}]\n--- File contents ---\n${result.content_preview}\n--- End file ---\n${text}`;
                        } else {
                            messageToSend = `[USER ATTACHED FILE: ${result.original_name}, saved at ${result.path}] ${text}`;
                        }
                    } catch (err) {
                        console.error('File upload failed:', err);
                        this.addMessage('assistant', `Upload failed: ${err.message}`);
                        return;
                    }
                    this.clearFile();
                }

                if (!messageToSend.trim()) return;

                // Show panel if hidden
                if (!this.isVisible) this.show();

                // Send through the existing voice conversation path
                const imagePath = this._pendingImagePath || null;
                const imageThumbUrl = this._pendingImageThumbUrl || null;
                this._pendingImagePath = null;
                this._pendingImageThumbUrl = null;
                if (window.ModeManager?.clawdbotMode) {
                    window.ModeManager.clawdbotMode.sendMessage(messageToSend, { image_path: imagePath, imageUrl: imageThumbUrl });
                }
            },

            handleUpload(inputEl) {
                const files = inputEl?.files;
                if (!files || files.length === 0) return;
                if (files.length === 1) {
                    this._stageFile(files[0]);
                } else {
                    this._stageBulkFiles(Array.from(files));
                }
            },

            clearFile() {
                this._pendingFile = null;
                this._pendingBulkFiles = null;
                const preview = document.getElementById('tp-file-preview');
                if (preview) preview.style.display = 'none';
                const fileInput = document.getElementById('tp-file-input');
                if (fileInput) fileInput.value = '';
            },

            async uploadFile(file) {
                const formData = new FormData();
                formData.append('file', file);

                const serverUrl = window.CONFIG?.serverUrl || '';
                let resp;
                try {
                    resp = await fetch(`${serverUrl}/api/upload`, {
                        method: 'POST',
                        body: formData,
                        credentials: 'same-origin'
                    });
                } catch (networkErr) {
                    console.error('Upload network error:', networkErr, 'file:', file.name, 'size:', file.size);
                    throw new Error(`Network error uploading ${file.name} (${(file.size / 1024 / 1024).toFixed(1)}MB)`);
                }

                if (!resp.ok) {
                    const errBody = await resp.text().catch(() => '');
                    console.error('Upload server error:', resp.status, errBody, 'file:', file.name);
                    let msg;
                    try { msg = JSON.parse(errBody).error; } catch(e) {}
                    throw new Error(msg || `Server error ${resp.status} uploading ${file.name}`);
                }

                return await resp.json();
            },

            async uploadBulkFiles(files) {
                const results = [];
                // Upload sequentially to avoid overwhelming the server
                for (const file of files) {
                    try {
                        const result = await this.uploadFile(file);
                        results.push(result);
                    } catch (err) {
                        results.push({ error: err.message, name: file.name });
                    }
                }
                return results;
            }
        };

        // ===== ACTION CONSOLE =====
        // Shows agent actions (tool calls, lifecycle events, system events)
        window.ActionConsole = {
            panel: null,
            entries: null,
            button: null,
            unreadDot: null,
            isVisible: false,

            init() {
                this.panel = document.getElementById('action-console');
                this.entries = document.getElementById('action-entries');
                this.button = document.getElementById('console-button');
                this.unreadDot = document.getElementById('console-unread');
                console.log('Action Console initialized');
            },

            show() {
                if (this.panel) {
                    this.panel.style.display = 'flex';
                    this.isVisible = true;
                    this.button?.classList.add('active');
                    if (this.unreadDot) this.unreadDot.style.display = 'none';
                    if (this.entries) this.entries.scrollTop = this.entries.scrollHeight;
                }
            },

            hide() {
                if (this.panel) {
                    this.panel.style.display = 'none';
                    this.isVisible = false;
                    this.button?.classList.remove('active');
                }
            },

            toggle() {
                if (this.isVisible) this.hide(); else this.show();
            },

            clear() {
                if (this.entries) this.entries.innerHTML = '';
            },

            _formatTime(ts) {
                const d = ts ? new Date(ts * 1000) : new Date();
                return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
            },

            _icons: {
                tool: '🔧',
                lifecycle: '⚡',
                system: '📡',
                error: '❌',
                chat: '💬',
                tts: '🔊',
                stt: '🎤',
            },

            addEntry(type, text, detail = null, ts = null) {
                if (!this.entries) return;
                const entry = document.createElement('div');
                entry.className = `ac-entry ${type}`;
                const icon = this._icons[type] || '•';
                const time = this._formatTime(ts);
                let html = `<span class="ac-ts">${time}</span><span class="ac-icon">${icon}</span>${this._escHtml(text)}`;
                if (detail) {
                    html += `<span class="ac-detail">${this._escHtml(detail)}</span>`;
                }
                entry.innerHTML = html;
                this.entries.appendChild(entry);
                this.entries.scrollTop = this.entries.scrollHeight;

                // Trim to last 200 entries
                while (this.entries.children.length > 200) {
                    this.entries.removeChild(this.entries.firstChild);
                }

                // Show unread dot if panel is closed
                if (!this.isVisible && this.unreadDot) {
                    this.unreadDot.style.display = 'block';
                }
            },

            // Process actions array from API response
            processActions(actions) {
                if (!actions || !actions.length) return;
                for (const action of actions) {
                    if (action.type === 'tool') {
                        const phase = action.phase === 'result' ? '✓' : '→';
                        // Build a readable detail line from the input parameters
                        let detail = '';
                        if (action.phase === 'result') {
                            // For results, show first meaningful line of output
                            const raw = action.result || '';
                            detail = raw.split('\n').filter(l => l.trim())[0]?.slice(0, 120) || raw.slice(0, 120);
                        } else if (action.input && typeof action.input === 'object' && Object.keys(action.input).length) {
                            const inp = action.input;
                            // Extract the most useful field for display
                            detail = inp.command || inp.path || inp.file_path || inp.filePath ||
                                     inp.query || inp.url || inp.pattern ||
                                     inp.oldText?.slice?.(0, 60) ||
                                     inp.content?.slice?.(0, 60) ||
                                     Object.values(inp)[0]?.toString?.()?.slice(0, 120) || '';
                        }
                        this.addEntry('tool', `${phase} Tool: ${action.name}`, detail, action.ts);
                    } else if (action.type === 'lifecycle') {
                        const label = action.phase === 'start' ? 'Agent started processing' :
                                      action.phase === 'end' ? 'Agent finished' : `Lifecycle: ${action.phase}`;
                        this.addEntry('lifecycle', label, null, action.ts);
                    } else if (action.type === 'subagent') {
                        const label = action.phase === 'spawning' ? '🚀 Subagent spawning...' :
                                      action.phase === 'start' ? '🔄 Subagent running...' :
                                      action.phase === 'end' ? '✅ Subagent completed' : `Subagent: ${action.phase}`;
                        this.addEntry('system', label, action.sessionKey || '', action.ts);
                    }
                }
            },

            async resetSession() {
                try {
                    const resp = await fetch(`${CONFIG.serverUrl}/api/session/reset`, { method: 'POST' });
                    const data = await resp.json();
                    this.addEntry('system', `Session reset: ${data.old} → ${data.new}`);
                    this.addEntry('system', 'Next response will be slow (cold start)');
                } catch (e) {
                    this.addEntry('error', `Session reset failed: ${e.message}`);
                }
            },

            async showSessionInfo() {
                try {
                    const resp = await fetch(`${CONFIG.serverUrl}/api/session`);
                    const data = await resp.json();
                    this.addEntry('system', `Session: ${data.sessionKey}`);
                    if (data.consecutiveEmpty > 0) {
                        this.addEntry('system', `Empty responses: ${data.consecutiveEmpty}/3 before auto-reset`);
                    }
                } catch (e) {
                    this.addEntry('error', `Session info failed: ${e.message}`);
                }
            },

            _escHtml(s) {
                if (!s) return '';
                return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            }
        };

        // ===== FACE RECOGNITION MODULE =====
        window.FaceID = {
            async identify() {
                const statusEl = document.getElementById('face-id-status');
                const btn = document.getElementById('face-id-btn');
                const video = document.getElementById('camera-video');

                if (!video || !video.srcObject) {
                    statusEl.textContent = 'Camera not active';
                    statusEl.className = 'face-id-status';
                    return;
                }

                btn.disabled = true;
                statusEl.textContent = 'Identifying...';

                try {
                    // Capture frame from camera
                    const canvas = document.getElementById('capture-canvas');
                    canvas.width = video.videoWidth || 640;
                    canvas.height = video.videoHeight || 480;
                    canvas.getContext('2d').drawImage(video, 0, 0);
                    const imageData = canvas.toDataURL('image/jpeg', 0.8);

                    const response = await fetch(`${CONFIG.serverUrl}/api/identify`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: imageData })
                    });
                    const data = await response.json();

                    if (data.name && data.name !== 'unknown') {
                        statusEl.textContent = `${data.name} (${data.confidence}%)`;
                        statusEl.className = 'face-id-status identified';
                    } else {
                        statusEl.textContent = data.message || 'Not recognized';
                        statusEl.className = 'face-id-status';
                    }
                } catch (error) {
                    statusEl.textContent = 'Error: ' + error.message;
                    statusEl.className = 'face-id-status';
                } finally {
                    btn.disabled = false;
                }
            },

            async loadKnownFaces() {
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/faces`);
                    const data = await response.json();
                    const faces = data.faces || [];
                    const el = document.getElementById('face-id-known');
                    if (el) el.textContent = faces.length ? `Known: ${faces.map(f=>f.name).join(', ')}` : 'No faces saved';
                } catch (e) {
                    // ignore
                }
            }
        };

        // Load known faces on init
        window.FaceID.loadKnownFaces();

        // ===== FACE PANEL (Edge Tab) =====
        window.FacePanel = {
            panel: document.getElementById('face-panel'),
            button: document.getElementById('face-button'),
            isOpen: false,

            toggle() {
                this.isOpen ? this.hide() : this.show();
            },

            show() {
                this.panel.classList.add('open');
                this.button.classList.add('active');
                this.isOpen = true;
                this.loadFaces();
            },

            hide() {
                this.panel.classList.remove('open');
                this.button.classList.remove('active');
                this.isOpen = false;
            },

            async loadFaces() {
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/faces`);
                    const data = await response.json();
                    const faces = data.faces || [];
                    const list = document.getElementById('fp-face-list');
                    if (faces.length === 0) {
                        list.innerHTML = '<li style="color:var(--text-dim)">No faces registered</li>';
                        return;
                    }
                    list.innerHTML = faces.map(f => {
                        const count = f.photo_count || 0;
                        return `<li><span>${f.name}</span><span class="confidence" style="color:var(--text-dim)">${count} photo${count !== 1 ? 's' : ''}</span>` +
                               `<button onclick="FacePanel.deleteFace('${f.name}')" style="margin-left:8px;font-size:10px;padding:2px 6px;background:transparent;border:1px solid var(--red);color:var(--red);border-radius:3px;cursor:pointer">&#x2715;</button></li>`;
                    }).join('');
                } catch (e) {
                    document.getElementById('fp-face-list').innerHTML = '<li style="color:var(--text-dim)">Could not load faces</li>';
                }
            },

            async deleteFace(name) {
                if (!confirm(`Remove face profile for "${name}"?`)) return;
                try {
                    await fetch(`${CONFIG.serverUrl}/api/faces/${encodeURIComponent(name)}`, { method: 'DELETE' });
                    this.loadFaces();
                } catch (e) { console.error('Delete face error:', e); }
            },

            async identify() {
                const statusEl = document.getElementById('fp-status');
                const video = document.getElementById('camera-video');
                if (!video || !video.srcObject) {
                    statusEl.textContent = 'Turn on camera first';
                    return;
                }
                statusEl.textContent = 'Identifying...';
                try {
                    const canvas = document.getElementById('capture-canvas');
                    canvas.width = video.videoWidth || 640;
                    canvas.height = video.videoHeight || 480;
                    canvas.getContext('2d').drawImage(video, 0, 0);
                    const imageData = canvas.toDataURL('image/jpeg', 0.8);

                    const response = await fetch(`${CONFIG.serverUrl}/api/identify`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: imageData })
                    });
                    const data = await response.json();
                    if (data.name && data.name !== 'unknown') {
                        statusEl.textContent = `Recognized: ${data.name} (${data.confidence}%)`;
                        statusEl.style.color = 'var(--green)';
                    } else {
                        statusEl.textContent = data.message || 'Not recognized';
                        statusEl.style.color = 'var(--cyan)';
                    }
                } catch (error) {
                    statusEl.textContent = 'Error: ' + error.message;
                    statusEl.style.color = 'var(--red)';
                }
            },

            async capture() {
                const video = document.getElementById('camera-video');
                const nameInput = document.getElementById('fp-name-input');
                const statusEl = document.getElementById('fp-status');
                const name = nameInput.value.trim();
                if (!name) { statusEl.textContent = 'Enter a name first'; return; }
                if (!video || !video.srcObject) { statusEl.textContent = 'Turn on camera first'; return; }

                const canvas = document.getElementById('capture-canvas');
                canvas.width = video.videoWidth || 640;
                canvas.height = video.videoHeight || 480;
                canvas.getContext('2d').drawImage(video, 0, 0);
                const imageData = canvas.toDataURL('image/jpeg', 0.8);
                await this._savePhoto(name, imageData);
            },

            async handleUpload(input) {
                const nameInput = document.getElementById('fp-name-input');
                const statusEl = document.getElementById('fp-status');
                const name = nameInput.value.trim();
                if (!name) { statusEl.textContent = 'Enter a name first'; input.value = ''; return; }

                for (const file of input.files) {
                    const reader = new FileReader();
                    reader.onload = async (e) => {
                        await this._savePhoto(name, e.target.result);
                    };
                    reader.readAsDataURL(file);
                }
                input.value = '';
            },

            async _savePhoto(name, imageData) {
                const statusEl = document.getElementById('fp-status');
                statusEl.textContent = 'Saving...';
                try {
                    const response = await fetch(`${CONFIG.serverUrl}/api/faces/${encodeURIComponent(name)}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ image: imageData })
                    });
                    const data = await response.json();
                    statusEl.textContent = data.message || 'Photo saved';
                    statusEl.style.color = 'var(--green)';
                    this.loadFaces();
                    window.FaceID.loadKnownFaces();
                } catch (error) {
                    statusEl.textContent = 'Error: ' + error.message;
                    statusEl.style.color = 'var(--red)';
                }
            }
        };

        // ===== MODE SELECTOR (mode-button) =====
        window.ModeSelector = {
            button:      document.getElementById('mode-button'),
            picker:      document.getElementById('mode-picker'),
            currentMode: 'normal',   // 'normal' | 'listen' | 'a2a'
            isOpen:      false,

            toggle(event) {
                this.isOpen ? this.close() : this.open(event);
            },

            open(event) {
                this.isOpen = true;
                this.picker.classList.add('open');
                this.button.classList.add('active');
                // Position vertically near the button
                if (event) {
                    const rect = this.button.getBoundingClientRect();
                    const top = rect.top + rect.height / 2;
                    const viewH = window.innerHeight;
                    const pickerH = 160; // approx height
                    const clampedTop = Math.min(Math.max(top, pickerH / 2 + 8), viewH - pickerH / 2 - 8);
                    this.picker.style.top = clampedTop + 'px';
                    this.picker.style.transform = 'translateY(-50%)';
                }
                // Close on outside click
                setTimeout(() => document.addEventListener('click', this._outsideClick, { once: true }), 0);
            },

            close() {
                this.isOpen = false;
                this.picker.classList.remove('open');
                if (this.currentMode === 'normal') this.button.classList.remove('active');
                document.removeEventListener('click', this._outsideClick);
            },

            _outsideClick: null,

            select(mode) {
                this.currentMode = mode;
                this.close();

                // Update checkmarks
                ['normal', 'listen', 'a2a'].forEach(m => {
                    const el = document.getElementById('mode-check-' + m);
                    const btn = document.getElementById('mode-opt-' + m);
                    if (el) el.textContent = m === mode ? '✓' : '';
                    if (btn) btn.classList.toggle('active', m === mode);
                });

                // Update button icon to reflect mode
                const icons = { normal: '🎛️', listen: '👂', a2a: '🤝' };
                const iconEl = document.getElementById('mode-button-icon');
                if (iconEl) iconEl.textContent = icons[mode] || '🎛️';

                // Keep button highlighted when non-normal mode is active
                this.button.classList.toggle('active', mode !== 'normal');

                // Activate mode panels
                if (mode === 'a2a') {
                    window.AgentToAgentPanel?.show();
                } else {
                    window.AgentToAgentPanel?.hide();
                }

                if (mode === 'listen') {
                    window.ListenPanel?.open();
                } else {
                    window.ListenPanel?.close();
                }

                // Apply STT behaviour for listen mode
                const stt = window._sttInstance;
                if (stt) {
                    if (mode === 'listen') {
                        // Wrap onResult: capture into transcript instead of auto-sending to agent
                        if (!stt._origOnResultListen) {
                            stt._origOnResultListen = stt.onResult;
                            stt.onResult = (text) => {
                                // Accumulate into listen buffer — do NOT send to agent
                                window.ListenPanel?.addFinal(text);
                                // Reset processing flag so STT keeps listening
                                stt.isProcessing = false;
                                stt.accumulatedText = '';
                            };
                        }
                        // Start mic if not already running (works without an active call)
                        if (!stt.isListening) {
                            this._listenStartedSTT = true;
                            stt.start().then(ok => {
                                if (ok) console.log('ListenMode: mic started');
                            });
                        } else {
                            this._listenStartedSTT = false;
                        }
                    } else if (stt._origOnResultListen) {
                        // Leaving listen mode — restore original callback
                        stt.onResult = stt._origOnResultListen;
                        delete stt._origOnResultListen;
                        if (stt.onInterim) delete stt.onInterim;
                        // Stop mic if we started it ourselves and no call is active
                        if (this._listenStartedSTT && !ModeManager?.clawdbotMode?._voiceActive) {
                            stt.stop();
                            this._listenStartedSTT = false;
                            console.log('ListenMode: mic stopped (no active call)');
                        }
                    }
                }

                console.log('Conversation mode:', mode);
            }
        };
        // Bind outside-click closure after object is created
        window.ModeSelector._outsideClick = (e) => {
            if (!window.ModeSelector.picker.contains(e.target) &&
                e.target !== window.ModeSelector.button) {
                window.ModeSelector.close();
            }
        };

        // ===== AGENT-TO-AGENT PANEL =====
        window.AgentToAgentPanel = {
            panel: document.getElementById('a2a-panel'),
            isOpen: false,
            role:   'default',

            show() {
                this.isOpen = true;
                this.panel.classList.add('open');
            },

            hide() {
                this.isOpen = false;
                this.panel.classList.remove('open');
            },

            setRole(role) {
                this.role = role;
                console.log('A2A role set to:', role);
            },

            setTurnStatus(who, state) {
                const dot   = document.getElementById('a2a-dot');
                const label = document.getElementById('a2a-turn-label');
                if (dot) dot.className = 'a2a-dot ' + (state || '');
                if (label) label.textContent = who || 'Idle';
            },

            appendMessage(who, text) {
                const transcript = document.getElementById('a2a-transcript');
                if (!transcript) return;
                const empty = transcript.querySelector('.a2a-transcript-empty');
                if (empty) empty.remove();
                const msg = document.createElement('div');
                msg.className = 'a2a-msg';
                const whoEl = document.createElement('div');
                whoEl.className = 'a2a-msg-who ' + (who || '');
                whoEl.textContent = who === 'human' ? '👤 Human' : who === 'pgai' ? '🤖 Agent' : '🤖 Assistant';
                const textEl = document.createElement('div');
                textEl.className = 'a2a-msg-text';
                textEl.textContent = text;
                msg.appendChild(whoEl);
                msg.appendChild(textEl);
                transcript.appendChild(msg);
                transcript.scrollTop = transcript.scrollHeight;
            }
        };

        // ===== LISTEN MODE PANEL =====
        // Shows live transcription in a right-side panel.
        // onResult is intercepted to capture text into the buffer instead of sending to agent.
        // "Send as Context" sends the full buffer to the agent as a long voice prompt.
        window.ListenPanel = {
            panel:       document.getElementById('listen-panel'),
            _buffer:     '',   // finalized text
            _interim:    '',   // in-progress speech
            _shownLen:   0,    // length of last thing rendered — never shrink below this

            _render() {
                const container = document.getElementById('listen-transcript');
                if (!container) return;

                const full = this._buffer + (this._interim ? (this._buffer ? ' ' : '') + this._interim : '');

                if (!full) {
                    container.innerHTML = '<span id="listen-empty" style="color:var(--text-dim);font-style:italic">Start speaking - transcript will appear here</span>';
                    this._shownLen = 0;
                    return;
                }

                // Append-only: never let the display shrink (prevents flicker when
                // a phrase finalizes and the next interim hasn't arrived yet)
                if (full.length < this._shownLen) return;
                this._shownLen = full.length;

                if (container.firstChild?.nodeType === 3) {
                    container.firstChild.nodeValue = full;
                } else {
                    container.innerHTML = '';
                    container.appendChild(document.createTextNode(full));
                }
                container.scrollTop = container.scrollHeight;
            },

            open() {
                this.panel?.classList.add('open');
                const stt = window._sttInstance;
                if (stt) {
                    stt.onListenFinal = (text) => this.appendWords(text);
                    stt.onInterim     = (text) => this.setInterim(text);
                }
            },

            close() {
                this.panel?.classList.remove('open');
                const stt = window._sttInstance;
                if (stt) { delete stt.onListenFinal; delete stt.onInterim; }
                this._interim = '';
            },

            appendWords(text) {
                if (!text?.trim()) return;
                this._buffer += (this._buffer ? ' ' : '') + text.trim();
                this._interim = '';
                this._render();
                this._updateMeta();
            },

            addFinal(text) { /* no-op */ },

            setInterim(text) {
                this._interim = text || '';
                this._render();
                // Update word count live so it feels responsive
                const container = document.getElementById('listen-transcript');
                if (container) container.scrollTop = container.scrollHeight;
            },

            clear() {
                this._buffer   = '';
                this._interim  = '';
                this._shownLen = 0;
                this._render();
                this._updateMeta();
            },

            _getTitle() {
                return (document.getElementById('listen-title')?.value || '').trim() || 'Untitled';
            },

            _setStatus(msg, type) {
                const el = document.getElementById('listen-save-status');
                if (!el) return;
                el.textContent = msg;
                el.className = 'listen-save-status' + (type ? ' ' + type : '');
                if (type === 'ok') setTimeout(() => { if (el.textContent === msg) el.textContent = ''; }, 3000);
            },

            _updateMeta() {
                const words = this._buffer.trim() ? this._buffer.trim().split(/\s+/).length : 0;
                const wc = document.getElementById('listen-word-count');
                if (wc) wc.textContent = words + (words === 1 ? ' word' : ' words');
                const hasText = words > 0;
                const sendBtn = document.getElementById('listen-send-btn');
                const saveBtn = document.getElementById('listen-save-btn');
                const talkBtn = document.getElementById('listen-talk-btn');
                if (sendBtn) sendBtn.disabled = !hasText;
                if (saveBtn) saveBtn.disabled = !hasText;
                if (talkBtn) talkBtn.disabled = !hasText;
            },

            async save() {
                if (!this._buffer.trim()) return;
                const saveBtn = document.getElementById('listen-save-btn');
                if (saveBtn) saveBtn.disabled = true;
                this._setStatus('Saving…', '');
                try {
                    const res = await fetch('/api/transcripts/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: this._getTitle(), text: this._buffer.trim() }),
                    });
                    const data = await res.json();
                    if (data.saved) {
                        this._setStatus('✓ Saved — ' + data.path, 'ok');
                        console.log('ListenPanel: saved to', data.path);
                    } else {
                        this._setStatus('Save failed: ' + (data.error || 'unknown'), 'err');
                    }
                } catch (e) {
                    this._setStatus('Save error: ' + e.message, 'err');
                } finally {
                    if (saveBtn) saveBtn.disabled = !this._buffer.trim();
                }
            },

            // Send to agent without saving
            async sendOnly() {
                if (!this._buffer.trim()) return;
                const btn = document.getElementById('listen-send-btn');
                if (btn) { btn.disabled = true; btn.classList.add('sending'); btn.textContent = '⏳ Sending…'; }

                const msg = 'Here\'s some transcribed context I wanted to share with you:\n\n' + this._buffer.trim();
                try {
                    ModeManager?.clawdbotMode?.sendMessage(msg);
                    window.ModeSelector?.select('normal');
                    console.log('ListenPanel: sent to agent');
                    this.clear();
                } catch (e) {
                    console.error('ListenPanel: send failed', e);
                    this._setStatus('Send failed: ' + e.message, 'err');
                    if (btn) { btn.disabled = false; btn.classList.remove('sending'); btn.textContent = '📤 Send'; }
                }
            },

            // Save to server AND start voice call
            async saveAndTalk() {
                if (!this._buffer.trim()) return;
                const btn = document.getElementById('listen-talk-btn');
                if (btn) { btn.disabled = true; btn.classList.add('saving'); btn.textContent = '⏳ Saving…'; }

                // Save first
                try {
                    const res = await fetch('/api/transcripts/save', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: this._getTitle(), text: this._buffer.trim() }),
                    });
                    const data = await res.json();
                    if (data.saved) {
                        this._setStatus('✓ Saved — ' + data.path, 'ok');
                        console.log('ListenPanel: saved to', data.path);
                    } else {
                        this._setStatus('Save failed: ' + (data.error || 'unknown'), 'err');
                        if (btn) { btn.disabled = false; btn.classList.remove('saving'); btn.textContent = '📞 Save+Talk'; }
                        return;
                    }
                } catch (e) {
                    this._setStatus('Save error: ' + e.message, 'err');
                    if (btn) { btn.disabled = false; btn.classList.remove('saving'); btn.textContent = '📞 Save+Talk'; }
                    return;
                }

                // Switch to normal mode and start voice call
                try {
                    window.ModeSelector?.select('normal');
                    // Small delay to let mode switch complete
                    await new Promise(r => setTimeout(r, 100));
                    // Start the voice call
                    await ModeManager?.toggleVoice();
                    console.log('ListenPanel: voice call started');
                    this.clear();
                } catch (e) {
                    console.error('ListenPanel: failed to start call', e);
                    this._setStatus('Failed to start call: ' + e.message, 'err');
                    if (btn) { btn.disabled = false; btn.classList.remove('saving'); btn.textContent = '📞 Save+Talk'; }
                }
            }
        };

        // ===== PUSH-TO-TALK BUTTON =====
        // Tap  (<400ms): toggle PTT mode on/off
        //   OFF (default) — normal auto-detect STT
        //   ON  (orange)  — auto-STT blocked, mic only opens on hold
        // Hold (≥400ms) while ON: mic live (red), release → send
        window.PTTButton = {
            button:      document.getElementById('ptt-button'),
            pttMode:     false,
            _holding:    false,
            _pressStart: 0,
            _holdTimer:  null,

            // Always fetch fresh — ModeManager.clawdbotMode.stt is the canonical STT instance
            _getSTT() {
                return ModeManager?.clawdbotMode?.stt || window._sttInstance || null;
            },

            init() {
                const btn = this.button;
                if (!btn) return;

                btn.addEventListener('pointerdown', (e) => {
                    e.preventDefault();
                    btn.setPointerCapture(e.pointerId);
                    this._pressStart = Date.now();

                    if (this.pttMode) {
                        this._holdTimer = setTimeout(() => {
                            this._holding = true;
                            this._activateMic();
                        }, 400);
                    }
                });

                btn.addEventListener('pointerup', () => {
                    clearTimeout(this._holdTimer);
                    const duration = Date.now() - this._pressStart;
                    if (this._holding) {
                        this._holding = false;
                        this._releaseMic();
                    } else if (duration < 400) {
                        this._toggleMode();
                    }
                });

                btn.addEventListener('pointercancel', () => {
                    clearTimeout(this._holdTimer);
                    if (this._holding) { this._holding = false; this._releaseMic(); }
                });

                btn.addEventListener('pointerleave', (e) => {
                    // On mobile, pointerleave fires when finger shifts slightly during tap.
                    // Only cancel if we DON'T have pointer capture (setPointerCapture prevents this
                    // on most browsers, but iOS Safari can be inconsistent).
                    if (this._holding && !btn.hasPointerCapture?.(e.pointerId)) {
                        clearTimeout(this._holdTimer);
                        this._holding = false;
                        this._releaseMic();
                    }
                });
            },

            _toggleMode() {
                this.pttMode = !this.pttMode;
                this.button.classList.toggle('ptt-mode', this.pttMode);
                void this.button.offsetHeight; // force repaint on iOS Safari (prevents stale paint after e.preventDefault on pointerdown)
                const stt = this._getSTT();

                if (this.pttMode) {
                    // --- PTT ON ---
                    if (stt) {
                        stt.pttMute(); // Stop recording, discard audio, set muted
                    }
                    // Stop wake word detector
                    if (window.wakeDetector?.isListening) {
                        this._wakeWasRunning = true;
                        window.wakeDetector.stop();
                        document.getElementById('wake-button')?.classList.remove('active', 'listening');
                    }
                    console.log('PTT: ON — mic muted, hold to talk');
                } else {
                    // --- PTT OFF ---
                    if (stt) {
                        // Clear any stale isProcessing state from a previous response
                        if (stt.isProcessing && !ModeManager?.clawdbotMode?._ttsPlaying) {
                            stt.isProcessing = false;
                            stt.accumulatedText = '';
                        }
                        stt.pttUnmute(); // Unmute and resume continuous listening
                    }
                    // Restore wake word detector
                    if (this._wakeWasRunning && window.wakeDetector?.isSupported()) {
                        window.wakeDetector.start();
                        this._wakeWasRunning = false;
                    }
                    console.log('PTT: OFF — mic unmuted, auto-detect restored');
                }
            },

            _activateMic() {
                this.button.classList.add('holding');
                const stt = this._getSTT();
                if (!stt) return;

                // ⚡ PTT interrupt: if TTS is playing, kill it immediately
                const cm = ModeManager?.clawdbotMode;
                if (window._interruptionEnabled && cm?._ttsPlaying) {
                    console.log('⚡ PTT interrupt — killing TTS');
                    cm.stopAudio();
                    cm._ttsPlaying = false;
                    if (cm._fetchAbortController) {
                        cm._fetchAbortController.abort();
                        cm._fetchAbortController = null;
                        // Tell server to abort the openclaw run (fire-and-forget)
                        console.warn('⛔ ABORT source: PTT interrupt');
                        const serverUrl = cm.config?.serverUrl || '';
                        fetch(`${serverUrl}/api/conversation/abort`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ source: 'ptt-interrupt' }),
                        }).catch(() => {});
                    }
                    // Also stop VoiceConversation TTS (separate audio path)
                    if (window._voiceConversation) {
                        window._voiceConversation.stopAudio?.();
                        if (window._voiceConversation._fetchAbortController) {
                            window._voiceConversation._fetchAbortController.abort();
                            window._voiceConversation._fetchAbortController = null;
                        }
                    }
                }

                // Start recording — pttActivate handles all state
                stt.pttActivate();
            },

            _releaseMic() {
                this.button.classList.remove('holding');
                const stt = this._getSTT();
                if (!stt) return;

                // Stop recording and force transcription — pttRelease handles all state
                // onstop handler will send to Groq and call onResult asynchronously
                stt.pttRelease();
            }
        };
        window.PTTButton.init();

        // ===== PTT HOTKEY =====
        // Global keyboard/mouse-button shortcut that activates PTT.
        // Independently of PTT mode toggle — always works while a call is active.
        // Side mouse buttons (back/forward thumb buttons) are supported.
        window.PTTHotkey = {
            _hotkey:    null,    // { type:'key'|'mouse', code:string|number, label:string }
            _capturing: false,
            _held:      false,

            init() {
                const saved = localStorage.getItem('ptt_hotkey');
                if (saved) {
                    try { this._hotkey = JSON.parse(saved); } catch(e) {}
                }
                this._updateUI();
                this._bindListeners();
            },

            capture() {
                this._capturing = true;
                const label = document.getElementById('ptt-hotkey-label');
                const status = document.getElementById('ptt-hotkey-status');
                if (label) label.textContent = 'Press a key or mouse button…';
                if (status) status.textContent = 'Esc to cancel';
                console.log('[PTTHotkey] capture mode — waiting for input');
            },

            clear() {
                this._hotkey = null;
                this._capturing = false;
                localStorage.removeItem('ptt_hotkey');
                this._updateUI();
                console.log('[PTTHotkey] cleared');
            },

            _set(hotkey) {
                this._capturing = false;
                this._hotkey = hotkey;
                localStorage.setItem('ptt_hotkey', JSON.stringify(hotkey));
                this._updateUI();
                console.log('[PTTHotkey] set to:', hotkey.label);
            },

            _updateUI() {
                const label  = document.getElementById('ptt-hotkey-label');
                const setBtn = document.getElementById('ptt-hotkey-set');
                const clrBtn = document.getElementById('ptt-hotkey-clear');
                const status = document.getElementById('ptt-hotkey-status');
                const key = this._hotkey;
                if (label)  label.textContent = key ? key.label : 'None';
                if (setBtn) setBtn.textContent = key ? 'Change' : 'Set';
                if (clrBtn) clrBtn.style.display = key ? '' : 'none';
                if (status) status.textContent  = key ? `Hold ${key.label} = PTT` : 'Hold hotkey = hold PTT button';
            },

            _press() {
                // Fires PTTButton._activateMic directly (works even if PTT mode toggle is off)
                window.PTTButton?._activateMic();
            },

            _release() {
                window.PTTButton?._releaseMic();
            },

            _bindListeners() {
                // ── Keyboard ──────────────────────────────────────────────────
                document.addEventListener('keydown', (e) => {
                    if (this._capturing) {
                        if (e.key === 'Escape') {
                            this._capturing = false;
                            this._updateUI();
                            return;
                        }
                        // Ignore bare modifiers as the hotkey
                        if (['Control','Alt','Shift','Meta'].includes(e.key)) return;
                        e.preventDefault();
                        const label = e.code === 'Space' ? 'Space'
                            : e.key.length === 1 ? e.key.toUpperCase()
                            : e.key;
                        this._set({ type: 'key', code: e.code, label });
                        return;
                    }
                    if (!this._held && !e.repeat &&
                        this._hotkey?.type === 'key' && e.code === this._hotkey.code) {
                        e.preventDefault();
                        this._held = true;
                        this._press();
                    }
                });

                document.addEventListener('keyup', (e) => {
                    if (this._held &&
                        this._hotkey?.type === 'key' && e.code === this._hotkey.code) {
                        this._held = false;
                        this._release();
                    }
                });

                // ── Mouse buttons ─────────────────────────────────────────────
                // button 0 = left (skip — needed for UI), 1 = middle, 2 = right,
                // 3 = back (thumb), 4 = forward (thumb)
                const MOUSE_NAMES = { 1: 'Middle Button', 2: 'Right Button', 3: 'Mouse Back', 4: 'Mouse Forward' };

                // capture:true — runs before any element handler, can't be blocked by stopPropagation
                window.addEventListener('mousedown', (e) => {
                    if (this._capturing) {
                        if (e.button === 0) return; // left click — ignore (needed to click UI)
                        e.preventDefault();
                        const label = MOUSE_NAMES[e.button] || `Mouse ${e.button}`;
                        this._set({ type: 'mouse', code: e.button, label });
                        return;
                    }
                    if (!this._held &&
                        this._hotkey?.type === 'mouse' && e.button === this._hotkey.code) {
                        e.preventDefault(); // block browser back/forward navigation on btn 3/4
                        this._held = true;
                        this._press();
                    }
                }, { capture: true });

                window.addEventListener('mouseup', (e) => {
                    if (this._held &&
                        this._hotkey?.type === 'mouse' && e.button === this._hotkey.code) {
                        this._held = false;
                        this._release();
                    }
                }, { capture: true });

                // Also block auxclick (fires after mouseup for aux buttons) to prevent
                // browser back/forward navigation triggering after the PTT release
                window.addEventListener('auxclick', (e) => {
                    if (this._hotkey?.type === 'mouse' && e.button === this._hotkey.code) {
                        e.preventDefault();
                    }
                }, { capture: true });

                // Safety: if window loses focus while held, release
                window.addEventListener('blur', () => {
                    if (this._held) {
                        this._held = false;
                        this._release();
                    }
                    if (this._capturing) {
                        this._capturing = false;
                        this._updateUI();
                    }
                });
            }
        };
        window.PTTHotkey.init();

        // ===== PROFILE RUNTIME WIRING =====
        // Applies all runtime-relevant profile fields whenever a profile is loaded or switched.
        // Called from QuickSettings.init() (on page load) and QuickSettings.switchAgent() (on switch).
        window._activeProfileData = null;
        window._interruptionEnabled = false;
        window._maxResponseChars = null;

        window.applyProfile = function(profile) {
            window._activeProfileData = profile;

            // 1. STT silence timeout — applied immediately if STT is live, else deferred to poll
            const stt = window._sttInstance;
            if (stt) {
                const ms = profile?.stt?.silence_timeout_ms;
                if (ms != null) {
                    stt.silenceDelayMs = ms;
                    console.log(`[Profile] stt.silenceDelayMs = ${ms}ms`);
                }
                const vt = profile?.stt?.vad_threshold;
                if (vt != null) {
                    stt.vadThreshold = vt;
                    console.log(`[Profile] stt.vadThreshold = ${vt}`);
                }
                const maxRec = profile?.stt?.max_recording_s;
                if (maxRec != null) {
                    stt.maxRecordingMs = maxRec * 1000;
                    console.log(`[Profile] stt.maxRecordingMs = ${maxRec * 1000}ms`);
                }
                const accMs = profile?.stt?.accumulation_delay_ms;
                if (accMs != null) {
                    stt.accumulationDelayMs = accMs;
                    console.log(`[Profile] stt.accumulationDelayMs = ${accMs}ms`);
                }
                // PTT default — auto-enable if profile says so
                if (profile?.stt?.ptt_default === true && window.PTTButton && !window.PTTButton.pttMode) {
                    window.PTTButton._setPTT(true);
                }
            }

            // 2. Mode picker — show/hide options based on profile.modes
            const modes = profile?.modes || {};
            ['normal', 'listen', 'a2a'].forEach(key => {
                const btn = document.getElementById('mode-opt-' + key);
                if (btn) btn.style.display = (modes[key] === false) ? 'none' : '';
            });
            // PTT button
            const pttBtn = document.getElementById('ptt-button');
            if (pttBtn) pttBtn.style.display = (modes.ptt === false) ? 'none' : '';

            // 3. UI theme preset (maps to CSS data attribute → color overrides)
            const preset = profile?.ui?.theme_preset || '';
            document.body.dataset.themePreset = preset;

            // 4. Mode badge
            const showBadge = profile?.ui?.show_mode_badge;
            const badgeText = profile?.ui?.mode_badge_text;
            let badge = document.getElementById('profile-mode-badge');
            if (!badge && showBadge) {
                badge = document.createElement('div');
                badge.id = 'profile-mode-badge';
                badge.className = 'profile-mode-badge';
                document.body.appendChild(badge);
            }
            if (badge) {
                badge.style.display = showBadge ? '' : 'none';
                if (badgeText) badge.textContent = badgeText;
            }

            // 5. Conversation flags stored for use in API calls and TTS player
            window._interruptionEnabled = profile?.conversation?.interruption_enabled === true;
            window._maxResponseChars   = profile?.conversation?.max_response_chars || null;

            // 6. Camera auth / identify-on-wake flags (read at wake-time from _activeProfileData)
            // These are read directly from window._activeProfileData in the wake callback —
            // no extra storage needed here.

            // 7. Wake words — update detector when profile overrides them
            if (window.wakeDetector) {
                const profileWords = profile?.stt?.wake_words;
                if (Array.isArray(profileWords) && profileWords.length > 0) {
                    window.wakeDetector.wakeWords = profileWords;
                } else if (profileWords === null || profileWords === undefined) {
                    // null = use platform default
                    window.wakeDetector.wakeWords = ['wake up'];
                }
                // [] = empty array means disable wake word (leave as-is, detector won't match anything)
                console.log(`[Profile] wakeWords = ${JSON.stringify(window.wakeDetector.wakeWords)}`);
            }

            // 8. TTS provider/voice — switch ProviderManager to match profile voice settings
            if (profile?.voice && window.providerManager) {
                const newProvider = profile.voice.tts_provider;
                const newVoice = profile.voice.voice_id;
                if (newProvider) {
                    window.providerManager.selectedProvider = newProvider;
                    if (newVoice) window.providerManager.currentVoice = newVoice;
                    // Update provider dropdown
                    const provSelect = document.getElementById('voice-provider-select');
                    if (provSelect) provSelect.value = newProvider;
                    // Rebuild voice dropdown (includes cloned voices)
                    window.providerManager.updateProviderStatus();
                    window.providerManager.updateVoiceUI();
                    // Update active voice conversation
                    if (window.voiceAgent?.setTTSProvider) {
                        window.voiceAgent.setTTSProvider(newProvider, newVoice || 'autumn');
                    }
                    console.log(`[Profile] TTS: ${newProvider} / ${newVoice}`);
                }
            }

            console.log(`[Profile] applied: ${profile?.id} | silence=${profile?.stt?.silence_timeout_ms ?? 'default'}ms | accumulation=${profile?.stt?.accumulation_delay_ms ?? 'default'}ms | interruption=${window._interruptionEnabled} | wakeWords=${JSON.stringify(profile?.stt?.wake_words)} | modes=${JSON.stringify(profile?.modes)} | tts=${profile?.voice?.tts_provider}/${profile?.voice?.voice_id}`);
        };

        // Expose STT instance — lives at ModeManager.clawdbotMode.stt
        // WebSpeechSTT has mute/PTT support built in — no monkey-patching needed.
        const _sttExposePoll = setInterval(() => {
            const stt = ModeManager?.clawdbotMode?.stt;
            if (stt && !stt._exposed) {
                window._sttInstance = stt;
                clearInterval(_sttExposePoll);
                stt._exposed = true;

                const _sttName = stt instanceof DeepgramStreamingSTT ? 'Deepgram Streaming' : stt instanceof DeepgramSTT ? 'Deepgram Nova-2' : stt instanceof GroqSTT ? 'Groq Whisper' : 'Chrome Web Speech';
                console.log(`STT exposed (${_sttName})`);
                // Apply any profile settings that were deferred (profile loaded before STT existed)
                if (window._activeProfileData) {
                    const ms = window._activeProfileData?.stt?.silence_timeout_ms;
                    if (ms != null) {
                        stt.silenceDelayMs = ms;
                        console.log(`[Profile] deferred stt.silenceDelayMs = ${ms}ms`);
                    }
                    const vt = window._activeProfileData?.stt?.vad_threshold;
                    if (vt != null) {
                        stt.vadThreshold = vt;
                        console.log(`[Profile] deferred stt.vadThreshold = ${vt}`);
                    }
                    const maxRec = window._activeProfileData?.stt?.max_recording_s;
                    if (maxRec != null) {
                        stt.maxRecordingMs = maxRec * 1000;
                        console.log(`[Profile] deferred stt.maxRecordingMs = ${maxRec * 1000}ms`);
                    }
                    const accMs = window._activeProfileData?.stt?.accumulation_delay_ms;
                    if (accMs != null) {
                        stt.accumulationDelayMs = accMs;
                        console.log(`[Profile] deferred stt.accumulationDelayMs = ${accMs}ms`);
                    }
                    if (window._activeProfileData?.stt?.ptt_default === true && window.PTTButton && !window.PTTButton.pttMode) {
                        window.PTTButton._setPTT(true);
                    }
                }
                // Wire callbacks if listen panel already open when STT is acquired
                if (window.ModeSelector?.currentMode === 'listen') {
                    stt.onListenFinal = (text) => window.ListenPanel?.appendWords(text);
                    stt.onInterim     = (text) => window.ListenPanel?.setInterim(text);
                }
            }
        }, 200);

        // Start initialization
        init().catch(console.error);
