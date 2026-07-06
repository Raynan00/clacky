"""
organize_desktop.py — Clicky skill: voice-driven desktop tidying with preview + undo.

Drop in your skills directory (Clicky auto-loads it). Matches phrases like
"clean up my desktop" / "tidy this up" and "undo that".

The skill is a thin adapter: it routes voice -> the `organizer` package, builds a
DRY-RUN plan, asks the user to confirm, then executes. No files move without
confirmation.

NOTE: `manager` is Clicky's CompanionManager. The attribute names below
(llm.complete, speak, ask_confirm) are placeholders — map them to whatever the
base actually exposes when you wire it in (M2/M3 in the build plan).
"""

from __future__ import annotations

import re
from pathlib import Path

from organizer import build_plan, execute, undo
from organizer.undo import save as save_batch

DESKTOP = Path.home() / "Desktop"


def _make_llm_complete(manager):
    """Adapt Clicky's active LLM provider into the planner's expected callable:
    (system, user) -> text. Adjust to the real provider API."""
    def complete(system: str, user: str) -> str:
        # e.g. return manager.llm.complete(system=system, user=user, json_mode=True)
        return manager.llm.complete(system=system, user=user)
    return complete


# ---- handlers -------------------------------------------------------------

async def organize(transcript: str, manager) -> str:
    # 1. DRY RUN — build a plan, move nothing yet.
    try:
        plan = build_plan(DESKTOP, _make_llm_complete(manager))
    except ValueError as e:
        return f"I couldn't plan that safely: {e}"

    if not plan.moves:
        return "Your desktop already looks tidy — nothing to move."

    # 2. PREVIEW + CONFIRM. (In M3, also drive the cursor/annotation overlay here.)
    preview = plan.summary()
    confirmed = await manager.ask_confirm(
        f"Here's what I'd do:\n{preview}\nSay 'yes' to go ahead."
    )
    if not confirmed:
        return "Okay, I left everything where it was."

    # 3. EXECUTE + record for undo.
    batch = execute(plan)
    save_batch(batch)
    return f"{batch.result_summary()} Say 'undo' to reverse it."


async def undo_organize(transcript: str, manager) -> str:
    return undo.undo_last()


# ---- skill registrations (Clicky's expected format) -----------------------

ORGANIZE_SKILL = {
    "name": "organize_desktop",
    "trigger": r"(clean|tidy|organi[sz]e)\b.*\b(desktop|this|up|files?)",
    "description": "Organizes the desktop into folders, with a preview and undo.",
    "handler": organize,
}

UNDO_SKILL = {
    "name": "undo_organize",
    "trigger": r"\bundo\b.*\b(that|move|organi[sz]e|clean)?",
    "description": "Reverses the last organize action.",
    "handler": undo_organize,
}

# If the loader expects one SKILL per file, split this into two files and put
# one dict in each. Kept together here for readability.
SKILLS = [ORGANIZE_SKILL, UNDO_SKILL]
