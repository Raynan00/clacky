"""
executor.py — applies a confirmed Plan. Move-only, never delete.

Re-validates every move at execution time (the filesystem may have changed
since the plan was built), creates destination folders as needed, resolves
name collisions, and returns a Batch that undo.py can reverse.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from . import guards
from .planner import Plan
from .undo import Batch, MoveRecord


def _resolve_collision(dst: Path) -> Path:
    """If dst exists, append ' (1)', ' (2)', ... before the suffix."""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    i = 1
    while True:
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def execute(plan: Plan) -> Batch:
    batch = Batch(root=plan.root)
    for move in plan.moves:
        check = guards.is_safe_move(move.src, move.dst)  # re-check at run time
        if not check.allowed:
            batch.failures.append((move.src.name, check.reason))
            continue
        try:
            final_dst = _resolve_collision(move.dst)
            final_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move.src), str(final_dst))
            batch.records.append(MoveRecord(src=move.src, dst=final_dst))
        except OSError as e:
            batch.failures.append((move.src.name, str(e)))
    return batch
