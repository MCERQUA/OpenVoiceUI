/**
 * constants — Centralized configuration constants
 *
 * Extracted from AppShell.js to avoid hardcoding provider lists
 * and accepted file types directly in HTML templates.
 *
 * Add new TTS providers here — the settings UI will reflect them
 * automatically without touching the shell template.
 */

/**
 * TTS provider options shown in the settings dropdown.
 * @type {Array<{id: string, label: string}>}
 */
export const TTS_PROVIDERS = [
    { id: 'supertonic', label: 'Supertonic (Free)' },
    { id: 'groq', label: 'Groq Orpheus' },
    { id: 'hume', label: 'Hume EVI' },
];

/**
 * Accepted file types for the transcript panel upload input.
 * Includes documents, images, code, and 3D model formats.
 * @type {string}
 */
export const TRANSCRIPT_UPLOAD_ACCEPT = [
    'image/*',
    '.pdf', '.docx', '.xlsx', '.pptx',
    '.txt', '.md', '.json', '.csv',
    '.html', '.js', '.py', '.ts', '.css',
    '.glb', '.gltf', '.obj', '.fbx', '.stl',
    '.3ds', '.dae', '.ply', '.usdz', '.blend',
    '.mtl', '.hdr', '.exr',
].join(',');

/**
 * Sandbox permissions for the canvas iframe.
 * @type {string}
 */
export const CANVAS_SANDBOX_PERMISSIONS = [
    'allow-same-origin',
    'allow-scripts',
    'allow-popups',
    'allow-popups-to-escape-sandbox',
    'allow-forms',
    'allow-top-navigation-by-user-activation',
    'allow-downloads',
    'allow-pointer-lock',
].join(' ');
