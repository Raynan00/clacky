# Clacky — Agent Plan (v4): the computer-acting Windows port

**Decision (supersedes the v3 "direct-provider planner" design):** Clacky is a
proper Windows port of the *agent-mode* Clicky — a screen companion that sees,
talks, points, **and acts on the computer** by driving the GUI via Claude's
Computer Use tool. The files-organizer becomes one safe skill inside it, not
the product.

> This reverses v3's "one structured call, the model never touches anything."
> That was right for files-only; a computer agent needs the loop back:
> **observe → decide → act → observe**. See `SCOPE.md` for the (now historical)
> v2/v3 designs.

The "hands" in v1 are GUI actions, and the trust layer that gates them is the
moat. Everything below is grounded in the real base
(`Bitshank-2338/clicky-windows`, MIT) — we lift its shell and extend its
pointing engine into acting.

---

## 1. The base we lift (real seams)

The base is a tray app with a signal-wired brain; it's a *guide* (points, never
acts). We keep all its plumbing and replace exactly one call.

| Base file (Bitshank) | What it gives us | Clacky use |
|---|---|---|
| `companion_manager.py` `_end_capture_and_process` (~L496) | the core utterance→reply loop | host for the agent loop |
| `companion_manager.py` `stream_response(...)` (~L731, L1024) | **the one-shot LLM call** | **← replace with the agent loop** |
| `companion_manager.py` `sig_point_at/_hold/_release` (L242–244) | overlay narration signals | drive narration while the agent works |
| `skills/__init__.py` `match()` + `SKILL` dict | regex-trigger → async handler, run *before* the LLM | fast-path commands ("undo"); route agentic asks to the loop |
| `skills/example_self_mode.py` `_self_mode_armed` | seed of "click what it points at" | the literal starting point for acting |
| `ai/base_provider.py` `stream_response(user_text, screenshots_b64, history, …)` | provider interface — already takes screenshots | too narrow for tool-use; the agent path uses the Anthropic SDK directly |
| `ai/hybrid_pointer.py` `find_target() → Target(x,y,bbox,label,source)` | **Windows UIA** tree walk, logical px, pixel-perfect | **two uses:** actuation targeting **and** the trust classifier's element inspection |
| `ai/element_locator.py` (beta `computer-use-2025-11-24`) | already calls Claude Computer Use to *locate* | **extend "locate" → "act"** |
| `screen/capture.py` | multi-monitor screenshots | the loop's observe step |
| `clicky.spec` / `installer.iss` / `build.bat` | PyInstaller + Inno Setup packaging | Phase 5, inherited |

**Why this is one seam, not a rewrite:** voice, screen capture, the
UIA/pointing engine, DPI/multi-monitor correctness, tray, and `.exe` packaging
are all done. We add an agent loop and a trust gate; everything else is lifted.

---

## 2. The architecture (and what's already built here)

```
shell (Bitshank)  ──screenshot+transcript──▶  AGENT BRAIN (Claude Computer Use loop)
   voice/screen/overlay/tray                        │ proposes an action
        ▲                                            ▼
   narrate/confirm  ◀──────────────  TRUST GATE  classify_action() ── SAFE/CAUTION run
        │                                │          DANGEROUS → confirm
        └────────────────────────  ACTUATION (Win32 SendInput, logical px)
```

Clacky-side modules (this repo):

| Module | Status |
|---|---|
| `clacky/agent/permission.py` — `classify_action(Action) → Risk` | **built + unit-tested** (the safety-critical core) |
| `clacky/agent/computer_loop.py` — `ComputerAgent` (gate + dispatch) | scaffold; gate path tested headlessly |
| `clacky/agent/actuation.py` — `Actuator` / `WindowsActuator` / `RecordingActuator` | scaffold; recording backend tested |
| `clacky/agent/{fileops,journal,planner}.py`, `providers/`, `heuristic` | **intact** — become the files skill + its real undo |
| `organizer/`, `agent/sdk_tools.py` | dead, left as-is |

Tests: `tests/test_permission.py`, `tests/test_computer_loop.py` (11 passing).

---

## 3. The trust model (the differentiator — and the honest part)

**Undo does not survive general GUI actions.** You cannot un-send, un-buy,
un-type. So the model is *confirm-before-irreversible + narrate-before-acting*,
not "one word from undo." Four tiers (`permission.Risk`):

| Tier | Example | Behaviour |
|---|---|---|
| `SAFE` | screenshot, cursor move, scroll | run |
| `REVERSIBLE` | a file move (files skill) | run + **journal** (real undo) |
| `CAUTION` | click a normal button, type in a field | run, but **narrate first** (no undo) |
| `DANGEROUS` | Send / Delete / Buy / Post / **or an unidentified click** | **pause + confirm** |

Two safety properties hold by construction:
1. **Default-deny:** a click whose target we can't resolve via UIA → DANGEROUS.
2. **Non-bypassable gate:** every action goes through `classify_action` before
   it can reach the `Actuator` (`ComputerAgent._authorize_and_run` is the only
   route). File mutations still go through `fileops`, never raw input.

The README/launch copy must reflect this — promise "narrates everything and
pauses on anything it can't take back," not fake undo on GUI actions.

---

## 4. Model & cost (grounded; verify strings at build)

- **Brain:** `claude-opus-4-8` for demo quality (Opus 4.7+ added high-res
  vision with **pixel-accurate coordinates** — exactly what a GUI agent needs).
  Dev on `claude-sonnet-4-6` ($3/$15) to keep iteration cheap. Best computer-use
  accuracy on **adaptive thinking + `high`/`xhigh` effort**.
- **Computer Use** is a **client-side beta tool**; Bitshank's working beta
  header is `computer-use-2025-11-24` (confirm against live docs at build —
  these strings move).
- **Cost is real:** every step sends a screenshot (image tokens; high-res up to
  ~4.8K tokens each). Send screenshots at **~1080p** (720p/1366×768 if
  cost-sensitive). A multi-step task = many model round-trips.
- The **files skill** stays multi-provider / offline (heuristic, Ollama) — it's
  not Computer Use.

---

## 5. Build order

- **Phase 1 — stand up the shell (the M0 that was skipped).** Lift Bitshank
  into the repo as Clacky, rebrand (`Clicky`→`Clacky`, `%LOCALAPPDATA%\Clicky`,
  tray copy), get voice → screen → pointing running.
- **Phase 2 — pointing → acting.** Implement `WindowsActuator` (SendInput,
  reuse Bitshank coords) and `ComputerAgent.run` (Anthropic Computer Use loop).
  Prove **one bounded task** end-to-end. The gate + actuation interfaces are
  already in place and tested.
- **Phase 3 — trust layer hardening.** Wire `hybrid_pointer` UIA inspection into
  the classifier; voice/visual confirm for DANGEROUS; adversarial tests. *(The
  classifier itself is done; this connects it to live UIA + the shell.)*
- **Phase 4 — skills + files anchor.** Markdown skill packs; fold the
  files-organizer in as the keep-real-undo skill. **The demo.**
- **Phase 5 — package `.exe`** (Bitshank's PyInstaller + Inno Setup), clean-machine
  test, solve the Anthropic SDK / API-key onboarding.
- **Phase 6 — launch.**

---

## 6. Open items before Phase 1

- Bitshank source: cloned for reference; decide bundle-vs-submodule when lifting.
- `ANTHROPIC_API_KEY` for the Phase 2 agent path (Phase 1 can stay on free
  Ollama chat).
- Re-confirm the Computer Use tool `type`/version string and beta header against
  live docs when Phase 2 starts.
