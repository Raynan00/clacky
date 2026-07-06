"""
Heuristic provider — no AI, no API key, no network. A deterministic baseline
that sorts by file type (and a few name cues). Always available, so Clacky works
out of the box and in CI/demos. Less smart than an LLM (no content reasoning),
but guaranteed to run.

Implements the same contract as the LLM providers: returns the JSON plan the
planner expects.
"""

from __future__ import annotations

import json

from .base import LLMProvider

_BY_EXT = {
    # images
    ".png": "Images", ".jpg": "Images", ".jpeg": "Images", ".gif": "Images",
    ".webp": "Images", ".bmp": "Images", ".heic": "Images", ".svg": "Images",
    # documents
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents",
    ".txt": "Documents", ".md": "Documents", ".rtf": "Documents",
    ".odt": "Documents", ".pages": "Documents",
    # spreadsheets / data
    ".csv": "Spreadsheets", ".xls": "Spreadsheets", ".xlsx": "Spreadsheets",
    ".json": "Data", ".xml": "Data",
    # installers / archives
    ".exe": "Installers", ".msi": "Installers", ".dmg": "Installers",
    ".zip": "Archives", ".rar": "Archives", ".7z": "Archives",
    ".tar": "Archives", ".gz": "Archives",
    # media
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio",
    ".mp4": "Video", ".mov": "Video", ".avi": "Video", ".mkv": "Video",
    # code
    ".py": "Code", ".js": "Code", ".ts": "Code", ".html": "Code",
    ".css": "Code", ".c": "Code", ".cpp": "Code", ".go": "Code", ".rs": "Code",
}


class HeuristicProvider(LLMProvider):
    name = "heuristic"

    def __init__(self, model: str | None = None):
        pass  # no model

    @staticmethod
    def _folder(info: dict) -> str:
        name = info.get("name", "").lower()
        if "screenshot" in name or "screen shot" in name:
            return "Screenshots"
        return _BY_EXT.get(info.get("ext", ""), "Misc")

    def complete(self, system: str, user: str) -> str:
        files = json.loads(user)["files"]
        moves = [
            {"name": f["name"], "folder": self._folder(f), "reason": "sorted by type"}
            for f in files
        ]
        return json.dumps({"moves": moves})
