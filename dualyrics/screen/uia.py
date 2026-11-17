"""Caption text of web video players through UI Automation (Chrome and Edge expose the page's elements with
their HTML class as the UIA class name; YouTube's captions are ``ytp-caption-segment`` inside a
``caption-window``).

The first element whose class contains one of the configured names is found once (a search of the page,
at most every few seconds) and then only its subtree is read, a few times a second. Firefox without native
UIA, and pages without such elements, give ``None``: the screen source then uses OCR.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from typing import Any, Iterable

log = logging.getLogger(__name__)

SEARCH_EVERY = 3.0          # seconds between searches of the whole page while no caption element is known
MATCH_SUBSTRING = 2         # PropertyConditionFlags_MatchSubstring (Windows 10 1809+)
IGNORE_CASE = 1
TIMEOUT_MS = 800            # UIA connection / transaction timeouts: a hung app must not stall the reader

_mod: Any = None


def _uia_module() -> Any:
    global _mod
    if _mod is None:
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0      # type: ignore[attr-defined]  # COINIT_MULTITHREADED for the importing thread
        import comtypes.client

        _mod = comtypes.client.GetModule("UIAutomationCore.dll")
    return _mod


def com_init() -> None:
    """Join the multithreaded COM apartment in this thread (harmless when it already is in one)."""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


def available() -> bool:
    """comtypes is installed and the UIAutomationCore wrapper can be loaded (call it off the Tk thread)."""
    try:
        com_init()
        _uia_module()
        return True
    except Exception as e:
        log.info("UI Automation unavailable: %s", e)
        return False


class UiaReader:
    """Use from one thread."""

    def __init__(self) -> None:
        com_init()
        import comtypes.client

        self.m = _uia_module()
        self.uia = comtypes.client.CreateObject(self.m.CUIAutomation, interface=self.m.IUIAutomation)
        try:
            u2 = self.uia.QueryInterface(self.m.IUIAutomation2)
            u2.ConnectionTimeout = TIMEOUT_MS
            u2.TransactionTimeout = TIMEOUT_MS
        except Exception:
            pass
        self._text_cond = self.uia.CreatePropertyCondition(self.m.UIA_ControlTypePropertyId,
                                                           self.m.UIA_TextControlTypeId)
        self._classes: tuple[str, ...] = ()
        self._cond: Any = None
        self._hwnd = 0
        self._root: Any = None
        self._box: Any = None
        self._search_at = -1e9
        self._had_box_at = -1e9

    def reset(self) -> None:
        self._hwnd, self._root, self._box, self._search_at = 0, None, None, -1e9

    def _condition(self, classes: tuple[str, ...]) -> Any:
        if classes != self._classes or self._cond is None:
            conds = [self.uia.CreatePropertyConditionEx(self.m.UIA_ClassNamePropertyId, c,
                                                        MATCH_SUBSTRING | IGNORE_CASE) for c in classes]
            cond = conds[0] if conds else self.uia.CreateFalseCondition()
            for c in conds[1:]:
                cond = self.uia.CreateOrCondition(cond, c)
            self._classes, self._cond, self._box = classes, cond, None
        return self._cond

    def read(self, hwnd: int, classes: Iterable[str], now: float | None = None) -> str | None:
        """Caption text now ('' = the caption element is there but empty), or None (no caption element)."""
        now = time.monotonic() if now is None else now
        classes = tuple(c for c in classes if c)
        if not classes or not hwnd:
            return None
        cond = self._condition(classes)
        if hwnd != self._hwnd:
            self.reset()
            self._hwnd = hwnd
        try:
            if self._root is None:
                self._root = self.uia.ElementFromHandle(hwnd)
            if self._box is None:
                # a caption element that just went away usually comes back with the next line: look again soon
                every = 0.5 if now - self._had_box_at < 20 else SEARCH_EVERY
                if now - self._search_at < every:
                    return None
                self._search_at = now
                box = self._root.FindFirst(self.m.TreeScope_Descendants, cond)
                if not box:
                    return None
                self._box = box
            text = self._text_of(self._box)
            self._had_box_at = now
            return text
        except Exception as e:  # element gone (page changed), app busy or closed
            log.debug("uia read: %s", e)
            self._box = None
            if not self._root_alive():
                self._root = None
            return None

    def _root_alive(self) -> bool:
        try:
            return self._root is not None and bool(self._root.CurrentProcessId)
        except Exception:
            return False

    def _text_of(self, el: Any) -> str:
        arr = el.FindAll(self.m.TreeScope_Descendants, self._text_cond)
        parts = []
        for i in range(arr.Length if arr else 0):
            name = (arr.GetElement(i).CurrentName or "").strip()
            if name and (not parts or parts[-1] != name):
                parts.append(name)
        if not parts:
            own = (el.CurrentName or "").strip()
            return own
        return " ".join(parts)
