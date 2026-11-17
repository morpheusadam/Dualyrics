"""Per-pixel-alpha window content with ``UpdateLayeredWindow`` (soft shadows and glow over any app or game).

The overlay is a normal Tk toplevel (so Tk still delivers mouse events and menus); this module turns its
HWND into a layered window and pushes premultiplied BGRA frames to it. Pixels with alpha 0 let the mouse
through, so the renderer gives the box area a minimum alpha of 1 when the overlay should catch clicks.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PIL import Image

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
ULW_ALPHA = 0x2
AC_SRC_OVER, AC_SRC_ALPHA = 0x0, 0x1

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    class BLENDFUNCTION(ctypes.Structure):
        _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                    ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

    class _BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
                                           ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                           ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
                                           ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]


class LayeredSurface:
    """Keeps one DIB of the current size and blits frames to a layered HWND."""

    def __init__(self) -> None:
        self.hwnd = 0
        self._dc = gdi32.CreateCompatibleDC(None) if IS_WINDOWS else None
        self._bmp = None
        self._old = None
        self._bits = ctypes.c_void_p()
        self._size = (0, 0)
        self.ok = IS_WINDOWS

    def attach(self, hwnd: int) -> None:
        from dualyrics import winutil

        self.hwnd = hwnd
        # layered + tool window (no taskbar button, shown on every virtual desktop) + never steals focus
        winutil.set_exstyle(hwnd, add=winutil.WS_EX_LAYERED | winutil.WS_EX_TOOLWINDOW | winutil.WS_EX_NOACTIVATE)

    def _ensure(self, w: int, h: int) -> None:
        if (w, h) == self._size and self._bmp:
            return
        if self._bmp:
            gdi32.SelectObject(self._dc, self._old)
            gdi32.DeleteObject(self._bmp)
        bih = _BIH(ctypes.sizeof(_BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        self._bits = ctypes.c_void_p()
        self._bmp = gdi32.CreateDIBSection(self._dc, ctypes.byref(bih), 0, ctypes.byref(self._bits), None, 0)
        self._old = gdi32.SelectObject(self._dc, self._bmp)
        self._size = (w, h)

    def blit(self, img: Image.Image, x: int, y: int, alpha: float = 1.0) -> bool:
        """Show ``img`` (RGBA, straight alpha) at screen position (x, y) with a global ``alpha``."""
        if not IS_WINDOWS or not self.hwnd:
            return False
        w, h = img.size
        if w <= 0 or h <= 0:
            return False
        self._ensure(w, h)
        if not self._bmp:
            return False
        data = img.tobytes("raw", "BGRa")   # premultiplied BGRA, what UpdateLayeredWindow wants
        ctypes.memmove(self._bits, data, len(data))
        screen = user32.GetDC(None)
        try:
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, max(0, min(255, int(round(alpha * 255)))), AC_SRC_ALPHA)
            ok = user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(int(x), int(y))),
                                            ctypes.byref(wintypes.SIZE(w, h)), self._dc,
                                            ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
        finally:
            user32.ReleaseDC(None, screen)
        if not ok:
            log.debug("UpdateLayeredWindow failed: %s", ctypes.get_last_error())
        return bool(ok)

    def close(self) -> None:
        if IS_WINDOWS and self._dc:
            if self._bmp:
                gdi32.SelectObject(self._dc, self._old)
                gdi32.DeleteObject(self._bmp)
            gdi32.DeleteDC(self._dc)
        self._dc = self._bmp = None
