"""SUNO-REJECT-SURFACED (2026-09-29): a Suno generation refused AT SUBMIT must reach the agent.

Measured 2026-09-28 03:50Z: sunoapi.org answered HTTP 200 with
{"code":429,"msg":"The current credits are insufficient. Please top up.","data":null}.
The route returned {'action':'error'} to the browser, nothing reached the agent's next turn,
and no task-log row was written — the client had been told "45 seconds".
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import routes.suno as suno
from routes.conversation import _suno_failure_prefix

CREDITS_BODY = {"code": 429, "msg": "The current credits are insufficient. Please top up.", "data": None}


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = json.dumps(payload)
    return r


@pytest.fixture(scope="module")
def client():
    # The Suno blueprint is registered in server.py, not by create_app(), so build a
    # minimal app around it (same pattern as test_airadio_bridge.py).
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(suno.suno_bp)
    return app.test_client()


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(suno, "SUNO_API_KEY", "test-key")
    monkeypatch.setattr(suno, "GENERATED_MUSIC_DIR", tmp_path)
    del suno.failed_songs_queue[:]
    yield tmp_path
    del suno.failed_songs_queue[:]


def _task_rows(tmp_path):
    f = tmp_path / "suno-task-log.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_credit_refusal_is_queued_for_the_agent_and_logged(client, clean):
    with patch.object(suno.http_requests, "post", return_value=_resp(CREDITS_BODY)):
        r = client.get("/api/suno?action=generate&prompt=a+song+for+gio&title=Gio%27s+Song")
    assert r.get_json()["action"] == "error"
    assert len(suno.failed_songs_queue) == 1
    q = suno.failed_songs_queue[0]
    assert q["stage"] == "submit" and q["kind"] == "generate" and q["title"] == "Gio's Song"
    assert "credits are insufficient" in q["reason"]
    rows = _task_rows(clean)
    assert [r["event"] for r in rows] == ["rejected"]
    assert rows[0]["op"] == "generate" and rows[0]["title"] == "Gio's Song"


def test_jingle_refusal_via_post_is_queued(client, clean):
    with patch.object(suno.http_requests, "post", return_value=_resp(CREDITS_BODY)):
        client.post("/api/suno", json={"action": "jingle", "brand": "Acme Foam"})
    assert [q["kind"] for q in suno.failed_songs_queue] == ["jingle"]
    assert suno.failed_songs_queue[0]["brand"] == "Acme Foam"


def test_accepted_generation_is_not_queued(client, clean):
    ok = {"code": 200, "msg": "success", "data": {"taskId": "t-123"}}
    with patch.object(suno.http_requests, "post", return_value=_resp(ok)):
        r = client.get("/api/suno?action=generate&prompt=x&title=Fine")
    assert r.get_json()["action"] == "generating"
    assert suno.failed_songs_queue == []
    assert [r["event"] for r in _task_rows(clean)] == ["submitted"]


def test_non_generation_error_is_not_queued(client, clean):
    client.get("/api/suno?action=no_such_action")
    assert suno.failed_songs_queue == []


def test_queue_is_capped(client, clean):
    with patch.object(suno.http_requests, "post", return_value=_resp(CREDITS_BODY)):
        for i in range(suno._FAILED_QUEUE_MAX + 5):
            client.get(f"/api/suno?action=generate&prompt=p{i}&title=T{i}")
    assert len(suno.failed_songs_queue) == suno._FAILED_QUEUE_MAX


def test_agent_prefix_is_delivered_once_then_cleared(client, clean):
    with patch.object(suno.http_requests, "post", return_value=_resp(CREDITS_BODY)):
        client.get("/api/suno?action=generate&prompt=a+song&title=Gio%27s+Song")
    first = _suno_failure_prefix()
    assert first.startswith("[SYSTEM: Music generation you started did NOT go through")
    assert "Gio's Song" in first
    assert "credits are used up" in first          # out-of-credits gets the don't-retry hint
    assert _suno_failure_prefix() == ""             # consumed: never repeated next turn


def test_prefix_dedupes_a_job_queued_twice(clean):
    dup = {"job_id": "j1", "kind": "song", "title": "Twice", "reason": "GENERATE_AUDIO_FAILED"}
    suno.failed_songs_queue.extend([dict(dup), dict(dup)])
    p = _suno_failure_prefix()
    assert p.count("Twice") == 1
    assert "Offer to try again." in p


def test_empty_queue_gives_no_prefix(clean):
    assert _suno_failure_prefix() == ""
