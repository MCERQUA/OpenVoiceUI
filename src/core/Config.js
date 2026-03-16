/**
 * Config — frontend config manager (ADR-009: simple manager pattern)
 *
 * Loads config from the server endpoint /api/config, merges with
 * compile-time defaults, and exposes a simple get/set interface.
 *
 * Usage:
 *   import { config } from './Config.js';
 *
 *   await config.load();             // fetch from server
 *
 *   config.get('tts.provider');      // → 'supertonic'
 *   config.get('missing', 'default'); // → 'default'
 *   config.set('ui.volume', 0.8);    // local override (not persisted)
 *
 *   config.onChange('tts.provider', (val) => { ... });
 */

import { eventBus } from './EventBus.js';

/** Compile-time defaults — must not contain secrets */
const DEFAULTS = {
    tts: {
        provider: 'groq',
        volume: 1.0,
        rate: 1.0,
    },
    stt: {
        provider: 'webspeech',
        language: 'en-US',
        continuous: true,
        interimResults: true,
    },
    ui: {
        theme: 'dark',
        wakeWord: true,
        showTranscript: true,
        showFace: true,
    },
    music: {
        enabled: true,
        duckVolume: 0.15,
        duckDuration: 500,
    },
    session: {
        key: 'voice-main',
    },
    api: {
        baseUrl: '',  // same origin
        configEndpoint: '/api/config',
    },
};

class Config {
    constructor() {
        /** @type {Record<string, any>} flat key → value store */
        this._store = {};
        this._loaded = false;

        // Seed with defaults
        this._flattenInto(DEFAULTS, '');
    }

    /**
     * Load config from the server, merging over defaults.
     * Safe to call multiple times — subsequent calls re-fetch.
     * @returns {Promise<void>}
     */
    async load() {
        const endpoint = this.get('api.configEndpoint', '/api/config');
        try {
            const res = await fetch(endpoint);
            if (res.ok) {
                const data = await res.json();
                this._flattenInto(data, '');
                eventBus.emit('config:loaded', { source: 'server' });
            } else {
                console.warn(`[Config] Server returned ${res.status} — using defaults`);
                eventBus.emit('config:loaded', { source: 'defaults' });
            }
        } catch (err) {
            console.warn('[Config] Failed to fetch config — using defaults:', err.message);
            eventBus.emit('config:loaded', { source: 'defaults' });
        }
        this._loaded = true;
    }

    /**
     * Get a config value by dot-notation key.
     * @param {string} key  e.g. 'tts.provider'
     * @param {*} [fallback]
     * @returns {*}
     */
    get(key, fallback = undefined) {
        return key in this._store ? this._store[key] : fallback;
    }

    /**
     * Set a config value locally (does not persist to server).
     * Emits 'config:change' event.
     * @param {string} key
     * @param {*} value
     */
    set(key, value) {
        const prev = this._store[key];
        this._store[key] = value;
        if (prev !== value) {
            eventBus.emit('config:change', { key, value, prev });
        }
    }

    /**
     * Subscribe to changes for a specific key.
     * @param {string} key
     * @param {Function} handler  called with (newValue, oldValue)
     * @returns {Function} unsubscribe
     */
    onChange(key, handler) {
        return eventBus.on('config:change', ({ key: k, value, prev }) => {
            if (k === key) handler(value, prev);
        });
    }

    /**
     * Return all config as a nested object (reconstructed from flat store).
     * @returns {Record<string, any>}
     */
    all() {
        const result = {};
        for (const [key, value] of Object.entries(this._store)) {
            this._setNested(result, key.split('.'), value);
        }
        return result;
    }

    /**
     * Whether config has been loaded from server at least once.
     * @returns {boolean}
     */
    get isLoaded() {
        return this._loaded;
    }

    // ── private ──────────────────────────────────────────────────────────────

    /** Recursively flatten an object into dot-notation keys in this._store */
    _flattenInto(obj, prefix) {
        for (const [k, v] of Object.entries(obj)) {
            const fullKey = prefix ? `${prefix}.${k}` : k;
            if (v !== null && typeof v === 'object' && !Array.isArray(v)) {
                this._flattenInto(v, fullKey);
            } else {
                this._store[fullKey] = v;
            }
        }
    }

    /** Set a nested key path on an object */
    _setNested(obj, parts, value) {
        const key = parts[0];
        if (parts.length === 1) {
            obj[key] = value;
        } else {
            if (!obj[key] || typeof obj[key] !== 'object') obj[key] = {};
            this._setNested(obj[key], parts.slice(1), value);
        }
    }
}

// Singleton
export const config = new Config();

export { Config };
