#!/usr/bin/env python3
"""Generate voice preview samples for the BigHead character builder.

Usage: python3 generate-samples.py [openvoiceui-url]
Default: http://localhost:5009

Generates a short sample clip for each priority voice.
Add more voices to PRIORITY_VOICES to expand coverage.
"""
import sys, json, os, time
import urllib.request

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5009"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

SAMPLE_TEXT = "Hey, what's up? I'm your new character. Pretty cool, right?"

# ── Voices to generate samples for ──
# Format: (provider_id, voice_id, display_name)
# Add new voices here as providers are added
PRIORITY_VOICES = [
    # Supertonic (local, fast, free)
    ("supertonic", "M1", "Male 1"),
    ("supertonic", "M2", "Male 2"),
    ("supertonic", "M3", "Male 3"),
    ("supertonic", "F1", "Female 1"),
    ("supertonic", "F2", "Female 2"),
    ("supertonic", "F3", "Female 3"),
    # Groq Orpheus (fast, natural)
    ("groq", "autumn", "Autumn"),
    ("groq", "diana", "Diana"),
    ("groq", "hannah", "Hannah"),
    ("groq", "austin", "Austin"),
    ("groq", "daniel", "Daniel"),
    ("groq", "troy", "Troy"),
    # Resemble (cloned + best stock)
    ("resemble", "Kyle Valhalla", "Kyle Valhalla"),
    ("resemble", "Bryce BigHead", "Bryce BigHead"),
    ("resemble", "Aaron", "Aaron"),
    ("resemble", "Jessica", "Jessica"),
    ("resemble", "Ethan", "Ethan"),
    ("resemble", "Grace", "Grace"),
    ("resemble", "Archer", "Archer"),
    ("resemble", "Luna", "Luna"),
]


def safe_filename(provider, voice_id):
    """Convert provider+voice into a safe filename slug."""
    safe = f"{provider}_{voice_id}"
    for c in " ()/'\"":
        safe = safe.replace(c, "-")
    return safe


def generate_one(provider, voice_id, display_name):
    fname = safe_filename(provider, voice_id)

    # Skip if already exists
    for ext in (".wav", ".mp3"):
        if os.path.exists(os.path.join(OUT_DIR, fname + ext)):
            print(f"  SKIP {fname} (exists)")
            return True

    url = f"{BASE_URL}/api/tts/generate"
    payload = json.dumps({
        "text": SAMPLE_TEXT,
        "provider": provider,
        "voice": voice_id,
    }).encode()

    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content_type = resp.headers.get("Content-Type", "")
        audio_bytes = resp.read()

        if len(audio_bytes) < 100:
            print(f"  FAIL {fname}: response too small ({len(audio_bytes)} bytes)")
            return False

        # Determine format
        ext = ".mp3" if "mpeg" in content_type or audio_bytes[:3] == b"ID3" or (audio_bytes[0:2] == b'\xff\xfb') else ".wav"
        path = os.path.join(OUT_DIR, fname + ext)
        with open(path, "wb") as f:
            f.write(audio_bytes)
        print(f"  OK  {fname}{ext} ({len(audio_bytes):,} bytes)")
        return True

    except Exception as e:
        print(f"  FAIL {fname}: {e}")
        return False


def main():
    print(f"Voice Sample Generator")
    print(f"Server: {BASE_URL}")
    print(f"Output: {OUT_DIR}")
    print(f"Sample: \"{SAMPLE_TEXT}\"")
    print(f"Voices: {len(PRIORITY_VOICES)}")
    print()

    ok, fail = 0, 0
    for provider, voice_id, display in PRIORITY_VOICES:
        print(f"[{provider}/{display}]")
        if generate_one(provider, voice_id, display):
            ok += 1
        else:
            fail += 1
        # Small delay between cloud requests to avoid rate limits
        if provider != "supertonic":
            time.sleep(1.5)

    print(f"\nDone: {ok} OK, {fail} failed")


if __name__ == "__main__":
    main()
