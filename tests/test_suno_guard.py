"""Suno spend guards (2026-11-13): section-label normaliser, fingerprint, daily cap, 3-retry rule,
and the route wiring (shadow logs only; SUNO_GUARD_MODE=enforce refuses). No network: every Suno
POST is a mock, so nothing here can spend a credit."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

import routes.suno as suno
from services import suno_guard as g

SPANISH = (
    "[Intro hablado]\nEy, que se escuche la banda!\n\n"
    "[Verso 1]\nSubele al bajo, que retumbe la cabina,\nel coro de la obra canta en la esquina\n\n"
    "[Coro]\nAqui jala la banda, aqui jala la raza\n\n"
    "[Puente]\nDe norte a sur\n\n[Pre-Coro]\nya viene\n\n[Estribillo final]\notra vez\n"
)


# ---------------------------------------------------------------- normaliser (pure)
def test_spanish_labels_become_english():
    out, changes = g.normalize_section_labels(SPANISH)
    assert "[Verse 1]" in out and "[Chorus]" in out and "[Bridge]" in out
    assert "[Pre-Chorus]" in out and "[Chorus final]" in out
    assert "[Verso" not in out and "[Coro]" not in out and "[Puente]" not in out
    assert ("[Verso 1]", "[Verse 1]") in changes and len(changes) == 5


def test_lyric_words_and_unknown_tags_untouched():
    out, _ = g.normalize_section_labels(SPANISH)
    # "coro" inside a lyric line, the spoken-intro tag and every lyric line survive byte-for-byte
    assert "el coro de la obra canta en la esquina" in out
    assert "[Intro hablado]" in out
    assert "Aqui jala la banda, aqui jala la raza" in out
    lyric_lines = [l for l in SPANISH.splitlines() if not l.startswith("[")]
    assert lyric_lines == [l for l in out.splitlines() if not l.startswith("[")]


def test_english_and_inline_brackets_untouched():
    txt = "[Verse 1]\nhello [coro] inline\n[Chorus]\n[Intro — instrumental, corto]\n"
    out, changes = g.normalize_section_labels(txt)
    assert out == txt and changes == []


def test_accents_and_case():
    out, _ = g.normalize_section_labels("[INTRODUCCIÓN]\nx\n[coro 2]\ny")
    assert out == "[Intro]\nx\n[Chorus 2]\ny"


def test_normaliser_never_raises_on_junk():
    for junk in ("", None, "[", "]]][[[", "[" + "a" * 500 + "]"):
        out, ch = g.normalize_section_labels(junk)
        assert out == junk and ch == []


def test_spanish_lyrics_now_go_custom_mode():
    """The real bug: Spanish labels failed routes/suno.py's English-only lyrics check."""
    out, _ = g.normalize_section_labels(SPANISH)
    assert "[Verse" in out          # the route's custom-mode marker now matches
    assert "[Verse" not in SPANISH  # ...and did not before


# ---------------------------------------------------------------- fingerprint + evaluate
def test_fingerprint_ignores_tags_case_accents():
    eng, _ = g.normalize_section_labels(SPANISH)
    assert g.fingerprint(SPANISH) == g.fingerprint(eng) == g.fingerprint(SPANISH.upper())
    assert g.fingerprint(SPANISH) != g.fingerprint("[Verse]\ncompletely different words here")
    assert g.fingerprint("", "Same Title") == g.fingerprint("", "same  title!")


def _rows(n, fp, start, step=120):
    return [{"ts": start + i * step, "op": "song", "task_id": f"t{i}", "fp": fp} for i in range(n)]


T0 = datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc).timestamp()


def test_retry_rule_flags_the_fourth_take_only():
    fp = "abc"
    for n, expect in ((0, False), (1, False), (2, False), (3, True)):
        v = g.evaluate(_rows(n, fp, T0), op="song", fp=fp, now=T0 + 3600)
        assert v["would_block"] is expect, (n, v)
    assert "retry-rule" in g.evaluate(_rows(3, fp, T0), op="song", fp=fp, now=T0 + 3600)["reasons"][0]


def test_retry_window_expires():
    v = g.evaluate(_rows(3, "abc", T0), op="song", fp="abc", now=T0 + 7 * 3600)
    assert v["would_block"] is False


def test_daily_cap_flags_the_seventh():
    prior = [{"ts": T0 + i * 60, "op": "song", "task_id": f"t{i}", "fp": f"f{i}"} for i in range(6)]
    assert g.evaluate(prior[:5], op="song", fp="new", now=T0 + 3600)["would_block"] is False
    v = g.evaluate(prior, op="song", fp="new", now=T0 + 3600)
    assert v["would_block"] is True and v["day_count"] == 6 and "daily-cap" in v["reasons"][0]


def test_daily_cap_resets_at_utc_midnight():
    prior = [{"ts": T0 + i * 60, "op": "song", "task_id": f"t{i}", "fp": f"f{i}"} for i in range(8)]
    tomorrow = datetime(2026, 11, 12, 0, 5, tzinfo=timezone.utc).timestamp()
    assert g.evaluate(prior, op="song", fp="new", now=tomorrow)["would_block"] is False


