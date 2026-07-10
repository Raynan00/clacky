# Contributing to Clacky 🧤

Thanks for being here — this is an early build, and issues + PRs are the
fastest way to shape it.

## The 5-minute contribution: a skill

Skills are folders with a `SKILL.md` inside — the same open
[Agent Skills](https://agentskills.io) format used by Claude, Hermes, and
OpenClaw:

```
my-skill/
  SKILL.md
```

```markdown
---
name: my skill
description: One line about what it does.
---

# my skill

When the user asks to run "my skill", do the following:

1. Open ...
2. Then ...
```

Try it locally by dropping the folder into `~/.clacky/skills/` — Clacky picks
it up on the next turn (say the skill's name). To share it, PR the folder
into `skills/community/` in this repo.

## Code contributions

- **Setup:** `pip install -e ".[shell,claude,dev]"` on Windows 10/11, then
  `clacky run`. Full guide: [docs/USAGE.md](docs/USAGE.md).
- **Layout:** the voice companion lives in `clacky/shell/` — start with
  `routing.py` (intent), `tour.py` (pointing), `actions.py` (acting),
  `harness.py` (background agents). Design notes live in each module's
  docstring and [clacky/shell/VENDORED.md](clacky/shell/VENDORED.md).
- **Tests:** `python -m pytest -q` must stay green (CI runs it on every PR).
  The organizer/agent core is well-tested; the shell is verified by live runs
  — a shell test harness is a very welcome contribution.
- **Voice-path changes:** please include a quick note on how you live-tested
  (what you said, what she did) — the session log (`~/.clacky/logs/session.log`)
  makes great PR evidence.

## Bugs

Open an issue with your `session.log` excerpt (it's a timestamped flight
recorder — HEAR/ROUTE/POINT/ACT lines tell us almost everything). Never paste
your API keys.

## Ground rules

Be kind, credit generously, and keep the safety invariants: file operations
stay move-only + journaled, and nothing bypasses the undo path.
