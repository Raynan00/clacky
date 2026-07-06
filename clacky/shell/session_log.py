"""
session_log.py — a timestamped flight recorder for a Clacky turn.

Every meaningful event is logged with a wall-clock time AND an elapsed offset, to
the console and to ~/.clacky/logs/session.log, so a whole turn can be replayed on
a timeline to debug pointing / sync / latency:

  HEAR   — speech-to-text result
  ROUTE  — which lane the request went to
  THINK  — an LLM call in flight (start + how long it took)
  SAY    — the text the model produced (what it will speak)
  POINT  — where the cursor was sent, and HOW the coord was resolved
  SNAP   — a UIA snap that was applied or rejected
  ACT    — a real action on the machine (click / type / launch)
  TTS    — audio playback actually starting

Timestamps use perf_counter for a monotonic elapsed offset (great for deltas).
"""

from __future__ import annotations

import time
from pathlib import Path

_t0 = time.perf_counter()
_fh = None


def _stamp() -> str:
    ms = int((time.time() % 1) * 1000)
    return f"{time.strftime('%H:%M:%S')}.{ms:03d} +{time.perf_counter() - _t0:7.2f}s"


def turn(label: str = "") -> None:
    """Mark the start of a new turn — RESETS the elapsed clock so the +N.NNs offsets
    show true per-turn latency (time since you released the key), not session time."""
    global _t0
    _t0 = time.perf_counter()
    slog("TURN", "──────── " + (label or "new turn") + " ────────")


def slog(category: str, msg: str = "") -> None:
    """Log one timestamped event to console + ~/.clacky/logs/session.log."""
    line = f"[{_stamp()}] {category:<6} {msg}"
    print(line, flush=True)
    global _fh
    try:
        if _fh is None:
            d = Path.home() / ".clacky" / "logs"
            d.mkdir(parents=True, exist_ok=True)
            _fh = open(d / "session.log", "a", encoding="utf-8", buffering=1)
            _fh.write(f"\n===== run @ {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        _fh.write(line + "\n")
    except Exception:
        pass
