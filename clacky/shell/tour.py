"""
tour.py — pointing and the guided screen tour.

The design that finally made pointer/voice sync solid (after several failed
attempts): the model returns the WHOLE tour in one response with inline
[POINT:x,y] tags placed immediately before the sentence that describes each
element. Pointer timing is derived from tag position in the spoken text — one
channel, so they cannot drift. (A multi-turn tool-use loop is structurally
one step late: each turn's text describes the previous turn's move.)

Also owns the chat path's pointing glue: resolving [POINT] tags via the UIA
tree with the model's own coordinate as fallback, and the nudge-snap that
corrects near-misses to exact control centers without ever jumping into
containers.
"""

from __future__ import annotations

import asyncio
import re
import time

from config import cfg
from session_log import slog
from screen.capture import capture_all_screens
from ui.panel import AppState

# ── spoken-reply tag grammar (shared with the chat pipeline) ──────────────
POINT_RE = re.compile(r'\[POINT:(\d+),(\d+):([^:\]]+):screen(\d+)\]')
# Whiteboard annotation tags — same idea as POINT, parsed and stripped.
ARROW_RE     = re.compile(r'\[ARROW:(\d+),(\d+)->(\d+),(\d+)\]')
CIRCLE_RE    = re.compile(r'\[CIRCLE:(\d+),(\d+),(\d+):([^\]]+)\]')
UNDERLINE_RE = re.compile(r'\[UNDERLINE:(\d+),(\d+),(\d+)\]')
LABEL_RE     = re.compile(r'\[LABEL:(\d+),(\d+):([^\]]+)\]')


