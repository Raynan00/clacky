"""
undo.py — records executed moves and reverses the most recent batch.

A Batch is the unit of undo: "clean up my desktop" produces one Batch, and
"undo" reverses exactly that batch. Batches are also persisted to disk so undo
survives an app restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Where undo history lives (mirrors Clicky's %LOCALAPPDATA%\Clicky convention).
_HISTORY_DIR = Path.home() / ".clicky" / "organizer_history"


@dataclass
class MoveRecord:
    src: Path   # original location
    dst: Path   # where it ended up


@dataclass
class Batch:
    root: Path
    records: list[MoveRecord] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    when: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def result_summary(self) -> str:
        msg = f"Moved {len(self.records)} file(s)."
        if self.failures:
            msg += f" {len(self.failures)} could not be moved."
        return msg


def save(batch: Batch) -> Path:
    _HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _HISTORY_DIR / f"batch_{batch.when.replace(':', '-')}.json"
    payload = {
        "root": str(batch.root),
        "when": batch.when,
        "records": [{"src": str(r.src), "dst": str(r.dst)} for r in batch.records],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path


def _latest_batch_file() -> Path | None:
    if not _HISTORY_DIR.exists():
        return None
    files = sorted(_HISTORY_DIR.glob("batch_*.json"))
    return files[-1] if files else None


def undo_last() -> str:
    """Reverse the most recent saved batch. Returns a spoken-friendly summary."""
    latest = _latest_batch_file()
    if latest is None:
        return "There's nothing to undo."

    import shutil
    data = json.loads(latest.read_text())
    restored, failed = 0, 0
    # Reverse order so nested folders unwind cleanly.
    for rec in reversed(data["records"]):
        dst, src = Path(rec["dst"]), Path(rec["src"])
        try:
            if dst.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(dst), str(src))
                restored += 1
        except OSError:
            failed += 1
    latest.unlink(missing_ok=True)  # consume the batch once undone
    msg = f"Undone — restored {restored} file(s)."
    if failed:
        msg += f" {failed} could not be restored."
    return msg
