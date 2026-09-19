"""
Tests for routes/intake.py — signed one-time upload links (client file intake).

Covers: mint happy path, upload happy path, expired token, exhausted token,
wrong-tenant token, path-traversal-flavoured filename, oversize file,
disallowed content type. Storage paths are monkeypatched to a tmp dir so
these tests never touch the real runtime/ directory.
"""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def intake_client(tmp_path, monkeypatch):
    """Minimal Flask app with the intake blueprint registered, storage
    redirected to a tmp dir, and no AGENT_API_KEY / Clerk gate involved
    (this app registers only the blueprint, not app.py's require_auth)."""
    from app import create_app
    app, _ = create_app(config_override={"TESTING": True})

    import routes.intake as intake

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()

    monkeypatch.setattr(intake, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(intake, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(intake, "TOKENS_PATH", runtime_dir / "intake-tokens.json")
    monkeypatch.setattr(intake, "AUDIT_LOG_PATH", runtime_dir / "intake-audit.jsonl")
    # append_upload_index writes to the real UPLOADS_DIR module constant inside
    # static_files.py — patch it there too so tests never touch real data.
    import routes.static_files as static_files
    monkeypatch.setattr(static_files, "UPLOADS_DIR", uploads_dir)
    # Reset in-process rate-limit state between tests.
    intake._rate_hits.clear()
    monkeypatch.setenv("CLIENT_NAME", "test-tenant")

    app.register_blueprint(intake.intake_bp)
    return app.test_client(), intake, uploads_dir, runtime_dir


def _mint(client, **overrides):
    body = {"expiry_hours": 72, "max_uses": 3}
    body.update(overrides)
    resp = client.post("/api/intake/mint", json=body)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()


def _upload(client, token, filename="plans.pdf", content=b"%PDF-1.4 fake plan set", content_type="application/pdf"):
    data = {"file": (io.BytesIO(content), filename, content_type)}
    return client.post(f"/u/{token}", data=data, content_type="multipart/form-data")


class TestMint:
    def test_mint_returns_token_and_path(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        assert minted["token"]
        assert len(minted["token"]) >= 32
        assert minted["url_path"] == f"/u/{minted['token']}"

    def test_mint_persists_to_tokens_file(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        stored = json.loads((runtime_dir / "intake-tokens.json").read_text())
        assert minted["token"] in stored
        assert stored[minted["token"]]["tenant"] == "test-tenant"

    def test_mint_rejects_bad_max_uses(self, intake_client):
        client, *_ = intake_client
        resp = client.post("/api/intake/mint", json={"max_uses": 0})
        assert resp.status_code == 400


class TestUploadHappyPath:
    def test_get_form_renders_for_valid_token(self, intake_client):
        client, *_ = intake_client
        minted = _mint(client)
        resp = client.get(f"/u/{minted['token']}")
        assert resp.status_code == 200
        assert b"Upload your file" in resp.data

    def test_upload_accepts_pdf(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"])
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["ok"] is True

    def test_upload_writes_file_to_disk(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        _upload(client, minted["token"])
        saved = list(uploads_dir.glob("intake_*.pdf"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"%PDF-1.4 fake plan set"

    def test_upload_stored_filename_is_not_client_controlled(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        _upload(client, minted["token"], filename="../../evil.pdf")
        saved = list(uploads_dir.glob("intake_*.pdf"))
        assert len(saved) == 1
        assert saved[0].name.startswith("intake_")
        assert "evil" not in saved[0].name

    def test_upload_increments_use_count(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, max_uses=3)
        _upload(client, minted["token"])
        stored = json.loads((runtime_dir / "intake-tokens.json").read_text())
        assert stored[minted["token"]]["use_count"] == 1

    def test_upload_writes_audit_log(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        _upload(client, minted["token"])
        lines = (runtime_dir / "intake-audit.jsonl").read_text().strip().splitlines()
        assert len(lines) >= 1
        last = json.loads(lines[-1])
        assert last["action"] == "upload"
        assert last["accepted"] is True


class TestExpiredToken:
    def test_expired_token_upload_returns_410_not_401(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, expiry_hours=0.001)  # ~3.6 seconds... force expiry directly instead
        # Force it into the past directly rather than sleeping in a test.
        tokens = json.loads((runtime_dir / "intake-tokens.json").read_text())
        tokens[minted["token"]]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        (runtime_dir / "intake-tokens.json").write_text(json.dumps(tokens))

        resp = _upload(client, minted["token"])
        assert resp.status_code == 410
        assert resp.status_code != 401

    def test_expired_token_get_returns_friendly_html_not_401(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        tokens = json.loads((runtime_dir / "intake-tokens.json").read_text())
        tokens[minted["token"]]["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        (runtime_dir / "intake-tokens.json").write_text(json.dumps(tokens))

        resp = client.get(f"/u/{minted['token']}")
        assert resp.status_code == 410
        assert resp.status_code != 401
        assert b"Traceback" not in resp.data
        assert b"expired" in resp.data.lower()

    def test_unknown_token_returns_410_not_401(self, intake_client):
        client, *_ = intake_client
        resp = _upload(client, "totally-made-up-token-xyz")
        assert resp.status_code == 410
        assert resp.status_code != 401


class TestExhaustedToken:
    def test_token_exhausted_after_max_uses(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, max_uses=2)
        r1 = _upload(client, minted["token"], filename="a.pdf")
        r2 = _upload(client, minted["token"], filename="b.pdf")
        assert r1.status_code == 200
        assert r2.status_code == 200
        r3 = _upload(client, minted["token"], filename="c.pdf")
        assert r3.status_code == 410

    def test_failed_attempt_does_not_burn_a_use(self, intake_client):
        """A rejected (bad content-type) attempt must not count against max_uses —
        the spec explicitly calls out 'a failed attempt does not burn the link'."""
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, max_uses=1)
        bad = _upload(client, minted["token"], filename="virus.exe",
                       content=b"MZ", content_type="application/octet-stream")
        assert bad.status_code == 415
        good = _upload(client, minted["token"], filename="plans.pdf")
        assert good.status_code == 200


class TestWrongTenant:
    def test_token_minted_for_other_tenant_is_refused(self, intake_client, monkeypatch):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        # Simulate the token store having a record stamped for a different
        # tenant (e.g. copied across containers by mistake).
        tokens = json.loads((runtime_dir / "intake-tokens.json").read_text())
        tokens[minted["token"]]["tenant"] = "some-other-tenant"
        (runtime_dir / "intake-tokens.json").write_text(json.dumps(tokens))

        resp = _upload(client, minted["token"])
        assert resp.status_code == 410
        assert resp.status_code != 401


class TestPathTraversal:
    def test_traversal_filename_never_becomes_a_path(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"], filename="../../../etc/passwd.pdf")
        assert resp.status_code == 200
        # Nothing escaped uploads_dir; only server-generated intake_*.pdf exists.
        all_files = list(uploads_dir.rglob("*"))
        assert all(f.name.startswith("intake_") or f.is_dir() for f in all_files if f.is_file())
        # No file called passwd.pdf anywhere under (or above) uploads_dir.
        assert not (uploads_dir.parent / "etc").exists()

    def test_absolute_path_filename_is_flattened(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"], filename="/etc/passwd.pdf")
        assert resp.status_code == 200
        saved = list(uploads_dir.glob("intake_*.pdf"))
        assert len(saved) == 1


class TestOversizeFile:
    def test_oversize_file_rejected_with_413(self, intake_client, monkeypatch):
        client, intake, uploads_dir, runtime_dir = intake_client
        # Cap this token's own max_bytes small so the test doesn't need to
        # actually allocate 100MB.
        minted = _mint(client)
        tokens = json.loads((runtime_dir / "intake-tokens.json").read_text())
        tokens[minted["token"]]["max_bytes"] = 10
        (runtime_dir / "intake-tokens.json").write_text(json.dumps(tokens))

        resp = _upload(client, minted["token"], content=b"x" * 100)
        assert resp.status_code == 413

    def test_oversize_does_not_burn_a_use(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, max_uses=1)
        tokens = json.loads((runtime_dir / "intake-tokens.json").read_text())
        tokens[minted["token"]]["max_bytes"] = 10
        (runtime_dir / "intake-tokens.json").write_text(json.dumps(tokens))
        _upload(client, minted["token"], content=b"x" * 100)
        stored = json.loads((runtime_dir / "intake-tokens.json").read_text())
        assert stored[minted["token"]]["use_count"] == 0


class TestDisallowedContentType:
    def test_exe_extension_rejected(self, intake_client):
        client, *_ = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"], filename="malware.exe",
                        content=b"MZ", content_type="application/octet-stream")
        assert resp.status_code == 415

    def test_unknown_extension_rejected(self, intake_client):
        client, *_ = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"], filename="script.sh",
                        content=b"#!/bin/sh", content_type="text/plain")
        assert resp.status_code == 415

    def test_zip_is_allowed(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client)
        resp = _upload(client, minted["token"], filename="plans.zip",
                        content=b"PK\x03\x04fakezip", content_type="application/zip")
        assert resp.status_code == 200


class TestRateLimit:
    def test_per_token_rate_limit_returns_429(self, intake_client):
        client, intake, uploads_dir, runtime_dir = intake_client
        minted = _mint(client, max_uses=50)
        monkeypatch_limit = intake._RATE_MAX_PER_TOKEN
        for i in range(monkeypatch_limit):
            _upload(client, minted["token"], filename=f"f{i}.pdf")
        resp = _upload(client, minted["token"], filename="one-too-many.pdf")
        assert resp.status_code == 429
