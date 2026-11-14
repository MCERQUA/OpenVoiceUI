"""
routes/static_files.py — Static Asset Serving Blueprint (P2-T8)

Extracted from server.py during Phase 2 blueprint split.
Registers routes:
  GET  /sounds/<path:filepath>         — sound effect files
  GET  /uploads/<path:filename>        — uploaded user files
  GET  /src/<path:filepath>            — frontend JS/CSS source modules
  GET  /known_faces/<name>/<filename>  — face recognition photos
  GET  /api/dj-sound                   — DJ soundboard API (list/play)
"""

import hmac
import json
import logging
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, redirect, request, send_file

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

static_files_bp = Blueprint('static_files', __name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from services.paths import APP_ROOT, SOUNDS_DIR, UPLOADS_DIR, KNOWN_FACES_DIR, STATIC_DIR

BASE_DIR = APP_ROOT

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Uploads index — ONE ledger of every client-provided file, shared with the
# host-side intake helper (scripts/uploads/jambot-upload-intake.py writes the
# same schema for SMS/email media). Dotfile so serve_upload never exposes it.
# The agent reads it at workspace/uploads/.uploads-index.jsonl.
# ---------------------------------------------------------------------------

UPLOADS_INDEX = UPLOADS_DIR / '.uploads-index.jsonl'


def append_upload_index(record: dict) -> None:
    """Append one entry to the uploads ledger. Fail-soft: an index problem
    must never fail the upload itself."""
    try:
        import json as _json
        with open(UPLOADS_INDEX, 'a', encoding='utf-8') as fh:
            fh.write(_json.dumps(record, ensure_ascii=False) + '\n')
        try:
            UPLOADS_INDEX.chmod(0o666)  # host-side writers run as a different uid
        except OSError:
            pass
    except Exception as exc:  # noqa: BLE001
        logger.warning('uploads-index append failed: %s', exc)

# ---------------------------------------------------------------------------
# DJ Sounds catalogue
# ---------------------------------------------------------------------------

DJ_SOUNDS = {
    'air_horn': {
        'description': 'Classic stadium air horn - ba ba baaaa!',
        'when_to_use': 'Before drops, hype moments, celebrating wins, hip-hop DJ style'
    },
    'scratch_long': {
        'description': 'Extended DJ scratch solo - wicka wicka',
        'when_to_use': 'Transitions, hip-hop moments, showing off DJ skills'
    },
    'rewind': {
        'description': 'DJ rewind - pull up selecta!',
        'when_to_use': 'Going back, replaying something fire, dancehall pull-ups'
    },
    'record_stop': {
        'description': 'Record stopping abruptly',
        'when_to_use': 'Stopping everything, dramatic pause, cutting the music'
    },
    'impact': {
        'description': 'Punchy cinematic impact hit',
        'when_to_use': 'Punctuating statements, transitions, emphasis'
    },
    'crowd_cheer': {
        'description': 'Nightclub crowd cheering and going wild',
        'when_to_use': 'Big wins, amazing moments, festival energy, applause'
    },
    'crowd_hype': {
        'description': 'Hyped up rave crowd losing their minds',
        'when_to_use': 'Peak energy moments, party atmosphere'
    },
    'yeah': {
        'description': 'Hype man YEAH! vocal shot',
        'when_to_use': 'Hyping up, agreement, energy boost'
    },
    'lets_go': {
        'description': 'LETS GO! vocal chant',
        'when_to_use': 'Starting something, getting pumped, motivation'
    },
    'laser': {
        'description': 'Retro arcade laser zap - pew pew',
        'when_to_use': 'Sci-fi moments, gaming references, 80s vibes'
    },
    'gunshot': {
        'description': 'Dancehall gunshot sound - gun finger!',
        'when_to_use': 'Reggae/dancehall vibes, shooting down bad ideas'
    },
    'bruh': {
        'description': 'Classic bruh sound effect',
        'when_to_use': 'Facepalm moments, disappointment, when someone says something dumb'
    },
    'sad_trombone': {
        'description': 'Sad trombone wah wah wah - womp womp',
        'when_to_use': 'Fails, disappointments, when things go wrong'
    },
}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _safe_path(base_dir: Path, *parts) -> Path | None:
    """
    Resolve a path within base_dir, rejecting any traversal outside it.
    Returns the resolved Path on success, or None if traversal is detected.
    """
    try:
        resolved = (base_dir / Path(*parts)).resolve()
        base_resolved = base_dir.resolve()
        if resolved == base_resolved or base_resolved in resolved.parents:
            return resolved
    except Exception:
        pass
    return None


@static_files_bp.route('/sounds/<path:filepath>')
def serve_sound(filepath):
    """Serve sound effect files (including subdirectories like DJ-clips/)"""
    sound_path = _safe_path(SOUNDS_DIR, filepath)
    if sound_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if sound_path.exists():
        return send_file(sound_path, mimetype='audio/mpeg')
    return jsonify({"error": "Sound not found"}), 404


# ---------------------------------------------------------------------------
# Upload constants & helpers
# ---------------------------------------------------------------------------

# Hard limit enforced before writing to disk (100 MB)
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Maximum characters returned to the AI as content_preview for text files.
# Files at or below this threshold are inlined verbatim into the conversation.
# Larger files set content_preview_truncated=True and the agent is directed to
# use its Read tool on the saved path instead (which lives in a prunable
# tool-result block rather than an un-prunable user message).
_MAX_PREVIEW_CHARS = 50_000

# Maximum characters for extracted text from binary documents (PDF/DOCX/XLSX/PPTX).
# Binary extractors sanitize their own output, so they share this cap via
# _sanitize_text below.
_MAX_BINARY_PREVIEW_CHARS = 20_000

# Only block executables — accept everything else (weird exports, unknown formats, etc.)
_BLOCKED_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.com', '.scr', '.pif',
    '.msi', '.dll', '.sys', '.vbs', '.vbe', '.wsh',
    '.wsf', '.ps1', '.sh', '.cpl', '.inf', '.reg',
}

# Control characters to strip from extracted text (keeps \t \n \r)
_CTRL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _sanitize_text(text: str) -> str:
    """Strip control chars, collapse excessive blank lines, cap at binary preview limit.
    Used by PDF/DOCX/XLSX/PPTX extractors — they don't signal truncation back to
    the upload handler so use the tighter cap."""
    text = _CTRL_RE.sub('', text)
    text = re.sub(r'\n{4,}', '\n\n\n', text)  # no more than 3 consecutive blank lines
    return text[:_MAX_BINARY_PREVIEW_CHARS].strip()


def _read_text_preview(path: Path) -> tuple[str, bool]:
    """Read a text file for inline preview. Returns (sanitized_text, truncated).
    Never loads more than _MAX_PREVIEW_CHARS + 1 chars into memory, so large
    uploads don't spike RSS."""
    with open(path, 'r', errors='replace') as fh:
        raw = fh.read(_MAX_PREVIEW_CHARS + 1)
    truncated = len(raw) > _MAX_PREVIEW_CHARS
    cleaned = _CTRL_RE.sub('', raw[:_MAX_PREVIEW_CHARS])
    cleaned = re.sub(r'\n{4,}', '\n\n\n', cleaned)
    return cleaned.strip(), truncated


