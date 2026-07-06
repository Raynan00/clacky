# Clacky — Windows build plan (v3)

**The project:** Clacky is a self-contained, **installable Windows app** — a screen companion that sees your screen, talks with you, points at things, and (the part nobody else has) **actually acts**: you say "clean this up" and it safely organizes your desktop, previewing every move and letting you undo.

**The positioning:** *Clicky on Windows — and it has hands.* The Windows version is the demand-driven hook; the hands are the reason to download Clacky instead of the obscure community ports.

**The one-liner (for the launch post):** *"Clicky finally works on Windows — and I gave it hands. Say 'clean this up' and watch it tidy your desktop, safely."*

**Why it's a real project, not a clone:** Every existing Clicky — the Mac original and all the Windows ports — is a *guide*: it sees the screen and points, but never takes an action. Clacky adds a content-aware, safety-gated action layer (move files with reasoning, preview, and undo) — a capability class none of them have.

---

## Strategic read (why this works)

The Windows-port space has 3+ community builds (`tekram`, `Bitshank`, `pango07/flicky`) and they're all obscure — Bitshank's is genuinely feature-rich and has ~1 star. The original got 5k stars from one viral tweet. **Conclusion: the bottleneck for a popular Windows Clicky is distribution, not code.** The "popular Windows version" slot is empty because none of the builders marketed it.

That's the opening. With an audience, you can take that slot *and* differentiate with a feature none of them have. Two distribution moments from one project:

1. **Launch post** — "Clicky on Windows, with hands." Rides existing demand (instantly understandable, no explanation needed) and leads with novelty.
2. **Hands demo** — the voice-driven desktop tidy as its own clip. Differentiated, shareable content.

**On Farza-risk (official Windows version):** it's a timing bet you win by moving now, before any official build. And since the goal is a showcase, the engagement is banked even if official ships later — plus "+ hands" still differentiates from official. Move now.

---

## What Clacky is (and isn't)

- **Is:** your own independent, branded, installable Windows app. One download, runs, works.
- **Isn't:** a fork, a PR to anyone's repo, or an add-on skill that needs someone else's app installed first. For a showcase, "download Clacky, watch it work" beats "install this other person's app, then drop my file in." Bundle everything into Clacky.

You still *lift* plumbing from the MIT sources — you just ship it as Clacky.

---

## Repo landscape (live, all MIT-licensed)

| Repo | Stack | Role for Clacky |
|---|---|---|
| `farzaa/clicky` | Swift / SwiftUI (macOS) | Reference — POINT protocol, proxy-worker pattern, personality |
| `tekram/clicky-windows` | Electron / TypeScript | Reference — clean pluggable-services design |
| `Bitshank-2338/clicky-windows` | Python 3.11+ / PyQt6 | **Primary source to lift from** — released, deep feature set, .exe packaging already solved |
| `pango07/flicky` | Electron (cross-platform) | Reference — NSIS Windows packaging |

**Why lift from Bitshank:** it already solves the boring, hard parts — pixel-perfect pointing (Computer Use API + universal grid locator, DPI/multi-monitor correct), full voice loop (push-to-talk, multi-provider STT/TTS), multi-provider LLM, and crucially **Windows packaging** (PyInstaller + Inno Setup installer). You inherit a working, packageable app and spend your energy on the hands + the brand.

---

## Architecture: the "hands" layer (this repo)

```
organizer/            # the hands — pure, no UI/LLM/app deps, fully tested
  planner.py          # dry-run plan: reads dir, reasons per file by intent. No side effects.
  executor.py         # applies a confirmed plan. Move-only, never deletes.
  undo.py             # records source->dest per batch; reverses last batch (persists to disk)
  guards.py           # home-only, protected-path denylist, sensitive-file skip, batch cap
skills/
  organize_desktop.py # voice triggers ("clean this up" / "undo") -> planner -> preview -> execute
```

