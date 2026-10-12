"""LYRICS-AT-GENERATION (2026-10-12): Suno timestamped lyrics are fetched ONCE, when the
song completes — never again.

Every get-timestamped-lyrics call is CHARGED. The host cron re-bought the same 17
instrumental songs every hour because an instrumental never produced a cache file.
These tests pin the contract: one call per vocal song, zero for an instrumental or a
song without an audioId, no inline retry after a failure, and no second call when the
webhook callback and the status poll both complete the same song.

No test reaches the network: requests.post / requests.get are replaced by fakes.
"""

import json
import threading
import time
from unittest.mock import MagicMock

import pytest
import requests

import routes.suno as suno
from services import suno_lyrics

LYRICS_URL_PART = 'get-timestamped-lyrics'
_REAL_START_BACKGROUND = suno_lyrics._start_background

ALIGNED = [
    {'word': '[Verse 1]\nHello ', 'startS': 1.0, 'endS': 1.5, 'success': True},
    {'word': 'world ', 'startS': 1.5, 'endS': 2.0, 'success': True},
    {'word': 'again\n', 'startS': 2.0, 'endS': 2.6, 'success': True},
    {'word': '[Chorus]\nSing ', 'startS': 3.0, 'endS': 3.4, 'success': True},
    {'word': 'along', 'startS': 3.4, 'endS': 4.0, 'success': True},
]
LYRICS_OK = {'code': 200, 'msg': 'success', 'data': {'alignedWords': ALIGNED}}


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    r.headers = {}
    return r


def _audio_resp():
    r = MagicMock()
    r.status_code = 200
    r.headers = {'Content-Length': '3'}
    r.iter_content.return_value = [b'mp3']
    return r


class FakeSuno:
    """Stands in for sunoapi.org. Counts every get-timestamped-lyrics POST."""

    def __init__(self, lyrics_payload=None, lyrics_exc=None, delay=0.0):
        self.lyrics_payload = lyrics_payload if lyrics_payload is not None else LYRICS_OK
        self.lyrics_exc = lyrics_exc
        self.delay = delay
        self.lyrics_calls = []
        self.record_info = {}           # task_id -> sunoData list
        self._lock = threading.Lock()

    def post(self, url, headers=None, json=None, timeout=None, **kw):
        if LYRICS_URL_PART in url:
            with self._lock:
                self.lyrics_calls.append(json)
            if self.delay:
                time.sleep(self.delay)
            if self.lyrics_exc:
                raise self.lyrics_exc
            return _resp(self.lyrics_payload)
        if url.endswith('/api/v1/generate'):
            return _resp({'code': 200, 'msg': 'success', 'data': {'taskId': 'task-gen'}})
        raise AssertionError(f'unexpected POST {url}')

    def get(self, url, headers=None, params=None, timeout=None, stream=False, **kw):
        if 'record-info' in url:
            tid = (params or {}).get('taskId')
            return _resp({'code': 200, 'data': {
                'status': 'SUCCESS', 'response': {'sunoData': self.record_info[tid]}}})
        return _audio_resp()


@pytest.fixture(scope='module')
def client():
    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(suno.suno_bp)
    return app.test_client()


