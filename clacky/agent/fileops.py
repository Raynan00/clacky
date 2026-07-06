"""
fileops.py — pure, safe filesystem primitives. No SDK, no side effects beyond
the explicit move. This is the only place that touches the disk, so all the
safety rules live here and are trivially testable.

Rules: move-only (never delete), home-folder-only, skip protected/sensitive
files, resolve name collisions instead of overwriting.
"""

from __future__ import annotations

import shutil
from pathlib import Path

MAX_BATCH = 200

_PROTECTED_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "system32",
    "appdata", "$recycle.bin", "boot", "perflogs", "programdata",
}
_PROTECTED_SUFFIXES = {".sys", ".dll", ".exe", ".lnk"}
_SENSITIVE_HINTS = ("password", "secret", ".env", "id_rsa", "wallet", "seed")


def _is_under_protected(path: Path) -> bool:
    parts = {p.lower() for p in path.parts}
    return bool(parts & _PROTECTED_DIR_NAMES)


def check_root(root: Path) -> tuple[bool, str]:
    """Validate the directory Clacky was asked to organize."""
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return False, f"{root} is not a directory"
    if _is_under_protected(root):
        return False, f"{root} is inside a protected system location"
    home = Path.home().resolve()
    if home != root and home not in root.parents:
        return False, f"{root} is outside your home folder"
    return True, ""


def check_move(src: Path, dst: Path, root: Path) -> tuple[bool, str]:
    """Validate a single proposed move. Returns (ok, reason-if-not)."""
    src = src.expanduser().resolve()
    root = root.expanduser().resolve()
    if not src.exists():
        return False, f"source no longer exists: {src.name}"
    if src.is_dir():
        return False, f"refusing to move a directory: {src.name}"
    if _is_under_protected(src) or _is_under_protected(dst):
        return False, "move touches a protected system location"
    if src.suffix.lower() in _PROTECTED_SUFFIXES:
        return False, f"protected file type: {src.suffix}"
    if any(h in src.name.lower() for h in _SENSITIVE_HINTS):
        return False, f"looks sensitive, skipping: {src.name}"
    # Destination must stay inside the organized root.
    try:
        dst.expanduser().resolve().relative_to(root)
    except ValueError:
        return False, "destination escapes the target folder"
    return True, ""


def resolve_collision(dst: Path) -> Path:
    """If dst exists, append ' (1)', ' (2)', … before the suffix."""
    if not dst.exists():
        return dst
    stem, suffix, parent = dst.stem, dst.suffix, dst.parent
    i = 1
    while True:
        cand = parent / f"{stem} ({i}){suffix}"
        if not cand.exists():
            return cand
        i += 1


def apply_move(src: Path, dst: Path) -> Path:
    """Execute a validated move. Creates parent dirs, avoids overwrite.
    Returns the final destination path."""
    final = resolve_collision(dst)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(final))
    return final
