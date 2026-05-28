"""
Story Engine API — generates next scene on demand.

POST /api/story/generate-scene
  Body: { story_id, choice_text, scene_history: [{title, summary}], genre, tone }
  Returns: { scene: {...}, status: "ready" }

Assets are written to the tenant's canvas-pages/stories/{story_id}/ directory.
"""

import os
import json
import time
import base64
import logging
import threading
from pathlib import Path

import httpx
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

story_bp = Blueprint('story', __name__)

# ── Keys ──────────────────────────────────────────────────────────────────────
HF_TOKEN        = os.getenv('HF_TOKEN', '')
SUNO_API_KEY    = os.getenv('SUNO_API_KEY', '')
RESEMBLE_KEY    = os.getenv('RESEMBLE_API_KEY', '')
OPENAI_API_KEY  = os.getenv('OPENAI_API_KEY', '')

# Canvas pages dir (mounted volume)
CANVAS_PAGES_DIR = Path(os.getenv('CANVAS_PAGES_DIR', '/app/runtime/canvas-pages'))

# ── Resemble voices for story use ─────────────────────────────────────────────
STORY_VOICES = {
    'narrator': {'uuid': '819fcc57', 'exaggeration': 0.3,  'prompt': 'calm measured mysterious storyteller'},
    'female_1': {'uuid': 'fb2d2858', 'exaggeration': 0.65, 'prompt': 'young woman, cautious but brave'},
    'female_2': {'uuid': 'a9fc5a41', 'exaggeration': 0.55, 'prompt': 'warm confident female voice'},
    'male_1':   {'uuid': '6e37aa15', 'exaggeration': 0.7,  'prompt': 'deep commanding male voice'},
    'male_2':   {'uuid': '316d5642', 'exaggeration': 0.5,  'prompt': 'calm measured male narrator'},
    'villain':  {'uuid': '6e37aa15', 'exaggeration': 0.9,  'prompt': 'cold menacing, slow and deliberate'},
}

# ── LLM: generate scene JSON ──────────────────────────────────────────────────
SCENE_SYSTEM = """You are a story scene generator. Given a choice the player made, generate the next scene as JSON.

Return ONLY valid JSON matching this exact schema:
{
  "title": "Scene title (3-6 words)",
  "image_prompt": "Detailed cinematic image description for FLUX.1 image generation. Include: setting, lighting, mood, style. 30-50 words.",
  "ambient": {
    "prompt": "Suno sound prompt for ambient loop (15-30 words)",
    "soundKey": "Am|C|Dm|Em|Bm|Any",
    "volume": 0.35
  },
  "sfx": [
    {
      "id": "sfx_NAME",
      "prompt": "Suno sound prompt (10-20 words)",
      "trigger": "after_line_N",
      "volume": 0.8,
      "delay_ms": 0
    }
  ],
  "script": [
    {
      "type": "narration|dialogue",
      "character": "narrator|CHAR_KEY",
      "text": "Spoken line (max 40 words)"
    }
  ],
  "choices": [
    {"id": "choice_KEY", "text": "Button label (4-8 words)"},
    {"id": "choice_KEY2", "text": "Button label (4-8 words)"}
  ]
}

Rules:
- 3-5 script lines total
- 2-4 sfx entries — place them at meaningful dramatic moments (after_line_0 through after_line_N-1 where N = script length, or after_line_N for when choices appear)
- sfx prompts must be short, specific sound descriptions (no music, just sound effects or ambience)
- 2-3 choices (or 0 if this is a story ending)
- Keep narration immersive and tense — 20-35 words per line
- dialogue lines max 15 words
- Characters: narrator always exists. Other characters use keys like elara, guard, merchant — consistent with story context"""


