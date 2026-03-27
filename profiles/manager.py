"""
profiles/manager.py — ProfileManager class (P5-T4)

Loads, validates, and manages agent profiles stored as JSON files.
ADR-002: Profile storage as JSON files.
ADR-003: Abstract base class + registry pattern.
"""

import copy
import dataclasses
import json
import logging
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _dc_from_dict(cls, data: dict):
    """Construct a dataclass from a dict, silently ignoring unknown keys."""
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class LLMQueueConfig:
    """OpenClaw Gateway queue + streaming behaviour. null = use gateway default."""
    mode: Optional[str] = None                   # collect | steer | steer-backlog | None
    block_streaming_chunk: Optional[int] = None  # min chars before TTS sentence fires
    block_streaming_coalesce: Optional[bool] = None


@dataclass
class LLMConfig:
    provider: str = "zai"
    model: Optional[str] = None
    parameters: Dict = field(default_factory=dict)
    queue: LLMQueueConfig = field(default_factory=LLMQueueConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        queue_data = data.pop("queue", {}) or {}
        obj = _dc_from_dict(cls, data)
        obj.queue = _dc_from_dict(LLMQueueConfig, queue_data)
        return obj

    def to_dict(self) -> dict:
        d = asdict(self)
        d["queue"] = asdict(self.queue)
        return d


@dataclass
class VoiceConfig:
    tts_provider: str = "groq"
    voice_id: str = "autumn"
    speed: float = 1.0
    parameters: Dict = field(default_factory=dict)
    parallel_sentences: Optional[bool] = None
    min_sentence_chars: Optional[int] = None
    inter_sentence_gap_ms: Optional[int] = None


@dataclass
class STTConfig:
    provider: str = "webspeech"
    language: str = "en-US"
    silence_timeout_ms: Optional[int] = None
    continuous: Optional[bool] = None
    wake_words: Optional[list] = None
    wake_word_required: Optional[bool] = None
    ptt_default: Optional[bool] = None


@dataclass
class ContextConfig:
    enable_fts: bool = True
    enable_briefing: bool = True
    enable_history: bool = True
    max_history_messages: int = 12


@dataclass
class FeatureConfig:
    canvas: bool = True
    vision: bool = True
    music: bool = False
    tools: bool = False


@dataclass
class UIConfig:
    theme: str = "dark"
    theme_preset: Optional[str] = None
    face_enabled: bool = True
    face_mode: str = "halo-smoke"
    face_mood: str = "neutral"
    face_config: Dict = field(default_factory=dict)
    transcript_panel: bool = True
    thought_bubbles: bool = True
    show_mode_badge: bool = False
    mode_badge_text: Optional[str] = None
    voice_mode: str = "supertonic"
    visualizer_enabled: bool = True
    music_autoplay: bool = False


@dataclass
class ConversationConfig:
    """Conversation flow: greeting, timeouts, interruption, response cap."""
    greeting: Optional[str] = None
    auto_hangup_silence_ms: Optional[int] = None
    interruption_enabled: Optional[bool] = None
    max_response_chars: Optional[int] = None


@dataclass
class ModesConfig:
    """Which UI input/output modes are available for this agent."""
    normal: bool = True
    listen: bool = False
    ptt: bool = True
    a2a: bool = False


@dataclass
class SessionConfig:
    """Gateway session key strategy."""
    key_strategy: Optional[str] = None   # persistent | per-call | per-message | None
    key_prefix: Optional[str] = None


@dataclass
class AuthConfig:
    """Per-profile auth override."""
    required: Optional[bool] = None
    allowed_roles: Optional[list] = None


@dataclass
class Profile:
    id: str
    name: str
    description: str = ""
    version: str = "1.0"
    system_prompt: str = ""
    adapter: str = "clawdbot"
    llm: LLMConfig = field(default_factory=LLMConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    features: FeatureConfig = field(default_factory=FeatureConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    modes: ModesConfig = field(default_factory=ModesConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    adapter_config: Dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict) -> "Profile":
        # LLM needs special handling for nested queue sub-object
        llm_data = dict(data.get("llm", {}))
        llm_queue = _dc_from_dict(LLMQueueConfig, llm_data.pop("queue", {}) or {})
        llm = _dc_from_dict(LLMConfig, llm_data)
        llm.queue = llm_queue

        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            system_prompt=data.get("system_prompt", ""),
            adapter=data.get("adapter", "clawdbot"),
            llm=llm,
            voice=_dc_from_dict(VoiceConfig, data.get("voice", {})),
            stt=_dc_from_dict(STTConfig, data.get("stt", {})),
            context=_dc_from_dict(ContextConfig, data.get("context", {})),
            features=_dc_from_dict(FeatureConfig, data.get("features", {})),
            ui=_dc_from_dict(UIConfig, data.get("ui", {})),
            conversation=_dc_from_dict(ConversationConfig, data.get("conversation", {})),
            modes=_dc_from_dict(ModesConfig, data.get("modes", {})),
            session=_dc_from_dict(SessionConfig, data.get("session", {})),
            auth=_dc_from_dict(AuthConfig, data.get("auth", {})),
            adapter_config=data.get("adapter_config", {}),
        )

    def to_dict(self) -> Dict:
        llm_dict = asdict(self.llm)
        llm_dict["queue"] = asdict(self.llm.queue)
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "system_prompt": self.system_prompt,
            "adapter": self.adapter,
            "llm": llm_dict,
            "voice": asdict(self.voice),
            "stt": asdict(self.stt),
            "context": asdict(self.context),
            "features": asdict(self.features),
            "ui": asdict(self.ui),
            "conversation": asdict(self.conversation),
            "modes": asdict(self.modes),
            "session": asdict(self.session),
            "auth": asdict(self.auth),
            "adapter_config": self.adapter_config,
        }


class ProfileManager:
    _instance: Optional["ProfileManager"] = None

    def __init__(self, profiles_dir: str = "profiles"):
        self.profiles_dir = Path(profiles_dir)
        self._profiles: Dict[str, Profile] = {}
        self._default_id: str = "default"
        self._load_profiles()

    @classmethod
    def get_instance(cls) -> "ProfileManager":
        if cls._instance is None:
            import os
            # Use runtime profiles dir if mounted (persists across container recreates)
            runtime_profiles = Path("/app/runtime/profiles")
            if runtime_profiles.is_dir():
                profiles_path = str(runtime_profiles)
            else:
                # Fallback to bundled profiles (read-only in image)
                project_root = Path(__file__).parent.parent
                profiles_path = str(project_root / "profiles")
            cls._instance = ProfileManager(profiles_path)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        """For testing: discard the cached singleton."""
        cls._instance = None

    def _load_profiles(self):
        if not self.profiles_dir.exists():
            self.profiles_dir.mkdir(parents=True)
            logger.warning("Created profiles directory: %s", self.profiles_dir)
            return

        for file_path in sorted(self.profiles_dir.glob("*.json")):
            if file_path.name == "schema.json":
                continue
            try:
                with open(file_path) as f:
                    data = json.load(f)
                profile = Profile.from_dict(data)
                self._profiles[profile.id] = profile
                logger.info("Loaded profile: %s", profile.id)
            except Exception as exc:
                logger.error("Failed to load profile %s: %s", file_path, exc)

    def get_profile(self, profile_id: str = None) -> Optional[Profile]:
        if profile_id is None:
            profile_id = self._default_id
        profile = self._profiles.get(profile_id)
        if not profile:
            logger.warning("Profile '%s' not found, falling back to default", profile_id)
            profile = self._profiles.get(self._default_id)
        return profile

    def list_profiles(self) -> List[Dict]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "version": p.version,
                "adapter_config": p.adapter_config,
            }
            for p in self._profiles.values()
        ]

    def profile_exists(self, profile_id: str) -> bool:
        return profile_id in self._profiles

    def save_profile(self, profile: Profile) -> bool:
        try:
            file_path = self.profiles_dir / f"{profile.id}.json"
            with open(file_path, "w") as f:
                json.dump(profile.to_dict(), f, indent=2)
            self._profiles[profile.id] = profile
            logger.info("Saved profile: %s", profile.id)
            return True
        except Exception as exc:
            logger.error("Failed to save profile %s: %s", profile.id, exc)
            return False

    def delete_profile(self, profile_id: str) -> bool:
        if profile_id == self._default_id:
            logger.warning("Cannot delete default profile '%s'", profile_id)
            return False
        if profile_id not in self._profiles:
            return False
        try:
            file_path = self.profiles_dir / f"{profile_id}.json"
            if file_path.exists():
                file_path.unlink()
            del self._profiles[profile_id]
            logger.info("Deleted profile: %s", profile_id)
            return True
        except Exception as exc:
            logger.error("Failed to delete profile %s: %s", profile_id, exc)
            return False

    def validate_profile(self, data: Dict) -> List[str]:
        errors = []

        pid = data.get("id", "")
        if not pid:
            errors.append("id is required")
        elif not all(c.isalnum() or c in "-_" for c in pid):
            errors.append("id must be alphanumeric (hyphens and underscores allowed)")

        if not data.get("name"):
            errors.append("name is required")

        if not data.get("system_prompt"):
            errors.append("system_prompt is required")

        llm = data.get("llm", {})
        if not llm.get("provider"):
            errors.append("llm.provider is required")

        voice = data.get("voice", {})
        if not voice.get("tts_provider"):
            errors.append("voice.tts_provider is required")

        return errors

    def apply_partial_update(self, profile_id: str, updates: Dict) -> Optional[Profile]:
        """
        Apply a partial update (dict) to an existing profile and save it.
        Returns the updated Profile, or None if the profile does not exist.
        """
        profile = self._profiles.get(profile_id)
        if not profile:
            return None

        # Merge at the top-level sub-dict level (one level deep)
        base = profile.to_dict()
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                base[key] = {**base[key], **value}
            else:
                base[key] = value

        updated = Profile.from_dict(base)
        self.save_profile(updated)
        return updated


def get_profile_manager() -> ProfileManager:
    """Convenience accessor for the ProfileManager singleton."""
    return ProfileManager.get_instance()