@pytest.fixture
def env(tmp_path, monkeypatch):
    music = tmp_path / 'generated_music'
    music.mkdir()
    cache = tmp_path / 'canvas-pages' / '_data' / 'singalong'
    monkeypatch.setattr(suno, 'SUNO_API_KEY', 'test-key')
    monkeypatch.setattr(suno, 'SUNO_CALLBACK_URL', '')
    monkeypatch.setattr(suno, 'SUNO_WEBHOOK_SECRET', '')
    monkeypatch.setattr(suno, 'GENERATED_MUSIC_DIR', music)
    monkeypatch.setattr(suno, 'GENERATED_METADATA_FILE', music / 'generated_metadata.json')
    monkeypatch.setattr(suno, 'suno_jobs', {})
    monkeypatch.setattr(suno, 'completed_songs_queue', [])
    monkeypatch.setattr(suno, 'failed_songs_queue', [])
    monkeypatch.setattr(suno, '_is_safe_download_url', lambda url: True)
    receipts = []
    monkeypatch.setattr(suno, '_provider_receipt', lambda op, units='1': receipts.append(op))
    monkeypatch.setattr(suno_lyrics, 'CACHE_DIR', cache)
    monkeypatch.setattr(suno_lyrics, 'CALL_LOG', music / 'suno-lyrics-calls.jsonl')
    # Run the background fetch inline so the test can assert on its result. One test
    # below exercises the real thread.
    monkeypatch.setattr(suno_lyrics, '_start_background', lambda fn, name: fn())

    fake = FakeSuno()
    monkeypatch.setattr(requests, 'post', fake.post)
    monkeypatch.setattr(requests, 'get', fake.get)

    outcomes = []
    real_fetch = suno_lyrics.fetch_once

    def _recording_fetch(**kw):
        out = real_fetch(**kw)
        outcomes.append(out)
        return out
    monkeypatch.setattr(suno_lyrics, 'fetch_once', _recording_fetch)

    class E:
        pass
    e = E()
    e.music, e.cache, e.fake, e.outcomes, e.receipts = music, cache, fake, outcomes, receipts
    e.call_log = music / 'suno-lyrics-calls.jsonl'
    return e


def _job(task_id, title='Test Song', instrumental=False, **extra):
    job_id = f'job-{task_id}'
    suno.suno_jobs[job_id] = {'status': 'generating', 'prompt': 'a song', 'title': title,
                              'style': 'rock', 'task_id': task_id,
                              'created_at': time.time() - 60, 'instrumental': instrumental,
                              **extra}
    return job_id


def _clip(audio_id='audio-1', title='Test Song', lyrics='[Verse]\nHello world'):
    c = {'audioUrl': 'https://cdn.example/a.mp3', 'title': title, 'duration': 120,
         'prompt': lyrics}
    if audio_id is not None:
        c['id'] = audio_id
    return c


def _log_rows(e):
    if not e.call_log.exists():
        return []
    return [json.loads(l) for l in e.call_log.read_text().splitlines()]


def _marker(e, audio_id):
    p = e.cache / '.suno-lyrics-markers' / f'{audio_id}.json'
    return json.loads(p.read_text()) if p.exists() else None


def _poll(client, job_id):
    return client.get(f'/api/suno?action=status&job_id={job_id}').get_json()


# ── (a) a completed vocal song → exactly 1 call, cache in the host-sync format ──────────

def test_completed_vocal_song_makes_one_call_and_writes_the_cache(client, env):
    env.fake.record_info['task-1'] = [_clip('audio-1'), _clip('audio-1b')]
    job_id = _job('task-1')

    r = _poll(client, job_id)

    assert r['action'] == 'complete'
    assert env.fake.lyrics_calls == [{'taskId': 'task-1', 'audioId': 'audio-1'}]
    cache = json.loads((env.cache / 'lyrics-test-song.json').read_text())
    # The exact shape the host sync's write_cache() produces and sing-along.html reads.
    assert set(cache) == {'source', 'offset', 'lines', 'filename', 'saved', 'sync'}
    assert cache['source'] == 'suno' and cache['offset'] == 0
    assert cache['filename'] == 'test-song.mp3'
    assert cache['sync'] == {'via': 'openvoiceui-generation', 'aligned_words': len(ALIGNED),
                             'task_id': 'task-1', 'audio_id': 'audio-1'}
    assert [[w['w'] for w in l['words']] for l in cache['lines']] == [
        ['Hello', 'world', 'again'], ['Sing', 'along']]
    # The [Section] tag lives INSIDE the timed token, so the first word is interpolated
    # into it (as sing-along.html does): "Hello" starts 10/16 of the way through
    # "[Verse 1]\nHello " (1.0s..1.5s), "Sing" 9/14 through "[Chorus]\nSing " (3.0..3.4s).
    assert cache['lines'][0]['t0'] == pytest.approx(1.0 + 0.5 * 10 / 16)
    assert cache['lines'][1]['t0'] == pytest.approx(3.0 + 0.4 * 9 / 14)
    assert not list(env.cache.glob('*.tmp-*'))           # atomic: no temp left behind

    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['via'] == 'generation' and rows[0]['result'] == 'ok'
    assert rows[0]['task_id'] == 'task-1' and rows[0]['audio_id'] == 'audio-1'
    assert rows[0]['aligned_words'] == len(ALIGNED) and 'ts' in rows[0]
    assert _marker(env, 'audio-1')['state'] == 'ok'
    assert env.receipts.count('timestamped_lyrics') == 1


