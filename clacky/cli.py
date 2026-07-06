"""
Clacky CLI.

    clacky organize [PATH]          tidy a folder (default: Desktop)
        -n / --dry-run             show the plan without moving anything
        -p / --provider NAME       claude | openai | gemini | ollama | heuristic
        -m / --model NAME          override the model for that provider
    clacky undo                     reverse the last organize
    clacky --version

Autonomous by default (it just does it); every move is journaled so `clacky
undo` puts it all back. No Claude Code — uses your chosen provider's API key,
runs fully local/free via Ollama, or zero-config via the heuristic sorter.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, config
from .agent import journal


def _default_target() -> Path:
    return Path.home() / "Desktop"


def _cmd_organize(args) -> int:
    from .agent.runtime import run_organize
    from .providers import get_provider

    root = Path(args.path).expanduser() if args.path else _default_target()
    provider_name = args.provider or config.active_provider()

    try:
        provider = get_provider(provider_name, model=args.model)
    except (RuntimeError, ValueError) as e:
        print(f"Clacky: {e}", file=sys.stderr)
        return 2
    except ImportError:
        pkg = {"claude": "anthropic", "openai": "openai",
               "gemini": "google-generativeai"}.get(provider_name, provider_name)
        print(f"Clacky: the '{provider_name}' provider needs its library.\n"
              f"       pip install {pkg}", file=sys.stderr)
        return 3

    try:
        sess, plan = run_organize(root, provider, dry_run=args.dry_run)
    except ValueError as e:
        print(f"Clacky: {e}", file=sys.stderr)
        return 2
    except Exception as e:                      # network/provider failures
        print(f"Clacky: organize failed via {provider_name}: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        if not plan.moves:
            print(f"\nNothing to organize in {root} — looks tidy already.")
        else:
            print(f"\nPlan ({len(plan.moves)} move(s)) via {provider_name} — "
                  f"nothing was changed:")
            for m in plan.moves:
                reason = f"   ({m.reason})" if m.reason else ""
                print(f"  {m.name} -> {m.dest_folder}/{reason}")
            if plan.skipped:
                print(f"  ...{len(plan.skipped)} skipped")
            print("\nRun without --dry-run to apply.")
    else:
        n = len(sess.batch.records)
        print(f"\nDone — moved {n} file(s)." if n else "\nNothing to move.")
        if n:
            print("Run `clacky undo` to reverse it.")
    return 0


def _cmd_undo(_args) -> int:
    print(journal.undo_last())
    return 0


def _cmd_run(_args) -> int:
    from .companion import launch
    return launch()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clacky", description="Clacky — the agent you can take back.")
    p.add_argument("--version", action="version", version=f"clacky {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    org = sub.add_parser("organize", help="tidy a folder (default: Desktop)")
    org.add_argument("path", nargs="?", help="folder to organize (default: ~/Desktop)")
    org.add_argument("-n", "--dry-run", action="store_true",
                     help="preview the plan without moving anything")
    org.add_argument("-p", "--provider",
                     help="claude | openai | gemini | ollama | heuristic")
    org.add_argument("-m", "--model", help="override the model")
    org.set_defaults(func=_cmd_organize)

    undo = sub.add_parser("undo", help="reverse the last organize")
    undo.set_defaults(func=_cmd_undo)

    runp = sub.add_parser("run", help="launch the companion shell (voice + screen + pointing)")
    runp.set_defaults(func=_cmd_run)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
