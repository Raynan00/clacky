"""
planner.py — builds a dry-run organization plan. NO side effects.

The planner reads a directory, gathers lightweight context for each file
(name, size, type, and a small content peek), asks the LLM to group files by
*intent*, and returns a structured Plan. Nothing is moved here — the plan is
shown to the user for confirmation first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import guards

# Peek at most this many bytes of a text file to infer intent cheaply.
_TEXT_PEEK_BYTES = 600
_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts", ".html"}


@dataclass
class Move:
    src: Path
    dst_folder: str        # relative folder name under the root, e.g. "Screenshots"
    reason: str            # one-line human-readable justification

    @property
    def dst(self) -> Path:
        return self.src.parent / self.dst_folder / self.src.name


@dataclass
class Plan:
    root: Path
    moves: list[Move] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, why)

    def summary(self) -> str:
        from collections import Counter
        buckets = Counter(m.dst_folder for m in self.moves)
        lines = [f"{n} file(s) → {folder}/" for folder, n in sorted(buckets.items())]
        if self.skipped:
            lines.append(f"{len(self.skipped)} skipped")
        return "\n".join(lines) if lines else "Nothing to organize."


def _file_context(p: Path) -> dict:
    info = {"name": p.name, "ext": p.suffix.lower(), "size": p.stat().st_size}
    if p.suffix.lower() in _TEXT_SUFFIXES:
        try:
            info["peek"] = p.read_text(errors="ignore")[:_TEXT_PEEK_BYTES]
        except OSError:
            pass
    return info


_SYSTEM_PROMPT = """You are a file-organizing assistant. Given a list of files \
with metadata, group them into a small set of sensible destination folders based \
on INTENT and CONTENT, not just extension. Prefer few, meaningful folders \
(e.g. Screenshots, Documents, Installers, Images, Projects, Archives). \
For each file output a destination folder name and a short reason. \
Return ONLY valid JSON: {"moves":[{"name":..,"folder":..,"reason":..}]}. \
Do not invent files; only use the names provided."""


def build_plan(root: str | Path, llm_complete) -> Plan:
    """
    root        : directory to organize (e.g. the Desktop)
    llm_complete: callable(system: str, user: str) -> str  (returns model text)
                  Inject the app's existing Claude/Ollama provider here so the
                  planner stays provider-agnostic and easy to test with a fake.
    """
    root = Path(root).expanduser().resolve()
    g = guards.is_safe_root(root)
    if not g.allowed:
        raise ValueError(f"unsafe root: {g.reason}")

    files = [p for p in root.iterdir() if p.is_file()]
    plan = Plan(root=root)
    if not files:
        return plan

    contexts = [_file_context(p) for p in files]
    user_msg = json.dumps({"files": contexts}, default=str)
    raw = llm_complete(_SYSTEM_PROMPT, user_msg)

    try:
        decided = {m["name"]: m for m in json.loads(raw).get("moves", [])}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"could not parse LLM plan: {e}")

    by_name = {p.name: p for p in files}
    for name, m in decided.items():
        src = by_name.get(name)
        if src is None:
            continue  # model hallucinated a name; ignore
        folder = str(m.get("folder", "")).strip().strip("/\\")
        if not folder:
            plan.skipped.append((name, "no folder suggested"))
            continue
        move = Move(src=src, dst_folder=folder, reason=str(m.get("reason", "")))
        check = guards.is_safe_move(src, move.dst)
        if check.allowed:
            plan.moves.append(move)
        else:
            plan.skipped.append((name, check.reason))

    batch = guards.enforce_batch_size(len(plan.moves))
    if not batch.allowed:
        raise ValueError(batch.reason)
    return plan
