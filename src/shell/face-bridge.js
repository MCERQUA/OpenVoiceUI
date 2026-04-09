/**
 * face-bridge.js — Connect FaceModule to EventBridge (P6-T4)
 *
 * Subscribes to agent events and drives face state changes.
 * Returns an array of unsubscribe functions for clean teardown.
 *
 * Ref: future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md (App Shell section)
 */

import { AgentEvents } from '../core/EventBridge.js';

/**
 * Wire FaceModule to the EventBridge.
 * @param {import('../core/EventBridge.js').EventBridge} bridge
 * @returns {Function[]} unsubscribe functions
 */
export function connectFace(bridge) {
    // FaceModule is a global loaded by index.html (legacy pattern)
    const face = () => window.FaceModule;

    // Custom face iframe bridge (receives forwarded events)
    const custom = () => window.CustomFaceLoader;

    const unsubs = [
        bridge.on(AgentEvents.STATE_CHANGED, ({ state }) => {
            if (!face()) return;
            if (state === 'speaking')  face().setMood('neutral');
            if (state === 'listening') face().setMood('listening');
            if (state === 'thinking')  face().setMood('thinking');
            if (state === 'idle')      face().setMood('neutral');
            // Forward state to custom face iframe
            custom()?.setState(state);
            custom()?.setSpeaking(state === 'speaking');
        }),
        bridge.on(AgentEvents.MOOD, ({ mood }) => {
            face()?.setMood(mood);
            custom()?.setMood(mood);
        }),
        bridge.on(AgentEvents.CONNECTED, () => {
            face()?.setMood('happy');
            custom()?.setMood('happy');
        }),
        bridge.on(AgentEvents.DISCONNECTED, () => {
            face()?.setMood('neutral');
            custom()?.setMood('neutral');
        }),
        bridge.on(AgentEvents.ERROR, () => {
            face()?.setMood('sad');
            custom()?.setMood('sad');
        }),
        // Forward audio level to custom face iframe
        bridge.on(AgentEvents.AUDIO_LEVEL, ({ level }) => {
            custom()?.setAmplitude(level);
        }),
    ];

    return unsubs;
}
