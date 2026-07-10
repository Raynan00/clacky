"""
acp_client.py — drive `hermes acp` as a persistent agent session over stdio.

Why not `hermes -z` (one-shot)? Because one-shot mode never mounts MCP servers
— proven empirically: the agent is handed only its built-in tools, so "put it
in my Notion" can't work no matter how the connection is configured. Every
persistent Hermes surface (chat, TUI, the Telegram/Discord gateways, and this
ACP adapter) DOES mount MCP, which is how Atomic Hermes and the bots reach
connected apps. ACP (Agent Client Protocol — JSON-RPC over stdio, the open
standard editors use) is the clean headless version of that surface.

The flow, one process per task:
  initialize → session/new (declaring the MCP servers) → session/prompt,
  streaming the agent's reply back while auto-approving its tool permissions.

Stdlib only. Blocking; callers run it via asyncio.to_thread.
"""

from __future__ import annotations

import json
import subprocess
import threading
import queue
import time
from pathlib import Path


def available() -> bool:
    """True when `hermes acp` has its dependencies (the agent-client-protocol
    package). Checked once, cached."""
    global _AVAILABLE
    try:
        return _AVAILABLE
    except NameError:
        pass
    _AVAILABLE = False
    try:
        import shutil
        if shutil.which("hermes"):
            r = subprocess.run(["hermes", "acp", "--check"],
                               capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _AVAILABLE = r.returncode == 0 and "OK" in (r.stdout or "")
    except Exception:
        _AVAILABLE = False
    return _AVAILABLE


def _mcp_servers_from_config() -> list[dict]:
    """Config's mcp_servers → ACP session/new mcpServers shape."""
    try:
        import yaml
        from clacky.connections import config_path
        cfg = yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    out = []
    for name, s in (cfg.get("mcp_servers") or {}).items():
        if not isinstance(s, dict) or s.get("enabled") is False:
            continue
        if s.get("url"):
            headers = [{"name": k, "value": v}
                       for k, v in (s.get("headers") or {}).items()]
            out.append({"type": "http", "name": name,
                        "url": s["url"], "headers": headers})
        elif s.get("command"):
            out.append({"name": name, "command": s["command"],
                        "args": list(s.get("args") or []),
                        "env": [{"name": k, "value": v}
                                for k, v in (s.get("env") or {}).items()]})
    return out


class _Session:
    """One `hermes acp` subprocess, spoken to in newline-delimited JSON-RPC."""

    def __init__(self, cwd: Path, env: dict | None):
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            ["hermes", "acp", "--accept-hooks"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", cwd=str(cwd), env=env, creationflags=flags)
        self._id = 0
        self._inbox: queue.Queue = queue.Queue()
        self.stderr_tail: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self._inbox.put(json.loads(line))
                except Exception:
                    pass

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr_tail.append(line.rstrip())
            if len(self.stderr_tail) > 60:
                self.stderr_tail = self.stderr_tail[-60:]

    def _write(self, obj: dict):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float) -> tuple[dict, str]:
        """Send a JSON-RPC request; pump the connection until its response,
        auto-answering the agent's callbacks. Returns (response, streamed_text)."""
        self._id += 1
        my_id = self._id
        self._write({"jsonrpc": "2.0", "id": my_id, "method": method, "params": params})
        deadline = time.time() + timeout
        # The stream interleaves the agent's running commentary with tool
        # calls. Only the text AFTER the last tool call is the actual answer —
        # everything before is it thinking out loud ("now I'll push a copy…"),
        # which must never reach the user's ears.
        segments: list[list[str]] = [[]]
        chunks = segments[0]
        self.tool_evidence: list[str] = getattr(self, "tool_evidence", [])
        while time.time() < deadline:
            try:
                msg = self._inbox.get(timeout=2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError("hermes acp exited early")
                continue
            m = msg.get("method")
            if m == "session/request_permission":
                # Background lane: the user already consented by delegating.
                opts = (msg.get("params") or {}).get("options") or []
                pick = next((o for o in opts
                             if "allow" in (o.get("optionId", "") + o.get("kind", "")).lower()),
                            opts[0] if opts else {})
                self._write({"jsonrpc": "2.0", "id": msg["id"],
                             "result": {"outcome": {"outcome": "selected",
                                        "optionId": pick.get("optionId", "allow")}}})
                continue
            if m == "session/update":
                u = (msg.get("params") or {}).get("update") or {}
                kind = u.get("sessionUpdate")
                if kind == "agent_message_chunk":
                    c = u.get("content") or {}
                    if c.get("type") == "text":
                        chunks.append(c.get("text", ""))
                elif kind in ("tool_call", "tool_call_update"):
                    # Keep raw tool traffic: it's the EVIDENCE layer. A URL is
                    # only trustworthy if a tool result actually contains it
                    # (the server returned it), not just because the model
                    # wrote it in prose.
                    try:
                        self.tool_evidence.append(json.dumps(u))
                    except Exception:
                        pass
                    if chunks:                       # start a fresh segment
                        segments.append([])
                        chunks = segments[-1]
                continue
            if m:                                   # other agent→client call
                if "id" in msg:
                    self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
                continue
            if msg.get("id") == my_id:
                final = next(("".join(s) for s in reversed(segments)
                              if "".join(s).strip()), "")
                return msg, final
        raise TimeoutError(method)

    def close(self):
        try:
            self.proc.kill()
        except Exception:
            pass


def run(prompt: str, cwd: Path, model: str, timeout_s: int,
        env: dict | None = None) -> tuple[bool, str, str]:
    """Run one task to completion in a fresh ACP session. Returns
    (ok, text, tool_evidence) — evidence is the raw tool traffic, used to
    verify that URLs the agent reports were actually returned by tools.

    The model is taken from config's `model`/`provider` (set by the harness),
    so no fragile ACP set_model picker call is needed."""
    s = _Session(cwd, env)
    try:
        s.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False,
                                          "writeTextFile": False}},
            "clientInfo": {"name": "clacky", "version": "0.2"}},
            timeout=60)
        r, _ = s.request("session/new",
                         {"cwd": str(cwd), "mcpServers": _mcp_servers_from_config()},
                         timeout=min(120, timeout_s))
        sid = (r.get("result") or {}).get("sessionId")
        if not sid:
            return False, "the background session wouldn't start", ""
        r, text = s.request("session/prompt",
                            {"sessionId": sid,
                             "prompt": [{"type": "text", "text": prompt}]},
                            timeout=timeout_s)
        stop = (r.get("result") or {}).get("stopReason")
        ok = stop in (None, "end_turn", "completed", "stop")
        return ok, text.strip() or "done", "\n".join(
            getattr(s, "tool_evidence", []))
    finally:
        s.close()
