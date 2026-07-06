"""
config.py — local configuration. Reads keys/settings from the environment and
an optional .env (no hard dependency on python-dotenv). No hosted login, no
Claude Code — just like Bitshank's base.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env_files() -> None:
    for env_path in (Path.cwd() / ".env", Path.home() / ".clacky" / ".env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env_files()


def get(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def active_provider() -> str:
    """Pick the provider: explicit CLACKY_PROVIDER, else first key present,
    else local Ollama (free, no key)."""
    explicit = os.environ.get("CLACKY_PROVIDER")
    if explicit:
        return explicit.lower()
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    return "ollama"
