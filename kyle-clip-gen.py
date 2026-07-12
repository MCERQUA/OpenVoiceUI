#!/usr/bin/env python3
"""kyle-clip-gen.py — generate a TTS clip in bhb's REAL Kyle clone voice.

Runs INSIDE openvoiceui-bhb so it uses the container's working RESEMBLE_API_KEY and the
identical provider code path the live voice uses. Kyle = Resemble UUID 704cb696; the
stoner style (exaggeration 0.6 + the deep-stoner-drawl prompt) is auto-applied from
resemble_voice_styles.json — no need to pass it. Long text is chunked at 400 chars so
Resemble doesn't drop it. Output WAV header is normalized so the clip plays everywhere.

Usage (in-container):  python3 /app/kyle-clip-gen.py "text to speak" /path/out.wav
"""
import sys, os, struct, time
sys.path.insert(0, '/app')
from services.tts import generate_tts_chunked, get_provider

VOICE = "704cb696"  # Kyle Valhalla clone
ATTEMPTS = 8         # Resemble's cluster intermittently 500s; clips are offline so retry hard


def _is_wav(b) -> bool:
    return bool(b) and len(b) > 1000 and b[:4] == b'RIFF' and b[8:12] == b'WAVE'


def synth(text: str) -> bytes:
    """Generate Kyle audio with hard retry — a Resemble 500 returns an HTML error body,
    not audio, so we validate the WAV header and retry until we get real audio."""
    prov = get_provider('resemble')
    last = "?"
    for i in range(ATTEMPTS):
        try:
            audio = generate_tts_chunked(prov, text, VOICE, max_chars=400)
            if _is_wav(audio):
                return audio
            last = f"got {len(audio) if audio else 0}B non-WAV (likely a Resemble 5xx error body)"
        except Exception as e:
            last = str(e)[:200]
        sys.stderr.write(f"[kyle-clip] attempt {i+1}/{ATTEMPTS} failed: {last} — retrying\n")
        sys.stderr.flush()
        time.sleep(min(2 + i, 8))
    raise RuntimeError(f"all {ATTEMPTS} attempts failed; last: {last}")


def fix_wav(b: bytes) -> bytes:
    """Resemble streams WAV with RIFF/data sizes = 0xffffffff (unknown). Rewrite them to
    the real byte counts so standalone players accept the clip."""
    if len(b) < 12 or b[:4] != b'RIFF' or b[8:12] != b'WAVE':
        return b
    n = len(b)
    ba = bytearray(b)
    struct.pack_into('<I', ba, 4, n - 8)
    pos = 12
    while pos < n - 8:
        cid = bytes(ba[pos:pos + 4])
        sz = struct.unpack_from('<I', ba, pos + 4)[0]
        if cid == b'data':
            if sz == 0xffffffff or pos + 8 + sz > n:
                struct.pack_into('<I', ba, pos + 4, n - (pos + 8))
            break
        if sz == 0xffffffff:
            break
        pos += 8 + sz
    return bytes(ba)


def main():
    if len(sys.argv) < 3:
        print("usage: kyle-clip-gen.py \"text\" /path/out.wav", file=sys.stderr)
        sys.exit(2)
    text, out = sys.argv[1], sys.argv[2]
    audio = fix_wav(synth(text))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(audio)
    # ~bytes/(2*24000) = seconds for PCM_16 mono @24k
    secs = max(0, (len(audio) - 44)) / (2 * 24000)
    print(f"OK {len(audio)} bytes (~{secs:.1f}s) -> {out}")


if __name__ == "__main__":
    main()
