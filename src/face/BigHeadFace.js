/**
 * BigHeadFace — Animated BigHead Billionaires character avatar.
 *
 * Renders a layered character (BODY → TEXTURE → OUTFIT → HEAD → MOUTH → EYES)
 * with real-time lip sync (amplitude → viseme mouth shapes) and mood-driven
 * eye expression swaps.  Assets loaded from the BHB GitHub Pages CDN.
 *
 * Follows the HaloSmokeFace global-script pattern:
 *   window.BigHeadFace.start(container)
 *   window.BigHeadFace.stop()
 *   window.BigHeadFace.setThinking(bool)
 *   window.BigHeadFace.setCharacter(config)
 */
window.BigHeadFace = (function () {
    'use strict';

    // ── Asset CDN ────────────────────────────────────────────────────────────
    const CDN = 'https://bhaleyart.github.io/BigHeadCharacterCooker';

    // ── Trait catalogs (exact filenames from CharacterCooker) ─────────────────
    const TRAITS = {
        BODY: ['Blank','Charcoal','High Voltage','Nebulous','Pinky','Shockwave','Tangerine','Turquoise','Woody','Frogger','Area 51','Dark Tone','Mid Tone','Light Tone','Jolly Roger','Cyber Punk','Talking Corpse','Day Tripper','Meat Lover','Golden God','Chrome Dome','Candy Gloss','Man On Fire','Water Boy','Icecream Man','Reptilian','Juiced Up','Toxic Waste','Love Potion','Pop Artist','Autopsy','Ghostly','Blue Screen','Networker'],
        HEAD: ['None','Antenna','Bandana Bro','Beanie','Blonde Beanie','Blonde Bun','Blue Bedhead','Brain Squid','Bravo','Brunette Beanie','Brunette Ponytail','Burger Crown','Captain Hat','Cat Hat','Chad Bandana','Cherry Sundae','Clown Wig','Fancy Hat','Fireman','Flame Princess','Fossilized','Gamer Girl','Ginger Ponytail','Heated','Horny Horns','Hunted','Jester','Kingly','Mad Hatter','Masked Up','Mohawk Blue','Mohawk Green','Mohawk Red','Mortricia','Outlaw','Overload','Patrol Cap','Pharaoh Hat','Pink Pigtails','Powdered Wig','Press Pass','Propeller','Rainbow Babe','Recon Helmet','Robin Hood','Santa Hat','Sewer Slime','Snapback Blue','Snapback Hippy','Snapback Red','Snapback Yellow','Sombrero','Spiritual','Surgeon','UwU Kitty','Valhalla Cap','Way Dizzy'],
        EYES: ['Curious','Alien','Annoyed','Demonic','Diamond','Dots','Grumpy','Hypnotized','Infuriated','Insect','Joy','Light Bright','Monocle','Ouchy','Paranoid','Possessed','Ruby Stare','Spider','Stare','Stoney Eyes','Sunglasses','Surprised','Tears','Deceased','Too Chill','VR Headset','3D Glasses','Blink','Stern'],
        MOUTH: ['Mmm','Simpleton','Stache','Creeper','Pierced','Fangs','Gold Teeth','Diamond Teeth','Birdy','Panic','Sss','Ahh','Ehh','Uhh','LLL','Rrr','Fff','Ooo','Thh','Eee','Haha','Rofl','Bean Frown','Bean Smile','Smirk','Bored','Gas Mask','Scuba'],
        OUTFIT: ['None','Blue Tee','Blueberry Dye','Degen Green','Degen Purple','Earthy Dye','Hodl Black','Hodl White','Locked Up','Moto-X','Orange Zip','Passion Dye','Pink Zip','Raider Ref','Red Tee','Smally Bigs','Yellow Tee','Blue Zip','Red Zip','White Zip','Hornet Zip','Ghostly Zip','Gold Jacket','Tuxedo','Thrashed','The Fuzz','Pin Striped','Designer Zip','Luxury Zip','Explorer','Power Armor','Shinobi','Thrilled','Trenches','Ski Jacket','Sled Jacket','Commando','Space Cadet','Burgler','Commandant','Golden Knight','Honey Bee','Necromancer','Paladin','Refined Suit','Sexy Jacket','Stoner Hoodie','The Duke','Rave Hoodie','Scuba suit temp','Burger Suit','Scrubs'],
        TEXTURE: ['None','Blood','Acid','Ink','Dart Frog Blue','Dart Frog Red','Dart Frog Yellow','Magical','Puzzled','Rug Life Ink','Pulverized'],
        BACKGROUNDS: ['None','Natural','Mania','Regal','Lavish','Sunflower','Snowflake','Bleach','Vibes','Burst','Aquatic','Passionate','Envious','Enlightened','Haunted','Cursed']
    };

    // ── Viseme mouth shapes for lip sync (amplitude → shape) ─────────────────
    const VISEME_SHAPES = ['Mmm', 'Eee', 'Ehh', 'Ahh'];   // closed → wide open
    const VISEME_THRESHOLDS = [0.03, 0.08, 0.15];           // amp breakpoints

    // ── Mood → Eye expression mapping ────────────────────────────────────────
    const MOOD_TO_EXPRESSION = {
        neutral:   'Stare',
        happy:     'Joy',
        sad:       'Ouchy',
        angry:     'Infuriated',
        thinking:  'Curious',
        surprised: 'Surprised',
        listening: 'Stern'
    };

    // ── SUBSET filename lookup (display name → subset prefix) ────────────────
    // SUBSET files are lowercase, no spaces, e.g. "Light Bright" → "lightbright"
    const SUBSET_PREFIX = {
        'Alien': 'alien', 'Demonic': 'demonic', 'Diamond': 'diamond',
        'Dots': 'dots', 'Hypnotized': 'hypnotized', 'Light Bright': 'lightbright',
        'Monocle': 'monocle', 'Possessed': 'possesed', // typo in source assets
        'Ruby Stare': 'ruby', 'Spider': 'spider', 'Stoney Eyes': 'stoneyeyes',
        'Sunglasses': 'sunglasses', '3D Glasses': '3dglasses',
        'Annoyed': 'annoyed', 'Deceased': 'deceased', 'Grumpy': 'grumpy',
        'Insect': 'insect', 'Paranoid': 'paranoid', 'Too Chill': 'toochill',
        'VR Headset': 'vrheadset'
    };

    // Which subset prefixes have which expressions
    const FULL_SUBSET = new Set([
        'alien','demonic','diamond','dots','hypnotized','lightbright',
        'monocle','possesed','ruby','spider','stoneyeyes','sunglasses','3dglasses'
    ]);
    const SUBSET_EXPRESSIONS = {
        'blink': true, 'curious': true, 'infuriated': true,
        'joy': true, 'ouchy': true, 'stern': true, 'surprised': true
    };
    // Partial subsets (only some expressions)
    const PARTIAL_SUBSET = {
        'annoyed':  new Set(['blink']),
        'deceased': new Set(['blink', 'ouchy']),
        'grumpy':   new Set(['ouchy']),
        'insect':   new Set(['ouchy']),
        'paranoid': new Set(['ouchy']),
        'toochill': new Set(['blink'])
    };

    // ── Default character ────────────────────────────────────────────────────
    const DEFAULT_CHARACTER = {
        BODY: 'Mid Tone',
        HEAD: 'Outlaw',
        EYES: 'Curious',
        MOUTH: 'Mmm',
        OUTFIT: 'Tuxedo',
        TEXTURE: 'None',
        BACKGROUNDS: 'None',
        female: false
    };

    // ── Render layer order (bottom → top) ────────────────────────────────────
    const LAYER_ORDER = ['BACKGROUNDS', 'BODY', 'TEXTURE', 'OUTFIT', 'HEAD', 'MOUTH', 'EYES'];

    // ── State ────────────────────────────────────────────────────────────────
    let _canvas = null, _ctx = null, _container = null, _raf = null;
    let _character = { ...DEFAULT_CHARACTER };
    let _currentMood = 'neutral';
    let _thinking = false;
    let _amplitude = 0;
    let _targetAmplitude = 0;
    let _currentViseme = 'Mmm';
    let _blinkTimer = null;
    let _isBlinking = false;
    let _blinkStart = 0;
    let _bobPhase = 0;
    let _lastTime = 0;

    // Image cache: key = url, value = HTMLImageElement (loaded)
    const _imgCache = {};
    // Currently loaded layer images (keyed by layer name)
    const _layers = {};
    // Expression eye images cache
    const _eyeExprCache = {};

    // ── Image loading ────────────────────────────────────────────────────────

    function _imgUrl(category, name) {
        // GIRL/ folder for female-only layers
        if (category === 'EYELASHES' || category === 'BREASTS') {
            return `${CDN}/GIRL/${encodeURIComponent(name)}.png`;
        }
        return `${CDN}/${category}/${encodeURIComponent(name)}.png`;
    }

    function _subsetUrl(prefix, expression) {
        return `${CDN}/EYES/SUBSET/${prefix}-${expression}.png`;
    }

    function _loadImage(url) {
        if (_imgCache[url]) return Promise.resolve(_imgCache[url]);
        return new Promise((resolve, reject) => {
            const img = new Image();
            img.crossOrigin = 'anonymous';
            img.onload = () => { _imgCache[url] = img; resolve(img); };
            img.onerror = () => reject(new Error(`Failed to load: ${url}`));
            img.src = url;
        });
    }

    async function _preloadCharacter(cfg) {
        const promises = [];

        for (const layer of LAYER_ORDER) {
            const name = cfg[layer];
            if (!name || name === 'None') { _layers[layer] = null; continue; }
            const url = _imgUrl(layer, name);
            promises.push(
                _loadImage(url)
                    .then(img => { _layers[layer] = img; })
                    .catch(() => { _layers[layer] = null; })
            );
        }

        // Preload female layers if needed
        if (cfg.female) {
            promises.push(
                _loadImage(_imgUrl('BREASTS', 'Breasts')).catch(() => null),
                _loadImage(_imgUrl('EYELASHES', 'Eyelashes')).catch(() => null)
            );
        }

        // Preload all viseme mouth shapes
        for (const shape of VISEME_SHAPES) {
            const url = _imgUrl('MOUTH', shape);
            promises.push(_loadImage(url).catch(() => null));
        }

        // Preload base eye expressions for mood mapping
        for (const expr of Object.values(MOOD_TO_EXPRESSION)) {
            const url = _imgUrl('EYES', expr);
            promises.push(_loadImage(url).catch(() => null));
        }
        // Also preload Blink
        promises.push(_loadImage(_imgUrl('EYES', 'Blink')).catch(() => null));

        // Preload SUBSET variants for the selected eye type
        const eyeName = cfg.EYES;
        const prefix = SUBSET_PREFIX[eyeName];
        if (prefix) {
            const exprList = FULL_SUBSET.has(prefix)
                ? Object.keys(SUBSET_EXPRESSIONS)
                : (PARTIAL_SUBSET[prefix] ? [...PARTIAL_SUBSET[prefix]] : []);
            for (const expr of exprList) {
                const url = _subsetUrl(prefix, expr);
                promises.push(
                    _loadImage(url)
                        .then(img => { _eyeExprCache[`${prefix}-${expr}`] = img; })
                        .catch(() => {})
                );
            }
            // Also load base subset
            const baseUrl = `${CDN}/EYES/SUBSET/${prefix}.png`;
            promises.push(
                _loadImage(baseUrl)
                    .then(img => { _eyeExprCache[`${prefix}-base`] = img; })
                    .catch(() => {})
            );
        }

        await Promise.allSettled(promises);
    }

    // ── Eye expression resolver ──────────────────────────────────────────────

    function _getEyeImage(mood) {
        const eyeName = _character.EYES;
        const prefix = SUBSET_PREFIX[eyeName];
        const expression = MOOD_TO_EXPRESSION[mood] || 'Stare';

        // If blinking, try subset blink first
        if (_isBlinking) {
            if (prefix) {
                const key = `${prefix}-blink`;
                if (_eyeExprCache[key]) return _eyeExprCache[key];
            }
            // Fall back to generic Blink
            const url = _imgUrl('EYES', 'Blink');
            return _imgCache[url] || _layers.EYES;
        }

        // Try subset expression (e.g. alien-joy)
        if (prefix) {
            const exprLower = expression.toLowerCase();
            const key = `${prefix}-${exprLower}`;
            if (_eyeExprCache[key]) return _eyeExprCache[key];

            // If no matching expression in subset, use subset base
            const baseKey = `${prefix}-base`;
            if (_eyeExprCache[baseKey]) return _eyeExprCache[baseKey];
        }

        // Fall back to generic expression image (e.g. Joy.png from EYES/)
        const url = _imgUrl('EYES', expression);
        if (_imgCache[url]) return _imgCache[url];

        // Ultimate fallback: the character's selected eye
        return _layers.EYES;
    }

    // ── Mouth viseme resolver ────────────────────────────────────────────────

    function _getMouthImage() {
        const url = _imgUrl('MOUTH', _currentViseme);
        return _imgCache[url] || _layers.MOUTH;
    }

    function _updateViseme(amp) {
        if (amp > VISEME_THRESHOLDS[2]) {
            _currentViseme = VISEME_SHAPES[3]; // Ahh
        } else if (amp > VISEME_THRESHOLDS[1]) {
            _currentViseme = VISEME_SHAPES[2]; // Ehh
        } else if (amp > VISEME_THRESHOLDS[0]) {
            _currentViseme = VISEME_SHAPES[1]; // Eee
        } else {
            _currentViseme = VISEME_SHAPES[0]; // Mmm
        }
    }

    // ── Audio amplitude extraction ───────────────────────────────────────────

    function _getAmplitude() {
        const an = window.audioAnalyser;
        if (!an) return 0;

        const td = new Uint8Array(an.fftSize || 2048);
        try { an.getByteTimeDomainData(td); } catch (_) { return 0; }

        let sum = 0;
        for (let i = 0; i < td.length; i++) {
            const v = (td[i] - 128) / 128;
            sum += v * v;
        }
        return Math.sqrt(sum / td.length);
    }

    // ── Blink scheduling ─────────────────────────────────────────────────────

    function _scheduleBlink() {
        _blinkTimer = setTimeout(() => {
            _isBlinking = true;
            _blinkStart = performance.now();
            // Blink lasts 150ms
            setTimeout(() => {
                _isBlinking = false;
                _scheduleBlink();
            }, 150);
        }, 2000 + Math.random() * 4000);
    }

    // ── Loading indicator ─────────────────────────────────────────────────────

    function _drawLoading(msg) {
        if (!_canvas || !_ctx) return;
        const w = _canvas.width, h = _canvas.height;
        _ctx.fillStyle = '#0a0f1a';
        _ctx.fillRect(0, 0, w, h);
        _ctx.fillStyle = '#64748b';
        _ctx.font = `${14 * (window.devicePixelRatio || 1)}px system-ui, sans-serif`;
        _ctx.textAlign = 'center';
        _ctx.fillText(msg || 'Loading...', w / 2, h / 2);
    }

    // ── Main render loop ─────────────────────────────────────────────────────

    function _render(now) {
        if (!_canvas || !_ctx) return;
        _raf = requestAnimationFrame(_render);

        const dt = Math.min(0.05, (now - _lastTime) / 1000);
        _lastTime = now;

        // Get audio amplitude
        const rawAmp = _getAmplitude();
        _targetAmplitude = Math.min(1, rawAmp * 4.5);
        _amplitude += (_targetAmplitude - _amplitude) * 0.3;

        // Update viseme from amplitude
        _updateViseme(_amplitude);

        // Subtle head bob when speaking
        _bobPhase += dt * (2 + _amplitude * 6);
        const bobY = _amplitude > 0.03 ? Math.sin(_bobPhase) * 3 * _amplitude : 0;

        // Breathing idle when not speaking
        const breathe = _amplitude < 0.03 ? Math.sin(now * 0.002) * 1.5 : 0;
        const totalBob = bobY + breathe;

        // Canvas sizing
        const rect = _canvas.getBoundingClientRect();
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const w = Math.floor(rect.width * dpr);
        const h = Math.floor(rect.height * dpr);
        if (_canvas.width !== w || _canvas.height !== h) {
            _canvas.width = w;
            _canvas.height = h;
        }

        // Dark background fill (always visible even before images load)
        _ctx.fillStyle = '#0a0f1a';
        _ctx.fillRect(0, 0, w, h);
        _ctx.imageSmoothingEnabled = true;
        _ctx.imageSmoothingQuality = 'high';

        // Draw full 1000x1000 character scaled to fit the canvas
        // with slight bob offset for animation
        const drawLayer = (img) => {
            if (!img) return;
            _ctx.drawImage(img, 0, 0 + totalBob * dpr, w, h);
        };

        // Scene background (if set)
        drawLayer(_layers.BACKGROUNDS);

        // Body
        drawLayer(_layers.BODY);

        // Texture
        drawLayer(_layers.TEXTURE);

        // Outfit
        drawLayer(_layers.OUTFIT);

        // Female: Breasts overlay (above outfit)
        if (_character.female) {
            const breastsUrl = _imgUrl('BREASTS', 'Breasts');
            drawLayer(_imgCache[breastsUrl]);
        }

        // Head
        drawLayer(_layers.HEAD);

        // Mouth — use viseme shape
        drawLayer(_getMouthImage());

        // Female: Eyelashes (above mouth)
        if (_character.female) {
            const lashUrl = _imgUrl('EYELASHES', 'Eyelashes');
            drawLayer(_imgCache[lashUrl]);
        }

        // Eyes — mood-driven expression
        drawLayer(_getEyeImage(_currentMood));

    }

    // ── Character config loading ─────────────────────────────────────────────

    async function _loadCharacterFromServer() {
        try {
            const res = await fetch('/api/bighead/active');
            if (res.ok) {
                const data = await res.json();
                if (data && data.BODY) return data;
            }
        } catch (_) {}

        // Fall back to profile storage
        try {
            const profile = window._serverProfile;
            if (profile?.ui?.bighead_character) {
                return profile.ui.bighead_character;
            }
        } catch (_) {}

        return null;
    }

    // ── Public API ───────────────────────────────────────────────────────────

    async function start(container, config) {
        stop();

        _container = container;

        // Hide eyes
        const eyesEl = container.querySelector('.eyes-container');
        if (eyesEl) eyesEl.style.display = 'none';

        // Hide waveform mouth
        const mouthEl = container.querySelector('.mouth-container');
        if (mouthEl) mouthEl.style.display = 'none';

        // Hide visualizer bars (they clash with character)
        container.querySelectorAll('.visualizer-container, .side-visualizer').forEach(el => {
            el.style.display = 'none';
        });

        // Add mode class
        const faceBox = document.getElementById('face-box');
        if (faceBox) faceBox.classList.add('bighead-mode');

        // Remove any existing bighead canvas
        const old = container.querySelector('#bighead-canvas');
        if (old) old.remove();

        // Create canvas
        _canvas = document.createElement('canvas');
        _canvas.id = 'bighead-canvas';
        Object.assign(_canvas.style, {
            position: 'absolute',
            top: '0', left: '0',
            width: '100%', height: '100%',
            borderRadius: '20px',
            pointerEvents: 'none',
            zIndex: '20',
            imageRendering: 'auto'
        });
        container.appendChild(_canvas);
        _ctx = _canvas.getContext('2d');

        // Size the canvas immediately so first-frame draw works
        const rect = _canvas.getBoundingClientRect();
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        _canvas.width = Math.max(2, Math.floor(rect.width * dpr));
        _canvas.height = Math.max(2, Math.floor(rect.height * dpr));

        // Draw loading indicator
        _drawLoading('Loading character...');

        // Load character config: prefer passed-in config (from profile.face_config),
        // then server /api/bighead/active, then DEFAULT_CHARACTER.
        if (config && config.BODY) {
            _character = { ...DEFAULT_CHARACTER, ...config };
            console.log('[BigHeadFace] Character from profile face_config:', _character.BODY);
        } else {
            try {
                const saved = await _loadCharacterFromServer();
                if (saved) {
                    _character = { ...DEFAULT_CHARACTER, ...saved };
                }
                console.log('[BigHeadFace] Character from server:', _character.BODY);
            } catch (err) {
                console.error('[BigHeadFace] Failed to load character config:', err);
            }
        }

        // Preload all needed images
        _drawLoading('Loading assets...');
        try {
            await _preloadCharacter(_character);
            // Count loaded layers
            const loaded = LAYER_ORDER.filter(l => _layers[l] !== null).length;
            console.log(`[BigHeadFace] Loaded ${loaded}/${LAYER_ORDER.length} layers`);
            if (loaded === 0) {
                console.warn('[BigHeadFace] No layers loaded! Character:', _character);
            }
        } catch (err) {
            console.error('[BigHeadFace] Failed to preload assets:', err);
        }

        // Start animation
        _lastTime = performance.now();
        _raf = requestAnimationFrame(_render);

        // Start blink cycle
        _scheduleBlink();

        console.log('[BigHeadFace] Started with character:', _character);
    }

    function stop() {
        if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
        if (_blinkTimer) { clearTimeout(_blinkTimer); _blinkTimer = null; }

        if (_canvas && _canvas.parentNode) _canvas.parentNode.removeChild(_canvas);

        // Restore hidden elements
        if (_container) {
            const mouthEl = _container.querySelector('.mouth-container');
            if (mouthEl) mouthEl.style.display = '';
            _container.querySelectorAll('.visualizer-container, .side-visualizer').forEach(el => {
                el.style.display = '';
            });
        }

        const faceBox = document.getElementById('face-box');
        if (faceBox) faceBox.classList.remove('bighead-mode');

        _canvas = null;
        _ctx = null;
        _container = null;
        _isBlinking = false;
        _thinking = false;
        _amplitude = 0;
        _targetAmplitude = 0;
        _currentViseme = 'Mmm';
    }

    function setThinking(v) {
        _thinking = !!v;
    }

    function setMood(mood) {
        const valid = ['neutral', 'happy', 'sad', 'angry', 'thinking', 'surprised', 'listening'];
        _currentMood = valid.includes(mood) ? mood : 'neutral';
    }

    async function setCharacter(cfg) {
        _character = { ...DEFAULT_CHARACTER, ...cfg };
        await _preloadCharacter(_character);
    }

    function getCharacter() {
        return { ..._character };
    }

    function getTraits() {
        return TRAITS;
    }

    function getDefaultCharacter() {
        return { ...DEFAULT_CHARACTER };
    }

    // ── Auto-mood detection from response text ──────────────────────────

    const _MOOD_KEYWORDS = {
        happy: /\b(haha|hahaha|lol|lmao|awesome|amazing|great|love it|love that|funny|hilarious|excited|yeah|sweet|nice one|hell yeah|dope|sick|rad|stoked|wooo|woo|yay|let's go|brilliant)\b/i,
        sad: /\b(sorry|unfortunately|sad|miss you|rough|that sucks|bummer|damn shame|heartbreaking|devastating|lost|gone|rip|tragic|depressing)\b/i,
        angry: /\b(pissed|angry|furious|hate|bullshit|what the fuck|are you kidding|god damn it|screw that|hell no|that's fucked|ridiculous|unbelievable|outrageous)\b/i,
        surprised: /\b(whoa|holy shit|holy crap|no way|seriously|wait what|oh my god|oh shit|what the|really\?|for real|are you serious|damn|wow|insane|crazy)\b/i,
        thinking: /\b(hmm+|let me think|good question|well|interesting|that's a thought|i wonder|consider|tricky|tough call|not sure)\b/i
    };

    let _moodResetTimer = null;

    function detectMood(text) {
        if (!text || text.length < 5) return;

        // Check for explicit [MOOD:xxx] tag first
        const tagMatch = text.match(/\[MOOD:(neutral|happy|sad|angry|thinking|surprised|listening)\]/i);
        if (tagMatch) {
            _applyDetectedMood(tagMatch[1].toLowerCase());
            return;
        }

        // Auto-detect from keywords — check most expressive moods first
        for (const mood of ['angry', 'surprised', 'sad', 'happy', 'thinking']) {
            if (_MOOD_KEYWORDS[mood].test(text)) {
                _applyDetectedMood(mood);
                return;
            }
        }
    }

    function _applyDetectedMood(mood) {
        if (mood === _currentMood) return;
        _currentMood = mood;
        console.log('[BigHeadFace] Auto-mood:', mood);

        // Reset to neutral after a few seconds
        if (_moodResetTimer) clearTimeout(_moodResetTimer);
        if (mood !== 'neutral') {
            _moodResetTimer = setTimeout(() => {
                _currentMood = 'neutral';
                console.log('[BigHeadFace] Auto-mood reset → neutral');
            }, 4000);
        }
    }

    return {
        start, stop, setThinking, setMood, setCharacter, detectMood,
        getCharacter, getTraits, getDefaultCharacter,
        TRAITS, DEFAULT_CHARACTER, CDN
    };
})();

// Self-register with FaceRenderer plugin system.
// start(container, config) passes face_config from the profile directly to BigHeadFace.
if (window.FaceRenderer?.registerFace) {
    window.FaceRenderer.registerFace('bighead', {
        start(container, config) { return window.BigHeadFace.start(container, config); },
        stop()                   { window.BigHeadFace.stop(); },
        setMood(mood)            { window.BigHeadFace.setMood(mood); },
        setThinking(v)           { window.BigHeadFace.setThinking(v); },
        detectMood(text)         { window.BigHeadFace.detectMood(text); }
    }, {
        name: 'BigHead Avatar',
        description: 'Animated BigHead character with lip sync and expressions'
    });
}
