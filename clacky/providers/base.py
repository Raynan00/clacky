"""The single interface every provider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """One non-streaming completion. Returns the model's text (expected to
        be JSON, since the planner asks for JSON). Implementations should keep
        this synchronous and dependency-lazy (import the vendor SDK inside
        __init__, not at module top), so missing libs never break `import
        clacky`."""
        raise NotImplementedError
