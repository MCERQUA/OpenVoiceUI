"""LLM provider package.

Importing this package registers all LLM providers with the registry.
"""

from providers.llm.base import LLMProvider, LLMResponse, LLMError

# Import concrete providers so their registry.register() calls fire
from providers.llm import zai_provider  # noqa: F401
from providers.llm import clawdbot_provider  # noqa: F401
from providers.llm import nearai_provider  # noqa: F401

__all__ = ["LLMProvider", "LLMResponse", "LLMError"]
