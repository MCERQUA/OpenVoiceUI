/**
 * PlaylistEditor — Upload, reorder, and manage music tracks (P4-T2)
 *
 * Features:
 *   - Playlist tabs (library / generated)
 *   - Track list with drag-and-drop reordering
 *   - Upload new tracks (file picker + drag-and-drop zone)
 *   - Delete tracks with confirmation
 *   - Inline title edit + save
 *   - Save order button (persists via POST /api/music/playlist/<playlist>/order)
 *
 * Usage (standalone):
 *   import { PlaylistEditor } from './PlaylistEditor.js';
 *   const editor = new PlaylistEditor();
 *   editor.mount(document.getElementById('my-container'));
 *
 * Usage (via SettingsPanel):
 *   SettingsPanel.open('playlist');
 *
 * Backend endpoints used:
 *   GET  /api/music?action=list&playlist=X
 *   POST /api/music/upload
 *   DELETE /api/music/track/<playlist>/<filename>
 *   PUT  /api/music/track/<playlist>/<filename>/metadata
 *   GET  /api/music/playlist/<playlist>/order
 *   POST /api/music/playlist/<playlist>/order
 */

export class PlaylistEditor {
    constructor() {
        this._root = null;
        this._playlist = 'library';
        this._tracks = [];
        this._uploading = false;
    }

    // -------------------------------------------------------------------------
    // Public API
    // -------------------------------------------------------------------------

    async mount(container) {
        this._root = container;
        this._render();
        await this._loadTracks();
    }

    destroy() {
        if (this._root) {
            this._root.innerHTML = '';
            this._root = null;
        }
    }

    // -------------------------------------------------------------------------
    // Render
    // -------------------------------------------------------------------------

    _render() {
        this._root.innerHTML = `
            <div class="pe-tabs">
                <button class="pe-tab active" data-playlist="library">Library</button>
                <button class="pe-tab" data-playlist="generated">Generated</button>
            </div>
            <div class="pe-upload-zone" id="pe-upload-zone">
                <span>&#x1F3B5; Drop audio files here or
                    <label for="pe-file-input" class="pe-upload-link">browse</label>
                </span>
                <input type="file" id="pe-file-input"
                    accept=".mp3,.wav,.ogg,.m4a,.webm" multiple style="display:none">
                <div class="pe-upload-status" id="pe-upload-status"></div>
            </div>
            <div class="pe-track-list" id="pe-track-list">
                <div class="pe-loading">Loading tracks&#x2026;</div>
            </div>
            <div class="pe-footer">
                <button class="pe-save-order-btn" id="pe-save-order">&#x1F4BE; Save Order</button>
                <span class="pe-track-count" id="pe-track-count"></span>
            </div>
        `;
        this._attachListeners();
    }