def _extract_pdf(path: Path) -> str:
    """Extract text from a PDF using pypdf. Returns sanitized string."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ''
            pages.append(text)
        except Exception:
            pages.append(f'[Page {i + 1}: extraction failed]')
    return _sanitize_text('\n\n'.join(pages))


def _extract_docx(path: Path) -> str:
    """Extract text from a .docx using python-docx. Returns sanitized string."""
    from docx import Document
    doc = Document(str(path))
    parts = []
    for para in doc.paragraphs:
        t = para.text.strip()
        if t:
            parts.append(t)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(' | '.join(cells))
    return _sanitize_text('\n'.join(parts))


def _extract_xlsx(path: Path) -> str:
    """Extract cell values from a .xlsx using openpyxl. Returns sanitized string."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    parts = []
    try:
        for sheet in wb.worksheets:
            parts.append(f'[Sheet: {sheet.title}]')
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append('\t'.join(cells))
    finally:
        wb.close()
    return _sanitize_text('\n'.join(parts))


def _extract_pptx(path: Path) -> str:
    """Extract text from a .pptx using python-pptx. Returns sanitized string."""
    from pptx import Presentation
    prs = Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = para.text.strip()
                    if t:
                        slide_texts.append(t)
        if slide_texts:
            parts.append(f'[Slide {i}]')
            parts.extend(slide_texts)
    return _sanitize_text('\n'.join(parts))


def _call_skill_runner(path: Path, original_name: str) -> str | None:
    """
    Try to extract document text via the shared skill-runner service.
    Returns extracted text on success, None if the service is unavailable.
    Falls back gracefully so local extractors can take over.
    """
    try:
        import requests
        with open(path, 'rb') as fh:
            resp = requests.post(
                'http://skill-runner:8900/extract',
                files={'file': (original_name, fh)},
                data={'filename': original_name},
                timeout=30,
            )
        if resp.ok:
            data = resp.json()
            return data.get('text', '')
        logger.warning('skill-runner /extract returned %d for %s', resp.status_code, original_name)
    except Exception as exc:
        logger.debug('skill-runner unavailable, using local extractors: %s', exc)
    return None


@static_files_bp.route('/api/upload', methods=['POST'])
def upload_file():
    """Accept a file upload from the text panel and save to uploads/."""
    import mimetypes
    import uuid

    if 'file' not in request.files:
        import io as _io
        import time as _time
        ct = request.content_type or ''
        # iOS/Cloudflare sometimes sends camera photo as raw binary body (no multipart).
        # Detect: Content-Type is an image MIME, body has data, no other fields.
        if (ct.startswith('image/') or ct == 'application/octet-stream') and request.content_length and request.content_length > 0:
            raw = request.get_data()
            if raw:
                ext_guess = mimetypes.guess_extension(ct) or '.jpg'
                if ext_guess in ('.jpe', '.jpeg'):
                    ext_guess = '.jpg'
                fname_raw = f'capture_{int(_time.time())}{ext_guess}'
                logger.info('upload: raw-body fallback — ct=%s len=%s fname=%s', ct, len(raw), fname_raw)
                from werkzeug.datastructures import FileStorage
                f_raw = FileStorage(stream=_io.BytesIO(raw), filename=fname_raw, content_type=ct)
                # Jump straight to save logic via a local alias
                _raw_file = f_raw
            else:
                _raw_file = None
        else:
            _raw_file = None

        if _raw_file is None:
            logger.warning(
                'upload: no file field — content_type=%r content_length=%r '
                'files_keys=%r form_keys=%r user_agent=%r',
                ct,
                request.content_length,
                list(request.files.keys()),
                list(request.form.keys()),
                request.headers.get('User-Agent', '')[:120],
            )
            return jsonify({'error': 'No file provided'}), 400

        # Use the raw-body file as `f` for the rest of the route
        f = _raw_file
    else:
        f = request.files['file']

    if not f.filename:
        # iOS camera blobs arrive with no filename — generate one from content-type
        import time as _time
        ct = f.content_type or 'image/jpeg'
        ext_guess = mimetypes.guess_extension(ct) or '.jpg'
        # mimetypes sometimes returns .jpe or .jpeg — normalize to .jpg
        if ext_guess in ('.jpe', '.jpeg'):
            ext_guess = '.jpg'
        f.filename = f'capture_{int(_time.time())}{ext_guess}'
        logger.info('upload: empty filename from client; generated fallback %s (content_type=%s)', f.filename, ct)

    # --- Sanitize filename, validate extension ---
    original_name = Path(f.filename).name
    ext = Path(original_name).suffix.lower()
    if ext in _BLOCKED_EXTENSIONS:
        return jsonify({'error': f'File type "{ext}" is not allowed'}), 415

    # --- Size check before writing to disk ---
    # Seek to end to get byte length without reading into memory
    f.stream.seek(0, 2)
    file_size = f.stream.tell()
    f.stream.seek(0)
    if file_size > _MAX_UPLOAD_BYTES:
        return jsonify({'error': 'File too large (100 MB max)'}), 413

    # --- Save with original-stem prefix + short uuid suffix so the agent
    #     can find files by the name the user uploaded them as. UUID suffix
    #     prevents collisions when the same name is uploaded twice. ---
    stem_safe = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(original_name).stem)[:80].strip('._-') or 'file'
    safe_name = f"{stem_safe}__{uuid.uuid4().hex[:8]}{ext}"
    dest = UPLOADS_DIR / safe_name
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    f.save(str(dest))

    # --- HEIC/HEIF → JPG auto-conversion ---
    # Browsers can't display HEIC natively. Convert on ingest so the file is
    # immediately usable. Uses pillow-heif (in requirements.txt).
    if ext in ('.heic', '.heif'):
        jpg_name = f"{stem_safe}__{uuid.uuid4().hex[:8]}.jpg"
        jpg_dest = UPLOADS_DIR / jpg_name
        try:
            import pillow_heif
            from PIL import Image as _Image
            pillow_heif.register_heif_opener()
            with _Image.open(str(dest)) as _img:
                _img.convert('RGB').save(str(jpg_dest), 'JPEG', quality=90)
            dest.rename(dest.parent / (dest.stem + '.heic.original'))
            dest = jpg_dest
            safe_name = jpg_name
            ext = '.jpg'
            logger.info(f'### HEIC→JPG conversion ok: {jpg_name}')
        except Exception as _e:
            logger.warning(f'### HEIC→JPG conversion error: {_e}')

    mime = f.mimetype or mimetypes.guess_type(original_name)[0] or ''
    # Re-derive mime from converted extension so HEIC→JPG is flagged as image
    if ext == '.jpg':
        mime = 'image/jpeg'
    is_image = mime.startswith('image/')

    # Full public URL — the agent (and the index) always get the complete location
    url_full = f"https://{request.host}/uploads/{safe_name}"

    result = {
        'original_name': original_name,
        'path': str(dest),
        'filename': safe_name,
        'url': f'/uploads/{safe_name}',
        'url_full': url_full,
        'agent_path': f'workspace/uploads/{safe_name}',
        'file_size': file_size,
    }

    append_upload_index({
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'filename': safe_name,
        'original_name': original_name,
        'url': url_full,
        'agent_path': f'workspace/uploads/{safe_name}',
        'source': 'chat',
        'from': '',
        'mime': mime,
        'size': file_size,
        'note': '',
    })

    if is_image:
        result['type'] = 'image'
        return jsonify(result)

    result['type'] = 'file'

    # --- Extract readable content by type ---
    _BINARY_EXTS = {'.pdf', '.docx', '.xlsx', '.pptx'}

    try:
        if ext in _BINARY_EXTS:
            # Try shared skill-runner first (preferred — keeps this container lean)
            text = _call_skill_runner(dest, original_name)

            # Fall back to local extractors if skill-runner unavailable
            if text is None:
                if ext == '.pdf':
                    text = _extract_pdf(dest)
                elif ext == '.docx':
                    text = _extract_docx(dest)
                elif ext == '.xlsx':
                    text = _extract_xlsx(dest)
                elif ext == '.pptx':
                    text = _extract_pptx(dest)

            if text:
                result['content_preview'] = text
                result['extracted_type'] = ext.lstrip('.')
            else:
                result['extraction_error'] = (
                    f'Could not extract text from {ext} file. '
                    'Install skill-runner or document packages to enable this.'
                )

        else:
            # Plain text / code / CSV — stream the first _MAX_PREVIEW_CHARS only
            text_types = {'text/', 'application/json', 'application/xml', 'application/javascript'}
            if any(mime.startswith(t) for t in text_types) or ext in {
                '.txt', '.md', '.csv', '.log',
                '.py', '.js', '.ts', '.json', '.yaml', '.yml',
                '.html', '.css',
            }:
                preview, truncated = _read_text_preview(dest)
                result['content_preview'] = preview
                if truncated:
                    result['content_preview_truncated'] = True

    except Exception as exc:
        logger.warning('Document extraction failed for %s: %s', original_name, exc)
        result['extraction_error'] = f'Could not extract text from {ext} file'

    return jsonify(result)


