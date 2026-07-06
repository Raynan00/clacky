"""
permission.py — the trust layer (the differentiator).

This module is now load-bearing. In the files-only world a move was always
REVERSIBLE, so classification was almost a formality. For a *computer-acting*
agent (Claude Computer Use driving the GUI) it is the safety-critical core:
every action the agent proposes is classified BEFORE it runs, and the
classification decides whether it executes autonomously or pauses for an
explicit, separate confirmation.

The honest reality of GUI actions: **most are not reversible.** You cannot
un-send an email, un-click Buy, or un-type into a field. So the model has four
tiers, and "undo" is no longer the hero — *confirm-before-irreversible* and
*narrate-before-acting* are:

    SAFE       read-only (screenshot, cursor move, scroll)         → run
    REVERSIBLE undoable mutation (a file move; journaled)          → run + journal
    CAUTION    irreversible but routine GUI mutation (click a      → run, but
               normal button, type into a field) — no real undo       narrate first
    DANGEROUS  irreversible + high-stakes (Send/Delete/Buy/…, or   → PAUSE + confirm
               a click whose target we could not identify)

Default-deny posture: a click whose target element we could not resolve via
the Windows UIA tree is treated as DANGEROUS — if we can't reason about what a
click does, we don't do it autonomously.

The reversible file skill keeps its real undo via ``journal.py``; that is the
one capability where "say undo" still means something.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Risk(Enum):
    SAFE = "safe"             # read-only / no mutation                 → run
    REVERSIBLE = "reversible" # mutates, but undoable (file moves)      → run + journal
    CAUTION = "caution"       # irreversible, routine GUI mutation      → run + narrate
    DANGEROUS = "dangerous"   # irreversible + high-stakes / unknown    → confirm first


# ── files skill (unchanged) ────────────────────────────────────────────────

def classify_move() -> Risk:
    """A file move is always reversible (we journal src→dst and can move back)."""
    return Risk.REVERSIBLE


def is_allowed(risk: Risk) -> bool:
    """Whether an action may run autonomously (without a separate confirm).

    SAFE / REVERSIBLE / CAUTION run on their own; DANGEROUS must be confirmed.
    """
    return risk in (Risk.SAFE, Risk.REVERSIBLE, Risk.CAUTION)


def needs_confirm(risk: Risk) -> bool:
    """True only for actions that must pause for an explicit user confirmation."""
    return risk is Risk.DANGEROUS


# ── computer-use agent (the new safety-critical path) ──────────────────────

# Read-only / navigational actions — no state change, always safe to run.
# Includes the Phase-2 narration tools (point / say): they move the overlay
# cursor and speak, but never touch the user's machine or apps.
_READONLY_KINDS = frozenset({
    "screenshot", "cursor_position", "mouse_move", "cursor_move",
    "scroll", "hover", "wait",
    "point", "say", "narrate", "caption",
})

# Mutating actions that act *at a point* — they need a known target element to
# be reasoned about. A click on an unidentified target is default-denied.
_TARGETED_KINDS = frozenset({
    "left_click", "double_click", "triple_click", "right_click",
    "middle_click", "left_mouse_down", "left_mouse_up", "drag", "left_click_drag",
})

# Substrings in a target element's name/role that mark an irreversible,
# high-stakes control. Matched case-insensitively against the UIA label.
# Keep conservative and additive — a false DANGEROUS only costs one confirm;
# a false SAFE could send an email.
DANGER_LABEL_PATTERNS = (
    "send", "delete", "remove", "discard", "trash", "erase", "wipe",
    "buy", "purchase", "pay", "checkout", "place order", "order now",
    "submit", "confirm", "publish", "post", "share", "uninstall",
    "format", "permanent", "delete forever", "move to trash", "empty",
    "unsubscribe", "deactivate", "close account", "delete account",
)


@dataclass(frozen=True)
class Action:
    """A single GUI action the agent proposes, before execution.

    ``target_label`` / ``target_role`` come from the Windows UIA element under
    the action point — Bitshank's ``ai/hybrid_pointer.py`` already resolves
    this (``Target.label`` / ``Target.source``). When the target can't be
    resolved, leave them empty and the classifier defaults to DANGEROUS for
    clicks.
    """
    kind: str                 # e.g. "left_click" | "type" | "key" | "screenshot"
    target_label: str = ""    # UIA element name/text under the action point
    target_role: str = ""     # UIA control type (Button, Hyperlink, Edit, …)
    text: str = ""            # payload for "type" actions (NOT scanned for danger)


def classify_action(action: Action) -> Risk:
    """Classify one proposed GUI action. The single chokepoint between the
    agent and the user's machine.

    Note: ``text`` (what the agent wants to type) is deliberately NOT scanned
    for danger words — typing the word "delete" into a document is not
    dangerous; clicking a button *labelled* Delete is. Danger is a property of
    the target control, not of typed content.
    """
    kind = action.kind.strip().lower()

    if kind in _READONLY_KINDS:
        return Risk.SAFE

    label = (action.target_label or "").lower()
    role = (action.target_role or "").lower()
    haystack = f"{label} {role}"

    if any(p in haystack for p in DANGER_LABEL_PATTERNS):
        return Risk.DANGEROUS

    # A click/drag whose target we couldn't identify via UIA → we can't reason
    # about its consequences → default-deny (confirm). Typing and key presses
    # without a resolved target are routine (CAUTION), not click-through.
    if kind in _TARGETED_KINDS and not label:
        return Risk.DANGEROUS

    # Routine, identified GUI mutation (type into a field, click a benign
    # button). Runs autonomously, but must be narrated first — there is no undo.
    return Risk.CAUTION
