"""
connections.py — one place that knows how apps get wired into the background
agent lane (MCP servers in the harness config, ~/.hermes/config.yaml).

Three consumers share this:
  - `clacky connect` (cli.py) — the terminal path
  - the just-in-time ConnectDialog (shell/ui/connect_dialog.py) — when a task
    asks to deliver into an app that isn't connected yet
  - the harness prompt builder (shell/harness.py) — tells the agent what it
    actually has, so it never attempts or claims impossible deliveries
"""

from __future__ import annotations

import json
import time
from pathlib import Path

# Official hosted MCP endpoints for common apps — so "connect notion" needs
# zero typing. Data, not behavior: extend via ~/.clacky/connections.json
# {"registry": {"myapp": "https://…"}} or a PR.
KNOWN_APPS = {
    "notion": "https://mcp.notion.com/mcp",
    "linear": "https://mcp.linear.app/sse",
    "sentry": "https://mcp.sentry.dev/mcp",
    "github": "https://api.githubcopilot.com/mcp/",
    "huggingface": "https://huggingface.co/mcp",
    # The long tail: Composio's Connect MCP is one fixed URL for everyone —
    # only the API key (dashboard.composio.dev) is yours. Connecting it gives
    # background agents 1000+ apps; Composio handles each app's auth at
    # runtime by handing back an authorization link.
    "composio": "https://connect.composio.dev/mcp",
}


def api_key_header_for(url: str) -> str | None:
    """Some hosted servers authenticate with a static API key header instead
    of browser sign-in. Returns the header name, or None for OAuth/Bearer."""
    try:
        from urllib.parse import urlsplit
        host = urlsplit(url).netloc.lower()
    except Exception:
        return None
    if host == "composio.dev" or host.endswith(".composio.dev"):
        return "x-consumer-api-key"
    return None


def config_path() -> Path:
    """The harness config file — resolved the way hermes itself resolves it.

    On Windows hermes' home is %LOCALAPPDATA%\\hermes, NOT ~/.hermes (that's
    the Linux/Mac path most docs show). Asking hermes' own constant is the
    only way to never write config into a file it never reads."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home()) / "config.yaml"
    except Exception:
        return Path.home() / ".hermes" / "config.yaml"


def _state_path() -> Path:
    return Path.home() / ".clacky" / "connections.json"


def _state_load() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _state_save(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


def known_app_url(name: str) -> str | None:
    """Registry lookup: builtin well-known apps, user additions win."""
    user = (_state_load().get("registry") or {})
    return user.get(name.lower()) or KNOWN_APPS.get(name.lower())


def connected_servers() -> list[str]:
    """Names of the MCP servers the background lane will actually have."""
    try:
        import yaml
        cfg = yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}
        return sorted((cfg.get("mcp_servers") or {}).keys())
    except Exception:
        return []


def add_server(name: str, target: str, token: str | None = None) -> Path:
    """Write/merge one MCP server into the harness config and return its path.

    `target` is either a hosted server URL (https://…) or a local stdio
    command line (e.g. "python -m mcp_server_fetch")."""
    import yaml

    p = config_path()
    cfg = {}
    if p.exists():
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    servers = cfg.setdefault("mcp_servers", {})

    if target.startswith(("http://", "https://")):
        # Remote servers get explicit patience — hermes' default connect
        # deadline makes hosted-server mounting a startup race (tools appear
        # some runs, vanish others).
        entry: dict = {"url": target, "connect_timeout": 60, "timeout": 180}
        key_header = api_key_header_for(target)
        if token and key_header:
            entry["headers"] = {key_header: token}
        elif token:
            entry["headers"] = {"Authorization": f"Bearer {token}"}
    else:
        cmd = target.split()
        entry = {"command": cmd[0], "args": cmd[1:]}

    servers[name] = entry
    # Hermes waits only mcp_discovery_timeout (default 1.5s!) for MCP servers
    # before the session's first tool snapshot — remote handshakes take ~3s+,
    # so hosted servers lose the race and their tools silently never appear.
    # 30 caps the wait; it only ever blocks for real connect time.
    try:
        if float(cfg.get("mcp_discovery_timeout") or 0) < 30:
            cfg["mcp_discovery_timeout"] = 30
    except (TypeError, ValueError):
        cfg["mcp_discovery_timeout"] = 30
    # Hermes' one-shot CLI sessions only mount MCP servers that are named
    # EXPLICITLY in the platform toolset list (it resolves 'cli' with
    # include_default_mcp_servers=False) — without this, connected apps exist
    # in config but never become tools for background tasks.
    cli = cfg.setdefault("platform_toolsets", {}).setdefault("cli", ["hermes-cli"])
    if isinstance(cli, list):
        if not any(str(t).startswith("hermes-") for t in cli):
            cli.insert(0, "hermes-cli")
        if name not in cli:
            cli.append(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def connect_oauth(name: str, url: str, on_status=None) -> Path:
    """The no-pasting path: browser sign-in (see oauth.py), then persist —
    access token into the harness config, refresh bundle into Clacky's own
    state so future tasks renew it silently. Raises oauth.OAuthError if the
    server can't do browser sign-in (callers fall back to a token prompt)."""
    from . import oauth
    bundle = oauth.authorize(url, on_status=on_status)
    state = _state_load()
    state.setdefault("oauth", {})[name] = {"url": url, **bundle}
    _state_save(state)
    return add_server(name, url, bundle["access_token"])


def refresh_stale(max_age_slack_s: int = 0) -> None:
    """Renew any expired OAuth tokens and rewrite the harness config. Called
    best-effort before each background task; failures leave the old token in
    place (it may still work) rather than breaking the task."""
    state = _state_load()
    entries = state.get("oauth") or {}
    dirty = False
    for name, bundle in entries.items():
        if not bundle.get("refresh_token"):
            continue
        if time.time() + max_age_slack_s < float(bundle.get("expires_at", 0)):
            continue
        try:
            from . import oauth
            fresh = oauth.refresh(bundle)
            entries[name] = {"url": bundle.get("url", ""), **fresh}
            add_server(name, bundle["url"], fresh["access_token"])
            dirty = True
        except Exception:
            continue
    if dirty:
        _state_save(state)
