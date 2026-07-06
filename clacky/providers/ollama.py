"""Local Ollama provider — free, offline, no API key. Stdlib only."""

from __future__ import annotations

import json
import os
import urllib.request

from .base import LLMProvider

DEFAULT_MODEL = "llama3.1"


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str | None = None, host: str | None = None):
        self._model = model or os.environ.get("CLACKY_OLLAMA_MODEL", DEFAULT_MODEL)
        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    def complete(self, system: str, user: str) -> str:
        payload = {
            "model": self._model,
            "system": system,
            "prompt": user,
            "stream": False,
            "format": "json",          # ask Ollama to constrain to JSON
        }
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())["response"]
