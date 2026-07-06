"""
deepgram_streaming.py — live (WebSocket) Deepgram STT for push-to-talk.

The batch provider (`deepgram_stt.py`) can only start transcribing *after* you
release the key — all of its latency is post-release dead air. This streams the
audio to Deepgram *while you're still speaking*, so the transcript is essentially
ready the instant you let go (push-to-talk = we know exactly when you stopped, so
no silence-detection wait is needed).

Design: audio arrives on the sounddevice thread via `feed()` (non-blocking,
thread-safe queue). A sender task ships frames to the WebSocket; a receiver task
accumulates the `is_final` segments. `finish()` flushes the tail and returns the
joined transcript. Any failure returns "" so the caller can fall back to batch —
the full PCM buffer is always still captured.

Opt-in via CLICKY/MITTS env (the manager gates it); off by default.
"""

from __future__ import annotations

import asyncio
import json
import queue

import websockets

from config import cfg

_WS_URL = (
    "wss://api.deepgram.com/v1/listen?model=nova-3&language=en"
    "&encoding=linear16&sample_rate=16000&channels=1"
    "&smart_format=true&punctuate=true&interim_results=true&endpointing=300"
)


def _ws_url() -> str:
    """Base URL + nova-3 keyterm boosting: bias recognition toward words that
    must come through clean — the app's name plus anything in
    CLACKY_STT_KEYTERMS (comma-separated, e.g. 'FL Studio,Premiere')."""
    import os
    from urllib.parse import quote
    terms = ["Clacky"]
    extra = os.environ.get("CLACKY_STT_KEYTERMS", "")
    terms += [t.strip() for t in extra.split(",") if t.strip()]
    return _WS_URL + "".join(f"&keyterm={quote(t)}" for t in terms)


class DeepgramStreamingSession:
    """One push-to-talk utterance, streamed live to Deepgram."""

    def __init__(self):
        self._q: "queue.SimpleQueue" = queue.SimpleQueue()
        self._finals: list[str] = []
        self._ws = None
        self._sender = None
        self._receiver = None

    async def start(self) -> bool:
        """Open the socket and start the pump tasks. Returns False on failure."""
        try:
            self._ws = await websockets.connect(
                _ws_url(),
                additional_headers={"Authorization": f"Token {cfg.deepgram_api_key}"},
                max_size=None,
            )
        except Exception as e:
            print(f"[clacky-debug] deepgram stream connect failed: {e}", flush=True)
            self._ws = None
            return False
        self._sender = asyncio.create_task(self._send_loop())
        self._receiver = asyncio.create_task(self._recv_loop())
        return True

    def feed(self, chunk: bytes):
        """Called from the audio thread — non-blocking, thread-safe."""
        if chunk:
            self._q.put(chunk)

    async def _send_loop(self):
        loop = asyncio.get_event_loop()
        try:
            while True:
                chunk = await loop.run_in_executor(None, self._q.get)
                if chunk is None:                 # sentinel from finish()
                    break
                if self._ws is not None:
                    await self._ws.send(chunk)
        except Exception:
            pass

    async def _recv_loop(self):
        try:
            async for msg in self._ws:
                try:
                    data = json.loads(msg)
                except Exception:
                    continue
                alt = (data.get("channel", {}).get("alternatives") or [{}])[0]
                text = (alt.get("transcript") or "").strip()
                if text and data.get("is_final"):
                    self._finals.append(text)
        except Exception:
            pass

    async def finish(self, grace: float = 0.4) -> str:
        """Flush the tail, close, and return the joined transcript ("" on failure).
        `grace` is the short wait for Deepgram's final segment(s) after Finalize —
        small because the audio was already streamed during speech."""
        self._q.put(None)                          # stop the sender
        try:
            if self._ws is not None:
                try:
                    await self._ws.send(json.dumps({"type": "Finalize"}))
                except Exception:
                    pass
                await asyncio.sleep(grace)         # collect trailing final(s)
                try:
                    await self._ws.send(json.dumps({"type": "CloseStream"}))
                except Exception:
                    pass
                try:
                    await self._ws.close()
                except Exception:
                    pass
        finally:
            for t in (self._sender, self._receiver):
                if t is not None:
                    t.cancel()
        return " ".join(self._finals).strip()
