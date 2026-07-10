"""
Just-in-time app connect prompt (the Claude-Cowork pattern).

A background task asked to deliver its output into an app that isn't wired
into the background lane yet ("…and put it in my Notion"). Instead of the
agent silently improvising, Clacky pops this dialog. The primary path is a
browser sign-in — the OAuth flow Claude Code uses for MCP servers: click
Connect, approve in the browser, done. No tokens to hunt down. Pasting a
URL/token by hand is the fallback for servers that don't do OAuth (e.g.
Composio's per-user URLs). Skipping just falls back to files — this is an
offer, never a gate.
"""

from __future__ import annotations

import threading
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit
)

from clacky.connections import api_key_header_for, known_app_url


class ConnectDialog(QDialog):
    """One question: connect <app> now, or skip and fall back to files?

    `on_done(connected: bool)` is called exactly once, whatever the user does
    (connect, skip, Esc, or closing the window)."""

    status_signal = pyqtSignal(str)          # progress line from the OAuth thread
    oauth_done    = pyqtSignal(bool, str)    # (ok, error message)

    def __init__(self, app_name: str, on_done: Callable[[bool], None], parent=None):
        super().__init__(parent)
        self._app = app_name
        self._on_done = on_done
        self._answered = False
        self._busy = False

        self.setWindowTitle(f"Connect {app_name}")
        self.setModal(False)
        self.setMinimumSize(480, 220)
        self.setStyleSheet("""
            QDialog { background: #0e1014; color: #e8eaed; }
            QLabel  { color: #e8eaed; }
            QLabel#title { font-size: 20px; font-weight: 700; }
            QLabel#subtitle { color: #a0a3a8; font-size: 13px; }
            QLabel#status { color: #c8cbd0; font-size: 13px; }
            QLineEdit {
                background: #1a1d22; border: 1px solid #2a2d33;
                border-radius: 6px; padding: 8px; color: #e8eaed;
            }
            QPushButton {
                background: #1f6feb; color: white; border: none;
                padding: 10px 18px; border-radius: 8px;
                font-weight: 600; font-size: 13px;
            }
            QPushButton:hover  { background: #2f7fff; }
            QPushButton:disabled { background: #333; color: #888; }
            QPushButton#secondary {
                background: transparent; color: #a0a3a8;
                border: 1px solid #2a2d33;
            }
            QPushButton#secondary:hover { color: #e8eaed; border-color: #444; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 26, 32, 22)
        layout.setSpacing(12)

        title = QLabel(f"Connect {app_name}?")
        title.setObjectName("title")
        layout.addWidget(title)

        known = known_app_url(app_name)
        key_based = bool(known and api_key_header_for(known))
        if key_based:
            how = (f'Paste your API key from '
                   f'<a href="https://dashboard.composio.dev" '
                   f'style="color:#2f7fff">dashboard.composio.dev</a> — one '
                   f"key connects her to 1000+ apps.")
        elif known:
            how = "Connect signs you in with your browser."
        else:
            how = (f"Paste its MCP server URL — "
                   f'<a href="https://composio.dev" style="color:#2f7fff">composio.dev</a> '
                   f"hosts one for most apps.")
        subtitle = QLabel(
            f"This task wants to deliver into <b>{app_name}</b>. " + how)
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        subtitle.setOpenExternalLinks(True)
        layout.addWidget(subtitle)

        # Plumbing appears only when it's actually needed — a known OAuth app
        # is just a sentence and a Connect button, like Claude Code's flow.
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("server URL  (or a local command)")
        if known:
            self.url_edit.setText(known)
            self.url_edit.hide()
        layout.addWidget(self.url_edit)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if key_based:
            self.token_edit.setPlaceholderText("Composio API key")
        else:
            self.token_edit.setPlaceholderText(
                "token — only for servers without browser sign-in")
            if known:
                self.token_edit.hide()
        layout.addWidget(self.token_edit)

        if known and not key_based:
            adv = QLabel('<a href="#" style="color:#5a5d63">use a custom '
                         'server or token instead…</a>')
            adv.linkActivated.connect(lambda _:
                (self.url_edit.show(), self.token_edit.show(), adv.hide()))
            layout.addWidget(adv)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        layout.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.skip_btn = QPushButton("Skip — just leave me files")
        self.skip_btn.setObjectName("secondary")
        self.skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.skip_btn)
        btn_row.addStretch(1)
        self.connect_btn = QPushButton("Connect && continue")
        self.connect_btn.clicked.connect(self._on_connect)
        btn_row.addWidget(self.connect_btn)
        layout.addLayout(btn_row)

        self.status_signal.connect(self.status.setText)
        self.oauth_done.connect(self._on_oauth_done)

    # ── Outcomes (each path answers exactly once) ─────────────────────────────

    def _finish(self, connected: bool):
        if self._answered:
            return
        self._answered = True
        try:
            self._on_done(connected)
        except Exception:
            pass

    def _on_connect(self):
        if self._busy:
            return
        target = self.url_edit.text().strip()
        token = self.token_edit.text().strip()
        if not target:
            self.status.setText("⚠️ Paste a server URL (or local command) first.")
            return

        # Key-based servers (Composio et al.) need their key — no browser flow.
        if (not token and target.startswith(("http://", "https://"))
                and api_key_header_for(target)):
            self.status.setText(
                "⚠️ This server uses an API key — paste it in the field above "
                "(Composio: dashboard.composio.dev).")
            return

        # Token given, or a local command → static config, no browser needed.
        if token or not target.startswith(("http://", "https://")):
            try:
                from clacky.connections import add_server
                add_server(self._app, target, token or None)
            except Exception as e:
                self.status.setText(f"⚠️ Couldn't save that: {e}")
                return
            self._finish(True)
            self.accept()
            return

        # Primary path: browser sign-in (OAuth), off the UI thread.
        self._busy = True
        self.connect_btn.setEnabled(False)
        self.status.setText("Contacting the server…")

        def _worker():
            try:
                from clacky.connections import connect_oauth
                connect_oauth(self._app, target,
                              on_status=self.status_signal.emit)
                self.oauth_done.emit(True, "")
            except Exception as e:
                self.oauth_done.emit(False, str(e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_oauth_done(self, ok: bool, err: str):
        self._busy = False
        self.connect_btn.setEnabled(True)
        if ok:
            self._finish(True)
            self.accept()
            return
        self.url_edit.show()
        self.token_edit.show()
        self.status.setText(
            f"⚠️ Browser sign-in didn't work ({err}). If this server uses "
            f"plain tokens, paste one above and hit Connect again.")

    def reject(self):          # Skip button, Esc, and the window's ✕ all land here
        self._finish(False)
        super().reject()
