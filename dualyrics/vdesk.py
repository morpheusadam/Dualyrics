"""Windows virtual desktops through the public ``IVirtualDesktopManager`` COM interface (ctypes, no pywin32).

What the public interface can do (Windows 10 and 11):

- tell whether a window is on the desktop the user is looking at (``IsWindowOnCurrentVirtualDesktop``);
- tell which desktop a window belongs to (``GetWindowDesktopId``);
- move one of *our own* windows to another desktop (``MoveWindowToDesktop``).

There is no public way to pin a window to all desktops or to read the current desktop id directly, so
"show on all virtual desktops" works by following: when the overlay is not on the current desktop, it is
moved to the desktop of the foreground window. "Current desktop only" remembers the desktop the overlay was
placed on and hides it while the user works on another one. Both degrade to doing nothing when the
interface is missing (Windows Server, older builds) or a call fails.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
CLSID_VDM = "{aa509086-5ca9-4c25-8f95-589d3c07b48a}"
IID_VDM = "{a5cd92ff-29be-454c-8d04-d82879fb3f1b}"


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_str(cls, s: str) -> "GUID":
        u = uuid.UUID(s)
        g = cls()
        ctypes.memmove(ctypes.byref(g), u.bytes_le, 16)
        return g

    def __str__(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(memoryview(self))))

    def is_null(self) -> bool:
        return not any(bytes(memoryview(self)))


class VirtualDesktops:
    def __init__(self) -> None:
        self._ptr = ctypes.c_void_p()
        self._vtbl = None
        self.available = False
        if not IS_WINDOWS:
            return
        try:
            ole32 = ctypes.WinDLL("ole32")
            ole32.CoInitializeEx(None, 0x2)  # apartment threaded; S_FALSE / RPC_E_CHANGED_MODE are fine
            clsid, iid = GUID.from_str(CLSID_VDM), GUID.from_str(IID_VDM)
            hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 0x1 | 0x4, ctypes.byref(iid),
                                        ctypes.byref(self._ptr))
            if hr != 0 or not self._ptr:
                log.info("virtual desktop manager unavailable (0x%08X)", hr & 0xFFFFFFFF)
                return
            vtbl = ctypes.cast(ctypes.cast(self._ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                               ctypes.POINTER(ctypes.c_void_p))
            HRESULT = ctypes.c_long
            self._is_on = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.HWND,
                                             ctypes.POINTER(wintypes.BOOL))(vtbl[3])
            self._get_id = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.HWND,
                                              ctypes.POINTER(GUID))(vtbl[4])
            self._move = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, wintypes.HWND,
                                            ctypes.POINTER(GUID))(vtbl[5])
            self.available = True
        except Exception:
            log.debug("virtual desktop manager failed", exc_info=True)

    def on_current(self, hwnd: int) -> bool | None:
        if not self.available or not hwnd:
            return None
        val = wintypes.BOOL()
        if self._is_on(self._ptr, hwnd, ctypes.byref(val)) != 0:
            return None
        return bool(val.value)

    def desktop_of(self, hwnd: int) -> str:
        """Desktop id of a window ('' when unknown, e.g. shell windows that are on every desktop)."""
        if not self.available or not hwnd:
            return ""
        g = GUID()
        if self._get_id(self._ptr, hwnd, ctypes.byref(g)) != 0 or g.is_null():
            return ""
        return str(g)

    def move(self, hwnd: int, desktop: str) -> bool:
        if not self.available or not hwnd or not desktop:
            return False
        try:
            return self._move(self._ptr, hwnd, ctypes.byref(GUID.from_str(desktop))) == 0
        except Exception:
            return False


_vd: VirtualDesktops | None = None


def manager() -> VirtualDesktops:
    global _vd
    if _vd is None:
        _vd = VirtualDesktops()
    return _vd


def foreground_hwnd() -> int:
    if not IS_WINDOWS:
        return 0
    return ctypes.windll.user32.GetForegroundWindow() or 0
