"""
test_organizer.py — prove the organizer core works headless, with a FAKE LLM.

Run:  python -m pytest test_organizer.py -v
This is your M1 safety net: plan -> execute -> undo, no app, no API, no UI.
"""

from __future__ import annotations

import json
from pathlib import Path

from organizer import build_plan, execute, undo


def _fake_llm(_system: str, user: str) -> str:
    """Deterministic stand-in for Claude: sort by extension into fixed buckets."""
    files = json.loads(user)["files"]
    bucket = {".png": "Screenshots", ".jpg": "Images", ".pdf": "Documents",
              ".txt": "Documents", ".exe": "Installers"}
    moves = [
        {"name": f["name"],
         "folder": bucket.get(f["ext"], "Misc"),
         "reason": f"{f['ext']} file"}
        for f in files
    ]
    return json.dumps({"moves": moves})


def _seed(root: Path):
    for name in ["a.png", "b.png", "report.pdf", "notes.txt", "setup.exe"]:
        (root / name).write_text("x")


def test_plan_groups_by_intent(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)  # sandbox home
    desktop = tmp_path / "Desktop"; desktop.mkdir()
    _seed(desktop)

    plan = build_plan(desktop, _fake_llm)
    folders = {m.dst_folder for m in plan.moves}
    assert folders == {"Screenshots", "Documents", "Installers"}
    assert len(plan.moves) == 5
    # Dry run: nothing has actually moved yet.
    assert (desktop / "a.png").exists()


def test_execute_then_undo_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop = tmp_path / "Desktop"; desktop.mkdir()
    _seed(desktop)

    plan = build_plan(desktop, _fake_llm)
    batch = execute(plan)
    undo.save(batch)

    # Files moved into folders.
    assert (desktop / "Screenshots" / "a.png").exists()
    assert not (desktop / "a.png").exists()

    # Undo restores originals.
    msg = undo.undo_last()
    assert "restored" in msg.lower()
    assert (desktop / "a.png").exists()


def test_guards_block_outside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir()
    outside = tmp_path / "elsewhere"; outside.mkdir()
    try:
        build_plan(outside, _fake_llm)
        assert False, "should have refused a root outside home"
    except ValueError as e:
        assert "home" in str(e).lower()
