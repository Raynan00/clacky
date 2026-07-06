"""
Adversarial tests for the GUI-action trust classifier — the safety-critical
code. The agent *will* attempt things; these pin down what runs autonomously
versus what must pause for a confirm. Bias every uncertain case toward
DANGEROUS.
"""

from clacky.agent.permission import (
    Action,
    Risk,
    classify_action,
    classify_move,
    is_allowed,
    needs_confirm,
)


# ── files path stays intact (the old green path) ───────────────────────────

def test_files_risk_model_unchanged():
    assert classify_move() is Risk.REVERSIBLE
    assert is_allowed(Risk.SAFE) and is_allowed(Risk.REVERSIBLE)
    assert not is_allowed(Risk.DANGEROUS)


# ── read-only actions are SAFE ─────────────────────────────────────────────

def test_readonly_actions_are_safe():
    for kind in ("screenshot", "mouse_move", "scroll", "cursor_position", "wait"):
        assert classify_action(Action(kind=kind)) is Risk.SAFE


# ── high-stakes targets are DANGEROUS regardless of action kind ────────────

def test_danger_labels_are_dangerous():
    for label in ("Send", "Delete Forever", "Buy now", "Confirm payment",
                  "Move to Trash", "Post", "Unsubscribe", "Delete account"):
        a = Action(kind="left_click", target_label=label, target_role="Button")
        assert classify_action(a) is Risk.DANGEROUS, label
        assert needs_confirm(classify_action(a))


# ── a click we can't identify is default-denied ────────────────────────────

def test_unidentified_click_is_dangerous():
    # No UIA label resolved → we can't reason about consequences → confirm.
    assert classify_action(Action(kind="left_click")) is Risk.DANGEROUS
    assert classify_action(Action(kind="double_click", target_label="")) is Risk.DANGEROUS


# ── routine, identified GUI mutations are CAUTION (run, but narrate) ────────

def test_routine_mutations_are_caution():
    benign_clicks = ("Save Draft", "Next", "Reply", "New Tab", "Bold")
    for label in benign_clicks:
        r = classify_action(Action(kind="left_click", target_label=label, target_role="Button"))
        assert r is Risk.CAUTION, label
        assert is_allowed(r) and not needs_confirm(r)


def test_typing_is_not_judged_by_content():
    # Typing the word "delete" into a field is NOT dangerous — danger is a
    # property of the *target control*, never of typed text.
    a = Action(kind="type", text="please delete the old draft and send later")
    assert classify_action(a) is Risk.CAUTION
    assert not needs_confirm(classify_action(a))


def test_keypress_without_target_is_routine():
    assert classify_action(Action(kind="key", text="ctrl+a")) is Risk.CAUTION
