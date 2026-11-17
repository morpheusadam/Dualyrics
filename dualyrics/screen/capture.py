"""Window geometry and screen grabs for the subtitle region (GDI, no extra packages).

The grab copies only the region from the screen without ``CAPTUREBLT``, so layered windows (the Dualyrics
overlay) are not part of it; while the screen source runs the overlay is also excluded from capture with
``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`` (Windows 10 2004 and later), so OCR never reads our own
lines back.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11
SRCCOPY = 0x00CC0020
GA_ROOT = 2

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.restype = ctypes.c_void_p
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
    gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
    gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = ctypes.c_void_p
    gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


class _BIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def window_ok(hwnd: int) -> bool:
    return IS_WINDOWS and bool(hwnd) and bool(user32.IsWindow(hwnd)) and not user32.IsIconic(hwnd)


def client_box(hwnd: int) -> tuple[int, int, int, int] | None:
    """Client area of a window in screen pixels (left, top, right, bottom), or None."""
    if not window_ok(hwnd):
        return None
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    if r.right - r.left < 16 or r.bottom - r.top < 16:
        return None
    return pt.x, pt.y, pt.x + (r.right - r.left), pt.y + (r.bottom - r.top)


def is_front(hwnd: int) -> bool:
    """``hwnd`` (or its top-level window) is the window in front. Screen pixels are grabbed only then: a grab
    copies whatever the screen shows in that box, so a window moved over the target (a chat, a password manager)
    must never be read and sent for translation."""
    if not IS_WINDOWS or not hwnd:
        return False
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    return user32.GetAncestor(fg, GA_ROOT) == user32.GetAncestor(hwnd, GA_ROOT)


def front_client_box(hwnd: int) -> tuple[int, int, int, int] | None:
    """:func:`client_box` of ``hwnd`` while it is the window in front, else None (the screen source's reader)."""
    return client_box(hwnd) if is_front(hwnd) else None


def grab(box: tuple[int, int, int, int]) -> Any:
    """A PIL RGB image of a screen box (layered windows, such as our overlay, are left out)."""
    from PIL import Image

    left, top, right, bottom = box
    w, h = max(1, right - left), max(1, bottom - top)
    if not IS_WINDOWS:
        return Image.new("RGB", (w, h))
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        gdi32.BitBlt(mem, 0, 0, w, h, screen, left, top, SRCCOPY)
        bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.SelectObject(mem, old)
        if not gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bih), 0):
            raise OSError("GetDIBits failed")
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


def set_capture_excluded(hwnd: int, excluded: bool) -> bool:
    """Leave our overlay out of screen grabs while the screen source reads subtitles."""
    if not IS_WINDOWS or not hwnd:
        return False
    ok = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if excluded else WDA_NONE))
    if not ok and excluded:
        log.debug("SetWindowDisplayAffinity failed: %s", ctypes.get_last_error())
    return ok
