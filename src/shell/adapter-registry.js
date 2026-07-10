/**
 * adapter-registry.js — Adapter ID → module path mapping + dynamic import (P6-T5)
 *
 * The AdapterRegistry knows which JS file corresponds to each adapter ID
 * declared in a profile's "adapter" field. It handles dynamic import with
 * caching so each module is only fetched once, even when multiple profiles
 * reference the same adapter.
 *
 * Adding a new adapter system:
 *   1. Create src/adapters/my-adapter.js  (copy _template.js)
 *   2. Add an entry to ADAPTER_PATHS below
 *   3. Create profiles/my-profile.json with "adapter": "my-adapter"
 *   Done — no other changes needed.
 *
 * Ref: future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md (Plug-and-Play section)
 *
 * Usage:
 *   import { adapterRegistry } from './adapter-registry.js';
 *   const adapter = await adapterRegistry.load('hume-evi');
 */

// ─────────────────────────────────────────────────────────────────────────────
// Static adapter map — adapter ID → relative path from this file (src/shell/)
// ─────────────────────────────────────────────────────────────────────────────

const ADAPTER_PATHS = {
    'clawdbot':       '../adapters/ClawdBotAdapter.js',
    'hume-evi':       '../adapters/hume-evi.js',
    'xai-realtime':   '../adapters/xai-realtime.js',
    // Add future adapters here:
    // 'elevenlabs-classic': '../adapters/elevenlabs-classic.js',
    // 'hybrid':             '../adapters/hybrid.js',
    // 'openai-realtime':    '../adapters/openai-realtime.js',
};

/** Default adapter used when a profile omits the "adapter" field. */
const DEFAULT_ADAPTER_ID = 'clawdbot';

// ─────────────────────────────────────────────────────────────────────────────
// AdapterRegistry
// ─────────────────────────────────────────────────────────────────────────────

class AdapterRegistry {
    constructor() {
        /**
         * Cache of already-loaded adapter objects, keyed by adapter ID.
         * @type {Object.<string, object>}
         */
        this._cache = {};
    }

    /**
     * Load an adapter by its ID.
     *
     * Resolves to the adapter object (the object with init/start/stop/destroy
     * exported as `default` from the adapter module, or as a named export).
     *
     * Results are cached so the module is only dynamically imported once.
     *
     * @param {string} [adapterId] - Adapter ID as declared in a profile's "adapter" field.
     *   Falls back to DEFAULT_ADAPTER_ID if omitted or unknown.
     * @returns {Promise<object>} Adapter object
     */
    async load(adapterId) {
        const id = adapterId || DEFAULT_ADAPTER_ID;

        // Return cached module if already loaded
        if (this._cache[id]) return this._cache[id];

        const path = ADAPTER_PATHS[id];

        if (!path) {
            console.warn(
                `[AdapterRegistry] Unknown adapter "${id}" — falling back to "${DEFAULT_ADAPTER_ID}".`,
                `Known adapters: [${this.knownAdapters().join(', ')}]`
            );
            // Avoid infinite recursion if default itself is unknown
            if (id === DEFAULT_ADAPTER_ID) {
                throw new Error(`[AdapterRegistry] Default adapter "${DEFAULT_ADAPTER_ID}" has no registered path.`);
            }
            return this.load(DEFAULT_ADAPTER_ID);
        }

        try {
            const module = await import(path);

            // Prefer default export; fall back to first named export that looks like an adapter
            const adapter =
                module.default ||
                Object.values(module).find(
                    v => v && typeof v === 'object' && typeof v.init === 'function'
                );

            if (!adapter) {
                throw new Error(`No adapter export found in "${path}"`);
            }

            this._cache[id] = adapter;
            console.log(`[AdapterRegistry] Loaded adapter: "${id}" from ${path}`);
            return adapter;

        } catch (err) {
            console.error(`[AdapterRegistry] Failed to load adapter "${id}":`, err);

            // If loading the requested adapter fails, try the default as a fallback
            if (id !== DEFAULT_ADAPTER_ID) {
                console.warn(`[AdapterRegistry] Falling back to default adapter "${DEFAULT_ADAPTER_ID}"`);
                return this.load(DEFAULT_ADAPTER_ID);
            }

            throw err; // Re-throw if even the default adapter fails
        }
    }

    /**
     * Return true if adapterId is registered in the static map.
     * @param {string} adapterId
     * @returns {boolean}
     */
    has(adapterId) {
        return adapterId in ADAPTER_PATHS;
    }

    /**
     * Return the list of all registered adapter IDs.
     * @returns {string[]}
     */
    knownAdapters() {
        return Object.keys(ADAPTER_PATHS);
    }

    /**
     * The default adapter ID used when a profile omits "adapter".
     * @type {string}
     */
    get defaultAdapter() {
        return DEFAULT_ADAPTER_ID;
    }

    /**
     * Clear the module cache (primarily for testing).
     */
    clearCache() {
        this._cache = {};
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Singleton — one registry for the whole app
// ─────────────────────────────────────────────────────────────────────────────

export const adapterRegistry = new AdapterRegistry();

// Also export class and constants for testing / multiple instances
export { AdapterRegistry, ADAPTER_PATHS, DEFAULT_ADAPTER_ID };
