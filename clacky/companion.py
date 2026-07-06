"""
clacky.companion — launches the vendored companion shell as Clacky.

The shell (``clacky/shell/``, vendored from Bitshank-2338/clicky-windows, MIT)
uses top-level absolute imports (``from config import cfg``,
``from ai... import ...``) that resolve against its own directory. We add that
directory to ``sys.path`` and run its entry point exactly as ``python main.py``
would — keeping the vendored tree unmodified except for the rebrand.

Phase 1: this launches the shell as a *guide* — it sees the screen, talks, and
points. The computer-acting agent loop (``clacky/agent/computer_loop.py``)
replaces the shell's one-shot LLM call in Phase 2; see ``docs/AGENT_PLAN.md``.
"""

from __future__ import annotations

import os
import runpy
import sys

SHELL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shell")
_ENTRY = os.path.join(SHELL_DIR, "main.py")


def launch() -> int:
    """Run the companion shell. Returns a process exit code on early failure;
    on success the shell's own Qt loop owns the process until the user quits."""
    # Packaged (.exe): the shell's modules are bundled as top-level names and its
    # `main.py` lives in the archive, not on disk — so import and run it directly
    # instead of runpy'ing a loose file (which doesn't exist when frozen).
    if getattr(sys, "frozen", False):
        try:
            import main as _shell_main  # bundled top-level; see clacky.spec
        except ImportError as e:
            missing = getattr(e, "name", None) or "a dependency"
            print(f"Clacky: shell import failed (missing: {missing}).", file=sys.stderr)
            return 3
        _shell_main.main()
        return 0

    # Dev checkout: run main.py as a script from the shell directory.
    if not os.path.isfile(_ENTRY):
        print("Clacky: companion shell not found at clacky/shell/.", file=sys.stderr)
        return 1

    if SHELL_DIR not in sys.path:
        sys.path.insert(0, SHELL_DIR)

    try:
        runpy.run_path(_ENTRY, run_name="__main__")
    except ImportError as e:
        missing = getattr(e, "name", None) or "a dependency"
        print(
            "Clacky: the companion shell needs its dependencies.\n"
            '       pip install -e ".[shell]"\n'
            f"       (missing: {missing})",
            file=sys.stderr,
        )
        return 3
    return 0
