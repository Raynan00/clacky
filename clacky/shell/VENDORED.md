# Vendored companion shell

This directory is a **bundled copy** of the Windows companion shell from:

- **`Bitshank-2338/clicky-windows`** — https://github.com/Bitshank-2338/clicky-windows
- Source commit: `3f5e6b6aa7df7d32181fd758f426cd14f4f2cfa2` (2026-06-18)
- License: **MIT** — see [`LICENSE.bitshank`](LICENSE.bitshank) (original copyright preserved).

Per `docs/BUILD_PLAN.md`, Clacky ships this as part of its own product (bundle,
not a fork or submodule), as MIT permits, with the notice preserved and credit
in the top-level `README.md`.

## What was changed from upstream

Kept deliberately minimal so the shell stays close to upstream and is easy to
re-vendor:

1. **Rebrand only**, applied across `*.py`:
   - `Clicky` → `Clacky` (display strings, app name, and the data dir
     `%LOCALAPPDATA%\Clicky` → `%LOCALAPPDATA%\Clacky`).
   - `~/.clicky/` → `~/.clacky/` (user skills dir).
   - The `CLICKY_*` environment-variable names were renamed to `CLACKY_*`
     (config + `.env`); no back-compat aliases remain.
2. **Clacky's agentic layer** was then built on top of the vendored base and
   lives in dedicated modules mixed into `CompanionManager`:
   - `routing.py` — intent routing (local fast-paths + a Haiku router as the
     authority) and the shared warm API clients
   - `tour.py` — the inline-`[POINT]`-tag guided tour + all pointing glue
     (UIA snap, tag parsing, model-coordinate fallback)
   - `actions.py` — the Computer Use agent loop, app/URL launching, the
     voice-driven (journaled, reversible) organizer, Google Workspace tools,
     and background research agents
   - plus `memory_store.py` (cross-session memory + routines),
     `google_workspace.py`, `session_log.py` (timestamped flight recorder),
     and `audio/stt/deepgram_streaming.py` (live push-to-talk STT)

   `companion_manager.py` keeps the vendored capture→STT→LLM→TTS state machine
   and dispatches into those modules.

## How it's launched

`clacky run` → `clacky/companion.py` adds this directory to `sys.path` and runs
`main.py` as `__main__` (the vendored absolute imports resolve against this
tree). The shell is otherwise unmodified.

## Verifying it runs (needs a real Windows desktop)

This can't be verified headlessly — it's a PyQt6 tray app with mic + screen +
overlay. On a real desktop:

```
pip install -e ".[shell]"      # PyQt6, audio, pointing (UIA), Ollama, …
clacky run                       # tray buddy; hold the hotkey and talk
```

Develop on the free local Ollama path (no API key needed for Phase 1; pointing
falls back to Windows UIA). See `clacky/shell/SETUP.md` for provider keys.

## Not yet done (tracked)

- **Cosmetic/packaging rebrand:** `clicky.spec`, `installer.iss`, `build.bat`,
  and the vendored `README.md`/docs still say "Clicky" — Phase 5 packaging.
- ~~`CLICKY_*` → `CLACKY_*` env rename~~ — **done** (config + `.env`).
- **Wheel packaging** of `clacky/shell/` as package data — Phase 5.
- **Trim** unused `tutor_features/` (lessons/quizzes/collab) once the agent
  path is in — optional.
