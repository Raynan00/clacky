# Using Clacky (the voice companion)

This is the practical guide to `clacky run` — setup, what to say, and how to get
out of trouble. For the file organizer, see [CLI.md](CLI.md).

> Clacky is Windows-only and an early build. The core loop works; expect rough
> edges, especially in speech recognition.

---

## 1. Install

Requires **Windows 10/11** and **Python 3.10+**.

```powershell
git clone <your-repo-url> clacky
cd clacky
pip install -e ".[shell,claude]"
```

This pulls the shell dependencies (PyQt6, sounddevice, mss, uiautomation, edge-tts,
faster-whisper, …) and the Anthropic SDK. First launch may download a small local
Whisper model for the wake word.

## 2. Keys

Clacky needs an **Anthropic API key** (for seeing / pointing / acting) and works
much better with a **Deepgram key** (fast, accurate speech-to-text).

Copy `.env.example` → `clacky/shell/.env` and fill in:

```
ANTHROPIC_API_KEY=sk-ant-...
DEEPGRAM_API_KEY=...
CLACKY_ACTIVE_LLM=claude
CLACKY_HOTKEY=ctrl+alt+m
```

Get keys: [console.anthropic.com](https://console.anthropic.com) ·
[console.deepgram.com](https://console.deepgram.com) (free tier is plenty).
On first run, a setup wizard can also collect these for you.

## 3. Run

```powershell
clacky run
```

A small buddy appears near your cursor and Clacky sits in the system tray.

- **Push-to-talk:** hold **`Ctrl+Alt+M`**, speak, then **release**. (Hold the whole
  time you're talking — it's not tap-to-toggle.)
- **Wake word:** you can also just say **"hey Clacky"** without the hotkey.
- **Stop it talking / acting:** press **`Esc`**, or hold the hotkey again to barge in.
- **Quit:** use the **tray icon → Quit** (see troubleshooting for why this matters).

## 4. What to say

**Ask about the screen**
- "What's on my screen?"  ·  "What does this button do?"  ·  "Where's the settings icon?"

**Get a tour (it points at several things)**
- "Explain my screen."  ·  "Walk me through this."  ·  "What can I do here?"

**Make it act** (it does it directly — watch it)
- "Open Notepad and type hello world."  ·  "Click the Save button."  ·  "Go to youtube."
- **Force the "do it" mode:** start with **"go…"**, **"agent…"**, **"do it"**, **"take over…"**, or **"Clacky, go…"** — e.g. *"go open Chrome and search for lofi."* Normally Clacky guesses whether you're asking a question or giving a command; a trigger word removes the guess and guarantees it acts (great when a phrase could read either way, and for reliable demos).

**Memory & routines** (persist across restarts)
- "Remember I prefer dark mode."  ·  "What do you know about me?"
- "Save this as my morning routine: open gmail then open slack."  ·  "Do my morning routine."

**Email / calendar** (opens your logged-in web apps)
- "Check my email."  ·  "What's on my calendar today?"

**Background research** (works while you keep talking)
- "Go find out the best budget laptops right now and tell me later."

## 5. Troubleshooting

**It triggers but nothing records / "0 bytes captured."**
Almost always a stuck mic from a previous run. Quit via the **tray**, not `Ctrl+C`
(Ctrl+C can leave a `python.exe` holding the microphone). If it happens:
```powershell
taskkill /F /IM python.exe
```
Then relaunch. Also check **Settings → System → Sound → Input** has the right mic.

**The hotkey does nothing / a blinking bar appears.**
Another app owns `Ctrl+Alt+M` (Claude Desktop, screen tools, etc.). Change
`CLACKY_HOTKEY` in `clacky/shell/.env` to something free, e.g. `ctrl+alt+c`.

**Speech recognition mishears me.**
This is the known weak spot. A Deepgram key helps a lot vs. local Whisper. Speak a
beat after pressing the key, and hold until you've finished. The wake word ("hey
Clacky") is a coined word, so it's less reliable than push-to-talk — prefer the hotkey.

**It points slightly off.**
The buddy snaps to real UI elements via Windows accessibility, but on some apps
(canvas/games) the tree is sparse and it falls back to the model's estimate. Menus,
buttons, and standard controls are accurate.

**It won't use my Claude features.**
The acting, tours, memory, and routing all require `ANTHROPIC_API_KEY`. On the free
Ollama path, Clacky can still see, talk, and point, but not act.

**"Windows can't find <app>" when she tries to open something.**
Some apps (Steam-likes, Store installs) aren't resolvable by name. Teach her where
yours live — create `~/.clacky/apps.json`:
```json
{ "steam": ["D:\\Games\\Steam\\steam.exe", "steam://open/main"],
  "my tool": "mytool://" }
```
Candidates are tried in order (exe paths, then URL protocols); your entries
override the built-ins.

## Tuning (env vars)

| Variable | Default | What it does |
|---|---|---|
| `CLACKY_HOTKEY` | `ctrl+alt+m` | Push-to-talk combo |
| `CLACKY_WAKE_WORD` | `1` | `0` = wake word fully off (e.g. while recording a video that says "Clacky") |
| `CLACKY_STT_KEYTERMS` | — | Comma-separated words to bias speech recognition toward (e.g. `Premiere Pro,Figma`) |
| `CLACKY_STREAM_STT` / `CLACKY_STREAM_TTS` | `1` | `0` reverts to batch STT / whole-reply TTS |
| `CLACKY_MOVE_STAGGER` | `0.12` | Seconds between file moves during organize/undo (`0` = instant) |
| `CLACKY_ORGANIZE_MODEL` | Haiku | Model that plans folder cleanups |

## 6. Optional: Gmail / Calendar via the Google API

By default Clacky drives your **logged-in web apps** (no setup). For the faster,
headless API path, drop a Google **Desktop OAuth client** at
`~/.clacky/google_credentials.json` and `pip install ".[google]"`. See
[`clacky/shell/google_workspace.py`](../clacky/shell/google_workspace.py).