def generate_scene_json(choice_text: str, scene_history: list, genre: str, tone: str) -> dict:
    history_text = '\n'.join(
        f"Scene {i+1}: {s.get('title','')} — {s.get('summary','')}"
        for i, s in enumerate(scene_history)
    )
    user_msg = f"""Genre: {genre}
Tone: {tone}
Story so far:
{history_text}

Player chose: "{choice_text}"

Generate the next scene JSON."""

    r = httpx.post(
        'https://api.openai.com/v1/chat/completions',
        json={
            'model': 'gpt-4o-mini',
            'messages': [
                {'role': 'system', 'content': SCENE_SYSTEM},
                {'role': 'user',   'content': user_msg},
            ],
            'temperature': 0.85,
            'max_tokens': 1200,
            'response_format': {'type': 'json_object'},
        },
        headers={'Authorization': f'Bearer {OPENAI_API_KEY}'},
        timeout=30.0,
    )
    r.raise_for_status()
    content = r.json()['choices'][0]['message']['content']
    return json.loads(content)


# ── Asset generators ──────────────────────────────────────────────────────────

def gen_image(prompt: str, out_path: Path) -> bool:
    try:
        r = httpx.post(
            'https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell',
            content=json.dumps({'inputs': prompt}).encode(),
            headers={'Authorization': f'Bearer {HF_TOKEN}', 'Content-Type': 'application/json'},
            timeout=60.0,
        )
        if r.status_code == 200 and 'image' in r.headers.get('content-type', ''):
            out_path.write_bytes(r.content)
            return True
        logger.error(f'[story] image gen failed: {r.status_code} {r.text[:200]}')
    except Exception as e:
        logger.error(f'[story] image gen error: {e}')
    return False


def gen_suno_sound(prompt: str, loop: bool, key: str, out_path: Path) -> bool:
    try:
        r = httpx.post(
            'https://api.sunoapi.org/api/v1/generate/sounds',
            json={'prompt': prompt, 'model': 'V5_5', 'soundLoop': loop, 'soundKey': key},
            headers={'Authorization': f'Bearer {SUNO_API_KEY}', 'Content-Type': 'application/json'},
            timeout=30.0,
        )
        if r.status_code != 200:
            logger.error(f'[story] suno submit failed: {r.text[:200]}')
            return False
        task_id = r.json().get('data', {}).get('taskId')
        for _ in range(40):
            time.sleep(5)
            poll = httpx.get(
                f'https://api.sunoapi.org/api/v1/generate/record-info?taskId={task_id}',
                headers={'Authorization': f'Bearer {SUNO_API_KEY}'},
                timeout=15.0,
            )
            pdata = poll.json().get('data', {})
            if pdata.get('status') == 'SUCCESS':
                clips = pdata.get('response', {}).get('sunoData', [])
                if clips:
                    url = clips[0].get('sourceAudioUrl') or clips[0].get('audioUrl')
                    dl = httpx.get(url, timeout=30.0, follow_redirects=True)
                    out_path.write_bytes(dl.content)
                    return True
            elif pdata.get('status') == 'FAILED':
                logger.error(f'[story] suno generation failed')
                return False
    except Exception as e:
        logger.error(f'[story] suno error: {e}')
    return False


def gen_tts_line(text: str, char_key: str, out_path: Path) -> bool:
    voice = STORY_VOICES.get(char_key, STORY_VOICES['narrator'])
    ssml = f'<speak exaggeration="{voice["exaggeration"]}" prompt="{voice["prompt"]}">{text}</speak>'
    try:
        r = httpx.post(
            'https://f.cluster.resemble.ai/stream',
            json={'voice_uuid': voice['uuid'], 'data': ssml, 'precision': 'PCM_16', 'sample_rate': 24000},
            headers={'Authorization': f'Bearer {RESEMBLE_KEY}', 'Content-Type': 'application/json'},
            timeout=30.0,
        )
        if r.status_code == 200 and len(r.content) > 100:
            out_path.write_bytes(r.content)
            return True
        logger.error(f'[story] tts failed: {r.status_code}')
    except Exception as e:
        logger.error(f'[story] tts error: {e}')
    return False


# ── Main endpoint ─────────────────────────────────────────────────────────────

