"""
narrator.py — Phase 2, step 1: the agentic narration + pointing loop.

This replaces the one-shot "reply with [POINT] tags" pipeline with a real loop:
Claude looks at the screen, points at the single most relevant element, says one
short spoken line about it, then decides whether there's a next thing worth
showing — weaving cursor and voice the way a person walking you through a screen
would. That loop is what makes pointing feel *intelligent* instead of a blind
one-shot guess.

Read-only for now: the only tools are `point` and `say` (both SAFE). Nothing on
the machine is touched — clicking/typing come next, gated as DANGEROUS.

Every tool call passes through the trust gate (`classify_action`) before it runs
— the same non-bypassable chokepoint the file skill and the computer agent use.

The core loop here is provider-agnostic and pure orchestration: the caller
injects `model_call` (one Claude tool-use turn) and the tool executors
(`point_fn` / `say_fn`), so this is unit-testable headlessly with fakes. The
shell adapter wires `model_call` to the Anthropic client and the tool executors
to the UIA pointer + overlay + TTS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .permission import Action, Risk, classify_action, needs_confirm

NARRATE_SYSTEM = (
    "You are Clacky, walking the user through what's on their screen, out loud, in "
    "a LOOP. Each step: point at the SINGLE most relevant element and say ONE "
    "short spoken sentence about it, then decide if there's a next thing worth "
    "showing. Keep it flowing and brief — a few steps, never a lecture. Use the "
    "element's EXACT on-screen text as the label. Speak for the ear; never say "
    "coordinates or the word 'screenshot'. When the walkthrough is complete, call "
    "finish."
)

# Anthropic tool-use schema. The shell passes these as `tools=`.
NARRATE_TOOLS = [
    {
        "name": "point",
        "description": ("Fly the cursor to an on-screen element (identified by its "
                        "EXACT visible text) and say one short spoken sentence about it."),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string",
                          "description": "the element's exact on-screen text, e.g. 'Sign in'"},
                "note": {"type": "string", "description": "one short spoken sentence about it"},
            },
            "required": ["label", "note"],
            "additionalProperties": False,
        },
    },
    {
        "name": "say",
        "description": "Say one short spoken sentence without pointing (context, or when nothing needs a point).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "finish",
        "description": "Call when the walkthrough is complete and there's nothing more worth showing.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


@dataclass
class NarrateOutcome:
    steps: int              # how many point/say steps actually ran
    stopped: str            # "finish" | "cancelled" | "max_steps" | "end" | "blocked"


# model_call(messages) -> the assistant's content as a list of plain dict blocks
# (each {"type": "text"|"tool_use", ...}). The shell converts SDK blocks to dicts.
ModelCall = Callable[[list], Awaitable[list]]
PointFn = Callable[[str, str], Awaitable[bool]]   # (label, note) -> found & pointed?
SayFn = Callable[[str], Awaitable[None]]           # (text) -> spoken


async def run_narration(
    model_call: ModelCall,
    point_fn: PointFn,
    say_fn: SayFn,
    initial_user_content: list,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    max_steps: int = 6,
) -> NarrateOutcome:
    """Drive the observe → point/say → decide loop until Claude calls finish
    (or we hit max_steps / the user barges in). Returns what happened."""
    messages = [{"role": "user", "content": initial_user_content}]
    steps = 0

    for _turn in range(max_steps + 2):
        if cancelled():
            return NarrateOutcome(steps, "cancelled")

        content = await model_call(messages)
        messages.append({"role": "assistant", "content": content})

        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if not tool_uses:
            return NarrateOutcome(steps, "end")   # Claude just talked, no tool → done

        results = []
        for tu in tool_uses:
            name = tu.get("name")
            inp = tu.get("input") or {}
            tuid = tu.get("id")

            if name == "finish":
                return NarrateOutcome(steps, "finish")

            if name == "point":
                label = str(inp.get("label", "")).strip()
                note = str(inp.get("note", "")).strip()
                action = Action(kind="point", target_label=label)
                if _blocked(action):
                    results.append(_result(tuid, "blocked by safety policy", is_error=True))
                    continue
                found = await point_fn(label, note)
                results.append(_result(tuid, "pointed" if found else "couldn't find that element"))
                steps += 1
            elif name == "say":
                text = str(inp.get("text", "")).strip()
                if _blocked(Action(kind="say")):
                    results.append(_result(tuid, "blocked by safety policy", is_error=True))
                    continue
                await say_fn(text)
                results.append(_result(tuid, "said"))
                steps += 1
            else:
                results.append(_result(tuid, f"unknown tool: {name}", is_error=True))

            if steps >= max_steps:
                # Let the model know it's out of steps; it should wrap up.
                messages.append({"role": "user", "content": results})
                return NarrateOutcome(steps, "max_steps")

        messages.append({"role": "user", "content": results})

    return NarrateOutcome(steps, "max_steps")


def _blocked(action: Action) -> bool:
    """The trust chokepoint. Narration actions are SAFE; anything that would be
    DANGEROUS (a future click/type) is blocked here until a confirm flow exists."""
    return needs_confirm(classify_action(action))


def _result(tool_use_id, text: str, *, is_error: bool = False) -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
    if is_error:
        block["is_error"] = True
    return block
