"""
memory_store.py — Clacky' cross-session memory + learned skills.

Two things OpenClicky has that classic Clicky doesn't: it *remembers* you
between sessions, and it can *learn a skill* once and reuse it. This is the
disk-backed store behind both. One JSON file at ``~/.clacky/memory.json``:

    {
      "facts":  [{"text": "...", "ts": "..."}, ...],   # things to remember
      "skills": {"morning routine": {"steps": "...", "ts": "..."}, ...}
    }

- **Facts** are injected into every system prompt, so Clacky always "knows"
  them — passive recall, no lookup step.
- **Skills** are named routines injected into the agent's prompt, so "do my
  morning routine" just runs the saved steps.

Kept dependency-free and defensive: a corrupt/missing file degrades to empty
rather than crashing startup.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class MemoryStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or (Path.home() / ".clacky" / "memory.json")
        self.facts: list[dict] = []
        self.skills: dict[str, dict] = {}
        self._load()

    # ── persistence ──────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.facts = [f for f in data.get("facts", []) if f.get("text")]
            self.skills = {k: v for k, v in data.get("skills", {}).items()
                           if v.get("steps")}
        except Exception:
            self.facts, self.skills = [], {}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"facts": self.facts, "skills": self.skills},
                           indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[clacky-debug] memory save error: {e}", flush=True)

    # ── facts ────────────────────────────────────────────────────────────
    def add_fact(self, text: str) -> bool:
        text = (text or "").strip()
        if not text:
            return False
        low = text.lower()
        # Skip near-duplicates (same text, case-insensitive).
        if any(f["text"].lower() == low for f in self.facts):
            return False
        self.facts.append({"text": text, "ts": _now()})
        self.facts = self.facts[-100:]            # keep it bounded
        self.save()
        return True

    def forget(self, query: str) -> int:
        """Remove facts matching `query` (substring, case-insensitive). The words
        'everything' / 'all' wipe them. Returns how many were removed."""
        q = (query or "").strip().lower()
        before = len(self.facts)
        if not q or q in ("everything", "all", "it all", "all of it"):
            self.facts = []
        else:
            self.facts = [f for f in self.facts if q not in f["text"].lower()]
        removed = before - len(self.facts)
        if removed:
            self.save()
        return removed

    def facts_block(self) -> str:
        """Prompt fragment listing what Clacky remembers (empty string if none)."""
        if not self.facts:
            return ""
        lines = "\n".join(f"  - {f['text']}" for f in self.facts)
        return ("WHAT YOU REMEMBER about this user (from past sessions — use it "
                "naturally, don't recite it):\n" + lines)

    # ── skills ───────────────────────────────────────────────────────────
    def add_skill(self, name: str, steps: str) -> bool:
        name = (name or "").strip().lower()
        steps = (steps or "").strip()
        if not name or not steps:
            return False
        self.skills[name] = {"steps": steps, "ts": _now()}
        self.save()
        return True

    def remove_skill(self, name: str) -> bool:
        name = (name or "").strip().lower()
        if name in self.skills:
            del self.skills[name]
            self.save()
            return True
        return False

    def skills_block(self) -> str:
        """Prompt fragment listing learned routines (empty string if none)."""
        if not self.skills:
            return ""
        lines = "\n".join(f'  - "{n}": {v["steps"]}' for n, v in self.skills.items())
        return ("LEARNED ROUTINES you can run when asked (by name or intent):\n"
                + lines)
