"""
Headless tests for the Clacky CLI core — no provider SDKs, no network, no UI.
Covers file-op safety, the planner (with a fake provider), the orchestrator
execute→journal→undo roundtrip, dry-run, and risk classification.

Run:  python -m pytest tests/test_clacky_core.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

from clacky.agent import fileops, journal, planner, runtime, session
from clacky.agent.permission import Risk, classify_move, is_allowed
from clacky.providers.base import LLMProvider


class FakeProvider(LLMProvider):
    """Deterministic stand-in: buckets by extension, returns the JSON the
    planner expects. No network."""
    name = "fake"

    def complete(self, system: str, user: str) -> str:
        files = json.loads(user)["files"]
        bucket = {".png": "Screenshots", ".jpg": "Images", ".pdf": "Documents",
                  ".txt": "Documents", ".exe": "Installers"}
        moves = [{"name": f["name"], "folder": bucket.get(f["ext"], "Misc"),
                  "reason": f"{f['ext']} file"} for f in files]
        return json.dumps({"moves": moves})


def _seed(root: Path):
    for n in ["a.png", "b.png", "report.pdf", "notes.txt", "setup.exe"]:
        (root / n).write_text("x")


# ── fileops safety ─────────────────────────────────────────────────────────

def test_check_root_blocks_outside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    outside = tmp_path / "elsewhere"; outside.mkdir()
    ok, reason = fileops.check_root(outside)
    assert not ok and "home" in reason.lower()


def test_check_move_rejects_sensitive_and_protected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "Desktop"; root.mkdir()
    secret = root / "my_password.txt"; secret.write_text("x")
    dll = root / "thing.dll"; dll.write_text("x")
    ok1, _ = fileops.check_move(secret, root / "Documents" / secret.name, root)
    ok2, _ = fileops.check_move(dll, root / "Documents" / dll.name, root)
    assert not ok1 and not ok2


def test_apply_move_resolves_collisions(tmp_path):
    (tmp_path / "A").mkdir(); (tmp_path / "B").mkdir()
    (tmp_path / "A" / "f.txt").write_text("1")
    (tmp_path / "B" / "f.txt").write_text("2")
    final = fileops.apply_move(tmp_path / "A" / "f.txt", tmp_path / "B" / "f.txt")
    assert final.name == "f (1).txt" and final.exists()
    assert (tmp_path / "B" / "f.txt").read_text() == "2"


# ── risk model ─────────────────────────────────────────────────────────────

def test_risk_model():
    assert classify_move() is Risk.REVERSIBLE
    assert is_allowed(Risk.SAFE) and is_allowed(Risk.REVERSIBLE)
    assert not is_allowed(Risk.DANGEROUS)


# ── planner (with fake provider) ───────────────────────────────────────────

def test_planner_groups_by_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "Desktop"; root.mkdir(); _seed(root)
    plan = planner.build_plan(root, FakeProvider())
    folders = {m.dest_folder for m in plan.moves}
    # setup.exe is a protected file type — the guard skips it even though the
    # provider proposed moving it to Installers. So: 4 moves, no Installers.
    assert folders == {"Screenshots", "Documents"}
    assert len(plan.moves) == 4
    assert any(name == "setup.exe" for name, _ in plan.skipped)
    assert (root / "a.png").exists()                # dry: nothing moved yet


def test_planner_rejects_bad_provider_output(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "Desktop"; root.mkdir(); _seed(root)

    class Garbage(LLMProvider):
        name = "garbage"
        def complete(self, system, user): return "not json at all"

    try:
        planner.build_plan(root, Garbage())
        assert False, "should have raised on invalid JSON"
    except ValueError:
        pass


# ── orchestrator: execute → journal → undo ─────────────────────────────────

def test_run_organize_and_undo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(journal, "HISTORY_DIR", tmp_path / ".clacky" / "history")
    root = tmp_path / "Desktop"; root.mkdir(); _seed(root)

    sess, plan = runtime.run_organize(root, FakeProvider(), dry_run=False)
    assert len(sess.batch.records) == 4             # setup.exe skipped (protected)
    assert (root / "setup.exe").exists()            # never moved
    assert (root / "Screenshots" / "a.png").exists()
    assert not (root / "a.png").exists()

    msg = journal.undo_last()
    assert "restored" in msg.lower()
    assert (root / "a.png").exists()


def test_run_organize_dry_run_moves_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "Desktop"; root.mkdir(); _seed(root)
    sess, plan = runtime.run_organize(root, FakeProvider(), dry_run=True)
    assert len(plan.moves) == 4                      # setup.exe skipped (protected)
    assert sess.plan and (root / "a.png").exists()  # untouched
