"""
Rate-limit keys must be the endpoint that actually serves the path (#587).

'upload_file' named server.py's bare /api/upload route, a shadowed duplicate: server.py registers
static_files_bp first, so its rule matches and `static_files.upload_file` serves every request.
Because the bare name still resolved to a view function, apply_endpoint_rate_limits logged no
warning and the anonymous upload path ran at the 200/minute default, 40x the intended cap.
"""
import io
import os
import re

import pytest
from flask import jsonify


@pytest.fixture()
def up(tmp_path, monkeypatch):
    import routes.static_files as sf
    monkeypatch.setattr(sf, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(sf, "UPLOADS_INDEX", tmp_path / ".uploads-index.jsonl")
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", "pk_test_ratelimit")
    monkeypatch.setenv("AGENT_API_KEY", "agent-key-for-tests-0001")
    import services.auth as auth
    monkeypatch.setattr(auth, "verify_clerk_token", lambda t: "user_1" if t == "good-session" else None)
    return tmp_path


def _app(table=None, anonymous_table=None):
    """Built in server.py's order: the blueprint first, then server.py's own duplicate rule."""
    from app import create_app
    from routes.static_files import static_files_bp
    from services.rate_limits import apply_endpoint_rate_limits
    app, _ = create_app(config_override={"TESTING": True})
    if "static_files" not in app.blueprints:
        app.register_blueprint(static_files_bp)

    @app.route("/api/upload", methods=["POST"])
    def upload_file():          # stand-in for server.py's shadowed duplicate
        return jsonify({"served_by": "server.upload_file"})

    apply_endpoint_rate_limits(app, table, anonymous_table)
    return app


def _post(client, headers=None):
    return client.post("/api/upload", data={"file": (io.BytesIO(b"hi"), "a.txt")},
                       content_type="multipart/form-data", headers=headers or {}).status_code


def test_server_registers_the_blueprint_before_its_duplicate_route():
    src = open(os.path.join(os.path.dirname(__file__), "..", "server.py"), encoding="utf-8").read()
    bp_at = src.index("app.register_blueprint(static_files_bp)")
    dup = re.search(r'@app\.route\("/api/upload", methods=\["POST"\]\)', src)
    if dup:                     # the duplicate may be retired later; then there is nothing to shadow
        assert bp_at < dup.start()


def test_every_limited_key_is_the_endpoint_that_serves_its_path(up):
    from services.rate_limits import ANONYMOUS_RATE_LIMITS, ENDPOINT_RATE_LIMITS
    app = _app()
    adapter = app.url_map.bind("localhost")
    checked = 0
    for key in list(ENDPOINT_RATE_LIMITS) + list(ANONYMOUS_RATE_LIMITS):
        for rule in [r for r in app.url_map.iter_rules() if r.endpoint == key]:
            for method in sorted((rule.methods or set()) - {"HEAD", "OPTIONS"}):
                served, _ = adapter.match(rule.rule, method=method)
                assert served == key, f"{key!r} is limited, but {method} {rule.rule} is served by {served!r}"
                checked += 1
    assert checked >= 1
    assert "upload_file" not in ENDPOINT_RATE_LIMITS          # the shadowed duplicate
    served, _ = adapter.match("/api/upload", method="POST")
    assert served == "static_files.upload_file"
    assert served in ANONYMOUS_RATE_LIMITS


def test_sixth_quick_anonymous_post_is_429(up):
    client = _app().test_client()
    assert [_post(client) for _ in range(6)] == [200] * 5 + [429]


@pytest.mark.parametrize("how", ["bearer", "cookie", "agent-key"])
def test_signed_in_owner_bulk_batch_is_not_throttled(up, how):
    """bulk-image-uploader.html posts one file per request, three at a time."""
    client = _app().test_client()
    headers = {}
    if how == "bearer":
        headers["Authorization"] = "Bearer good-session"
    elif how == "cookie":
        client.set_cookie("__session", "good-session")
    else:
        headers["X-Agent-Key"] = "agent-key-for-tests-0001"
    assert [_post(client, headers) for _ in range(12)] == [200] * 12


def test_a_bad_token_is_still_anonymous(up):
    client = _app().test_client()
    codes = [_post(client, {"Authorization": "Bearer forged"}) for _ in range(6)]
    assert codes == [200] * 5 + [429]


def test_old_key_left_the_real_route_unlimited(up):
    """The bug as shipped: the bare key wraps the dead duplicate, the real route never 429s."""
    client = _app(table={"upload_file": "5/minute"}, anonymous_table={}).test_client()
    assert 429 not in [_post(client) for _ in range(8)]
