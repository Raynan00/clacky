"""Direct LLM provider clients (Bitshank-style). No agent SDK, no Claude Code.

Each provider implements `LLMProvider.complete(system, user) -> str`. The
planner injects one of these, so the rest of Clacky is provider-agnostic and
testable with a fake.
"""

from .base import LLMProvider
from .factory import get_provider

__all__ = ["LLMProvider", "get_provider"]