def test_credits_are_logged_when_suno_returns_them(client, env):
    env.fake.lyrics_payload = {'code': 200, 'msg': 'success',
                               'data': {'alignedWords': ALIGNED, 'creditsUsed': 2}}
    env.fake.record_info['task-c'] = [_clip('audio-c')]
    _poll(client, _job('task-c'))
    assert _log_rows(env)[0]['credits'] == {'creditsUsed': 2}


# ── (b) the callback and the poll both fire → still 1 call ──────────────────────────────

def _callback(client, task_id, clip):
    body = {'code': 200, 'msg': 'All generated successfully.',
            'data': {'callbackType': 'complete', 'task_id': task_id, 'data': [clip]}}
    return client.post('/api/suno/callback', json=body)


def test_callback_then_poll_for_the_same_song_makes_one_call(client, env):
    clip = _clip('audio-2', title='Both Legs')
    env.fake.record_info['task-2'] = [clip]
    job_id = _job('task-2', title='Both Legs')

    assert _callback(client, 'task-2', clip).get_json() == {'status': 'ok'}
    # The poll is answered by a worker whose copy of the job never saw the callback.
    suno.suno_jobs[job_id]['status'] = 'generating'
    assert _poll(client, job_id)['action'] == 'complete'

    assert env.outcomes == ['ok', 'skipped-duplicate']   # both legs reached the fetch
    assert len(env.fake.lyrics_calls) == 1
    assert len(_log_rows(env)) == 1
    assert (env.cache / 'lyrics-both-legs.json').exists()


