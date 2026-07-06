"""
routing.py — how Clacky decides what a request IS.

Two tiers, cheapest first:
  1. `_fast_route` — instant local heuristics for the unambiguous cases
     ("open X", "walk me through this", "clean up my desktop"). These are
     deliberately conservative shortcuts, NOT the authority: anything they
     don't recognize falls through to tier 2.
  2. `_route` — a fast Haiku call that classifies the request into a lane
     (act / walkthrough / organize / remember / learn_skill / background /
     workspace / chat). The model is the routing authority; the regexes are
     just latency optimizations on top of it.

Also owns the shared warm HTTP/Anthropic clients (connection reuse saves a
TLS handshake per model call).
"""

from __future__ import annotations

import os
import re

from config import cfg
from session_log import slog


class RoutingMixin:
    def _get_http(self):
        """One shared httpx client for all raw Anthropic calls (tour/agent/locate).
        Reusing the pool keeps the TLS connection WARM — a fresh client per call
        was paying a ~0.2-0.4s handshake on every single model round-trip."""
        if getattr(self, "_http", None) is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=60)
        return self._http

    def _get_anthropic(self):
        """Shared Anthropic SDK client (router / workspace / research) — same
        warm-connection reasoning as _get_http."""
        if getattr(self, "_anthropic", None) is None:
            import anthropic
            self._anthropic = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
        return self._anthropic

    def _reset_clients(self):
        """Drop cached clients so the next call rebuilds them — used after network
        errors (e.g. a stale pool after system sleep/resume)."""
        self._http = None
        self._anthropic = None

    def _fast_route(self, transcript: str):
        """Instant LOCAL routing for unambiguous cases — skips the Haiku hop.
        Deliberately conservative: returns None (defer to the model) unless it's
        clearly an on-screen action or a clearly factual question. Everything subtle
        — memory, skills, background, walkthrough, 'can you…' phrasings, screen
        references — still routes through the model, so we keep its robustness."""
        s = (transcript or "").strip().lower()
        if not s:
            return None
        # Explicit "do it" trigger (Clicky's "…agent" pattern): a lead word that
        # FORCES the act path with no guessing — for control, and for reliable
        # demo takes. Checked BEFORE the length cap so long commands still trigger.
        if re.match(r"(clacky[\s,]+)?"
                    r"(go(?!\s+(over|through|back))|agent|do it|just do it|"
                    r"go ahead|take over|handle (it|this)|make it happen)\b", s):
            return "act"
        # Folder cleanup → the journaled organizer (instant route). Requires BOTH a
        # cleanup verb and a folder word so "clean up my timeline" stays with chat.
        if (not re.match(r"(how|what|where|why)\b", s)
                and re.search(r"\b(clean|tidy|organi[sz]e|sort)\b", s)
                and re.search(r"\b(desktop|downloads?|documents)\b", s)):
            return "organize"
        if len(s) > 120:
            return None
        # "walk me through" ANYWHERE in the sentence is an unambiguous tour ask
        # ("I have no idea how to use FL Studio, walk me through making a beat").
        if re.search(r"\bwalk me through\b", s) and not re.match(
                r"(how|what|where|why)\b", s):
            return "walkthrough"
        # Clear screen TOUR → walkthrough, instantly (skips the ~2s Haiku hop for
        # the most common demo phrasings). Kept unambiguous — "explain quantum
        # physics" won't match, only screen/app-referential tours.
        if re.match(r"(walk me through|give me a (tour|walk|while)[- ]?(through)?|"
                    r"walkthrough|show me around|"
                    r"what can i do (here|on|with)|what'?s on (my|the) screen|"
                    r"what am i (looking at|seeing)|"
                    r"explain (this|my|the) (screen|app|page|panel|window|interface|"
                    r"program|tool)|"
                    r"explain how (this|the) (app|program|screen) works)\b", s):
            return "walkthrough"
        # Clear on-screen action: an imperative action verb STARTS the sentence.
        if re.match(r"(open|launch|click|double.?click|right.?click|press|type\b|"
                    r"scroll|close|maximi[sz]e|minimi[sz]e)\b", s):
            return "act"
        # Clear factual question: interrogative, no action verb, no screen reference.
        if re.match(r"(what is|what's|whats|who is|who's|when (is|was|did)|"
                    r"why (is|does|do)|how (do|does|much|many|old)|define|"
                    r"explain what)\b", s) and not re.search(
                    r"\b(screen|here|this (button|icon|menu|page|window)|point|"
                    r"show me|remember|forget)\b", s):
            return "chat"
        return None

    async def _route(self, transcript: str) -> dict:
        """Decide the route WITH THE MODEL, not regex — the OpenClicky way. One
        fast Haiku call returns a structured decision: the lane, plus any fact to
        remember/forget or skill to learn. Falls back to {'route': 'chat'}."""
        import anthropic
        try:
            client = self._get_anthropic()
            sys_prompt = (
                    "Route requests for Clacky, a Windows voice assistant that can "
                    "see the screen, talk, point, control the computer, and remember "
                    "things across sessions. Pick ONE route via the `route` tool:\n"
                    "- act: DO something on the computer — open an app, click, type, "
                    "search, navigate, run a learned routine, any hands-on task.\n"
                    "- walkthrough: a warm spoken TOUR of the whole screen, pointing "
                    "out several things — 'walk me through this', 'give me a tour', "
                    "'explain my screen', 'what's on my screen', 'show me around', "
                    "'what can I do here', 'explain this app'.\n"
                    "- remember: the user is giving a fact to keep for later "
                    "('remember that...', 'my name is...', 'I always...'). Put the "
                    "distilled fact (concise, third-person) in `fact`.\n"
                    "- forget: drop something remembered — put what to forget in "
                    "`fact` (use 'everything' to wipe all).\n"
                    "- learn_skill: the user is teaching a reusable routine ('save "
                    "this as my morning routine', 'whenever I say X, do Y'). Put a "
                    "short `skill_name` and the `skill_steps`.\n"
                    "- background: the user delegates a task to run in the background "
                    "and report back later — research something, look up or find out "
                    "X, dig into a question. NOT for things they want answered right "
                    "now (chat) or done on-screen now (act).\n"
                    "- organize: tidy/clean up a FOLDER of files (desktop, downloads, "
                    "documents) — moving files into folders. NOT for on-screen UI "
                    "tasks or app windows.\n"
                    "- undo: reverse/revert the file cleanup — 'undo that', 'put my "
                    "files back', 'revert the cleanup'.\n"
                    "- chat: a specific question or ONE thing — 'what does this "
                    "button do', 'where's the X', 'what is this [single element]', "
                    "or any question not about touring the whole screen. Anything "
                    "that is NOT one of the above.")
            workspace_on = False
            try:
                import google_workspace as _gw
                workspace_on = _gw.is_configured()
            except Exception:
                workspace_on = False
            if workspace_on:
                sys_prompt += ("\n- workspace: read or send Gmail, or read Google "
                               "Calendar — the user has connected their Google "
                               "account, so prefer this for email/calendar.")
            if self._memory.skills:
                sys_prompt += ("\nKNOWN ROUTINES the user can run — route a request to "
                               "run any of these to 'act': "
                               + ", ".join(f'"{n}"' for n in self._memory.skills) + ".")
            route_enum = ["act", "walkthrough", "remember", "forget",
                          "learn_skill", "background", "organize", "undo", "chat"]
            if workspace_on:
                route_enum.insert(0, "workspace")
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=256,
                system=sys_prompt,
                tools=[{"name": "route", "description": "Choose the route.",
                        "input_schema": {"type": "object", "properties": {
                            "route": {"type": "string", "enum": route_enum},
                            "fact": {"type": "string"},
                            "skill_name": {"type": "string"},
                            "skill_steps": {"type": "string"}},
                            "required": ["route"], "additionalProperties": False}}],
                tool_choice={"type": "tool", "name": "route"},
                messages=[{"role": "user", "content": transcript}],
            )
            for b in resp.content:
                if b.type == "tool_use":
                    return b.input or {"route": "chat"}
        except Exception as e:
            print("[clacky-debug] route error:", e, flush=True)
            self._reset_clients()   # stale pool (e.g. after sleep) → rebuild next turn
        return {"route": "chat"}

