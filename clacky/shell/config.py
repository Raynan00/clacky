import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Where the user's .env lives. In a dev checkout it sits next to this file; in a
# packaged (PyInstaller) .exe the app dir is read-only, so keys live in a
# user-writable config dir instead — that's what makes the "just add your keys"
# .exe work. We always ALSO read the user dir so both layouts behave the same.
_HERE = Path(__file__).parent
_FROZEN = bool(getattr(sys, "frozen", False))
_USER_DIR = Path(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")) / "Clacky"
_USER_ENV = _USER_DIR / ".env"
# Where runtime changes (provider switch, wizard-saved keys) are written.
_WRITABLE_ENV = _USER_ENV if _FROZEN else (_HERE / ".env")

# Load in priority order: bundled/dev first, then the user config dir (which
# overrides — and is the only source in a frozen build). .env.local wins over .env.
for _p in (_HERE / ".env", _HERE / ".env.local", _USER_ENV):
    if _p.exists():
        load_dotenv(_p, override=True)


@dataclass
class Config:
    # LLM
    anthropic_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    openai_api_key: Optional[str] = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    google_api_key: Optional[str] = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or None)
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    # Legacy single-model knob — still respected as a fallback for both slots
    # below. New users should prefer OLLAMA_VISION_MODEL / OLLAMA_TEXT_MODEL.
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.2-vision"))
    # Two-slot model selection: vision = screen-aware queries, text = Code Mode
    # / journal Q&A / no-screenshot replies. Either can be overridden at runtime
    # via cfg.set_ollama_model("vision"|"text", name).
    ollama_vision_model: str = field(default_factory=lambda: os.getenv("OLLAMA_VISION_MODEL", "") or os.getenv("OLLAMA_MODEL", "llama3.2-vision"))
    ollama_text_model:   str = field(default_factory=lambda: os.getenv("OLLAMA_TEXT_MODEL", "") or "llama3.2:3b")

    # STT
    deepgram_api_key: Optional[str] = field(default_factory=lambda: os.getenv("DEEPGRAM_API_KEY") or None)
    # English-only "small.en" is far more accurate than the old "base" default
    # (and .en beats the multilingual model for English at the same size).
    # Tune via WHISPER_MODEL: "base.en" (faster), "medium.en"/"distil-large-v3"
    # (more accurate), or set DEEPGRAM_API_KEY for cloud STT (best accuracy+speed).
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "small.en"))

    # TTS
    elevenlabs_api_key: Optional[str] = field(default_factory=lambda: os.getenv("ELEVENLABS_API_KEY") or None)
    elevenlabs_voice_id: str = field(default_factory=lambda: os.getenv("ELEVENLABS_VOICE_ID", ""))

    # Search
    tavily_api_key: Optional[str] = field(default_factory=lambda: os.getenv("TAVILY_API_KEY") or None)

    # App
    hotkey: str = field(default_factory=lambda: os.getenv("CLACKY_HOTKEY", "ctrl+alt+m"))

    def llm_provider(self) -> str:
        """Returns the active LLM provider (runtime override > priority chain).

        Priority chain: Claude > OpenAI > GitHub Copilot > Gemini > Ollama.
        """
        override = os.environ.get("CLACKY_ACTIVE_LLM", "").strip().lower()
        if override in self.available_llm_providers():
            return override
        if self.anthropic_api_key:
            return "claude"
        if self.openai_api_key:
            return "openai"
        try:
            from ai.github_copilot_provider import is_authenticated as _gh_ok
            if _gh_ok():
                return "copilot"
        except Exception:
            pass
        if self.google_api_key:
            return "gemini"
        return "ollama"

    def available_llm_providers(self) -> list[str]:
        """All providers the user can switch to right now."""
        out = []
        if self.anthropic_api_key:
            out.append("claude")
        if self.openai_api_key:
            out.append("openai")
        try:
            from ai.github_copilot_provider import is_authenticated as _gh_ok
            if _gh_ok():
                out.append("copilot")
        except Exception:
            pass
        if self.google_api_key:
            out.append("gemini")
        out.append("ollama")   # always available if the daemon is running
        return out

    def set_active_llm(self, name: str) -> None:
        """Runtime switch — next query uses this provider. Persisted to .env."""
        name = name.lower()
        os.environ["CLACKY_ACTIVE_LLM"] = name
        # Write to .env so the choice survives restarts (user dir when frozen).
        env_path = _WRITABLE_ENV
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True) if env_path.exists() else []
            key = "CLACKY_ACTIVE_LLM"
            found = False
            for i, line in enumerate(lines):
                if line.startswith(key + "=") or line.startswith(key + " ="):
                    lines[i] = f"{key}={name}\n"
                    found = True
                    break
            if not found:
                lines.append(f"\n{key}={name}\n")
            env_path.write_text("".join(lines), encoding="utf-8")
        except Exception:
            pass  # non-fatal — runtime switch still works via os.environ

    def save_env_values(self, values: dict) -> None:
        """Persist env values (API keys etc.) to the writable .env and apply them
        to the running process immediately — used by the setup wizard, so a new
        user can paste keys and go, no restart. In a frozen .exe this writes to
        %LOCALAPPDATA%\\Clacky\\.env (the app dir is read-only)."""
        env_path = _WRITABLE_ENV
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            lines = (env_path.read_text(encoding="utf-8").splitlines(keepends=True)
                     if env_path.exists() else [])
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            for key, val in values.items():
                val = (val or "").strip()
                if not val:
                    continue
                os.environ[key] = val
                for i, line in enumerate(lines):
                    if line.startswith(key + "=") or line.startswith(key + " ="):
                        lines[i] = f"{key}={val}\n"
                        break
                else:
                    lines.append(f"{key}={val}\n")
            env_path.write_text("".join(lines), encoding="utf-8")
        except Exception:
            pass  # keys still applied via os.environ for this session
        # Refresh the live cfg fields so providers pick the keys up immediately.
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or None
        self.deepgram_api_key = os.getenv("DEEPGRAM_API_KEY") or None
        self.openai_api_key = os.getenv("OPENAI_API_KEY") or None
        self.google_api_key = (os.getenv("GOOGLE_API_KEY")
                               or os.getenv("GEMINI_API_KEY") or None)

    def stt_provider(self) -> str:
        # Allow explicit override via env (so users can force whisper_cpp etc.)
        forced = os.getenv("CLACKY_STT", "").strip().lower()
        if forced in ("deepgram", "openai", "whisper_cpp", "faster_whisper"):
            return forced
        if self.deepgram_api_key:
            return "deepgram"
        if self.openai_api_key:
            return "openai"
        # Prefer whisper.cpp (GPU-accelerated, same engine as Handy) when the
        # pywhispercpp package is installed; otherwise fall back to faster-whisper.
        try:
            import pywhispercpp  # noqa: F401
            return "whisper_cpp"
        except ImportError:
            return "faster_whisper"

    def tts_provider(self) -> str:
        if self.elevenlabs_api_key:
            return "elevenlabs"
        if self.openai_api_key:
            return "openai"
        return "edge_tts"

    def search_provider(self) -> str:
        if self.tavily_api_key:
            return "tavily"
        return "duckduckgo"

    def describe(self) -> dict:
        """Human-readable summary of active providers for the setup panel."""
        return {
            "llm": self.llm_provider(),
            "stt": self.stt_provider(),
            "tts": self.tts_provider(),
            "search": self.search_provider(),
            "ollama_model": self.ollama_model,
            "ollama_vision_model": self.get_ollama_model("vision"),
            "ollama_text_model":   self.get_ollama_model("text"),
        }

    # ── Ollama runtime model selection ───────────────────────────────────

    def get_ollama_model(self, kind: str = "vision") -> str:
        """Return the active model for the given kind ("vision" | "text").

        Reads runtime override from CLACKY_OLLAMA_VISION_MODEL /
        CLACKY_OLLAMA_TEXT_MODEL first, then the dataclass field, then the
        legacy single-model knob.
        """
        env_key = "CLACKY_OLLAMA_VISION_MODEL" if kind == "vision" else "CLACKY_OLLAMA_TEXT_MODEL"
        runtime = os.environ.get(env_key, "").strip()
        if runtime:
            return runtime
        return self.ollama_vision_model if kind == "vision" else self.ollama_text_model

    def set_ollama_model(self, kind: str, name: str) -> None:
        """Runtime switch for vision/text Ollama model. Persists for the session."""
        if kind not in ("vision", "text"):
            return
        env_key = "CLACKY_OLLAMA_VISION_MODEL" if kind == "vision" else "CLACKY_OLLAMA_TEXT_MODEL"
        os.environ[env_key] = (name or "").strip()
        # Mirror onto the dataclass so describe() picks it up immediately
        if kind == "vision":
            self.ollama_vision_model = name
        else:
            self.ollama_text_model = name


# Singleton
cfg = Config()
