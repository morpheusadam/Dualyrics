"""Processes and windows (Win32 via ctypes): foreground app, fullscreen check, running apps, exe icons and
"pick a window". Used by per-app profiles and the "Add app" helpers of the settings window.

Everything degrades to empty results on other platforms or when Windows refuses access.
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
import os
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable

from dualyrics.profiles import AppRef

log = logging.getLogger(__name__)

IS_WINDOWS = os.name == "nt"
OWN_PID = os.getpid()
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                 "Windows.UI.Core.CoreWindow", "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland"}
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x2

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    version = ctypes.WinDLL("version", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.LPARAM]
    user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.c_void_p]
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    shell32.ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.HICON),
                                       ctypes.POINTER(wintypes.HICON), wintypes.UINT]
    shell32.ExtractIconExW.restype = wintypes.UINT
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


@dataclass(frozen=True)
class AppInfo:
    name: str          # display name (file description, window title or exe name)
    exe: str           # lower-case file name, e.g. "gow.exe"
    path: str          # full path ('' when Windows did not tell)
    title: str = ""    # window title
    pid: int = 0
    hwnd: int = 0      # the window, when it was picked by a click

    @property
    def ref(self) -> AppRef:
        return AppRef(self.exe, self.path)


@dataclass(frozen=True)
class Foreground:
    app: AppRef | None
    pid: int = 0
    title: str = ""
    fullscreen: bool = False
    own: bool = False          # one of our windows (settings, overlay): keep the previous context
    shell: bool = False        # desktop / taskbar
    hwnd: int = 0


# ---------------------------------------------------------------- basics
_path_cache: dict[int, tuple[float, str]] = {}


def process_path(pid: int) -> str:
    if not IS_WINDOWS or not pid:
        return ""
    hit = _path_cache.get(pid)
    if hit and time.monotonic() - hit[0] < 30:
        return hit[1]
    path = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                path = buf.value
        finally:
            kernel32.CloseHandle(h)
    now = time.monotonic()
    if len(_path_cache) > 1024:   # drop expired entries (ended processes) instead of everything
        for k in [k for k, (at, _p) in list(_path_cache.items()) if now - at >= 30]:
            _path_cache.pop(k, None)
    _path_cache[pid] = (now, path)
    return path


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _uwp_child_pid(hwnd: int, host_pid: int) -> int:
    """Store apps run inside ApplicationFrameHost: the real app owns a child window."""
    found = [0]
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(child, _lp):  # noqa: ANN001
        p = _pid(child)
        if p and p != host_pid:
            found[0] = p
            return False
        return True

    user32.EnumChildWindows(hwnd, proc_t(cb), 0)
    return found[0]


def is_fullscreen(hwnd: int) -> bool:
    """The window covers its whole monitor (borderless/windowed fullscreen games, F11 browsers, players)."""
    if not IS_WINDOWS or not hwnd or user32.IsIconic(hwnd):
        return False
    r = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    mi = _MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    hmon = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not hmon or not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def foreground() -> Foreground:
    if not IS_WINDOWS:
        return Foreground(None)
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return Foreground(None)
    pid = _pid(hwnd)
    if pid == OWN_PID:
        return Foreground(None, pid, own=True)
    cls = _class(hwnd)
    path = process_path(pid)
    if ntpath.basename(path).lower() == "applicationframehost.exe":
        child = _uwp_child_pid(hwnd, pid)
        if child:
            pid, path = child, process_path(child)
    shell = cls in SHELL_CLASSES
    app = AppRef.of(path) if path else None
    return Foreground(app, pid, _title(hwnd), fullscreen=not shell and is_fullscreen(hwnd), shell=shell,
                      hwnd=int(hwnd))


def running() -> list[AppRef]:
    """Every process (exe name; full path when Windows allows reading it)."""
    if not IS_WINDOWS:
        return []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    out: dict[str, AppRef] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            exe = e.szExeFile.lower()
            # never our own process: a profile for python.exe / Dualyrics.exe would otherwise always match
            if exe and exe not in out and e.th32ProcessID != OWN_PID:
                out[exe] = AppRef(exe, process_path(e.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(e))
    finally:
        kernel32.CloseHandle(snap)
    return list(out.values())


# ---------------------------------------------------------------- app details for the UI
def file_description(path: str) -> str:
    """The FileDescription in an exe's version resource (e.g. "Spotify"), or ''."""
    if not IS_WINDOWS or not path:
        return ""
    try:
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, buf):
            return ""
        ptr, n = ctypes.c_void_p(), wintypes.UINT()
        if not version.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(n)) or not n.value:
            langs = [(0x0409, 0x04B0)]
        else:
            arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
            langs = [(arr[0], arr[1]), (0x0409, 0x04B0), (0x0409, 0x04E4)]
        for lang, cp in langs:
            key = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\FileDescription"
            if version.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(n)) and n.value:
                text = ctypes.wstring_at(ptr, n.value).rstrip("\x00").strip()
                if text:
                    return text
    except Exception:
        log.debug("version info failed for %s", path, exc_info=True)
    return ""


