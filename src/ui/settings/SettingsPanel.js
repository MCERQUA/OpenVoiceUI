/**
 * SettingsPanel - Modular settings UI
 * Theme picker, face mode selector, and other settings
 */

window.SettingsPanel = {
    isOpen: false,
    currentPanel: null,
    container: null,
    _voicePreview: null,     // TTSVoicePreview instance
    _playlistEditor: null,   // PlaylistEditor instance
    _profileSwitcher: null,  // ProfileSwitcher instance

    init() {
        // Create settings modal container
        this.createContainer();

        // Listen for escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) {
                this.close();
            }
        });
    },

    createContainer() {
        // Check if already exists
        if (document.getElementById('settings-panel-container')) return;

        const container = document.createElement('div');
        container.id = 'settings-panel-container';
        container.innerHTML = `
            <div class="settings-overlay" onclick="SettingsPanel.close()"></div>
            <div class="settings-modal">
                <div class="settings-header">
                    <h2>Settings</h2>
                    <button class="settings-close" onclick="SettingsPanel.close()">&times;</button>
                </div>
                <div class="settings-content" id="settings-content">
                    <!-- Dynamic content loaded here -->
                </div>
            </div>
        `;
        document.body.appendChild(container);
        this.container = container;
    },

    open(panel = 'themes') {
        if (!this.container) this.createContainer();

        this.isOpen = true;
        this.currentPanel = panel;
        this.container.classList.add('open');

        // Load panel content
        this.loadPanel(panel);
    },

    close() {
        this.isOpen = false;
        if (this.container) {
            this.container.classList.remove('open');
        }
        // Clean up voice preview player on close
        if (this._voicePreview) {
            this._voicePreview.destroy();
            this._voicePreview = null;
        }
        // Clean up playlist editor on close
        if (this._playlistEditor) {
            this._playlistEditor.destroy();
            this._playlistEditor = null;
        }
        // Clean up profile switcher on close
        if (this._profileSwitcher) {
            this._profileSwitcher.destroy();
            this._profileSwitcher = null;
        }
    },

    loadPanel(panel) {
        const content = document.getElementById('settings-content');
        if (!content) return;

        switch (panel) {
            case 'themes':
                content.innerHTML = this.renderThemesPanel();
                this.attachThemeListeners();
                break;
            case 'face':
                content.innerHTML = this.renderFacePanel();
                this.attachFaceListeners();
                break;
            case 'voice':
                content.innerHTML = this.renderVoicePanel();
                this.mountVoicePreview();
                break;
            case 'playlist':
                content.innerHTML = this.renderPlaylistPanel();
                this.mountPlaylistEditor();
                break;
            case 'profiles':
                content.innerHTML = this.renderProfilesPanel();
                this.mountProfileSwitcher();
                break;
            case 'full':
            default:
                content.innerHTML = this.renderFullPanel();
                this.attachAllListeners();
                break;
        }
    },

    renderFullPanel() {
        return `
            <div class="settings-section">
                <h3 onclick="SettingsPanel.toggleSection(this)">
                    <span class="section-arrow">&#9660;</span> Themes & Colors
                </h3>
                <div class="section-content">
                    ${this.renderThemesContent()}
                </div>
            </div>
            <div class="settings-section">
                <h3 onclick="SettingsPanel.toggleSection(this)">
                    <span class="section-arrow">&#9660;</span> Face Display
                </h3>
                <div class="section-content">
                    ${this.renderFaceContent()}
                </div>
            </div>
            <div class="settings-section">
                <h3 onclick="SettingsPanel.toggleSection(this)">
                    <span class="section-arrow">&#9660;</span> Voice Preview
                </h3>
                <div class="section-content">
                    <div id="voice-preview-root"><div class="tts-preview-loading">Loading voices\u2026</div></div>
                </div>
            </div>
            <div class="settings-section">
                <h3 onclick="SettingsPanel.toggleSection(this)">
                    <span class="section-arrow">&#9660;</span> Playlist Editor
                </h3>
                <div class="section-content">
                    <div id="playlist-editor-root"><div class="pe-loading">Loading playlist\u2026</div></div>
                </div>
            </div>
            <div class="settings-section">
                <h3 onclick="SettingsPanel.toggleSection(this)">
                    <span class="section-arrow">&#9660;</span> Agent Profiles
                </h3>
                <div class="section-content">
                    <div id="profile-switcher-root"><div class="ps-loading">Loading profiles\u2026</div></div>
                </div>
            </div>
        `;
    },

    renderThemesPanel() {
        return `
            <div class="settings-section open">
                <h3>Themes & Colors</h3>
                <div class="section-content">
                    ${this.renderThemesContent()}
                </div>
            </div>
        `;
    },

    renderThemesContent() {
        const theme = window.ThemeManager?.getCurrentTheme() || {};
        const presets = window.ThemeManager?.presets || {};

        return `
            <div class="theme-presets">
                <label>Presets</label>
                <div class="preset-grid">
                    ${Object.entries(presets).map(([name, colors]) => `
                        <button class="preset-btn" data-preset="${name}" style="background: linear-gradient(135deg, ${colors.primary}, ${colors.accent});">
                            ${name}
                        </button>
                    `).join('')}
                </div>
            </div>
            <div class="theme-custom">
                <label>Custom Colors</label>
                <div class="color-pickers">
                    <div class="color-picker-group">
                        <label>Primary</label>
                        <input type="color" id="theme-primary" value="${theme.primary || '#0088ff'}"> <!-- fallback matches --blue -->
                    </div>
                    <div class="color-picker-group">
                        <label>Accent</label>
                        <input type="color" id="theme-accent" value="${theme.accent || '#00ffff'}"> <!-- fallback matches --cyan -->
                    </div>
                </div>
                <button class="reset-btn" onclick="SettingsPanel.resetTheme()">Reset to Default</button>
            </div>
        `;
    },

    renderFacePanel() {
        return `
            <div class="settings-section open">
                <h3>Face Display</h3>
                <div class="section-content">
                    ${this.renderFaceContent()}
                </div>
            </div>
        `;
    },

    renderFaceContent() {
        // Kept for backwards compatibility — used when FacePicker is unavailable
        const currentMode = window.FaceRenderer?.getCurrentMode() || 'eyes';
        const modes = window.FaceRenderer?.getAvailableModes() || [];

        return `
            <div class="face-modes">
                <label>Display Mode</label>
                <div class="mode-grid">
                    ${modes.map(mode => `
                        <button class="mode-btn ${mode.id === currentMode ? 'active' : ''}" data-mode="${mode.id}">
                            <span class="mode-name">${mode.name}</span>
                            <span class="mode-desc">${mode.description}</span>
                        </button>
                    `).join('')}
                </div>
            </div>
        `;
    },

    renderVoicePanel() {
        return `
            <div class="settings-section open">
                <h3>Voice Preview</h3>
                <div class="section-content">
                    <p class="tts-preview-hint">Click a voice to hear a sample.</p>
                    <div id="voice-preview-root"><div class="tts-preview-loading">Loading voices\u2026</div></div>
                </div>
            </div>
        `;
    },

    renderPlaylistPanel() {
        return `
            <div class="settings-section open">
                <h3>Playlist Editor</h3>
                <div class="section-content">
                    <div id="playlist-editor-root"><div class="pe-loading">Loading playlist\u2026</div></div>
                </div>
            </div>
        `;
    },

    async mountPlaylistEditor() {
        const root = document.getElementById('playlist-editor-root');
        if (!root) return;

        if (this._playlistEditor) {
            this._playlistEditor.destroy();
            this._playlistEditor = null;
        }

        try {
            const mod = await import('/src/ui/settings/PlaylistEditor.js');
            const PlaylistEditor = mod.PlaylistEditor || mod.default;
            this._playlistEditor = new PlaylistEditor();
            await this._playlistEditor.mount(root);
        } catch (err) {
            console.error('[SettingsPanel] Failed to load PlaylistEditor:', err);
            root.innerHTML = '<div class="pe-error">Playlist editor unavailable.</div>';
        }
    },

    async mountVoicePreview() {
        const root = document.getElementById('voice-preview-root');
        if (!root) return;

        // Destroy previous instance if any
        if (this._voicePreview) {
            this._voicePreview.destroy();
            this._voicePreview = null;
        }

        // Try to load TTSVoicePreview module dynamically
        try {
            const mod = await import('/src/ui/settings/TTSVoicePreview.js');
            const TTSVoicePreview = mod.TTSVoicePreview || mod.default;
            this._voicePreview = new TTSVoicePreview();
            await this._voicePreview.mount(root);
        } catch (err) {
            console.error('[SettingsPanel] Failed to load TTSVoicePreview:', err);
            root.innerHTML = '<div class="tts-preview-error">Voice preview unavailable.</div>';
        }
    },

    renderProfilesPanel() {
        return `
            <div class="settings-section open">
                <h3>Agent Profiles</h3>
                <div class="section-content">
                    <p class="ps-hint">Select a profile to switch the agent's personality, voice, and settings.</p>
                    <div id="profile-switcher-root"><div class="ps-loading">Loading profiles\u2026</div></div>
                </div>
            </div>
        `;
    },

    async mountProfileSwitcher() {
        const root = document.getElementById('profile-switcher-root');
        if (!root) return;

        if (this._profileSwitcher) {
            this._profileSwitcher.destroy();
            this._profileSwitcher = null;
        }

        try {
            const mod = await import('/src/ui/ProfileSwitcher.js');
            const ProfileSwitcher = mod.ProfileSwitcher || mod.default;
            this._profileSwitcher = new ProfileSwitcher();
            await this._profileSwitcher.mount(root);
        } catch (err) {
            console.error('[SettingsPanel] Failed to load ProfileSwitcher:', err);
            root.innerHTML = '<div class="ps-error">Profile switcher unavailable.</div>';
        }
    },

    attachAllListeners() {
        this.attachThemeListeners();
        this.attachFaceListeners();
        // Voice preview mounts async after render — trigger it now if the root exists
        const voiceRoot = document.getElementById('voice-preview-root');
        if (voiceRoot) {
            this.mountVoicePreview();
        }
        // Playlist editor mounts async
        const playlistRoot = document.getElementById('playlist-editor-root');
        if (playlistRoot) {
            this.mountPlaylistEditor();
        }
        // Profile switcher mounts async
        const profileRoot = document.getElementById('profile-switcher-root');
        if (profileRoot) {
            this.mountProfileSwitcher();
        }
    },

    attachThemeListeners() {
        // Color picker listeners
        const primaryPicker = document.getElementById('theme-primary');
        const accentPicker = document.getElementById('theme-accent');

        if (primaryPicker) {
            primaryPicker.addEventListener('input', (e) => {
                window.ThemeManager?.setPrimaryColor(e.target.value);
            });
        }

        if (accentPicker) {
            accentPicker.addEventListener('input', (e) => {
                window.ThemeManager?.setAccentColor(e.target.value);
            });
        }

        // Preset buttons
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const preset = btn.dataset.preset;
                window.ThemeManager?.applyPreset(preset);

                // Update color pickers
                const theme = window.ThemeManager?.getCurrentTheme();
                if (primaryPicker && theme) primaryPicker.value = theme.primary;
                if (accentPicker && theme) accentPicker.value = theme.accent;
            });
        });
    },

    attachFaceListeners() {
        const root = document.getElementById('face-picker-root');
        if (root && window.FacePicker) {
            window.FacePicker.mount(root);
            return;
        }

        // Legacy fallback: plain mode buttons (FacePicker not loaded)
        document.querySelectorAll('.mode-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const mode = btn.dataset.mode;
                window.FaceRenderer?.setMode(mode);

                document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });
    },

    toggleSection(header) {
        const section = header.parentElement;
        section.classList.toggle('open');
        const arrow = header.querySelector('.section-arrow');
        if (arrow) {
            arrow.innerHTML = section.classList.contains('open') ? '&#9660;' : '&#9654;';
        }
    },

    resetTheme() {
        window.ThemeManager?.resetTheme();

        // Update pickers
        const theme = window.ThemeManager?.getCurrentTheme();
        const primaryPicker = document.getElementById('theme-primary');
        const accentPicker = document.getElementById('theme-accent');
        if (primaryPicker && theme) primaryPicker.value = theme.primary;
        if (accentPicker && theme) accentPicker.value = theme.accent;
    }
};