class TourMixin:
    def _fire_main_point(self):
        """Fire the single held point ONCE — but only after speech has started, so
        the cursor never points before any talking. Called both when the point
        resolves AND when speech begins; whichever happens later actually fires it.
        This makes pointing reliable even if the model emits [POINT] mid-reply
        (which used to be silently dropped)."""
        if self._main_point_fired or not self._speech_started:
            return
        mp = self._main_point
        if mp is None:
            return
        self._main_point_fired = True
        self._pointing_held = True
        self.sig_point_hold.emit(True)
        self.sig_point_at.emit(mp[0], mp[1], mp[2])

    def _snap_to_uia(self, vx, vy):
        """Snap a PHYSICAL screen coordinate to the exact center of the small UI
        control under it (pixel-perfect), or return None to keep the original.
        Guards against snapping to big containers/windows, whose center would be
        far from the intended element. Reuses the same uiautomation the chat path
        uses, so no new dependency."""
        try:
            import uiautomation as auto
            ctrl = auto.ControlFromPoint(int(vx), int(vy))
            if ctrl is None:
                return None
            r = ctrl.BoundingRectangle
            w, h = r.right - r.left, r.bottom - r.top
            name = (getattr(ctrl, "Name", "") or "")[:24]
            # Only snap to genuine SMALL controls (buttons/fields/icons) — never big
            # panels/containers (e.g. Premiere's custom "TabPanel" windows).
            if w <= 1 or h <= 1 or w > 600 or h > 360:
                slog("SNAP", f"skip: control {w}x{h} too big '{name}'")
                return None
            cx, cy = r.left + w / 2.0, r.top + h / 2.0
            dist = ((cx - vx) ** 2 + (cy - vy) ** 2) ** 0.5
            # A real correction only NUDGES to the element's exact center. A far jump
            # means we hit a container, not the target → keep the model's coordinate.
            if dist > 30:
                slog("SNAP", f"skip: {dist:.0f}px jump to '{name}' (container)")
                return None
            slog("SNAP", f"({int(vx)},{int(vy)})->({int(cx)},{int(cy)}) {dist:.0f}px '{name}'")
            return (cx, cy)
        except Exception:
            return None

    def _point_to_logical(self, cx, cy, screen_n=1):
        """Scale a coordinate from the screenshot's pixel space to logical screen
        coords (Qt cursor space) for the given 1-based screen. Returns (None, None)
        if there's no screenshot or the coordinate is a dummy 0,0."""
        shots = getattr(self, "_point_shots", None)
        if not shots or (cx == 0 and cy == 0):
            return None, None
        idx = screen_n - 1
        shot = shots[idx] if 0 <= idx < len(shots) else shots[0]
        w = shot.width or 1
        h = shot.height or 1
        cx = max(0.0, min(float(cx), w)); cy = max(0.0, min(float(cy), h))
        scale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0
        pw = shot.physical_width or w
        ph = shot.physical_height or h
        lx = int(round((cx / w * pw + shot.physical_left) / scale))
        ly = int(round((cy / h * ph + shot.physical_top) / scale))
        return lx, ly

    def _parse_points(self, text: str, collect: bool = False):
        """Parse [POINT] tags and resolve them to accurate coords via UIA. If
        `collect`, only the FIRST resolved point is kept (in self._main_point) to be
        held for the whole response — Clicky's model — instead of firing every tag
        immediately. Non-collect mode fires each immediately (whole-reply fallback)."""
        for match in POINT_RE.finditer(text):
            if getattr(self, "_suppress_llm_point", False):
                continue  # an accurate detected coordinate already drives the cursor
            xs, ys, label, sn = match.groups()
            label = label.strip()
            if label in self._pointed_labels:
                continue  # already handled this one during the response
            self._pointed_labels.add(label)
            # Reliable pointing (OpenClicky/Clicky-style): SNAP to the exact UIA
            # element when we can find it by label (~5ms, pixel-perfect), otherwise
            # TRUST THE MODEL'S OWN coordinate from the tag. Always shows a point —
            # no more "explains without pointing" when UIA doesn't have the element.
            lx = ly = None
            try:
                from ai.hybrid_pointer import find_target
                t = find_target(label, skip_ocr=True, skip_vision=True)
            except Exception:
                t = None
            if t is not None:
                lx, ly = float(t.x), float(t.y)
                slog("POINT", f"'{label}' -> ({lx:.0f},{ly:.0f}) via UIA (exact)")
            else:
                mlx, mly = self._point_to_logical(float(xs or 0), float(ys or 0),
                                                  int(sn) if sn else 1)
                if mlx is not None:
                    lx, ly = float(mlx), float(mly)
                    slog("POINT", f"'{label}' -> ({lx:.0f},{ly:.0f}) via model coord")
            if lx is None:
                slog("POINT", f"'{label}' -> DROPPED (no coordinate)")
                continue
            if collect:
                if self._main_point is None:      # ONE held point per response
                    self._main_point = (lx, ly, label)
                    self._fire_main_point()       # fires if speech already started
            else:
                self.sig_point_at.emit(lx, ly, label)
        for match in ARROW_RE.finditer(text):
            x1, y1, x2, y2 = (float(v) for v in match.groups())
            self.sig_arrow.emit(x1, y1, x2, y2)
        for match in CIRCLE_RE.finditer(text):
            x, y, r, _label = match.groups()
            self.sig_circle.emit(float(x), float(y), float(r))
        for match in UNDERLINE_RE.finditer(text):
            x, y, w = (float(v) for v in match.groups())
            self.sig_underline.emit(x, y, w)
        for match in LABEL_RE.finditer(text):
            x, y, txt = match.groups()
            self.sig_label.emit(float(x), float(y), txt.strip())

    # Inline tour tag: [POINT:x,y] immediately BEFORE the sentence it belongs to.
    _TOUR_TAG_RE = re.compile(r"\[POINT:\s*(\d+)\s*,\s*(\d+)\s*\]")

    @staticmethod
    def _bubble_label(text: str, maxlen: int = 38) -> str:
        """A clean caption for the cursor bubble: the first phrase of the sentence,
        cut at a natural boundary (sentence end, em-dash, comma, colon) — never a
        mid-word chop like 'This is the Mixer — each vertica'."""
        t = " ".join((text or "").split())
        m = re.search(r"[.!?]|\s[—–-]\s|[,:;]", t)
        if m and m.start() >= 8:            # don't cut absurdly short
            t = t[:m.start()]
        if len(t) > maxlen:
            t = t[:maxlen].rsplit(" ", 1)[0] + "…"
        return t.strip()

    def _parse_tour_segments(self, text: str):
        """Split tour text into [(coords|None, sentence), ...]. Each [POINT:x,y]
        tag applies to the text that FOLLOWS it (until the next tag), so the
        pairing comes from the model's own text order — it cannot be off by one."""
        parts = self._TOUR_TAG_RE.split(text or "")
        segs = []
        intro = parts[0].strip()
        if intro:
            segs.append((None, intro))
        for i in range(1, len(parts) - 2, 3):
            seg = parts[i + 2].strip()
            if seg:
                segs.append(((int(parts[i]), int(parts[i + 1])), seg))
        return segs

    async def _run_narration(self, transcript: str):
        """Screen tour, the way Clicky/OpenClicky actually do it: ONE model call
        returns the whole spoken tour with inline [POINT:x,y] tags placed right
        before the sentence that describes each element. Each point fires exactly
        when its sentence's audio starts, so pointer and voice share ONE channel
        (the text) and can never drift apart.

        (The old multi-turn Computer-Use loop was structurally one step late:
        forced tool_choice made turn 1 point with no speech, and every later
        turn's text described the PREVIOUS turn's move — off by one, forever.)"""
        import base64
        import httpx
        from ai.element_locator import _pick_resolution, _resize_jpeg, _API_URL

        api_key = cfg.anthropic_api_key
        # Use the screenshot prewarmed on key-press (captured while the user was
        # still talking) — a fresh capture+resize here costs ~0.2s on the critical path.
        pre = getattr(self, "_prewarmed", None)
        if pre and (time.monotonic() - pre[0]) < 8.0:
            shots = pre[1]
        else:
            shots = capture_all_screens()
        if not shots or not api_key:
            await self._reply_local("I can't see your screen right now.")
            return
        shot = shots[0]
        tw, th = _pick_resolution(shot.width, shot.height)
        resized_b64 = base64.b64encode(
            _resize_jpeg(base64.b64decode(shot.base64_jpeg), tw, th)).decode("ascii")

        pw = shot.physical_width or shot.width
        ph = shot.physical_height or shot.height
        dscale = shot.dpi_scale if shot.dpi_scale > 0 else 1.0

        def to_logical(cx, cy):
            cx = max(0.0, min(float(cx), tw)); cy = max(0.0, min(float(cy), th))
            vx = cx / tw * pw + shot.physical_left
            vy = cy / th * ph + shot.physical_top
            snapped = self._snap_to_uia(vx, vy)   # pixel-perfect if a small control's there
            if snapped is not None:
                vx, vy = snapped
            return int(round(vx / dscale)), int(round(vy / dscale))

        system = (
            "You are Clacky, a warm, upbeat guide giving the user a quick spoken "
            "TOUR of their screen — like a friend showing them around an app, or a "
            f"patient teacher. The attached screenshot is {tw}x{th} pixels, origin "
            "top-left. Pick the 3-4 most useful things on screen. For EACH one, "
            "write a line in EXACTLY this format:\n"
            "[POINT:x,y] One short, friendly sentence teaching what it is and what "
            "it's for.\n"
            "where x,y is the pixel coordinate of the CENTER of that element in the "
            "attached image, and the tag comes immediately BEFORE its sentence. "
            "Teach, don't just name (e.g. '[POINT:640,52] This is the search bar — "
            "type here to jump to anything'). You may open with ONE short greeting "
            "sentence (no tag) and close with ONE short warm line (no tag). Never "
            "say coordinates aloud, never say 'screenshot'. Talk for the ear: "
            "short, natural, encouraging — like you're genuinely happy to help."
        )
        body = {"model": "claude-sonnet-5", "max_tokens": 1024, "system": system,
                "messages": [{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": resized_b64}},
                    {"type": "text", "text": transcript}]}]}
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}

        self._cancel_flag = False
        self._emit_state(AppState.THINKING)
        held = False
        try:
            import json as _json
            http = self._get_http()   # shared warm pool — no per-tour TLS handshake

            async def _synth(t):
                try:
                    audio = await self._get_tts().synth(t)
                    if not audio:
                        slog("ERROR", f"segment synth EMPTY: {t[:40]!r}")
                    return audio
                except Exception as e:
                    slog("ERROR", f"segment synth failed ({e}): {t[:40]!r}")
                    return b""

            async def seg_stream():
                """Yield (coords, sentence) segments AS the model writes them —
                SSE streaming, so the greeting can speak while the rest of the
                tour is still generating. Falls back to the batch call only if
                the stream dies before yielding anything (a mid-stream failure
                after speech has started just ends the tour early — never replays)."""
                got_any = False
                try:
                    sbody = dict(body); sbody["stream"] = True
                    slog("THINK", "tour model call (streaming)...")
                    _t = time.perf_counter()
                    buf, cur = "", None
                    async with http.stream("POST", _API_URL, json=sbody,
                                           headers=headers) as r:
                        if r.status_code >= 400:
                            detail = (await r.aread())[:200]
                            raise RuntimeError(f"HTTP {r.status_code}: {detail}")
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            try:
                                evt = _json.loads(line[5:].strip())
                            except Exception:
                                continue
                            if evt.get("type") == "content_block_delta":
                                d = evt.get("delta", {})
                                if d.get("type") != "text_delta":
                                    continue
                                buf += d.get("text", "")
                                # A segment completes when the NEXT tag appears.
                                while True:
                                    m = self._TOUR_TAG_RE.search(buf)
                                    if not m:
                                        break
                                    pre = buf[:m.start()].strip()
                                    if pre:
                                        if not got_any:
                                            slog("THINK", "...first segment at "
                                                 f"{time.perf_counter() - _t:.2f}s")
                                        got_any = True
                                        yield (cur, pre)
                                    cur = (int(m.group(1)), int(m.group(2)))
                                    buf = buf[m.end():]
                            elif evt.get("type") == "message_stop":
                                break
                    # Trailing segment (strip any dangling partial tag).
                    tail = re.sub(r"\[POINT:[^\]]*$", "", buf).strip()
                    if tail:
                        got_any = True
                        yield (cur, tail)
                    slog("THINK", f"...stream done in {time.perf_counter() - _t:.2f}s")
                    if got_any:
                        return
                except Exception as e:
                    slog("ERROR", f"tour stream failed: {e}")
                    self._reset_clients()
                    if got_any:
                        return            # spoke some of it — end early, don't replay
                # Batch fallback — one plain call, then parse (the pre-stream path).
                slog("THINK", "tour model call (batch fallback)...")
                try:
                    r = await self._get_http().post(_API_URL, json=body,
                                                    headers=headers)
                    if r.status_code >= 400:
                        slog("ERROR", f"tour HTTP {r.status_code}: {r.text[:200]}")
                        return
                    text = " ".join(b.get("text", "") for b in
                                    r.json().get("content", [])
                                    if b.get("type") == "text")
                    for seg in self._parse_tour_segments(text):
                        yield seg
                except Exception as e:
                    slog("ERROR", f"tour batch fallback failed: {e}")
                    self._reset_clients()

            # Producer: pull segments off the stream, start each synth IMMEDIATELY.
            seg_q: asyncio.Queue = asyncio.Queue()

            async def _pump():
                try:
                    async for coords, seg_text in seg_stream():
                        seg_q.put_nowait(
                            (coords, seg_text,
                             asyncio.create_task(_synth(seg_text))))
                finally:
                    seg_q.put_nowait(None)
            pump_task = asyncio.create_task(_pump())

            # Consumer: play in order, gapless; each point fires exactly as its
            # sentence's audio starts. Later segments synth while earlier ones play.
            from audio.playback import play_mp3_async
            spoken = []
            while True:
                item = await seg_q.get()
                if item is None:
                    break
                coords, seg_text, synth_task = item
                if self._cancel_flag:
                    synth_task.cancel()
                    continue
                audio = await synth_task
                if not spoken:
                    self._emit_state(AppState.SPEAKING)
                if coords is not None:
                    lx, ly = to_logical(*coords)
                    label = self._bubble_label(seg_text)
                    slog("POINT", f"cursor -> ({lx},{ly}) '{label}'")
                    if not held:
                        self.sig_point_hold.emit(True)
                        held = True
                    self.sig_point_at.emit(float(lx), float(ly), label)
                self.sig_response_chunk.emit(seg_text + " ")
                slog("TTS", f"speaking: {seg_text[:40]!r}")
                spoken.append(seg_text)
                if audio:
                    await play_mp3_async(audio)
                elif seg_text:
                    try:
                        await self._get_tts().speak(seg_text)   # synth-miss fallback
                    except Exception as e:
                        slog("ERROR", f"segment SKIPPED (no audio): {seg_text[:40]!r} ({e})")
            await pump_task
            if not spoken:
                await self._reply_local("I couldn't read the screen just now — "
                                        "mind asking again?")
                return
            clean = " ".join(spoken)
            self.sig_response_done.emit(clean)
            self._last_response = clean   # tag-free, for "say it again"
        except Exception as e:
            import traceback
            print("[clacky-debug] narration error:", flush=True)
            traceback.print_exc()
            self.sig_error.emit(str(e))
        finally:
            self.sig_point_release.emit()
            self._emit_state(AppState.IDLE)