def test_concurrent_completions_race_to_one_call(env):
    env.fake.delay = 0.2                                 # hold the first call open
    kw = dict(task_id='task-3', audio_id='audio-3', filename='race.mp3',
              lyrics='[Verse]\nHello', api_key='test-key')
    results = []
    threads = [threading.Thread(target=lambda: results.append(suno_lyrics.fetch_once(**kw)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ['ok', 'skipped-duplicate']
    assert len(env.fake.lyrics_calls) == 1


# ── (c) an instrumental → 0 calls ────────────────────────────────────────────────────────

def test_instrumental_request_makes_no_call(client, env):
    # A JSON POST carries a real bool — this used to raise AttributeError (True.lower()).
    r = client.post('/api/suno', json={'action': 'generate', 'prompt': 'chill lofi beat',
                                       'instrumental': True}).get_json()
    assert r['action'] == 'generating'
    job_id = r['job_id']
    assert suno.suno_jobs[job_id]['instrumental'] is True
    suno.suno_jobs[job_id]['created_at'] -= 60
    env.fake.record_info['task-gen'] = [_clip('audio-4', title='Chill', lyrics='')]

    assert _poll(client, job_id)['action'] == 'complete'

    assert env.fake.lyrics_calls == []
    assert _log_rows(env) == []                           # the log holds paid calls only
    mk = _marker(env, 'audio-4')
    assert mk['state'] == 'instrumental' and mk['called'] is False


def test_instrumental_lyrics_on_a_callback_from_another_worker_makes_no_call(client, env):
    # No job in this worker's memory: the "[Instrumental]" lyrics are the only signal.
    _callback(client, 'task-5', _clip('audio-5', title='Beat', lyrics='[Instrumental]'))
    assert env.fake.lyrics_calls == []
    assert _marker(env, 'audio-5')['state'] == 'instrumental'


def test_explicit_action_does_not_rebuy_a_known_instrumental(client, env):
    env.fake.record_info['task-6'] = [_clip('audio-6', title='Ambient', lyrics='[Instrumental]')]
    _poll(client, _job('task-6', title='Ambient'))
    r = client.post('/api/suno', json={'action': 'timestamped_lyrics',
                                       'filename': 'ambient.mp3'}).get_json()
    assert r['action'] == 'error' and r.get('instrumental') is True
    assert env.fake.lyrics_calls == []


def test_explicit_action_calls_are_logged_too(client, env):
    meta = {'song.mp3': {'title': 'Song', 'suno_id': 'audio-7', 'task_id': 'task-7'}}
    (env.music / 'generated_metadata.json').write_text(json.dumps(meta))
    r = client.post('/api/suno', json={'action': 'timestamped_lyrics',
                                       'filename': 'song.mp3'}).get_json()
    assert r['action'] == 'timestamped_lyrics'
    rows = _log_rows(env)
    assert len(env.fake.lyrics_calls) == 1 and len(rows) == 1
    assert rows[0]['via'] == 'action' and rows[0]['result'] == 'ok'


# ── (d) a failure → 1 call, no retry, failure recorded ──────────────────────────────────

def test_failure_is_recorded_and_not_retried(client, env):
    env.fake.lyrics_payload = {'code': 500, 'msg': 'internal error', 'data': None}
    env.fake.record_info['task-8'] = [_clip('audio-8', title='Unlucky')]
    job_id = _job('task-8', title='Unlucky')

    assert _poll(client, job_id)['action'] == 'complete'   # delivery is unaffected
    suno.suno_jobs[job_id]['status'] = 'generating'        # completes again
    _poll(client, job_id)

    assert len(env.fake.lyrics_calls) == 1
    mk = _marker(env, 'audio-8')
    assert mk['state'] == 'failed' and 'code=500' in mk['error']
    rows = _log_rows(env)
    assert len(rows) == 1 and rows[0]['result'] == 'failed' and rows[0]['suno_code'] == 500
    assert not (env.cache / 'lyrics-unlucky.json').exists()


def test_network_error_is_a_recorded_failure(env):
    env.fake.lyrics_exc = requests.ConnectionError('boom')
    out = suno_lyrics.fetch_once(task_id='task-9', audio_id='audio-9', filename='x.mp3',
                                 lyrics='la la', api_key='test-key')
    assert out == 'failed' and len(env.fake.lyrics_calls) == 1
    assert 'ConnectionError' in _marker(env, 'audio-9')['error']
    assert suno_lyrics.fetch_once(task_id='task-9', audio_id='audio-9', filename='x.mp3',
                                  lyrics='la la', api_key='test-key') == 'skipped-duplicate'
    assert len(env.fake.lyrics_calls) == 1


# ── (e) a missing audioId → 0 calls ──────────────────────────────────────────────────────

def test_missing_audio_id_makes_no_call(client, env):
    env.fake.record_info['task-10'] = [_clip(audio_id=None, title='No Id')]
    assert _poll(client, _job('task-10', title='No Id'))['action'] == 'complete'
    assert env.outcomes == ['skipped-no-ids']
    assert env.fake.lyrics_calls == []
    assert _log_rows(env) == []
    assert not (env.cache / '.suno-lyrics-markers').exists()


def test_background_thread_really_runs(env, monkeypatch):
    monkeypatch.setattr(suno_lyrics, '_start_background', _REAL_START_BACKGROUND)
    env.fake.delay = 0.3
    t = suno_lyrics.schedule(task_id='task-11', audio_id='audio-11', filename='bg.mp3',
                             lyrics='[Verse]\nHi', api_key='test-key')
    assert t.is_alive()                  # the caller (song delivery) did not wait
    t.join(5)
    assert len(env.fake.lyrics_calls) == 1
    assert (env.cache / 'lyrics-bg.json').exists()
