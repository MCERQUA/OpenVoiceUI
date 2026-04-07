/**
 * BaseFace — abstract base class for face/avatar modules (ADR-009: simple manager pattern)
 *
 * All face implementations extend this class and override the lifecycle methods.
 * The face manager loads faces from manifest.json and swaps them at runtime.
 *
 * Usage:
 *   import { BaseFace } from './BaseFace.js';
 *
 *   class MyFace extends BaseFace {
 *     init(container) { ... }
 *     setMood(mood) { ... }
 *     destroy() { ... }
 *   }
 *
 * Events emitted on eventBus:
 *   'face:mood'      { mood: string }         — mood changed
 *   'face:ready'     { id: string }            — face initialized
 *   'face:changed'   { from: string, to: string } — face swapped
 */

import { eventBus } from '../core/EventBus.js';

export const VALID_MOODS = ['neutral', 'happy', 'sad', 'angry', 'thinking', 'surprised', 'listening'];

/**
 * @abstract
 */
export class BaseFace {
    /**
     * @param {string} id  unique face ID from manifest (e.g. 'eyes', 'orb')
     */
    constructor(id) {
        if (new.target === BaseFace) {
            throw new Error('BaseFace is abstract — extend it, do not instantiate it directly');
        }
        /** @type {string} */
        this.id = id;
        /** @type {string} */
        this.currentMood = 'neutral';
        /** @type {HTMLElement|null} */
        this.container = null;
        /** @protected */
        this._initialized = false;
    }

    /**
     * Initialize the face inside the given container element.
     * Called once when the face is first activated.
     * @param {HTMLElement} container  the .face-box element
     * @abstract
     */
    init(container) {
        throw new Error(`${this.constructor.name}.init() not implemented`);
    }

    /**
     * Set the face's emotional state.
     * @param {string} mood  one of VALID_MOODS
     * @abstract
     */
    setMood(mood) {
        throw new Error(`${this.constructor.name}.setMood() not implemented`);
    }

    /**
     * Trigger a blink animation (optional — no-op by default).
     */
    blink() {
        // optional override
    }

    /**
     * React to audio amplitude (0-1). Used for speaking animations.
     * @param {number} amplitude  0.0 (silent) to 1.0 (loud)
     */
    setAmplitude(amplitude) {
        // optional override
    }

    /**
     * Called when the face is deactivated / replaced by another face.
     * Clean up timers, animation frames, DOM mutations.
     * @abstract
     */
    destroy() {
        throw new Error(`${this.constructor.name}.destroy() not implemented`);
    }

    // ── protected helpers ─────────────────────────────────────────────────────

    /**
     * Normalize and validate a mood string.
     * @param {string} mood
     * @returns {string}  valid mood, falls back to 'neutral'
     * @protected
     */
    _normalizeMood(mood) {
        return VALID_MOODS.includes(mood) ? mood : 'neutral';
    }

    /**
     * Emit a face:mood event on the shared eventBus.
     * @param {string} mood
     * @protected
     */
    _emitMood(mood) {
        this.currentMood = mood;
        eventBus.emit('face:mood', { mood, faceId: this.id });
    }

    /**
     * Emit face:ready after init completes.
     * @protected
     */
    _emitReady() {
        this._initialized = true;
        eventBus.emit('face:ready', { id: this.id });
    }
}

/**
 * FaceManager — loads the active face from manifest, handles swapping.
 *
 * Usage:
 *   import { faceManager } from './BaseFace.js';
 *
 *   await faceManager.load('eyes');   // activate EyeFace
 *   faceManager.setMood('happy');
 *   faceManager.blink();
 *   await faceManager.load('orb');    // swap to OrbFace
 */
class FaceManager {
    constructor() {
        /** @type {BaseFace|null} */
        this._active = null;
        /** @type {HTMLElement|null} */
        this._container = null;
        /** @type {Record<string, () => Promise<BaseFace>>} */
        this._registry = {};
        /** @type {Object|null} loaded manifest */
        this._manifest = null;
    }

    /**
     * Point the manager at a container element and load the manifest.
     * @param {HTMLElement} container  .face-box DOM element
     * @param {string} [manifestUrl]   URL to manifest.json
     */
    async init(container, manifestUrl = '/src/face/manifest.json') {
        this._container = container;
        try {
            const res = await fetch(manifestUrl);
            if (res.ok) {
                this._manifest = await res.json();
            }
        } catch (err) {
            console.warn('[FaceManager] Could not load manifest:', err.message);
        }
    }

    /**
     * Register a face factory.
     * @param {string} id
     * @param {() => Promise<BaseFace>} factory  async factory that returns a face instance
     */
    register(id, factory) {
        this._registry[id] = factory;
    }

    /**
     * Activate a face by ID.  Destroys the current face first.
     * @param {string} id
     */
    async load(id) {
        if (!this._registry[id]) {
            console.warn(`[FaceManager] Unknown face id: ${id}`);
            return;
        }

        const previousId = this._active?.id ?? null;

        // Destroy current face
        if (this._active) {
            this._active.destroy();
            this._active = null;
        }

        // Create & init new face
        const face = await this._registry[id]();
        if (this._container) {
            face.init(this._container);
        }
        this._active = face;

        // Persist selection to server profile
        const profileId = window._activeProfileData?.id || window.providerManager?._activeProfileId || 'default';
        fetch('/api/profiles/' + profileId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ui: { face_mode: id } })
        }).catch(e => console.warn('Failed to save face to profile:', e));
        if (window._serverProfile) {
            if (!window._serverProfile.ui) window._serverProfile.ui = {};
            window._serverProfile.ui.face_mode = id;
        }

        eventBus.emit('face:changed', { from: previousId, to: id });
    }

    /**
     * Load the previously saved face (or a default).
     * @param {string} [defaultId]
     */
    async loadSaved(defaultId = 'eyes') {
        let saved = defaultId;
        try { saved = window._serverProfile?.ui?.face_mode || defaultId; } catch (_) {}
        await this.load(saved);
    }

    /**
     * Delegate setMood to the active face.
     * @param {string} mood
     */
    setMood(mood) {
        this._active?.setMood(mood);
    }

    /** Delegate blink to the active face. */
    blink() {
        this._active?.blink();
    }

    /**
     * Delegate amplitude to the active face.
     * @param {number} amplitude
     */
    setAmplitude(amplitude) {
        this._active?.setAmplitude(amplitude);
    }

    /** @returns {string|null} */
    get activeFaceId() {
        return this._active?.id ?? null;
    }

    /** @returns {string} */
    get currentMood() {
        return this._active?.currentMood ?? 'neutral';
    }

    /** @returns {Array<{id, name, description}>} faces from manifest */
    get availableFaces() {
        return this._manifest?.faces ?? [];
    }
}

// Singleton
export const faceManager = new FaceManager();