def test_owner_confirmed_clears_but_is_recorded():
    v = g.evaluate(_rows(5, "abc", T0), op="song", fp="abc", now=T0 + 3600, owner_confirmed=True)
    assert v["would_block"] is False and v["reasons"] and "[owner_confirmed]" in v["reasons"][0]


def test_sfx_never_counts_or_blocks():
    v = g.evaluate(_rows(20, "abc", T0), op="sfx", fp="abc", now=T0 + 3600)
    assert v["would_block"] is False


def test_mode_flag(monkeypatch):
    monkeypatch.delenv("SUNO_GUARD_MODE", raising=False)
    assert g.mode() == "shadow"
    monkeypatch.setenv("SUNO_GUARD_MODE", "ENFORCE")
    assert g.mode() == "enforce"
    monkeypatch.setenv("SUNO_GUARD_MODE", "yes please")
    assert g.mode() == "shadow"


def test_load_submissions_dedupes_and_filters(tmp_path):
    p = tmp_path / "suno-task-log.jsonl"
    rows = [
        {"ts": "2026-11-11T13:00:00+00:00", "event": "submitted", "op": "song", "task_id": "a", "prompt": "x"},
        {"ts": "2026-11-11T13:00:00+00:00", "event": "submitted", "op": "song", "task_id": "a", "prompt": "x"},
        {"ts": "2026-11-11T13:01:00+00:00", "event": "submitted", "op": "sfx", "task_id": "b"},
        {"ts": "2026-11-11T13:02:00+00:00", "event": "clip", "task_id": "a"},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    subs = g.load_submissions(p)
    assert [s["task_id"] for s in subs] == ["a"]
    assert g.load_submissions(tmp_path / "missing.jsonl") == []


# ---------------------------------------------------------------- route wiring (mocked Suno)
@pytest.fixture(scope="module")
def client():
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(suno.suno_bp)
    return app.test_client()


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(suno, "SUNO_API_KEY", "test-key")
    monkeypatch.setattr(suno, "GENERATED_MUSIC_DIR", tmp_path)
    monkeypatch.delenv("SUNO_GUARD_MODE", raising=False)
    del suno.failed_songs_queue[:]
    yield tmp_path
    del suno.failed_songs_queue[:]


def _ok(task):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"code": 200, "msg": "success", "data": {"taskId": task}}
    r.text = "{}"
    return r


def _post(client, n, **extra):
    calls = []
    def fake(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _ok(f"task-{len(calls)}-{n}")
    with patch.object(suno.http_requests, "post", side_effect=fake):
        r = client.post("/api/suno", json={"action": "generate", "prompt": SPANISH,
                                           "title": "Mi Cancion", "style": "cumbia", **extra})
    return r, calls


def test_spanish_lyrics_submit_in_custom_mode_with_title(client, clean):
    r, calls = _post(client, 0)
    assert r.get_json()["action"] == "generating"
    sent = calls[0]
    assert sent["customMode"] is True and sent["title"] == "Mi Cancion"
    assert "[Verse 1]" in sent["prompt"] and "[Verso 1]" not in sent["prompt"]
    assert "el coro de la obra canta en la esquina" in sent["prompt"]
    row = json.loads((clean / "suno-task-log.jsonl").read_text().splitlines()[0])
    assert row["fp"] == g.fingerprint(SPANISH)


def test_shadow_mode_logs_but_never_refuses(client, clean):
    statuses = [_post(client, i)[0].status_code for i in range(5)]
    assert statuses == [200] * 5                      # 5 takes all went through
    log = [json.loads(l) for l in (clean / "suno-guard-log.jsonl").read_text().splitlines()]
    assert [r["retry_count"] for r in log] == [3, 4]   # takes 4 and 5 flagged
    assert all(r["mode"] == "shadow" and r["would_block"] for r in log)


def test_enforce_mode_refuses_the_fourth_take(client, clean, monkeypatch):
    monkeypatch.setenv("SUNO_GUARD_MODE", "enforce")
    for i in range(3):
        assert _post(client, i)[0].status_code == 200
    r, calls = _post(client, 3)
    assert r.status_code == 429 and calls == []       # Suno was never called
    body = r.get_json()
    assert body["action"] == "error" and body["guard"] == "suno-spend"
    assert "owner" in body["response"].lower()
    r2, calls2 = _post(client, 4, owner_confirmed=True)
    assert r2.status_code == 200 and len(calls2) == 1  # the owner said yes


def test_guard_fault_allows_the_request(client, clean, monkeypatch):
    monkeypatch.setenv("SUNO_GUARD_MODE", "enforce")
    monkeypatch.setattr(g, "evaluate", MagicMock(side_effect=RuntimeError("boom")))
    assert _post(client, 0)[0].status_code == 200
