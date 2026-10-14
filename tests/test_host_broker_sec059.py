"""
SEC-059: container restart + vision queue via the host broker, with requests MOCKED.

Nothing here touches docker, the network, or /mnt/clients: the token path is redirected
with SEC059_TOKEN_FILE, the broker host with PROVISION_SERVICE_HOST, and every HTTP call
goes through a FakeHTTP installed over services.host_broker.requests.
"""
import json
import logging
import re
import subprocess
from pathlib import Path

import pytest
import requests as real_requests

from services import host_broker as hb

TENANT = "alpha"
TOKEN = "f" * 64
ROOT = Path(__file__).resolve().parents[1]


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)

    def json(self):
        return self._body


class FakeHTTP:
    """Scripted answers; records every call (url, json, headers, timeout)."""

    def __init__(self, answer=None, raise_exc=None):
        self.calls = []
        self.answer = answer or FakeResp(200, {"ok": True, "created": "2026-10-14T22:00:00Z",
                                               "jambot_shared": "connected", "delivered": "/x.md"})
        self.raise_exc = raise_exc

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        if self.raise_exc:
            raise self.raise_exc
        return self.answer


@pytest.fixture
def broker(monkeypatch, tmp_path):
    tok = tmp_path / ".sec059-broker-token"
    tok.write_text(TOKEN + "\n")
    monkeypatch.setenv("SEC059_TOKEN_FILE", str(tok))
    monkeypatch.setenv("PROVISION_SERVICE_HOST", "10.99.0.1")
    monkeypatch.setenv("RESTART_VIA_BROKER", "1")
    monkeypatch.delenv("VAULT_RESTART_VIA_DOCKER", raising=False)
    fake = FakeHTTP()
    monkeypatch.setattr(hb.requests, "post", fake.post)
    fake.token_path = tok
    return fake


# ── flags + identity ──────────────────────────────────────────────────────

def test_restart_mode_precedence(monkeypatch):
    monkeypatch.delenv("RESTART_VIA_BROKER", raising=False)
    monkeypatch.delenv("VAULT_RESTART_VIA_DOCKER", raising=False)
    assert hb.restart_mode() == "legacy-docker"
    monkeypatch.setenv("RESTART_VIA_BROKER", "1")
    assert hb.restart_mode() == "broker" and hb.via_broker()
    monkeypatch.setenv("VAULT_RESTART_VIA_DOCKER", "1")      # explicit opt-out wins
    assert hb.restart_mode() == "docker" and not hb.via_broker()


def test_token_read_per_call_and_absent_token_sends_no_header(broker):
    assert hb.auth_headers(TENANT) == {"X-Tenant-Token": TOKEN}
    broker.token_path.write_text("e" * 64)                    # host rotated it: no restart needed
    assert hb.auth_headers(TENANT) == {"X-Tenant-Token": "e" * 64}
    broker.token_path.unlink()
    assert hb.auth_headers(TENANT) == {}                      # the broker will answer 503, loudly


def test_broker_url_prefers_env_then_default_route(monkeypatch):
    monkeypatch.setenv("PROVISION_SERVICE_HOST", "10.1.2.3")
    assert hb.broker_url() == "http://10.1.2.3:5200"
    monkeypatch.delenv("PROVISION_SERVICE_HOST")
    monkeypatch.setattr(hb, "default_gateway", lambda: "10.40.176.1")
    assert hb.broker_url() == "http://10.40.176.1:5200"


def test_safe_subject_is_filename_safe():
    assert hb.safe_subject("Screenshot 2026-10-14 at 9.41.07 PM") == "analyze-Screenshot-2026-10-14-at-9.41.07-PM"
    assert hb.safe_subject("x; rm -rf /") == "analyze-x-rm-rf"
    assert hb.safe_subject("") == "analyze-image"
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", hb.safe_subject("é/ü\\name" * 20))


# ── restart via broker ────────────────────────────────────────────────────

def test_restart_success_posts_bound_request(broker):
    res = hb.restart_container("openclaw", TENANT)
    assert res["ok"] is True
    call = broker.calls[0]
    assert call["url"] == f"http://10.99.0.1:5200/restart/openclaw/{TENANT}"
    assert call["json"] == {"dry_run": False}
    assert call["headers"]["X-Tenant-Token"] == TOKEN


def test_restart_403_raises_with_status(broker):
    broker.answer = FakeResp(403, {"error": "X-Tenant-Token mismatch"})
    with pytest.raises(hb.BrokerError) as ei:
        hb.restart_container("openclaw", TENANT)
    assert ei.value.status == 403 and "mismatch" in str(ei.value)


def test_restart_unreachable_raises_status_0(broker):
    broker.raise_exc = real_requests.ConnectionError("refused")
    with pytest.raises(hb.BrokerError) as ei:
        hb.restart_container("openclaw", TENANT)
    assert ei.value.status == 0 and "unreachable" in str(ei.value)


def test_restart_200_without_ok_is_still_a_failure(broker):
    broker.answer = FakeResp(200, {"ok": False, "error": "compose up failed"})
    with pytest.raises(hb.BrokerError):
        hb.restart_container("openclaw", TENANT)


