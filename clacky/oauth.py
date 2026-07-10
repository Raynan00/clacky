"""
oauth.py — the browser sign-in behind "Connect & continue" (no token pasting).

This is the same dance Claude Code does for `claude mcp add`: remote MCP
servers advertise OAuth 2.1, so connecting an app is a browser approval, not
a credential hunt. Concretely:

  1. discovery — RFC 9728 protected-resource metadata on the server, then
     RFC 8414 authorization-server metadata on its issuer
  2. dynamic client registration (RFC 7591) — "Clacky" registers itself,
     no pre-provisioned client id needed
  3. authorization-code + PKCE (RFC 7636) in the user's browser, with a
     one-shot localhost callback catching the redirect
  4. token exchange + refresh (with RFC 8707 `resource` binding)

Stdlib only — this also runs from the bare `clacky connect` CLI.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


class OAuthError(RuntimeError):
    """Anything that means 'the browser flow can't work here' — callers fall
    back to token pasting with this message."""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _get_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
        "User-Agent": "clacky-connect",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "clacky-connect"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise OAuthError(f"server said HTTP {e.code} during registration")


def _post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json", "User-Agent": "clacky-connect"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode()).get("error_description", "")
        except Exception:
            pass
        raise OAuthError(f"token request failed (HTTP {e.code}) {detail}".strip())


def discover(server_url: str) -> dict:
    """Server URL → authorization-server metadata (endpoints for the flow)."""
    u = urllib.parse.urlsplit(server_url)
    origin = f"{u.scheme}://{u.netloc}"

    issuers: list[str] = []
    for probe in (f"{origin}/.well-known/oauth-protected-resource{u.path.rstrip('/')}",
                  f"{origin}/.well-known/oauth-protected-resource"):
        try:
            issuers = _get_json(probe).get("authorization_servers") or []
            if issuers:
                break
        except Exception:
            continue
    if not issuers:
        issuers = [origin]          # legacy servers: the origin IS the issuer

    for issuer in issuers:
        iu = urllib.parse.urlsplit(issuer)
        base, path = f"{iu.scheme}://{iu.netloc}", iu.path.rstrip("/")
        for probe in (f"{base}/.well-known/oauth-authorization-server{path}",
                      f"{base}/.well-known/openid-configuration{path}",
                      f"{base}{path}/.well-known/openid-configuration"):
            try:
                meta = _get_json(probe)
                if meta.get("authorization_endpoint") and meta.get("token_endpoint"):
                    return meta
            except Exception:
                continue
    raise OAuthError("this server doesn't advertise a browser sign-in")


class _Callback(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802 (stdlib API)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        self.server.result = {k: v[0] for k, v in q.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>Clacky is connected 🧤</h2>"
                         "You can close this tab and go back to what you were "
                         "doing.".encode())

    def log_message(self, *args):                        # keep the console quiet
        pass


def authorize(server_url: str, timeout: int = 240, on_status=None) -> dict:
    """Run the full browser flow. Returns a token bundle:
    {access_token, refresh_token?, expires_at, token_endpoint, client_id,
     resource} — everything needed to refresh later without a browser."""
    say = on_status or (lambda s: None)
    meta = discover(server_url)

    srv = HTTPServer(("127.0.0.1", 0), _Callback)
    srv.result = None
    srv.timeout = 1.0
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback"

    try:
        if not meta.get("registration_endpoint"):
            raise OAuthError("server doesn't support automatic app registration")
        say("Introducing Clacky to the server…")
        reg = _post_json(meta["registration_endpoint"], {
            "client_name": "Clacky",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
        client_id = reg.get("client_id")
        if not client_id:
            raise OAuthError("registration returned no client id")

        verifier = _b64url(secrets.token_bytes(32))
        state = secrets.token_urlsafe(16)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "state": state,
            "resource": server_url,
        }
        if meta.get("scopes_supported"):
            params["scope"] = " ".join(meta["scopes_supported"])

        say("Opened your browser — approve Clacky there…")
        webbrowser.open(meta["authorization_endpoint"] + "?"
                        + urllib.parse.urlencode(params))

        deadline = time.time() + timeout
        while srv.result is None and time.time() < deadline:
            srv.handle_request()                          # 1 s ticks
        result = srv.result
    finally:
        srv.server_close()

    if not result:
        raise OAuthError("timed out waiting for the browser approval")
    if result.get("state") != state:
        raise OAuthError("state mismatch — please try again")
    if "error" in result:
        raise OAuthError(result.get("error_description") or result["error"])

    say("Finishing up…")
    tok = _post_form(meta["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": result.get("code", ""),
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": server_url,
    })
    if "access_token" not in tok:
        raise OAuthError("no access token came back")
    return {
        "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"),
        "expires_at": time.time() + int(tok.get("expires_in") or 3600) - 60,
        "token_endpoint": meta["token_endpoint"],
        "client_id": client_id,
        "resource": server_url,
    }


def refresh(bundle: dict) -> dict:
    """Trade a refresh token for a fresh access token; returns an updated
    bundle (refresh tokens may rotate)."""
    if not bundle.get("refresh_token"):
        raise OAuthError("no refresh token stored")
    tok = _post_form(bundle["token_endpoint"], {
        "grant_type": "refresh_token",
        "refresh_token": bundle["refresh_token"],
        "client_id": bundle["client_id"],
        "resource": bundle.get("resource", ""),
    })
    if "access_token" not in tok:
        raise OAuthError("refresh returned no access token")
    out = dict(bundle)
    out["access_token"] = tok["access_token"]
    out["refresh_token"] = tok.get("refresh_token") or bundle["refresh_token"]
    out["expires_at"] = time.time() + int(tok.get("expires_in") or 3600) - 60
    return out
