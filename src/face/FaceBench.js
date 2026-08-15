/**
 * FaceBench — in-app face rendering benchmark.
 *
 * Measures real frame-time cost of each face mode on the ACTUAL device
 * (phone / Pi5 / desktop), compared against a known-floor baseline face
 * ('bench-dot': one rAF loop drawing a single dot).
 *
 * Usage:
 *   - Console:  FaceBench.run()                      // dot, eyes, halo-smoke
 *               FaceBench.run({ seconds: 8 })
 *               FaceBench.run({ modes: ['bench-dot','eyes','orb','halo-smoke'] })
 *   - URL:      append  ?facebench=1  to auto-run after page load.
 *
 * Reports per mode: avg FPS, median / p95 frame time (ms), % of frames
 * over 33ms (jank), and estimated main-thread busy ratio. Results show in
 * an on-screen overlay (usable on a phone without devtools), the console,
 * and are returned as JSON. Restores the previously active face when done.
 *
 * Nothing here persists to the profile — all mode switches use skipPersist.
 */
window.FaceBench = (function () {
    'use strict';

    // ── Baseline face: the honest floor. One rAF loop, one dot. ──────────
    // Registered lazily (inside run()) so it never appears in the face
    // picker during normal client use.
    const DotFace = (function () {
        let canvas = null, ctx = null, raf = null, t0 = 0;
        function loop(now) {
            if (!canvas) return;
            raf = requestAnimationFrame(loop);
            const rect = canvas.getBoundingClientRect();
            const dpr = Math.min(2, window.devicePixelRatio || 1);
            const w = Math.max(2, Math.floor(rect.width * dpr));
            const h = Math.max(2, Math.floor(rect.height * dpr));
            if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
            ctx.fillStyle = '#060810';
            ctx.fillRect(0, 0, w, h);
            const pulse = 1 + Math.sin((now - t0) * 0.002) * 0.15;
            ctx.fillStyle = '#3b82f6';
            ctx.beginPath();
            ctx.arc(w / 2, h / 2, Math.min(w, h) * 0.06 * pulse, 0, Math.PI * 2);
            ctx.fill();
        }
        function start(container) {
            stop();
            const eyesEl = container.querySelector('.eyes-container');
            if (eyesEl) eyesEl.style.display = 'none';
            canvas = document.createElement('canvas');
            canvas.id = 'bench-dot-canvas';
            Object.assign(canvas.style, {
                position: 'absolute', top: '0', left: '0',
                width: '100%', height: '100%', borderRadius: '50%',
                pointerEvents: 'none', background: '#060810', zIndex: '20'
            });
            container.appendChild(canvas);
            ctx = canvas.getContext('2d');
            t0 = performance.now();
            raf = requestAnimationFrame(loop);
        }
        function stop() {
            if (raf) { cancelAnimationFrame(raf); raf = null; }
            if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
            canvas = null; ctx = null;
        }
        return { start, stop };
    })();

    // ── Frame sampling ────────────────────────────────────────────────────
    function sampleFrames(seconds) {
        return new Promise(resolve => {
            const deltas = [];
            let last = null, raf = null;
            const t0 = performance.now();
            function tick(now) {
                if (last !== null) deltas.push(now - last);
                last = now;
                if (now - t0 < seconds * 1000) {
                    raf = requestAnimationFrame(tick);
                } else {
                    resolve(deltas);
                }
            }
            raf = requestAnimationFrame(tick);
        });
    }

    function stats(deltas) {
        if (!deltas.length) return null;
        const sorted = [...deltas].sort((a, b) => a - b);
        const sum = deltas.reduce((a, b) => a + b, 0);
        const pct = p => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * p))];
        const jank = deltas.filter(d => d > 33.4).length / deltas.length;
        return {
            frames: deltas.length,
            avgFps: +(1000 / (sum / deltas.length)).toFixed(1),
            medianMs: +pct(0.5).toFixed(2),
            p95Ms: +pct(0.95).toFixed(2),
            worstMs: +sorted[sorted.length - 1].toFixed(1),
            jankPct: +(jank * 100).toFixed(1)
        };
    }

    // ── Overlay UI ────────────────────────────────────────────────────────
    let _overlay = null;
    function overlay(html) {
        if (!_overlay) {
            _overlay = document.createElement('div');
            _overlay.id = 'face-bench-overlay';
            Object.assign(_overlay.style, {
                position: 'fixed', top: '8px', left: '8px', right: '8px',
                maxWidth: '520px', margin: '0 auto', zIndex: '99999',
                background: 'rgba(6,8,14,0.94)', color: '#e5e7eb',
                font: '12px/1.5 monospace', padding: '10px 12px',
                borderRadius: '8px', border: '1px solid #3b82f6',
                whiteSpace: 'pre', overflowX: 'auto'
            });
            _overlay.addEventListener('click', () => _overlay.remove() && (_overlay = null));
            document.body.appendChild(_overlay);
        }
        _overlay.innerHTML = html;
    }

    // ── Runner ────────────────────────────────────────────────────────────
    let _running = false;

    async function run(opts = {}) {
        if (_running) { console.warn('[FaceBench] already running'); return; }
        const FR = window.FaceRenderer;
        if (!FR || !FR.container) { console.error('[FaceBench] FaceRenderer not initialized'); return; }
        _running = true;

        // Register the baseline floor face (idempotent)
        if (!FR._registry['bench-dot']) {
            FR.registerFace('bench-dot', DotFace, {
                name: 'Bench Dot', description: 'Benchmark baseline — single dot'
            });
        }

        const seconds = opts.seconds || 6;
        const settle = opts.settleMs || 800; // let each face warm up before sampling
        // Default: the floor + every installed, user-selectable face
        const requested = opts.modes ||
            ['bench-dot', ...FR.getAvailableModes().filter(m => m.installed).map(m => m.id)];
        const modes = requested.filter(m => FR.hasFace(m));
        const skipped = requested.filter(m => !FR.hasFace(m));
        if (skipped.length) console.warn('[FaceBench] skipping uninstalled faces:', skipped);

        const prevMode = FR.currentMode;
        const prevConfig = FR._currentConfig;
        const results = {};

        overlay(`FaceBench: ${modes.length} faces x ${seconds}s each...\n(keep this tab in the foreground)`);

        try {
            for (const mode of modes) {
                FR.setMode(mode, null, { skipPersist: true });
                overlay(renderTable(results) + `\n>> measuring "${mode}" (${seconds}s)...`);
                await new Promise(r => setTimeout(r, settle));
                const deltas = await sampleFrames(seconds);
                results[mode] = stats(deltas);
                console.log(`[FaceBench] ${mode}:`, results[mode]);
            }
        } finally {
            FR.setMode(prevMode, prevConfig, { skipPersist: true });
            _running = false;
        }

        // Normalize: cost relative to the dot floor (median frame time)
        const floor = results['bench-dot']?.medianMs || null;
        for (const m of Object.keys(results)) {
            if (results[m] && floor) results[m].xFloor = +(results[m].medianMs / floor).toFixed(2);
        }

        const device = `${screen.width}x${screen.height}@${window.devicePixelRatio} ` +
                       `cores:${navigator.hardwareConcurrency || '?'} ua:${navigator.userAgent.slice(0, 60)}`;
        console.table(results);
        console.log('[FaceBench] device:', device);
        overlay(renderTable(results) + `\n${device}\n(tap to dismiss)`);
        return { device, seconds, results };
    }

    function renderTable(results) {
        const rows = Object.entries(results);
        if (!rows.length) return 'FaceBench';
        let out = 'face          fps   med(ms) p95(ms) jank%  xFloor\n';
        for (const [m, s] of rows) {
            if (!s) continue;
            out += `${m.padEnd(13)} ${String(s.avgFps).padStart(5)} ${String(s.medianMs).padStart(7)} ` +
                   `${String(s.p95Ms).padStart(7)} ${String(s.jankPct).padStart(5)} ${String(s.xFloor ?? '-').padStart(7)}\n`;
        }
        return out;
    }

    // Auto-run via ?facebench=1
    if (/[?&]facebench=1/.test(location.search)) {
        window.addEventListener('load', () => setTimeout(() => run(), 3000));
    }

    return { run, stats, _DotFace: DotFace };
})();