    _attachListeners() {
        // Tab switching
        this._root.querySelectorAll('.pe-tab').forEach(tab => {
            tab.addEventListener('click', async () => {
                this._root.querySelectorAll('.pe-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');
                this._playlist = tab.dataset.playlist;
                await this._loadTracks();
            });
        });

        // File input
        const fileInput = this._root.querySelector('#pe-file-input');
        fileInput.addEventListener('change', async (e) => {
            if (e.target.files.length > 0) {
                await this._uploadFiles(Array.from(e.target.files));
                fileInput.value = '';
            }
        });

        // Upload drop zone
        const zone = this._root.querySelector('#pe-upload-zone');
        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('drag-over');
        });
        zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
        zone.addEventListener('drop', async (e) => {
            e.preventDefault();
            zone.classList.remove('drag-over');
            const files = Array.from(e.dataTransfer.files)
                .filter(f => /\.(mp3|wav|ogg|m4a|webm)$/i.test(f.name));
            if (files.length > 0) await this._uploadFiles(files);
        });

        // Save order button
        this._root.querySelector('#pe-save-order').addEventListener('click', () => this._saveOrder());
    }

    // -------------------------------------------------------------------------
    // Track list
    // -------------------------------------------------------------------------

    async _loadTracks() {
        const list = this._root.querySelector('#pe-track-list');
        const count = this._root.querySelector('#pe-track-count');
        list.innerHTML = '<div class="pe-loading">Loading&#x2026;</div>';

        try {
            const resp = await fetch(`/api/music?action=list&playlist=${this._playlist}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            this._tracks = data.tracks || [];

            if (count) {
                count.textContent = `${this._tracks.length} track${this._tracks.length !== 1 ? 's' : ''}`;
            }

            if (this._tracks.length === 0) {
                list.innerHTML = '<div class="pe-empty">No tracks yet. Upload some audio files above!</div>';
                return;
            }

            list.innerHTML = '';
            this._tracks.forEach((track, i) => {
                list.appendChild(this._buildTrackRow(track, i));
            });
            this._initDragSort(list);
        } catch (err) {
            list.innerHTML = `<div class="pe-error">Failed to load tracks: ${err.message}</div>`;
        }
    }

    _buildTrackRow(track) {
        const row = document.createElement('div');
        row.className = 'pe-track-row';
        row.dataset.filename = track.filename;
        row.draggable = true;

        const title = track.title || track.name;
        const artist = track.artist || '';
        const fmt = track.format ? track.format.toUpperCase() : '';
        const size = this._formatSize(track.size_bytes || 0);

        row.innerHTML = `
            <span class="pe-drag-handle" title="Drag to reorder">&#x2630;</span>
            <div class="pe-track-info">
                <input class="pe-track-title" value="${this._esc(title)}" placeholder="Track title">
                <span class="pe-track-meta">${this._esc(artist)}${artist && fmt ? ' &bull; ' : ''}${fmt}${fmt || artist ? ' &bull; ' : ''}${size}</span>
            </div>
            <div class="pe-track-actions">
                <button class="pe-btn-save" title="Save title">&#x2714;</button>
                <button class="pe-btn-delete" title="Delete track">&#x1F5D1;</button>
            </div>
        `;

        row.querySelector('.pe-btn-save').addEventListener('click', async () => {
            const input = row.querySelector('.pe-track-title');
            await this._saveMeta(track.filename, { title: input.value.trim() });
        });

        row.querySelector('.pe-btn-delete').addEventListener('click', async () => {
            const displayTitle = row.querySelector('.pe-track-title').value || title;
            if (!confirm(`Delete "${displayTitle}"?\nThis cannot be undone.`)) return;
            await this._deleteTrack(track.filename);
        });

        // Save on Enter key in title input
        row.querySelector('.pe-track-title').addEventListener('keydown', async (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                await this._saveMeta(track.filename, { title: e.target.value.trim() });
            }
        });

        return row;
    }

    // -------------------------------------------------------------------------
    // Drag-and-drop reorder
    // -------------------------------------------------------------------------

    _initDragSort(list) {
        let dragSrc = null;

        list.addEventListener('dragstart', (e) => {
            const row = e.target.closest('.pe-track-row');
            if (!row) return;
            dragSrc = row;
            row.classList.add('pe-dragging');
            e.dataTransfer.effectAllowed = 'move';
        });

        list.addEventListener('dragover', (e) => {
            e.preventDefault();
            const row = e.target.closest('.pe-track-row');
            if (!row || row === dragSrc) return;
            list.querySelectorAll('.pe-track-row').forEach(r =>
                r.classList.remove('pe-drag-above', 'pe-drag-below'));
            const rect = row.getBoundingClientRect();
            row.classList.add(e.clientY > rect.top + rect.height / 2 ? 'pe-drag-below' : 'pe-drag-above');
        });

        list.addEventListener('dragleave', (e) => {
            if (!list.contains(e.relatedTarget)) {
                list.querySelectorAll('.pe-track-row').forEach(r =>
                    r.classList.remove('pe-drag-above', 'pe-drag-below'));
            }
        });

        list.addEventListener('drop', (e) => {
            e.preventDefault();
            const target = e.target.closest('.pe-track-row');
            list.querySelectorAll('.pe-track-row').forEach(r =>
                r.classList.remove('pe-drag-above', 'pe-drag-below', 'pe-dragging'));
            if (!target || !dragSrc || target === dragSrc) { dragSrc = null; return; }
            const rect = target.getBoundingClientRect();
            const after = e.clientY > rect.top + rect.height / 2;
            list.insertBefore(dragSrc, after ? target.nextSibling : target);
            dragSrc = null;
        });

        list.addEventListener('dragend', () => {
            list.querySelectorAll('.pe-track-row').forEach(r =>
                r.classList.remove('pe-dragging', 'pe-drag-above', 'pe-drag-below'));
            dragSrc = null;
        });
    }

    // -------------------------------------------------------------------------
    // API calls
    // -------------------------------------------------------------------------

    async _uploadFiles(files) {
        if (this._uploading) return;
        this._uploading = true;
        const status = this._root.querySelector('#pe-upload-status');

        status.textContent = `Uploading ${files.length} file${files.length !== 1 ? 's' : ''}…`;
        status.style.color = '';

        let uploaded = 0;
        for (const file of files) {
            const fd = new FormData();
            fd.append('file', file);
            try {
                const resp = await fetch('/api/music/upload', { method: 'POST', body: fd });
                const data = await resp.json();
                if (!resp.ok) {
                    this._showStatus(`Error: ${data.error || 'Upload failed'}`, true);
                    continue;
                }
                uploaded++;
                this._showStatus(`Uploading… (${uploaded}/${files.length})`);
            } catch (err) {
                this._showStatus(`Upload error: ${err.message}`, true);
            }
        }

        this._uploading = false;
        if (uploaded > 0) {
            this._showStatus(`${uploaded} track${uploaded !== 1 ? 's' : ''} uploaded!`);
            await this._loadTracks();
            setTimeout(() => this._showStatus(''), 3000);
        }
    }

    async _saveMeta(filename, fields) {
        try {
            const resp = await fetch(
                `/api/music/track/${this._playlist}/${encodeURIComponent(filename)}/metadata`,
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(fields),
                }
            );
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            this._showStatus('Saved!');
            setTimeout(() => this._showStatus(''), 2000);
        } catch (err) {
            this._showStatus(`Save failed: ${err.message}`, true);
        }
    }

    async _deleteTrack(filename) {
        try {
            const resp = await fetch(
                `/api/music/track/${this._playlist}/${encodeURIComponent(filename)}`,
                { method: 'DELETE' }
            );
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            await this._loadTracks();
            this._showStatus('Track deleted.');
            setTimeout(() => this._showStatus(''), 2000);
        } catch (err) {
            this._showStatus(`Delete failed: ${err.message}`, true);
        }
    }

    async _saveOrder() {
        const list = this._root.querySelector('#pe-track-list');
        const rows = list.querySelectorAll('.pe-track-row');
        const order = Array.from(rows).map(r => r.dataset.filename);

        try {
            const resp = await fetch(`/api/music/playlist/${this._playlist}/order`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ order }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            this._showStatus('Order saved!');
            setTimeout(() => this._showStatus(''), 2000);
        } catch (err) {
            this._showStatus(`Order save failed: ${err.message}`, true);
        }
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    _showStatus(msg, isError = false) {
        const el = this._root ? this._root.querySelector('#pe-upload-status') : null;
        if (!el) return;
        el.textContent = msg;
        el.style.color = isError ? 'var(--red)' : 'var(--green)';
    }

    _formatSize(bytes) {
        if (bytes < 1024) return `${bytes}B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
    }

    _esc(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
}