# ---------------------------------------------------------------------------
# Chunked upload — files larger than one request can carry.
#
# Every *.jam-bot.com host is Cloudflare-proxied and the edge refuses any request body over
# ~100 MB before the origin sees it (measured 2026-11-14: a 131 MB phone .MOV POSTed to
# /api/upload got `413 ... cloudflare` after 5.3 MB). A phone video is routinely bigger than
# that, so the client sends the file as sequential slices (each well under the edge cap) and
# the slice route appends them to one hidden partial file, then renames it into uploads/ when
# the last byte arrives. Nothing is deleted on the way: the partial IS the final file.
#
# Two steps (review of PR #586):
#
#   POST /api/upload/chunk/start   JSON or form: name, size[, grant]
#       -> {upload_id, size, expires}
#     Needs a credential: a Clerk session (Bearer or __session cookie), the tenant agent key,
#     or the upload GRANT that routes/canvas.py embeds in a canvas page when it serves that page
#     to a signed-in viewer. The grant exists because a canvas page holds at best one pushed
#     60 s Clerk token, and a page opened from a texted link holds none. Start validates and
#     FREEZES the name, extension and declared size, RESERVES that size against the per-container
#     cap and the shared-disk floor, creates the empty partial, and returns a SERVER-ISSUED
#     upload_id signed over (id, size, expiry, tenant).
#
#   POST /api/upload/chunk         multipart: upload_id, offset, file[, size]
#     Public in the auth gate, because a long upload outlives any session token: the signed
#     upload_id IS the credential. An id this server did not sign, an expired id, or a size
#     different from the one frozen at start is refused before a byte lands. Only start ever
#     creates a partial (slices open it without O_CREAT). A retried slice (offset <= bytes
#     held) truncates back to its offset and rewrites, so a lost response never duplicates
#     bytes; a slice past the end answers 409 with what is held. Free space is re-checked on
#     every slice, because /mnt/clients is ONE disk shared by every tenant on the host.
#
# Files in uploads/.chunks/ (a dot-dir: never served, never listed):
#   <id>.meta.json  frozen name/ext/size/expiry      <id>.partial   the bytes so far
#   <id>.done.json  the finished result, written BEFORE the partial is renamed, so a slice
#                   that finds the partial gone always finds the result (idempotent finish)
#   .grant.key      this instance's HMAC key (same volume, so every worker and restart agree)
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


_MAX_CHUNKED_TOTAL_BYTES = _env_int('OVU_CHUNKED_UPLOAD_MAX_MB', 4096) * 1024 * 1024
# Declared sizes of UNFINISHED uploads, reserved at start (not bytes already written, which an
# opener could keep at one byte each and then fill). An upload whose id has expired stops
# reserving its declared size but keeps counting the bytes it actually holds on disk, so an
# abandoned partial still counts until it is finished or an operator moves it out.
_MAX_CHUNKED_INFLIGHT_BYTES = _env_int('OVU_CHUNKED_INFLIGHT_MAX_MB', 8192) * 1024 * 1024
# Disk that must stay free after this upload's remaining bytes land, checked at start (against
# every live reservation) and again on every slice. The per-container cap alone cannot protect
# a disk that many containers share and that may have less free space than one cap.
_CHUNK_FREE_FLOOR_BYTES = _env_int('OVU_UPLOAD_FREE_FLOOR_MB', 2048) * 1024 * 1024
_CHUNK_UPLOAD_TTL_S = _env_int('OVU_CHUNKED_UPLOAD_TTL_H', 24) * 3600   # a started upload
_CHUNK_GRANT_TTL_S = _env_int('OVU_UPLOAD_GRANT_TTL_H', 12) * 3600      # a page-embedded grant

_CHUNK_TOKEN_RE = re.compile(r'^c1\.([0-9a-f]{24})\.([0-9]{1,13})\.([0-9]{1,12})\.([0-9a-f]{64})$')
_CHUNK_GRANT_RE = re.compile(r'^g1\.([0-9]{1,12})\.([0-9a-f]{16})\.([0-9a-f]{64})$')
_CHUNK_CTRL_RE = re.compile(r'[\x00-\x1f\x7f]')


def _chunk_dir() -> Path:
    d = UPLOADS_DIR / '.chunks'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chunk_key() -> bytes:
    """This instance's signing key. Created once with O_EXCL; a reader that catches another
    worker between create and write waits for the bytes instead of minting a second key."""
    import secrets
    import time
    p = _chunk_dir() / '.grant.key'
    for _ in range(50):
        try:
            raw = p.read_bytes().strip()
            if len(raw) >= 32:
                return raw
        except FileNotFoundError:
            try:
                fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            with os.fdopen(fd, 'wb') as fh:
                fh.write(secrets.token_hex(32).encode('ascii'))
                fh.flush()
                os.fsync(fh.fileno())
            continue
        time.sleep(0.02)
    raise RuntimeError('chunked upload signing key is unreadable')


def _chunk_tenant() -> str:
    """What a signature is bound to: the tenant name when the container has one, else the host
    the request came in on. A token minted for one tenant never verifies on another."""
    t = (os.getenv('JAMBOT_TENANT') or os.getenv('TENANT_NAME') or '').strip().lower()
    return t or (request.host or '').split(':')[0].lower()