**The flow (reusing the companion's voice + overlay + pointing):**

1. Push-to-talk → STT → "clean up my desktop." Trigger matches.
2. Read the target directory (start: Desktop only).
3. Build per-file context — name, type, content peek (text snippet; vision/OCR for images). LLM returns a **structured plan** (destination + one-line reason per file). No execution yet.
4. **Preview** in the panel; drive the existing pointing engine to sweep the cursor over file groups while narrating ("these 12 screenshots → Screenshots/…"). Companion + hands combine on camera here.
5. Confirm by voice ("yes, go ahead") or a button.
6. `executor.py` applies the moves; `undo.py` records the batch.
7. Speak the result: "Done — moved 23 files. Say 'undo' to reverse."

---

## The three differentiators (what makes it look senior)

1. **Dry-run preview, always.** Nothing moves until you approve the plan. The biggest maturity signal — respect for the user's real data.
2. **Undo.** Every batch is reversible, and it survives an app restart.
3. **Content-aware reasoning, not extension-sorting.** Infer *intent* ("screenshot from your project," "2025 tax PDF"), not dumb `.png`/`.pdf` buckets. The reasoning is what justifies an LLM in the loop.

Baseline safety in `guards.py`: protected-path denylist, batch cap, **move-only** (deletion = separate explicit confirm), plus reuse the base's Privacy Guard to skip sensitive windows.

---

## Milestone order

**M0 — Stand up *your* app (≈1–2 days).** Bring the Bitshank base into the Clacky repo (lift under MIT, keep notices), rebrand it Clacky, get it running: `python main.py`, confirm voice → screen → pointing. Develop against local Ollama to keep iteration free; use Claude for demo-quality.

**M1 — Hands core, headless ✅ (done — this repo).** `planner` + `executor` + `undo` + `guards` with passing tests. The part that's genuinely yours. Build solid, no UI.

**M2 — Wire hands in as a skill.** `skills/organize_desktop.py` calls the planner, returns the plan as text. Confirm-by-text first.

**M3 — Voice-driven, on screen.** Connect the preview to the panel + pointing engine: cursor sweeps file groups while narrating, confirm by voice, speak the result. **This is the demo.**

**M4 — Polish for the recording.** Tune narration/personality, smooth the preview animation, handle obvious on-camera failure cases gracefully.

**M5 — Package & release (the distribution step).** Build a single installable `.exe` (PyInstaller + Inno Setup, inherited from the base). Test the installer **on a clean/second Windows machine** — this is where "works on my machine" breaks. Write a tight README with a demo GIF, a one-command run, and a download link. Cut a GitHub release.

**M6 — Launch.** Post the launch hook + demo clip. Link the release. Pin the repo. Be ready to answer "how do I run it" fast — early friction kills momentum.

Ship the working demo after M3. M5/M6 are what convert it from a portfolio repo into a *thing people download*.

---

## The demo (the asset that earns engagement)

- 30–60s recording of your *actual* cluttered desktop. Real > staged.
- Hold the hotkey: "clean this up." Cursor sweeps the mess, Clacky narrates what it found, shows the plan, you say "go," files animate into folders. End on a clean desktop + "say undo to reverse."
- Before/after is the hook.
- Pair with a short writeup of the *interesting* engineering: the guide-vs-act decision, dry-run/undo safety, content-aware reasoning.

---

## Ownership & attribution (decided)

Clacky is **your own independent project** — not a fork, not a PR. MIT permits lifting code into a new project as long as you:

- Keep the original **MIT LICENSE** text + copyright lines for reused code.
- Credit **farzaa** (original concept) and **Bitshank** (the Windows base) in the README.
- Give it its own name/identity (done: Clacky) so it reads as your product.

Narrative: *"I built Clacky — the Windows version of the cursor companion, and I gave it hands,"* with a credits line. Not "I forked Clicky."

---

## Risks & gotchas

- **Packaging is the real boss fight.** A `.exe` that runs on *your* machine but breaks on someone else's (missing DLLs, mic permissions, antivirus flags on unsigned binaries) will tank a launch. Budget real time for M5 and test on a clean machine. Consider noting "unsigned — Windows SmartScreen may warn" in the README.
- **The demo must actually work on camera.** Voice + screen + pointing + safe file moves is a lot of moving parts live. Have a hotkey-triggered text fallback if voice fights you.
- **API costs.** Pointing + content-peeking burn vision tokens. Dev on Ollama (free, local); Claude for the demo.
- **Don't over-scope the organizer.** Desktop-only, move-not-delete, one folder at a time. Narrow + works-on-camera beats general + breaks-live.
- **Move before official.** The distribution window is open now; it narrows if/when Farza ships an official Windows build.

---

## The framing that makes it land

Lead with demand, differentiate with novelty: *"Clicky on Windows — and it has hands."* The Windows version gets people in the door (the demand already exists); the safe, reasoning-driven action layer is why they download **Clacky** specifically and the part that shows you can design a feature, not just follow a tutorial.
