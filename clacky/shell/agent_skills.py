"""
agent_skills.py — Clacky's learnable skills, in the open SKILL.md standard.

Skills are folders under ``~/.clacky/skills/<slug>/SKILL.md`` with YAML-ish
frontmatter (``name``, ``description``) and a markdown body of instructions —
the same shape used by Claude, Hermes, and OpenClaw (agentskills.io). That
buys three things:

  * "Clacky, save this as my morning routine" writes a real, editable file
  * ONE store feeds both brains — the foreground computer-use agent and the
    background harness get the same skills
  * portability: drop a community skill folder in, or PR one to the repo

Progressive disclosure keeps prompts lean: routers/prompts see only
name + description; a skill's full body is injected only when it's invoked.

Legacy voice-taught routines (memory_store JSON) migrate to SKILL.md files
automatically on first load; the JSON entries are left untouched as backup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from session_log import slog

SKILLS_DIR = Path.home() / ".clacky" / "skills"


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "skill"


def save_skill(name: str, instructions: str, description: str = "") -> Path | None:
    """Write (or overwrite) a skill as SKILL.md. Returns the file path."""
    name = (name or "").strip()
    instructions = (instructions or "").strip()
    if not name or not instructions:
        return None
    if not description:
        description = " ".join(instructions.split())[:80]
    d = SKILLS_DIR / _slug(name)
    try:
        d.mkdir(parents=True, exist_ok=True)
        p = d / "SKILL.md"
        p.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n"
            f"# {name}\n\n"
            f"When the user asks to run \"{name}\", do the following:\n\n"
            f"{instructions}\n",
            encoding="utf-8")
        slog("SKILL", f"saved '{name}' -> {p}")
        return p
    except Exception as e:
        slog("ERROR", f"skill save failed ({e})")
        return None


def _parse(p: Path) -> Skill | None:
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return None
    name, desc, body = p.parent.name, "", text
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?(.*)", text, re.S)
    if m:
        body = m.group(2).strip()
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            if k.strip().lower() == "name" and v.strip():
                name = v.strip()
            elif k.strip().lower() == "description":
                desc = v.strip()
    return Skill(name=name, description=desc, body=body, path=p)


def load_skills() -> list[Skill]:
    """All SKILL.md skills, sorted by name. Cheap enough to call per-turn."""
    out = []
    try:
        for p in sorted(SKILLS_DIR.glob("*/SKILL.md")):
            s = _parse(p)
            if s:
                out.append(s)
    except Exception:
        pass
    return sorted(out, key=lambda s: s.name.lower())


def find(name_or_text: str) -> Skill | None:
    """Match a skill whose name appears in the utterance (longest name wins,
    so 'game time deluxe' beats 'game time')."""
    t = (name_or_text or "").lower()
    best = None
    for s in load_skills():
        if s.name.lower() in t and (best is None or len(s.name) > len(best.name)):
            best = s
    return best


def names_block() -> str:
    """Prompt fragment: names + descriptions only (progressive disclosure)."""
    skills = load_skills()
    if not skills:
        return ""
    lines = "\n".join(f'  - "{s.name}": {s.description}' for s in skills)
    return "LEARNED SKILLS the user can invoke by name:\n" + lines


def migrate_legacy(legacy: dict) -> int:
    """One-time lift of memory_store JSON routines into SKILL.md files.
    Skips any name that already has a skill folder. Returns count migrated."""
    n = 0
    for name, entry in (legacy or {}).items():
        if (SKILLS_DIR / _slug(name) / "SKILL.md").exists():
            continue
        steps = (entry or {}).get("steps", "")
        if steps and save_skill(name, steps):
            n += 1
    if n:
        slog("SKILL", f"migrated {n} legacy routine(s) to SKILL.md")
    return n