def _chunk_sig(*parts) -> str:
    import hashlib
    msg = '|'.join(str(p) for p in parts).encode('utf-8')
    return hmac.new(_chunk_key(), msg, hashlib.sha256).hexdigest()


def mint_upload_grant(ttl: int | None = None) -> str:
    """A short-lived grant to START chunked uploads, for a page served to a signed-in viewer."""
    import secrets
    import time
    exp = int(time.time()) + int(ttl or _CHUNK_GRANT_TTL_S)
    nonce = secrets.token_hex(8)
    return f'g1.{exp}.{nonce}.{_chunk_sig("grant", exp, nonce, _chunk_tenant())}'


def _upload_grant_ok(token: str) -> bool:
    import time
    m = _CHUNK_GRANT_RE.match((token or '').strip())
    if not m:
        return False
    exp, now = int(m.group(1)), time.time()
    if exp <= now or exp > now + _CHUNK_GRANT_TTL_S + 60:
        return False
    return hmac.compare_digest(_chunk_sig('grant', exp, m.group(2), _chunk_tenant()), m.group(3))


def _clerk_configured() -> bool:
    return bool((os.getenv('CLERK_PUBLISHABLE_KEY') or os.getenv('VITE_CLERK_PUBLISHABLE_KEY', '')).strip())


def _chunk_start_credential(body: dict) -> str | None:
    """Name of the credential that may start an upload, or None. Same rules as the app's auth
    gate (which lets this path through so the page grant can work): no login configured on
    this install means open, like every other route."""
    if not _clerk_configured():
        return 'open'
    agent_key = os.getenv('AGENT_API_KEY', '').strip()
    presented = request.headers.get('X-Agent-Key', '')
    if agent_key and presented and hmac.compare_digest(presented.encode('utf-8'), agent_key.encode('utf-8')):
        return 'agent-key'
    if _upload_grant_ok(request.headers.get('X-Upload-Grant') or str(body.get('grant') or '')):
        return 'grant'
    from services.auth import verify_clerk_token
    auth = request.headers.get('Authorization', '')
    # Bearer first, then the cookie: a canvas page's pushed token expires after 60 s while the
    # same-origin __session cookie may still be good.
    for tok in (auth[7:].strip() if auth.startswith('Bearer ') else '', request.cookies.get('__session', '')):
        if tok and verify_clerk_token(tok):
            return 'clerk'
    return None


def _chunk_clean_name(raw: str) -> tuple[str, str]:
    """(original_name, ext) with control characters (NUL included) stripped before anything is
    written; the extension keeps only [a-z0-9] so it can never fail at rename time."""
    name = _CHUNK_CTRL_RE.sub('', raw or '').replace('\\', '/').strip()
    name = (Path(name).name or 'upload')[:255]
    suffix = Path(name).suffix.lower()
    ext = re.sub(r'[^a-z0-9]', '', suffix[1:])[:16] if suffix else ''
    return name, (f'.{ext}' if ext else '')


def _chunk_reservations(chunk_dir: Path) -> tuple[int, int]:
    """(reserved, still_to_write) across every unfinished upload in this container."""
    import time
    now = time.time()
    reserved = pending = 0
    seen = set()
    for meta_p in chunk_dir.glob('*.meta.json'):
        uid = meta_p.name[:-len('.meta.json')]
        seen.add(uid)
        if (chunk_dir / f'{uid}.done.json').exists():
            continue
        try:
            held = (chunk_dir / f'{uid}.partial').stat().st_size
        except FileNotFoundError:
            continue  # nothing on disk and nothing can be added (slices never create it)
        try:
            meta = json.loads(meta_p.read_text(encoding='utf-8'))
            size, exp = int(meta.get('size', 0)), int(meta.get('exp', 0))
        except (OSError, ValueError, TypeError):
            size, exp = 0, 0
        if exp > now:
            reserved += max(size, held)
            pending += max(size - held, 0)
        else:
            reserved += held
    for p in chunk_dir.glob('*.partial'):   # a partial with no meta (never issued by start)
        if p.name[:-len('.partial')] not in seen:
            try:
                reserved += p.stat().st_size
            except OSError:
                pass
    return reserved, pending


def _chunk_done_marker(chunk_dir: Path, upload_id: str) -> Path:
    return chunk_dir / f'{upload_id}.done.json'


