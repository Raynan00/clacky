"""Pick a provider by name (or auto-detect from config)."""

from __future__ import annotations

from .base import LLMProvider
from .. import config


def get_provider(name: str | None = None, model: str | None = None) -> LLMProvider:
    name = (name or config.active_provider()).lower()
    if name == "claude":
        from .claude import ClaudeProvider
        return ClaudeProvider(model=model)
    if name == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(model=model)
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(model=model)
    if name == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(model=model)
    if name == "heuristic":
        from .heuristic import HeuristicProvider
        return HeuristicProvider()
    raise ValueError(f"Unknown provider '{name}'. "
                     f"Choose: claude, openai, gemini, ollama, heuristic.")
