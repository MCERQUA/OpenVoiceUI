"""Suno defects found while auditing the bundled suno-music skill (2026-10-16).

1. `[JINGLE_GENERATE:...]` was read aloud: no strip chain (server TTS cleaner, browser display
   and stream chains) removed it, and the uncommitted-promise detector did not count it as work.
2. The tag's 5th field (`repeat`) was parsed by the browser and then dropped before the request.
3. Sound-effect links (`/generated_music/sfx/<file>`, returned by `status`) were 404: the only
   generated-music route takes one path segment.
4. `wav_convert`, `music_video` and `generate_lyrics` were submitted and paid for, but nothing
   fetched their output: `status` polled the SONG record endpoint and the webhook only understood
   song payloads.

No test reaches sunoapi.org: requests.get / requests.post are replaced by a fake provider.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

import routes.music as music
import routes.suno as suno
from routes.conversation import WORK_ACTION_TAG_RE, clean_for_tts

ROOT = Path(__file__).resolve().parent.parent
JS_TESTS = Path(__file__).resolve().parent / 'js'
JINGLE = '[JINGLE_GENERATE:Acme Plumbing|country|m|false|1]'


# --- 1. the jingle tag is never spoken ------------------------------------------------------

def test_server_tts_cleaner_strips_the_jingle_tag():
    spoken = clean_for_tts(f'Cooking your jingle now. {JINGLE} Give it a minute.')
    assert 'JINGLE_GENERATE' not in spoken.upper() and 'Acme' not in spoken and '|' not in spoken
    assert 'Cooking your jingle now' in spoken and 'Give it a minute' in spoken


def test_a_jingle_tag_counts_as_work_for_the_promise_detector():
    # Without this, a reply whose only action is a jingle is nudged as a broken promise and
    # the nudge can buy a second paid jingle.
    assert WORK_ACTION_TAG_RE.search(f'On it. {JINGLE}')


@pytest.mark.parametrize('rel', ['src/app.js', 'src/core/VoiceSession.js'])
def test_every_browser_strip_chain_that_strips_songs_also_strips_jingles(rel):
    src = (ROOT / rel).read_text()
    song = src.count(r".replace(/\[SUNO_GENERATE:[^\]]*\]/gi, '')")
    jingle = src.count(r".replace(/\[JINGLE_GENERATE:[^\]]*\]/gi, '')")
    assert song > 0
    assert jingle == song, f'{rel}: {song} chains strip SUNO_GENERATE, {jingle} strip JINGLE_GENERATE'


# --- 2. the repeat field reaches the request -------------------------------------------------

def test_generate_jingle_sends_repeat_static():
    src = (ROOT / 'src/app.js').read_text()
    m = re.search(r'async generateJingle\(([^)]*)\)\s*\{(.*?)\n            async generate\(prompt\)', src, re.S)
    assert m, 'generateJingle not found in src/app.js'
    params, body = m.group(1), m.group(2)
    assert 'repeat' in [p.strip() for p in params.split(',')], params
    assert re.search(r'\brepeat\b', body.split('JSON.stringify', 1)[1].split('})', 1)[0])


def test_generate_jingle_sends_repeat_in_node():
    node = shutil.which('node')
    if not node:
        pytest.skip('node not installed')
    proc = subprocess.run([node, str(JS_TESTS / 'jingle-repeat.test.js')],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- fixtures for 3 and 4 ---------------------------------------------------------------------

def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    r.headers = {}
    return r


def _media(body=b'RIFFmedia'):
    r = MagicMock()
    r.status_code = 200
    r.headers = {'Content-Length': str(len(body))}
    r.iter_content.return_value = [body]
    return r


class Provider:
    """Fake sunoapi.org: each record-info endpoint answers only for its own kind."""

    def __init__(self):
        self.records = {}          # record path -> data payload
        self.calls = []

    def post(self, url, *a, **kw):
        self.calls.append(('POST', url))
        tid = {'/api/v1/wav/generate': 'wav-task-1', '/api/v1/mp4/generate': 'mp4-task-1',
               '/api/v1/lyrics': 'lyr-task-1'}.get(url.replace(suno.SUNO_API_BASE, ''), 'task-x')
        return _resp({'code': 200, 'msg': 'success', 'data': {'taskId': tid}})

    def get(self, url, *a, params=None, stream=False, **kw):
        self.calls.append(('GET', url))
        if stream:
            return _media()
        path = url.replace(suno.SUNO_API_BASE, '')
        if path in self.records:
            return _resp({'code': 200, 'msg': 'success', 'data': self.records[path]})
        # The song endpoint does not know these task ids.
        return _resp({'code': 404, 'msg': 'task not found', 'data': None})


@pytest.fixture
def env(tmp_path, monkeypatch):
    gm = tmp_path / 'generated_music'
    gm.mkdir()
    (gm / 'my-song.mp3').write_bytes(b'mp3')
    (gm / 'generated_metadata.json').write_text(json.dumps(
        {'my-song.mp3': {'title': 'My Song', 'suno_id': 'aud-1', 'task_id': 'gen-1'}}))
    for mod in (suno, music):
        monkeypatch.setattr(mod, 'GENERATED_MUSIC_DIR', gm)
    monkeypatch.setattr(suno, 'GENERATED_METADATA_FILE', gm / 'generated_metadata.json')
    monkeypatch.setattr(suno, 'SUNO_API_KEY', 'test-key')
    monkeypatch.setattr(suno, 'SUNO_CALLBACK_URL', '')
    monkeypatch.setattr(suno, 'SUNO_WEBHOOK_SECRET', '')
    monkeypatch.setattr(suno, 'suno_jobs', {})
    monkeypatch.setattr(suno, '_provider_receipt', lambda *a, **k: None)
    monkeypatch.setattr(suno, '_is_safe_download_url', lambda url: True)
    monkeypatch.setattr(suno, '_schedule_lyrics_fetch', lambda *a, **k: None)  # paid; not under test
    p = Provider()
    monkeypatch.setattr(requests, 'post', p.post)
    monkeypatch.setattr(requests, 'get', p.get)

    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(suno.suno_bp)
    app.register_blueprint(music.music_bp)

    class E:
        pass
    e = E()
    e.gm, e.provider, e.client = gm, p, app.test_client()
    return e


def _age(job_id, seconds=60):
    suno.suno_jobs[job_id]['created_at'] = time.time() - seconds


# --- 3. sound-effect (and other generated sub-folder) links are served -----------------------

def test_sfx_link_returned_by_status_is_served(env):
    (env.gm / 'sfx').mkdir()
    (env.gm / 'sfx' / 'coin-pickup.mp3').write_bytes(b'ID3sfx')
    r = env.client.get('/generated_music/sfx/coin-pickup.mp3')
    assert r.status_code == 200 and r.mimetype == 'audio/mpeg' and r.data == b'ID3sfx'
    d = env.client.get('/generated_music/sfx/coin-pickup.mp3?download=1')
    assert d.status_code == 200 and 'attachment' in d.headers['Content-Disposition']


@pytest.mark.parametrize('url', ['/generated_music/sfx/missing.mp3',
                                 '/generated_music/secrets/coin-pickup.mp3',
                                 '/generated_music/sfx/..%2Fmy-song.mp3'])
def test_generated_subfolder_route_refuses_unknown_and_traversal(env, url):
    (env.gm / 'sfx').mkdir()
    (env.gm / 'secrets').mkdir()
    (env.gm / 'secrets' / 'coin-pickup.mp3').write_bytes(b'x')
    assert env.client.get(url).status_code == 404


# --- 4. WAV / video / lyrics results are fetched and saved -----------------------------------

def test_wav_convert_result_is_fetched_saved_and_served(env):
    r = env.client.post('/api/suno', json={'action': 'wav_convert', 'filename': 'my-song.mp3'})
    job_id = r.get_json()['job_id']
    _age(job_id)
    env.provider.records['/api/v1/wav/record-info'] = {
        'taskId': 'wav-task-1', 'successFlag': 'SUCCESS',
        'response': {'audioWavUrl': 'https://cdn.example/x.wav'}}
    s = env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()
    assert s['status'] == 'complete', s
    assert s['url'].startswith('/generated_music/wav/my-song-') and s['url'].endswith('.wav')
    served = env.client.get(s['url'])
    assert served.status_code == 200 and served.mimetype == 'audio/wav'
    # A second poll does not download again.
    downloads = [c for c in env.provider.calls if c == ('GET', 'https://cdn.example/x.wav')]
    env.client.get(f'/api/suno?action=status&job_id={job_id}')
    assert [c for c in env.provider.calls if c == ('GET', 'https://cdn.example/x.wav')] == downloads


def test_music_video_result_is_fetched_saved_and_served(env):
    r = env.client.post('/api/suno', json={'action': 'music_video', 'filename': 'my-song.mp3'})
    job_id = r.get_json()['job_id']
    _age(job_id)
    env.provider.records['/api/v1/mp4/record-info'] = {
        'taskId': 'mp4-task-1', 'successFlag': 'SUCCESS',
        'response': {'videoUrl': 'https://cdn.example/v.mp4'}}
    s = env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()
    assert s['status'] == 'complete', s
    assert s['url'].startswith('/generated_music/video/') and s['url'].endswith('.mp4')
    assert env.client.get(s['url']).mimetype == 'video/mp4'


def test_generated_lyrics_come_back_from_status(env):
    r = env.client.post('/api/suno', json={'action': 'generate_lyrics', 'prompt': 'rain on a tin roof'})
    body = r.get_json()
    assert body.get('job_id'), body
    env.provider.records['/api/v1/lyrics/record-info'] = {
        'taskId': 'lyr-task-1', 'status': 'SUCCESS', 'type': 'LYRICS',
        'response': {'taskId': 'lyr-task-1', 'data': [
            {'title': 'Tin Roof', 'text': '[Verse]\nRain', 'status': 'complete', 'errorMessage': ''},
            {'title': 'Bad', 'text': '', 'status': 'failed', 'errorMessage': 'x'}]}}
    s = env.client.get(f"/api/suno?action=status&job_id={body['job_id']}").get_json()
    assert s['status'] == 'complete', s
    assert s['lyrics'] == [{'title': 'Tin Roof', 'text': '[Verse]\nRain'}]
    saved = json.loads((env.gm / 'lyrics' / 'lyrics-lyrtask1.json').read_text())
    assert saved['lyrics'][0]['title'] == 'Tin Roof'


def test_pending_and_failed_process_jobs_report_their_state(env):
    job_id = env.client.post('/api/suno', json={'action': 'wav_convert',
                                                'filename': 'my-song.mp3'}).get_json()['job_id']
    _age(job_id)
    env.provider.records['/api/v1/wav/record-info'] = {'taskId': 'wav-task-1', 'successFlag': 'PENDING'}
    assert env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()['status'] == 'generating'
    env.provider.records['/api/v1/wav/record-info'] = {
        'taskId': 'wav-task-1', 'successFlag': 'GENERATE_WAV_FAILED', 'errorMessage': 'source expired'}
    s = env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()
    assert s['status'] == 'failed' and s['reason'] == 'source expired'


def test_wav_callback_saves_the_file_and_completes_the_job(env):
    job_id = env.client.post('/api/suno', json={'action': 'wav_convert',
                                                'filename': 'my-song.mp3'}).get_json()['job_id']
    cb = {'code': 200, 'msg': 'success',
          'data': {'task_id': 'wav-task-1', 'audioWavUrl': 'https://cdn.example/x.wav'}}
    assert env.client.post('/api/suno/callback', json=cb).status_code == 200
    files = list((env.gm / 'wav').glob('*.wav'))
    assert len(files) == 1
    s = env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()
    assert s['status'] == 'complete' and s['url'] == f'/generated_music/wav/{files[0].name}'


def test_video_callback_saves_the_file(env):
    cb = {'code': 200, 'msg': 'MP4 generated successfully.',
          'data': {'task_id': 'mp4-task-9', 'video_url': 'https://cdn.example/v.mp4'}}
    assert env.client.post('/api/suno/callback', json=cb).status_code == 200
    assert len(list((env.gm / 'video').glob('*-mp4task9.mp4'))) == 1


def test_lyrics_callback_saves_the_lyrics(env):
    cb = {'code': 200, 'msg': 'All generated successfully.',
          'data': {'callbackType': 'complete', 'taskId': 'lyr-task-7',
                   'data': [{'text': '[Chorus]\nHey', 'title': 'Hey', 'status': 'complete',
                             'errorMessage': ''}]}}
    assert env.client.post('/api/suno/callback', json=cb).status_code == 200
    saved = json.loads((env.gm / 'lyrics' / 'lyrics-lyrtask7.json').read_text())
    assert saved['lyrics'] == [{'title': 'Hey', 'text': '[Chorus]\nHey'}]


def test_failed_wav_callback_marks_the_job_failed(env):
    job_id = env.client.post('/api/suno', json={'action': 'wav_convert',
                                                'filename': 'my-song.mp3'}).get_json()['job_id']
    cb = {'code': 500, 'msg': 'conversion failed', 'data': {'task_id': 'wav-task-1', 'audioWavUrl': None}}
    env.client.post('/api/suno/callback', json=cb)
    s = env.client.get(f'/api/suno?action=status&job_id={job_id}').get_json()
    assert s['status'] == 'failed'
    assert not (env.gm / 'wav').exists() or not list((env.gm / 'wav').iterdir())


def test_song_callbacks_are_not_mistaken_for_process_results(env):
    # A normal song "complete" callback still goes down the song path (audio saved to the root).
    cb = {'code': 200, 'msg': 'ok', 'data': {'callbackType': 'complete', 'task_id': 'gen-9', 'data': [
        {'id': 'aud-9', 'audio_url': 'https://cdn.example/s.mp3', 'title': 'Song Nine',
         'prompt': '[Verse]\nla', 'duration': 30}]}}
    env.client.post('/api/suno/callback', json=cb)
    assert (env.gm / 'song-nine.mp3').exists()
    assert not (env.gm / 'lyrics').exists()