def test_restart_rejects_unknown_service(broker):
    with pytest.raises(hb.BrokerError):
        hb.restart_container("hermes", TENANT)
    assert broker.calls == []


# ── vault wiring ──────────────────────────────────────────────────────────

@pytest.fixture
def vault(monkeypatch, tmp_path, broker):
    from services import vault as v
    clients = tmp_path / "clients"
    (clients / TENANT / "compose").mkdir(parents=True)
    (clients / TENANT / "compose" / "docker-compose.yml").write_text(f"name: jambot-{TENANT}\n")
    monkeypatch.setattr(v, "_CLIENTS_DIR", clients)
    v._restart_outcomes.clear()
    return v


def test_vault_restart_via_broker_success(vault, broker, caplog):
    with caplog.at_level(logging.INFO):
        assert vault._do_restart_container(f"openclaw-{TENANT}") is True
    assert broker.calls[0]["url"].endswith(f"/restart/openclaw/{TENANT}")
    out = vault.last_restart_outcome(f"openclaw-{TENANT}")
    assert out["ok"] is True and out["mode"] == "broker"


def test_vault_restart_via_broker_403_is_loud_and_false(vault, broker, caplog):
    broker.answer = FakeResp(403, {"error": "source address is not inside jambot-alpha"})
    with caplog.at_level(logging.ERROR):
        assert vault._do_restart_container(f"openclaw-{TENANT}") is False
    assert any("RESTART DID NOT HAPPEN" in r.message and "openclaw-alpha" in r.message
               for r in caplog.records if r.levelno >= logging.ERROR)
    assert vault.last_restart_outcome(f"openclaw-{TENANT}")["ok"] is False


def test_vault_restart_via_broker_unreachable_is_loud_and_false(vault, broker, caplog):
    broker.raise_exc = real_requests.ConnectionError("no route")
    with caplog.at_level(logging.ERROR):
        assert vault._do_restart_container(f"openclaw-{TENANT}") is False
    assert any("RESTART DID NOT HAPPEN" in r.message for r in caplog.records if r.levelno >= logging.ERROR)


def test_vault_self_restart_connection_drop_is_unknown_not_success(vault, broker, caplog):
    broker.raise_exc = real_requests.ConnectionError("connection reset")
    with caplog.at_level(logging.WARNING):
        assert vault._do_restart_container(f"openvoiceui-{TENANT}") is False
    out = vault.last_restart_outcome(f"openvoiceui-{TENANT}")
    assert out["ok"] is False and "UNKNOWN" in out["detail"] and "docker inspect" in out["detail"]


def test_vault_restart_via_broker_never_runs_docker(vault, broker, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("subprocess must not run on the broker path")
    monkeypatch.setattr(subprocess, "run", boom)
    assert vault._do_restart_container(f"openclaw-{TENANT}") is True


def test_vault_docker_flag_keeps_subprocess_path(vault, broker, monkeypatch):
    monkeypatch.setenv("VAULT_RESTART_VIA_DOCKER", "1")
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert vault._do_restart_container(f"openclaw-{TENANT}") is True
    assert ran[0][:2] == ["docker", "compose"] and ran[0][-1] == "openclaw"
    assert broker.calls == []
    assert vault.last_restart_outcome(f"openclaw-{TENANT}")["mode"] == "docker"


def test_vault_unflagged_default_is_legacy_with_warning(vault, broker, monkeypatch, caplog):
    monkeypatch.delenv("RESTART_VIA_BROKER")
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    with caplog.at_level(logging.WARNING):
        assert vault._do_restart_container(f"openclaw-{TENANT}") is True
    assert any("SEC-059" in r.message and "RESTART_VIA_BROKER=1" in r.message for r in caplog.records)
    assert broker.calls == []


def test_vault_rejects_foreign_container_names(vault, broker):
    assert vault._do_restart_container(f"hermes-{TENANT}") is False
    assert vault._do_restart_container("nodash") is False
    assert broker.calls == []


def test_vault_plugin_config_sync_sends_tenant_token(vault, broker, monkeypatch):
    import urllib.request
    seen = {}

    class R:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.header_items())
        seen["url"] = req.full_url
        return R()
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert vault._sync_to_plugin_via_provision_service(TENANT, "hermes", {"GLM_API_KEY": "x"}, {}) is True
    assert seen["headers"].get("X-tenant-token") == TOKEN
    assert seen["url"].endswith(f"/config/hermes/{TENANT}")


# ── vision queue ──────────────────────────────────────────────────────────

def test_queue_vision_task_success_and_failure(broker):
    res = hb.queue_vision_task(TENANT, "analyze-IMG_1", "image_path: /mnt/clients/alpha/x.png\n")
    assert res["ok"] is True and res["delivered"] == "/x.md"
    call = broker.calls[0]
    assert call["url"].endswith(f"/mesh-task/{TENANT}")
    assert call["json"] == {"to": "vision@mesh", "subject": "analyze-IMG_1",
                            "body": "image_path: /mnt/clients/alpha/x.png\n"}
    assert call["headers"]["X-Tenant-Token"] == TOKEN

    broker.answer = FakeResp(502, {"ok": False, "error": "mesh-send failed", "rc": 3})
    with pytest.raises(hb.BrokerError) as ei:
        hb.queue_vision_task(TENANT, "analyze-IMG_1", "body")
    assert ei.value.status == 502


