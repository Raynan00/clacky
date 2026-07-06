# Base architecture — `Bitshank-2338/clicky-windows`

Reference notes on the companion shell Clacky lifts from (Python 3.11+ / PyQt6, MIT). Read this before M0/M1.5 — it marks the exact seam where the Claude Agent SDK replaces the base's one-shot LLM call.

> Source reviewed: `main.py`, `companion_manager.py` (the brain). Other modules (`ui/`, `audio/`, `ai/`, `screen/`, `tutor_features/`, `skills/`) referenced from imports.

---

## 1. Shape: a tray app with a signal-wired brain

`main.py` boots a PyQt6 app that **never opens a window** (`setQuitOnLastWindowClosed(False)`) and creates four decoupled objects:

| Object | Role |
|---|---|
| `CompanionManager` | the brain — state machine + async orchestration |
| `CompanionPanel` (`ui/panel.py`) | optional chat panel + model dropdown |
| `CursorOverlay` (`ui/overlay.py`) | the blue buddy, pointing flight, annotations |
| `TrayManager` (`ui/tray.py`) | system-tray icon + full menu |

They **never call each other directly** — everything is wired through Qt signals in `main.py`. The manager emits (`sig_point_at`, `sig_response_chunk`, `sig_state_changed`, ~20 signals); `main.py` connects each to a panel/overlay/tray slot. This decoupling is why the shell is easy to lift: swap the brain's internals without touching the UI.

Two global hotkeys bound at startup: `GlobalHotkeyMonitor` (push-to-talk press/release) and `StopHotkey` (Esc → cancel).

---

## 2. Threading model

- The brain runs an **asyncio event loop on a background thread** (`_run_loop`), so the Qt UI thread never blocks.
- Work is scheduled with `_submit(coro)` → `asyncio.run_coroutine_threadsafe`.
- Results marshal back to the UI thread **only** via `pyqtSignal`s (audio thread → `sig_audio_level`, etc.). Cross-thread UI calls never happen directly.
- A **sleep/wake watchdog** thread detects resume-from-sleep (heartbeat drift > 15s) and restarts the mic + loop.

State machine: `IDLE → LISTENING → THINKING → SPEAKING → IDLE`, broadcast via `sig_state_changed` (drives panel status, tray icon, and cursor mode).

---

## 3. The core loop — `CompanionManager._end_capture_and_process`

This is the whole product, and it is a **single-turn request→response**, not an agent:

1. **Stop recording → PCM**; ignore if < 0.1s of audio.
2. **STT** transcribe (`_get_stt().transcribe`).
3. **Short-circuits before any LLM call:**
   - voice commands: `is_stop` / `is_next` (lesson) / `is_repeat` / journal queries / quiz review — answered locally.
   - **`skills_pkg.match(transcript)`** → if a registered skill's regex matches, run its `handler(self, transcript)` and return. **← the base's extension point.**
4. **Screen capture** (`capture_all_screens`) — skipped if the active window is sensitive (Privacy Guard) or the query is an identity question.
5. **Parallel side-work** (`asyncio.create_task`): web search + element location.
   - Pointing is tiered: **hybrid pointer** (Windows UIA accessibility tree ≈5ms, pixel-perfect → OCR for canvas apps) → **Anthropic Computer Use** (if `ANTHROPIC_API_KEY`) → **universal grid locator** (any vision LLM, ~25–50px). Coordinate fires to the overlay *immediately* so the buddy flies over while the LLM still thinks.
6. **One streaming LLM call** (`_get_llm().stream_response`) with a big system prompt assembled by `_build_system_prompt` (window title, detected coordinate, code-mode, language, OCR text, attached docs, web results).
7. **Tag parsing while streaming:** regex extracts `[POINT:x,y:label:screenN]` and `[ARROW]/[CIRCLE]/[UNDERLINE]/[LABEL]` from the text, fires overlay signals, and **buffers partial tags** (`ANY_PARTIAL_RE`) so a half-written tag never leaks into the panel.
8. **Per-app memory:** history keyed by window title (`_app_memory[app_key]`), capped at 20 messages.
9. **TTS** speaks the cleaned text; the point is **held** during speech, **released** after (`sig_point_hold` / `sig_point_release`).

