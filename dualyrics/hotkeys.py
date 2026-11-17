"""Global hotkeys with RegisterHotKey (ctypes, no extra dependency) on a dedicated message-loop thread."""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable

log = logging.getLogger(__name__)

MODS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK: dict[str, int] = {
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "space": 0x20, "enter": 0x0D, "tab": 0x09,
    "pageup": 0x21, "pagedown": 0x22, "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
}
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"f{n}": 0x6F + n for n in range(1, 25)})


def parse(combo: str) -> tuple[int, int] | None:
    """'ctrl+alt+up' -> (modifiers, virtual key). Needs at least one modifier."""
    parts = [p for p in combo.lower().replace(" ", "").split("+") if p]
    if len(parts) < 2:
        return None
    mods = 0
    for p in parts[:-1]:
        if p not in MODS:
            return None
        mods |= MODS[p]
    vk = VK.get(parts[-1])
    return (mods, vk) if vk else None


class HotkeyManager:
    """Registers {action: combo}; calls ``on_hotkey(action)`` from its own thread."""

    def __init__(self, on_hotkey: Callable[[str], None]) -> None:
        self.on_hotkey = on_hotkey
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self.failed: list[str] = []

    def start(self, bindings: dict[str, str]) -> list[str]:
        """(Re)register hotkeys. Returns the combos that could not be registered."""
        self.stop()
        bindings = {a: c for a, c in bindings.items() if c}
        if not bindings:
            self.failed = []
            return []
        ready = threading.Event()
        self._thread = threading.Thread(target=self._loop, args=(bindings, ready), name="hotkeys", daemon=True)
        self._thread.start()
        ready.wait(2)
        return self.failed

    def _loop(self, bindings: dict[str, str], ready: threading.Event) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # make sure this thread has a message queue
        ids: dict[int, str] = {}
        failed: list[str] = []
        for n, (action, combo) in enumerate(bindings.items(), start=1):
            parsed = parse(combo)
            if parsed and user32.RegisterHotKey(None, n, parsed[0] | MOD_NOREPEAT, parsed[1]):
                ids[n] = action
            else:
                failed.append(combo)
        self.failed = failed
        if failed:
            log.warning("hotkeys not registered: %s", failed)
        ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                try:
                    self.on_hotkey(ids[msg.wParam])
                except Exception:
                    log.exception("hotkey handler failed")
        for n in ids:
            user32.UnregisterHotKey(None, n)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = 0
