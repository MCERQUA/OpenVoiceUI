"""
Provenance sidecars are never served from /uploads/ (2026-11-27).

Measured on the live fleet the same day, signed out: a known <media>.meta.json (the Mac lane's generation record:
tool, prompt, source paths) answered 200 with its JSON body on all 10 live tenants that have one. The social-post
gate writes a second record kind, <media>.social.json. Both are internal: /uploads/<x>.meta.json and
/uploads/<x>.social.json now answer 404 exactly like a dotfile, in every UPLOADS_AUTH_MODE, for every caller
(anonymous, signed link, agent key), before any existence check. The media file next to it is unchanged.
"""
import pytest

AGENT = "agent-key-sidecars"
SIGN_KEY = "s" * 40


@pytest.fixture(params=["off", "shadow", "enforce"])
def client(tmp_path, monkeypatch, request):
    import routes.static_files as sf
    up = tmp_path / "uploads"
    (up / "sub").mkdir(parents=True)
    (up / "card.png").write_bytes(b"\x89PNG\r\n\x1a\ncard")
    (up / "card.png.meta.json").write_text('{"tool": {"engine": "grok", "prompt": "secret prompt"}}')
    (up / "card.png.social.json").write_text('{"made": {"generator": "chatgpt"}}')
    (up / "sub" / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (up / "sub" / "clip.mp4.META.JSON").write_text('{"x": 1}')
    (up / "report.json").write_text('{"ok": true}')          # an ordinary json upload: NOT a sidecar
    monkeypatch.setattr(sf, "UPLOADS_DIR", up)
    monkeypatch.setattr(sf, "UPLOADS_SHADOW_LOG", up / ".uploads-auth-shadow.jsonl")
    monkeypatch.setattr(sf, "UPLOADS_REQUIRE_AUTH", True)
    monkeypatch.setattr(sf, "UPLOADS_AUTH_MODE", request.param)
    monkeypatch.setenv("UPLOADS_SIGNING_KEY", SIGN_KEY)
    monkeypatch.setenv("AGENT_API_KEY", AGENT)
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sf.static_files_bp)
    c = app.test_client()
    c.mode = request.param
    return c


def _signed(path, ttl=600):
    from services import upload_links
    return upload_links.signed_url(path, ttl, key=SIGN_KEY)


@pytest.mark.parametrize("path", [
    "/uploads/card.png.meta.json", "/uploads/card.png.social.json", "/uploads/sub/clip.mp4.META.JSON",
    "/uploads/missing.png.meta.json",
])
def test_sidecar_is_404_for_anonymous_signed_and_agent(client, path):
    r = client.get(path)
    assert r.status_code == 404, (client.mode, path, r.status_code)
    assert b"secret prompt" not in r.data and b"generator" not in r.data
    assert client.get(_signed(path)).status_code == 404
    assert client.get(path, headers={"X-Agent-Key": AGENT}).status_code == 404


def test_media_next_to_the_sidecar_is_unchanged(client):
    # the image itself still follows the normal mode: served in off/shadow, needs a grant in enforce
    r = client.get("/uploads/card.png")
    assert r.status_code == (401 if client.mode == "enforce" else 200)
    assert client.get(_signed("/uploads/card.png")).status_code == 200


def test_an_ordinary_json_upload_is_not_a_sidecar(client):
    r = client.get(_signed("/uploads/report.json"))
    assert r.status_code == 200 and b'"ok"' in r.data


def test_helper_cases():
    from routes.static_files import _is_provenance_sidecar as f
    assert f("a.png.meta.json") and f("x/y/a.mp4.social.json") and f("A.PNG.Meta.Json")
    assert not f("meta.json.png") and not f("report.json") and not f("a.metajson")
