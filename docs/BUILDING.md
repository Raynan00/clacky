# Building the Clacky `.exe`

Package the voice companion as a Windows executable so people can run it without
a Python checkout — they just add their keys.

> **Reality check.** This is a big Qt + audio + Whisper app. A first PyInstaller
> build almost never works in one shot — you build, hit a `ModuleNotFoundError`
> or a missing DLL at runtime, add it, and rebuild. Budget an afternoon, not five
> minutes. The `clacky.spec` here is a solid starting point, not a guarantee.

## Build (on Windows)

```powershell
pip install -e ".[shell,claude]"     # the app + its deps
pip install pyinstaller
pyinstaller clacky.spec
```

Output: **`dist/Clacky/Clacky.exe`** plus a folder of dependencies. This is a
*folder* build on purpose — one-file `.exe`s extract to a temp dir on every launch
(slow, and more likely to trip antivirus). **Distribute the whole `dist/Clacky/`
folder** (zip it).

## How users add their keys (BYOK)

The frozen app can't write into itself, so keys live in a user-writable dir. Two ways:

1. **Setup wizard** — on first launch, it collects keys.
2. **Manually** — create `%LOCALAPPDATA%\Clacky\.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   DEEPGRAM_API_KEY=...
   CLACKY_ACTIVE_LLM=claude
   ```
   (`clacky/shell/config.py` reads this dir when frozen — no rebuild needed.)

## Known rough spots (in likely order)

- **Missing modules at runtime** → add the name to `hiddenimports` in `clacky.spec`
  and rebuild. Common culprits: Qt plugins, `comtypes` (for `uiautomation`), audio backends.
- **`faster_whisper` / `ctranslate2`** — the wake word uses a local Whisper model.
  Its native DLLs must come along (the spec's `collect_all("faster_whisper")` handles
  most), and the model itself downloads on first run (needs network + write access to
  the user dir). If you want the wake word to work offline, pre-bundle the model.
- **The icon line** — `clacky.spec` points `icon=` at `clacky/shell/assets/icon.ico`.
  If that file isn't there, generate one (see `clacky/shell/assets/make_icon.py`) or
  delete the `icon=` line.
- **The shell launches by adding its dir to `sys.path` and running `main.py`**
  (`clacky/companion.py`). That "run-as-script" pattern can need tweaking when frozen —
  if launch fails, import the shell's `main()` directly in `packaging/clacky_app.py`
  instead of going through `companion.launch()`.
- **Antivirus** — unsigned single-folder PyInstaller apps sometimes get flagged. For
  real distribution, **code-sign** `Clacky.exe` (a cheap OV cert clears most of it).
- **Size** — expect a few hundred MB (PyQt6 + Whisper + audio). Normal.

## Faster iteration while debugging

Build with a console so you can see the error, then flip back to windowed for release:

```powershell
pyinstaller --console --onedir --name Clacky packaging/clacky_app.py
```

Run `dist/Clacky/Clacky.exe` from a terminal, read the traceback, fix, repeat. Once
it launches cleanly, use `pyinstaller clacky.spec` for the real (windowed) build.