Cancellation: Esc sets `_cancel_flag` (breaks the stream loop) and calls `stop_audio()`.

---

## 4. The POINT tag protocol (how "see + point" works)

Provider-agnostic and text-based: the LLM is instructed to emit `[POINT:x,y:label:screen1]` inline. `_parse_points` regex-matches it and emits `sig_point_at` → `CursorOverlay.point_at(x, y, label)` flies the cursor along a bezier arc and dwells. Same mechanism for the four annotation tags. No structured tool-calling — just tags in free text.

---

## 5. Providers (all lazy, runtime-swappable)

| Kind | Options | Selector |
|---|---|---|
| LLM | claude / openai / gemini / copilot / ollama | `_get_llm()` reads `cfg.llm_provider()` |
| STT | deepgram / openai / whisper.cpp / faster-whisper | `_get_stt()` |
| TTS | elevenlabs / openai / edge | `_get_tts()` |

Switching provider nulls the cached instance so it re-inits on next call. Ollama is auto-started (`_ensure_ollama_running`) if selected.

---

## 6. ⭐ The seam where Clacky changes it

**The base is a one-shot pipeline: one utterance → one screenshot → one LLM reply → speak.** No agent loop, no tool-use cycle, no background autonomy. The "skills" are regex→handler one-shots.

Clacky becomes agentic with a **surgical swap at step 6**, keeping everything else:

```
 KEEP (lift as-is):                          REPLACE:
   steps 1–2  capture + STT                    step 6  single stream_response()
   step  4    screen capture                     │
   step  5    pointing engine + overlay          ▼
   step  7    tag parsing → overlay         ┌──────────────────────────────┐
   step  9    TTS + state machine           │ Claude Agent SDK session      │
   tray, hotkeys, providers, privacy guard, │  • loops + calls tools        │
   sleep watchdog, signal architecture      │  • tools constrained to       │
                                            │    Clacky safe_fs (reversible) │
                                            │  • can_use_tool permission    │
                                            │    hook = risk gate + journal │
                                            └──────────────────────────────┘
```

Concretely:
- **Step 3's skill match** stays as a fast path for trivial commands, but agentic requests route into the SDK session instead of the single LLM call.
- **Step 6** (`async for chunk in self._get_llm().stream_response(...)`) is replaced by an SDK agent run. The agent's text still streams to the panel; it can still emit POINT tags for narration (step 7 is unchanged), but now it can also **call tools across multiple turns**.
- **Tool calls pass through Clacky' `can_use_tool` permission hook** (see `SCOPE.md` §4.1): SAFE/REVERSIBLE run autonomously + journal; DANGEROUS confirm. File mutations only go through `safe_fs` (the tested `organizer` code), never raw shell.
- **The "money rule":** the SDK uses the local Claude sign-in first (free per task), direct API as fallback.

Everything in the KEEP column is reusable plumbing — well-built, with graceful fallbacks, DPI/multi-monitor handling, and clean signal decoupling. The agentic upgrade is one seam, not a rewrite.

---

## 7. Things to watch when lifting

- **Rebrand:** strings, `setApplicationName("Clicky")`, `%LOCALAPPDATA%\Clicky` paths, tray copy → Clacky.
- **The skills system here is regex→handler**, not markdown. Clacky moves to SDK markdown skills (per SCOPE §4.2); the base's `skills_pkg` becomes the *fast-path command* matcher, not the agent's skill source.
- **Don't expose the base's provider `stream_response` as the agent path** — that's the one-shot call you're replacing. Keep it only if you want a non-agentic "quick answer" mode alongside Agent Mode.
- **Privacy Guard + sensitive-window skip** should also gate what the *agent* can see — reuse it on the SDK path.
