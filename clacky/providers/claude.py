"""Anthropic Claude provider (direct Messages API — no Claude Code)."""

from __future__ import annotations

import os

from .base import LLMProvider

DEFAULT_MODEL = "claude-sonnet-5"


class ClaudeProvider(LLMProvider):
    name = "claude"

    def __init__(self, model: str | None = None):
        from anthropic import Anthropic   # lazy import
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self._client = Anthropic(api_key=key)
        self._model = model or DEFAULT_MODEL

    def complete(self, system: str, user: str) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(
            block.text for block in msg.content
            if getattr(block, "type", "") == "text"
        )
