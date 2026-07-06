"""Google Gemini provider (direct API)."""

from __future__ import annotations

import os

from .base import LLMProvider

DEFAULT_MODEL = "gemini-1.5-flash"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None):
        import google.generativeai as genai   # lazy import
        key = os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GOOGLE_API_KEY is not set.")
        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(model or DEFAULT_MODEL)

    def complete(self, system: str, user: str) -> str:
        resp = self._model.generate_content(
            [system, user],
            generation_config={"response_mime_type": "application/json"},
        )
        return resp.text or ""
