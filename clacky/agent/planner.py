"""
planner.py — turns a folder into a proposed organization Plan with ONE
structured provider call. The model only ever returns JSON; it never touches
the disk. Your code (the orchestrator) executes the plan through safe_fs.

This is the safety win of dropping the agent loop: the LLM proposes, your code
disposes. There is no autonomous tool-calling to constrain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import fileops
from ..providers.base import LLMProvider

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".log", ".json", ".py", ".js", ".ts", ".html"}
_PEEK_BYTES = 600

SYSTEM_PROMPT = (
    "You are Clacky, a file-organizing planner. Given a JSON list of files with "
    "metadata, group them into a SMALL set of sensible destination folders by "
    "INTENT and CONTENT, not just extension (e.g. Screenshots, Documents, "
    "Images, Installers, Archives, Projects). For each file output a "
    "destination folder and a VERY short reason (under 6 words). Use ONLY the "
    "file names given; never invent files. Return ONLY the raw JSON — no "
    "markdown fences, no commentary before or after — of the exact form: "
    '{"moves":[{"name":"<exact file name>","folder":"<folder>","reason":"<why>"}]}'
)


def _extract_json(raw: str) -> dict:
    """Parse the provider's reply into the plan dict, tolerating the usual
    wrappers: ```json fences, a prose preamble/afterword, stray whitespace.
    Falls back to the outermost {...} slice before giving up."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.lstrip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip().strip("`").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            return json.loads(raw[start:end + 1])
        raise


@dataclass
class Move:
    name: str
    dest_folder: str
    reason: str = ""


@dataclass
class Plan:
    root: Path
    moves: list[Move] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        from collections import Counter
        buckets = Counter(m.dest_folder for m in self.moves)
        lines = [f"{n} → {folder}/" for folder, n in sorted(buckets.items())]
        if self.skipped:
            lines.append(f"{len(self.skipped)} skipped")
        return "\n".join(lines) if lines else "Nothing to organize."


def _file_context(p: Path) -> dict:
    info = {"name": p.name, "ext": p.suffix.lower(), "size": p.stat().st_size}
    if p.suffix.lower() in _TEXT_SUFFIXES:
        try:
            info["peek"] = p.read_text(errors="ignore")[:_PEEK_BYTES]
        except OSError:
            pass
    return info


def build_plan(root: str | Path, provider: LLMProvider) -> Plan:
    """Gather context, ask the provider for a plan, validate every move.
    No side effects — nothing is moved here."""
    root = Path(root).expanduser().resolve()
    ok, reason = fileops.check_root(root)
    if not ok:
        raise ValueError(reason)

    files = [p for p in root.iterdir() if p.is_file()]
    plan = Plan(root=root)
    if not files:
        return plan

    contexts = [_file_context(p) for p in files]
    raw = provider.complete(SYSTEM_PROMPT, json.dumps({"files": contexts}, default=str))

    try:
        decided = _extract_json(raw).get("moves", [])
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        raise ValueError(f"provider did not return a valid plan: {e}")

    by_name = {p.name: p for p in files}
    for m in decided:
        src = by_name.get(m.get("name"))
        if src is None:
            continue                                   # hallucinated name
        folder = str(m.get("folder", "")).strip().strip("/\\")
        if not folder:
            plan.skipped.append((src.name, "no folder suggested"))
            continue
        ok, reason = fileops.check_move(src, root / folder / src.name, root)
        if ok:
            plan.moves.append(Move(src.name, folder, str(m.get("reason", ""))))
        else:
            plan.skipped.append((src.name, reason))

    if len(plan.moves) > fileops.MAX_BATCH:
        raise ValueError(f"plan has {len(plan.moves)} moves, over the "
                         f"{fileops.MAX_BATCH} cap")
    return plan
