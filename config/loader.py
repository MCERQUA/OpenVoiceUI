"""
Config loader: reads config/default.yaml with environment variable overrides.

Usage:
    from config.loader import config

    config.get('server.port')          # -> 5001
    config.get('gateway.url')          # -> 'ws://127.0.0.1:18791'
    config['tts.provider']             # dict-style access also works
    config.flag('use_blueprints')      # -> True/False (from flags.yaml)

Environment variable override rules:
  - Direct named overrides (highest priority):
      PORT                 -> server.port
      CLAWDBOT_GATEWAY_URL -> gateway.url
      CLAWDBOT_AUTH_TOKEN  -> gateway.auth_token
      GATEWAY_SESSION_KEY  -> gateway.session_key
      GEMINI_MODEL         -> models.gemini
      TTS_PROVIDER         -> tts.provider
      USE_GROQ_TTS         -> tts.use_groq  (true/false string)
      MAX_HISTORY_MESSAGES -> conversation.max_history_messages
      LOG_LEVEL            -> logging.level
      ENABLE_FTS           -> features.enable_fts
      ENABLE_BRIEFING      -> features.enable_briefing
      ENABLE_HISTORY_RELOAD-> features.enable_history_reload
  - Generic double-underscore override:
      SERVER__PORT=5002    -> server.port = 5002

Feature flag environment variable override:
  - FEATURE_<FLAG_NAME_UPPER>=true/false
      FEATURE_USE_BLUEPRINTS=true   -> flags.use_blueprints = True
      FEATURE_PERSISTENT_WEBSOCKET=true -> flags.use_persistent_websocket = True
"""

import os
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# Path to the default config file (same directory as this module)
_DEFAULT_YAML = Path(__file__).parent / "default.yaml"

# Path to the feature flags file
_FLAGS_YAML = Path(__file__).parent / "flags.yaml"

# Named env var → dotted config key mappings
_ENV_MAP = {
    "PORT":                   ("server.port",                     int),
    "CLAWDBOT_GATEWAY_URL":   ("gateway.url",                     str),
    "CLAWDBOT_AUTH_TOKEN":    ("gateway.auth_token",              str),
    "GATEWAY_SESSION_KEY":    ("gateway.session_key",             str),
    "GEMINI_MODEL":           ("models.gemini",                   str),
    "TTS_PROVIDER":           ("tts.provider",                    str),
    "USE_GROQ_TTS":           ("tts.use_groq",                    "bool"),
    "MAX_HISTORY_MESSAGES":   ("conversation.max_history_messages", int),
    "LOG_LEVEL":              ("logging.level",                   str),
    "ENABLE_FTS":             ("features.enable_fts",             "bool"),
    "ENABLE_BRIEFING":        ("features.enable_briefing",         "bool"),
    "ENABLE_HISTORY_RELOAD":  ("features.enable_history_reload",  "bool"),
    "OPENCLAW_WORKSPACE":     ("paths.openclaw_workspace",         str),
}


def _cast(value: str, cast_type) -> Any:
    """Cast a string env var value to the target type."""
    if cast_type == "bool":
        return value.lower() in ("true", "1", "yes")
    if cast_type == int:
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"Cannot cast {value!r} to int")
    if cast_type == float:
        return float(value)
    return value  # str passthrough


def _deep_set(data: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path."""
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _deep_get(data: dict, dotted_key: str, default: Any = None) -> Any:
    """Get a value from a nested dict using a dotted key path."""
    parts = dotted_key.split(".")
    node = data
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _load_yaml(path: Path) -> dict:
    """Load a YAML file, returning empty dict if missing or yaml unavailable."""
    if not _YAML_AVAILABLE:
        print("⚠ Warning: PyYAML not installed. Run: pip install pyyaml")
        return {}
    if not path.exists():
        print(f"⚠ Warning: Config file not found: {path}")
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _apply_env_overrides(data: dict) -> None:
    """Apply named env var overrides to the config dict (in-place)."""
    # Named mappings (highest precedence)
    for env_key, (config_key, cast_type) in _ENV_MAP.items():
        value = os.environ.get(env_key)
        if value is not None:
            try:
                _deep_set(data, config_key, _cast(value, cast_type))
            except (ValueError, TypeError) as e:
                import logging
                logging.getLogger(__name__).warning(
                    f"Ignoring env var {env_key}={value!r}: {e}"
                )

    # Generic double-underscore overrides: SERVER__PORT=5002 → server.port
    for env_key, value in os.environ.items():
        if "__" in env_key:
            parts = env_key.lower().split("__", 1)
            if len(parts) == 2:
                dotted = f"{parts[0]}.{parts[1]}"
                # Only override if the key already exists in the loaded config
                if _deep_get(data, dotted) is not None:
                    _deep_set(data, dotted, value)


def _load_flags(path: Path) -> dict:
    """Load feature flags from flags.yaml with env var overrides.

    Each flag can be overridden via FEATURE_<FLAG_NAME_UPPER>=true/false.
    Example: FEATURE_USE_BLUEPRINTS=true overrides flags.use_blueprints.
    """
    raw = _load_yaml(path)
    flags: dict = raw.get("flags", {})

    # Apply env overrides: FEATURE_USE_BLUEPRINTS=true -> use_blueprints=True
    for key in list(flags.keys()):
        env_key = f"FEATURE_{key.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            flags[key] = env_val.lower() in ("true", "1", "yes")

    return flags


class Config:
    """Immutable config accessor loaded from YAML + env overrides."""

    def __init__(self, yaml_path: Path = _DEFAULT_YAML, flags_path: Path = _FLAGS_YAML):
        self._data = _load_yaml(yaml_path)
        _apply_env_overrides(self._data)
        self._flags = _load_flags(flags_path)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a config value by dotted key. Returns default if not found."""
        return _deep_get(self._data, key, default)

    def __getitem__(self, key: str) -> Any:
        value = _deep_get(self._data, key)
        if value is None:
            raise KeyError(f"Config key not found: {key}")
        return value

    def __contains__(self, key: str) -> bool:
        return _deep_get(self._data, key) is not None

    def as_dict(self) -> dict:
        """Return a copy of the full config dict."""
        import copy
        return copy.deepcopy(self._data)

    def flag(self, name: str, default: bool = False) -> bool:
        """Check if a feature flag is enabled.

        Flags are loaded from config/flags.yaml with env var overrides.
        Example: config.flag('use_blueprints') -> True/False
        Env override: FEATURE_USE_BLUEPRINTS=true
        """
        return self._flags.get(name, default)

    def reload(self) -> None:
        """Reload config from YAML file and re-apply env overrides."""
        self._data = _load_yaml(_DEFAULT_YAML)
        _apply_env_overrides(self._data)
        self._flags = _load_flags(_FLAGS_YAML)


# Module-level singleton — import this everywhere:
#   from config.loader import config
config = Config()
