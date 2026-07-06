"""
computer_loop.py — the agent brain (Phase 2 scaffold).

This is the surgical replacement for Bitshank's one-shot LLM call. In the base
shell, ``companion_manager._end_capture_and_process`` ends in:

    async for chunk in self._get_llm().stream_response(...)   # one screenshot → one reply

(see companion_manager.py around lines 731 and 1024). That is a *guide*: it
points and talks, but never acts. Clacky replaces that single call with an
agentic **observe → decide → act → observe** loop driven by Claude's Computer
Use tool — the one change that turns the Windows port from a guide into hands.

Why a loop (and why this reverses the repo's v3 "one structured call" design):
GUI automation cannot be done in a single call. Each step is a screenshot in,
a tool action out, executed on the machine, then a fresh screenshot — repeated
until the task is done.

The trust layer is non-bypassable: EVERY proposed action passes through
``permission.classify_action`` before it can reach the ``Actuator``. SAFE /
REVERSIBLE / CAUTION run autonomously (CAUTION is narrated first); DANGEROUS
pauses for an explicit confirm. File mutations still go through the safe
``fileops`` layer, never raw input.

Status: scaffold. The Anthropic client call and the Bitshank shell wiring land
in Phase 2; this file fixes the control flow and the seams so the trust gate,
actuation, and narration plug in cleanly.

Grounding notes (verify at build time — these move):
  • Computer Use is a CLIENT-SIDE beta tool: Claude emits actions, this loop
    executes them and sends back the next screenshot.
  • Bitshank already calls Computer Use to *locate* a coordinate
    (ai/element_locator.py) with beta header "computer-use-2025-11-24" — extend
    "locate" into "act", don't rebuild it.
  • Model: claude-opus-4-8 for demo quality (pixel-accurate coords, high-res
    vision); claude-sonnet-4-6 for cheaper dev. Adaptive thinking + high effort
    for best computer-use accuracy. Screenshots cost image tokens — send ~1080p.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from .actuation import Actuator
from .permission import Action, Risk, classify_action, needs_confirm


class TargetResolver(Protocol):
    """Resolves the UIA element under an action point so the trust classifier
    can reason about it. Implemented by Bitshank's ai/hybrid_pointer.py
    (Tier 1 = Windows UI Automation): given a point, return (label, role)."""

    def describe_at(self, x: int, y: int) -> tuple[str, str]: ...


@dataclass
class Narrator:
    """Hooks back into the companion shell so the buddy can speak/point/flag
    while the agent works. Wire these to the manager's Qt signals
    (sig_point_at / TTS) — narration is what makes the latency feel intentional
    and what surfaces a pending DANGEROUS confirm on screen."""

    point_at: Callable[[int, int, str], None] = lambda x, y, label: None
    say: Callable[[str], None] = lambda text: None
    confirm: Callable[[str], bool] = lambda prompt: False   # returns user's yes/no


class ComputerAgent:
    """Runs one task to completion: observe screen → Claude decides → gate →
    act → repeat. The brain is Claude Computer Use; this class owns the trust
    gate, actuation, and narration."""

    def __init__(
        self,
        actuator: Actuator,
        resolver: TargetResolver,
        narrator: Optional[Narrator] = None,
        model: str = "claude-opus-4-8",
    ) -> None:
        self.actuator = actuator
        self.resolver = resolver
        self.narrator = narrator or Narrator()
        self.model = model

    # -- the gate: the only path from a proposed action to the machine --------

    def _authorize_and_run(self, action: Action, x: int = 0, y: int = 0) -> bool:
        """Classify one action and either run it, narrate+run it, or pause for
        confirm. Returns True if it executed. This is the chokepoint — there is
        no other route from the agent to the Actuator."""
        risk = classify_action(action)

        if needs_confirm(risk):
            ok = self.narrator.confirm(
                f"About to {action.kind} on '{action.target_label or 'an unknown target'}'. "
                f"This can't be undone — go ahead?"
            )
            if not ok:
                self.narrator.say("Skipped — left that one for you.")
                return False
        elif risk is Risk.CAUTION:
            # Irreversible but routine → announce before acting (no undo).
            self.narrator.say(f"{action.kind} on {action.target_label or 'that'}.")

        self._dispatch(action, x, y)
        return True

    def _dispatch(self, action: Action, x: int, y: int) -> None:
        kind = action.kind.lower()
        if kind in ("left_click", "click"):
            self.actuator.left_click(x, y)
        elif kind == "double_click":
            self.actuator.double_click(x, y)
        elif kind == "right_click":
            self.actuator.right_click(x, y)
        elif kind in ("mouse_move", "cursor_move", "move"):
            self.actuator.move(x, y)
        elif kind == "type":
            self.actuator.type_text(action.text)
        elif kind == "key":
            self.actuator.key(action.text)
        # screenshot/scroll/etc. handled by the loop's observe step (Phase 2).

    # -- the loop -------------------------------------------------------------

    def run(self, instruction: str) -> str:
        """observe → decide (Claude) → gate → act → repeat, until done.

        Phase 2: drive Claude's Computer Use tool with the Anthropic SDK in a
        manual agentic loop (screenshot as an image block in; tool_use action
        out; handle pause_turn; resolve each action's target via
        self.resolver; gate via _authorize_and_run; loop on the next
        screenshot). Wire screenshots from screen/capture.py and narration from
        the manager's Qt signals.
        """
        raise NotImplementedError(
            "ComputerAgent.run is Phase 2 — connect the Anthropic Computer Use "
            "loop and the Bitshank shell. The trust gate (_authorize_and_run) "
            "and actuation are ready and unit-tested; this wires them to Claude."
        )
