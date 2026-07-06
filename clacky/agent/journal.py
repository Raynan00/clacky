"""
journal.py — persistent undo journal. One agent task = one Batch. "clacky undo"
reverses the most recent batch. Batches persist to disk so undo survives a
restart. Pure (no SDK).
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HISTORY_DIR = Path.home() / ".clacky" / "history"


@dataclass
class MoveRecord:
    src: Path   # original location
    dst: Path   # where it ended up


@dataclass
class Batch:
    records: list[MoveRecord] = field(default_factory=list)
    when: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def add(self, src: Path, dst: Path) -> None:
        self.records.append(MoveRecord(Path(src), Path(dst)))

    def summary(self) -> str:
        return f"moved {len(self.records)} file(s)"


def save(batch: Batch) -> Path | None:
    if not batch.records:
        return None
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"batch_{batch.when.replace(':', '-')}.json"
    path.write_text(json.dumps({
        "when": batch.when,
        "records": [{"src": str(r.src), "dst": str(r.dst)} for r in batch.records],
    }, indent=2))
    return path


def _latest() -> Path | None:
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("batch_*.json"))
    return files[-1] if files else None


def undo_last(move_delay: float = 0.0) -> str:
    """Reverse the most recent saved batch. Returns a human-friendly summary.
    `move_delay` staggers the restores (cosmetic — files visibly march back)."""
    import time as _time
    latest = _latest()
    if latest is None:
        return "Nothing to undo."
    data = json.loads(latest.read_text())
    restored, failed = 0, 0
    for rec in reversed(data["records"]):       # reverse order unwinds cleanly
        dst, src = Path(rec["dst"]), Path(rec["src"])
        try:
            if dst.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                restored += 1
                if move_delay > 0:
                    _time.sleep(move_delay)
        except OSError:
            failed += 1
    latest.unlink(missing_ok=True)              # consume once undone
    msg = f"Undone — restored {restored} file(s)."
    if failed:
        msg += f" {failed} could not be restored."
    return msg
