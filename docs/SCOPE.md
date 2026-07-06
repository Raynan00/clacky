# Clacky — Scope & Design (v3, direct providers)

> **⚠️ SUPERSEDED for the current direction — see [`AGENT_PLAN.md`](AGENT_PLAN.md) (v4).**
> The project is now a proper Windows port of the *computer-acting* Clicky: it
> drives the GUI via Claude's Computer Use tool, with the file-organizer as one
> safe skill. That brings back an agent loop (observe → decide → act), which the
> v3 "one structured call" design below deliberately dropped. This file is kept
> for the lane/wedge framing and as history; the v4 architecture, the Bitshank
> integration map, and the build order live in `AGENT_PLAN.md`.

> *The open-source Windows agent companion you're waitlisted for — and the one you can actually trust with your files.*

> **⚠️ Architecture update (v3 supersedes the SDK sections below).** Clacky no
> longer builds on Claude Code / the Claude Agent SDK. Following how the actual
> Windows base (Bitshank) is built, it now calls **LLM providers directly**
> (Claude / OpenAI / Gemini / Ollama, plus a zero-config heuristic), with **no
> agent loop**: the model returns a JSON *plan* in one call and Clacky' own code
> executes it through the safe-file layer. This is simpler, removes the
> Claude-subscription onboarding wall, restores a free local/offline path, and
> is *safer* — the model never calls tools or touches the disk, so there is no
> agent autonomy to constrain. The trust layer (preview, move-only, undo,
> home-only guards) is unchanged. Sections 2, 4, 5 below that describe the
> Agent SDK, `can_use_tool`, and in-process MCP tools are **historical**; see
> `BASE_ARCHITECTURE.md` and `CLI.md` for the current design. The lane,
> wedge, non-goals, safety model, distribution, and risks all still hold.

---

## 1. The lane (why this exists)

| | macOS | Windows |
|---|---|---|
| **Closed (heyclicky)** | ✅ agent mode shipped | ⏳ **waitlist only** |
| **Open source** | ✅ openclicky has agent mode | ❌ **empty** — ports are pointing-only |

"Hands" is **not novel** — heyclicky and openclicky both have agentic mode on Mac. The gap is **Windows + agentic + open-source**, and it's empty: official Windows is a waitlist (demand proven), every Windows port (Bitshank/tekram/flicky) is pointing-only. Clacky fills it.

**Two-part wedge (equal weight):**
1. **Windows-first + open source** — the agent people are waitlisted for, free and now.
2. **Trust via reversibility, not interruption** — runs autonomously like Clicky, but everything it does is undoable; only irreversible actions pause to confirm. "The autonomous agent you can take back."

---

## 2. What `openclicky` taught us (and what we copy)

openclicky's Agent Mode is **not bespoke**. From its resource pack:

