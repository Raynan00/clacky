"""
harness.py — Clacky's background-work lane, backed by an embedded agent harness.

The design lesson comes straight from OpenClicky's architecture: don't build an
agent runtime, EMBED one behind a clean process boundary. Our default backend is
`hermes-agent` (Nous Research — 212K-star MIT harness, driven headlessly via
`hermes -z`, running on the user's same Anthropic key). The boundary is one
function, so the backend is swappable (Claude Agent SDK was also spiked and
works; `CLACKY_BG_HARNESS` picks).

What this buys over the old builtin lane: background tasks that produce
ARTIFACTS — "go research X" stops being a spoken summary and becomes a file on
disk — plus the harness's own tools (web, files, code) without us maintaining
an agent loop.

Each task runs in its own workspace under ~/.clacky/background/. The harness is
instructed to leave its outputs there; whatever files appear are the artifacts.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from session_log import slog

_BG_ROOT = Path.home() / ".clacky" / "background"


@dataclass
class HarnessResult:
    ok: bool
    summary: str                       # the harness's final text (for the ear)
    workspace: Path | None = None
    artifacts: list[Path] = field(default_factory=list)


def harness_available() -> bool:
    """True if the hermes backend is installed (checked once, cached)."""
    global _AVAILABLE
    try:
        return _AVAILABLE
    except NameError:
        pass
    _AVAILABLE = shutil.which("hermes") is not None
    return _AVAILABLE


def _task_workspace(task: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower())[:32].strip("-") or "task"
    ws = _BG_ROOT / f"{stamp}-{slug}"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _connected_servers() -> list[str]:
    """Names of MCP servers the harness will actually have (from its config)."""
    try:
        from clacky.connections import connected_servers
        return connected_servers()
    except Exception:
        return []


def _build_prompt(task: str, ws: Path) -> str:
    skill_part = ""
    try:
        import agent_skills
        m = agent_skills.find(task)
        if m:
            skill_part = (f"\n\nThis task invokes the user's skill "
                          f"\"{m.name}\" — follow its instructions:\n{m.body}\n")
    except Exception:
        pass
    servers = _connected_servers()
    connected = ", ".join(servers) if servers else "none"
    return (
        f"You are Clacky's background worker. Task: {task}{skill_part}\n\n"
        f"Work autonomously. Save any outputs — reports, lists, documents, "
        f"data — as files inside this folder: {ws}\n"
        f"Prefer a single well-named markdown file for research-style tasks.\n"
        f"Connected external apps (MCP servers): {connected}. If the task asks "
        f"you to deliver output to an app that is NOT connected, do not attempt "
        f"it and never claim you did — save the output as files here instead, "
        f"and say in your summary that the app isn't connected yet and that "
        f"running 'clacky connect' would wire it up.\n"
        f"When done, reply with a SHORT spoken-style summary (2-3 sentences, "
        f"for the ear: no paths, no markdown) of what you found or did."
    )


async def run_background_task(task: str, timeout_s: int | None = None) -> HarnessResult:
    """Run one task through the harness. Blocking work happens off-loop.

    Env knobs: CLACKY_BG_MODEL (default claude-sonnet-5),
    CLACKY_BG_TIMEOUT seconds (default 600)."""
    if not harness_available():
        return HarnessResult(False, "no harness installed")

    # Renew any expired connected-app tokens (OAuth) so deliveries keep working.
    try:
        from clacky.connections import refresh_stale
        refresh_stale()
    except Exception:
        pass

    ws = _task_workspace(task)
    model = os.environ.get("CLACKY_BG_MODEL", "claude-sonnet-5")
    timeout_s = timeout_s or int(os.environ.get("CLACKY_BG_TIMEOUT", "600"))
    slog("BG", f"harness task starting (model={model}): {task[:60]!r}")
    t0 = time.perf_counter()

    def _run() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["hermes", "-z", _build_prompt(task, ws),
             "--provider", "anthropic", "-m", model],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(ws),
        )

    try:
        proc = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        slog("BG", f"harness task TIMED OUT after {timeout_s}s")
        return HarnessResult(False, "that background task ran too long, so I stopped it",
                             workspace=ws)
    except Exception as e:
        slog("BG", f"harness task failed to run: {e}")
        return HarnessResult(False, "I couldn't start that background task", workspace=ws)

    dt = time.perf_counter() - t0
    artifacts = sorted(p for p in ws.rglob("*") if p.is_file())
    summary = (proc.stdout or "").strip()
    # Keep the spoken part tight even if the harness rambles.
    if len(summary) > 500:
        summary = summary[:500].rsplit(".", 1)[0] + "."
    slog("BG", f"harness task done in {dt:.0f}s exit={proc.returncode} "
               f"artifacts={len(artifacts)}")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()[-200:]
        slog("BG", f"harness stderr: {err}")
        return HarnessResult(False, "the background task hit an error", ws, artifacts)
    return HarnessResult(True, summary or "done", ws, artifacts)


def open_workspace(ws: Path) -> None:
    """Reveal the task's artifacts in Explorer (called after she reports back)."""
    try:
        os.startfile(str(ws))  # noqa: S606 — local folder, user-initiated flow
    except Exception:
        pass