def test_queue_vision_task_body_is_capped(broker):
    hb.queue_vision_task(TENANT, "s", "y" * (hb.BODY_MAX + 500))
    assert len(broker.calls[0]["json"]["body"]) == hb.BODY_MAX


def test_conversation_route_carries_no_docker_exec_path():
    """The migration script's P2 gate: `grep -q 'docker.*exec' /app/routes/conversation.py`
    must find NOTHING, or the socket is not removed for that tenant."""
    text = (ROOT / "routes" / "conversation.py").read_text()
    assert re.search(r"docker.*exec", text) is None
    assert "host_broker" in text and "queue_vision_task" in text


def test_plugins_module_sends_tenant_token_on_every_broker_call(monkeypatch, broker):
    from services import plugins as p
    monkeypatch.setattr(p, "_get_username", lambda: TENANT)
    seen = []

    class Resp:
        ok = True
        status_code = 200

        def json(self):
            return {"ok": True, "status": "running"}

    def rec(method):
        def f(url, **kw):
            seen.append((method, url, kw.get("headers") or {}))
            return Resp()
        return f
    monkeypatch.setattr(p.requests, "get", rec("GET"))
    monkeypatch.setattr(p.requests, "put", rec("PUT"))
    monkeypatch.setattr(p.requests, "post", rec("POST"))
    monkeypatch.setattr(p, "_registry", {"hermes-agent": {"lifecycle": {"post_install": {
        "requires_container": True, "provision": "hermes"}}}})
    p.get_container_status("hermes-agent")
    p.get_plugin_config("hermes-agent")
    p.update_plugin_config("hermes-agent", {"glm_api_key": "x"})
    assert len(seen) == 3
    for method, url, headers in seen:
        assert headers.get("X-Tenant-Token") == TOKEN, (method, url, headers)


# ── PR #541 review D1: a delivered request with no answer is UNKNOWN, never "did not happen" ──
def test_read_timeout_on_openclaw_restart_is_unknown_not_failure(monkeypatch, caplog):
    import requests
    from services import host_broker, vault
    monkeypatch.setattr(host_broker, "tenant_token", lambda t: "x" * 64)
    def boom(*a, **k):
        raise requests.ReadTimeout("read timed out")
    monkeypatch.setattr(host_broker.requests, "post", boom)
    with caplog.at_level("WARNING"):
        ok = vault._restart_via_broker("openclaw", "acme", "openclaw-acme")
    assert ok is False
    out = vault.last_restart_outcome("openclaw-acme")
    assert "UNKNOWN" in out["detail"] and "DID NOT HAPPEN" not in out["detail"]
    assert not any("RESTART DID NOT HAPPEN" in r.getMessage() for r in caplog.records)


def test_connect_failure_on_openclaw_restart_still_says_did_not_happen(monkeypatch):
    import requests
    from services import host_broker, vault
    monkeypatch.setattr(host_broker, "tenant_token", lambda t: "x" * 64)
    def boom(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(host_broker.requests, "post", boom)
    assert vault._restart_via_broker("openclaw", "acme", "openclaw-acme") is False
    assert "DID NOT HAPPEN" in vault.last_restart_outcome("openclaw-acme")["detail"]


def test_restart_client_timeout_exceeds_broker_compose_timeout(monkeypatch):
    from services import host_broker
    seen = {}
    monkeypatch.setattr(host_broker, "_post", lambda t, p, b, timeout: seen.setdefault("t", timeout) or {"ok": True})
    host_broker.restart_container("openclaw", "acme", dry_run=True)
    assert seen["t"] > 180


# ── token location: the tenant VAULT dir (visible to 23/24 OVUs, 0 openclaw), not compose/ ──
def test_token_path_prefers_in_container_vault(monkeypatch, tmp_path):
    from services import host_broker
    monkeypatch.delenv("SEC059_TOKEN_FILE", raising=False)
    vault = tmp_path / "runtime-vault"; vault.mkdir()
    monkeypatch.setattr(host_broker, "_RUNTIME_VAULT_DIR", vault)
    assert host_broker.tenant_token_path("acme") == vault / ".sec059-broker-token"


def test_token_path_host_fallback_is_vault_not_compose(monkeypatch, tmp_path):
    from services import host_broker
    monkeypatch.delenv("SEC059_TOKEN_FILE", raising=False)
    monkeypatch.setattr(host_broker, "_RUNTIME_VAULT_DIR", tmp_path / "absent")
    monkeypatch.setattr(host_broker, "_CLIENTS_DIR", tmp_path / "clients")
    p = host_broker.tenant_token_path("acme")
    assert p == tmp_path / "clients" / "acme" / "vault" / ".sec059-broker-token"
    assert "compose" not in p.parts
