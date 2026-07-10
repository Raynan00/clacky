"""
harness.py — Clacky's background-work lane, backed by an embedded agent harness.

The design lesson comes straight from OpenClicky's architecture: don't build an
agent runtime, EMBED one behind a clean process boundary. Our backend is
`hermes-agent` (Nous Research — MIT, running on the user's same Anthropic key),
driven as a persistent ACP session (see acp_client.py) so it mounts connected
MCP apps — one-shot `hermes -z` never does. `-z` remains a fallback for
research-only when the ACP deps aren't present.

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

# Secrets the background agent has no business inheriting (its terminal and
# browser touch the open web; a prompt-injected page must find nothing).
_KEEP_PRIVATE = {
    "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
    "ELEVENLABS_API_KEY", "TAVILY_API_KEY",
    "VSCODE_GIT_IPC_AUTH_TOKEN", "VSCODE_GIT_ASKPASS_MAIN",
    "VSCODE_GIT_ASKPASS_NODE",
}


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


def _build_prompt(task: str, ws: Path, context: str = "") -> str:
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
    if "composio" in servers:
        apps_part = (
            f"Connected external apps (MCP servers): {connected}. Composio "
            f"brokers 1000+ apps (Notion, Slack, Sheets, …): if the task names "
            f"an app that isn't directly connected, reach it through Composio's "
            f"tools. If Composio says the app needs authorizing first, save the "
            f"authorization link it gives you into a file here named "
            f"CONNECT-<app>.txt, still save the output as files, and mention "
            f"the link file in your summary — never claim a delivery you "
            f"didn't make.\n")
    else:
        apps_part = (
            f"Connected external apps (MCP servers): {connected}. If the task "
            f"asks you to deliver output to an app that is NOT connected, do "
            f"not attempt it and never claim you did — save the output as "
            f"files here instead, and say in your summary that the app isn't "
            f"connected yet and that running 'clacky connect' would wire it "
            f"up.\n")
    context_part = ""
    if context:
        context_part = (
            f"\nWhat the user was looking at when they asked (their screen, "
            f"described for you — use it to resolve references like "
            f"\"this\"): {context}\n")
    return (
        f"You are Clacky's background worker. Task: {task}{skill_part}\n"
        f"{context_part}\n"
        f"Work autonomously. Save any outputs — reports, lists, documents, "
        f"data — as files inside this folder: {ws}\n"
        f"Prefer a single well-named markdown file for research-style tasks.\n"
        + apps_part +
        f"When done, reply with a SHORT spoken-style summary (2-3 sentences, "
        f"for the ear: no paths, no markdown) of what you found or did."
    )


async def run_background_task(task: str, timeout_s: int | None = None,
                              ws: Path | None = None,
                              context: str = "") -> HarnessResult:
    """Run one task through the harness. Blocking work happens off-loop.
    Pass `ws` to continue in an existing workspace (e.g. finishing a delivery
    whose files are already there); `context` carries a text description of
    the user's screen for deictic tasks ("research this") — the harness has
    no eyes, so the foreground's eyes translate.

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

    ws = ws or _task_workspace(task)
    model = os.environ.get("CLACKY_BG_MODEL", "claude-sonnet-5")
    timeout_s = timeout_s or int(os.environ.get("CLACKY_BG_TIMEOUT", "900"))
    _ensure_config_model(model)          # so the ACP session runs on it
    slog("BG", f"harness task starting (model={model}): {task[:60]!r}")
    t0 = time.perf_counter()

    # The harness only needs the Anthropic key — the rest of Clacky's secrets
    # (Deepgram, editor IPC tokens, …) must not leak into an agent whose
    # terminal reads the web.
    env = {k: v for k, v in os.environ.items() if k not in _KEEP_PRIVATE}
    prompt = _build_prompt(task, ws, context)

    try:
        import acp_client
        if acp_client.available():
            # Persistent ACP session — the ONLY headless surface that mounts
            # connected MCP apps (one-shot `-z` never does).
            ok, summary = await asyncio.to_thread(
                acp_client.run, prompt, ws, model, timeout_s, env)
        else:
            ok, summary = await asyncio.to_thread(
                _run_oneshot, prompt, ws, model, timeout_s, env)
    except TimeoutError:
        arts = sorted(p for p in ws.rglob("*") if p.is_file())
        slog("BG", f"harness task TIMED OUT after {timeout_s}s "
                   f"(salvaged {len(arts)} artifact(s))")
        return HarnessResult(False,
                             "that background task ran long and I had to stop it"
                             + (" — I kept what it finished" if arts else ""),
                             workspace=ws, artifacts=arts)
    except Exception as e:
        slog("BG", f"harness task failed to run: {e}")
        return HarnessResult(False, "I couldn't start that background task",
                             workspace=ws)

    dt = time.perf_counter() - t0
    artifacts = sorted(p for p in ws.rglob("*") if p.is_file())
    if len(summary) > 500:                # keep the spoken part tight
        summary = summary[:500].rsplit(".", 1)[0] + "."
    slog("BG", f"harness task done in {dt:.0f}s ok={ok} "
               f"artifacts={len(artifacts)}")
    if not ok:
        return HarnessResult(False, summary or "the background task hit an error",
                             ws, artifacts)
    return HarnessResult(True, summary or "done", ws, artifacts)


def _ensure_config_model(model: str) -> None:
    """Point hermes' default model at CLACKY_BG_MODEL (ACP sessions read it
    from config; set_model's curated picker rejects some valid ids)."""
    try:
        import yaml
        from clacky.connections import config_path
        p = config_path()
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {} if p.exists() else {}
        if cfg.get("model") != model or cfg.get("provider") != "anthropic":
            cfg["model"] = model
            cfg["provider"] = "anthropic"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    except Exception:
        pass


def _run_oneshot(prompt: str, ws: Path, model: str, timeout_s: int,
                 env: dict) -> tuple[bool, str]:
    """Fallback path when ACP deps are absent: one-shot `-z`. Research and
    files work here; connected-app delivery does not (no MCP in one-shot)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["hermes", "-z", prompt, "--provider", "anthropic", "-m", model],
            capture_output=True, text=True, timeout=timeout_s,
            cwd=str(ws), creationflags=flags, env=env)
    except subprocess.TimeoutExpired:
        raise TimeoutError("oneshot")
    if proc.returncode != 0:
        slog("BG", f"oneshot stderr: {(proc.stderr or '').strip()[-200:]}")
        return False, "the background task hit an error"
    return True, (proc.stdout or "").strip() or "done"


def open_workspace(ws: Path) -> None:
    """Reveal the task's artifacts in Explorer (called after she reports back)."""
    try:
        os.startfile(str(ws))  # noqa: S606 — local folder, user-initiated flow
    except Exception:
        pass
