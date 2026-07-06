"""
Central state machine for Clacky Windows.

Orchestrates:
  hotkey / wake-word → ambient listener capture → STT → screen capture
  → web search → (optional Claude Computer Use pointing) → LLM → TTS
"""

import asyncio
import os
import re
import threading
import time
from datetime import datetime
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from config import cfg
from session_log import slog, turn as _log_turn
from ai.base_provider import BaseLLMProvider, Message
from audio.ambient_listener import AmbientListener
from screen.capture import capture_all_screens
from ui.panel import AppState
from tutor import (
    active_window_title, app_key,
    is_locate, is_multistep, is_next, is_stop, is_sensitive_window,
    is_repeat, is_journal_today, is_journal_week, is_quiz_review,
    is_identity_question,
)
from tutor_features import (
    journal, pdf_context, ocr, code_mode, lesson_recorder,
    multilang, workflow_capture, collab,
)
import skills as skills_pkg


def _ensure_ollama_running():
    """Start Ollama if it isn't already running. Waits up to 8 s for it to be ready."""
    import subprocess
    import urllib.request

    url = "http://localhost:11434/api/tags"
    for _ in range(2):
        try:
            urllib.request.urlopen(url, timeout=2)
            return  # already up
        except Exception:
            pass

    # Not responding — launch it detached so it survives the Python process
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    except FileNotFoundError:
        return  # ollama not installed, provider will fail gracefully

    # Wait up to 8 s for the server to come up
    for _ in range(16):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:
            pass


_SOUL_CACHE: Optional[str] = None


def _load_soul() -> str:
    """Clacky' persona/voice, from SOUL.md (OpenClicky pattern). Edit that file
    to change how Clacky sounds — it's the one place the voice lives."""
    global _SOUL_CACHE
    if _SOUL_CACHE is None:
        try:
            from pathlib import Path
            _SOUL_CACHE = (Path(__file__).parent / "SOUL.md").read_text(
                encoding="utf-8").strip()
        except Exception:
            _SOUL_CACHE = (
                "You are Clacky, a voice-first Windows buddy next to the cursor. "
                "You see the screen, talk out loud, and point at things. Be direct, "
                "warm, and brief — a friend at the user's shoulder, never a tutor."
            )
    return _SOUL_CACHE


def _build_system_prompt(
    window_title: str = "",
    lesson_step: int = 0,
    total_steps: int = 0,
    quiz_mode: bool = False,
    detected_coord: Optional[tuple] = None,
    code_active: bool = False,
    language_code: str = "en",
    extra: str = "",
) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    ctx_lines = [f"TODAY'S DATE: {today}."]
    if window_title:
        ctx_lines.append(f'ACTIVE WINDOW: "{window_title}"')
    if detected_coord:
        _x, _y, label = detected_coord
        ctx_lines.append(
            f"DETECTED ELEMENT: the pointing engine already moved the cursor onto "
            f"'{label}'. The user can SEE the cursor — name the element in plain "
            f"words and NEVER say coordinates, numbers, or pixel positions aloud."
        )
    if total_steps > 1:
        ctx_lines.append(
            f"LESSON PROGRESS: step {lesson_step + 1} of {total_steps}. "
            "Explain ONLY this step, then end with \"Say 'next' when ready.\""
        )

    # ── Quiz mode: dominant prompt that completely replaces normal behaviour ──
    if quiz_mode:
        return f"""You are Clacky, an interactive QUIZ TUTOR. The user has
turned on Quiz Mode and wants to be tested, NOT explained to.

{chr(10).join(ctx_lines)}

ABSOLUTE QUIZ RULES (override everything else):
  • NEVER answer the user's question directly. NEVER point at UI elements.
    NEVER emit [POINT:...] tags. NEVER explain how things work.
  • If the user is greeting / starting ("hello", "what's on my screen", "begin",
    "quiz me", anything), START the quiz: ask ONE short, specific question
    about what's visible on screen — name a button, recognise an icon, predict
    what a click would do, identify the active app, etc.
  • If the user's last message looks like an ANSWER (a noun, a short phrase, a
    yes/no), evaluate it in ≤1 sentence ("Correct!" / "Close — actually..."),
    then immediately ask the NEXT question.
  • Questions should be progressively harder. Vary topic across UI literacy,
    keyboard shortcuts, what's currently visible, predicting outcomes.
  • Keep it warm and encouraging. Never lecture.
  • Format every turn as:  <one-line evaluation if applicable>  <one question>

STYLE: short, friendly, never more than 2 sentences. End every turn with a
question mark."""

    return f"""{_load_soul()}

{chr(10).join(ctx_lines)}

How you work (the mechanics — these are tools, not a script; stay natural):
  1. POINTING — your signature move. Lead with it: when the screen has a target
     relevant to what they asked, point FIRST, then say your one line. Point at
     the SINGLE most relevant thing — don't pepper the screen with points.
     • If a DETECTED ELEMENT is provided above, the cursor is ALREADY on it — do
       NOT emit a [POINT:...] tag; just name it in one short sentence.
     • Otherwise emit [POINT:X,Y:LABEL:screen1] where X,Y are the pixel
       coordinates of the CENTER of the element in the screenshot you were given
       (top-left origin), and LABEL is its short on-screen text (e.g. "Sign in",
       "File"). Give BOTH your best pixel estimate AND the exact label — we snap to
       the precise element by label when we can, and use your coordinate otherwise.
     • Only point at something actually on screen and clearly relevant. If it's
       not visible, say so plainly ("I don't see X — your screen shows [actual]").
       Never invent generic directions.
     • If pointing wouldn't help — a conceptual question, or nothing relevant is
       on screen — emit [POINT:none] and just answer. Don't force a point.

  2. MULTI-STEP TASKS (export, install, configure, setup, etc.):
     Describe ONLY the next single step. Point at it. End with "Say 'next' when
     ready." Never dump a numbered list of 5 steps in one response.

  3. VISION & VOICE: describe only what is ACTUALLY on the screen, and trust your
     eyes over the user's words (if they say "YouTube" but the screen shows
     Google, tell them). Speak as if you're looking at their screen directly —
     say "I can see…" or "on your screen…". NEVER say the words "screenshot" or
     "image"; you're looking at their screen, not a picture.

  4. WEB SEARCH: when [Web Search Results] appear in the system prompt, you MUST
     use them as your primary source. Give a DIRECT, SPECIFIC answer — never say
     "I don't know" or list vague options if the results contain real names,
     rankings, or facts. Commit to what the search found. Cite like [1], [2].
     Today is {today}. Your training data is stale — always prefer search results
     over your own memory for anything recent (news, rankings, current events,
     "who is", "what is the best", "latest", "top", etc.).

  5. PUBLIC figures, celebrities, YouTubers, athletes, politicians, companies,
     products, brands — ANSWER FREELY using your training data + search results.
     NEVER refuse with "I can't identify people" / "I can't help with that" /
     "personal or sensitive". The user is asking a tutor question, not running
     facial recognition — these are public figures with public Wikipedia pages.
     If asked "who is MrBeast" — say "MrBeast (Jimmy Donaldson) is an American
     YouTuber known for…". Same for any other public person.

  6. ANNOTATE for emphasis: when teaching where multiple things matter, you
     MAY emit annotation tags (in addition to one POINT tag):
       [ARROW:x1,y1->x2,y2]            line with arrowhead
       [CIRCLE:x,y,r:label]            ring around an area
       [UNDERLINE:x,y,width]           underline a word
       [LABEL:x,y:short text]          floating caption
     Use sparingly — at most 2 annotations per response.

Keep replies tight, and no markdown bullets unless you're genuinely listing
options.{_code_addendum(code_active)}{_lang_addendum(language_code)}{extra}"""


def _code_addendum(active: bool) -> str:
    if not active:
        return ""
    from tutor_features.code_mode import code_system_prompt_addendum
    return code_system_prompt_addendum()


def _lang_addendum(code: str) -> str:
    from tutor_features.multilang import language_directive
    return language_directive(code)


def _guess_label(transcript: str) -> str:
    """Extract a 1-3 word label from a locate query for the speech bubble.
       'where is the search bar' → 'search bar' """
    t = transcript.lower().strip().rstrip("?.!")
    for kw in ("where is the ", "where's the ", "show me the ",
              "find the ", "locate the ", "click the ", "click on the ",
              "how do i click ", "how do i find ", "how do i open ",
              "point at the ", "point to the ", "highlight the "):
        if kw in t:
            tail = t.split(kw, 1)[1]
            words = tail.split()
            return " ".join(words[:3]) or "here"
    return "right here!"


