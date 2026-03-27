/**
 * HaloSmokeFace — Halo ring + wispy smoke core, audio-reactive.
 *
 * Reads from window.audioAnalyser (AnalyserNode) for real-time TTS audio data.
 * Falls back to a gentle idle animation when no audio context is active.
 *
 * Exposes: window.HaloSmokeFace.start(container), window.HaloSmokeFace.stop()
 */
window.HaloSmokeFace = (function () {
    'use strict';

    const TAU = Math.PI * 2;
    const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
    const lerp = (a, b, t) => a + (b - a) * t;
    const rand = (a = 0, b = 1) => a + Math.random() * (b - a);

    // ── Perlin noise ──────────────────────────────────────────────────────────
    const P = new Uint8Array(512);

    function _initPerlin() {
        const p = [];
        for (let i = 0; i < 256; i++) p[i] = i;
        for (let i = 255; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [p[i], p[j]] = [p[j], p[i]];
        }
        for (let i = 0; i < 512; i++) P[i] = p[i & 255];
    }

    function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
    function grad(h, x, y) { const v = h & 3; return ((v & 1) ? -x : x) + ((v & 2) ? -y : y); }
    function perlin(x, y) {
        const xi = Math.floor(x) & 255, yi = Math.floor(y) & 255;
        const xf = x - Math.floor(x), yf = y - Math.floor(y);
        const u = fade(xf), v = fade(yf);
        const aa = P[P[xi] + yi], ab = P[P[xi] + yi + 1];
        const ba = P[P[xi + 1] + yi], bb = P[P[xi + 1] + yi + 1];
        return lerp(
            lerp(grad(aa, xf, yf), grad(ba, xf - 1, yf), u),
            lerp(grad(ab, xf, yf - 1), grad(bb, xf - 1, yf - 1), u),
            v
        );
    }
    function fbm(x, y, oct = 4) {
        let v = 0, a = 0.5, f = 1;
        for (let i = 0; i < oct; i++) { v += a * perlin(x * f, y * f); f *= 2.1; a *= 0.48; }
        return v;
    }

    // ── Settings ──────────────────────────────────────────────────────────────
    const S = { quality: 'med', motion: 2.0, trails: 0.2, coreInt: 0.20, sensitivity: 2.10 };

    // ── Audio feature smoothing state ─────────────────────────────────────────
    let _sm = null;
    let _fd = null;

    function _resetSm() {
        _sm = { amp: 0, bass: 0, mid: 0, treble: 0, kick: 0, drive: 0,
                transient: 0, prevRms: 0, burstDecay: 0, es: 0, ema: 0.02 };
        _fd = null;
    }

    function _getFeatures() {
        const an = window.audioAnalyser;

        if (!an) {
            const t = performance.now() * 0.001;
            if (_thinking) {
                // Breathing pulse — dramatic slow oscillation while AI processes
                const breath = (Math.sin(t * Math.PI) * 0.5 + 0.5); // ~2s cycle
                const amp = 0.08 + breath * 0.27; // 0.08 – 0.35
                return { amp, bass: amp * 0.9, mid: amp * 0.5, treble: amp * 0.3, kick: 0, drive: amp * 0.8, burst: 0, freq: null };
            }
            // Gentle idle pulse — calm swirling with no reactivity
            const idle = (Math.sin(t * 0.9) * 0.5 + 0.5) * 0.06;
            return { amp: idle, bass: idle, mid: idle * 0.5, treble: idle * 0.2, kick: 0, drive: idle * 0.8, burst: 0, freq: null };
        }

        // Resize frequency buffer if needed
        if (!_fd || _fd.length !== an.frequencyBinCount) {
            _fd = new Uint8Array(an.frequencyBinCount);
        }
        an.getByteFrequencyData(_fd);

        // Time-domain RMS
        const td = new Uint8Array(an.fftSize || 2048);
        try { an.getByteTimeDomainData(td); } catch (_) {}

        let sum = 0, peak = 0;
        for (let i = 0; i < td.length; i++) {
            const v = (td[i] - 128) / 128;
            const av = Math.abs(v);
            if (av > peak) peak = av;
            sum += v * v;
        }
        const rms = Math.sqrt(sum / td.length);

        const bins = _fd.length;
        const ny = (an.context?.sampleRate || 48000) / 2;
        const hi = hz => clamp(Math.round(hz / ny * bins), 0, bins - 1);
        const avgRange = (a, b) => {
            let s = 0, c = 0;
            for (let i = a; i <= b; i++) { s += _fd[i]; c++; }
            return (c ? s / c : 0) / 255;
        };

        const bass = avgRange(hi(20), hi(220));
        const mid  = avgRange(hi(220), hi(1500));
        const tre  = avgRange(hi(1500), hi(6500));

        // Spectral energy + flux
        let en = 0;
        for (let i = 0; i < bins; i++) { const v = _fd[i] / 255; en += v * v; }
        en /= bins;
        _sm.es = lerp(_sm.es, en, 0.06);
        const fl = Math.max(0, en - _sm.es);

        // Speech transient detection
        const rmsJump = Math.max(0, rms - _sm.prevRms);
        _sm.transient = Math.max(_sm.transient * 0.82, rmsJump * 12);
        _sm.prevRms = lerp(_sm.prevRms, rms, 0.15);

        // Burst: combines flux + transient
        _sm.burstDecay = Math.max(_sm.burstDecay * 0.88, clamp(_sm.transient + fl * 6, 0, 1));

        // AGC amplitude
        _sm.ema = lerp(_sm.ema, rms, 0.02);
        const ag = 0.085 / Math.max(0.006, _sm.ema);
        let ar2 = rms * ag * S.sensitivity;
        ar2 = Math.max(ar2, peak * 0.55 * S.sensitivity);
        let amp = clamp(ar2, 0, 2);
        amp = Math.pow(amp, 0.62);
        amp = clamp(amp, 0, 1);

        const atkRate = 0.28, relRate = 0.08;
        _sm.amp    = amp > _sm.amp   ? lerp(_sm.amp, amp, atkRate)   : lerp(_sm.amp, amp, relRate);
        _sm.bass   = lerp(_sm.bass,   bass, 0.14);
        _sm.mid    = lerp(_sm.mid,    mid,  0.14);
        _sm.treble = lerp(_sm.treble, tre,  0.14);

        const kt = clamp(fl * 9, 0, 1);
        _sm.kick  = kt > _sm.kick ? lerp(_sm.kick, kt, 0.32) : lerp(_sm.kick, kt, 0.12);

        const dt2 = clamp(_sm.amp * 0.85 + _sm.kick * 0.9, 0, 1);
        _sm.drive = dt2 > _sm.drive ? lerp(_sm.drive, dt2, 0.25) : lerp(_sm.drive, dt2, 0.10);

        return {
            amp: _sm.amp, bass: _sm.bass, mid: _sm.mid, treble: _sm.treble,
            kick: _sm.kick, drive: _sm.drive, burst: _sm.burstDecay, freq: _fd
        };
    }

    // ── Visual state (reset on each start) ───────────────────────────────────
    let _sparks = [];
    let _wisps  = [];
    let _wispInited = false;
    let _distortion = 0;
    let _colorShock = 0;
    let _spin = 0;
    let _thinking = false;

    function _initWisps() {
        _wisps = [];
        for (let i = 0; i < 28; i++) {
            _wisps.push({
                angle:    rand(0, TAU),
                radius:   rand(0.15, 0.85),
                speed:    rand(0.15, 0.6) * (Math.random() > 0.5 ? 1 : -1),
                width:    rand(0.4, 1.8),
                hueOff:   rand(0, 360),
                noiseOff: rand(0, 100),
                opacity:  rand(0.12, 0.35),
                layer:    Math.floor(rand(0, 3))
            });
        }
        _wispInited = true;
    }

    function _spawnSpark(cx, cy, r, hue) {
        _sparks.push({ a: rand(0, TAU), r, v: rand(2, 5.5), life: rand(0.3, 0.85), hue });
        if (_sparks.length > 160) _sparks.splice(0, _sparks.length - 160);
    }

    // ── Main draw function (ported from voice-orb-halo-smoke2.html) ───────────
    function _draw(ctx, t, dt, f, w, h) {
        const cx = w * 0.5, cy = h * 0.5, base = Math.min(w, h) * 0.38;
        if (!_wispInited) _initWisps();

        const mo = S.motion, dr = f.drive, ki = f.kick, burst = f.burst || 0;

        // Trail fade
        ctx.globalCompositeOperation = 'source-over';
        ctx.fillStyle = `rgba(6,8,14,${S.trails})`;
        ctx.fillRect(0, 0, w, h);

        // Distortion envelope
        const distTarget = clamp(burst * 2.2 + ki * 1.8, 0, 1);
        _distortion = distTarget > _distortion
            ? lerp(_distortion, distTarget, 0.4)
            : lerp(_distortion, distTarget, 0.06);

        const shockTarget = clamp(burst * 3 + ki * 2, 0, 1);
        _colorShock = shockTarget > _colorShock
            ? lerp(_colorShock, shockTarget, 0.5)
            : lerp(_colorShock, shockTarget, 0.04);

        const hue0   = (t * 12 + _colorShock * 180 + ki * 120) % 360;
        const ringR  = base * (0.62 + dr * 0.05 + ki * 0.04);
        const coreR  = ringR * 0.88;
        const dist   = _distortion;
        const ci     = S.coreInt;
        const calmHue = (hue0 + 200) % 360;

        // ── Inner orb: ambient glow ──
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';

        const glowAlpha = 0.06 + dr * 0.08 + burst * 0.12;
        const coreGlow = ctx.createRadialGradient(cx, cy, coreR * 0.02, cx, cy, coreR * 1.05);
        coreGlow.addColorStop(0,   `hsla(${calmHue},70%,65%,${glowAlpha + 0.04})`);
        coreGlow.addColorStop(0.4, `hsla(${(calmHue + 40) % 360},80%,55%,${glowAlpha})`);
        coreGlow.addColorStop(0.8, `hsla(${(calmHue + 90) % 360},60%,40%,${glowAlpha * 0.4})`);
        coreGlow.addColorStop(1,   'rgba(0,0,0,0)');
        ctx.fillStyle = coreGlow;
        ctx.beginPath(); ctx.arc(cx, cy, coreR * 1.05, 0, TAU); ctx.fill();

        // ── Wispy smoke strands ──
        const segments = S.quality === 'high' ? 48 : S.quality === 'low' ? 24 : 36;

        for (const ws of _wisps) {
            const thinkBoost = _thinking ? 1.2 : 0;
            ws.angle += dt * ws.speed * (0.3 + thinkBoost + dr * 1.5 + burst * 3.0) * mo;
            const layerDepth = 0.4 + ws.layer * 0.25;

            ctx.beginPath();
            for (let i = 0; i <= segments; i++) {
                const frac  = i / segments;
                const theta = ws.angle + frac * TAU * 0.6;
                let r = coreR * ws.radius * layerDepth;

                const nx = fbm(theta * 0.8 + ws.noiseOff + t * 0.15 * (1 + dist * 4), frac * 3 + t * 0.1, 3);
                const ny = fbm(theta * 1.2 + ws.noiseOff * 0.7 + t * 0.12, frac * 2.5 - t * 0.08, 3);

                r += nx * coreR * 0.22 * (0.5 + dist * 1.8) * ci;
                if (dist > 0.05) {
                    const warpN = fbm(theta * 3.5 + t * 2.5 * dist + ws.noiseOff, frac * 5 + t * 1.5, 2);
                    r += warpN * coreR * 0.35 * dist * ci;
                }

                const swirl = theta + ny * 0.6 * (1 + dist * 2.5);
                const x = cx + Math.cos(swirl) * r;
                const y = cy + Math.sin(swirl) * r;
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }

            const wHue   = (calmHue + ws.hueOff + _colorShock * 200 + dist * 160) % 360;
            const wSat   = 60 + dist * 35;
            const wLit   = 50 + dist * 18 + burst * 12;
            const wAlpha = ws.opacity * (0.5 + dr * 1.2 + burst * 1.5) * ci;
            ctx.strokeStyle  = `hsla(${wHue},${wSat}%,${wLit}%,${clamp(wAlpha, 0, 0.6)})`;
            ctx.lineWidth    = ws.width * (1 + dist * 2.5 + dr * 1.2);
            ctx.shadowColor  = `hsla(${wHue},90%,60%,${clamp(wAlpha * 0.7, 0, 0.4)})`;
            ctx.shadowBlur   = 12 + dist * 25 + dr * 15;
            ctx.stroke();
        }

        // ── Burst flares (speech transients) ──
        if (burst > 0.15) {
            const flareCount = Math.floor(3 + burst * 8);
            for (let i = 0; i < flareCount; i++) {
                const fa   = rand(0, TAU);
                const fr   = coreR * rand(0.1, 0.7);
                const fl2  = coreR * rand(0.05, 0.25) * burst;
                const fhue = (hue0 + rand(-60, 60)) % 360;
                ctx.strokeStyle  = `hsla(${fhue},100%,70%,${burst * 0.35})`;
                ctx.lineWidth    = 0.8 + burst * 2;
                ctx.shadowBlur   = 8 + burst * 18;
                ctx.shadowColor  = `hsla(${fhue},100%,65%,${burst * 0.3})`;
                ctx.beginPath();
                ctx.moveTo(cx + Math.cos(fa) * fr,         cy + Math.sin(fa) * fr);
                ctx.lineTo(cx + Math.cos(fa) * (fr + fl2), cy + Math.sin(fa) * (fr + fl2));
                ctx.stroke();
            }
        }

        // ── Thinking: orbiting dots ──
        if (_thinking) {
            const orbitSpeed = 1.2;
            const dotCount = 3;
            const dotRadius = 4;
            const trailLen = 0.35; // radians of trail arc
            for (let i = 0; i < dotCount; i++) {
                const baseAngle = t * orbitSpeed + (i / dotCount) * TAU;
                const dx = cx + Math.cos(baseAngle) * ringR;
                const dy = cy + Math.sin(baseAngle) * ringR;
                const dotHue = (calmHue + i * 40) % 360;

                // Comet trail
                ctx.beginPath();
                ctx.arc(cx, cy, ringR, baseAngle - trailLen, baseAngle, false);
                ctx.strokeStyle = `hsla(${dotHue},80%,65%,0.25)`;
                ctx.lineWidth = 3;
                ctx.shadowColor = `hsla(${dotHue},90%,60%,0.3)`;
                ctx.shadowBlur = 10;
                ctx.stroke();

                // Bright dot
                ctx.beginPath();
                ctx.arc(dx, dy, dotRadius, 0, TAU);
                ctx.fillStyle = `hsla(${dotHue},85%,75%,0.9)`;
                ctx.shadowColor = `hsla(${dotHue},100%,70%,0.8)`;
                ctx.shadowBlur = 18;
                ctx.fill();
            }
            ctx.shadowBlur = 0;
        }

        // ── Central bright core dot ──
        const dotR    = coreR * 0.06 * (1 + burst * 2.5 + dr * 0.8);
        const dotGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(0.01, dotR));
        dotGrad.addColorStop(0,   `hsla(${(hue0 + 60) % 360},90%,85%,${0.15 + burst * 0.5 + dr * 0.2})`);
        dotGrad.addColorStop(0.5, `hsla(${hue0},80%,65%,${0.08 + burst * 0.3})`);
        dotGrad.addColorStop(1,   'rgba(0,0,0,0)');
        ctx.fillStyle   = dotGrad;
        ctx.shadowBlur  = 20 + burst * 40;
        ctx.shadowColor = `hsla(${hue0},90%,70%,${0.2 + burst * 0.4})`;
        ctx.beginPath(); ctx.arc(cx, cy, dotR, 0, TAU); ctx.fill();
        ctx.shadowBlur = 0;
        ctx.restore();

        // ── Halo ring: frequency bars ──
        const freq = f.freq;
        const q    = S.quality;
        const bars = q === 'high' ? 200 : q === 'low' ? 110 : 160;
        const step = freq ? Math.max(1, Math.floor(freq.length / bars)) : 1;

        // Spawn sparks on kicks / bursts
        if (ki > 0.20) {
            const count = Math.floor(1 + (ki - 0.2) * 12);
            for (let i = 0; i < count; i++) {
                _spawnSpark(cx, cy, ringR + rand(-3, 6), (hue0 + rand(-30, 30)) % 360);
            }
        }
        if (burst > 0.3) {
            const count = Math.floor(burst * 5);
            for (let i = 0; i < count; i++) {
                _spawnSpark(cx, cy, ringR + rand(-5, 5), (hue0 + rand(-50, 50) + 180) % 360);
            }
        }

        ctx.globalCompositeOperation = 'lighter';
        ctx.lineCap = 'round';
        _spin += dt * (0.15 + dr * 1.1 + ki * 2.0 + burst * 1.5) * mo;

        // Blurred halo pass
        ctx.filter = `blur(${(6 + dr * 16 + ki * 20).toFixed(1)}px)`;
        for (let i = 0; i < bars; i++) {
            const a   = (i / bars) * TAU + _spin;
            const mg  = freq ? freq[i * step] / 255 : 0.04;
            const len = base * (0.12 + mg * 0.55 * (0.65 + f.treble * 0.5 + dr * 0.45));
            const hu  = (hue0 + (i / bars) * 150 + f.treble * 120) % 360;
            ctx.strokeStyle = `hsla(${hu},100%,64%,${0.06 + mg * 0.25 + dr * 0.08})`;
            ctx.lineWidth   = 2.5 + mg * 4.2 + dr * 1.5;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(a) * ringR, cy + Math.sin(a) * ringR);
            ctx.lineTo(cx + Math.cos(a) * (ringR + len), cy + Math.sin(a) * (ringR + len));
            ctx.stroke();
        }

        // Crisp halo pass
        ctx.filter = 'none';
        for (let i = 0; i < bars; i++) {
            const a   = (i / bars) * TAU + _spin;
            const mg  = freq ? freq[i * step] / 255 : 0.04;
            const len = base * (0.10 + mg * 0.48 * (0.65 + f.treble * 0.5));
            const hu  = (hue0 + (i / bars) * 160 + f.mid * 90) % 360;
            ctx.strokeStyle = `hsla(${hu},100%,72%,${0.07 + mg * 0.38 + dr * 0.06})`;
            ctx.lineWidth   = 1.0 + mg * 2.4 + dr * 0.7;
            ctx.beginPath();
            ctx.moveTo(cx + Math.cos(a) * ringR, cy + Math.sin(a) * ringR);
            ctx.lineTo(cx + Math.cos(a) * (ringR + len), cy + Math.sin(a) * (ringR + len));
            ctx.stroke();
        }

        // Sparks
        ctx.shadowBlur = 18 * (0.15 + dr + ki * 1.2);
        for (let i = _sparks.length - 1; i >= 0; i--) {
            const s = _sparks[i];
            s.life -= dt * (0.75 + dr * 0.4);
            s.a    += dt * s.v * (1 + ki * 1.4 + burst * 0.8) * mo;
            const al = clamp(s.life, 0, 1) * (0.08 + dr * 0.20 + ki * 0.18);
            if (al <= 0) { _sparks.splice(i, 1); continue; }
            ctx.shadowColor = `hsla(${s.hue},100%,70%,${al})`;
            ctx.fillStyle   = `hsla(${s.hue},100%,70%,${al})`;
            ctx.beginPath();
            ctx.arc(cx + Math.cos(s.a) * s.r, cy + Math.sin(s.a) * s.r, 1.3 + dr * 1.8 + burst * 1.2, 0, TAU);
            ctx.fill();
        }
        ctx.shadowBlur = 0;
        ctx.globalCompositeOperation = 'source-over';
    }

    // ── Animation loop ────────────────────────────────────────────────────────
    let _canvas = null, _ctx = null, _container = null;
    let _t0 = 0, _last = 0, _raf = null, _firstFrame = true;

    function _loop(now) {
        if (!_canvas) return;
        _raf = requestAnimationFrame(_loop);

        let dt = (now - _last) / 1000;
        _last = now;
        dt = Math.max(0.001, Math.min(dt, 0.05));
        const t = (now - _t0) / 1000;

        // Resize canvas to match its own CSS size (90% of face-box, circular)
        const rect = _canvas.getBoundingClientRect();
        const dpr  = Math.min(2, window.devicePixelRatio || 1);
        const w    = Math.max(2, Math.floor(rect.width  * dpr));
        const h    = Math.max(2, Math.floor(rect.height * dpr));
        if (_canvas.width !== w || _canvas.height !== h) {
            _canvas.width  = w;
            _canvas.height = h;
        }

        // Fully opaque fill on first frame so nothing bleeds through
        if (_firstFrame) {
            _ctx.fillStyle = '#060810';
            _ctx.fillRect(0, 0, w, h);
            _firstFrame = false;
        }

        const f = _getFeatures();
        _draw(_ctx, t, dt, f, w, h);
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Start the face inside the given container element (.face-box).
     * @param {HTMLElement} container
     */
    function start(container) {
        stop(); // clean up any previous run
        _initPerlin();
        _resetSm();

        _container = container;

        // Add class to hide the waveform mouth (face-box stays square)
        const faceBox = document.getElementById('face-box');
        if (faceBox) faceBox.classList.add('halo-smoke-mode');

        // Hide the classic eyes while this face is active
        const eyesEl = container.querySelector('.eyes-container');
        if (eyesEl) eyesEl.style.display = 'none';

        // Remove any existing orb canvas from legacy FaceRenderer
        const oldOrb = container.querySelector('#orb-canvas');
        if (oldOrb) oldOrb.remove();

        // Create our canvas
        _canvas = document.createElement('canvas');
        _canvas.id = 'halo-smoke-canvas';
        Object.assign(_canvas.style, {
            position: 'absolute',
            top: '0', left: '0',
            width: '100%', height: '100%',
            borderRadius: '50%',
            pointerEvents: 'none',
            background: '#060810',
            zIndex: '20'
        });
        container.appendChild(_canvas);
        _ctx = _canvas.getContext('2d', { alpha: true });

        // Reset all visual state
        _sparks     = [];
        _wisps      = [];
        _wispInited = false;
        _distortion = 0;
        _colorShock = 0;
        _spin       = 0;
        _thinking   = false;
        _firstFrame = true;

        _t0   = performance.now();
        _last = _t0;
        _raf  = requestAnimationFrame(_loop);
    }

    /**
     * Stop the face and remove the canvas from the DOM.
     */
    function stop() {
        if (_raf) { cancelAnimationFrame(_raf); _raf = null; }
        if (_canvas && _canvas.parentNode) _canvas.parentNode.removeChild(_canvas);
        // Restore face-box to default shape
        const faceBox = document.getElementById('face-box');
        if (faceBox) faceBox.classList.remove('halo-smoke-mode');
        _canvas    = null;
        _ctx       = null;
        _container = null;
    }

    function setThinking(v) { _thinking = !!v; }

    return { start, stop, setThinking };
})();

// Self-register with FaceRenderer plugin system
if (window.FaceRenderer?.registerFace) {
    window.FaceRenderer.registerFace('halo-smoke', window.HaloSmokeFace, {
        name: 'Halo Smoke Orb',
        description: 'Halo ring + wispy smoke core, reacts to TTS audio'
    });
}
