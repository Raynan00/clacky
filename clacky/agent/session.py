"""
session.py — the active run's shared state. The SDK custom tool runs as a
module-level function (it can't take extra args), so it reads the current
Session here: the root being organized, dry-run flag, the collected plan
(dry-run), and the undo Batch (live run).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .journal import Batch


@dataclass
class Session:
    root: Path
    dry_run: bool = False
    plan: list[tuple[str, str]] = field(default_factory=list)   # (src_name, dest_folder)
    batch: Batch = field(default_factory=Batch)


_current: Session | None = None


def start(root: Path, dry_run: bool) -> Session:
    global _current
    _current = Session(root=Path(root).expanduser().resolve(), dry_run=dry_run)
    return _current


def current() -> Session:
    if _current is None:
        raise RuntimeError("No active Clacky session — call session.start() first.")
    return _current