def _chunk_stored_result(done_marker: Path):
    try:
        return json.loads(done_marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


@static_files_bp.route('/api/upload/chunk/start', methods=['POST'])
def upload_file_chunk_start():
    """Authorize, validate and reserve one large upload; return its signed upload_id."""
    import fcntl
    import secrets
    import shutil
    import time

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = request.form.to_dict()
    if not _chunk_start_credential(body):
        return jsonify({
            'error': 'Sign in to upload large files — open this page from your app and try again',
            'code': 'auth_required',
            'accepted_credentials': [
                'Authorization: Bearer <Clerk session JWT>', '__session cookie (Clerk)',
                'X-Agent-Key: <AGENT_API_KEY>', 'grant: <upload grant embedded in the canvas page>',
            ],
        }), 401
    try:
        size = int(str(body.get('size', '')).strip())
    except ValueError:
        return jsonify({'error': 'size must be an integer'}), 400
    if size <= 0:
        return jsonify({'error': 'Invalid size'}), 400
    if size > _MAX_CHUNKED_TOTAL_BYTES:
        return jsonify({'error': f'File too large ({_MAX_CHUNKED_TOTAL_BYTES // (1024 * 1024)} MB max)'}), 413
    original_name, ext = _chunk_clean_name(str(body.get('name') or ''))
    raw_suffix = Path(str(body.get('name') or '')).suffix.lower()
    if ext in _BLOCKED_EXTENSIONS or raw_suffix in _BLOCKED_EXTENSIONS:
        return jsonify({'error': f'File type "{ext or raw_suffix}" is not allowed'}), 415

    chunk_dir = _chunk_dir()
    # One start at a time per container, so two concurrent starts cannot both fit under the cap.
    with open(chunk_dir / '.reserve.lock', 'a') as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            reserved, pending = _chunk_reservations(chunk_dir)
            if reserved + size > _MAX_CHUNKED_INFLIGHT_BYTES:
                logger.warning('upload: chunked start refused, %d MB reserved + %d MB > cap %d MB',
                               reserved >> 20, size >> 20, _MAX_CHUNKED_INFLIGHT_BYTES >> 20)
                return jsonify({'error': 'Too many unfinished large uploads on this server — try again later'}), 507
            free = shutil.disk_usage(str(chunk_dir)).free
            if free - pending - size < _CHUNK_FREE_FLOOR_BYTES:
                logger.warning('upload: chunked start refused, free %d MB - pending %d MB - %d MB < floor %d MB',
                               free >> 20, pending >> 20, size >> 20, _CHUNK_FREE_FLOOR_BYTES >> 20)
                return jsonify({'error': 'Not enough storage space for this file'}), 507
            uid = secrets.token_hex(12)
            exp = int(time.time()) + _CHUNK_UPLOAD_TTL_S
            meta = {'id': uid, 'size': size, 'name': original_name, 'ext': ext, 'exp': exp,
                    'tenant': _chunk_tenant(), 'started': int(time.time())}
            fd = os.open(str(chunk_dir / f'{uid}.meta.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(json.dumps(meta))
            os.close(os.open(str(chunk_dir / f'{uid}.partial'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)

    token = f'c1.{uid}.{size}.{exp}.{_chunk_sig("upload", uid, size, exp, _chunk_tenant())}'
    logger.info('upload: chunked upload started %s (%d bytes) id=%s', original_name, size, uid)
    return jsonify({'ok': True, 'upload_id': token, 'size': size, 'expires': exp})


def _chunk_finish(chunk_dir: Path, uid: str, partial: Path, done_marker: Path, meta: dict) -> dict:
    """Move a complete partial into uploads/. Called holding the partial's lock. The result is
    written FIRST, then the rename: a slice that finds the partial gone always finds the result,
    and a crash between the two is finished by the next slice under the same name."""
    import mimetypes
    import uuid
    result = _chunk_stored_result(done_marker)
    if result is None:
        original_name, ext = meta['name'], meta['ext']
        stem_safe = re.sub(r'[^A-Za-z0-9._-]+', '_', Path(original_name).stem)[:80].strip('._-') or 'file'
        safe_name = f"{stem_safe}__{uuid.uuid4().hex[:8]}{ext}"
        dest = UPLOADS_DIR / safe_name
        mime = mimetypes.guess_type(original_name)[0] or ''
        result = {
            'original_name': original_name,
            'path': str(dest),
            'filename': safe_name,
            'url': f'/uploads/{safe_name}',
            'url_full': f"https://{request.host}/uploads/{safe_name}",
            'agent_path': f'workspace/uploads/{safe_name}',
            'file_size': int(meta['size']),
            'mime': mime,
            'type': 'image' if mime.startswith('image/') else 'file',
        }
        tmp = chunk_dir / f'{uid}.done.json.tmp'
        tmp.write_text(json.dumps(result), encoding='utf-8')
        os.replace(tmp, done_marker)
    dest = UPLOADS_DIR / result['filename']
    os.replace(partial, dest)
    try:
        dest.chmod(0o644)
    except OSError:
        pass
    logger.info('upload: chunked upload complete %s (%d bytes) as %s',
                result['original_name'], result['file_size'], result['filename'])
    append_upload_index({
        'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'filename': result['filename'],
        'original_name': result['original_name'],
        'url': result['url_full'],
        'agent_path': result['agent_path'],
        'source': 'chat',
        'from': '',
        'mime': result.get('mime', ''),
        'size': result['file_size'],
        'note': 'chunked',
    })
    return result


@static_files_bp.route('/api/upload/chunk', methods=['POST'])
def upload_file_chunk():
    """Append one slice of a started upload; finalize into uploads/ on the last slice."""
    import fcntl
    import shutil
    import time

    m = _CHUNK_TOKEN_RE.match((request.form.get('upload_id') or '').strip())
    if not m:
        return jsonify({'error': 'Invalid upload_id — start the upload first (POST /api/upload/chunk/start)',
                        'code': 'start_required'}), 400
    uid, size, exp = m.group(1), int(m.group(2)), int(m.group(3))
    if not hmac.compare_digest(_chunk_sig('upload', uid, size, exp, _chunk_tenant()), m.group(4)):
        return jsonify({'error': 'upload_id was not issued by this server', 'code': 'start_required'}), 403

    chunk_dir = _chunk_dir()
    partial = chunk_dir / f'{uid}.partial'
    done_marker = _chunk_done_marker(chunk_dir, uid)

    # A slice for an upload that already finished (its response was lost and the client resent
    # the last slice): answer with the stored result, even after the id expired.
    if not partial.exists():
        stored = _chunk_stored_result(done_marker)
        if stored is not None:
            return jsonify(stored)
    if exp <= time.time():
        return jsonify({'error': 'This upload expired — start it again', 'code': 'start_required'}), 410
    try:
        meta = json.loads((chunk_dir / f'{uid}.meta.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        meta = None
    if not meta or int(meta.get('size', -1)) != size:
        return jsonify({'error': 'This upload is no longer held — start it again', 'code': 'start_required'}), 410

    declared = (request.form.get('size') or '').strip()
    if declared and declared != str(size):
        return jsonify({'error': 'size is fixed when the upload starts', 'size': size}), 400
    try:
        offset = int(request.form.get('offset', ''))
    except ValueError:
        return jsonify({'error': 'offset must be an integer'}), 400
    if offset < 0 or offset >= size:
        return jsonify({'error': 'Invalid offset'}), 400

    piece = request.files.get('file')
    if piece is None:
        return jsonify({'error': 'No file provided'}), 400
    piece.stream.seek(0, 2)
    piece_len = piece.stream.tell()
    piece.stream.seek(0)
    if piece_len <= 0:
        return jsonify({'error': 'Empty slice — re-attach the file and try again'}), 400
    if offset + piece_len > size:
        return jsonify({'error': 'Slice runs past the declared size'}), 400

    # The shared disk, re-checked on every slice: what this upload still has to write must fit
    # above the floor (other tenants' uploads and the server itself fill the same disk).
    free = shutil.disk_usage(str(chunk_dir)).free
    if free - (size - offset) < _CHUNK_FREE_FLOOR_BYTES:
        logger.warning('upload: chunked slice refused, free %d MB - remaining %d MB < floor %d MB',
                       free >> 20, (size - offset) >> 20, _CHUNK_FREE_FLOOR_BYTES >> 20)
        return jsonify({'error': 'Not enough storage space for this file', 'received': offset}), 507

    try:
        fd = os.open(str(partial), os.O_RDWR)   # never O_CREAT: only start creates a partial
    except FileNotFoundError:
        stored = _chunk_stored_result(done_marker)
        if stored is not None:
            return jsonify(stored)
        return jsonify({'error': 'This upload is no longer held — start it again', 'code': 'start_required'}), 410

    with os.fdopen(fd, 'r+b') as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            # Another request may have finished the upload (renamed the partial into uploads/)
            # while this one waited for the lock: this handle then points at the FINAL file,
            # which must not be rewritten.
            try:
                same = os.fstat(fh.fileno()).st_ino == os.stat(partial).st_ino
            except FileNotFoundError:
                same = False
            if not same:
                stored = _chunk_stored_result(done_marker)
                if stored is not None:
                    return jsonify(stored)
                return jsonify({'error': 'Upload state changed — retry'}), 409
            if done_marker.exists():
                # A finish wrote its result and stopped before the rename: complete THAT finish
                # (same name) instead of touching the bytes.
                return jsonify(_chunk_finish(chunk_dir, uid, partial, done_marker, meta))
            fh.seek(0, 2)
            held = fh.tell()
            if offset > held:
                return jsonify({'error': 'Missing earlier bytes', 'received': held}), 409
            if offset < held:
                fh.truncate(offset)  # retried slice: rewrite from its own offset
            fh.seek(offset)
            shutil.copyfileobj(piece.stream, fh, 1024 * 1024)
            fh.flush()
            os.fsync(fh.fileno())
            held = fh.tell()
            if held < size:
                return jsonify({'ok': True, 'received': held, 'size': size})
            return jsonify(_chunk_finish(chunk_dir, uid, partial, done_marker, meta))
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@static_files_bp.route('/static/emulators/<path:filepath>')
def serve_emulator(filepath):
    """Serve bundled emulator files (js-dos, etc.) from /app/static/emulators/."""
    emulators_dir = STATIC_DIR / 'emulators'
    path = _safe_path(emulators_dir, filepath)
    if path is None:
        return jsonify({"error": "Invalid path"}), 400
    if not path.exists():
        return jsonify({"error": "File not found"}), 404
    mime_types = {'.js': 'application/javascript', '.css': 'text/css', '.wasm': 'application/wasm'}
    mime = mime_types.get(path.suffix, 'application/octet-stream')
    response = send_file(path, mimetype=mime)
    response.headers['Cache-Control'] = 'public, max-age=86400'
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# ---------------------------------------------------------------------------
# Uploads auth (2026-10-06): /uploads/ was served to anyone with the URL, and search engines index it.
# A plain login gate would BREAK legitimate machine consumers (scripts fetching files over HTTP, MMS
# providers fetching media), so this ships in phases, controlled by UPLOADS_AUTH_MODE:
#   off     — no check (self-hosted default when CANVAS_REQUIRE_AUTH is off)
#   shadow  — serve as before, but log every request that WOULD be denied (the default)
#   enforce — deny unless the request carries a grant (401, or a login redirect for browsers)
# A request is granted by ANY of:
#   session    — a valid Clerk token (the same check canvas pages use)
#   signature  — ?exp=&sig= minted by services/upload_links.py with this instance's UPLOADS_SIGNING_KEY
#   agent_key  — the internal X-Agent-Key header matching AGENT_API_KEY (same check as app.require_auth)
# Each would-deny row records the state of all three grants, so enforcement can be decided per consumer.
# The would-deny log is a dotfile, so serve_upload never exposes it.
# ---------------------------------------------------------------------------
UPLOADS_AUTH_MODE = os.getenv('UPLOADS_AUTH_MODE', 'shadow').strip().lower()
UPLOADS_REQUIRE_AUTH = os.getenv('CANVAS_REQUIRE_AUTH', 'false').lower() == 'true'
UPLOADS_SHADOW_LOG = UPLOADS_DIR / '.uploads-auth-shadow.jsonl'
_UPLOADS_SHADOW_MAX_BYTES = 5 * 1024 * 1024

# Always-private areas (2026-11-09). The phased mode above still leaves /uploads/ public on every
# instance (shadow), but some areas are client intake that is never a page asset: Field Dock photos
# are receipts / work orders / invoices with a Bill-To name, address and phone, and uploads/sms/ is
# raw inbound MMS. These, and the /api/uploads listing (the index of every filename), are ENFORCED
# whenever CANVAS_REQUIRE_AUTH is on, whatever UPLOADS_AUTH_MODE says, with the same three grants.
# Prefixes are relative to UPLOADS_DIR and matched on the RESOLVED path (so '../', './', '//' and
# symlinks cannot walk around them). An instance adds its own with UPLOADS_PRIVATE_PREFIXES
# (comma-separated, e.g. "receipts/,leads/"); the built-in ones cannot be removed by env.
# Top-level sms-*.jpg (sms-router MMS saves) are NOT here: they are deliberately public website
# assets (sms-router app.py) and are used by public canvas pages; they wait for enforce mode.
_BUILTIN_PRIVATE_PREFIXES = ('field-dock/', 'sms/')


def _private_prefixes():
    extra = os.getenv('UPLOADS_PRIVATE_PREFIXES', '')
    out = list(_BUILTIN_PRIVATE_PREFIXES)
    for p in extra.split(','):
        p = p.strip().strip('/')
        if p and not p.startswith('.') and '..' not in p:
            out.append(p + '/')
    return tuple(out)


def _upload_is_private(rel_path):
    """True when a path relative to UPLOADS_DIR (posix, already resolved) is in an always-private
    area. Case-folded so 'Field-Dock/' cannot slip past on a case-insensitive filesystem. The area's
    own directory ('field-dock', no trailing slash) counts as inside it."""
    rel = rel_path.strip('/').lower() + '/'
    return any(rel.startswith(p.lower()) for p in _private_prefixes())


def _upload_rel(path):
    """The resolved path of `path` relative to the resolved UPLOADS_DIR, posix; '' if outside."""
    try:
        return Path(os.path.realpath(path)).relative_to(Path(os.path.realpath(UPLOADS_DIR))).as_posix()
    except (ValueError, OSError):
        return ''


def _upload_has_session():
    from services.auth import get_token_from_request, verify_clerk_token
    try:
        token = get_token_from_request()
        return bool(token and verify_clerk_token(token))
    except Exception:
        return False


def _upload_session_state():
    """'ok' | 'absent' | why a presented token failed. The grant is decided by verify_clerk_token
    alone; the failure reason is a diagnostic. 'expired' is the common one: the __session cookie
    lives 30 days but carries a ~60 s Clerk token that only the app shell refreshes, so a browser
    that navigated away from the shell sends a stale token."""
    from services.auth import get_token_from_request
    if _upload_has_session():
        return 'ok'
    try:
        token = get_token_from_request()
    except Exception:
        return 'invalid'
    if not token:
        return 'absent'
    try:
        from services.auth import token_failure_reason
        why = token_failure_reason(token)
    except Exception:
        return 'invalid'
    # 'ok' here means it verified a moment after verify_clerk_token said no; the decision stands.
    return why if why in ('expired', 'not-allowed', 'no-keys') else 'invalid'


def _upload_agent_key_state():
    """The internal agent key, checked exactly as app.require_auth does (X-Agent-Key vs
    AGENT_API_KEY, no grant when the key is unset), with a constant-time comparison."""
    presented = request.headers.get('X-Agent-Key')
    if not presented:
        return 'absent'
    expected = os.getenv('AGENT_API_KEY', '').strip()
    if not expected:
        return 'unconfigured'
    return 'ok' if hmac.compare_digest(presented.encode('utf-8'), expected.encode('utf-8')) else 'invalid'


def _upload_signature_state(filename):
    from services import upload_links
    return upload_links.verify('/uploads/' + filename, request.args.get('exp'), request.args.get('sig'))


def _upload_grants(filename, listing=False):
    """Evaluate grants cheapest-first, stopping at the first that holds. Returns
    (granted_by or None, {grant: state}) — states are only complete when nothing granted.
    A listing (/api/uploads) has no signed-link grant: a signature opens one file, never the index."""
    states = {}
    checks = [('agent_key', _upload_agent_key_state)]
    if not listing:
        checks.append(('signature', lambda: _upload_signature_state(filename)))
    checks.append(('session', _upload_session_state))
    for name, check in checks:
        states[name] = check()
        if states[name] == 'ok':
            return name, states
    return None, states


def _upload_deny_reason(states):
    """The most specific reason: the first PRESENTED credential that failed, else no credential."""
    for name in ('signature', 'agent_key', 'session'):
        if states.get(name) not in (None, 'absent'):
            return f'{name}-{states[name]}'
    return 'no-credential'


def _upload_wants_html():
    return request.headers.get('Accept', '').startswith('text/html')


def _upload_enforce_action(listing=False):
    """What enforce would answer this request: a browser navigation is sent through the login
    shell, which refreshes a stale session and comes straight back (the canvas-page bounce);
    anything else gets a bare 401 and does not recover on its own."""
    return 'login-redirect' if (not listing and _upload_wants_html()) else '401'


def _upload_shadow_record(filename, exists, states=None, reason=None, listing=False, mode=None):
    try:
        if UPLOADS_SHADOW_LOG.exists() and UPLOADS_SHADOW_LOG.stat().st_size > _UPLOADS_SHADOW_MAX_BYTES:
            return
        ref = request.headers.get('Referer', '')
        row = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'file': filename[:200], 'exists': bool(exists), 'mode': mode or UPLOADS_AUTH_MODE,
            'ua': request.headers.get('User-Agent', '')[:160],
            'ref_host': re.sub(r'^https?://([^/]+).*$', r'\1', ref)[:120] if ref else '',
            'accept': request.headers.get('Accept', '')[:60],
            'reason': reason or 'no-credential',
            'grants': states or {},
            'enforce_action': _upload_enforce_action(listing),
        }
        fd = os.open(str(UPLOADS_SHADOW_LOG), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            os.write(fd, (json.dumps(row) + '\n').encode('utf-8'))
        finally:
            os.close(fd)
    except Exception as e:  # measurement must never break serving
        logger.warning('[uploads-auth] shadow record failed: %s', e)


def _upload_access_denied(filename, exists, listing=False, private=False):
    """None = serve. Otherwise the deny response.

    Every route that serves bytes from UPLOADS_DIR calls this BEFORE checking existence:
    serve_upload, routes/albums.py serve_album_photo, and the /api/uploads listing (listing=True,
    which takes no signed-link grant and always answers JSON).

    The listing and private=True (an always-private area, see _BUILTIN_PRIVATE_PREFIXES) are
    enforced whenever auth is required, in every UPLOADS_AUTH_MODE; everything else is denied in
    enforce mode only. Their denial rows are logged with mode 'always-enforce' (a real denial,
    not a would-deny)."""
    if not UPLOADS_REQUIRE_AUTH:
        return None
    # Every caller gets the private-area rule on the path as requested; callers that know the
    # RESOLVED path (serve_upload, albums) also pass private= for it.
    private = private or (not listing and _upload_is_private(filename))
    always = listing or private
    if not always and UPLOADS_AUTH_MODE not in ('shadow', 'enforce'):
        return None
    granted_by, states = _upload_grants(filename, listing=listing)
    if granted_by:
        return None
    reason = _upload_deny_reason(states)
    _upload_shadow_record(filename, exists, states, reason, listing=listing,
                          mode='always-enforce' if always else None)
    if not always and UPLOADS_AUTH_MODE != 'enforce':
        return None
    logger.warning('[uploads-auth] DENIED %s (%s)', filename[:200], reason)
    if _upload_enforce_action(listing) == 'login-redirect':
        return redirect('/?redirect=/uploads/' + filename)
    if listing:
        return jsonify({'error': 'Unauthorized', 'code': 'auth_required'}), 401
    return 'Unauthorized', 401


_UPLOADS_LISTING = '(listing) /api/uploads'


@static_files_bp.route('/api/uploads', methods=['GET'])
def list_uploads():
    """List uploaded files. Optional ?type=image|text filter.

    Moved here from server.py (uploads auth phase 3). Since 2026-11-09 the listing is ALWAYS gated
    when auth is required (every UPLOADS_AUTH_MODE): it is the index that makes every filename on the
    instance discoverable. app.require_auth also gates it now ('/api/upload' is public as an exact
    path for the POST only); this route gate stays as defense in depth. Grants: Clerk session
    (Bearer or __session cookie) or X-Agent-Key; a signed link never opens the index.
    """
    denied = _upload_access_denied(_UPLOADS_LISTING, True, listing=True)
    if denied is not None:
        return denied
    file_type = request.args.get("type")  # "image", "text", or None for all
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    text_exts = {".pdf", ".txt", ".md", ".json", ".csv", ".html", ".js", ".py", ".ts", ".css"}

    files = []
    for f in sorted(UPLOADS_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_dir() or f.name.startswith("."):
            continue
        ext = f.suffix.lower()
        is_image = ext in image_exts
        kind = "image" if is_image else ("text" if ext in text_exts else "other")
        if file_type and kind != file_type:
            continue
        stat = f.stat()
        files.append({
            "filename": f.name,
            "url": f"/uploads/{f.name}",
            "type": kind,
            "size": stat.st_size,
            "modified": stat.st_mtime,
        })

    return jsonify({"files": files, "count": len(files)})


@static_files_bp.route('/uploads/<path:filename>')
def serve_upload(filename):
    """Serve uploaded files (path traversal guarded).

    NO-CACHE POLICY (do not change without reading docs/jambot/no-cache-policy.md):
    Uploads contain icons, wallpapers, agent-generated assets, and canvas page
    media that agents and admins regenerate live. Caching breaks the "live
    updates always visible" guarantee of the canvas system. The ONLY assets
    that may be cached are known_faces photos (handled by a separate route).
    Optimize for size (smaller files), not for browser cache hits.
    """
    # dotfiles (e.g. the .uploads-index.jsonl ledger) are internal — never served
    if any(part.startswith('.') for part in Path(filename).parts):
        return jsonify({"error": "File not found"}), 404
    upload_path = _safe_path(UPLOADS_DIR, filename)
    if upload_path is None:
        return jsonify({"error": "Invalid path"}), 400
    # Uploads auth: decide access BEFORE answering whether the file exists (no 401/404 existence oracle).
    # Private areas are judged on the RESOLVED path, so './', '//', '../' and symlinks cannot dodge them.
    private = _upload_is_private(_upload_rel(upload_path)) or _upload_is_private(filename)
    denied = _upload_access_denied(filename, upload_path.exists(), private=private)
    if denied is not None:
        return denied
    if not upload_path.exists():
        return jsonify({"error": "File not found"}), 404
    resp = send_file(upload_path)
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    # F-5 (2026-07-15): neutralize STORED XSS. Uploads are user/agent-supplied; an
    # .html or .svg served with a rendering content-type executes in OUR origin and can
    # steal the Clerk session. Serve only known-safe media types inline with their real
    # mime; serve EVERYTHING else (html, svg, xml, js, ...) as a non-rendering
    # text/plain attachment. Agent code-file uploads still store + download fine — they
    # just never execute in-browser. nosniff + a locked-down CSP are belt-and-suspenders.
    _ext = upload_path.suffix.lower()
    _SAFE_INLINE = {
        '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.avif',
        '.mp4', '.webm', '.ogg', '.mov', '.mp3', '.wav', '.m4a', '.pdf',
    }
    # Inert DOWNLOAD formats (2026-10-12): office documents, CSV and zip never execute in a
    # browser, so they keep their REAL mime — still an attachment, still nosniff + the
    # sandbox CSP below. Downgrading them to text/plain made iOS Safari show a client's .xlsx
    # as mojibake and save it as "<name>.xlsx.txt". Anything not listed (html, svg, js, xml,
    # unknown) still falls back to text/plain, so the F-5 guard fails closed as before.
    _DOWNLOAD_MIME = {
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        '.xls': 'application/vnd.ms-excel',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.doc': 'application/msword',
        '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        '.ppt': 'application/vnd.ms-powerpoint',
        '.ods': 'application/vnd.oasis.opendocument.spreadsheet',
        '.odt': 'application/vnd.oasis.opendocument.text',
        '.csv': 'text/csv; charset=utf-8',
        '.zip': 'application/zip',
    }
    if _ext not in _SAFE_INLINE:
        resp.headers['Content-Type'] = _DOWNLOAD_MIME.get(_ext, 'text/plain; charset=utf-8')
        resp.headers['Content-Disposition'] = (
            'attachment; filename="%s"' % Path(filename).name.replace('"', ''))
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    # CSP by type (2026-08-02): the strict `sandbox; default-src 'none'` neutralizes stored
    # XSS in html/svg/js, but `sandbox` with no allowlist ALSO stops the browser's built-in
    # media/pdf viewer from loading — so direct links to an uploaded .mp4/.mp3/.pdf served 200
    # but played nothing. Inert media served inline with
    # its real mime + nosniff has no script surface, so it gets a CSP that permits only
    # self-origin media/images/pdf to render while still blocking all script + framing, and
    # crucially drops `sandbox`. Non-safe types (already downgraded to a text/plain attachment
    # above) keep the maximally-locked CSP.
    if _ext in _SAFE_INLINE:
        resp.headers['Content-Security-Policy'] = (
            "default-src 'none'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "object-src 'self'; style-src 'unsafe-inline'; frame-ancestors 'none'")
    else:
        resp.headers['Content-Security-Policy'] = "default-src 'none'; sandbox; frame-ancestors 'none'"
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@static_files_bp.route('/src/<path:filepath>')
def serve_src(filepath):
    """Serve frontend source files (JS, CSS modules)"""
    # P7-T3 security: prevent path traversal (same guard used by serve_sound)
    src_path = _safe_path(APP_ROOT / 'src', filepath)
    if src_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if not src_path.exists():
        return jsonify({"error": "File not found"}), 404

    mime_types = {
        '.js': 'application/javascript',
        '.css': 'text/css',
        '.html': 'text/html',
        '.json': 'application/json',
    }
    mime_type = mime_types.get(src_path.suffix.lower(), 'text/plain')
    resp = send_file(src_path, mimetype=mime_type)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@static_files_bp.route('/known_faces/<name>/<filename>')
def serve_face_photo(name, filename):
    """Serve face photos for the My Face section.

    CACHE ALLOWED — face photos are the documented exception to the
    no-cache policy (docs/jambot/no-cache-policy.md). Face content does not
    update live and is identity-stable. A long browser cache here is a
    deliberate perf win and does NOT violate the canvas/icons rule.
    """
    photo_path = _safe_path(KNOWN_FACES_DIR, name, filename)
    if photo_path is None:
        return jsonify({"error": "Invalid path"}), 400
    if photo_path.exists():
        resp = send_file(photo_path)
        resp.headers['Cache-Control'] = 'public, max-age=86400'
        return resp
    return jsonify({"error": "Photo not found"}), 404


@static_files_bp.route('/api/dj-sound', methods=['GET'])
def handle_dj_sound():
    """
    DJ Soundboard endpoint.
    Query params:
      - action: 'list' or 'play'
      - sound: sound name (e.g., 'air_horn', 'scratch', 'siren_rise')
    Returns sound info or triggers playback.
    """
    action = request.args.get('action', 'list')
    sound = request.args.get('sound', '')

    if action == 'list':
        sounds_list = [
            {
                'name': name,
                'description': info['description'],
                'when_to_use': info['when_to_use'],
                'available': (SOUNDS_DIR / f"{name}.mp3").exists(),
            }
            for name, info in DJ_SOUNDS.items()
        ]
        return jsonify({
            'action': 'list',
            'sounds': sounds_list,
            'count': len(sounds_list),
            'response': (
                f"Soundboard loaded! {len(sounds_list)} effects ready. "
                "I got air horns, sirens, scratches, crowd effects, and more!"
            ),
        })

    if action == 'play':
        if not sound:
            sound = random.choice(list(DJ_SOUNDS.keys()))

        sound_lower = sound.lower().replace(' ', '_').replace('-', '_')

        matched = next(
            (name for name in DJ_SOUNDS if sound_lower in name or name in sound_lower),
            None,
        )
        if not matched:
            matched = next(
                (name for name in DJ_SOUNDS
                 if any(word in name for word in sound_lower.split('_'))),
                None,
            )

        if not matched:
            return jsonify({
                'action': 'error',
                'response': (
                    f"No sound matching '{sound}'. "
                    "Try: air_horn, siren, scratch, applause, bass_drop, rewind..."
                ),
            })

        sound_file = SOUNDS_DIR / f"{matched}.mp3"
        if not sound_file.exists():
            return jsonify({
                'action': 'error',
                'response': f"Sound file for '{matched}' not found. Need to generate it first!",
            })

        info = DJ_SOUNDS[matched]
        return jsonify({
            'action': 'play',
            'sound': matched,
            'description': info['description'],
            'url': f"/sounds/{matched}.mp3",
            'response': f"*{info['description'].upper()}* 🎵",
        })

    return jsonify({'error': 'Unknown action'}), 400


@static_files_bp.route('/manifest.json')
def serve_manifest():
    """PWA Web App Manifest — dynamically injects CLIENT_NAME for per-tenant PWA identity"""
    import json as _json, os as _os
    client_name = _os.environ.get("CLIENT_NAME", "").strip()
    path = STATIC_DIR / 'manifest.json'
    manifest = _json.loads(path.read_text())
    if client_name:
        manifest["name"] = client_name
        manifest["short_name"] = client_name
    resp = Response(
        _json.dumps(manifest, indent=2),
        mimetype='application/manifest+json'
    )
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp


@static_files_bp.route('/sw.js')
def serve_sw():
    """PWA Service Worker — must be served from root scope"""
    path = STATIC_DIR / 'sw.js'
    resp = send_file(path, mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp


@static_files_bp.route('/static/icons/<filename>')
def serve_icon(filename):
    """PWA icons.

    NO-CACHE: icons across the system are not cached. See
    docs/jambot/no-cache-policy.md.
    """
    icon_path = _safe_path(STATIC_DIR / 'icons', filename)
    if icon_path is None or not icon_path.exists():
        return jsonify({"error": "Icon not found"}), 404
    resp = send_file(icon_path, mimetype='image/png')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@static_files_bp.route('/install')
def serve_install():
    """PWA install landing page"""
    path = STATIC_DIR / 'install.html'
    resp = send_file(path, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp


@static_files_bp.route('/admin')
def serve_admin():
    """Serve the OpenUI admin dashboard"""
    admin_path = APP_ROOT / 'src' / 'admin.html'
    if not admin_path.exists():
        return jsonify({"error": "Admin dashboard not found"}), 404
    resp = send_file(admin_path, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp
