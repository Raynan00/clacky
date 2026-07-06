"""
Headless test of the Phase-2 narration loop: a fake model_call scripts a
point -> say -> finish sequence; recording tool executors confirm the loop runs
each step through the gate, in order, and stops on finish. No Anthropic, no GUI.
"""

import asyncio

from clacky.agent.narrator import run_narration
from clacky.agent.permission import Action, Risk, classify_action


def _run(coro):
    return asyncio.run(coro)


def test_narration_tools_are_safe():
    # The narration tools must classify SAFE — they never touch the machine.
    assert classify_action(Action(kind="point", target_label="Save")) is Risk.SAFE
    assert classify_action(Action(kind="say")) is Risk.SAFE


def test_loop_runs_point_then_say_then_finishes():
    # Script three assistant turns: point, say, finish.
    turns = [
        [{"type": "tool_use", "id": "t1", "name": "point",
          "input": {"label": "Sign in", "note": "that's the sign-in button"}}],
        [{"type": "tool_use", "id": "t2", "name": "say",
          "input": {"text": "you're already logged in though"}}],
        [{"type": "tool_use", "id": "t3", "name": "finish", "input": {}}],
    ]
    seq = iter(turns)

    async def model_call(_messages):
        return next(seq)

    pointed, said = [], []

    async def point_fn(label, note):
        pointed.append((label, note))
        return True

    async def say_fn(text):
        said.append(text)

    out = _run(run_narration(model_call, point_fn, say_fn, initial_user_content=[]))

    assert out.stopped == "finish"
    assert out.steps == 2
    assert pointed == [("Sign in", "that's the sign-in button")]
    assert said == ["you're already logged in though"]


def test_loop_respects_max_steps():
    # A model that keeps pointing forever must be capped.
    async def model_call(_messages):
        return [{"type": "tool_use", "id": "x", "name": "point",
                 "input": {"label": "thing", "note": "a thing"}}]

    async def point_fn(label, note):
        return True

    async def say_fn(text):
        pass

    out = _run(run_narration(model_call, point_fn, say_fn, initial_user_content=[], max_steps=3))
    assert out.stopped == "max_steps"
    assert out.steps == 3


def test_missing_element_reports_back_not_crash():
    turns = [
        [{"type": "tool_use", "id": "t1", "name": "point",
          "input": {"label": "Nonexistent", "note": "..."}}],
        [{"type": "tool_use", "id": "t2", "name": "finish", "input": {}}],
    ]
    seq = iter(turns)

    async def model_call(_messages):
        return next(seq)

    async def point_fn(label, note):
        return False   # UIA couldn't find it

    async def say_fn(text):
        pass

    out = _run(run_narration(model_call, point_fn, say_fn, initial_user_content=[]))
    assert out.stopped == "finish"   # loop continued past the miss, didn't crash
