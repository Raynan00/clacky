# Clacky 🧤

**Clicky for Windows — open source. Talk to your PC — she sees your screen, points at things, and actually does them.**

🎬 **Demo & write-up:** [raynanwuyep.com/clacky](https://raynanwuyep.com/clacky) · ⬇ **[Download for Windows](https://github.com/Raynan00/clacky/releases/latest)**

Clacky is a voice-first desktop companion for Windows. Hold a hotkey, talk, and she:

- **sees** your screen and answers questions about it,
- **points** — a little buddy flies to whatever you're asking about (snaps to the real UI element, pixel-accurate),
- **acts** — opens apps, clicks, types, runs multi-step tasks, using Claude Computer Use,
- **remembers** you across sessions and **learns routines** you teach her by voice,
- **tours** an app — "explain my screen" gives you a spoken, pointing walkthrough.

Her brain is **Claude** (Sonnet 5 + Computer Use); voice via **Deepgram** streaming STT and free **Edge TTS**.

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
- *"Open Notepad and type hello"*, *"click the Save button"*, *"go to YouTube"* → she acts on your machine.
- *"Remember I prefer dark mode"*, *"save this as my morning routine"* → cross-session memory + learned routines.
- *"Check my email"*, *"what's on my calendar"* → opens your logged-in web apps (or an opt-in Google API).
- *"Go research X and tell me later"* → a background agent works while you keep talking.

**The file organizer — `clacky organize`:**

- Tidies a folder from one LLM call; move-only and **fully reversible** with `clacky undo`.

## Getting started (Windows 10/11)

You'll need an **Anthropic API key** (Clacky's brain) and ideally a **Deepgram key**
(fast, accurate voice — free tier; without it she falls back to slower local
Whisper). A first-run setup wizard collects both — links included.

### Option 1 — Download the app *(no Python needed)*

1. Grab **`Clacky-v0.1.0-windows.zip`** from [**Releases**](https://github.com/Raynan00/clacky/releases/latest)
2. Extract anywhere and run `Clacky.exe`
   *(the exe is unsigned, so SmartScreen may warn on first run — "More info → Run anyway")*
3. The setup wizard walks you through your keys
4. **Hold `Ctrl+Alt+M`, say *"what's on my screen?"*, and release** 🧤

### Option 2 — Run from source *(Python 3.10+)*

```powershell
git clone https://github.com/Raynan00/clacky.git
cd clacky
pip install -e ".[shell,claude]"
clacky run
```

Keys: let the wizard collect them, or copy `.env.example` → `clacky/shell/.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
CLACKY_ACTIVE_LLM=claude
```

Full setup, what to say, and troubleshooting: **[docs/USAGE.md](docs/USAGE.md)**.

### Just want the file organizer? (no voice, no keys)

```powershell
pip install -e .
clacky organize ~/Desktop -p heuristic --dry-run   # preview, zero config
clacky organize ~/Desktop                           # do it
clacky undo                                          # reverse it
```

## A note on safety

The **file organizer** is move-only and fully reversible (`clacky undo`). The **voice agent acts directly** — like Clicky, she does what you ask rather than nagging for permission — but she stops and hands back before genuinely irreversible, high-stakes actions (send, delete, buy). It's an early build acting on your real machine, so **watch her, and press `Esc` to stop at any time.**

## Roadmap

- **Learnable skills (SKILL.md)** — adopting the same open Agent Skills standard used by Claude, Hermes, and OpenClaw, with Clacky's twist: you teach her by *voice*.
- **Clacky Bridge (MCP)** — exposing her eyes and pointer as an MCP server, so *any* agent (Claude, OpenClaw, Hermes) can see and point at a Windows screen.
- **Better desktop control** — opt-in shortcut/icon arrangement, more launcher coverage.

Issues and PRs very welcome — this is an early build and the fastest way to shape it. 🧤

## Layout

```
clacky/
  shell/        # the voice + screen companion (clacky run) — the main app
    routing.py  #   intent routing: local fast-paths + Haiku router
    tour.py     #   guided screen tour + pointing (inline [POINT] tags)
    actions.py  #   computer-use agent, launchers, organizer, background agents
  agent/        # computer-use actuation, permission model, safe file ops + undo
  providers/    # Claude / OpenAI / Gemini / Ollama / heuristic, behind one interface
  cli.py        # clacky organize / undo / run
docs/           # USAGE.md (start here), plus design docs
tests/          # headless tests with a fake provider
packaging/      # PyInstaller entry (clacky.spec builds the .exe)
organizer/      # early file-organizer prototype — superseded by clacky/, kept for its tests
```

## Credits & license

Clacky is an independent project. It builds on ideas and open-source work from:

- **Clicky** by [@farzaa](https://github.com/farzaa/clicky) — the original macOS screen-companion concept (MIT).
- **Clicky for Windows** by [Bitshank-2338 / Shashank Singh](https://github.com/Bitshank-2338/clicky-windows) — the Python/PyQt6 Windows companion Clacky lifts its voice + pointing pipeline from (MIT).
- **OpenClicky** by [@jasonkneen](https://github.com/jasonkneen/openclicky) — the actively maintained open-source Clicky with Agent Mode; design reference for how agent capabilities are structured (MIT, macOS/Swift).

Clacky is released under the [MIT License](LICENSE). It is not affiliated with or endorsed by the above projects or Anthropic.