@story_bp.route('/api/story/generate-scene', methods=['POST'])
def generate_scene():
    data = request.get_json(force=True)
    story_id      = data.get('story_id', 'story-unknown')
    choice_text   = data.get('choice_text', '')
    scene_history = data.get('scene_history', [])
    genre         = data.get('genre', 'fantasy')
    tone          = data.get('tone', 'mysterious')
    scene_index   = data.get('scene_index', 1)  # which scene number this is

    if not choice_text:
        return jsonify({'error': 'choice_text required'}), 400

    # 1. Generate scene JSON via LLM
    try:
        scene_data = generate_scene_json(choice_text, scene_history, genre, tone)
    except Exception as e:
        logger.error(f'[story] LLM error: {e}')
        return jsonify({'error': f'Scene generation failed: {e}'}), 500

    scene_id = f'scene_{scene_index:03d}'
    story_dir = CANVAS_PAGES_DIR / 'stories' / story_id
    story_dir.mkdir(parents=True, exist_ok=True)
    try:
        story_dir.chmod(0o777)
        (CANVAS_PAGES_DIR / 'stories').chmod(0o777)
    except Exception:
        pass

    # 2. Generate all assets in parallel
    threads = []
    errors = []

    # Image
    image_file = story_dir / f'{scene_id}_image.jpg'
    def do_image():
        if not gen_image(scene_data.get('image_prompt', 'dark fantasy scene'), image_file):
            errors.append('image')
    threads.append(threading.Thread(target=do_image))

    # Ambient sound
    ambient = scene_data.get('ambient', {})
    ambient_file = story_dir / f'{scene_id}_ambient.mp3'
    def do_ambient():
        if not gen_suno_sound(
            ambient.get('prompt', 'dark ambient atmosphere'),
            True,
            ambient.get('soundKey', 'Am'),
            ambient_file
        ):
            errors.append('ambient')
    threads.append(threading.Thread(target=do_ambient))

    # SFX sounds
    sfx_list = scene_data.get('sfx', [])
    for sfx in sfx_list:
        sfx_file = story_dir / f'{scene_id}_{sfx["id"]}.mp3'
        def do_sfx(s=sfx, f=sfx_file):
            gen_suno_sound(s.get('prompt', 'sound effect'), False, 'Any', f)
        threads.append(threading.Thread(target=do_sfx))

    # TTS lines
    script = scene_data.get('script', [])
    for i, line in enumerate(script):
        line_file = story_dir / f'{scene_id}_line_{i:02d}.wav'
        def do_tts(l=line, lf=line_file):
            gen_tts_line(l.get('text', ''), l.get('character', 'narrator'), lf)
        threads.append(threading.Thread(target=do_tts))

    for t in threads: t.start()
    for t in threads: t.join()

    # 3. Build the scene object the canvas needs (with resolved file paths)
    base = f'stories/{story_id}'

    # Rebuild sounds array from scene_data + resolved paths
    sounds = []
    if ambient_file.exists():
        sounds.append({
            'id': 'ambient',
            'role': 'ambient',
            'file': f'{base}/{scene_id}_ambient.mp3',
            'trigger': 'scene_start',
            'volume': ambient.get('volume', 0.35),
        })
    for sfx in sfx_list:
        sfx_file = story_dir / f'{scene_id}_{sfx["id"]}.mp3'
        if sfx_file.exists():
            sounds.append({
                'id': sfx['id'],
                'role': 'sfx',
                'file': f'{base}/{scene_id}_{sfx["id"]}.mp3',
                'trigger': sfx.get('trigger', 'after_line_0'),
                'volume': sfx.get('volume', 0.8),
                'delay_ms': sfx.get('delay_ms', 0),
            })

    # Rebuild script with resolved audio paths
    resolved_script = []
    for i, line in enumerate(script):
        line_file = story_dir / f'{scene_id}_line_{i:02d}.wav'
        resolved_script.append({
            'type':      line.get('type', 'narration'),
            'character': line.get('character', 'narrator'),
            'text':      line.get('text', ''),
            'audio':     f'{base}/{scene_id}_line_{i:02d}.wav' if line_file.exists() else None,
        })

    scene_out = {
        'scene_id':   scene_id,
        'title':      scene_data.get('title', 'Unknown'),
        'image_file': f'{base}/{scene_id}_image.jpg' if image_file.exists() else None,
        'sounds':     sounds,
        'script':     resolved_script,
        'choices':    scene_data.get('choices', []),
    }

    return jsonify({'status': 'ready', 'scene': scene_out})