def app_from_path(path: str, title: str = "", pid: int = 0, hwnd: int = 0) -> AppInfo:
    exe = ntpath.basename(path).lower()
    name = file_description(path) or title or ntpath.splitext(ntpath.basename(path))[0]
    return AppInfo(name[:80], exe, path, title, pid, hwnd)


def running_apps() -> list[AppInfo]:
    """Apps with a visible top-level window (what "Add from running apps" lists), one per exe, by name."""
    if not IS_WINDOWS:
        return []
    found: dict[str, AppInfo] = {}
    proc_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):  # noqa: ANN001
        try:
            if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: skip dialogs
                return True
            if user32.GetWindowLongPtrW(hwnd, -20) & 0x80:   # WS_EX_TOOLWINDOW
                return True
            title = _title(hwnd)
            if not title or _class(hwnd) in SHELL_CLASSES:
                return True
            pid = _pid(hwnd)
            if pid == OWN_PID:
                return True
            path = process_path(pid)
            if ntpath.basename(path).lower() == "applicationframehost.exe":
                child = _uwp_child_pid(hwnd, pid)
                if not child:
                    return True
                pid, path = child, process_path(child)
            if path and path.lower() not in found:
                found[path.lower()] = app_from_path(path, title, pid)
        except Exception:
            log.debug("enum window failed", exc_info=True)
        return True

    user32.EnumWindows(proc_t(cb), 0)
    return sorted(found.values(), key=lambda a: a.name.lower())


class _ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD), ("yHotspot", wintypes.DWORD),
                ("hbmMask", wintypes.HBITMAP), ("hbmColor", wintypes.HBITMAP)]


class _BITMAP(ctypes.Structure):
    _fields_ = [("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long), ("bmHeight", ctypes.c_long),
                ("bmWidthBytes", ctypes.c_long), ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
                ("bmBits", ctypes.c_void_p)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _bitmap_bytes(hdc: int, hbm: int) -> tuple[int, int, bytes] | None:
    bm = _BITMAP()
    if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
        return None
    w, h = bm.bmWidth, abs(bm.bmHeight)
    bih = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    if not gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bih), 0):
        return None
    return w, h, buf.raw


def exe_icon(path: str, size: int = 32) -> Any:
    """The exe's icon as a PIL RGBA image (``size`` x ``size``), or None."""
    if not IS_WINDOWS or not path or path.startswith(("\\\\", "//")):
        return None   # no network paths (an imported profile could point at a share and leak the sign-in)
    from PIL import Image

    large, small = wintypes.HICON(), wintypes.HICON()
    if not shell32.ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1):
        return None
    icon = large.value or small.value
    other = small.value if icon == large.value else large.value
    info = _ICONINFO()
    hdc = user32.GetDC(None)
    try:
        if not icon or not user32.GetIconInfo(icon, ctypes.byref(info)):
            return None
        color = _bitmap_bytes(hdc, info.hbmColor) if info.hbmColor else None
        if color is None:
            return None
        w, h, data = color
        img = Image.frombuffer("RGBA", (w, h), data, "raw", "BGRA", 0, 1).copy()
        if img.getextrema()[3][1] == 0:  # old icon without alpha: use the mask
            mask = _bitmap_bytes(hdc, info.hbmMask) if info.hbmMask else None
            if mask is not None:
                m = Image.frombuffer("RGBA", (mask[0], mask[1]), mask[2], "raw", "BGRA", 0, 1).convert("L")
                img.putalpha(m.point(lambda v: 0 if v else 255).resize((w, h)))
            else:
                img.putalpha(255)
        return img.resize((size, size), Image.LANCZOS) if (w, h) != (size, size) else img
    except Exception:
        log.debug("icon extraction failed for %s", path, exc_info=True)
        return None
    finally:
        user32.ReleaseDC(None, hdc)
        for hbm in (info.hbmColor, info.hbmMask):
            if hbm:
                gdi32.DeleteObject(hbm)
        for h_ in (icon, other):
            if h_:
                user32.DestroyIcon(h_)


def pick_window(timeout: float = 20.0, cancelled: Callable[[], bool] = lambda: False) -> AppInfo | None:
    """Wait for the next left click on any window and return its app (blocking: run it on a thread).

    Esc or a right click cancels; a click on one of our own windows cancels too.
    """
    if not IS_WINDOWS:
        return None

    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    end = time.monotonic() + timeout
    while down(0x01) and time.monotonic() < end:   # the click that started picking
        time.sleep(0.02)
    while time.monotonic() < end and not cancelled():
        if down(0x1B) or down(0x02):               # Esc, right button
            return None
        if down(0x01):
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            hwnd = user32.GetAncestor(user32.WindowFromPoint(pt), 2)  # GA_ROOT
            pid = _pid(hwnd) if hwnd else 0
            if not pid or pid == OWN_PID:
                return None
            path = process_path(pid)
            if ntpath.basename(path).lower() == "applicationframehost.exe":
                child = _uwp_child_pid(hwnd, pid)
                if child:
                    pid, path = child, process_path(child)
            return app_from_path(path, _title(hwnd), pid, int(hwnd)) if path else None
        time.sleep(0.02)
    return None
