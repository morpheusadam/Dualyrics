"""Real blur behind the overlay's box, for templates whose frost mode is ``acrylic`` (Liquid Glass, Glass).

The overlay is a per-pixel-alpha layered window (``UpdateLayeredWindow``); Windows cannot blur behind such a
window without also blurring its soft shadows and glow. So the blur lives in a second window: a click-through
tool window placed exactly under the box, just below the overlay in z-order, with the Windows acrylic accent
(``SetWindowCompositionAttribute``, ``ACCENT_ENABLE_ACRYLICBLURBEHIND``, Windows 10 1803 and later) or, where
that is refused, the plain blur-behind accent. A window region clips it to the box's rounded corners where
Windows honours it; Windows 11 draws the accent over the whole window and ignores the region, so there the
window gets DWM's own rounded corners (``DWMWA_WINDOW_CORNER_PREFERENCE``, 8 or 4 px at 96 DPI) and is inset
just enough that those corners lie inside the template's larger ones (``blur_inset``). The
overlay then draws the template's translucent tint, light rim and sheen on top, so the box is real frosted
glass. When the accent cannot be set (older Windows, remote desktop, a driver that refuses it) the window is
never shown and the template's simulated frost remains (docs/DESIGN.md 2.4).
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import tkinter as tk
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
LWA_ALPHA = 0x2
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200
TINT_ALPHA = 0x30          # the acrylic accent's own tint; the overlay's box tint is drawn on top of it
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND, DWMWCP_ROUND, DWMWCP_ROUNDSMALL = 1, 2, 3
DWM_ROUND, DWM_ROUND_SMALL = 8.0, 4.0     # corner radii (px at 96 DPI) of DWMWCP_ROUND / DWMWCP_ROUNDSMALL


def wanted(template: dict[str, Any]) -> bool:
    """The template asks for real blur and has a visible box to put it behind."""
    c = template.get("container") or {}
    blur = c.get("blur") or {}
    bg = c.get("background") or {}
    return blur.get("mode") == "acrylic" and float(bg.get("opacity", 0)) > 0


def tint_abgr(hex_color: str, alpha: int = TINT_ALPHA) -> int:
    """``#RRGGBB`` as the ABGR integer the accent policy expects."""
    try:
        r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    except (ValueError, TypeError, IndexError):
        r = g = b = 0
    return (max(0, min(255, alpha)) << 24) | (b << 16) | (g << 8) | r


def corner_preference(radius: float, dpi_scale: float) -> tuple[int, float]:
    """(DWM corner preference, its radius in px) for a box whose corners have ``radius`` px."""
    if radius <= 1:
        return DWMWCP_DONOTROUND, 0.0
    if radius < DWM_ROUND * dpi_scale:
        return DWMWCP_ROUNDSMALL, DWM_ROUND_SMALL * dpi_scale
    return DWMWCP_ROUND, DWM_ROUND * dpi_scale


def blur_inset(radius: float, dwm_radius: float) -> int:
    """Pixels to move every edge of the blur window inwards so that its DWM corner (``dwm_radius``) lies inside
    the box's rounder corner (``radius``): the corner circles touch when the inset is (R - r)(1 - 1/sqrt 2)."""
    if radius <= dwm_radius:
        return 0
    return int(math.ceil((radius - dwm_radius) * (1 - 1 / math.sqrt(2))))


def box_rect(pos: tuple[int, int], box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Screen rectangle (x, y, w, h) of a frame's box when the frame image is shown at ``pos``."""
    x0, y0, x1, y1 = box
    return pos[0] + x0, pos[1] + y0, max(0, x1 - x0), max(0, y1 - y0)


if IS_WINDOWS:
    class ACCENTPOLICY(ctypes.Structure):
        _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                    ("GradientColor", wintypes.DWORD), ("AnimationId", ctypes.c_int)]

    class WINCOMPATTRDATA(ctypes.Structure):
        _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        _SWCA = _user32.SetWindowCompositionAttribute
        _SWCA.argtypes = [wintypes.HWND, ctypes.POINTER(WINCOMPATTRDATA)]
        _SWCA.restype = wintypes.BOOL
    except AttributeError:      # very old Windows
        _SWCA = None
    _user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
    _user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
    _gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    _gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
    _gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    try:
        _dwm = ctypes.WinDLL("dwmapi")
        _dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        _dwm.DwmSetWindowAttribute.restype = ctypes.c_long
    except OSError:
        _dwm = None
    try:
        _GetDpiForWindow = _user32.GetDpiForWindow
        _GetDpiForWindow.argtypes = [wintypes.HWND]
        _GetDpiForWindow.restype = wintypes.UINT
    except AttributeError:      # before Windows 10 1607
        _GetDpiForWindow = None


def set_corner_preference(hwnd: int, pref: int) -> bool:
    """Windows 11 DWM corner rounding; False where the attribute is unknown (Windows 10)."""
    if not IS_WINDOWS or not hwnd or _dwm is None:
        return False
    val = ctypes.c_int(pref)
    try:
        return _dwm.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(val),
                                          ctypes.sizeof(val)) == 0
    except OSError:
        return False


def window_dpi_scale(hwnd: int) -> float:
    if not IS_WINDOWS or not hwnd or _GetDpiForWindow is None:
        return 1.0
    try:
        dpi = int(_GetDpiForWindow(hwnd))
    except OSError:
        return 1.0
    return dpi / 96.0 if dpi > 0 else 1.0


