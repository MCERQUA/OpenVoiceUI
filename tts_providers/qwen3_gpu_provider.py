"""
qwen3-gpu: voice-clone client for the GPU lane's ``vcg`` endpoint.

Spec: docs/jambot/ovu-voice-clone-seam-spec.md (MIKE-AI) §2.1-2.6, §3, §4.
Wire: BLACKBOARD/mac-claude/voice-clone-gpu-wire-v0.md, as BUILT in
vcg-build-v0.1.md (where the build differs from the draft, the build wins:
port 61215, a clone never takes a card, the peer fence closes the socket).

What this client guarantees on the OVU side:

* **Bearer read PER REQUEST** from ``VCG_TOKEN_FILE`` (default
  ``/app/runtime/secrets/vcg.token``, a read-only bind outside every workspace
  mount). Never cached in module or instance state, never logged, never put in
  an exception message (only the public ``kid`` is ever logged). A missing,
  unreadable, empty or malformed token = provider unavailable and NO network
  call. Rotation needs no restart: the next request reads the new file.
* **tenant_id on every request** (body or query) from the container env, never
  from a request body. vcg rejects a token/tenant mismatch with 403
  ``tenant_mismatch``; that maps to the same enum value here.
* **Consent pre-check** before any network call: no ``consent_id``, or no
  record at ``VOICE_CLONE_SEAM_DIR/consent/<consent_id>.json``, refuses the
  clone. ``consent_id`` and ``audio_sha256`` are echoed into the metadata.
* **reference_text is REQUIRED on the wire**: recitation phrase, then the
  typed transcript, then OVU's own STT (a hook; see ``stt_transcriber``).
* **Typed errors**: every failure is a CloneError from the spec enum plus the
  six wire codes; anything unknown is ``internal`` with a message, never an
  opaque 500.

Not a default anywhere: a tenant reaches this provider only when its
``_seam/policy.json`` names ``qwen3-gpu`` AND its container holds a token.
"""

from __future__ import annotations

import email.utils
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .base_provider import (
    CLONE_ERROR_CODES, CloneCapableTTSProvider, CloneError, CloneInfo,
    CloneRequest, CloneResult, soft_retire_clone_dir,
)

logger = logging.getLogger(__name__)

PROVIDER_ID = 'qwen3-gpu'

#: vcg on res-desktop-gpu, tailnet only (vcg-build-v0.1: 61215, not the
#: drafted 61214). Overridable for a second box or for tests.
DEFAULT_VCG_URL = 'http://100.72.230.88:61215'
DEFAULT_TOKEN_FILE = '/app/runtime/secrets/vcg.token'

#: vcg1.<kid: 8 hex>.<secret: 32 bytes base64url, no padding = 43 chars>
_TOKEN_RE = re.compile(r'^vcg1\.([0-9a-f]{8})\.([A-Za-z0-9_-]{16,128})$')
#: vcg's own tenant rule (wire §7).
_TENANT_RE = re.compile(r'^[a-z0-9-]{1,40}$')
#: spec §2.2 name rule.
_NAME_RE = re.compile(r'^[A-Za-z0-9 _-]{1,40}$')
#: consent ids become a filename: no separators, no dots.
_CONSENT_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_JOB_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}$')

_AUDIO_EXTS = ('.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac')
#: vcg refuses a request body over 4 MiB (413). Leave room for the meta part
#: and multipart framing.
_MAX_AUDIO_BYTES = 4 * 1024 * 1024 - 64 * 1024
#: vcg /v1/tts text cap per call (wire §2).
_TTS_MAX_CHARS = 600


def _token_file() -> Path:
    return Path(os.environ.get('VCG_TOKEN_FILE') or DEFAULT_TOKEN_FILE)


def _base_url() -> str:
    return (os.environ.get('VCG_URL') or DEFAULT_VCG_URL).rstrip('/')


def _read_token() -> Tuple[Optional[str], str]:
    """(token, why_not). Reads the file fresh on EVERY call.

    ``why_not`` never contains any of the file's content.
    """
    path = _token_file()
    try:
        with open(path, 'rb') as f:
            raw = f.read(4096)
    except FileNotFoundError:
        return None, 'token file missing'
    except IsADirectoryError:
        return None, 'token path is not a file'
    except PermissionError:
        return None, 'token file unreadable (permission)'
    except OSError as exc:
        return None, f'token file unreadable ({exc.__class__.__name__})'
    try:
        token = raw.decode('ascii').strip()
    except UnicodeDecodeError:
        return None, 'token file is not ASCII'
    if not token:
        return None, 'token file empty'
    if not _TOKEN_RE.match(token):
        return None, 'token file malformed (expected vcg1.<kid>.<secret>)'
    return token, ''