- **Runtime = Claude Agent SDK** (`ClaudeAgentSDKBridge/`) + Codex fallback + a `BackgroundComputerUseRuntime/`. It delegates the agent loop; it doesn't build one.
- **Skills = markdown packs.** ~25 files (`notion.md`, `spotify.md`, `blender.md`, `powerpoint.md`, `github-pr-workflow.md`, …) — domain know-how, not code — plus `skill-suggestion-rules.json` (context → which skill), `SOUL.md` (personality), `OpenClickyModelInstructions.md` (system behavior).
- **"Money rule":** Agent SDK first (uses the user's already-paid Claude sign-in — free per task); direct API only as fallback (per-token). Local keys, no hosted login.

**We copy all three.** Clacky is *not* a from-scratch agent — building one would be a weaker reinvention of the SDK. Clacky is the **Windows companion + a trust layer on top of the SDK**, with file-organizing as the first skill.

---

## 3. What Clacky v1 IS / IS NOT

**IS:** a Windows-native companion shell (voice/screen/overlay, lifted from Bitshank) wrapping the **Claude Agent SDK**, with a **reversible safe-file-ops tool**, a **risk-gating permission hook**, an **undo journal**, and **markdown skills** (the organize-desktop skill + one more, plus a suggestion router).

**IS NOT:**
- ❌ A from-scratch agent loop / planner (the SDK is the brain).
- ❌ GUI pixel-clicking for v1 — hands are *filesystem/tool* actions (reliable, undoable). Pointing overlay stays a transparency flourish.
- ❌ heyclicky's full breadth (Figma→site, etc.). The SDK *allows* it; v1 ships two skills, done well.
- ❌ Anything that deletes/sends/pays without an explicit separate confirm.
- ❌ macOS/Linux; a provider zoo; hosted login.

---

## 4. Architecture — three layers + skills

```
   ┌─────────────────────────────────────────────────────────┐
   │  COMPANION SHELL   (Python/PyQt6, lifted from Bitshank)  │
   │  push-to-talk STT · screen capture · cursor overlay ·    │
   │  tray · TTS                                              │
   └───────────────┬─────────────────────────────────────────┘
                   │ transcript + screen context
                   ▼
   ┌─────────────────────────────────────────────────────────┐
   │  AGENT RUNTIME   (Claude Agent SDK, Python)              │
   │  plans + runs autonomously in the background.            │
   │  Money rule: local Claude sign-in first, API key fallback│
   └───────────────┬─────────────────────────────────────────┘
                   │ every tool call passes through…
                   ▼
   ┌─────────────────────────────────────────────────────────┐
   │  CLACKY TRUST LAYER   (yours — the differentiator)        │
   │  • Safe tools: file ops via a reversible move/rename/    │
   │    create tool (the `organizer` code). Never raw delete. │
   │  • Permission hook (can_use_tool): risk-gate each call.  │
   │      SAFE / REVERSIBLE → run autonomously + journal      │
   │      DANGEROUS (delete/send/pay/overwrite) → confirm     │
   │  • Undo journal: reverse the last batch on "undo".       │
   └───────────────┬─────────────────────────────────────────┘
                   ▲
                   │ guided by
   ┌─────────────────────────────────────────────────────────┐
   │  SKILLS   (markdown packs + suggestion rules)            │
   │  organize-desktop.md  +  one more  +  routing rules      │
   └─────────────────────────────────────────────────────────┘
```

**Default flow (autonomous):** voice → shell → SDK agent plans → calls a tool → trust layer classifies risk → safe/reversible run immediately + journal → result spoken, "say undo to reverse." The confirm gate fires *only* for DANGEROUS calls.

### 4.1 The trust layer (the part that's genuinely yours)

The agent is powerful but must not be trusted with raw destructive operations. So:

1. **Constrain the toolset.** Don't expose raw shell `rm`/`mv` to the agent. File mutation happens only through a Clacky-provided tool whose implementation is the tested `organizer` code — move/rename/create, journaled, never destructive.
2. **`can_use_tool` permission hook = the safety brain.** Every tool call the SDK wants to make is classified:
   - `SAFE` (read, screenshot, search) → run.
   - `REVERSIBLE` (move/rename a file) → run + append to the undo journal.
   - `DANGEROUS` (delete, overwrite, send email, network write, anything irreversible) → pause for an explicit confirm; flag it visually/audibly.
3. **Undo journal** persists batches; "undo" reverses the last one. Each reversible op stores a minimal token (`{src,dst}`), already implemented in the current `undo.py`.

This is what makes "an autonomous agent loose on my files" not terrifying — and it's the work a rushed clone skips.

### 4.2 Skills = markdown (copy openclicky's pattern)

A skill is a markdown pack describing *how* to do something + when to use it — not a Python plugin. v1 ships:

- `skills/organize-desktop.md` — the reference skill: how to tidy a folder by intent, using the safe-file-ops tool.
- One more (e.g. `skills/rename-by-content.md`) — proves it's a real skill system, not single-purpose.
- `skills/suggestion-rules.json` — context (active app / phrase) → which skill to surface, mirroring openclicky's `skill-suggestion-rules.json`.
- `SOUL.md` / `system-instructions.md` — personality + base behavior.

New capabilities later are mostly *new markdown* + (only if needed) a new safe tool. That's the cheap content stream for launch follow-ups.

---

## 5. How the existing M1 code maps in

The tested `organizer/` package is **not wasted** — it becomes the safe-tool engine:

| Today (`organizer/`) | Becomes |
|---|---|
| `planner.build_plan()` | folded into the `organize-desktop.md` skill's guidance + a tool the agent calls; the LLM planning now comes from the SDK agent, not a separate call |
| `executor` + `Move`/collision logic | the **safe-file-ops tool** implementation invoked by the agent |
| `undo.py` (token-based) | the **undo journal** behind the permission hook |
| `guards.py` | the **risk classifier** inside `can_use_tool` (home-only, denylist, batch caps, SAFE/REVERSIBLE/DANGEROUS) |

The pure logic and tests survive; what changes is *who decides what to do* — the SDK agent, not a bespoke planner.

---

## 6. Repo structure (v2 target)

```
clacky/
  shell/            # companion: voice, screen, overlay, tray (from Bitshank, rebranded)
  agent/
    runtime.py      # Claude Agent SDK setup; money-rule routing (SDK first, API fallback)
    permission.py   # can_use_tool hook → risk classify → autonomous / confirm
    journal.py      # persistent undo batches
  tools/
    safe_fs.py      # reversible file ops exposed to the agent (the old organizer/executor)
  skills/
    organize-desktop.md
    rename-by-content.md
    suggestion-rules.json
  resources/
    SOUL.md
    system-instructions.md
  app.py
tests/              # safe_fs + permission-hook tests (the safety-critical code)
docs/               # SCOPE.md, BUILD_PLAN.md
```

---

## 7. Milestones (revised for SDK)

- **M0** — Stand up the companion shell on Windows (Bitshank base, rebranded Clacky). Voice → screen → overlay working.
- **M1** — Safe file-ops + undo core ✅ *(done — becomes the safe tool + journal).*
- **M1.5 (the keystone)** — Integrate the Claude Agent SDK. Expose `safe_fs` as its only file-mutation tool. Implement `can_use_tool` (autonomous + risk gate). Ship `organize-desktop.md`. Drive by **text/CLI** first. Goal: "tidy my desktop" runs autonomously through the *real agent*, fully undoable, with deletes blocked. **This proves the whole thesis.**
- **M2** — Wire into the shell: voice → agent → spoken result + undo.
- **M3** — On-screen narration: overlay/cursor sweeps affected files while it works (non-blocking). **The demo.**
- **M4** — Polish for the recording.
- **M5** — Package `.exe`. Must handle the SDK dependency (bundle/guide Node + Claude Code CLI, or an API-key path). Test on a clean machine.
- **M6** — Launch + 1–2 more markdown skills as follow-up content.

---

## 8. Safety model (the trust wedge, concretely)

Principle: **autonomous by default, reversible always, interrupt only when it can't be taken back.**

1. **Undo is the hero, not confirmation.** Runs like Clicky; every `REVERSIBLE` tool call is journaled; one word reverses the batch, across restarts.
2. **Confirmation is the rare exception** — only `DANGEROUS`/irreversible calls pause.
3. **The agent can't reach destructive ops** — toolset is constrained to the safe layer; raw shell/delete isn't exposed. Defense in depth with the permission hook.
4. **Policy is non-bypassable** — home-only roots, protected-path denylist, sensitive-file skip, batch caps, reuse Privacy-Guard for sensitive windows.
5. **Honest irreversibility** — no fake undo; irreversible = `DANGEROUS` and announced before acting.

The permission hook is now the **safety-critical code** — it gets the hardest tests, because the agent *will* attempt things and the hook is what stands between it and your data.

---

## 9. Risks specific to this scope

- **SDK setup friction (the #1 risk).** The Agent SDK needs the Claude Code CLI/Node + a Claude sign-in (free path) or an API key. Document both; the installer/README must make this painless or adoption dies at step one.
- **Permission-hook correctness is safety-critical.** A misclassified destructive call = data loss. Test adversarially; default-deny anything unrecognized.
- **Constrain the toolset for real** — if the agent can shell out, the safety guarantees leak. Lock tools down.
- **Packaging + SDK = a harder boss fight** than a plain app (see M5). Budget for it.
- **Move before official Windows ships.** The lane is open now.
- **Don't out-scope into heyclicky's breadth.** Two skills, flawless, beats ten that flake on camera.

---

## 10. v2+ (the payoff)

With the SDK + trust layer in place, new capabilities are mostly new markdown skills (+ a safe tool only when a new *kind* of action appears). Each new skill is a fresh launch post: "tidy my Downloads," "rename by content," "summarize the open PDF and draft an email" (that last one exercises the `DANGEROUS` send-gate — a great trust demo). The trust layer is the moat; skills are the content stream.
