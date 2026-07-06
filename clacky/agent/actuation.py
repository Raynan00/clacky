"""
actuation.py — the Windows action executor (Phase 2 scaffold).

Turns the agent's chosen actions ("click here", "type this") into real OS
input. This is the layer that makes Clacky *act* rather than just point.

It deliberately mirrors the action vocabulary of Claude's Computer Use tool
(left_click / type / key / scroll / screenshot / …) so the agent loop in
``computer_loop.py`` can hand actions straight through after the permission
gate clears them.

Coordinate contract: coordinates are LOGICAL screen pixels, the same space
Bitshank's ``ai/hybrid_pointer.py`` ``Target.center_xy`` produces and the same
the cursor overlay flies to. Reuse Bitshank's DPI / multi-monitor mapping —
do NOT re-derive it here.

Implementation note: the concrete backend is Win32 ``SendInput`` (via
``pywin32`` or ``ctypes``) or ``pyautogui`` as a first cut. Kept abstract so
the agent loop and the trust tests don't depend on a real desktop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Actuator(ABC):
    """Executes a single validated action. Implementations touch the real OS;
    tests use a recording fake."""

    @abstractmethod
    def left_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    def double_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    def right_click(self, x: int, y: int) -> None: ...

    @abstractmethod
    def move(self, x: int, y: int) -> None: ...

    @abstractmethod
    def scroll(self, x: int, y: int, dy: int) -> None: ...

    @abstractmethod
    def type_text(self, text: str) -> None: ...

    @abstractmethod
    def key(self, combo: str) -> None: ...      # e.g. "ctrl+a", "enter"


class RecordingActuator(Actuator):
    """Test/dry-run actuator — records calls instead of touching the OS.

    Lets the agent loop and the trust gate be exercised headlessly (and on
    camera as a 'preview' mode) with zero risk to the machine.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def left_click(self, x, y):   self.calls.append(("left_click", x, y))
    def double_click(self, x, y): self.calls.append(("double_click", x, y))
    def right_click(self, x, y):  self.calls.append(("right_click", x, y))
    def move(self, x, y):         self.calls.append(("move", x, y))
    def scroll(self, x, y, dy):   self.calls.append(("scroll", x, y, dy))
    def type_text(self, text):    self.calls.append(("type_text", text))
    def key(self, combo):         self.calls.append(("key", combo))


class WindowsActuator(Actuator):
    """Real backend via pynput. Coordinates are PHYSICAL screen pixels (the same
    space Win32 SetCursorPos uses). pynput is imported lazily so importing this
    module (e.g. for RecordingActuator in tests) never requires it."""

    def __init__(self):
        from pynput.mouse import Controller as _Mouse, Button as _Button
        from pynput.keyboard import Controller as _Keyboard
        self._mouse = _Mouse()
        self._Button = _Button
        self._kbd = _Keyboard()

    def left_click(self, x, y):
        self._mouse.position = (int(x), int(y))
        self._mouse.click(self._Button.left, 1)

    def double_click(self, x, y):
        self._mouse.position = (int(x), int(y))
        self._mouse.click(self._Button.left, 2)

    def right_click(self, x, y):
        self._mouse.position = (int(x), int(y))
        self._mouse.click(self._Button.right, 1)

    def move(self, x, y):
        self._mouse.position = (int(x), int(y))

    def scroll(self, x, y, dy):
        self._mouse.position = (int(x), int(y))
        self._mouse.scroll(0, dy)

    def type_text(self, text):
        self._kbd.type(text)

    def key(self, combo):
        """Press a key or chord: 'enter', 'cmd' (the Windows key), 'ctrl+c',
        'cmd+space', 'alt+tab'. Modifiers are held while the final key is tapped."""
        from pynput.keyboard import Key
        mods = {"ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt,
                "shift": Key.shift, "cmd": Key.cmd, "win": Key.cmd,
                "super": Key.cmd, "meta": Key.cmd}
        named = {"enter": Key.enter, "return": Key.enter, "tab": Key.tab,
                 "esc": Key.esc, "escape": Key.esc, "space": Key.space,
                 "backspace": Key.backspace, "delete": Key.delete, "del": Key.delete,
                 "up": Key.up, "down": Key.down, "left": Key.left, "right": Key.right,
                 "home": Key.home, "end": Key.end, "pageup": Key.page_up,
                 "pagedown": Key.page_down, "cmd": Key.cmd, "win": Key.cmd,
                 "super": Key.cmd}
        parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
        if not parts:
            return
        held = [mods[p] for p in parts[:-1] if p in mods]
        last = parts[-1]
        key = named.get(last, last if len(last) == 1 else None)
        if key is None:
            return
        for m in held:
            self._kbd.press(m)
        try:
            self._kbd.press(key)
            self._kbd.release(key)
        finally:
            for m in reversed(held):
                self._kbd.release(m)