def _kid(token: str) -> str:
    m = _TOKEN_RE.match(token or '')
    return m.group(1) if m else '?'


def _tenant() -> Tuple[Optional[str], str]:
    from services.voice_clone import tenant_id
    t = (tenant_id() or '').strip()
    if not t:
        return None, 'tenant id not set (JAMBOT_TENANT)'
    if not _TENANT_RE.match(t):
        return None, 'tenant id does not match ^[a-z0-9-]{1,40}$'
    return t, ''


def _redact(text: Any, token: Optional[str]) -> str:
    """Bound a remote message and strip the bearer from it, defensively
    (vcg never echoes it, but nothing it sends goes out unchecked)."""
    s = str(text or '')
    if token:
        s = s.replace(token, '[redacted]')
        secret = token.rsplit('.', 1)[-1]
        if len(secret) >= 8:
            s = s.replace(secret, '[redacted]')
    return s[:300]


def _retry_after(value: Optional[str]) -> Optional[int]:
    """Retry-After as whole seconds (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return max(0, int(dt.timestamp() - time.time()))


def _seam_dir() -> Path:
    import services.paths as paths
    return Path(paths.VOICE_CLONE_SEAM_DIR)


def _clones_dir() -> Path:
    """OVU-side metadata for qwen3-gpu clones (spec §4): one folder per voice
    under VOICE_CLONES_DIR/qwen3-gpu/, so the list UI works without the GPU.
    Outside the qwen3 (fal) layout, whose listing needs a metadata.json at the
    top of each entry (this folder has none)."""
    import services.paths as paths
    return Path(paths.VOICE_CLONES_DIR) / PROVIDER_ID


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _measure_duration(path: Path) -> Optional[float]:
    """Seconds of audio, or None if it cannot be measured.

    WAV via the stdlib; anything else via ffprobe (in the OVU image).
    """
    if path.suffix.lower() == '.wav':
        try:
            with wave.open(str(path), 'rb') as w:
                rate = w.getframerate()
                if rate > 0:
                    return w.getnframes() / float(rate)
        except (wave.Error, EOFError, OSError):
            pass  # not a PCM wav the stdlib reads; try ffprobe
    exe = shutil.which('ffprobe')
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
            capture_output=True, text=True, timeout=20, check=False,
        )
        return float(out.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def _transcode_for_wire(src: Path) -> Optional[Path]:
    """>4 MiB source -> 24 kHz mono FLAC next to it (wire §3: edge-trim only,
    NEVER denoise; this does neither, it only resamples). The original stays
    untouched and the derived file is kept, so a later restore can re-send the
    exact bytes the box hashed. Returns None if ffmpeg is unavailable/fails."""
    exe = shutil.which('ffmpeg')
    if not exe:
        return None
    dst = src.with_name(src.stem + '.vcg24k.flac')
    try:
        subprocess.run(
            [exe, '-nostdin', '-v', 'error', '-y', '-i', str(src),
             '-ac', '1', '-ar', '24000', '-c:a', 'flac', str(dst)],
            capture_output=True, timeout=60, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return dst if dst.is_file() else None


def _split_text(text: str, limit: int = _TTS_MAX_CHARS) -> List[str]:
    """Pack sentences into chunks of at most ``limit`` chars (vcg's cap)."""
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    cur = ''
    for sentence in re.split(r'(?<=[.!?])\s+', text):
        while len(sentence) > limit:  # one overlong sentence: wrap on a space
            cut = sentence.rfind(' ', 0, limit)
            cut = cut if cut > 0 else limit
            if cur:
                chunks.append(cur)
                cur = ''
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if not sentence:
            continue
        if cur and len(cur) + 1 + len(sentence) > limit:
            chunks.append(cur)
            cur = sentence
        else:
            cur = f'{cur} {sentence}' if cur else sentence
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def _join_wavs(parts: List[bytes]) -> bytes:
    if len(parts) == 1:
        return parts[0]
    frames = []
    params = None
    for p in parts:
        with wave.open(io.BytesIO(p), 'rb') as w:
            if params is None:
                params = w.getparams()
            frames.append(w.readframes(w.getnframes()))
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(b''.join(frames))
    return out.getvalue()


class Qwen3GpuProvider(CloneCapableTTSProvider):
    """Qwen3-TTS-12Hz-1.7B-Base on the GPU lane (res-desktop-gpu), via vcg.

    Async on the wire (POST /v1/clones -> 202 + job_id); ``create_clone``
    polls the job for up to ``clone_deadline_s`` (spec §2.4: sync clone 120 s)
    and returns ``pending`` + job_id if it is still running, so the job is
    never lost. The build makes a clone decode -> validate -> write profile,
    with no GPU card, so a pending result past 120 s should be rare.
    """

    clone_label = PROVIDER_ID
    clone_input = ('path',)
    async_clone = True
    min_ref_seconds = 10
    max_ref_seconds = 30
    clone_languages = ('English',)

    #: OVU-enforced timeouts (spec §2.4). Instance attributes so a test can
    #: shorten them; the provider cannot extend them past these defaults.
    submit_timeout_s = 30.0
    clone_deadline_s = 120.0
    poll_interval_s = 1.0
    tts_timeout_s = 120.0
    health_timeout_s = 5.0
    connect_timeout_s = 5.0

    def __init__(self, stt_transcriber: Optional[Callable[[str, Optional[str]], Optional[str]]] = None):
        super().__init__()
        # OVU's own STT, the last source of reference_text (spec PR1 notes:
        # recitation phrase, then typed text, then OVU's STT; a tenant never
        # calls a box STT). Signature: (audio_path, language) -> text | None.
        # TODO(voice-clone consent/recitation PR): wire this to OVU's server-
        # side STT (providers/stt) once that PR picks the tenant's STT. PR 1
        # exposes no STT hook on the clone path, so it is None here and a
        # clone with neither a recitation phrase nor typed text is refused
        # (bad_request) before any network call.
        self.stt_transcriber = stt_transcriber

    # ------------------------------------------------------------------
    # configuration (read per request, never cached)
    # ------------------------------------------------------------------

    def _context(self) -> Tuple[str, str]:
        """(token, tenant) for ONE request, or CloneError('unavailable').
        Called immediately before every HTTP request."""
        token, why = _read_token()
        if not token:
            raise CloneError('unavailable', f'{PROVIDER_ID} not configured: {why}')
        tenant, why = _tenant()
        if not tenant:
            raise CloneError('unavailable', f'{PROVIDER_ID} not configured: {why}')
        return token, tenant

    def _configured(self) -> bool:
        return bool(_read_token()[0]) and bool(_tenant()[0])

    @property
    def _status(self) -> str:
        """Read by tts_providers._catalog_info for a cached instance."""
        return 'active' if self._configured() else 'inactive'

    # ------------------------------------------------------------------
    # transport + error mapping
    # ------------------------------------------------------------------

    def _timeout(self, total: float) -> httpx.Timeout:
        return httpx.Timeout(total, connect=min(self.connect_timeout_s, total))

    def _request(self, method: str, route: str, *, timeout: float,
                 params: Optional[Dict[str, Any]] = None,
                 json_body: Optional[Dict[str, Any]] = None,
                 files: Optional[Dict[str, Any]] = None) -> Tuple[httpx.Response, str]:
        """One vcg call. Returns (response, token) for a 2xx; raises a mapped
        CloneError otherwise. The token is read here, fresh, every call."""
        token, tenant = self._context()
        params = dict(params or {})
        if json_body is not None:
            json_body = {**json_body, 'tenant_id': tenant}
        elif files is None:
            params['tenant_id'] = tenant
        url = f'{_base_url()}{route}'
        t0 = time.monotonic()
        try:
            with httpx.Client(timeout=self._timeout(timeout)) as c:
                resp = c.request(method, url, params=params or None,
                                 json=json_body, files=files,
                                 headers={'Authorization': f'Bearer {token}'})
        except httpx.TimeoutException as exc:
            raise CloneError('timeout', f'{PROVIDER_ID}: vcg did not answer in time '
                                        f'({exc.__class__.__name__})') from None
        except (httpx.ConnectError, httpx.RemoteProtocolError,
                httpx.ReadError, httpx.WriteError, httpx.NetworkError) as exc:
            # refused, reset, or the peer fence closing the socket (C10)
            raise CloneError('unavailable', f'{PROVIDER_ID}: vcg unreachable or closed '
                                            f'the connection ({exc.__class__.__name__})') from None
        except httpx.HTTPError as exc:
            raise CloneError('internal', f'{PROVIDER_ID}: transport error '
                                         f'({exc.__class__.__name__})') from None
        ms = int((time.monotonic() - t0) * 1000)
        logger.info('[qwen3-gpu] %s %s kid=%s tenant=%s -> %s in %dms',
                    method, route, _kid(token), tenant, resp.status_code, ms)
        if 200 <= resp.status_code < 300:
            return resp, token
        raise self._map_error(resp, token)

    def _map_error(self, resp: httpx.Response, token: str) -> CloneError:
        status = resp.status_code
        retry_after = _retry_after(resp.headers.get('Retry-After'))
        err: Dict[str, Any] = {}
        try:
            body = resp.json()
            if isinstance(body, dict) and isinstance(body.get('error'), dict):
                err = body['error']
        except ValueError:
            pass
        code = err.get('code') if isinstance(err.get('code'), str) else None
        message = _redact(err.get('message') or '', token)
        retryable = err.get('retryable') if isinstance(err.get('retryable'), bool) else None

        if code is None:
            # No vcg envelope (a proxy page, an empty body): map by status.
            if status == 503:
                code = 'busy' if retry_after is not None else 'unavailable'
            elif status == 504:
                code = 'timeout'
            elif status == 429:
                code = 'quota'
            else:
                code = 'internal'
            message = message or f'vcg HTTP {status} with no error envelope'
        elif code not in CLONE_ERROR_CODES:
            message = f'vcg returned unknown error code {code[:40]!r} (HTTP {status}): {message}'
            code, retryable = 'internal', False

        if code in ('unauthorized', 'tenant_mismatch'):
            # CONFIG alarm: this container's token is bad/revoked or belongs
            # to another tenant. kid is public; the secret never leaves here.
            logger.error('[qwen3-gpu] CONFIG ALARM %s (HTTP %s) kid=%s', code, status, _kid(token))
        return CloneError(code, message or code, retryable, retry_after)

    # ------------------------------------------------------------------
    # clone
    # ------------------------------------------------------------------

    def _check_consent(self, consent_id: Optional[str]) -> Dict[str, Any]:
        """Spec §3: a consent record must exist BEFORE any spend."""
        if not consent_id:
            raise CloneError('bad_request', f'{PROVIDER_ID}: consent_id is required')
        if not _CONSENT_ID_RE.match(consent_id):
            raise CloneError('bad_request', f'{PROVIDER_ID}: malformed consent_id')
        path = _seam_dir() / 'consent' / f'{consent_id}.json'
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            raise CloneError('consent_mismatch',
                             f'{PROVIDER_ID}: no consent record for {consent_id}') from None
        except (OSError, ValueError):
            raise CloneError('consent_mismatch',
                             f'{PROVIDER_ID}: consent record {consent_id} unreadable') from None
        if not isinstance(record, dict) or record.get('statement_accepted') is not True:
            raise CloneError('consent_mismatch',
                             f'{PROVIDER_ID}: consent record {consent_id} does not accept the statement')
        if record.get('consent_id') not in (None, consent_id):
            raise CloneError('consent_mismatch',
                             f'{PROVIDER_ID}: consent record {consent_id} names another id')
        return record

    def _reference_text(self, req: CloneRequest) -> Tuple[str, str]:
        """(text, source). Order: recitation phrase, typed text, OVU's STT."""
        for source, value in (('recitation', req.recitation_phrase),
                              ('typed', req.reference_text)):
            if value and value.strip():
                return value.strip(), source
        if self.stt_transcriber is not None and req.audio_path:
            try:
                text = self.stt_transcriber(req.audio_path, req.language)
            except Exception as exc:  # STT is best effort; never a 500
                logger.warning('[qwen3-gpu] OVU STT failed: %s', exc.__class__.__name__)
                text = None
            if text and text.strip():
                return text.strip(), 'stt'
        raise CloneError('bad_request',
                         f'{PROVIDER_ID}: reference_text is required (recite the phrase or type '
                         f'what the recording says)')

    def _prepare_audio(self, req: CloneRequest) -> Dict[str, Any]:
        if not req.audio_path:
            raise CloneError('bad_request', f'{PROVIDER_ID}: audio file upload required')
        src = Path(req.audio_path)
        ext = src.suffix.lower()
        if ext not in _AUDIO_EXTS:
            raise CloneError('bad_audio', f'{PROVIDER_ID}: unsupported audio format {ext}')
        try:
            source_bytes = src.read_bytes()
        except OSError:
            raise CloneError('bad_audio', f'{PROVIDER_ID}: reference audio unreadable') from None
        source_sha = _sha256_bytes(source_bytes)
        if req.audio_sha256 and req.audio_sha256.lower() != source_sha:
            raise CloneError('bad_audio', f'{PROVIDER_ID}: audio_sha256 does not match the saved upload')

        sent_path, sent_bytes = src, source_bytes
        if len(source_bytes) > _MAX_AUDIO_BYTES:
            flac = _transcode_for_wire(src)
            if flac is None:
                raise CloneError('bad_audio', f'{PROVIDER_ID}: reference over 4 MiB and could not be '
                                              f'transcoded to 24 kHz mono FLAC')
            sent_path, sent_bytes = flac, flac.read_bytes()
            if len(sent_bytes) > _MAX_AUDIO_BYTES:
                raise CloneError('bad_audio', f'{PROVIDER_ID}: reference still over 4 MiB after transcode')

        if sent_path is src and req.duration_s is not None:
            duration = float(req.duration_s)
        else:
            duration = _measure_duration(sent_path)
        if duration is None:
            if not shutil.which('ffprobe') and sent_path.suffix.lower() != '.wav':
                raise CloneError('internal', f'{PROVIDER_ID}: cannot measure duration (ffprobe missing)')
            raise CloneError('bad_audio', f'{PROVIDER_ID}: could not measure the reference duration')
        if duration < self.min_ref_seconds or duration > self.max_ref_seconds:
            raise CloneError('bad_audio', f'{PROVIDER_ID}: reference is {duration:.1f}s; needs '
                                          f'{self.min_ref_seconds}-{self.max_ref_seconds}s')
        return {
            'source_path': str(src), 'source_sha256': source_sha,
            'sent_path': str(sent_path), 'bytes': sent_bytes,
            'audio_sha256': _sha256_bytes(sent_bytes),
            'duration_s': round(duration, 3), 'ext': sent_path.suffix.lower(),
        }

    def create_clone(self, req: CloneRequest) -> CloneResult:
        t0 = time.monotonic()
        # 1. Configured? (token + tenant). Fail closed, no network.
        self._context()
        if req.tenant_id and req.tenant_id != _tenant()[0]:
            raise CloneError('tenant_mismatch', f'{PROVIDER_ID}: request tenant is not this container\'s')
        # 2. Everything checkable locally, before any spend.
        if not req.name or not _NAME_RE.match(req.name):
            raise CloneError('bad_request', f'{PROVIDER_ID}: name must be 1-40 of [A-Za-z0-9 _-]')
        if not req.request_id:
            raise CloneError('bad_request', f'{PROVIDER_ID}: request_id is required')
        self._check_consent(req.consent_id)
        ref_text, ref_source = self._reference_text(req)
        audio = self._prepare_audio(req)

        meta = {
            'request_id': req.request_id, 'name': req.name,
            'audio_sha256': audio['audio_sha256'], 'duration_s': audio['duration_s'],
            'reference_text': ref_text, 'language': req.language,
            'consent_id': req.consent_id, 'code_id': req.code_id,
        }  # tenant_id is added by _request from the container env
        filename = f'reference{audio["ext"]}'
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

        # 3. Submit (token read fresh inside _request).
        _, tenant = self._context()
        meta['tenant_id'] = tenant
        resp, _token = self._request(
            'POST', '/v1/clones', timeout=self.submit_timeout_s,
            files={'meta': ('meta.json', json.dumps(meta).encode(), 'application/json'),
                   'audio': (filename, audio['bytes'], mime)},
        )
        job = self._json(resp)

        # 4. Poll the job (each poll reads the token fresh) up to the deadline.
        while job.get('status') == 'pending':
            job_id = job.get('job_id') or ''
            if not _JOB_ID_RE.match(job_id):
                raise CloneError('internal', f'{PROVIDER_ID}: vcg returned a pending job with no valid job_id')
            if time.monotonic() - t0 >= self.clone_deadline_s:
                logger.warning('[qwen3-gpu] clone %s still pending after %ss; returning job_id',
                               job_id, self.clone_deadline_s)
                return CloneResult(status='pending', provider=PROVIDER_ID, name=req.name,
                                   voice_id=job.get('voice_id'), job_id=job_id,
                                   created_at=job.get('created_at') or '',
                                   clone_time_ms=int((time.monotonic() - t0) * 1000),
                                   metadata={'tenant_id': tenant, 'consent_id': req.consent_id,
                                             'audio_sha256': audio['audio_sha256'],
                                             'request_id': req.request_id})
            time.sleep(self.poll_interval_s)
            try:
                job = self._json(self._request('GET', f'/v1/jobs/{job_id}',
                                               timeout=self.submit_timeout_s)[0])
            except CloneError as exc:
                if not exc.retryable:
                    raise
                logger.warning('[qwen3-gpu] poll %s: %s (will retry)', job_id, exc.code)

        status = job.get('status')
        if status == 'failed':
            raise CloneError.from_dict(self._job_error(job))
        if status != 'ready':
            raise CloneError('internal', f'{PROVIDER_ID}: vcg returned job status {str(status)[:20]!r}')

        voice_id = job.get('voice_id') or ''
        self._check_voice_id(voice_id, tenant)
        clone = job.get('clone') if isinstance(job.get('clone'), dict) else {}
        for key, want in (('consent_id', req.consent_id), ('audio_sha256', audio['audio_sha256'])):
            got = clone.get(key)
            if got is not None and got != want:
                raise CloneError('consent_mismatch',
                                 f'{PROVIDER_ID}: vcg stored a different {key} for {voice_id}')

        created_at = clone.get('created_at') or job.get('created_at') or \
            time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        record = {
            'voice_id': voice_id, 'name': req.name, 'provider': PROVIDER_ID,
            'tenant_id': tenant, 'consent_id': req.consent_id,
            'audio_sha256': audio['audio_sha256'], 'source_sha256': audio['source_sha256'],
            'source_audio_path': audio['source_path'], 'sent_audio_path': audio['sent_path'],
            'duration_s': audio['duration_s'], 'reference_text': ref_text,
            'reference_text_source': ref_source, 'language': req.language,
            'voice_ref': job.get('voice_ref') or '', 'request_id': req.request_id,
            'job_id': job.get('job_id'), 'code_id': req.code_id, 'created_by': None,
            'created_at': created_at,
            'clone_time_ms': int((time.monotonic() - t0) * 1000),
        }
        self._write_record(record)
        logger.info('[qwen3-gpu] clone ready %s tenant=%s in %dms',
                    voice_id, tenant, record['clone_time_ms'])
        return CloneResult(status='ready', provider=PROVIDER_ID, name=req.name,
                           voice_id=voice_id, job_id=job.get('job_id'),
                           created_at=created_at, clone_time_ms=record['clone_time_ms'],
                           voice_ref=record['voice_ref'], metadata=record)

    def get_clone_status(self, job_id: str) -> CloneResult:
        if not job_id or not _JOB_ID_RE.match(job_id):
            raise CloneError('bad_request', f'{PROVIDER_ID}: malformed job_id')
        resp, _ = self._request('GET', f'/v1/jobs/{job_id}', timeout=self.submit_timeout_s)
        job = self._json(resp)
        status = job.get('status')
        if status not in ('pending', 'ready', 'failed'):
            raise CloneError('internal', f'{PROVIDER_ID}: vcg returned job status {str(status)[:20]!r}')
        # TODO(jobs-route PR, spec §2.3): a job that turns ready here has no
        # OVU metadata folder yet; the jobs route should write it on ready.
        clone = job.get('clone') if isinstance(job.get('clone'), dict) else {}
        return CloneResult(status=status, provider=PROVIDER_ID,
                           name=clone.get('name', ''),
                           voice_id=job.get('voice_id'), job_id=job_id,
                           created_at=job.get('created_at') or '',
                           voice_ref=job.get('voice_ref') or '',
                           error=self._job_error(job) if status == 'failed' else None,
                           metadata=job)

    @staticmethod
    def _job_error(job: Dict[str, Any]) -> Dict[str, Any]:
        err = job.get('error') if isinstance(job.get('error'), dict) else {}
        code = err.get('code')
        if code not in CLONE_ERROR_CODES:
            return {'code': 'internal', 'retryable': False,
                    'message': f'vcg job failed with unknown code {str(code)[:40]!r}: '
                               f'{str(err.get("message") or "")[:200]}'}
        return {'code': code, 'retryable': err.get('retryable'),
                'message': str(err.get('message') or code)[:300]}

    @staticmethod
    def _json(resp: httpx.Response) -> Dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            raise CloneError('internal', f'{PROVIDER_ID}: vcg returned non-JSON (HTTP {resp.status_code})') from None
        if not isinstance(body, dict):
            raise CloneError('internal', f'{PROVIDER_ID}: vcg returned a non-object (HTTP {resp.status_code})')
        return body

    @staticmethod
    def _check_voice_id(voice_id: str, tenant: str) -> None:
        if (not voice_id or '..' in voice_id or '/' in voice_id
                or not re.match(r'^clone_' + re.escape(tenant) + r'_[A-Za-z0-9_-]{1,100}$', voice_id)):
            raise CloneError('internal', f'{PROVIDER_ID}: vcg returned a voice_id outside this tenant')

    def _write_record(self, record: Dict[str, Any]) -> None:
        """Spec §4 metadata folder. A write failure is logged loudly but does
        not fail the clone: the voice exists on the box and list_clones (box)
        still returns it."""
        vdir = _clones_dir() / record['voice_id']  # voice_id passed _check_voice_id
        try:
            vdir.mkdir(parents=True, exist_ok=True)
            tmp = vdir / '.metadata.json.tmp'
            tmp.write_text(json.dumps(record, indent=2))
            os.replace(tmp, vdir / 'metadata.json')
        except OSError as exc:
            record['local_record'] = False
            logger.error('[qwen3-gpu] could not write metadata for %s: %s',
                         record['voice_id'], exc.__class__.__name__)

    # ------------------------------------------------------------------
    # list / disable / health
    # ------------------------------------------------------------------

    def _own_tenant(self, tenant_id: Optional[str]) -> str:
        _, tenant = self._context()
        if tenant_id and tenant_id != tenant:
            # Jail cell: never ask the box about another tenant.
            raise CloneError('tenant_mismatch', f'{PROVIDER_ID}: {tenant_id!r} is not this container\'s tenant')
        return tenant

    def list_clones(self, tenant_id: str) -> List[CloneInfo]:
        tenant = self._own_tenant(tenant_id)
        body = self._json(self._request('GET', '/v1/clones', timeout=self.submit_timeout_s,
                                        params={'include_disabled': 0})[0])
        if body.get('tenant_id') not in (None, tenant):
            raise CloneError('tenant_mismatch', f'{PROVIDER_ID}: vcg answered for another tenant')
        out = []
        prefix = f'clone_{tenant}_'
        for c in body.get('clones') or []:
            if not isinstance(c, dict) or not str(c.get('voice_id', '')).startswith(prefix):
                continue
            out.append(CloneInfo(voice_id=c['voice_id'], name=c.get('name', ''),
                                 provider=PROVIDER_ID, created_at=c.get('created_at', ''),
                                 tenant_id=tenant, consent_id=c.get('consent_id'),
                                 audio_sha256=c.get('audio_sha256'), metadata=c))
        return out

    def disable_clone(self, tenant_id: str, voice_id: str, reason: str = '') -> None:
        """Takedown on the box (reversible there: rename), then the local
        metadata folder is renamed .retired. A box 404 means the voice is
        already unspeakable, so the local record is still retired."""
        tenant = self._own_tenant(tenant_id)
        self._check_voice_id(voice_id, tenant)
        try:
            self._request('POST', f'/v1/clones/{voice_id}/disable',
                          timeout=self.submit_timeout_s, json_body={'reason': reason or ''})
        except CloneError as exc:
            if exc.code != 'not_found':
                raise
            logger.warning('[qwen3-gpu] disable %s: not on the box; retiring local record', voice_id)
        if (_clones_dir() / voice_id).is_dir():
            soft_retire_clone_dir(_clones_dir(), voice_id, reason)

    def health(self) -> Dict[str, Any]:
        try:
            body = self._json(self._request('GET', '/v1/health', timeout=self.health_timeout_s)[0])
        except CloneError as exc:
            return {'state': 'down', 'detail': f'{exc.code}: {exc.message}', 'load': None}
        load = body.get('load')
        return {
            'state': body.get('state') if body.get('state') in ('ok', 'degraded', 'down') else 'down',
            'detail': str(body.get('reason') or ''),
            'load': float(load) if isinstance(load, (int, float)) else None,
            'accepting': body.get('accepting'),
            'clone_accepting': body.get('clone_accepting'),
            'retry_after_s': body.get('retry_after_s'),
        }

    # ------------------------------------------------------------------
    # synthesis
    # ------------------------------------------------------------------

    def generate_speech(self, text: str, voice: Optional[str] = None, **kwargs) -> bytes:
        """POST /v1/tts per <=600-char chunk; WAV 24 kHz mono, joined.

        Raises RuntimeError("qwen3-gpu TTS error <HTTP>: <code> ...") so
        services/tts.py classifies it as 4xx/5xx and falls back at once
        (config/tts-fallback.json: qwen3-gpu -> groq) instead of retrying.
        """
        self.validate_text(text)
        try:
            token, tenant = self._context()
        except CloneError as exc:
            raise RuntimeError(f'{PROVIDER_ID} TTS error 503: unavailable ({exc.message})') from None
        if not voice or not voice.startswith(f'clone_{tenant}_'):
            raise RuntimeError(f'{PROVIDER_ID} TTS error 400: bad_request (speaks only this '
                               f'tenant\'s clones; got {str(voice)[:60]!r})')
        language = kwargs.get('language')
        parts = []
        for chunk in _split_text(text):
            body = {'voice_id': voice, 'text': chunk, 'format': 'wav'}
            if language:
                body['language'] = language
            try:
                resp, _ = self._request('POST', '/v1/tts', timeout=self.tts_timeout_s, json_body=body)
            except CloneError as exc:
                http = {'unavailable': 503, 'busy': 503, 'timeout': 504, 'quota': 429,
                        'not_found': 404, 'disabled': 410, 'bad_request': 400,
                        'unauthorized': 401, 'tenant_mismatch': 403}.get(exc.code, 500)
                if exc.code == 'not_found':
                    # TODO(restore PR, wire §2 "Restore"): re-submit the stored
                    # source (metadata.json sent_audio_path) with this voice_id.
                    logger.warning('[qwen3-gpu] %s not on the box (restore path not built)', voice)
                ra = f' retry-after={exc.retry_after}s' if exc.retry_after else ''
                raise RuntimeError(f'{PROVIDER_ID} TTS error {http}: {exc.code}{ra}') from None
            parts.append(resp.content)
        return _join_wavs(parts)

    # ------------------------------------------------------------------
    # provider interface (network-free: /api/tts/providers and /api/tts/voices
    # call get_info for every provider on every page load)
    # ------------------------------------------------------------------

    def _local_records(self) -> List[Dict[str, Any]]:
        base = _clones_dir()
        if not base.is_dir():
            return []
        out = []
        for vdir in sorted(base.iterdir()):
            if vdir.name.startswith(('_', '.')) or vdir.name.endswith('.retired'):
                continue
            try:
                rec = json.loads((vdir / 'metadata.json').read_text())
            except (OSError, ValueError):
                continue
            if isinstance(rec, dict) and rec.get('voice_id') == vdir.name:
                out.append(rec)
        return out

    def list_voices(self) -> List[str]:
        if not self._configured():
            return []
        return [r['voice_id'] for r in self._local_records()]

    def get_default_voice(self) -> Optional[str]:
        voices = self.list_voices()
        return voices[0] if voices else None

    def is_available(self) -> bool:
        """Configured = a well-formed token AND a valid tenant id. No network:
        busy/quiet-window surface as typed errors from the actual call, with
        Retry-After, instead of a bare 'not available'."""
        return self._configured()

    def get_info(self) -> Dict[str, Any]:
        configured = self._configured()
        records = self._local_records() if configured else []
        return {
            'name': 'Qwen3-TTS (GPU lane)',
            'provider_id': PROVIDER_ID,
            'status': 'active' if configured else 'inactive',
            'description': ('Qwen3-TTS-12Hz-1.7B-Base on the GPU lane via vcg (tailnet). '
                            'Tenant-scoped clones only. Opt-in per tenant.'),
            'quality': 'very-high',
            'latency': 'medium',
            'cost_per_minute': 0.0,
            'voices': [r['voice_id'] for r in records],
            'cloned_voices': [{'voice_id': r['voice_id'], 'name': r.get('name', '')} for r in records],
            'features': ['voice-cloning', 'remote-gpu', 'free', 'wav-output'],
            'requires_api_key': False,
            'audio_format': 'wav',
            'sample_rate': 24000,
            'max_characters': _TTS_MAX_CHARS,
            'available': configured,
            'capabilities': self.clone_capabilities(),
            'error': None if configured else 'not configured (no vcg token for this tenant)',
        }


__all__ = ['Qwen3GpuProvider', 'PROVIDER_ID', 'DEFAULT_VCG_URL', 'DEFAULT_TOKEN_FILE']
