"""
runtime.py — the orchestrator. Replaces the old SDK/agent-loop version.

Flow (no Claude Code, no agent autonomy):
    check root → planner.build_plan(provider) → preview OR execute via
    safe_fs → journal for undo.

The LLM only proposes a JSON plan; this code is the only thing that moves
files, and every move is validated again and recorded for undo.
"""

from __future__ import annotations

from pathlib import Path

from . import fileops, journal, planner, session
from .permission import Risk, classify_move
from ..providers.base import LLMProvider


def run_organize(root: Path, provider: LLMProvider, dry_run: bool,
                 move_delay: float = 0.0) -> tuple[session.Session, planner.Plan]:
    """Plan and (unless dry_run) apply an organization of `root`.

    `move_delay` staggers the moves (seconds between files). Purely cosmetic —
    the voice path uses ~0.12s so icons visibly vanish one by one, which reads
    as live work on camera instead of a jump cut. CLI default is 0 (instant)."""
    ok, reason = fileops.check_root(Path(root))
    if not ok:
        raise ValueError(reason)

    plan = planner.build_plan(root, provider)
    sess = session.start(root=plan.root, dry_run=dry_run)

    if dry_run:
        sess.plan = [(m.name, m.dest_folder) for m in plan.moves]
        return sess, plan

    import time as _time
    for m in plan.moves:
        # Every move is REVERSIBLE by classification; re-validate at apply time
        # (the filesystem may have changed since planning).
        assert classify_move() is Risk.REVERSIBLE
        src = plan.root / m.name
        ok, _ = fileops.check_move(src, plan.root / m.dest_folder / m.name, plan.root)
        if not ok:
            continue
        final = fileops.apply_move(src, plan.root / m.dest_folder / m.name)
        sess.batch.add(src, final)
        if move_delay > 0:
            _time.sleep(move_delay)

    journal.save(sess.batch)
    return sess, plan
