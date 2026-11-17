"""Fonts: the bundled Vazirmatn (SIL OFL 1.1, loaded for this process only), installed families and weights.

``load_bundled()`` registers ``assets/fonts/*.ttf`` with ``AddFontResourceExW(..., FR_PRIVATE)`` so GDI, Tk and
the overlay renderer can use Vazirmatn without the user installing anything. Static weights show up as
separate GDI families ("Vazirmatn", "Vazirmatn Medium", "Vazirmatn SemiBold"), so :func:`pick` maps a CSS
weight to the family name that has it.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Iterable

from dualyrics import paths

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
FR_PRIVATE = 0x10
FONTS_DIR = paths.ASSETS_DIR / "fonts"
_loaded = False
_lock = threading.Lock()
_families: set[str] | None = None

# family-name suffixes that name a weight ("Segoe UI Semibold", "Vazirmatn Medium", "...Display Semib")
WEIGHT_WORDS = (
    ("extralight", 200), ("ultralight", 200), ("extra light", 200), ("semilight", 350), ("semil", 350),
    ("thin", 100), ("light", 300), ("medium", 500), ("semibold", 600), ("demibold", 600), ("semib", 600),
    ("extrabold", 800), ("ultrabold", 800), ("black", 900), ("heavy", 900), ("bold", 700),
)

LATIN_UI = ("Segoe UI Variable Text", "Segoe UI", "Arial")
LATIN_DISPLAY = ("Segoe UI Variable Display", "Segoe UI")
PERSIAN_UI = ("Vazirmatn", "Segoe UI", "Tahoma")
MONO = ("Cascadia Mono", "Consolas", "Courier New")


def load_bundled() -> int:
    """Register the bundled fonts for this process (idempotent). Returns how many files were added."""
    global _loaded, _families
    with _lock:
        if _loaded or not IS_WINDOWS:
            return 0
        _loaded = True
        n = 0
        try:
            gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
            gdi32.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
            gdi32.AddFontResourceExW.restype = ctypes.c_int
            for f in sorted(FONTS_DIR.glob("*.ttf")):
                if gdi32.AddFontResourceExW(str(f), FR_PRIVATE, None):
                    n += 1
        except Exception:
            log.debug("could not load bundled fonts", exc_info=True)
        _families = None
        return n


def families() -> set[str]:
    """Every font family GDI can use in this process (bundled private fonts included)."""
    global _families
    with _lock:
        if _families is not None:
            return _families
    found: set[str] = set()
    if IS_WINDOWS:
        try:
            found = _enum_gdi()
        except Exception:
            log.debug("font enumeration failed", exc_info=True)
    with _lock:
        _families = found
    return found


class _LOGFONTW(ctypes.Structure):
    _fields_ = [("lfHeight", ctypes.c_long), ("lfWidth", ctypes.c_long), ("lfEscapement", ctypes.c_long),
                ("lfOrientation", ctypes.c_long), ("lfWeight", ctypes.c_long), ("lfItalic", ctypes.c_byte),
                ("lfUnderline", ctypes.c_byte), ("lfStrikeOut", ctypes.c_byte), ("lfCharSet", ctypes.c_byte),
                ("lfOutPrecision", ctypes.c_byte), ("lfClipPrecision", ctypes.c_byte),
                ("lfQuality", ctypes.c_byte), ("lfPitchAndFamily", ctypes.c_byte),
                ("lfFaceName", ctypes.c_wchar * 32)]


def _enum_gdi() -> set[str]:
    gdi32 = ctypes.WinDLL("gdi32")
    user32 = ctypes.WinDLL("user32")
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(_LOGFONTW), ctypes.c_void_p, wintypes.DWORD,
                                wintypes.LPARAM)
    gdi32.EnumFontFamiliesExW.argtypes = [wintypes.HDC, ctypes.POINTER(_LOGFONTW), proc_t, wintypes.LPARAM,
                                          wintypes.DWORD]
    found: set[str] = set()

    def cb(lf, _tm, _kind, _lp):  # noqa: ANN001
        name = lf.contents.lfFaceName
        if name and not name.startswith("@"):
            found.add(name)
        return 1

    hdc = user32.GetDC(None)
    try:
        lf = _LOGFONTW()
        lf.lfCharSet = 1  # DEFAULT_CHARSET: every family once
        gdi32.EnumFontFamiliesExW(hdc, ctypes.byref(lf), proc_t(cb), 0, 0)
    finally:
        user32.ReleaseDC(None, hdc)
    return found


def first_installed(stack: Iterable[str], fallback: Iterable[str] = ()) -> str:
    fams = families()
    for name in list(stack) + list(fallback):
        if name in fams:
            return name
    return next(iter(stack), "Segoe UI")


def _suffix_weight(suffix: str) -> int | None:
    s = suffix.lower().strip()
    for word, w in WEIGHT_WORDS:
        if s == word:
            return w
    if len(s) >= 5:  # GDI cuts face names at 31 characters: "Segoe UI Variable Text Semiligh"
        for word, w in WEIGHT_WORDS:
            if word.startswith(s):
                return w
    return None


def pick(family: str, weight: int) -> tuple[str, int]:
    """(GDI face name, GDI weight) for ``family`` at CSS ``weight``: a named weight family when the
    font has one ("Segoe UI Semibold"), otherwise the family itself with the weight (GDI picks or fakes)."""
    fams = families()
    options: dict[int, str] = {}
    prefix = family + " "
    for name in fams:
        if name.startswith(prefix):
            w = _suffix_weight(name[len(prefix):])
            if w is not None:
                options[w] = name
    if not options:
        return family, weight
    if weight in (400, 700) or weight not in options:
        # nearest named weight that is closer than the regular/bold faces of the family itself
        best = min(options, key=lambda w: abs(w - weight))
        if abs(best - weight) < min(abs(400 - weight), abs(700 - weight)):
            return options[best], 400
        return family, 700 if weight >= 600 else 400
    return options[weight], 400


def ui_family(rtl: bool, role: str = "text") -> str:
    """Settings-window family per DESIGN.md 1.2 (role: text, display or mono)."""
    if role == "mono":
        return first_installed(MONO)
    if rtl:
        return first_installed(PERSIAN_UI)
    return first_installed(LATIN_DISPLAY if role == "display" else LATIN_UI)
