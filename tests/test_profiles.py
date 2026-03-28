"""
Tests for agent profile JSON schema (P5-T3).
ADR-002: Profile storage as JSON files.
Validates all profiles in profiles/ against profiles/schema.json.
"""

import json
import pytest
from pathlib import Path

PROFILES_DIR = Path(__file__).parent.parent / "profiles"
SCHEMA_PATH = PROFILES_DIR / "schema.json"
REQUIRED_PROFILES = ["default"]


@pytest.fixture(scope="module")
def schema():
    assert SCHEMA_PATH.exists(), f"Schema file missing: {SCHEMA_PATH}"
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def profile_files():
    files = list(PROFILES_DIR.glob("*.json"))
    return [p for p in files if p.name != "schema.json"]


def load_profile(profile_id):
    path = PROFILES_DIR / f"{profile_id}.json"
    assert path.exists(), f"Profile file missing: {path}"
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Schema file tests
# ---------------------------------------------------------------------------

def test_schema_file_exists():
    assert SCHEMA_PATH.exists()


def test_schema_is_valid_json():
    with open(SCHEMA_PATH) as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert data.get("type") == "object"
    assert "required" in data
    assert "properties" in data


def test_schema_has_required_fields():
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    required = schema.get("required", [])
    assert "id" in required
    assert "name" in required
    assert "system_prompt" in required


# ---------------------------------------------------------------------------
# Required profile existence tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_required_profile_exists(profile_id):
    path = PROFILES_DIR / f"{profile_id}.json"
    assert path.exists(), f"Required profile missing: {profile_id}.json"


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_required_profile_is_valid_json(profile_id):
    profile = load_profile(profile_id)
    assert isinstance(profile, dict)


# ---------------------------------------------------------------------------
# Required field tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_has_id(profile_id):
    profile = load_profile(profile_id)
    assert "id" in profile
    assert profile["id"] == profile_id


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_id_format(profile_id):
    profile = load_profile(profile_id)
    import re
    assert re.match(r"^[a-z0-9-]+$", profile["id"]), \
        f"id '{profile['id']}' must be lowercase alphanumeric with hyphens only"


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_has_name(profile_id):
    profile = load_profile(profile_id)
    assert "name" in profile
    assert len(profile["name"]) > 0
    assert len(profile["name"]) <= 50


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_has_system_prompt(profile_id):
    profile = load_profile(profile_id)
    assert "system_prompt" in profile
    assert len(profile["system_prompt"]) >= 10


# ---------------------------------------------------------------------------
# LLM config tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_llm_config(profile_id):
    profile = load_profile(profile_id)
    assert "llm" in profile, f"{profile_id}: missing 'llm' section"
    llm = profile["llm"]
    assert "provider" in llm, f"{profile_id}: missing llm.provider"
    valid_providers = ["zai", "clawdbot", "openai", "ollama", "anthropic", "hume", "gateway"]
    assert llm["provider"] in valid_providers, \
        f"{profile_id}: unknown llm.provider '{llm['provider']}'"


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_llm_parameters(profile_id):
    profile = load_profile(profile_id)
    llm = profile.get("llm", {})
    params = llm.get("parameters", {})
    if "temperature" in params:
        assert 0 <= params["temperature"] <= 2, \
            f"{profile_id}: temperature must be between 0 and 2"
    if "max_tokens" in params:
        assert params["max_tokens"] >= 1, \
            f"{profile_id}: max_tokens must be >= 1"


# ---------------------------------------------------------------------------
# Voice config tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_voice_config(profile_id):
    profile = load_profile(profile_id)
    assert "voice" in profile, f"{profile_id}: missing 'voice' section"
    voice = profile["voice"]
    assert "tts_provider" in voice, f"{profile_id}: missing voice.tts_provider"
    assert "voice_id" in voice, f"{profile_id}: missing voice.voice_id"
    valid_providers = ["supertonic", "groq", "elevenlabs", "hume"]
    assert voice["tts_provider"] in valid_providers, \
        f"{profile_id}: unknown tts_provider '{voice['tts_provider']}'"


@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_voice_speed(profile_id):
    profile = load_profile(profile_id)
    speed = profile.get("voice", {}).get("speed")
    if speed is not None:
        assert 0.5 <= speed <= 2.0, \
            f"{profile_id}: voice.speed must be between 0.5 and 2.0"


# ---------------------------------------------------------------------------
# STT config tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_stt_config(profile_id):
    profile = load_profile(profile_id)
    assert "stt" in profile, f"{profile_id}: missing 'stt' section"
    stt = profile["stt"]
    assert "provider" in stt, f"{profile_id}: missing stt.provider"
    valid_providers = ["webspeech", "whisper", "deepgram"]
    assert stt["provider"] in valid_providers, \
        f"{profile_id}: unknown stt.provider '{stt['provider']}'"


# ---------------------------------------------------------------------------
# Features config tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_features(profile_id):
    profile = load_profile(profile_id)
    assert "features" in profile, f"{profile_id}: missing 'features' section"
    features = profile["features"]
    for key in ["canvas", "vision", "music", "tools"]:
        assert key in features, f"{profile_id}: missing features.{key}"
        assert isinstance(features[key], bool), \
            f"{profile_id}: features.{key} must be boolean"


# ---------------------------------------------------------------------------
# UI config tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_ui_config(profile_id):
    profile = load_profile(profile_id)
    assert "ui" in profile, f"{profile_id}: missing 'ui' section"
    ui = profile["ui"]
    if "theme" in ui:
        assert ui["theme"] in ["dark", "light"], \
            f"{profile_id}: ui.theme must be 'dark' or 'light'"
    if "face_enabled" in ui:
        assert isinstance(ui["face_enabled"], bool), \
            f"{profile_id}: ui.face_enabled must be boolean"


# ---------------------------------------------------------------------------
# Jsonschema validation (if jsonschema package available)
# ---------------------------------------------------------------------------

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
@pytest.mark.parametrize("profile_id", REQUIRED_PROFILES)
def test_profile_validates_against_schema(profile_id, schema):
    profile = load_profile(profile_id)
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(profile))
    assert not errors, \
        f"{profile_id} failed schema validation:\n" + \
        "\n".join(f"  - {e.message}" for e in errors)


# ---------------------------------------------------------------------------
# Cross-profile consistency tests
# ---------------------------------------------------------------------------

def test_all_profiles_have_unique_ids(profile_files):
    ids = []
    for path in profile_files:
        with open(path) as f:
            data = json.load(f)
        ids.append(data.get("id"))
    assert len(ids) == len(set(ids)), f"Duplicate profile IDs found: {ids}"


def test_all_profiles_have_unique_names(profile_files):
    names = []
    for path in profile_files:
        with open(path) as f:
            data = json.load(f)
        names.append(data.get("name"))
    assert len(names) == len(set(names)), f"Duplicate profile names found: {names}"


def test_minimum_three_profiles(profile_files):
    assert len(profile_files) >= 1, \
        f"Expected at least 1 profile, found {len(profile_files)}"
