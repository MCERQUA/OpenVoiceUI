"""
tests/test_profile_manager.py — Unit tests for ProfileManager (P5-T4)

Tests:
- ProfileManager loads profiles from the profiles/ directory
- Profile dataclasses (from_dict / to_dict round-trip)
- Validation logic
- CRUD operations (save, delete, partial update)
- API endpoints (GET list, GET single, POST create, PUT update, DELETE, activate)

ADR-002: Profile storage as JSON files.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from profiles.manager import (
    ProfileManager,
    Profile,
    LLMConfig,
    VoiceConfig,
    STTConfig,
    ContextConfig,
    FeatureConfig,
    UIConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_PROFILE_DATA = {
    "id": "test-agent",
    "name": "Test Agent",
    "system_prompt": "You are a test agent for unit testing.",
    "llm": {"provider": "zai", "model": "glm-5-turbo"},
    "voice": {"tts_provider": "supertonic", "voice_id": "M1"},
    "stt": {"provider": "webspeech"},
    "features": {"canvas": True, "vision": False, "music": False, "tools": False},
    "ui": {"theme": "dark", "face_enabled": True, "face_mood": "neutral"},
}


@pytest.fixture()
def tmp_manager(tmp_path):
    """ProfileManager backed by a fresh temp directory."""
    ProfileManager.reset_instance()
    mgr = ProfileManager(str(tmp_path))
    yield mgr
    ProfileManager.reset_instance()


@pytest.fixture()
def tmp_manager_with_profiles(tmp_path):
    """ProfileManager with one pre-loaded profile."""
    profile_file = tmp_path / "test-agent.json"
    profile_file.write_text(json.dumps(MINIMAL_PROFILE_DATA, indent=2))
    ProfileManager.reset_instance()
    mgr = ProfileManager(str(tmp_path))
    yield mgr
    ProfileManager.reset_instance()


@pytest.fixture()
def real_manager():
    """ProfileManager pointed at the real profiles/ directory (read-only tests)."""
    ProfileManager.reset_instance()
    mgr = ProfileManager(str(PROJECT_ROOT / "profiles"))
    yield mgr
    ProfileManager.reset_instance()


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestProfileDataclasses:

    def test_profile_from_dict_minimal(self):
        profile = Profile.from_dict(MINIMAL_PROFILE_DATA)
        assert profile.id == "test-agent"
        assert profile.name == "Test Agent"
        assert profile.llm.provider == "zai"
        assert profile.voice.tts_provider == "supertonic"

    def test_profile_from_dict_ignores_unknown_llm_fields(self):
        """hume provider has config_id in llm — must not raise."""
        data = dict(MINIMAL_PROFILE_DATA)
        data["llm"] = {"provider": "hume", "config_id": "abc123"}
        profile = Profile.from_dict(data)
        assert profile.llm.provider == "hume"

    def test_profile_to_dict_round_trip(self):
        profile = Profile.from_dict(MINIMAL_PROFILE_DATA)
        d = profile.to_dict()
        assert d["id"] == "test-agent"
        assert d["llm"]["provider"] == "zai"
        assert d["voice"]["tts_provider"] == "supertonic"

    def test_profile_defaults_applied(self):
        data = {
            "id": "bare",
            "name": "Bare",
            "system_prompt": "Bare agent.",
            "llm": {"provider": "zai"},
            "voice": {"tts_provider": "supertonic", "voice_id": "M1"},
        }
        profile = Profile.from_dict(data)
        assert profile.stt.provider == "webspeech"
        assert profile.context.enable_history is True
        assert profile.ui.theme == "dark"


# ---------------------------------------------------------------------------
# ProfileManager loading tests
# ---------------------------------------------------------------------------

class TestProfileManagerLoading:

    def test_empty_dir_creates_no_profiles(self, tmp_manager):
        assert tmp_manager.list_profiles() == []

    def test_loads_profile_from_file(self, tmp_manager_with_profiles):
        assert tmp_manager_with_profiles.profile_exists("test-agent")

    def test_list_profiles_returns_summary(self, tmp_manager_with_profiles):
        profiles = tmp_manager_with_profiles.list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["id"] == "test-agent"
        assert profiles[0]["name"] == "Test Agent"
        assert "system_prompt" not in profiles[0]  # summary only

    def test_get_profile_returns_profile(self, tmp_manager_with_profiles):
        profile = tmp_manager_with_profiles.get_profile("test-agent")
        assert profile is not None
        assert profile.id == "test-agent"

    def test_get_nonexistent_profile_returns_none_or_default(self, tmp_manager):
        profile = tmp_manager.get_profile("does-not-exist")
        assert profile is None

    def test_skips_schema_json(self, tmp_path):
        (tmp_path / "schema.json").write_text(json.dumps({"type": "object"}))
        ProfileManager.reset_instance()
        mgr = ProfileManager(str(tmp_path))
        assert not mgr.profile_exists("schema")
        ProfileManager.reset_instance()

    def test_loads_real_profiles(self, real_manager):
        profiles = real_manager.list_profiles()
        ids = [p["id"] for p in profiles]
        assert "default" in ids


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestProfileValidation:

    def test_valid_profile_no_errors(self, tmp_manager):
        errors = tmp_manager.validate_profile(MINIMAL_PROFILE_DATA)
        assert errors == []

    def test_missing_id(self, tmp_manager):
        data = dict(MINIMAL_PROFILE_DATA)
        data.pop("id")
        errors = tmp_manager.validate_profile(data)
        assert any("id" in e for e in errors)

    def test_missing_name(self, tmp_manager):
        data = {**MINIMAL_PROFILE_DATA, "name": ""}
        errors = tmp_manager.validate_profile(data)
        assert any("name" in e for e in errors)

    def test_missing_system_prompt(self, tmp_manager):
        data = {**MINIMAL_PROFILE_DATA, "system_prompt": ""}
        errors = tmp_manager.validate_profile(data)
        assert any("system_prompt" in e for e in errors)

    def test_invalid_id_characters(self, tmp_manager):
        data = {**MINIMAL_PROFILE_DATA, "id": "Bad Agent!"}
        errors = tmp_manager.validate_profile(data)
        assert any("id" in e for e in errors)

    def test_missing_llm_provider(self, tmp_manager):
        data = dict(MINIMAL_PROFILE_DATA)
        data["llm"] = {}
        errors = tmp_manager.validate_profile(data)
        assert any("llm.provider" in e for e in errors)


# ---------------------------------------------------------------------------
# CRUD tests
# ---------------------------------------------------------------------------

class TestProfileManagerCRUD:

    def test_save_profile(self, tmp_manager, tmp_path):
        profile = Profile.from_dict(MINIMAL_PROFILE_DATA)
        result = tmp_manager.save_profile(profile)
        assert result is True
        assert tmp_manager.profile_exists("test-agent")
        assert (tmp_path / "test-agent.json").exists()

    def test_delete_profile(self, tmp_manager_with_profiles, tmp_path):
        tmp_manager_with_profiles._default_id = "other"  # so test-agent is not default
        deleted = tmp_manager_with_profiles.delete_profile("test-agent")
        assert deleted is True
        assert not tmp_manager_with_profiles.profile_exists("test-agent")
        assert not (tmp_path / "test-agent.json").exists()

    def test_cannot_delete_default_profile(self, tmp_manager_with_profiles):
        tmp_manager_with_profiles._default_id = "test-agent"
        result = tmp_manager_with_profiles.delete_profile("test-agent")
        assert result is False
        assert tmp_manager_with_profiles.profile_exists("test-agent")

    def test_delete_nonexistent_returns_false(self, tmp_manager):
        assert tmp_manager.delete_profile("ghost") is False

    def test_partial_update_name(self, tmp_manager_with_profiles):
        updated = tmp_manager_with_profiles.apply_partial_update(
            "test-agent", {"name": "Updated Name"}
        )
        assert updated is not None
        assert updated.name == "Updated Name"
        # Re-load to confirm persistence
        loaded = tmp_manager_with_profiles.get_profile("test-agent")
        assert loaded.name == "Updated Name"

    def test_partial_update_merges_sub_dict(self, tmp_manager_with_profiles):
        updated = tmp_manager_with_profiles.apply_partial_update(
            "test-agent", {"llm": {"model": "glm-4"}}
        )
        assert updated.llm.model == "glm-4"
        assert updated.llm.provider == "zai"  # original value preserved

    def test_partial_update_nonexistent_returns_none(self, tmp_manager):
        result = tmp_manager.apply_partial_update("ghost", {"name": "X"})
        assert result is None


# ---------------------------------------------------------------------------
# API endpoint tests (dedicated Flask app with profiles blueprint)
# ---------------------------------------------------------------------------

@pytest.fixture()
def profiles_app(tmp_path, monkeypatch):
    """Minimal Flask app with profiles blueprint registered, backed by tmp_path."""
    from flask import Flask
    from flask_cors import CORS

    # Seed the temp profiles dir
    profile_file = tmp_path / "test-agent.json"
    profile_file.write_text(json.dumps(MINIMAL_PROFILE_DATA, indent=2))

    ProfileManager.reset_instance()
    mgr = ProfileManager(str(tmp_path))
    monkeypatch.setattr("routes.profiles.get_profile_manager", lambda: mgr)

    app = Flask(__name__)
    app.config["TESTING"] = True

    from routes.profiles import profiles_bp
    # Blueprints can only be registered once per app instance
    app.register_blueprint(profiles_bp)

    yield app, mgr
    ProfileManager.reset_instance()


@pytest.fixture()
def profiles_client(profiles_app):
    app, mgr = profiles_app
    return app.test_client(), mgr


class TestProfilesAPI:

    @pytest.fixture(autouse=True)
    def setup(self, profiles_client):
        """Wire the dedicated profiles test client."""
        self.client, self.manager = profiles_client

    def test_get_profiles_list(self):
        resp = self.client.get("/api/profiles")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "profiles" in data
        assert "active" in data
        assert any(p["id"] == "test-agent" for p in data["profiles"])

    def test_get_profile_by_id(self):
        resp = self.client.get("/api/profiles/test-agent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == "test-agent"
        assert "system_prompt" in data

    def test_get_nonexistent_profile(self):
        resp = self.client.get("/api/profiles/nope")
        assert resp.status_code == 404

    def test_create_profile(self):
        new_profile = {
            "id": "new-agent",
            "name": "New Agent",
            "system_prompt": "A brand new agent for testing.",
            "llm": {"provider": "zai"},
            "voice": {"tts_provider": "supertonic", "voice_id": "M2"},
        }
        resp = self.client.post(
            "/api/profiles",
            json=new_profile,
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["id"] == "new-agent"

    def test_create_profile_duplicate_returns_409(self):
        resp = self.client.post(
            "/api/profiles",
            json=MINIMAL_PROFILE_DATA,
            content_type="application/json",
        )
        assert resp.status_code == 409

    def test_create_profile_validation_error(self):
        resp = self.client.post(
            "/api/profiles",
            json={"id": "bad", "name": "Bad"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "errors" in resp.get_json()

    def test_update_profile(self):
        resp = self.client.put(
            "/api/profiles/test-agent",
            json={"name": "Renamed Agent"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Renamed Agent"

    def test_update_nonexistent_profile(self):
        resp = self.client.put(
            "/api/profiles/ghost",
            json={"name": "X"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_delete_profile(self):
        self.manager._default_id = "other"
        resp = self.client.delete("/api/profiles/test-agent")
        assert resp.status_code == 204

    def test_delete_nonexistent_profile(self):
        resp = self.client.delete("/api/profiles/ghost")
        assert resp.status_code == 404

    def test_activate_profile(self):
        resp = self.client.post(
            "/api/profiles/activate",
            json={"profile_id": "test-agent"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["active"] == "test-agent"

    def test_activate_nonexistent_profile(self):
        resp = self.client.post(
            "/api/profiles/activate",
            json={"profile_id": "nope"},
            content_type="application/json",
        )
        assert resp.status_code == 404
        assert resp.get_json()["ok"] is False

    def test_get_active_profile(self):
        # First activate test-agent
        self.client.post(
            "/api/profiles/activate",
            json={"profile_id": "test-agent"},
            content_type="application/json",
        )
        resp = self.client.get("/api/profiles/active")
        assert resp.status_code == 200
        assert resp.get_json()["id"] == "test-agent"
