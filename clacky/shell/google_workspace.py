"""
google_workspace.py — opt-in Gmail + Google Calendar via the official API.

This is the *reliable / precise* path that complements open_url's
browser-driving: structured data instead of screen-scraping, exact sends
instead of clicking, works even with no browser window open. It stays dormant
unless the user has done the one-time OAuth setup, so nothing here is imported
or required by default.

Setup (one time):
  1. console.cloud.google.com → new project.
  2. Enable the Gmail API and Google Calendar API.
  3. APIs & Services → Credentials → Create OAuth client ID → Desktop app.
  4. Download it as ``~/.clacky/google_credentials.json``.
  5. pip install "google-api-python-client google-auth-oauthlib"
First use pops a browser consent screen once; the token is cached at
``~/.clacky/google_token.json`` and auto-refreshed after that.

All Google imports are lazy (inside functions) so importing this module — e.g.
to call ``is_configured()`` — never needs the libraries installed.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from pathlib import Path

_DIR = Path.home() / ".clacky"
_CREDS = _DIR / "google_credentials.json"
_TOKEN = _DIR / "google_token.json"

# Minimal scopes: read mail, send mail, read calendar. No delete, no calendar
# writes — kept tight on purpose.
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def is_configured() -> bool:
    """True if the user has dropped in their OAuth client — the opt-in switch."""
    return _CREDS.exists()


def setup_help() -> str:
    return (
        "To connect Gmail/Calendar: make a project at console.cloud.google.com, "
        "enable the Gmail and Calendar APIs, create a Desktop OAuth client, and "
        "save it as ~/.clacky/google_credentials.json. Then run "
        'pip install "google-api-python-client google-auth-oauthlib".'
    )


def _creds():
    """Load/refresh the token, running the consent flow on first use. Blocking —
    call via asyncio.to_thread so the first-run browser prompt can't freeze the UI."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if _TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN), _SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDS), _SCOPES)
            creds = flow.run_local_server(port=0)
        _DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build
    return build(name, version, credentials=_creds(), cache_discovery=False)


# ── Gmail ────────────────────────────────────────────────────────────────
def gmail_list(query: str = "is:unread", count: int = 5) -> list[dict]:
    """Return up to `count` messages matching a Gmail search query (default
    unread), each as {from, subject, snippet}."""
    svc = _service("gmail", "v1")
    resp = svc.users().messages().list(
        userId="me", q=query or "is:unread", maxResults=max(1, min(count, 20))
    ).execute()
    out = []
    for m in resp.get("messages", []):
        full = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject"]).execute()
        headers = {h["name"]: h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        out.append({"from": headers.get("From", ""),
                    "subject": headers.get("Subject", "(no subject)"),
                    "snippet": full.get("snippet", "")})
    return out


def gmail_send(to: str, subject: str, body: str) -> dict:
    """Send an email. Irreversible/external — the caller gates this behind an
    explicit user request."""
    svc = _service("gmail", "v1")
    msg = MIMEText(body or "")
    msg["to"] = to
    msg["subject"] = subject or ""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return {"sent": True, "to": to}


# ── Calendar ───────────────────────────────────────────────────────────────
def calendar_events(count: int = 10, days: int = 1) -> list[dict]:
    """Upcoming events in the next `days` days (days=1 ≈ today), each as
    {title, start}."""
    from datetime import datetime, timezone, timedelta
    svc = _service("calendar", "v3")
    now = datetime.now(timezone.utc)
    resp = svc.events().list(
        calendarId="primary", timeMin=now.isoformat(),
        timeMax=(now + timedelta(days=max(1, days))).isoformat(),
        singleEvents=True, orderBy="startTime",
        maxResults=max(1, min(count, 25))).execute()
    out = []
    for e in resp.get("items", []):
        start = e.get("start", {})
        out.append({"title": e.get("summary", "(no title)"),
                    "start": start.get("dateTime", start.get("date", ""))})
    return out
