"""Small Win32 helpers via ctypes: DPI awareness, monitors, window styles, single-instance mutex."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    LONG_PTR = ctypes.c_ssize_t
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = LONG_PTR
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, LONG_PTR]
    user32.SetWindowLongPtrW.restype = LONG_PTR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenEventW.restype = wintypes.HANDLE
    kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.OpenMutexW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|NOACTIVATE|FRAMECHANGED


def set_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware so text is drawn crisp (call before creating Tk)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass(frozen=True)
class Monitor:
    name: str
    left: int
    top: int
    right: int
    bottom: int          # work area (excludes the taskbar)
    primary: bool
    screen: tuple[int, int, int, int] = (0, 0, 0, 0)   # full monitor rectangle (left, top, right, bottom)
    dpi: int = 96

    @property
    def screen_height(self) -> int:
        h = self.screen[3] - self.screen[1]
        return h if h > 0 else self.height

    @property
    def scale(self) -> float:
        return self.dpi / 96.0

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]


def _info(hmon: int) -> Monitor | None:
    mi = _MONITORINFOEXW()
    mi.cbSize = ctypes.sizeof(mi)
    if not user32.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(mi)):
        return None
    w, r = mi.rcWork, mi.rcMonitor
    dpi = 96
    try:
        dx, dy = wintypes.UINT(), wintypes.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(wintypes.HMONITOR(hmon), 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
            dpi = int(dx.value) or 96
    except Exception:
        pass
    return Monitor(mi.szDevice, w.left, w.top, w.right, w.bottom, bool(mi.dwFlags & 1),
                   (r.left, r.top, r.right, r.bottom), dpi)


def monitors() -> list[Monitor]:
    """All connected monitors (work areas, in physical pixels)."""
    if not IS_WINDOWS:
        return [Monitor("default", 0, 0, 1920, 1040, True)]
    found: list[Monitor] = []
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):  # noqa: ANN001
        m = _info(hmon)
        if m:
            found.append(m)
        return True

    user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    return found or [Monitor("default", 0, 0, 1920, 1040, True)]


def monitor_at(x: int, y: int) -> Monitor:
    """Monitor containing (x, y), or the nearest one."""
    if IS_WINDOWS:
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)  # MONITOR_DEFAULTTONEAREST
        m = _info(hmon) if hmon else None
        if m:
            return m
    mons = monitors()
    return next((m for m in mons if m.contains(x, y)), next((m for m in mons if m.primary), mons[0]))


def monitor_of_window(hwnd: int) -> Monitor | None:
    """Monitor that shows most of a window (None when unknown)."""
    if not IS_WINDOWS or not hwnd:
        return None
    try:
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        hmon = user32.MonitorFromWindow(hwnd, 2)
        return _info(hmon) if hmon else None
    except Exception:
        return None


def primary_monitor() -> Monitor:
    mons = monitors()
    return next((m for m in mons if m.primary), mons[0])


def toplevel_hwnd(widget) -> int:  # noqa: ANN001 - tkinter widget
    """Real top-level HWND of a Tk window (winfo_id is an inner child window)."""
    if not IS_WINDOWS:
        return 0
    return user32.GetAncestor(widget.winfo_id(), 2) or widget.winfo_id()  # GA_ROOT


def set_exstyle(hwnd: int, add: int = 0, remove: int = 0) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    new = (style | add) & ~remove
    if new != style:
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Let mouse clicks pass through the window (needs a layered window, which Tk alpha provides)."""
    if enabled:
        set_exstyle(hwnd, add=WS_EX_TRANSPARENT | WS_EX_LAYERED)
    else:
        set_exstyle(hwnd, remove=WS_EX_TRANSPARENT)


def set_round_corners(hwnd: int, enabled: bool) -> bool:
    """Windows 11 rounded corners via DWM. Returns False when not supported (Windows 10)."""
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        pref = ctypes.c_int(2 if enabled else 1)  # DWMWCP_ROUND / DWMWCP_DONOTROUND
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 33, ctypes.byref(pref),
                                                        ctypes.sizeof(pref))
        return hr == 0
    except Exception:
        return False


def set_dark_titlebar(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        val = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(wintypes.HWND(hwnd), 20, ctypes.byref(val), ctypes.sizeof(val))
    except Exception:
        pass


def set_app_user_model_id(app_id: str) -> None:
    """Give the process its own taskbar identity (so our icon is used instead of Python's)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id))
    except Exception:
        pass


# ---------------------------------------------------------------- single instance
class SingleInstance:
    """Named mutex guard. A second launch signals the first one (which opens its settings) and exits."""

    MUTEX = "Local\\Dualyrics.SingleInstance.Mutex"
    EVENT = "Local\\Dualyrics.SingleInstance.Show"
    QUIT_EVENT = "Local\\Dualyrics.SingleInstance.Quit"   # set by `Dualyrics.exe --quit` (the installer)

    def __init__(self) -> None:
        self.mutex = None
        self.event = None
        self.quit_event = None
        self.already_running = False
        if not IS_WINDOWS:
            return
        self.mutex = kernel32.CreateMutexW(None, True, self.MUTEX)
        self.already_running = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
        if not self.already_running:
            self.event = kernel32.CreateEventW(None, False, False, self.EVENT)
            self.quit_event = kernel32.CreateEventW(None, True, False, self.QUIT_EVENT)

    def signal_quit(self) -> bool:
        """Ask the running copy to quit (saves settings). False when no copy that understands it is running."""
        if not IS_WINDOWS:
            return False
        h = kernel32.OpenEventW(0x0002, False, self.QUIT_EVENT)
        if not h:
            return False
        kernel32.SetEvent(h)
        kernel32.CloseHandle(h)
        return True

    def quit_requested(self) -> bool:
        return bool(self.quit_event) and kernel32.WaitForSingleObject(self.quit_event, 0) == 0

    @classmethod
    def wait_until_gone(cls, timeout_s: float) -> bool:
        """Wait for the running copy to exit (its mutex disappears). Call after release()."""
        if not IS_WINDOWS:
            return True
        end = time.monotonic() + timeout_s
        while True:
            h = kernel32.OpenMutexW(0x00100000, False, cls.MUTEX)  # SYNCHRONIZE
            if not h:
                return True
            kernel32.CloseHandle(h)
            if time.monotonic() >= end:
                return False
            time.sleep(0.2)

    def signal_running_instance(self) -> None:
        if not IS_WINDOWS:
            return
        h = kernel32.OpenEventW(0x0002, False, self.EVENT)  # EVENT_MODIFY_STATE
        if h:
            kernel32.SetEvent(h)
            kernel32.CloseHandle(h)

    def wait_for_signal(self, timeout_ms: int) -> bool:
        """True when another launch asked us to show ourselves."""
        if not self.event:
            return False
        return kernel32.WaitForSingleObject(self.event, timeout_ms) == 0

    def release(self) -> None:
        if not IS_WINDOWS:
            return
        for h in (self.event, self.quit_event):
            if h:
                kernel32.CloseHandle(h)
        if self.mutex:
            if not self.already_running:
                kernel32.ReleaseMutex(self.mutex)
            kernel32.CloseHandle(self.mutex)
        self.mutex = self.event = self.quit_event = None