def _split_steps(text: str) -> list[str]:
    """Parse a numbered list out of an LLM response. Returns [] if not a list."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    steps = []
    for ln in lines:
        m = re.match(r"^(?:\d+[\).]|[-*])\s+(.+)$", ln)
        if m:
            steps.append(m.group(1).strip())
    return steps


POINT_RE = re.compile(r'\[POINT:(\d+),(\d+):([^:\]]+):screen(\d+)\]')
# A partial "[POINT..." prefix that hasn't closed yet — hold it back from display
# until the next chunk so we never leak a half tag.
POINT_PARTIAL_RE = re.compile(r'\[(?:P|PO|POI|POIN|POINT|POINT:[^\]]*)?$')

# Whiteboard annotation tags — same idea as POINT, parsed and stripped.
ARROW_RE     = re.compile(r'\[ARROW:(\d+),(\d+)->(\d+),(\d+)\]')
CIRCLE_RE    = re.compile(r'\[CIRCLE:(\d+),(\d+),(\d+):([^\]]+)\]')
UNDERLINE_RE = re.compile(r'\[UNDERLINE:(\d+),(\d+),(\d+)\]')
LABEL_RE     = re.compile(r'\[LABEL:(\d+),(\d+):([^\]]+)\]')
ANY_TAG_RE   = re.compile(
    r'\[(?:POINT|ARROW|CIRCLE|UNDERLINE|LABEL):[^\]]*\]'
)
ANY_PARTIAL_RE = re.compile(r'\[[A-Z]{0,9}(?::[^\]]*)?$')

# Questions that ask Clacky to locate / click UI elements — triggers the
# Computer Use element locator when Claude is the provider.
POINT_TRIGGER_RE = re.compile(
    r"\b(where\s+(is|do|can)|how\s+do\s+i\s+(click|find|open|access|use)|"
    r"point\s+(at|to)|show\s+me\s+(the|where)|click\s+(the|on)|find\s+the)\b",
    re.IGNORECASE,
)


class CompanionManager(QObject):
    """Thread-safe signals for Qt UI updates from async/audio threads."""

    sig_state_changed       = pyqtSignal(object)          # AppState
    sig_response_chunk      = pyqtSignal(str)
    sig_response_done       = pyqtSignal(str)
    sig_audio_level         = pyqtSignal(float)
    sig_point_at            = pyqtSignal(float, float, str)
    sig_point_hold          = pyqtSignal(bool)            # True → dwell forever until release
    sig_point_release       = pyqtSignal()                # end dwell + fly buddy back
    sig_error               = pyqtSignal(str)
    sig_copilot_models_done = pyqtSignal(int)             # arg = model count
    sig_models_refreshed    = pyqtSignal(str, int)        # (provider, count)
    sig_ollama_models       = pyqtSignal(dict)            # {"vision": [...], "text": [...]}
    sig_ollama_pull_status  = pyqtSignal(str, str)        # (model_name, status_msg)
    sig_arrow               = pyqtSignal(float, float, float, float)
    sig_circle              = pyqtSignal(float, float, float)
    sig_underline           = pyqtSignal(float, float, float)
    sig_label               = pyqtSignal(float, float, str)
    sig_recording_state     = pyqtSignal(bool, str)       # (is_recording, output_dir)

    def __init__(self):
        super().__init__()
        self._state: AppState = AppState.IDLE
        self._history: List[Message] = []
        self._current_model: Optional[str] = None
        self._web_search_enabled = True
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Providers (lazy)
        self._llm: Optional[BaseLLMProvider] = None
        self._stt = None
        self._tts = None

        # Current in-flight generation — tracked so Esc / stop can cancel
        self._current_task: Optional[asyncio.Future] = None
        self._cancel_flag = False

        # Per-app memory: { window_title: [Message, ...] }
        self._app_memory: dict[str, List[Message]] = {}
        # Cross-session memory + learned skills (~/.clacky/memory.json)
        from memory_store import MemoryStore
        self._memory = MemoryStore()
        # Background agents: id -> {desc, status, result, task}
        self._bg: dict[int, dict] = {}
        self._bg_counter = 0
        # Live-streaming STT session for the current turn (opt-in; None = batch)
        self._stt_session = None
        # Current lesson: sequence of pending steps for multi-step tutorials
        self._lesson_steps: list[str] = []
        self._lesson_step_idx: int = 0
        # Toggles
        self._slow_mode = False
        self._quiz_mode = False
        self._privacy_guard = True
        self._code_mode_auto = True       # auto-detect IDE windows
        self._multilang = True             # auto-reply in user's language
        self._journal_enabled = True       # log every Q&A to SQLite
        self._ocr_enabled = True           # use Tesseract for fine print
        self._last_response = ""           # for "say it again" voice command
        self._attached_docs: list[tuple[str, str]] = []   # (filename, text)

        # Optional subsystems (lazy-init to keep startup fast)
        self._recorder: Optional[lesson_recorder.LessonRecorder] = None
        self._collab: Optional[collab.CollabSession] = None
        self._workflow: Optional[workflow_capture.WorkflowCapture] = None

        # Load user-created skills from skills/ + ~/.clacky/skills/
        try:
            skills_pkg.load_all()
        except Exception:
            pass

        # Always-on ambient listener
        self._listener = AmbientListener(
            on_level=self._handle_level,
            on_wake=self._handle_wake,
        )

        # Background asyncio loop
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Mic error: {e}")
        # Sleep/wake watchdog — restarts mic + loop after system resume
        self._start_sleep_watchdog()
        # On startup, refresh any stale model cache in the background.
        # 30-day TTL means this is a once-a-month no-op for most launches.
        self._submit(self._refresh_stale_models())
        # Move cold-start (provider init, edge-tts connect, TLS/DNS) off the first
        # turn so the first thing the user says isn't the slowest.
        self._submit(self._warm_up())

    async def _warm_up(self):
        """Pre-instantiate the configured STT/TTS/LLM providers and do a throwaway
        TTS synth + host pings, so the first real turn skips import/handshake cost."""
        try:
            self._get_stt()
            self._get_llm()
            tts = self._get_tts()
            if hasattr(tts, "synth"):
                await tts.synth("Ready.")        # warms edge-tts; audio discarded
                # Pre-baked instant acks: played the moment a SLOW route (tour /
                # act) is chosen, so Clacky responds audibly within ~1s even though
                # the real answer takes seconds. Perceived latency ≈ instant.
                self._acks = []
                for phrase in ("Let me take a look.",):
                    try:
                        self._acks.append(await tts.synth(phrase))
                    except Exception:
                        pass
                if self._filler_enabled():
                    self._fillers = []
                    for phrase in ("Let me see.", "One sec.", "Checking that."):
                        try:
                            self._fillers.append(await tts.synth(phrase))
                        except Exception:
                            pass
        except Exception:
            pass
        try:
            # Warm the SHARED pool (the one real calls reuse), not a throwaway
            # client — so the first turn's TLS handshake is already paid.
            http = self._get_http()
            for url in ("https://api.anthropic.com/", "https://api.deepgram.com/"):
                try:
                    await http.get(url, timeout=4)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if cfg.anthropic_api_key:
                # Tiny call to warm the SDK client's connection too (the router).
                await self._get_anthropic().messages.create(
                    model="claude-haiku-4-5-20251001", max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}])
        except Exception:
            pass

    async def _refresh_stale_models(self):
        try:
            from ai.model_registry import refresh_all_stale
            results = await refresh_all_stale()
            for prov, count in results.items():
                if count > 0:
                    self.sig_models_refreshed.emit(prov, count)
        except Exception:
            pass   # silent — not user-facing on startup

    def shutdown(self):
        # Kill any audio that was playing when the user clicked Quit
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass
        self._listener.stop()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ── Sleep/wake watchdog ───────────────────────────────────────────────────

    def _start_sleep_watchdog(self):
        """Background thread that detects system resume after sleep/hibernate
        and restarts the mic stream + asyncio loop so the panel stays live."""
        def _watch():
            HEARTBEAT = 5.0          # check every 5 s
            DRIFT_THRESHOLD = 15.0   # if we wake and >15 s have passed, resume occurred
            last_tick = time.monotonic()
            while True:
                time.sleep(HEARTBEAT)
                now = time.monotonic()
                drift = now - last_tick - HEARTBEAT
                last_tick = now
                if drift > DRIFT_THRESHOLD:
                    # System was sleeping — restart subsystems
                    self._on_system_resume()

        t = threading.Thread(target=_watch, daemon=True)
        t.start()

    def _on_system_resume(self):
        """Called automatically after the laptop wakes from sleep."""
        # 1. Restart the mic stream (sounddevice handles become stale on resume)
        try:
            self._listener.stop()
        except Exception:
            pass
        time.sleep(1.0)   # give Windows audio stack time to reinit
        try:
            self._listener.start()
        except Exception as e:
            self.sig_error.emit(f"Mic restart after sleep failed: {e}")

        # 2. If the asyncio loop thread died, restart it
        if not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

        # 3. Reset state to IDLE so the panel shows the correct status
        if self._state != AppState.IDLE:
            self._emit_state(AppState.IDLE)

    def _submit(self, coro):
        if self._loop:
            asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ── Provider lazy init ────────────────────────────────────────────────────

    def _get_llm(self) -> BaseLLMProvider:
        if self._llm is None:
            provider = cfg.llm_provider()
            if provider == "claude":
                from ai.claude_provider import ClaudeProvider
                self._llm = ClaudeProvider()
            elif provider == "openai":
                from ai.openai_provider import OpenAIProvider
                self._llm = OpenAIProvider()
            elif provider == "gemini":
                from ai.gemini_provider import GeminiProvider
                self._llm = GeminiProvider()
            elif provider == "copilot":
                from ai.github_copilot_provider import GitHubCopilotProvider
                self._llm = GitHubCopilotProvider()
            else:
                _ensure_ollama_running()
                from ai.ollama_provider import OllamaProvider
                self._llm = OllamaProvider()
        return self._llm

    def _get_stt(self):
        if self._stt is None:
            provider = cfg.stt_provider()
            print(f"[clacky-debug] STT provider = {provider} "
                  f"(deepgram key set: {bool(cfg.deepgram_api_key)})", flush=True)
            if provider == "deepgram":
                from audio.stt.deepgram_stt import DeepgramSTT
                self._stt = DeepgramSTT()
            elif provider == "openai":
                from audio.stt.openai_stt import OpenAISTT
                self._stt = OpenAISTT()
            elif provider == "whisper_cpp":
                try:
                    from audio.stt.whisper_cpp_stt import WhisperCppSTT
                    self._stt = WhisperCppSTT()
                except ImportError:
                    # pywhispercpp missing → fall back silently
                    from audio.stt.faster_whisper_stt import FasterWhisperSTT
                    self._stt = FasterWhisperSTT()
            else:
                from audio.stt.faster_whisper_stt import FasterWhisperSTT
                self._stt = FasterWhisperSTT()
        return self._stt

    def _get_http(self):
        """One shared httpx client for all raw Anthropic calls (tour/agent/locate).
        Reusing the pool keeps the TLS connection WARM — a fresh client per call
        was paying a ~0.2-0.4s handshake on every single model round-trip."""
        if getattr(self, "_http", None) is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=60)
        return self._http

    def _get_anthropic(self):
        """Shared Anthropic SDK client (router / workspace / research) — same
        warm-connection reasoning as _get_http."""
        if getattr(self, "_anthropic", None) is None:
            import anthropic
            self._anthropic = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        return self._anthropic

    def _reset_clients(self):
        """Drop cached clients so the next call rebuilds them — used after network
        errors (e.g. a stale pool after system sleep/resume)."""
        self._http = None
        self._anthropic = None

    def _get_tts(self):
        if self._tts is None:
            provider = cfg.tts_provider()
            if provider == "elevenlabs":
                from audio.tts.elevenlabs_provider import ElevenLabsProvider
                self._tts = ElevenLabsProvider()
            elif provider == "openai":
                from audio.tts.openai_tts_provider import OpenAITTSProvider
                self._tts = OpenAITTSProvider()
            else:
                from audio.tts.edge_tts_provider import EdgeTTSProvider
                self._tts = EdgeTTSProvider()
        return self._tts

    # ── Input sources ─────────────────────────────────────────────────────────

    def on_hotkey_press(self):
        # Barge-in: if Clacky is thinking or speaking, the hotkey cancels the
        # current turn (audio stops within ~50 ms) and starts a fresh capture.
        if self._state in (AppState.THINKING, AppState.SPEAKING):
            self._cancel_flag = True
            self._kill_filler()
            try:
                from audio.playback import stop_audio
                stop_audio()
            except Exception:
                pass
        elif self._state != AppState.IDLE:
            return
        self._begin_capture()

    def on_hotkey_release(self):
        if self._state == AppState.LISTENING:
            self._submit(self._end_capture_and_process())

    def _handle_wake(self):
        """Triggered from ambient listener when wake-word is detected."""
        # Hard kill-switch: CLACKY_WAKE_WORD=0 means the wake word can NEVER fire,
        # regardless of any toggle state (demo recording says "Clacky" in narration).
        if os.environ.get("CLACKY_WAKE_WORD", "1") == "0":
            slog("HEAR", "wake word detected but CLACKY_WAKE_WORD=0 — ignored")
            return
        if self._state != AppState.IDLE:
            return
        slog("HEAR", "wake word triggered")
        self._begin_capture()
        self._submit(self._auto_stop_after_pause())

    def _handle_level(self, rms: float):
        try:
            self.sig_audio_level.emit(rms)
        except Exception:
            pass   # never crash the sounddevice audio thread

    # ── Capture flow ──────────────────────────────────────────────────────────

    def _begin_capture(self):
        # Bump the turn counter so a just-cancelled turn can't reset us to IDLE.
        self._gen = getattr(self, "_gen", 0) + 1
        self._prewarm_screenshot()   # capture in parallel with the user speaking
        self._start_streaming_stt()  # opt-in: stream audio live as the user speaks
        self._listener.start_recording()
        print("[clacky-debug] recording started — hold the hotkey, speak, release", flush=True)
        self._emit_state(AppState.LISTENING)

    def _filler_enabled(self) -> bool:
        return os.environ.get("CLACKY_FILLER", "0") == "1"

    def _maybe_start_filler(self):
        """Perceived-latency: after a short delay, play a pre-synthesized 'thinking'
        clip to cover the LLM gap. Opt-in (CLACKY_FILLER=1); killed the instant the
        real reply begins speaking. No-op (and no risk) when disabled."""
        self._filler_task = None
        if not self._filler_enabled() or not getattr(self, "_fillers", None):
            return
        self._filler_task = asyncio.create_task(self._filler_loop())

    async def _filler_loop(self):
        try:
            await asyncio.sleep(0.8)
            if self._cancel_flag:
                return
            import random
            from audio.playback import play_mp3_async
            await play_mp3_async(random.choice(self._fillers))
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _kill_filler(self):
        t = getattr(self, "_filler_task", None)
        if t is None:
            return
        self._filler_task = None
        t.cancel()
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass

    def _streaming_stt_enabled(self) -> bool:
        # ON by default now: stream audio to Deepgram live so the transcript is
        # ready the instant you release the key (no post-release upload wait).
        # Only for the Deepgram path (needs the key); set CLACKY_STREAM_STT=0 to
        # force the old batch upload. Batch is still the automatic fallback if a
        # live session yields nothing, so this can't leave you with no transcript.
        return (os.environ.get("CLACKY_STREAM_STT", "1") != "0"
                and cfg.stt_provider() == "deepgram"
                and bool(cfg.deepgram_api_key))

    def _start_streaming_stt(self):
        """Opt-in: open a live Deepgram WebSocket and stream audio to it while the
        user speaks, so the transcript is ready ~instantly on release. Falls back
        to batch (in _end_capture) on any failure — the PCM buffer is still kept."""
        self._stt_session = None
        if not self._streaming_stt_enabled():
            return
        try:
            from audio.stt.deepgram_streaming import DeepgramStreamingSession
            self._stt_session = DeepgramStreamingSession()
            self._submit(self._stt_session.start())
            self._listener.set_rec_chunk_callback(self._stt_session.feed)
        except Exception as e:
            print(f"[clacky-debug] streaming stt start failed: {e}", flush=True)
            self._stt_session = None
            self._listener.set_rec_chunk_callback(None)

    def _prewarm_screenshot(self):
        """Grab the screen on key-press (in a thread) so it's ready the instant the
        user finishes speaking, instead of blocking the response path afterward."""
        import threading
        import time as _time
        self._prewarmed = None

        def _grab():
            try:
                shots = capture_all_screens()
                self._prewarmed = (_time.monotonic(), shots)
            except Exception:
                self._prewarmed = None
        threading.Thread(target=_grab, daemon=True).start()

    async def _auto_stop_after_pause(self):
        """When triggered by wake word, wait for user to finish speaking."""
        import time
        max_total_s = 10.0
        start_t = time.monotonic()
        while self._state == AppState.LISTENING:
            await asyncio.sleep(0.15)
            if time.monotonic() - start_t > max_total_s:
                break
        await self._end_capture_and_process()

    async def _end_capture_and_process(self):
        gen = getattr(self, "_gen", 0)   # this turn's id; a barge-in bumps it
        _log_turn()                      # reset the +N.NNs clock → true per-turn latency
        pcm = self._listener.stop_recording()
        self._listener.set_rec_chunk_callback(None)
        session = self._stt_session      # live-STT session for this turn (or None)
        self._stt_session = None
        # Log level + device so "recorded silence" (wrong default mic — e.g. a Camo/
        # Phone Link virtual device) is instantly visible instead of a mystery.
        try:
            import numpy as _np
            _arr = _np.frombuffer(pcm, dtype=_np.int16)
            _rms = float(_np.sqrt(((_arr.astype(_np.float32) / 32768.0) ** 2).mean())) if _arr.size else 0.0
        except Exception:
            _rms = -1.0
        try:
            import sounddevice as _sd
            _dev = _sd.query_devices(kind="input")["name"]
        except Exception:
            _dev = "?"
        print(f"[clacky-debug] captured {len(pcm)} bytes (~{len(pcm)/32000:.2f}s) "
              f"rms={_rms:.4f} mic='{_dev}'", flush=True)
        if 0 <= _rms < 0.002 and len(pcm) > 16000:
            print("[clacky-debug] -> that's SILENCE from a live mic — the default "
                  "input device is probably wrong (Camo/Phone Link virtual mic?). "
                  "Settings > Sound > Input, pick your real mic, restart Clacky.",
                  flush=True)
        if len(pcm) < 3200:  # < 0.1s of audio — ignore
            if session is not None:
                try:
                    await session.finish()   # close the socket cleanly
                except Exception:
                    pass
            print("[clacky-debug] -> dropped: too short / no audio. Hold the key while "
                  "speaking, and check the mic input device (System > Sound > Input).", flush=True)
            self._emit_state(AppState.IDLE)
            return

        self._emit_state(AppState.THINKING)
        self._suppress_llm_point = False  # accurate detection overrides LLM point-guess
        self._pointed_labels = set()      # dedup proactive points within this turn
        self._main_point = None           # the ONE point to hold this response (Clicky)
        self._main_point_fired = False    # fire it once, only after speech starts
        self._speech_started = False
        self._pointing_held = False       # → release the dwell at turn end

        try:
            # 1. Transcribe — prefer the live-streamed transcript (ready ~instantly,
            # since audio streamed during speech); fall back to batch on any miss.
            transcript = ""
            if session is not None:
                try:
                    transcript = await session.finish()
                except Exception:
                    transcript = ""
                if transcript.strip():
                    print("[clacky-debug] STT via live stream", flush=True)
            if not transcript.strip():
                transcript = await self._get_stt().transcribe(pcm)
            slog("HEAR", f"{transcript!r}")
            if not transcript.strip():
                print("[clacky-debug] -> dropped: empty transcript (audio captured, but "
                      "speech-to-text returned nothing).", flush=True)
                self._emit_state(AppState.IDLE)
                return

            # Pending-confirmation gate (Phase 2 actuation): a proposed click waits
            # here for an explicit yes/no. Nothing acts without this confirmation.
            if getattr(self, "_pending_action", None):
                await self._resolve_pending(transcript)
                return

            # ── Voice commands — short-circuit before LLM ──
            if is_stop(transcript):
                self.stop()
                return

            # "Undo" — reverse the last organize (instant, local, no LLM).
            _t = transcript.strip()
            if (re.fullmatch(r"(hey[\s,]+)?(clacky[\s,]+)?(un[- ]?do( that| it)?|"
                             r"undue|on[- ]?do|and do|"
                             r"put (it|them|everything) back|take it back)[\s.!?]*",
                             _t, re.I)
                    # …or a short sentence ENDING in "undo" ("actually I kinda
                    # liked the mess, undo") — but never questions ("how do I
                    # undo in Premiere" ends with 'premiere', doesn't match).
                    or (len(_t) < 70
                        and re.search(r"\b(un[- ]?do( that| it)?|put (it|everything) "
                                      r"back)[\s.!?]*$", _t, re.I)
                        and not re.match(r"(how|what|where|why|when)\b", _t, re.I))):
                from clacky.agent import journal as _org_journal
                _stagger = float(os.environ.get("CLACKY_MOVE_STAGGER", "0.12") or 0)
                msg = await asyncio.to_thread(_org_journal.undo_last, _stagger)
                slog("ACT", f"undo: {msg}")
                await self._reply_local(msg)
                return

            title = active_window_title()
            ak = app_key(title)

            if is_next(transcript) and self._lesson_steps:
                await self._advance_lesson_step(ak)
                return

            # "say it again" — replay the last response without a new LLM call
            if is_repeat(transcript) and self._last_response:
                self.sig_response_chunk.emit(self._last_response)
                self.sig_response_done.emit(self._last_response)
                self._emit_state(AppState.SPEAKING)
                try:
                    await self._get_tts().speak(self._last_response)
                except Exception:
                    pass
                self._emit_state(AppState.IDLE)
                return

            # Journal voice queries — answered locally, no LLM call needed
            if is_journal_today(transcript):
                msg = journal.summarise(journal.entries_today(),
                                        "Here's what you asked about today:\n")
                await self._reply_local(msg)
                return
            if is_journal_week(transcript):
                msg = journal.summarise(journal.entries_this_week(),
                                        "Here's the past week:\n")
                await self._reply_local(msg)
                return
            if is_quiz_review(transcript):
                await self._spaced_review()
                return

            # User-created skills (run BEFORE the LLM, like built-ins above)
            try:
                skill = skills_pkg.match(transcript)
                if skill:
                    msg = await skill["handler"](self, transcript)
                    if msg:
                        await self._reply_local(msg)
                    return
            except Exception as e:
                self.sig_error.emit(f"Skill error: {e}")

            # Intent routing — the MODEL decides, not a verb regex (the OpenClicky
            # way). One fast Haiku call picks the lane: a hands-on task (the
            # computer-use agent), a spoken screen walkthrough, or plain chat. This
            # is what kills the brittle "typed vs type" keyword matching.
            if cfg.anthropic_api_key:
                # Instant local fast-path for the obvious cases; Haiku router only
                # when it's genuinely ambiguous (hides the routing hop most turns).
                fast = self._fast_route(transcript)
                decision = {"route": fast} if fast else await self._route(transcript)
                route = decision.get("route", "chat")
                slog("ROUTE", f"-> {route}" + ("  (instant, forced)" if fast else "  (haiku)"))
                # Instant audible ack on the SLOW routes — the real response takes
                # seconds; a sub-second "On it!" makes the turn feel immediate.
                if route in ("act", "walkthrough", "organize") and getattr(self, "_acks", None):
                    import random as _random
                    from audio.playback import play_mp3_async
                    slog("TTS", "instant ack")
                    asyncio.create_task(play_mp3_async(_random.choice(self._acks)))
                if route == "act":
                    await self._run_task(transcript)
                    # Act-then-teach: "open X and explain it / walk me through it"
                    # chains the pointing tour onto whatever the task just opened —
                    # the do-AND-teach combo no legacy assistant has.
                    if not self._cancel_flag and re.search(
                            r"\b(and|then)\s+(explain|walk me through|"
                            r"show me around|teach me|tell me what)", transcript, re.I):
                        slog("ROUTE", "chaining tour after act (and-explain)")
                        await self._run_narration(
                            "Give me a quick tour of what's on screen now.")
                    return
                if route == "organize":
                    await self._run_organize_voice(transcript)
                    return
                if route == "workspace":
                    await self._run_workspace(transcript)
                    return
                if route == "background":
                    await self._spawn_background(transcript)
                    return
                if route == "walkthrough":
                    await self._run_narration(transcript)
                    return
                if route == "remember":
                    ok = self._memory.add_fact(decision.get("fact") or transcript)
                    await self._reply_local("Got it — I'll remember that."
                                            if ok else "I already had that noted.")
                    return
                if route == "forget":
                    n = self._memory.forget(decision.get("fact", ""))
                    await self._reply_local("Okay, forgotten." if n
                                            else "I didn't have that saved.")
                    return
                if route == "learn_skill":
                    name = (decision.get("skill_name") or "").strip()
                    steps = (decision.get("skill_steps") or "").strip()
                    if self._memory.add_skill(name, steps):
                        await self._reply_local(f"Learned it — I'll remember your {name}.")
                    else:
                        await self._reply_local(
                            "Tell me the steps and I'll save it as a routine.")
                    return
                # route == "chat" → fall through to the one-shot answer below.

            # 2. Screen capture — skipped if sensitive window (password manager etc.)
            #
            # ALSO skipped for "who is X" / "tell me about X" identity questions:
            # OpenAI + Claude refuse to identify people in screenshots even when
            # the answer is in their training data ("Sorry I can't identify the
            # person in images"). Stripping the screenshot lets the LLM answer
            # from training data + web search instead, which is what the user
            # actually wants when they ask "who is MrBeast" while on YouTube.
            sensitive = self._privacy_guard and is_sensitive_window(title)
            identity_q = is_identity_question(transcript)
            if sensitive or identity_q:
                screenshots = []
                images_b64 = []
            else:
                # Prefer the screenshot prewarmed on key-press (captured while the
                # user spoke); fall back to a fresh grab if it's missing/stale.
                import time as _time
                pre = getattr(self, "_prewarmed", None)
                if pre and (_time.monotonic() - pre[0]) < 8.0:
                    screenshots = pre[1]
                else:
                    screenshots = capture_all_screens()
                images_b64 = [s.base64_jpeg for s in screenshots]
            self._point_shots = screenshots   # for scaling model [POINT] coords

            # 3. Parallel side-work: web search + element locator
            #
            # Pointing now works for EVERY provider:
            #   • If ANTHROPIC_API_KEY is set → use Claude Computer Use
            #     (~5px accuracy, gold standard).
            #   • Otherwise → universal grid-based locator with the active
            #     vision LLM (Copilot GPT-4o, OpenAI, Gemini, Ollama llava).
            #     ~25-50px accuracy. Good enough for buttons/menus/icons.
            locate_triggered = is_locate(transcript)
            multistep = is_multistep(transcript)

            search_task = None
            locate_task = None
            if self._web_search_enabled:
                from ai.web_search import search
                search_task = asyncio.create_task(search(transcript))

            if screenshots and locate_triggered:
                shot = screenshots[0]
                # Pointing accuracy upgrade: try the hybrid pointer first.
                # Tier 1 (UIA tree) is ~5ms and pixel-perfect; tier 2 (OCR)
                # handles canvas apps. Falls through to the vision LLM grid
                # below only when both whiff.
                try:
                    from ai.hybrid_pointer import find_target as _hybrid_find
                    target = _hybrid_find(
                        transcript,
                        screenshot=shot,
                        llm_provider=self._get_llm(),
                    )
                except Exception:
                    target = None

                if target is not None and target.source in ("uia", "ocr"):
                    # Return the Target itself (it has .x/.y) so the detected-coord
                    # handler below works the same as the Computer-Use path. (Bug:
                    # returning a bare (x, y) tuple crashed on `detected.x`.)
                    async def _ready(t=target):
                        return t
                    locate_task = asyncio.create_task(_ready())
                elif cfg.anthropic_api_key:
                    # Path A — Anthropic Computer Use (best accuracy)
                    from ai.element_locator import detect_element
                    locate_task = asyncio.create_task(detect_element(
                        screenshot_jpeg_b64=shot.base64_jpeg,
                        original_width=shot.width,
                        original_height=shot.height,
                        physical_width=shot.physical_width,
                        physical_height=shot.physical_height,
                        physical_left=shot.physical_left,
                        physical_top=shot.physical_top,
                        dpi_scale=shot.dpi_scale,
                        screen_index=shot.index,
                        user_question=transcript,
                    ))
                else:
                    # Path B — Universal grid locator (any vision LLM)
                    try:
                        from ai.universal_locator import detect_element_universal
                        llm = self._get_llm()
                        locate_task = asyncio.create_task(detect_element_universal(
                            llm=llm,
                            screenshot_jpeg_b64=shot.base64_jpeg,
                            original_width=shot.width,
                            original_height=shot.height,
                            physical_width=shot.physical_width,
                            physical_height=shot.physical_height,
                            physical_left=shot.physical_left,
                            physical_top=shot.physical_top,
                            dpi_scale=shot.dpi_scale,
                            screen_index=shot.index,
                            user_question=transcript,
                            model=self._current_model,
                        ))
                    except Exception:
                        # Universal locator should never crash the main flow
                        locate_task = None

            search_results = ""
            if search_task:
                try:
                    search_results = await search_task or ""
                except Exception:
                    search_results = ""

            detected = None
            detected_coord = None
            if locate_task:
                try:
                    detected = await locate_task
                except Exception:
                    detected = None
            if detected:
                # Short label guess — first noun phrase after "the"/"where"
                label = _guess_label(transcript)
                detected_coord = (int(detected.x), int(detected.y), label)
                # Hold this precise point as THE point for the response, but DON'T
                # fire it now — firing during the LLM's think made the cursor point
                # a second or two before any talking. It's fired in sync with the
                # first spoken sentence instead (below / in the TTS player). Suppress
                # the LLM's own [POINT] guess so it can't override this exact spot.
                self._main_point = (float(detected.x), float(detected.y), label)
                self._fire_main_point()   # no-op until speech starts, then fires
                self._suppress_llm_point = True

            # ── Per-turn enrichment: code mode, language, OCR, attached docs ──
            code_active = self._code_mode_auto and code_mode.is_code_window(title)
            lang_code = (multilang.detect_language(transcript)
                         if self._multilang else "en")

            # OCR fallback for fine print (only if user actually asks to read)
            ocr_extra = ""
            if self._ocr_enabled and screenshots and ocr.needs_ocr(transcript):
                try:
                    import base64
                    jpeg = base64.b64decode(screenshots[0].base64_jpeg)
                    txt = ocr.run_ocr(jpeg)
                    if txt:
                        ocr_extra = ocr.format_for_prompt(txt)
                except Exception:
                    pass

            # Attached documents (drag-dropped PDFs etc.)
            doc_extra = ""
            for fname, text in self._attached_docs:
                doc_extra += pdf_context.format_for_prompt(fname, text)

            # 4. Build system prompt with all context
            system = _build_system_prompt(
                window_title=title,
                lesson_step=self._lesson_step_idx,
                total_steps=len(self._lesson_steps),
                quiz_mode=self._quiz_mode,
                detected_coord=detected_coord,
                code_active=code_active,
                language_code=lang_code,
                extra=ocr_extra + doc_extra + (
                    "\n\n" + self._memory.facts_block() if self._memory.facts else "")
                    + self._bg_block(),
            )
            if sensitive:
                system += (
                    "\n\nPRIVACY GUARD: the user's active window looks sensitive "
                    "(password manager, banking, login). I did NOT take a "
                    "screenshot. Answer from memory only, and tell the user you "
                    "skipped the screenshot for safety.\n"
                )
            if search_results:
                from ai.web_search import build_search_context
                system += build_search_context(search_results)

            # Use per-app history so context doesn't bleed between apps
            history = self._app_memory.setdefault(ak, [])

            # Speak the whole reply in one seamless pass by default — no gaps
            # between sentences. (Replies are short, so time-to-first-word is
            # fine, and points fire during generation, i.e. "show then tell".)
            # Set CLACKY_STREAM_TTS=1 for per-sentence streaming on long replies.
            # Gapless streaming TTS, ON by default: speak each finished sentence
            # while the NEXT one synthesizes, for fast time-to-first-word AND no
            # pauses between sentences. Needs a provider with synth(); set
            # CLACKY_STREAM_TTS=0 to force the old whole-reply pass.
            stream_tts = (os.environ.get("CLACKY_STREAM_TTS", "1") != "0"
                          and hasattr(self._get_tts(), "synth"))
            if self._multilang and lang_code != "en":
                try:
                    _tts = self._get_tts()
                    if hasattr(_tts, "set_voice"):
                        _tts.set_voice(multilang.voice_for(lang_code))
                except Exception:
                    pass
            say_buf = ""           # clean text accumulated but not yet spoken
            spoke_anything = False

            # Streaming-TTS pipeline: one player task drains synthesized audio in
            # order while sentences are synthesized AHEAD of playback → gapless.
            _audio_q: asyncio.Queue = asyncio.Queue()
            _player_task = None
            if stream_tts:
                async def _tts_player():
                    from audio.playback import play_mp3_async
                    while True:
                        audio = await _audio_q.get()
                        try:
                            if audio is None:
                                break
                            self._speech_started = True
                            self._fire_main_point()   # fire the held point (once)
                            if audio and not self._cancel_flag:
                                await play_mp3_async(audio)
                        except Exception:
                            pass
                        finally:
                            _audio_q.task_done()
                _player_task = asyncio.create_task(_tts_player())

            async def _speak_stream(text):
                """Synthesize a sentence and queue it; the player plays queued
                sentences in order → gapless. The single held point is fired by the
                player as the first sentence starts (Clicky-style)."""
                nonlocal spoke_anything
                text = (text or "").strip()
                if not text or self._cancel_flag:
                    return
                if not spoke_anything:
                    self._emit_state(AppState.SPEAKING)
                    spoke_anything = True
                self._kill_filler()          # real reply starting — cut any filler
                try:
                    audio = await self._get_tts().synth(text)     # synth ahead
                    await _audio_q.put(audio)
                except Exception:
                    pass

            # 5. Stream LLM — buffer partial [POINT:...] tags so they never leak
            full_response = ""
            display_buf = ""
            self._cancel_flag = False
            self._maybe_start_filler()   # opt-in 'thinking' filler under the LLM gap
            async for chunk in self._get_llm().stream_response(
                user_text=transcript,
                screenshots_b64=images_b64,
                history=history,
                system_prompt=system,
                model=self._current_model,
            ):
                if self._cancel_flag:
                    break
                full_response += chunk
                display_buf += chunk
                self._parse_points(display_buf, collect=True)
                display_buf = ANY_TAG_RE.sub("", display_buf)
                m = ANY_PARTIAL_RE.search(display_buf)
                if m:
                    flush = display_buf[: m.start()]
                    display_buf = display_buf[m.start():]
                else:
                    flush = display_buf
                    display_buf = ""
                if flush:
                    self.sig_response_chunk.emit(flush)
                    if stream_tts:
                        say_buf += flush
                        ready, say_buf = self._split_complete_sentences(say_buf)
                        await _speak_stream(ready)
            if display_buf:
                tail_flush = ANY_TAG_RE.sub("", display_buf)
                self.sig_response_chunk.emit(tail_flush)
                if stream_tts:
                    say_buf += tail_flush

            # 6. Update per-app history
            history.append(Message(role="user", content=transcript))
            history.append(Message(role="assistant", content=full_response))
            self._app_memory[ak] = history[-20:]

            # Multistep: parse numbered steps for later "next" invocations
            if multistep and not self._lesson_steps:
                steps = _split_steps(full_response)
                if len(steps) > 1:
                    self._lesson_steps = steps
                    self._lesson_step_idx = 0

            clean = ANY_TAG_RE.sub("", full_response).strip()
            self.sig_response_done.emit(clean)
            self._last_response = clean   # for "say it again"

            # Log to knowledge journal (skipped in quiz mode — those Q&As aren't
            # study material)
            if self._journal_enabled and not self._quiz_mode:
                try:
                    journal.log_qa(
                        question=transcript, answer=clean,
                        app_key=ak, window_title=title,
                        provider=cfg.llm_provider(),
                        model=self._current_model or "",
                    )
                except Exception:
                    pass

            # Lesson recorder gets the Q&A in transcript.md
            if self._recorder and self._recorder.is_recording:
                self._recorder.log_question(transcript)
                self._recorder.log_answer(clean)

            # Live-collab broadcast
            if self._collab and self._collab.code:
                try:
                    await self._collab.send({
                        "type": "qa", "q": transcript, "a": clean,
                    })
                except Exception:
                    pass

            # 7. TTS — hold the point visible while we speak. (Multilingual voice
            # was set before streaming began.) In streaming mode most of the reply
            # was already spoken sentence-by-sentence above; here we just speak the
            # trailing remainder. Otherwise speak the whole cleaned reply at once.
            if self._cancel_flag:
                if _player_task:
                    await _audio_q.put(None)         # let the player drain + exit
                return
            try:
                if stream_tts:
                    await _speak_stream(say_buf)     # final remainder
                    await _audio_q.put(None)         # drain queue, then stop player
                    if _player_task:
                        await _player_task
                else:
                    self._kill_filler()
                    self._speech_started = True
                    self._fire_main_point()     # fire the one held point as we speak
                    self._emit_state(AppState.SPEAKING)
                    await self._get_tts().speak(clean)
            except asyncio.CancelledError:
                pass

        except Exception as e:
            import traceback
            print("[clacky-debug] pipeline error:", flush=True)
            traceback.print_exc()
            self.sig_error.emit(str(e))

        finally:
            if self._pointing_held:
                self.sig_point_release.emit()
            # Don't reset to IDLE if a barge-in already started a newer turn.
            if getattr(self, "_gen", 0) == gen:
                self._emit_state(AppState.IDLE)

    async def _reply_local(self, msg: str):
        """Show + speak a message that doesn't need an LLM round-trip."""
        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
        self._last_response = msg
        self._emit_state(AppState.SPEAKING)
        try:
            await self._get_tts().speak(msg)
        except Exception:
            pass
        self._emit_state(AppState.IDLE)

    async def _spaced_review(self):
        """SR-style review: pick due entries from the journal, ask one back."""
        due = journal.due_for_review(limit=1)
        if not due:
            await self._reply_local(
                "Nothing due for review right now — keep learning, I'll quiz "
                "you in a few days."
            )
            return
        entry = due[0]
        msg = f"Review: {entry['question']}"
        # Mark "correct" optimistically — a real implementation would wait for
        # the user's answer and grade it. Stubbed: reschedule based on streak.
        try:
            journal.mark_reviewed(int(entry["id"]), correct=True)
        except Exception:
            pass
        await self._reply_local(msg)

    async def _advance_lesson_step(self, ak: str):
        """User said 'next' — re-render the stored next lesson step via TTS,
        no new LLM round-trip needed."""
        self._lesson_step_idx += 1
        if self._lesson_step_idx >= len(self._lesson_steps):
            msg = "That's the last step — you're done!"
            self._lesson_steps = []
            self._lesson_step_idx = 0
        else:
            step = self._lesson_steps[self._lesson_step_idx]
            total = len(self._lesson_steps)
            msg = f"Step {self._lesson_step_idx + 1} of {total}: {step}"

        self.sig_response_chunk.emit(msg)
        self.sig_response_done.emit(msg)
        self._emit_state(AppState.SPEAKING)
        try:
            await self._get_tts().speak(msg)
        except Exception:
            pass
        self._emit_state(AppState.IDLE)

    @staticmethod
    def _split_complete_sentences(buf: str) -> tuple[str, str]:
        """Split `buf` into (complete_sentences, remainder) for streaming TTS.

        A boundary is sentence-ending punctuation followed by ACTUAL whitespace —
        not end-of-buffer — so a decimal like "3.5" arriving across chunks is
        never spoken as "three. five", and the final sentence (no trailing space)
        is held back and spoken once at the end.
        """
        boundaries = list(re.finditer(r"[.!?…](?=\s)", buf))
        if not boundaries:
            return "", buf
        cut = boundaries[-1].end()
        return buf[:cut], buf[cut:]

    def _fire_main_point(self):
        """Fire the single held point ONCE — but only after speech has started, so
        the cursor never points before any talking. Called both when the point
        resolves AND when speech begins; whichever happens later actually fires it.
        This makes pointing reliable even if the model emits [POINT] mid-reply
        (which used to be silently dropped)."""
        if self._main_point_fired or not self._speech_started:
            return
        mp = self._main_point
        if mp is None:
            return
        self._main_point_fired = True
        self._pointing_held = True
        self.sig_point_hold.emit(True)
        self.sig_point_at.emit(mp[0], mp[1], mp[2])

    def _snap_to_uia(self, vx, vy):
        """Snap a PHYSICAL screen coordinate to the exact center of the small UI
        control under it (pixel-perfect), or return None to keep the original.
        Guards against snapping to big containers/windows, whose center would be
        far from the intended element. Reuses the same uiautomation the chat path
        uses, so no new dependency."""
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromPoint(int(vx), int(vy))
            if ctrl is None:
                return None
            r = ctrl.BoundingRectangle
            w, h = r.right - r.left, r.bottom - r.top
            name = (getattr(ctrl, "Name", "") or "")[:24]
            # Only snap to genuine SMALL controls (buttons/fields/icons) — never big
            # panels/containers (e.g. Premiere's custom "TabPanel" windows).
            if w <= 1 or h <= 1 or w > 600 or h > 360:
                slog("SNAP", f"skip: control {w}x{h} too big '{name}'")
                return None
            cx, cy = r.left + w / 2.0, r.top + h / 2.0
            dist = ((cx - vx) ** 2 + (cy - vy) ** 2) ** 0.5
            # A real correction only NUDGES to the element's exact center. A far jump
            # means we hit a container, not the target → keep the model's coordinate.
            if dist > 30:
                slog("SNAP", f"skip: {dist:.0f}px jump to '{name}' (container)")
                return None
            slog("SNAP", f"({int(vx)},{int(vy)})->({int(cx)},{int(cy)}) {dist:.0f}px '{name}'")
            return (cx, cy)
        except Exception:
            return None

    def _point_to_logical(self, cx, cy, screen_n=1):
        """Scale a coordinate from the screenshot's pixel space to logical screen
        coords (Qt cursor space) for the given 1-based screen. Returns (None, None)
        if there's no screenshot or the coordinate is a dummy 0,0."""
        shots = getattr(self, "_point_shots", None)
        if not shots or (cx == 0 and cy == 0):
            return None, None
        idx = screen_n - 1
        shot = shots[idx] if 0 <= idx < len(shots) else shots[0]
        w = shot.width or 1
        h = shot.height or 1
        cx = max(0.0, min(float(cx), w)); cy = max(0.0, min(float(cy), h))
        scale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0
        pw = shot.physical_width or w
        ph = shot.physical_height or h
        lx = int(round((cx / w * pw + shot.physical_left) / scale))
        ly = int(round((cy / h * ph + shot.physical_top) / scale))
        return lx, ly

    def _parse_points(self, text: str, collect: bool = False):
        """Parse [POINT] tags and resolve them to accurate coords via UIA. If
        `collect`, only the FIRST resolved point is kept (in self._main_point) to be
        held for the whole response — Clicky's model — instead of firing every tag
        immediately. Non-collect mode fires each immediately (whole-reply fallback)."""
        for match in POINT_RE.finditer(text):
            if getattr(self, "_suppress_llm_point", False):
                continue  # an accurate detected coordinate already drives the cursor
            xs, ys, label, sn = match.groups()
            label = label.strip()
            if label in self._pointed_labels:
                continue  # already handled this one during the response
            self._pointed_labels.add(label)
            # Reliable pointing (OpenClicky/Clicky-style): SNAP to the exact UIA
            # element when we can find it by label (~5ms, pixel-perfect), otherwise
            # TRUST THE MODEL'S OWN coordinate from the tag. Always shows a point —
            # no more "explains without pointing" when UIA doesn't have the element.
            lx = ly = None
            try:
                from ai.hybrid_pointer import find_target
                t = find_target(label, skip_ocr=True, skip_vision=True)
            except Exception:
                t = None
            if t is not None:
                lx, ly = float(t.x), float(t.y)
                slog("POINT", f"'{label}' -> ({lx:.0f},{ly:.0f}) via UIA (exact)")
            else:
                mlx, mly = self._point_to_logical(float(xs or 0), float(ys or 0),
                                                  int(sn) if sn else 1)
                if mlx is not None:
                    lx, ly = float(mlx), float(mly)
                    slog("POINT", f"'{label}' -> ({lx:.0f},{ly:.0f}) via model coord")
            if lx is None:
                slog("POINT", f"'{label}' -> DROPPED (no coordinate)")
                continue
            if collect:
                if self._main_point is None:      # ONE held point per response
                    self._main_point = (lx, ly, label)
                    self._fire_main_point()       # fires if speech already started
            else:
                self.sig_point_at.emit(lx, ly, label)
        for match in ARROW_RE.finditer(text):
            x1, y1, x2, y2 = (float(v) for v in match.groups())
            self.sig_arrow.emit(x1, y1, x2, y2)
        for match in CIRCLE_RE.finditer(text):
            x, y, r, _label = match.groups()
            self.sig_circle.emit(float(x), float(y), float(r))
        for match in UNDERLINE_RE.finditer(text):
            x, y, w = (float(v) for v in match.groups())
            self.sig_underline.emit(x, y, w)
        for match in LABEL_RE.finditer(text):
            x, y, txt = match.groups()
            self.sig_label.emit(float(x), float(y), txt.strip())

    # Inline tour tag: [POINT:x,y] immediately BEFORE the sentence it belongs to.
    _TOUR_TAG_RE = re.compile(r"\[POINT:\s*(\d+)\s*,\s*(\d+)\s*\]")

    @staticmethod
    def _bubble_label(text: str, maxlen: int = 38) -> str:
        """A clean caption for the cursor bubble: the first phrase of the sentence,
        cut at a natural boundary (sentence end, em-dash, comma, colon) — never a
        mid-word chop like 'This is the Mixer — each vertica'."""
        t = " ".join((text or "").split())
        m = re.search(r"[.!?]|\s[—–-]\s|[,:;]", t)
        if m and m.start() >= 8:            # don't cut absurdly short
            t = t[:m.start()]
        if len(t) > maxlen:
            t = t[:maxlen].rsplit(" ", 1)[0] + "…"
        return t.strip()

    def _parse_tour_segments(self, text: str):
        """Split tour text into [(coords|None, sentence), ...]. Each [POINT:x,y]
        tag applies to the text that FOLLOWS it (until the next tag), so the
        pairing comes from the model's own text order — it cannot be off by one."""
        parts = self._TOUR_TAG_RE.split(text or "")
        segs = []
        intro = parts[0].strip()
        if intro:
            segs.append((None, intro))
        for i in range(1, len(parts) - 2, 3):
            seg = parts[i + 2].strip()
            if seg:
                segs.append(((int(parts[i]), int(parts[i + 1])), seg))
        return segs

    async def _run_narration(self, transcript: str):
        """Screen tour, the way Clicky/OpenClicky actually do it: ONE model call
        returns the whole spoken tour with inline [POINT:x,y] tags placed right
        before the sentence that describes each element. Each point fires exactly
        when its sentence's audio starts, so pointer and voice share ONE channel
        (the text) and can never drift apart.

        (The old multi-turn Computer-Use loop was structurally one step late:
        forced tool_choice made turn 1 point with no speech, and every later
        turn's text described the PREVIOUS turn's move — off by one, forever.)"""
        import base64
        import httpx
        from ai.element_locator import _pick_resolution, _resize_jpeg, _API_URL

        api_key = cfg.anthropic_api_key
        # Use the screenshot prewarmed on key-press (captured while the user was
        # still talking) — a fresh capture+resize here costs ~0.2s on the critical path.
        pre = getattr(self, "_prewarmed", None)
        if pre and (time.monotonic() - pre[0]) < 8.0:
            shots = pre[1]
        else:
            shots = capture_all_screens()
        if not shots or not api_key:
            await self._reply_local("I can't see your screen right now.")
            return
        shot = shots[0]
        tw, th = _pick_resolution(shot.width, shot.height)
        resized_b64 = base64.b64encode(
            _resize_jpeg(base64.b64decode(shot.base64_jpeg), tw, th)).decode("ascii")

        pw = shot.physical_width or shot.width
        ph = shot.physical_height or shot.height
        dscale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0

        def to_logical(cx, cy):
            cx = max(0.0, min(float(cx), tw)); cy = max(0.0, min(float(cy), th))
            vx = cx / tw * pw + shot.physical_left
            vy = cy / th * ph + shot.physical_top
            snapped = self._snap_to_uia(vx, vy)   # pixel-perfect if a small control's there
            if snapped is not None:
                vx, vy = snapped
            return int(round(vx / dscale)), int(round(vy / dscale))

        system = (
            "You are Clacky, a warm, upbeat guide giving the user a quick spoken "
            "TOUR of their screen — like a friend showing them around an app, or a "
            f"patient teacher. The attached screenshot is {tw}x{th} pixels, origin "
            "top-left. Pick the 3-4 most useful things on screen. For EACH one, "
            "write a line in EXACTLY this format:\n"
            "[POINT:x,y] One short, friendly sentence teaching what it is and what "
            "it's for.\n"
            "where x,y is the pixel coordinate of the CENTER of that element in the "
            "attached image, and the tag comes immediately BEFORE its sentence. "
            "Teach, don't just name (e.g. '[POINT:640,52] This is the search bar — "
            "type here to jump to anything'). You may open with ONE short greeting "
            "sentence (no tag) and close with ONE short warm line (no tag). Never "
            "say coordinates aloud, never say 'screenshot'. Talk for the ear: "
            "short, natural, encouraging — like you're genuinely happy to help."
        )
        body = {"model": "claude-sonnet-5", "max_tokens": 1024, "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": resized_b64}},
                    {"type": "text", "text": transcript}]}]}
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}

        self._cancel_flag = False
        self._emit_state(AppState.THINKING)
        held = False
        try:
            import json as _json
            http = self._get_http()   # shared warm pool — no per-tour TLS handshake

            async def _synth(t):
                try:
                    audio = await self._get_tts().synth(t)
                    if not audio:
                        slog("ERROR", f"segment synth EMPTY: {t[:40]!r}")
                    return audio
                except Exception as e:
                    slog("ERROR", f"segment synth failed ({e}): {t[:40]!r}")
                    return b""

            async def seg_stream():
                """Yield (coords, sentence) segments AS the model writes them —
                SSE streaming, so the greeting can speak while the rest of the
                tour is still generating. Falls back to the batch call only if
                the stream dies before yielding anything (a mid-stream failure
                after speech has started just ends the tour early — never replays)."""
                got_any = False
                try:
                    sbody = dict(body); sbody["stream"] = True
                    slog("THINK", "tour model call (streaming)...")
                    _t = time.perf_counter()
                    buf, cur = "", None
                    async with http.stream("POST", _API_URL, json=sbody,
                                           headers=headers) as r:
                        if r.status_code >= 400:
                            detail = (await r.aread())[:200]
                            raise RuntimeError(f"HTTP {r.status_code}: {detail}")
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                evt = _json.loads(line[5:].strip())
                            except Exception:
                                continue
                            if evt.get("type") == "content_block_delta":
                                d = evt.get("delta", {})
                                if d.get("type") != "text_delta":
                                    continue
                                buf += d.get("text", "")
                                # A segment completes when the NEXT tag appears.
                                while True:
                                    m = self._TOUR_TAG_RE.search(buf)
                                    if not m:
                                        break
                                    pre = buf[:m.start()].strip()
                                    if pre:
                                        if not got_any:
                                            slog("THINK", "...first segment at "
                                                 f"{time.perf_counter() - _t:.2f}s")
                                        got_any = True
                                        yield (cur, pre)
                                    cur = (int(m.group(1)), int(m.group(2)))
                                    buf = buf[m.end():]
                            elif evt.get("type") == "message_stop":
                                break
                    # Trailing segment (strip any dangling partial tag).
                    tail = re.sub(r"\[POINT:[^\]]*$", "", buf).strip()
                    if tail:
                        got_any = True
                        yield (cur, tail)
                    slog("THINK", f"...stream done in {time.perf_counter() - _t:.2f}s")
                    if got_any:
                        return
                except Exception as e:
                    slog("ERROR", f"tour stream failed: {e}")
                    self._reset_clients()
                    if got_any:
                        return            # spoke some of it — end early, don't replay
                # Batch fallback — one plain call, then parse (the pre-stream path).
                slog("THINK", "tour model call (batch fallback)...")
                try:
                    r = await self._get_http().post(_API_URL, json=body,
                                                    headers=headers)
                    if r.status_code >= 400:
                        slog("ERROR", f"tour HTTP {r.status_code}: {r.text[:200]}")
                        return
                    text = " ".join(b.get("text", "") for b in
                                    r.json().get("content", [])
                                    if b.get("type") == "text")
                    for seg in self._parse_tour_segments(text):
                        yield seg
                except Exception as e:
                    slog("ERROR", f"tour batch fallback failed: {e}")
                    self._reset_clients()

            # Producer: pull segments off the stream, start each synth IMMEDIATELY.
            seg_q: asyncio.Queue = asyncio.Queue()

            async def _pump():
                try:
                    async for coords, seg_text in seg_stream():
                        seg_q.put_nowait(
                            (coords, seg_text,
                             asyncio.create_task(_synth(seg_text))))
                finally:
                    seg_q.put_nowait(None)
            pump_task = asyncio.create_task(_pump())

            # Consumer: play in order, gapless; each point fires exactly as its
            # sentence's audio starts. Later segments synth while earlier ones play.
            from audio.playback import play_mp3_async
            spoken = []
            while True:
                item = await seg_q.get()
                if item is None:
                    break
                coords, seg_text, synth_task = item
                if self._cancel_flag:
                    synth_task.cancel()
                    continue
                audio = await synth_task
                if not spoken:
                    self._emit_state(AppState.SPEAKING)
                if coords is not None:
                    lx, ly = to_logical(*coords)
                    label = self._bubble_label(seg_text)
                    slog("POINT", f"cursor -> ({lx},{ly}) '{label}'")
                    if not held:
                        self.sig_point_hold.emit(True)
                        held = True
                    self.sig_point_at.emit(float(lx), float(ly), label)
                self.sig_response_chunk.emit(seg_text + " ")
                slog("TTS", f"speaking: {seg_text[:40]!r}")
                spoken.append(seg_text)
                if audio:
                    await play_mp3_async(audio)
                elif seg_text:
                    try:
                        await self._get_tts().speak(seg_text)   # synth-miss fallback
                    except Exception as e:
                        slog("ERROR", f"segment SKIPPED (no audio): {seg_text[:40]!r} ({e})")
            await pump_task
            if not spoken:
                await self._reply_local("I couldn't read the screen just now — "
                                        "mind asking again?")
                return
            clean = " ".join(spoken)
            self.sig_response_done.emit(clean)
            self._last_response = clean   # tag-free, for "say it again"
        except Exception as e:
            import traceback
            print("[clacky-debug] narration error:", flush=True)
            traceback.print_exc()
            self.sig_error.emit(str(e))
        finally:
            self.sig_point_release.emit()
            self._emit_state(AppState.IDLE)

    def _get_actuator(self):
        if getattr(self, "_actuator", None) is None:
            from clacky.agent.actuation import WindowsActuator
            self._actuator = WindowsActuator()
        return self._actuator

    async def _run_click(self, target: str):
        """Phase 2 actuation: locate `target`, point at it, and click it directly.
        Only a genuinely irreversible, high-stakes target (Send / Delete / Buy /
        Post / …) pauses to confirm via _resolve_pending; routine clicks just run."""
        shots = capture_all_screens()
        if not shots or not cfg.anthropic_api_key:
            await self._reply_local("I can't see your screen right now.")
            return
        shot = shots[0]
        self._emit_state(AppState.THINKING)
        d = None
        try:
            from ai.element_locator import detect_element
            d = await detect_element(
                screenshot_jpeg_b64=shot.base64_jpeg,
                original_width=shot.width, original_height=shot.height,
                physical_width=shot.physical_width, physical_height=shot.physical_height,
                physical_left=shot.physical_left, physical_top=shot.physical_top,
                dpi_scale=shot.dpi_scale, screen_index=shot.index,
                user_question=f"the {target}",
            )
        except Exception:
            d = None
        if d is None:
            await self._reply_local(f"I don't see {target} on your screen.")
            return
        # Point the overlay at it (logical coords, the verified-accurate space).
        self.sig_point_hold.emit(True)
        self.sig_point_at.emit(float(d.x), float(d.y), target)
        # Physical coords for the real click (SetCursorPos space ≈ logical * DPI).
        scale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0
        px, py = int(d.x * scale), int(d.y * scale)
        from clacky.agent.permission import Action, classify_action, needs_confirm
        risk = classify_action(Action(kind="left_click", target_label=target))
        # Routine clicks just happen (like Clicky). Only genuinely irreversible,
        # high-stakes targets (Send / Delete / Buy / Post / …) still pause to
        # confirm — the one boundary even Clicky's own soul keeps.
        if needs_confirm(risk):
            self._pending_action = {"label": target, "px": px, "py": py}
            await self._reply_local(
                f"{target} looks irreversible — say yes to click it, or anything else to skip.")
            return
        try:
            self._get_actuator().left_click(px, py)
            await self._reply_local(f"Done — clicked {target}.")
        except Exception as e:
            print("[clacky-debug] click error:", flush=True)
            import traceback
            traceback.print_exc()
            self.sig_error.emit(str(e))
            await self._reply_local("I couldn't do that click.")
        finally:
            self.sig_point_release.emit()

    async def _resolve_pending(self, transcript: str):
        """Second half of an actuation: the utterance after a proposed click is
        the yes/no. Only an explicit affirmative executes it; anything else cancels."""
        pending = getattr(self, "_pending_action", None)
        self._pending_action = None
        self.sig_point_release.emit()
        if not pending:
            return
        if not re.search(r"\b(yes|yea|yeah|yep|yup|sure|do it|do that|go ahead|"
                         r"confirm|okay|ok|correct|please)\b", transcript, re.I):
            await self._reply_local("Okay, cancelled.")
            return
        try:
            self._get_actuator().left_click(pending["px"], pending["py"])
            await self._reply_local(f"Done — clicked {pending['label']}.")
        except Exception as e:
            print("[clacky-debug] click error:", flush=True)
            import traceback
            traceback.print_exc()
            self.sig_error.emit(str(e))
            await self._reply_local("I couldn't do that click.")

    def _tidy_desktop_icons(self):
        """Sort desktop icons by name — the finishing touch after a sweep (folders
        first, then shortcuts, alphabetical). Primary: Explorer's own view API
        (IShellFolderViewDual.SortColumns via COM — verified working). Fallback:
        the legacy WM_COMMAND menu post. Pure view-state: no files touched."""
        try:
            import comtypes
            try:
                comtypes.CoInitialize()      # brain thread needs its own COM init
            except Exception:
                pass
            import comtypes.client
            from comtypes.automation import VARIANT
            shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
            # SWC_DESKTOP=8, SWFO_NEEDDISPATCH=1
            disp = shell.Windows().FindWindowSW(VARIANT(), VARIANT(), 8, 0, 1)
            disp.Document.SortColumns = "prop:System.ItemNameDisplay;"
            slog("ACT", "desktop icons sorted by name (COM)")
            return
        except Exception as e:
            slog("ERROR", f"COM icon sort failed ({e}) — trying legacy post")
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            WM_COMMAND, SORT_BY_NAME = 0x0111, 0x7021
            # The live icon view can sit under Progman OR a WorkerW (Win11 often
            # keeps a STALE empty view under Progman) — find them ALL and post the
            # sort to each; the dead ones ignore it, the real one sorts.
            found = []
            prog = u.FindWindowW("Progman", None)
            h = u.FindWindowExW(prog, 0, "SHELLDLL_DefView", None)
            if h:
                found.append(h)

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def _enum(hwnd, _l):
                dv = u.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
                if dv and dv not in found:
                    found.append(dv)
                return True
            u.EnumWindows(_enum, 0)

            if found:
                for dv in found:
                    u.PostMessageW(dv, WM_COMMAND, SORT_BY_NAME, 0)
                slog("ACT", f"icon sort posted to {len(found)} desktop view(s)")
            else:
                slog("ERROR", "desktop view window not found (no icon sort)")
        except Exception as e:
            slog("ERROR", f"icon sort failed: {e}")

    async def _run_organize_voice(self, transcript: str):
        """Voice-wired folder organizer — the 'hands + undo' signature skill.
        Runs the existing journaled organize engine (home-folder-only, move-only,
        fully reversible), then speaks a short summary. 'Undo' puts it all back."""
        s = transcript.lower()
        from pathlib import Path as _P
        root, which = _P.home() / "Desktop", "desktop"
        if "download" in s:
            root, which = _P.home() / "Downloads", "downloads folder"
        elif "document" in s:
            root, which = _P.home() / "Documents", "documents folder"
        slog("ACT", f"organize {root}")
        try:
            from clacky import config as _cli_cfg
            from clacky.providers import get_provider
            from clacky.agent.runtime import run_organize
            try:
                prov_name = _cli_cfg.active_provider()
                model = None
                if prov_name == "claude":
                    # Grouping files doesn't need the big model — Haiku plans a
                    # 25-file desktop in a few seconds vs ~20s+ on Sonnet.
                    model = os.environ.get("CLACKY_ORGANIZE_MODEL",
                                           "claude-haiku-4-5-20251001")
                provider = get_provider(prov_name, model=model)
            except Exception:
                provider = get_provider("heuristic")   # zero-config fallback
            # Stagger the moves so icons visibly vanish one by one — reads as
            # live work on camera instead of a jump cut. CLACKY_MOVE_STAGGER=0
            # to disable.
            stagger = float(os.environ.get("CLACKY_MOVE_STAGGER", "0.12") or 0)
            try:
                sess, plan = await asyncio.to_thread(run_organize, root,
                                                     provider, False, stagger)
            except Exception as e:
                # LLM plan failed (bad JSON / network / timeout) → the heuristic
                # sorter still tidies by type, so the command never just dies.
                slog("ERROR", f"organize via {provider.name} failed: {e} "
                     "— retrying with heuristic")
                provider = get_provider("heuristic")
                sess, plan = await asyncio.to_thread(run_organize, root,
                                                     provider, False, stagger)
        except Exception as e:
            slog("ERROR", f"organize failed: {e}")
            await self._reply_local("Hmm, I couldn't tidy that up just now.")
            return
        n = len(sess.batch.records) if getattr(sess, "batch", None) else 0
        slog("ACT", f"organize done: {n} file(s) moved")
        if not n:
            await self._reply_local(
                f"Your {which} already looks tidy — nothing to move.")
            return
        tidy = ""
        if which == "desktop":
            self._tidy_desktop_icons()   # finishing touch: sort what's left by name
            tidy = ", and tidied up what's left"
        folders = sorted({m.dest_folder for m in plan.moves}) if plan.moves else []
        into = ""
        if folders:
            into = " into " + ", ".join(folders[:3]) + \
                   (" and more" if len(folders) > 3 else "")
        await self._reply_local(
            f"Done — I moved {n} file{'s' if n != 1 else ''}{into}{tidy}. "
            "Say 'undo' if you want everything back.")

    def _fast_route(self, transcript: str):
        """Instant LOCAL routing for unambiguous cases — skips the Haiku hop.
        Deliberately conservative: returns None (defer to the model) unless it's
        clearly an on-screen action or a clearly factual question. Everything subtle
        — memory, skills, background, walkthrough, 'can you…' phrasings, screen
        references — still routes through the model, so we keep its robustness."""
        s = (transcript or "").strip().lower()
        if not s:
            return None
        # Explicit "do it" trigger (Clicky's "…agent" pattern): a lead word that
        # FORCES the act path with no guessing — for control, and for reliable
        # demo takes. Checked BEFORE the length cap so long commands still trigger.
        if re.match(r"(clacky[\s,]+)?"
                    r"(go(?!\s+(over|through|back))|agent|do it|just do it|"
                    r"go ahead|take over|handle (it|this)|make it happen)\b", s):
            return "act"
        # Folder cleanup → the journaled organizer (instant route). Requires BOTH a
        # cleanup verb and a folder word so "clean up my timeline" stays with chat.
        if (not re.match(r"(how|what|where|why)\b", s)
                and re.search(r"\b(clean|tidy|organi[sz]e|sort)\b", s)
                and re.search(r"\b(desktop|downloads?|documents)\b", s)):
            return "organize"
        if len(s) > 120:
            return None
        # "walk me through" ANYWHERE in the sentence is an unambiguous tour ask
        # ("I have no idea how to use FL Studio, walk me through making a beat").
        if re.search(r"\bwalk me through\b", s) and not re.match(
                r"(how|what|where|why)\b", s):
            return "walkthrough"
        # Clear screen TOUR → walkthrough, instantly (skips the ~2s Haiku hop for
        # the most common demo phrasings). Kept unambiguous — "explain quantum
        # physics" won't match, only screen/app-referential tours.
        if re.match(r"(walk me through|give me a (tour|walk|while)[- ]?(through)?|"
                    r"walkthrough|show me around|"
                    r"what can i do (here|on|with)|what'?s on (my|the) screen|"
                    r"what am i (looking at|seeing)|"
                    r"explain (this|my|the) (screen|app|page|panel|window|interface|"
                    r"program|tool)|"
                    r"explain how (this|the) (app|program|screen) works)\b", s):
            return "walkthrough"
        # Clear on-screen action: an imperative action verb STARTS the sentence.
        if re.match(r"(open|launch|click|double.?click|right.?click|press|type\b|"
                    r"scroll|close|maximi[sz]e|minimi[sz]e)\b", s):
            return "act"
        # Clear factual question: interrogative, no action verb, no screen reference.
        if re.match(r"(what is|what's|whats|who is|who's|when (is|was|did)|"
                    r"why (is|does|do)|how (do|does|much|many|old)|define|"
                    r"explain what)\b", s) and not re.search(
                    r"\b(screen|here|this (button|icon|menu|page|window)|point|"
                    r"show me|remember|forget)\b", s):
            return "chat"
        return None

    async def _route(self, transcript: str) -> dict:
        """Decide the route WITH THE MODEL, not regex — the OpenClicky way. One
        fast Haiku call returns a structured decision: the lane, plus any fact to
        remember/forget or skill to learn. Falls back to {'route': 'chat'}."""
        import anthropic
        try:
            client = self._get_anthropic()
            sys_prompt = (
                    "Route requests for Clacky, a Windows voice assistant that can "
                    "see the screen, talk, point, control the computer, and remember "
                    "things across sessions. Pick ONE route via the `route` tool:\n"
                    "- act: DO something on the computer — open an app, click, type, "
                    "search, navigate, run a learned routine, any hands-on task.\n"
                    "- walkthrough: a warm spoken TOUR of the whole screen, pointing "
                    "out several things — 'walk me through this', 'give me a tour', "
                    "'explain my screen', 'what's on my screen', 'show me around', "
                    "'what can I do here', 'explain this app'.\n"
                    "- remember: the user is giving a fact to keep for later "
                    "('remember that...', 'my name is...', 'I always...'). Put the "
                    "distilled fact (concise, third-person) in `fact`.\n"
                    "- forget: drop something remembered — put what to forget in "
                    "`fact` (use 'everything' to wipe all).\n"
                    "- learn_skill: the user is teaching a reusable routine ('save "
                    "this as my morning routine', 'whenever I say X, do Y'). Put a "
                    "short `skill_name` and the `skill_steps`.\n"
                    "- background: the user delegates a task to run in the background "
                    "and report back later — research something, look up or find out "
                    "X, dig into a question. NOT for things they want answered right "
                    "now (chat) or done on-screen now (act).\n"
                    "- organize: tidy/clean up a FOLDER of files (desktop, downloads, "
                    "documents) — moving files into folders. NOT for on-screen UI "
                    "tasks or app windows.\n"
                    "- chat: a specific question or ONE thing — 'what does this "
                    "button do', 'where's the X', 'what is this [single element]', "
                    "or any question not about touring the whole screen. Anything "
                    "that is NOT one of the above.")
            workspace_on = False
            try:
                import google_workspace as _gw
                workspace_on = _gw.is_configured()
            except Exception:
                workspace_on = False
            if workspace_on:
                sys_prompt += ("\n- workspace: read or send Gmail, or read Google "
                               "Calendar — the user has connected their Google "
                               "account, so prefer this for email/calendar.")
            if self._memory.skills:
                sys_prompt += ("\nKNOWN ROUTINES the user can run — route a request to "
                               "run any of these to 'act': "
                               + ", ".join(f'"{n}"' for n in self._memory.skills) + ".")
            route_enum = ["act", "walkthrough", "remember", "forget",
                          "learn_skill", "background", "organize", "chat"]
            if workspace_on:
                route_enum.insert(0, "workspace")
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=256,
                system=sys_prompt,
                tools=[{"name": "route", "description": "Choose the route.",
                        "input_schema": {"type": "object", "properties": {
                            "route": {"type": "string", "enum": route_enum},
                            "fact": {"type": "string"},
                            "skill_name": {"type": "string"},
                            "skill_steps": {"type": "string"}},
                            "required": ["route"], "additionalProperties": False}}],
                tool_choice={"type": "tool", "name": "route"},
                messages=[{"role": "user", "content": transcript}],
            )
            for b in resp.content:
                if b.type == "tool_use":
                    return b.input or {"route": "chat"}
        except Exception as e:
            print("[clacky-debug] route error:", e, flush=True)
            self._reset_clients()   # stale pool (e.g. after sleep) → rebuild next turn
        return {"route": "chat"}

    def _call_workspace(self, name: str, inp: dict):
        """Dispatch one Workspace tool to the Google API (blocking — run in a
        thread). Returns JSON-able data the model then summarizes for the ear."""
        import google_workspace as gw
        if name == "gmail_list":
            return gw.gmail_list(inp.get("query", "is:unread"), int(inp.get("count", 5)))
        if name == "gmail_send":
            return gw.gmail_send(inp.get("to", ""), inp.get("subject", ""),
                                 inp.get("body", ""))
        if name == "calendar_events":
            return gw.calendar_events(int(inp.get("count", 10)), int(inp.get("days", 1)))
        return {"error": f"unknown tool {name}"}

    async def _run_workspace(self, instruction: str):
        """Opt-in reliable path: a small tool-use loop over the Gmail/Calendar API
        (no screen, no clicking), then ONE short spoken summary. Separate from the
        computer-use loop so structured API data never mixes with screenshots."""
        import json
        import anthropic
        WS_TOOLS = [
            {"name": "gmail_list",
             "description": "List Gmail messages matching a search query (default "
                            "'is:unread'). Returns from/subject/snippet.",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string"}, "count": {"type": "integer"}},
                 "additionalProperties": False}},
            {"name": "gmail_send",
             "description": "Send an email. Use ONLY when the user clearly asked to "
                            "send one; state the recipient in your spoken reply.",
             "input_schema": {"type": "object", "properties": {
                 "to": {"type": "string"}, "subject": {"type": "string"},
                 "body": {"type": "string"}},
                 "required": ["to", "subject", "body"], "additionalProperties": False}},
            {"name": "calendar_events",
             "description": "List upcoming Google Calendar events. days=1 for today.",
             "input_schema": {"type": "object", "properties": {
                 "count": {"type": "integer"}, "days": {"type": "integer"}},
                 "additionalProperties": False}},
        ]
        system = (
            "You are Clacky, handling the user's Gmail and Google Calendar through "
            "these tools. Use them to get what you need, then reply in ONE short, "
            "natural spoken line for the ear — summarize, never read raw data or "
            "email addresses aloud. Only send an email when the user clearly asked "
            "to, and name the recipient in your reply. Keep it warm and brief.")
        client = self._get_anthropic()
        messages = [{"role": "user", "content": instruction}]
        self._emit_state(AppState.THINKING)
        try:
            for _ in range(6):
                resp = await client.messages.create(
                    model="claude-sonnet-5", max_tokens=1024,
                    system=system, tools=WS_TOOLS, messages=messages)
                messages.append({"role": "assistant",
                                 "content": [b.model_dump() for b in resp.content]})
                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                text = " ".join(b.text for b in resp.content if b.type == "text")
                if not tool_uses:
                    await self._reply_local(text or "Done.")
                    return
                results = []
                for tu in tool_uses:
                    try:
                        data = await asyncio.to_thread(
                            self._call_workspace, tu.name, tu.input or {})
                        content = json.dumps(data)[:4000]
                    except Exception as e:
                        content = f"error: {e}"
                    results.append({"type": "tool_result",
                                    "tool_use_id": tu.id, "content": content})
                messages.append({"role": "user", "content": results})
        except Exception as e:
            print("[clacky-debug] workspace error:", e, flush=True)
            await self._reply_local("I couldn't reach your Google account just now.")

    async def _spawn_background(self, description: str):
        """Fire-and-forget: kick off a background agent that works the task and
        reports back, so the foreground stays free for you to keep talking. This is
        for NON-screen work (research, lookups) — a background agent can't drive the
        live mouse without fighting you, so screen tasks stay in the foreground."""
        self._bg_counter += 1
        tid = self._bg_counter
        self._bg[tid] = {"desc": description.strip(), "status": "running",
                         "result": None}
        await self._reply_local("On it — I'll look into that and report back.")
        self._bg[tid]["task"] = asyncio.create_task(self._bg_worker(tid, description))

    async def _bg_worker(self, tid: int, description: str):
        try:
            result = await self._research_agent(description)
            self._bg[tid].update(status="done", result=result)
        except Exception as e:
            print("[clacky-debug] bg worker error:", e, flush=True)
            self._bg[tid].update(status="error")
            result = "I ran into a problem looking into that."
        await self._bg_report(tid, result)

    async def _research_agent(self, description: str) -> str:
        """Background research via Anthropic's web_search; falls back to a
        knowledge-only answer if search is unavailable. Returns a short spoken line."""
        import anthropic
        client = self._get_anthropic()
        system = ("You are a research aide for Clacky. Investigate the request (use "
                  "web search when it helps), then answer in a concise, spoken-style "
                  "summary — 2 to 4 sentences for the ear, lead with the answer, no "
                  "URLs, citations, or markdown.")
        for tools in ([{"type": "web_search_20250305", "name": "web_search",
                        "max_uses": 5}], []):
            try:
                messages = [{"role": "user", "content": description}]
                for _ in range(4):
                    resp = await client.messages.create(
                        model="claude-sonnet-5", max_tokens=1024,
                        system=system, tools=tools, messages=messages)
                    if resp.stop_reason == "pause_turn":
                        messages.append({"role": "assistant",
                            "content": [b.model_dump() for b in resp.content]})
                        continue
                    text = " ".join(b.text for b in resp.content
                                    if b.type == "text").strip()
                    if text:
                        return text
                    break
            except Exception as e:
                print(f"[clacky-debug] research (search={bool(tools)}) error: {e}",
                      flush=True)
        return "I couldn't dig up a solid answer on that."

    async def _bg_report(self, tid: int, result: str):
        """Report back during a lull, so we never talk over an active turn."""
        for _ in range(180):                       # wait up to ~3 min for idle
            if self._state == AppState.IDLE:
                break
            await asyncio.sleep(1.0)
        await self._reply_local(f"Quick update — {result}")

    def _bg_block(self) -> str:
        """Prompt fragment so the foreground can answer 'what are you working on?'"""
        running = [t["desc"] for t in self._bg.values()
                   if t.get("status") == "running"]
        if not running:
            return ""
        return ("\n\nBACKGROUND TASKS you're working on right now (mention if asked): "
                + "; ".join(running))

    def _action_label(self, text, inp):
        """Short pointer-bubble tag during a task — prefer the model's own terse
        note, else derive one from the action. Kept tight for the little pill."""
        t = " ".join((text or "").split()[:5]).strip(" .!?")
        if t:
            return t[:36]
        a = (inp.get("action") or "").lower()
        if a == "type":
            s = (inp.get("text") or "").strip()
            return f"typing “{s[:16]}”" if s else "typing"
        if a == "key":
            return f"pressing {inp.get('text', '')}".strip()
        if a == "scroll":
            return "scrolling"
        if "double" in a:
            return "double-click"
        if "right" in a:
            return "right-click"
        if "click" in a:
            return "clicking"
        return ""

    def _exec_action(self, actuator, action, px, py, text):
        """Run one computer-tool action on the real machine (physical coords)."""
        a = (action or "").lower()
        slog("ACT", f"{a} @ ({px},{py})" + (f" text={text[:30]!r}" if text else ""))
        if a in ("left_click", "click") and px is not None:
            actuator.left_click(px, py)
        elif a == "double_click" and px is not None:
            actuator.double_click(px, py)
        elif a == "right_click" and px is not None:
            actuator.right_click(px, py)
        elif a in ("mouse_move", "cursor_move") and px is not None:
            actuator.move(px, py)
        elif a in ("left_click_drag",) and px is not None:
            actuator.left_click(px, py)          # simplified: click destination
        elif a == "type" and text:
            actuator.type_text(text)
        elif a == "key" and text:
            actuator.key(text)
        elif a == "scroll" and px is not None:
            actuator.scroll(px, py, -3)
        # screenshot / cursor_position / wait: no-op — the loop re-captures anyway.

    # Apps `start <name>` can't resolve (no App Paths entry). Each maps to a list
    # of candidates tried in order: absolute exe paths (if they exist) and URL
    # protocols. First hit wins; unknown names fall through to plain `start`.
    _APP_LAUNCHERS = {
        "steam": [r"C:\Program Files (x86)\Steam\steam.exe", "steam://open/main"],
        "discord": [r"%LOCALAPPDATA%\Discord\Update.exe|--processStart|Discord.exe",
                    "discord://"],
        "spotify": [r"%APPDATA%\Spotify\Spotify.exe", "spotify:"],
        "epic games": ["com.epicgames.launcher://apps"],
        "epic": ["com.epicgames.launcher://apps"],
        "telegram": [r"%APPDATA%\Telegram Desktop\Telegram.exe", "tg://"],
        "slack": [r"%LOCALAPPDATA%\Microsoft\WindowsApps\Slack.exe",  # Store install
                  r"%LOCALAPPDATA%\slack\slack.exe",
                  r"C:\Program Files\Slack\slack.exe"],
        "notion": [r"%LOCALAPPDATA%\Programs\Notion\Notion.exe", "notion://"],
    }

    def _launch_app(self, name: str):
        """Open a Windows app directly (fast, reliable) instead of driving the GUI.
        Known tricky apps (Steam etc.) launch via exe path / URL protocol; others
        via `start <name>` (App Paths registry + PATH)."""
        name = (name or "").strip()
        if not name:
            return
        import os
        import subprocess
        for cand in self._APP_LAUNCHERS.get(name.lower(), []):
            if "://" in cand or cand.endswith(":"):
                try:
                    os.startfile(cand)                   # URL protocol
                    slog("ACT", f"launched '{name}' via {cand}")
                    return
                except Exception:
                    continue
            parts = [os.path.expandvars(p) for p in cand.split("|")]
            if os.path.isfile(parts[0]):
                try:
                    subprocess.Popen(parts)
                    slog("ACT", f"launched '{name}' via {parts[0]}")
                    return
                except Exception:
                    continue
        # No local install found but it's a known web app → open the logged-in
        # web version instead of letting `start` fail with a "can't find" dialog.
        if name.lower() in self._WEB_APPS:
            slog("ACT", f"'{name}' not installed locally -> web app")
            self._open_url(name)
            return
        try:
            subprocess.Popen(["cmd", "/c", "start", "", name],
                             creationflags=0x08000000)   # CREATE_NO_WINDOW
        except Exception:
            try:
                os.startfile(name)                       # fallback
            except Exception as e:
                print("[clacky-debug] launch error:", e, flush=True)

    # Common web apps → their URL, so "check my email" opens the real logged-in
    # web app in the user's browser (no OAuth, no API — Clacky reads/acts with its
    # hands). This is our take on OpenClicky's Workspace integration.
    _WEB_APPS = {
        "gmail": "https://mail.google.com", "email": "https://mail.google.com",
        "google calendar": "https://calendar.google.com",
        "calendar": "https://calendar.google.com",
        "google drive": "https://drive.google.com", "drive": "https://drive.google.com",
        "google docs": "https://docs.google.com", "docs": "https://docs.google.com",
        "google sheets": "https://sheets.google.com", "sheets": "https://sheets.google.com",
        "youtube": "https://youtube.com", "maps": "https://maps.google.com",
        "gemini": "https://gemini.google.com", "chatgpt": "https://chatgpt.com",
        "slack": "https://app.slack.com/client", "notion": "https://notion.so",
    }

    def _open_url(self, target: str):
        """Open a URL or a known web app (gmail, calendar, …) in the default
        browser — the user's logged-in session. `start` routes URLs to the browser."""
        t = (target or "").strip()
        if not t:
            return
        t = self._WEB_APPS.get(t.lower(), t)
        if not re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
            t = "https://" + t
        self._launch_app(t)

    async def _run_task(self, instruction: str):
        """Phase 2: the general computer-use agent. Loops screenshot → Claude picks
        an action → we run it on the real machine → fresh screenshot → repeat, until
        the task is done. This is 'proper computer use' — it can open apps and drive
        them. Safety is prompt-level (won't send/delete/buy/post unless the task
        requires it, and stops to ask first) plus a step cap and Esc-to-stop.
        HIGH RISK + untested end-to-end — try safe tasks first (open Notepad, type)."""
        import base64
        import httpx
        from ai.element_locator import (_pick_resolution, _resize_jpeg,
                                         _API_URL, _BETA_HEADER)

        api_key = cfg.anthropic_api_key
        if not api_key:
            await self._reply_local("I need an Anthropic key for that.")
            return
        try:
            actuator = self._get_actuator()
        except Exception as e:
            self.sig_error.emit(str(e))
            await self._reply_local("I can't drive the mouse and keyboard right now.")
            return
        shots = capture_all_screens()
        if not shots:
            await self._reply_local("I can't see your screen right now.")
            return
        tw, th = _pick_resolution(shots[0].width, shots[0].height)

        def grab():
            s = capture_all_screens()
            if not s:
                return None, None
            shot = s[0]
            b64 = base64.b64encode(
                _resize_jpeg(base64.b64decode(shot.base64_jpeg), tw, th)).decode("ascii")
            return shot, b64

        def to_coords(shot, cx, cy):
            cx = max(0.0, min(float(cx), tw)); cy = max(0.0, min(float(cy), th))
            pw = shot.physical_width or shot.width
            ph = shot.physical_height or shot.height
            vx = cx / tw * pw + shot.physical_left
            vy = cy / th * ph + shot.physical_top
            scale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0
            return int(round(vx / scale)), int(round(vy / scale)), int(round(vx)), int(round(vy))

        async def say(text):
            text = (text or "").strip()
            if not text:
                return
            # Cap the spoken closing at WHOLE-SENTENCE boundaries only (max two
            # sentences; drop to one if those run long) — never chop mid-sentence.
            # Panel text = spoken text, so what you read is what she says.
            ends = list(re.finditer(r"[.!?](?=\s|$)", text))
            spoken = text
            if len(ends) >= 2:
                spoken = text[:ends[1].end()].strip()
            if len(spoken) > 200 and ends:
                spoken = text[:ends[0].end()].strip()
            if spoken != text:
                slog("SAY", f"closing capped {len(text)}->{len(spoken)} chars")
            self.sig_response_chunk.emit(spoken + " ")
            self._emit_state(AppState.SPEAKING)
            try:
                await self._get_tts().speak(spoken)
            except Exception:
                slog("ERROR", "closing TTS failed")

        system = (
            "You are Clacky, doing a task on the user's Windows screen with the "
            "computer tool. Work ONE action at a time: take an action, then you'll "
            "get a fresh screenshot of the result, then decide the next one. To open "
            "an app, use the launch_app tool — it opens it directly and reliably; do "
            "NOT drive the Start menu for that. For email, calendar, documents, or "
            "any website, use open_url (e.g. 'gmail', 'calendar', or a full URL) — it "
            "opens the user's logged-in web app in their browser; then read and act "
            "on it with the computer tool. "
            "With each tool call, include a VERY short text label (2-4 words) of "
            "what you're doing right then — e.g. 'clicking Save', 'opening the File "
            "menu', 'typing the address'. It shows as a tiny tag next to the cursor "
            "and is NOT read aloud, so keep it terse; never mention keystrokes, "
            "coordinates, or 'screenshot'. Do NOT speak (voice) during the steps. "
            "THE MOMENT the goal is visibly achieved, STOP — no extra clicks, no "
            "adjusting, no double-checking (e.g. once the video is playing, you are "
            "DONE; another click might pause it). "
            "Speak ONLY when finished (or when you stop to defer something): give "
            "ONE short sentence, under 15 words, about the outcome — e.g. 'Lofi's "
            "on — enjoy!' Never recap the steps you took. "
            "SAFETY: do NOT send, delete, buy, post, or pay for anything unless the "
            "task explicitly asks for it. If it does, do everything up to that step, "
            "then STOP with a closing line and NO tool call, saying what's left for "
            "the user to confirm."
        )
        mem, sk = self._memory.facts_block(), self._memory.skills_block()
        if mem or sk:
            system += "\n\n" + "\n\n".join(x for x in (mem, sk) if x)
        launch_tool = {
            "name": "launch_app",
            "description": ("Open a Windows app directly by name — fast and reliable, "
                            "no Start-menu driving. `name` is the app's common/exe "
                            "name, e.g. 'notepad', 'chrome', 'calc', 'explorer'."),
            "input_schema": {"type": "object",
                             "properties": {"name": {"type": "string"}},
                             "required": ["name"], "additionalProperties": False},
        }
        open_url_tool = {
            "name": "open_url",
            "description": ("Open a web page or web app in the user's default browser "
                            "(their logged-in session), then read/act on it with the "
                            "computer tool. `target` is a full URL or a known app name: "
                            "gmail, calendar, drive, docs, sheets, youtube, maps. Use "
                            "this for email, calendar, documents, and any website "
                            "instead of typing into the address bar."),
            "input_schema": {"type": "object",
                             "properties": {"target": {"type": "string"}},
                             "required": ["target"], "additionalProperties": False},
        }
        tools = [{"type": "computer_20251124", "name": "computer",
                  "display_width_px": tw, "display_height_px": th},
                 launch_tool, open_url_tool]
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                   "anthropic-beta": _BETA_HEADER, "content-type": "application/json"}
        shot, b64 = grab()
        messages = [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": instruction}]}]

        self._cancel_flag = False
        self._emit_state(AppState.THINKING)
        MAX_STEPS = 12
        try:
            import contextlib
            # Shared warm client (nullcontext → don't close the shared pool on exit)
            async with contextlib.nullcontext(self._get_http()) as http:
                for _ in range(MAX_STEPS):
                    if self._cancel_flag:
                        await say("Okay, stopping.")
                        break
                    body = {"model": "claude-sonnet-5", "max_tokens": 1024,
                            "system": system, "tools": tools, "messages": messages}
                    r = await http.post(_API_URL, json=body, headers=headers)
                    if r.status_code >= 400:
                        print(f"[clacky-debug] task HTTP {r.status_code}: "
                              f"{r.text[:300]}", flush=True)
                        break
                    content = r.json().get("content", [])
                    messages.append({"role": "assistant", "content": content})
                    text = " ".join(b.get("text", "") for b in content
                                    if b.get("type") == "text")
                    tool_uses = [b for b in content if b.get("type") == "tool_use"]
                    if not tool_uses:
                        await say(text)                # only the closing / defer line
                        break                          # task done / stopped to confirm

                    for tu in tool_uses:
                        inp = tu.get("input") or {}
                        if tu.get("name") == "launch_app":
                            self._launch_app(inp.get("name", ""))
                            continue
                        if tu.get("name") == "open_url":
                            self._open_url(inp.get("target", ""))
                            continue
                        coord = inp.get("coordinate")
                        px = py = None
                        if coord and len(coord) == 2:
                            lx, ly, px, py = to_coords(shot, coord[0], coord[1])
                            self.sig_point_at.emit(float(lx), float(ly),
                                                   self._action_label(text, inp))
                        try:
                            self._exec_action(actuator, inp.get("action"), px, py,
                                              inp.get("text"))
                        except Exception:
                            print("[clacky-debug] action error:", flush=True)
                            import traceback
                            traceback.print_exc()

                    # Web pages need longer to load than a local app window.
                    opened_url = any(t.get("name") == "open_url" for t in tool_uses)
                    await asyncio.sleep(1.8 if opened_url else 0.8)
                    shot, b64 = grab()
                    if shot is None:
                        break
                    result_img = {"type": "image", "source": {"type": "base64",
                                  "media_type": "image/jpeg", "data": b64}}
                    results = []
                    for tu in tool_uses:
                        blocks = []
                        if tu.get("name") == "launch_app":
                            blocks.append({"type": "text", "text":
                                f"Opened {(tu.get('input') or {}).get('name','')}."})
                        elif tu.get("name") == "open_url":
                            blocks.append({"type": "text", "text":
                                f"Opened {(tu.get('input') or {}).get('target','')} "
                                "in the browser."})
                        blocks.append(result_img)
                        results.append({"type": "tool_result",
                                        "tool_use_id": tu["id"], "content": blocks})
                    messages.append({"role": "user", "content": results})
        except Exception as e:
            import traceback
            print("[clacky-debug] task error:", flush=True)
            traceback.print_exc()
            self.sig_error.emit(str(e))
        finally:
            self.sig_point_release.emit()

    def _emit_state(self, state: AppState):
        self._state = state
        self.sig_state_changed.emit(state)

    # ── Settings ──────────────────────────────────────────────────────────────

    def set_model(self, model: str):
        self._current_model = model

    def set_active_provider(self, name: str):
        """Runtime switch between claude / openai / copilot / gemini / ollama."""
        cfg.set_active_llm(name)
        self._llm = None           # force re-init on next query
        self._current_model = None
        # If switching to Copilot and the cached model list is stale (or
        # missing), refresh it in the background so the panel shows the
        # *current* set of models GitHub offers — not stale hardcoded ones.
        if name == "copilot":
            try:
                from ai.github_copilot_provider import cache_is_stale
                if cache_is_stale():
                    self._submit(self._refresh_copilot_models())
            except Exception:
                pass
        elif name in ("claude", "openai", "gemini"):
            try:
                from ai.model_registry import cache_is_stale as _stale
                if _stale(name):
                    self._submit(self._refresh_one_model_list(name))
            except Exception:
                pass
        elif name == "ollama":
            # Surface installed models in the tray immediately
            self.refresh_ollama_models()

    async def _refresh_one_model_list(self, provider: str):
        try:
            from ai.model_registry import refresh
            ms = await refresh(provider)
            self.sig_models_refreshed.emit(provider, len(ms))
        except Exception as e:
            self.sig_error.emit(f"{provider} model refresh failed: {e}")

    def refresh_copilot_models(self):
        """Public — bound to the tray 'Refresh Copilot models' action."""
        self._submit(self._refresh_copilot_models())

    async def _refresh_copilot_models(self):
        try:
            from ai.github_copilot_provider import refresh_models_to_cache
            models = await refresh_models_to_cache()
            self.sig_copilot_models_done.emit(len(models))
        except Exception as e:
            self.sig_error.emit(f"Copilot model refresh failed: {e}")

    # ── Ollama model management ──────────────────────────────────────────────

    def refresh_ollama_models(self):
        """Public — kick off async poll of /api/tags. Result via sig_ollama_models."""
        self._submit(self._refresh_ollama_models())

    async def _refresh_ollama_models(self):
        try:
            from ai.ollama_provider import OllamaProvider
            classified = await OllamaProvider().list_models_classified()
            self.sig_ollama_models.emit(classified)
        except Exception as e:
            self.sig_error.emit(f"Ollama model list failed: {e}")

    def set_ollama_model(self, kind: str, name: str):
        """Tray callback — update the active vision/text model. No restart needed."""
        cfg.set_ollama_model(kind, name)
        # Force the provider instance to re-read cfg on next call
        if cfg.llm_provider() == "ollama":
            self._llm = None

    def pull_ollama_model(self, name: str):
        """Trigger `ollama pull <name>` in the background. Status via sig_ollama_pull_status."""
        self._submit(self._pull_ollama_model(name))

    async def _pull_ollama_model(self, name: str):
        from ai.ollama_models_registry import pull_model
        self.sig_ollama_pull_status.emit(name, f"Pulling {name}…")

        def _progress(msg: str):
            if msg:
                self.sig_ollama_pull_status.emit(name, msg)

        ok = await pull_model(name, cfg.ollama_host, on_progress=_progress)
        if ok:
            self.sig_ollama_pull_status.emit(name, f"✓ {name} ready")
            # Refresh the installed list so the tray menu picks it up
            await self._refresh_ollama_models()
        else:
            self.sig_ollama_pull_status.emit(name, f"✗ Pull failed for {name}")

    def set_web_search(self, enabled: bool):
        self._web_search_enabled = enabled

    def set_wake_word(self, enabled: bool):
        self._listener.set_wake_word_enabled(enabled)

    def set_slow_mode(self, enabled: bool):
        self._slow_mode = enabled

    def set_quiz_mode(self, enabled: bool):
        was = self._quiz_mode
        self._quiz_mode = enabled
        if enabled and not was:
            # Kick off the first question immediately so the user doesn't
            # have to ask "begin quiz". Uses the active screen as context.
            self._submit(self._kickoff_quiz())

    async def _kickoff_quiz(self):
        """Called when quiz mode flips ON — generates the first question
        without waiting for a user utterance."""
        if self._state != AppState.IDLE:
            return
        try:
            self._emit_state(AppState.THINKING)
            screenshots = capture_all_screens()
            images_b64 = [s.base64_jpeg for s in screenshots]
            title = active_window_title()
            system = _build_system_prompt(
                window_title=title, quiz_mode=True,
            )
            ak = app_key(title)
            history = self._app_memory.setdefault(ak, [])

            full = ""
            async for chunk in self._get_llm().stream_response(
                user_text="(quiz mode just enabled — start the quiz now)",
                screenshots_b64=images_b64,
                history=history,
                system_prompt=system,
                model=self._current_model,
            ):
                if self._cancel_flag:
                    break
                full += chunk
                self.sig_response_chunk.emit(chunk)
            self.sig_response_done.emit(full)
            self._emit_state(AppState.SPEAKING)
            try:
                await self._get_tts().speak(full)
            except Exception:
                pass
        except Exception as e:
            self.sig_error.emit(f"Quiz start failed: {e}")
        finally:
            self._emit_state(AppState.IDLE)

    def set_privacy_guard(self, enabled: bool):
        self._privacy_guard = enabled

    @property
    def slow_mode(self) -> bool:  return self._slow_mode
    @property
    def quiz_mode(self) -> bool:  return self._quiz_mode
    @property
    def privacy_guard(self) -> bool:  return self._privacy_guard

    def clear_history(self):
        self._history = []
        self._app_memory.clear()
        self._lesson_steps = []
        self._lesson_step_idx = 0

    # ── Attached documents (drag-drop on panel) ──────────────────────────────

    def attach_document(self, path: str) -> bool:
        text = pdf_context.extract_text(path)
        if not text.strip():
            return False
        from pathlib import Path
        self._attached_docs.append((Path(path).name, text))
        # Cap context — most recent 3 docs
        self._attached_docs = self._attached_docs[-3:]
        return True

    def clear_attachments(self):
        self._attached_docs = []

    # ── Lesson recording ─────────────────────────────────────────────────────

    def start_recording(self) -> Optional[str]:
        if self._recorder is None:
            self._recorder = lesson_recorder.LessonRecorder()
        out = self._recorder.start()
        if out:
            self.sig_recording_state.emit(True, str(out))
            return str(out)
        return None

    def stop_recording(self) -> Optional[str]:
        if not self._recorder or not self._recorder.is_recording:
            return None
        out = self._recorder.stop()
        self.sig_recording_state.emit(False, str(out) if out else "")
        return str(out) if out else None

    @property
    def is_recording(self) -> bool:
        return bool(self._recorder and self._recorder.is_recording)

    # ── Workflow capture (record clicks/keystrokes) ──────────────────────────

    def workflow_start(self) -> bool:
        if self._workflow is None:
            self._workflow = workflow_capture.WorkflowCapture()
        return self._workflow.start()

    def workflow_stop(self) -> str:
        if not self._workflow:
            return ""
        events = self._workflow.stop()
        return self._workflow.summarise() if events else ""

    # ── Live collaboration ───────────────────────────────────────────────────

    def collab_start_host(self):
        """Live-session host. Disabled — see tutor_features/collab.py."""
        self.sig_error.emit(
            "Live Session: not available in this build. "
            "Requires a WebRTC signalling server (planned for a future release)."
        )

    def collab_join(self, code: str):
        """Live-session join. Disabled — see tutor_features/collab.py."""
        self.sig_error.emit(
            "Live Session: not available in this build. "
            "Requires a WebRTC signalling server (planned for a future release)."
        )

    # ── Voice picker (ElevenLabs / Edge) ─────────────────────────────────────

    def set_tts_voice(self, voice: str):
        try:
            tts = self._get_tts()
            if hasattr(tts, "set_voice"):
                tts.set_voice(voice)
        except Exception:
            pass

    # ── Toggle setters for the rest of the new features ──────────────────────

    def set_code_mode_auto(self, enabled: bool):
        self._code_mode_auto = enabled

    def set_multilang(self, enabled: bool):
        self._multilang = enabled

    def set_journal(self, enabled: bool):
        self._journal_enabled = enabled

    def set_ocr_enabled(self, enabled: bool):
        self._ocr_enabled = enabled

    # ── Stop / cancel ─────────────────────────────────────────────────────────

    def stop(self):
        """Cancel the current LLM stream + any in-flight TTS. Bound to Esc."""
        self._cancel_flag = True
        # Kill audio playback immediately — flips the global stop event so
        # the chunked PortAudio loop bails out within ~50 ms.
        try:
            from audio.playback import stop_audio
            stop_audio()
        except Exception:
            pass
        # Some TTS providers also have their own cancel hook
        tts = self._tts
        if tts and hasattr(tts, "stop"):
            try:
                tts.stop()
            except Exception:
                pass
        # Clear any stored lesson so "stop" really means "back to zero"
        self._lesson_steps = []
        self._lesson_step_idx = 0
        self._emit_state(AppState.IDLE)
