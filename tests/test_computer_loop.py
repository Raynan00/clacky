"""
Exercises the agent's trust chokepoint headlessly: every proposed action goes
through classify_action, and only authorized ones reach the actuator. Uses the
RecordingActuator so there's no real desktop and no risk.
"""

from clacky.agent.actuation import RecordingActuator
from clacky.agent.computer_loop import ComputerAgent, Narrator
from clacky.agent.permission import Action


class _Resolver:
    def describe_at(self, x, y):
        return ("", "")


def _agent(confirm_returns):
    rec = RecordingActuator()
    narr = Narrator(
        point_at=lambda *a: None,
        say=lambda *a: None,
        confirm=lambda prompt: confirm_returns,
    )
    return ComputerAgent(rec, _Resolver(), narr), rec


def test_caution_action_runs_without_confirm():
    agent, rec = _agent(confirm_returns=False)
    ran = agent._authorize_and_run(
        Action(kind="left_click", target_label="Next", target_role="Button"), 10, 20
    )
    assert ran and rec.calls == [("left_click", 10, 20)]


def test_dangerous_action_blocked_when_user_declines():
    agent, rec = _agent(confirm_returns=False)
    ran = agent._authorize_and_run(
        Action(kind="left_click", target_label="Send", target_role="Button"), 5, 5
    )
    assert not ran and rec.calls == []      # nothing reached the machine


def test_dangerous_action_runs_only_after_explicit_confirm():
    agent, rec = _agent(confirm_returns=True)
    ran = agent._authorize_and_run(
        Action(kind="left_click", target_label="Send", target_role="Button"), 5, 5
    )
    assert ran and rec.calls == [("left_click", 5, 5)]


def test_typing_dispatches_text():
    agent, rec = _agent(confirm_returns=False)
    agent._authorize_and_run(Action(kind="type", text="hello"))
    assert rec.calls == [("type_text", "hello")]
