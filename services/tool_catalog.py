"""
Voice Agent Tool Catalog — loads config/tools.yaml and generates every
projection of the tool surface.

Spec: docs/jambot/voice-agent-tool-bus.md

    from services.tool_catalog import catalog

    catalog.realtime_tools(caps={'canvas', 'music'})   # -> tools[] for session.update
    catalog.marker_specs()                             # -> [MARKER] regex specs
    catalog.prompt_docs(caps={'canvas'})               # -> system-prompt block
    catalog.get('open_canvas_page')                    # -> ToolDef

Why one catalog: before this, the tool surface was defined implicitly by regexes
duplicated across src/core/VoiceSession.js and src/app.js. There was no schema, no
return value, and no way to hand a realtime model (Grok Voice, OpenAI Realtime) a
tool list. See future-dev-plans/17-MULTI-AGENT-FRAMEWORK.md §5.

Capability gating: realtime_tools() and prompt_docs() FILTER by capability. A tool
the profile is not allowed to use is never described to the model at all — it does
not learn the tool exists. That is deliberate; refusal-by-omission beats
refusal-by-rejection. `dangerous: true` tools need an explicit extra opt-in on top.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover - yaml is a hard dep in prod
    _YAML_AVAILABLE = False

_CATALOG_YAML = Path(__file__).parent.parent / "config" / "tools.yaml"

VALID_EXECUTION = {"client", "server_sync", "server_async"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarkerSpec:
    """The legacy [MARKER] form of a tool, for text-streaming agents."""
    tool_name: str
    form: str                      # human-readable, e.g. "[CANVAS:<page>]"
    pattern: str                   # regex with one capture group per entry in `groups`
    groups: List[str]              # capture-group index -> param name
    dedupe_key: str                # matches the `seen` keys in VoiceSession.js
    event: Optional[str]           # eventBus event emitted client-side

    def compiled(self) -> re.Pattern:
        return re.compile(self.pattern, re.IGNORECASE)


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    execution: str                 # client | server_sync | server_async
    capability: str
    params: Dict[str, Any]         # JSON Schema object
    dangerous: bool = False
    handler: Optional[str] = None  # server_* only: handler id in routes/tools.py
    event: Optional[str] = None    # client only: EventBridge event
    marker: Optional[MarkerSpec] = field(default=None)

    @property
    def is_async(self) -> bool:
        return self.execution == "server_async"

    @property
    def is_client(self) -> bool:
        return self.execution == "client"

    def to_realtime(self) -> Dict[str, Any]:
        """OpenAI-Realtime-shaped function definition (Grok Voice uses this too)."""
        return {
            "type": "function",
            "name": self.name,
            "description": " ".join(self.description.split()),
            "parameters": self.params or {"type": "object", "properties": {}},
        }


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class ToolCatalog:
    """Singleton view over config/tools.yaml."""

    _instance: Optional["ToolCatalog"] = None
    _lock = threading.Lock()

    def __new__(cls, *a, **kw):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, path: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self._path = path or _CATALOG_YAML
        self._tools: Dict[str, ToolDef] = {}
        self._capabilities: Dict[str, str] = {}
        self._version: int = 0
        self._initialized = True
        self.reload()

    # -- loading ------------------------------------------------------------

    def reload(self) -> None:
        """(Re)read the YAML. Safe to call at runtime; never raises on bad input."""
        if not _YAML_AVAILABLE:
            logger.error("tool_catalog: PyYAML unavailable — catalog is EMPTY")
            return
        if not self._path.exists():
            logger.error("tool_catalog: %s not found — catalog is EMPTY", self._path)
            return

        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh) or {}
        except Exception as exc:
            logger.error("tool_catalog: failed to parse %s: %s", self._path, exc)
            return

        self._version = int(raw.get("version", 0))
        self._capabilities = dict(raw.get("capabilities") or {})

        tools: Dict[str, ToolDef] = {}
        for entry in raw.get("tools") or []:
            tool = self._parse_tool(entry)
            if tool is None:
                continue
            if tool.name in tools:
                logger.error("tool_catalog: duplicate tool name %r — keeping first", tool.name)
                continue
            tools[tool.name] = tool

        self._tools = tools
        logger.info(
            "tool_catalog: loaded %d tools (%d with markers) from %s",
            len(tools), sum(1 for t in tools.values() if t.marker), self._path.name,
        )

    def _parse_tool(self, entry: Dict[str, Any]) -> Optional[ToolDef]:
        """Validate one catalog entry. Returns None (and logs) on anything malformed.

        A malformed entry must never take the whole catalog down — a single bad
        tool should not silently disarm canvas + music for every tenant.
        """
        name = (entry or {}).get("name")
        if not name:
            logger.error("tool_catalog: entry with no name — skipped: %r", entry)
            return None

        execution = entry.get("execution")
        if execution not in VALID_EXECUTION:
            logger.error(
                "tool_catalog: %s has invalid execution %r (expected one of %s) — skipped",
                name, execution, sorted(VALID_EXECUTION),
            )
            return None

        capability = entry.get("capability")
        if not capability:
            logger.error("tool_catalog: %s has no capability — skipped", name)
            return None
        if capability not in self._capabilities:
            logger.warning(
                "tool_catalog: %s uses capability %r not declared in capabilities: — "
                "it will be gated out of every profile", name, capability,
            )

        if execution == "client" and not entry.get("event"):
            logger.error("tool_catalog: client tool %s has no event — skipped", name)
            return None
        if execution.startswith("server") and not entry.get("handler"):
            logger.error("tool_catalog: server tool %s has no handler — skipped", name)
            return None

        marker = self._parse_marker(name, entry.get("marker"), entry.get("event"))

        return ToolDef(
            name=name,
            description=entry.get("description", ""),
            execution=execution,
            capability=capability,
            params=entry.get("params") or {"type": "object", "properties": {}},
            dangerous=bool(entry.get("dangerous", False)),
            handler=entry.get("handler"),
            event=entry.get("event"),
            marker=marker,
        )

    def _parse_marker(
        self, tool_name: str, raw: Any, event: Optional[str]
    ) -> Optional[MarkerSpec]:
        if not raw:            # `marker: null` or absent = tool has no legacy form
            return None
        pattern = raw.get("pattern")
        if not pattern:
            logger.error("tool_catalog: %s marker has no pattern — marker dropped", tool_name)
            return None
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            logger.error("tool_catalog: %s marker regex invalid (%s) — marker dropped",
                         tool_name, exc)
            return None

        groups = list(raw.get("groups") or [])
        if compiled.groups != len(groups):
            # This is the parity guard: a mismatch means the generated parser would
            # bind params to the wrong capture group — worse than no marker at all.
            logger.error(
                "tool_catalog: %s marker declares %d groups but regex has %d — marker dropped",
                tool_name, len(groups), compiled.groups,
            )
            return None

        return MarkerSpec(
            tool_name=tool_name,
            form=raw.get("form", ""),
            pattern=pattern,
            groups=groups,
            dedupe_key=raw.get("dedupe_key") or tool_name.upper(),
            event=event,
        )

    # -- accessors ----------------------------------------------------------

    @property
    def version(self) -> int:
        return self._version

    def get(self, name: str) -> Optional[ToolDef]:
        return self._tools.get(name)

    def all_tools(self) -> List[ToolDef]:
        return list(self._tools.values())

    def capabilities(self) -> Dict[str, str]:
        return dict(self._capabilities)

    def allowed(
        self,
        caps: Optional[Iterable[str]] = None,
        allow_dangerous: Optional[Iterable[str]] = None,
    ) -> List[ToolDef]:
        """Tools this profile may use.

        caps            — capability allowlist. None means NONE (fail closed).
        allow_dangerous — explicit per-tool opt-in for `dangerous: true` tools.
                          Holding the capability is not sufficient.
        """
        cap_set: Set[str] = set(caps or ())
        danger_set: Set[str] = set(allow_dangerous or ())
        out = []
        for tool in self._tools.values():
            if tool.capability not in cap_set:
                continue
            if tool.dangerous and tool.name not in danger_set:
                continue
            out.append(tool)
        return out

    # -- projection 1: realtime function calling ----------------------------

    def realtime_tools(
        self,
        caps: Optional[Iterable[str]] = None,
        allow_dangerous: Optional[Iterable[str]] = None,
    ) -> List[Dict[str, Any]]:
        """tools[] array for a realtime session.update (Grok / OpenAI Realtime)."""
        return [t.to_realtime() for t in self.allowed(caps, allow_dangerous)]

    # -- projection 2: legacy marker protocol -------------------------------

    def marker_specs(self) -> List[MarkerSpec]:
        """Every tool's [MARKER] form.

        NOT capability-filtered: text-streaming agents are gated by what their
        system prompt tells them exists, and the parser must still recognise (and
        strip) a marker it wasn't expecting rather than speak it aloud.
        """
        return [t.marker for t in self._tools.values() if t.marker]

    def strip_patterns(self) -> List[str]:
        """Regexes to remove marker text before display/TTS."""
        return [m.pattern for m in self.marker_specs()]

    # -- projection 3: prompt documentation ---------------------------------

    def prompt_docs(
        self,
        caps: Optional[Iterable[str]] = None,
        allow_dangerous: Optional[Iterable[str]] = None,
        style: str = "marker",
    ) -> str:
        """The 'here are your tools' block for brains without native function calling.

        style='marker'   → documents the [MARKER] form (text-streaming agents)
        style='function' → documents tools by name (realtime agents that still
                           benefit from prose guidance alongside tools[])
        """
        tools = self.allowed(caps, allow_dangerous)
        if not tools:
            return ""

        lines = ["## Available tools", ""]
        for tool in sorted(tools, key=lambda t: (t.capability, t.name)):
            desc = " ".join(tool.description.split())
            if style == "marker":
                if not tool.marker:
                    continue
                lines.append(f"- `{tool.marker.form}` — {desc}")
            else:
                required = tool.params.get("required") or []
                props = ", ".join(tool.params.get("properties", {}).keys()) or "no arguments"
                req = f" (required: {', '.join(required)})" if required else ""
                lines.append(f"- `{tool.name}({props})`{req} — {desc}")
        if len(lines) == 2:
            return ""
        return "\n".join(lines)

    # -- serialisation for the browser --------------------------------------

    def to_client_json(
        self,
        caps: Optional[Iterable[str]] = None,
        allow_dangerous: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """What GET /api/tools/catalog returns.

        The browser needs the routing table (which tool is client-side and which
        EventBridge event it maps to) plus the realtime tools[] to hand the model.
        """
        tools = self.allowed(caps, allow_dangerous)
        return {
            "version": self._version,
            "capabilities": self._capabilities,
            "realtime_tools": [t.to_realtime() for t in tools],
            "routing": {
                t.name: {
                    "execution": t.execution,
                    "event": t.event,
                    "capability": t.capability,
                    "dangerous": t.dangerous,
                }
                for t in tools
            },
            "markers": [
                {
                    "tool": m.tool_name,
                    "pattern": m.pattern,
                    "groups": m.groups,
                    "dedupe_key": m.dedupe_key,
                    "event": m.event,
                }
                for m in self.marker_specs()
            ],
        }


# Singleton — import this
catalog = ToolCatalog()