def set_accent(hwnd: int, state: int, tint: int = 0) -> bool:
    if not IS_WINDOWS or _SWCA is None or not hwnd:
        return False
    policy = ACCENTPOLICY(state, 2 if state else 0, tint, 0)
    data = WINCOMPATTRDATA(WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
                           ctypes.sizeof(policy))
    return bool(_SWCA(hwnd, ctypes.byref(data)))


class Backdrop:
    """The blur window under the overlay's box. All calls on the Tk thread."""

    def __init__(self, root: tk.Misc, overlay_hwnd: int) -> None:
        self.root = root
        self.overlay_hwnd = overlay_hwnd
        self.win: tk.Toplevel | None = None
        self.hwnd = 0
        self.failed = not IS_WINDOWS    # the accent was refused once: never try again this session
        self.shown = False
        self._geom: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._radius = -1
        self._shape: tuple[int, int] = (-1, 0)   # (DWM corner preference sent, 1 when Windows took it)
        self._tint = -1
        self._accent = 0
        self._excluded = False

    def _create(self) -> bool:
        if self.win is not None:
            return True
        if self.failed:
            return False
        from dualyrics import winutil

        try:
            win = tk.Toplevel(self.root)
            win.withdraw()
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.title("Dualyrics backdrop")
            tk.Frame(win, bg="#000000", highlightthickness=0, bd=0).pack(fill="both", expand=True)
            win.update_idletasks()
            hwnd = winutil.toplevel_hwnd(win)
            # click-through (layered + transparent), no taskbar button, never takes focus
            winutil.set_exstyle(hwnd, add=winutil.WS_EX_LAYERED | winutil.WS_EX_TRANSPARENT |
                                winutil.WS_EX_TOOLWINDOW | winutil.WS_EX_NOACTIVATE)
            _user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
        except Exception:
            log.info("blur backdrop unavailable", exc_info=True)
            self.failed = True
            return False
        self.win, self.hwnd = win, hwnd
        if self._excluded:
            self.set_capture_excluded(True)
        return True

    def _apply_accent(self, tint: int) -> bool:
        if tint == self._tint and self._accent:
            return True
        for state in (ACCENT_ENABLE_ACRYLICBLURBEHIND, ACCENT_ENABLE_BLURBEHIND):
            if set_accent(self.hwnd, state, tint):
                if state != self._accent:
                    log.info("blur backdrop: %s", "acrylic" if state == ACCENT_ENABLE_ACRYLICBLURBEHIND else "blur")
                self._accent, self._tint = state, tint
                return True
        log.info("blur backdrop: Windows refused the blur accent (%s); using the simulated frost",
                 ctypes.get_last_error() if IS_WINDOWS else "-")
        self.failed = True
        self.destroy()
        return False

    def show(self, rect: tuple[int, int, int, int], radius: int, color: str) -> bool:
        """Put the blur under ``rect`` (screen x, y, w, h) with rounded corners; False = not available."""
        x, y, w, h = rect
        if w < 4 or h < 4 or not self._create() or not self._apply_accent(tint_abgr(color)):
            self.hide()
            return False
        assert self.win is not None
        try:
            # Windows 11 ignores the region under the accent: DWM corners, inset to fit inside the box's corners
            pref, dwm_r = corner_preference(radius, window_dpi_scale(self.hwnd))
            if pref != self._shape[0]:
                self._shape = (pref, int(set_corner_preference(self.hwnd, pref)))
            inset = blur_inset(radius, dwm_r) if self._shape[1] else 0   # Windows 10: the region does it
            inset = min(inset, max(0, (min(w, h) - 4) // 2))
            if inset:
                x, y, w, h = x + inset, y + inset, w - 2 * inset, h - 2 * inset
                radius = max(0, radius - inset)
            if (w, h, radius) != (self._geom[2], self._geom[3], self._radius):
                d = max(0, radius) * 2
                rgn = _gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, d, d)
                if rgn and not _user32.SetWindowRgn(self.hwnd, rgn, True):
                    _gdi32.DeleteObject(rgn)     # the window owns the region only when the call succeeds
                self._radius = radius
            if not self.shown:
                self.win.deiconify()
                self.shown = True
            if (x, y, w, h) != self._geom:
                self.win.geometry(f"{w}x{h}+{x}+{y}")
                self._geom = (x, y, w, h)
            # just below the overlay (both are topmost), without activating anything
            _user32_set_pos(self.hwnd, self.overlay_hwnd, x, y, w, h)
        except Exception:
            log.debug("blur backdrop update failed", exc_info=True)
            return False
        return True

    def hide(self) -> None:
        if self.win is not None and self.shown:
            try:
                self.win.withdraw()
            except tk.TclError:
                pass
        self.shown = False

    def set_capture_excluded(self, excluded: bool) -> None:
        """Screen reading is on: the blur must not end up in the screen grabs (it would blur the subtitles)."""
        self._excluded = excluded
        if self.hwnd:
            from dualyrics.screen import capture

            capture.set_capture_excluded(self.hwnd, excluded)

    def destroy(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except tk.TclError:
                pass
        self.win, self.hwnd, self.shown = None, 0, False
        self._geom, self._radius, self._tint, self._accent = (0, 0, 0, 0), -1, -1, 0
        self._shape = (-1, 0)


def _user32_set_pos(hwnd: int, after: int, x: int, y: int, w: int, h: int) -> None:
    from dualyrics import winutil

    winutil.user32.SetWindowPos(hwnd, after, x, y, w, h, SWP_NOACTIVATE | SWP_NOOWNERZORDER)
