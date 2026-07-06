"""
guards.py — safety checks for the file organizer.

The organizer NEVER deletes and NEVER touches protected locations. Every move
is validated here before it is allowed into a plan or executed. This module is
pure (no side effects) so it is trivially unit-testable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Hard cap so a single "clean up" can't move thousands of files in one batch.
MAX_BATCH = 200

# Directories that must never be the source or destination of a move.
# Extend as needed; these are deliberately conservative.
_PROTECTED_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "system32",
    "appdata", "$recycle.bin", "boot", "perflogs", "programdata",
}

# Filenames / patterns we refuse to move (sensitive or system-critical).
_PROTECTED_FILE_SUFFIXES = {".sys", ".dll", ".lnk"}  # .lnk = shortcuts; opt-in to move later
_SENSITIVE_NAME_HINTS = ("password", "secret", ".env", "id_rsa", "wallet")


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str = ""


def _is_under_protected_dir(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return bool(parts & _PROTECTED_DIR_NAMES)


def is_safe_root(root: Path) -> GuardResult:
    """Validate the directory the user asked us to organize."""
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return GuardResult(False, f"{root} is not a directory")
    if _is_under_protected_dir(root):
        return GuardResult(False, f"{root} is inside a protected system location")
    # For v1 we only allow organizing inside the user's home tree (e.g. Desktop).
    home = Path.home().resolve()
    if home not in root.parents and root != home:
        return GuardResult(False, f"{root} is outside your home folder")
    return GuardResult(True)


def is_safe_move(src: Path, dst: Path) -> GuardResult:
    """Validate a single proposed move. Called for every file in a plan."""
    src = src.expanduser().resolve()
    dst = dst.expanduser()

    if not src.exists():
        return GuardResult(False, f"source no longer exists: {src.name}")
    if src.is_dir():
        return GuardResult(False, f"refusing to move a directory in v1: {src.name}")
    if _is_under_protected_dir(src) or _is_under_protected_dir(dst):
        return GuardResult(False, "move touches a protected system location")
    if src.suffix.lower() in _PROTECTED_FILE_SUFFIXES:
        return GuardResult(False, f"protected file type: {src.suffix}")
    if any(hint in src.name.lower() for hint in _SENSITIVE_NAME_HINTS):
        return GuardResult(False, f"looks sensitive, skipping: {src.name}")
    return GuardResult(True)


def enforce_batch_size(n_moves: int) -> GuardResult:
    if n_moves > MAX_BATCH:
        return GuardResult(False, f"plan has {n_moves} moves, over the {MAX_BATCH} cap")
    return GuardResult(True)
