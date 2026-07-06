# Clacky 🧤

**Clicky for Windows — open source. Talk to your PC and it sees your screen, points at things, and does them.**

Clacky is a voice-first desktop companion for Windows. Hold a hotkey, talk, and it:

- **sees** your screen and answers questions about it,
- **points** — a little buddy flies to whatever you're asking about (snaps to the real UI element, pixel-accurate),
- **acts** — opens apps, clicks, types, runs multi-step tasks, using Claude Computer Use,
- **remembers** you across sessions and can **learn routines** you teach it,
- **tours** an app — "explain my screen" gives you a spoken, pointing walkthrough.

Built on **Claude** (Sonnet 5 + Computer Use), **Deepgram** speech-to-text, and free **Edge TTS**.

### Where this fits

|                        | macOS                      | Windows                       |
|------------------------|----------------------------|-------------------------------|
| **Closed (heyclicky)** | agent mode shipped         | waitlist only                 |
| **Open source**        | OpenClicky has agent mode  | **empty — Clacky fills this** |

Clicky is Mac-only. Clacky brings the same idea to the majority of desktops that can't run it — open and free.

> ⚠️ **Early build, and honestly a bit rough.** The core loop — talk → see → point → act — works and is genuinely fun. But speech recognition isn't perfect, and the more advanced features (multi-step tasks, Gmail/Calendar, background research) are lightly tested. This is a "try it and tell me what breaks" release, not a finished product.

---

## What works today

**The voice companion — `clacky run`:**

- Push-to-talk voice; an on-screen buddy that points at what you ask about.
- *"What's on my screen?"* → a spoken answer, buddy points at the relevant thing.
- *"Explain my screen"* / *"walk me through this"* → a teaching tour that points out several things, one at a time.
- *"Open Notepad and type hello"*, *"click the Save button"*, *"go to youtube"* → it acts on your machine.
- *"Remember I prefer dark mode"*, *"save this as my morning routine"* → cross-session memory + learned routines.
- *"Check my email"*, *"what's on my calendar"* → opens your logged-in web apps (or an opt-in Google API).
- *"Go research X and tell me later"* → a background agent works while you keep talking.

**The file organizer — `clacky organize`:**

- Tidies a folder from one LLM call; move-only and **fully reversible** with `clacky undo`.

## Quick start (Windows)

**Requirements:** Windows 10/11, Python 3.10+, an **Anthropic API key** (and a **Deepgram key** for good speech-to-text — without it, it falls back to slower local Whisper).

```powershell
git clone <your-repo-url> clacky
cd clacky
pip install -e ".[shell,claude]"
```

Add your keys — copy `.env.example` to `clacky/shell/.env` and fill in the shell section:

```
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
CLACKY_ACTIVE_LLM=claude
```

(Or just run it — a first-run setup wizard walks you through keys.)

Then:

```powershell
clacky run
```

**Hold `Ctrl+Alt+M`, say *"what's on my screen?"*, and release.** Full setup + troubleshooting: **[docs/USAGE.md](docs/USAGE.md)**.

### Just want the file organizer? (no voice, no keys)

```powershell
pip install -e .
clacky organize ~/Desktop -p heuristic --dry-run   # preview, zero config
clacky organize ~/Desktop                           # do it
clacky undo                                          # reverse it
```

## A note on safety

The **file organizer** is move-only and fully reversible (`clacky undo`). The **voice agent acts directly** — like Clicky, it does what you ask rather than nagging for permission — but it pauses to confirm on genuinely irreversible, high-stakes actions (send, delete, buy). It's an early build acting on your real machine, so **watch it, and press `Esc` to stop it at any time.**

## Layout

```
clacky/
  shell/        # the voice + screen companion (clacky run) — the main app
  agent/        # computer-use actuation, permission model, safe file ops + undo
  providers/    # Claude / OpenAI / Gemini / Ollama / heuristic, behind one interface
  cli.py        # clacky organize / undo / run
docs/           # USAGE.md (start here), plus design docs
tests/          # headless tests with a fake provider
```

## Credits & license

Clacky is an independent project. It builds on ideas and open-source work from:

- **Clicky** by [@farzaa](https://github.com/farzaa/clicky) — the original macOS screen-companion concept (MIT).
- **Clicky for Windows** by [Bitshank-2338 / Shashank Singh](https://github.com/Bitshank-2338/clicky-windows) — the Python/PyQt6 Windows companion Clacky lifts its voice + pointing pipeline from (MIT).
- **OpenClicky** by [@jasonkneen](https://github.com/jasonkneen/openclicky) — the actively maintained open-source Clicky with Agent Mode; design reference for how agent capabilities are structured (MIT, macOS/Swift).

Clacky is released under the [MIT License](LICENSE). It is not affiliated with or endorsed by the above projects or Anthropic.
