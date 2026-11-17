"""Template renderer: draws a subtitle frame (box + lines + effects) into a Pillow RGBA image.

Text shaping comes from Windows: each line is drawn with GDI ``DrawTextW`` (which shapes through Uniscribe,
so Persian and Arabic letters join and run right to left) into a grayscale coverage mask. Everything else is
done with Pillow: solid or gradient fill through the mask, round outline (mask dilation), soft shadows and
glow (Gaussian blur), the box (fill, gradient, border, drop shadow, simulated frost), karaoke highlight and
compositing. The overlay window shows the result with per-pixel alpha (``UpdateLayeredWindow``), and the
settings preview and preset cards use the very same code path.

Rendered lines are cached by (text, style, scale), so a karaoke sweep only re-composites two cached layers
with a moving clip.
"""

from __future__ import annotations

import ctypes
import logging
import math
import sys
import threading
from collections import OrderedDict
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from dualyrics import fonts, langs, templates

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
BIDI_CONTROLS = str.maketrans("", "", "‎‏‪‫‬‭‮⁦⁧⁨⁩")
ELLIPSIS = "…"
GRIP = 7            # px around the box that still catch the mouse (resize edges)
CACHE_LINES = 160

# ---------------------------------------------------------------- GDI text masks
if IS_WINDOWS:
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
    gdi32.CreateFontW.restype = wintypes.HFONT
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = wintypes.HGDIOBJ
    gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetTextCharacterExtra.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.GdiFlush.argtypes = []
    gdi32.GetTextMetricsW.argtypes = [wintypes.HDC, ctypes.c_void_p]
    user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.RECT),
                                 wintypes.UINT]
    user32.DrawTextW.restype = ctypes.c_int

DT_SINGLELINE, DT_NOPREFIX, DT_NOCLIP, DT_CALCRECT, DT_RTLREADING = 0x20, 0x800, 0x100, 0x400, 0x20000


class _TEXTMETRICW(ctypes.Structure):
    _fields_ = [("tmHeight", ctypes.c_long), ("tmAscent", ctypes.c_long), ("tmDescent", ctypes.c_long),
                ("tmInternalLeading", ctypes.c_long), ("tmExternalLeading", ctypes.c_long),
                ("tmAveCharWidth", ctypes.c_long), ("tmMaxCharWidth", ctypes.c_long),
                ("tmWeight", ctypes.c_long), ("tmOverhang", ctypes.c_long),
                ("tmDigitizedAspectX", ctypes.c_long), ("tmDigitizedAspectY", ctypes.c_long),
                ("tmFirstChar", ctypes.c_wchar), ("tmLastChar", ctypes.c_wchar),
                ("tmDefaultChar", ctypes.c_wchar), ("tmBreakChar", ctypes.c_wchar),
                ("tmItalic", ctypes.c_byte), ("tmUnderlined", ctypes.c_byte), ("tmStruckOut", ctypes.c_byte),
                ("tmPitchAndFamily", ctypes.c_byte), ("tmCharSet", ctypes.c_byte)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


@dataclass(frozen=True)
class FontKey:
    face: str
    px: int
    weight: int
    italic: bool


class TextEngine:
    """GDI fonts, measuring and coverage masks (one memory DC, used under a lock)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._fonts: dict[FontKey, int] = {}
        self._metrics: dict[FontKey, tuple[int, int, int]] = {}
        self._measure: OrderedDict[tuple[Any, ...], int] = OrderedDict()
        self._dc = gdi32.CreateCompatibleDC(None) if IS_WINDOWS else None

    def font(self, key: FontKey) -> int:
        with self._lock:
            f = self._fonts.get(key)
            if f is None:
                f = gdi32.CreateFontW(-max(1, key.px), 0, 0, 0, key.weight, 1 if key.italic else 0, 0, 0, 1, 4,
                                      0, 4, 0, key.face)  # DEFAULT_CHARSET, OUT_TT_PRECIS, ANTIALIASED_QUALITY
                if len(self._fonts) > 96:
                    # GDI does not delete a font that is selected into a DC: select the stock font first, or
                    # the last used font would leak at every flush
                    gdi32.SelectObject(self._dc, gdi32.GetStockObject(13))  # SYSTEM_FONT
                    for old in list(self._fonts.values()):
                        gdi32.DeleteObject(old)
                    self._fonts.clear()
                    self._metrics.clear()
                self._fonts[key] = f
            return f

    def metrics(self, key: FontKey) -> tuple[int, int, int]:
        """(height, ascent, external leading) in px."""
        m = self._metrics.get(key)
        if m is None:
            with self._lock:
                gdi32.SelectObject(self._dc, self.font(key))
                tm = _TEXTMETRICW()
                gdi32.GetTextMetricsW(self._dc, ctypes.byref(tm))
                m = (tm.tmHeight, tm.tmAscent, tm.tmExternalLeading)
                self._metrics[key] = m
        return m

    def width(self, key: FontKey, text: str, rtl: bool, spacing: int = 0) -> int:
        ck = (key, text, rtl, spacing)
        w = self._measure.get(ck)
        if w is not None:
            return w
        with self._lock:
            gdi32.SelectObject(self._dc, self.font(key))
            gdi32.SetTextCharacterExtra(self._dc, spacing)
            r = wintypes.RECT(0, 0, 0, 0)
            flags = DT_SINGLELINE | DT_NOPREFIX | DT_CALCRECT | (DT_RTLREADING if rtl else 0)
            user32.DrawTextW(self._dc, text, len(text), ctypes.byref(r), flags)
            gdi32.SetTextCharacterExtra(self._dc, 0)
            w = max(0, r.right - r.left)
        self._measure[ck] = w
        if len(self._measure) > 4000:
            for _ in range(1000):
                self._measure.popitem(last=False)
        return w

    def mask(self, key: FontKey, text: str, rtl: bool, w: int, h: int, x: int, y: int,
             spacing: int = 0) -> Image.Image:
        """Grayscale coverage of ``text`` drawn at (x, y) in a ``w`` x ``h`` canvas."""
        w, h = max(1, w), max(1, h)
        with self._lock:
            bmi = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            hbm = gdi32.CreateDIBSection(self._dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbm:
                return Image.new("L", (w, h), 0)
            old = gdi32.SelectObject(self._dc, hbm)
            try:
                gdi32.SelectObject(self._dc, self.font(key))
                gdi32.SetBkMode(self._dc, 1)  # TRANSPARENT; the new DIB is all black
                gdi32.SetTextColor(self._dc, 0xFFFFFF)
                gdi32.SetTextCharacterExtra(self._dc, spacing)
                r = wintypes.RECT(x, y, w, h)
                flags = DT_SINGLELINE | DT_NOPREFIX | DT_NOCLIP | (DT_RTLREADING if rtl else 0)
                user32.DrawTextW(self._dc, text, len(text), ctypes.byref(r), flags)
                gdi32.SetTextCharacterExtra(self._dc, 0)
                gdi32.GdiFlush()
                raw = ctypes.string_at(bits, w * h * 4)
            finally:
                gdi32.SelectObject(self._dc, old)
                gdi32.DeleteObject(hbm)
        return Image.frombuffer("RGBA", (w, h), raw, "raw", "RGBA", 0, 1).getchannel("G")


_engine: TextEngine | None = None
_engine_lock = threading.Lock()


def engine() -> TextEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            fonts.load_bundled()
            _engine = TextEngine()
        return _engine


# ---------------------------------------------------------------- small image helpers
def rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _scaled_alpha(mask: Image.Image, k: float) -> Image.Image:
    if k >= 0.999:
        return mask
    k = max(0.0, k)
    return mask.point([int(v * k + 0.5) for v in range(256)])


def colorize(mask: Image.Image, color: str, opacity: float) -> Image.Image:
    layer = Image.new("RGBA", mask.size, rgb(color) + (0,))
    layer.putalpha(_scaled_alpha(mask, opacity))
    return layer


def _plus(m: Image.Image) -> Image.Image:
    out = m
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        out = ImageChops.lighter(out, ImageChops.offset(m, dx, dy))
    return out


def dilate(mask: Image.Image, r: float) -> Image.Image:
    """Round-ish dilation by ``r`` px (alternating square and plus steps = octagon), fractional radius
    blended, so outlines get round joins like a stroked path."""
    if r <= 0.05:
        return mask
    full = int(r)
    frac = r - full
    m = mask
    for i in range(full):
        m = m.filter(ImageFilter.MaxFilter(3)) if i % 2 == 0 else _plus(m)
    if frac > 0.05:
        nxt = m.filter(ImageFilter.MaxFilter(3)) if full % 2 == 0 else _plus(m)
        m = Image.blend(m, nxt, frac)
    return m


def gradient_image(size: tuple[int, int], grad: dict[str, Any]) -> Image.Image:
    """RGB image of a linear gradient (0 deg = left to right, 90 deg = top to bottom) over ``size``."""
    w, h = max(1, size[0]), max(1, size[1])
    stops = sorted(grad["stops"], key=lambda s: s["offset"])
    lut: list[tuple[int, int, int]] = []
    for i in range(256):
        t = i / 255
        if t <= stops[0]["offset"]:
            lut.append(rgb(stops[0]["color"]))
            continue
        if t >= stops[-1]["offset"]:
            lut.append(rgb(stops[-1]["color"]))
            continue
        for a, b in zip(stops, stops[1:]):
            if a["offset"] <= t <= b["offset"]:
                span = max(1e-6, b["offset"] - a["offset"])
                k = (t - a["offset"]) / span
                ca, cb = rgb(a["color"]), rgb(b["color"])
                lut.append(tuple(int(ca[j] + (cb[j] - ca[j]) * k + 0.5) for j in range(3)))  # type: ignore[arg-type]
                break
    ang = math.radians(grad["angle"])
    dx, dy = math.cos(ang), math.sin(ang)
    # t = projection of each pixel on the direction, normalised to the box: build a small field and resize
    sw, sh = min(w, 64), min(h, 64)
    corners = [(0, 0), (w, 0), (0, h), (w, h)]
    proj = [cx * dx + cy * dy for cx, cy in corners]
    lo, hi = min(proj), max(proj)
    span = max(1e-6, hi - lo)
    field_img = Image.new("L", (sw, sh))
    px = field_img.load()
    for j in range(sh):
        y = (j + 0.5) * h / sh
        for i in range(sw):
            x = (i + 0.5) * w / sw
            px[i, j] = int(max(0, min(255, (x * dx + y * dy - lo) / span * 255)))
    field_img = field_img.resize((w, h), Image.BILINEAR)
    chans = [field_img.point([c[k] for c in lut]) for k in range(3)]
    return Image.merge("RGB", chans)


def round_mask(w: int, h: int, r: float) -> Image.Image:
    """Anti-aliased rounded-rectangle coverage mask."""
    w, h = max(1, int(w)), max(1, int(h))
    m = Image.new("L", (w, h), 255)
    r = int(round(min(r, w / 2, h / 2)))
    if r <= 0:
        return m
    ss = 4
    circ = Image.new("L", (2 * r * ss, 2 * r * ss), 0)
    ImageDraw.Draw(circ).ellipse((0, 0, 2 * r * ss - 1, 2 * r * ss - 1), fill=255)
    circ = circ.resize((2 * r, 2 * r), Image.LANCZOS)
    m.paste(circ.crop((0, 0, r, r)), (0, 0))
    m.paste(circ.crop((r, 0, 2 * r, r)), (w - r, 0))
    m.paste(circ.crop((0, r, r, 2 * r)), (0, h - r))
    m.paste(circ.crop((r, r, 2 * r, 2 * r)), (w - r, h - r))
    return m


def reduced_motion() -> bool:
    """Windows "Animation effects" is off (SPI_GETCLIENTAREAANIMATION)."""
    if not IS_WINDOWS:
        return False
    try:
        val = wintypes.BOOL(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(val), 0):
            return not bool(val.value)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- inputs and outputs
@dataclass
class Lines:
    original: str = ""
    translation: str = ""
    next: str = ""
    speaker: str = ""
    status: str = ""


@dataclass
class Placed:
    """A rendered text block placed in the frame."""
    kind: str
    x: int
    y: int
    layer: Image.Image                     # shadows + outline + fill
    karaoke: "KaraokeLayers | None" = None
    rtl: bool = False


@dataclass
class KaraokeLayers:
    base: Image.Image                      # shadows + outline only
    unsung: Image.Image                    # fill at inactive opacity
    sung: Image.Image                      # highlight fill + glow
    rows: list[tuple[int, int, int, int, bool]]   # (x0, y0, x1, y1, rtl) of each wrapped line, layer coords
    pad: int


@dataclass
class Frame:
    image: Image.Image                     # full frame, RGBA (straight alpha)
    box: tuple[int, int, int, int]         # container rectangle inside ``image``
    karaoke: list[Placed] = field(default_factory=list)
    static: Image.Image | None = None      # frame without the karaoke lines' fills

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    def at(self, progress: float) -> Image.Image:
        """The frame with karaoke lines filled up to ``progress`` (0..1)."""
        if not self.karaoke or self.static is None or progress < 0:
            return self.image
        img = self.static.copy()
        for p in self.karaoke:
            k = p.karaoke
            assert k is not None
            clip = _sweep_clip(k.base.size, k.rows, progress)
            unsung = Image.composite(Image.new("RGBA", k.unsung.size, (0, 0, 0, 0)), k.unsung, clip)
            sung = Image.composite(k.sung, Image.new("RGBA", k.sung.size, (0, 0, 0, 0)), clip)
            img.alpha_composite(unsung, (p.x, p.y))
            img.alpha_composite(sung, (p.x, p.y))
        return img


def _sweep_clip(size: tuple[int, int], rows: list[tuple[int, int, int, int, bool]], progress: float) -> Image.Image:
    """255 where the line is already sung: rows fill one after the other from their start edge."""
    clip = Image.new("L", size, 0)
    d = ImageDraw.Draw(clip)
    total = sum(max(1, r[2] - r[0]) for r in rows) or 1
    left = progress * total
    h = size[1]
    for i, (x0, y0, x1, y1, rtl) in enumerate(rows):
        w = max(1, x1 - x0)
        top = 0 if i == 0 else y0
        bottom = h if i == len(rows) - 1 else y1
        if left <= 0:
            break
        part = min(w, left)
        left -= w
        if part >= w:
            d.rectangle((0, top, size[0], bottom), fill=255)
            continue
        edge = int(x1 - part) if rtl else int(x0 + part)
        if rtl:
            d.rectangle((edge, top, size[0], bottom), fill=255)
        else:
            d.rectangle((0, top, edge, bottom), fill=255)
        for s in range(1, 4):  # 3 px soft edge
            v = 255 - s * 64
            col = edge - s if rtl else edge + s
            d.line((col, top, col, bottom), fill=v)
    return clip


# ---------------------------------------------------------------- the renderer
class Renderer:
    def __init__(self) -> None:
        self._cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()
        self._lock = threading.RLock()

    # -- scale and size
    @staticmethod
    def scale(t: dict[str, Any], monitor_height: int, dpi_scale: float = 1.0, user: float = 1.0) -> float:
        mode = t["scale"]["mode"]
        if mode == "screen":
            base = monitor_height / max(1, t["scale"]["reference_height"])
        elif mode == "dpi":
            base = dpi_scale
        else:
            base = 1.0
        return max(0.25, base * user)

    @staticmethod
    def max_width(t: dict[str, Any], work_width: int, s: float) -> int:
        w = t["container"]["width"]
        if w["unit"] == "percent":
            px = work_width * min(100.0, w["value"]) / 100.0
        else:
            px = w["value"] * s
        return int(max(120, min(px, work_width if work_width > 0 else px)))

    # -- line blocks
    def _family(self, style: dict[str, Any], rtl: bool) -> list[str]:
        stack = style["font"]["rtl" if rtl else "latin"]
        out: list[str] = []
        for name in stack:
            if name == "auto":
                out += templates.DEFAULT_RTL if rtl else templates.DEFAULT_LATIN
            else:
                out.append(name)
        return out

    def font_key(self, style: dict[str, Any], rtl: bool, s: float) -> FontKey:
        fams = fonts.families()
        stack = self._family(style, rtl)
        fam = next((f for f in stack if f in fams), stack[0] if stack else "Segoe UI")
        face, weight = fonts.pick(fam, int(style["font"]["weight"]))
        italic = bool(style["font"]["italic"]) and not rtl
        return FontKey(face, max(4, int(round(style["font"]["size"] * s))), weight, italic)

    def _wrap(self, text: str, key: FontKey, rtl: bool, max_w: int, max_lines: int, spacing: int) -> list[str]:
        eng = engine()
        out: list[str] = []
        for para in text.split("\n"):
            words = para.split()
            line = ""
            for word in words:
                cand = f"{line} {word}" if line else word
                if not line or eng.width(key, cand, rtl, spacing) <= max_w:
                    if not line and eng.width(key, word, rtl, spacing) > max_w:
                        # a single word wider than the box (CJK without spaces): break it by characters
                        chunk = ""
                        for ch in word:
                            if chunk and eng.width(key, chunk + ch, rtl, spacing) > max_w:
                                out.append(chunk)
                                chunk = ch
                            else:
                                chunk += ch
                        line = chunk
                    else:
                        line = cand
                else:
                    out.append(line)
                    line = word
            if line:
                out.append(line)
        if len(out) > max_lines:
            keep = out[:max_lines]
            last = keep[-1]
            rest = " ".join(out[max_lines - 1:])
            # ellipsis at the logical end of the last kept line
            words = rest.split()
            fit = ""
            for word in words:
                cand = f"{fit} {word}" if fit else word
                if eng.width(key, cand + ELLIPSIS, rtl, spacing) > max_w:
                    break
                fit = cand
            keep[-1] = (fit or last) + ELLIPSIS
            out = keep
        return out or [""]

    def block(self, style: dict[str, Any], text: str, s: float, max_w: int, align: str, mirror: bool,
              kara: dict[str, Any] | None = None, block_w: int | None = None) -> tuple[Placed, int, int]:
        """Render one text block. Returns (placed with x=y=0, logical width, logical height); the layer has
        ``pad`` px of room on every side for outline, shadow and glow."""
        text = text.translate(BIDI_CONTROLS).strip()
        if style["uppercase"]:
            text = text.upper()
        rtl = langs.is_rtl_text(text)
        key = self.font_key(style, rtl, s)
        spacing = 0 if rtl else int(round(style["letter_spacing"] * s))
        ck = ("block", text, templates.style_hash(style), round(s, 3), max_w, align, mirror,
              templates.style_hash(kara) if kara else "", block_w)
        with self._lock:
            hit = self._cache.get(ck)
            if hit is not None:
                self._cache.move_to_end(ck)
                return hit
        eng = engine()
        rows = self._wrap(text, key, rtl, max_w, int(style["max_lines"]), spacing)
        widths = [eng.width(key, r, rtl, spacing) for r in rows]
        height, _asc, lead = eng.metrics(key)
        adv = max(height + lead, int(round(style["font"]["size"] * s * style["line_height"])))
        content_w = max(widths) if widths else 0
        bw = max(content_w, block_w or 0)
        bh = height + (len(rows) - 1) * adv
        o = style["outline"]
        ow = o["width"] * s if o["width"] > 0 and o["opacity"] > 0 else 0.0
        effects = list(style["shadow"])
        if kara and kara.get("glow"):
            effects.append(kara["glow"])
        reach = max([ow] + [(max(abs(e["x"]), abs(e["y"])) + e["blur"] * 1.6) * s + ow for e in effects])
        pad = int(math.ceil(reach + max(2, key.px * 0.25)))
        W, H = bw + 2 * pad, bh + 2 * pad
        mask = Image.new("L", (W, H), 0)
        row_boxes: list[tuple[int, int, int, int, bool]] = []
        for i, (row, rw) in enumerate(zip(rows, widths)):
            a = align
            if a in ("start", "end"):
                start_right = rtl and mirror
                a = ("right" if start_right else "left") if a == "start" else ("left" if start_right else "right")
            x = pad + {"left": 0, "right": bw - rw}.get(a, (bw - rw) // 2)
            y = pad + i * adv
            # draw with room around (overhangs), then paste the coverage into the block mask
            m = eng.mask(key, row, rtl, rw + 2 * pad, height + 2 * pad, pad, pad, spacing)
            mask = ImageChops.lighter(mask, _shift(m, x - pad, y - pad, (W, H)))
            row_boxes.append((x, y, x + rw, y + height, rtl))
        op = float(style["opacity"])
        shape = dilate(mask, ow) if ow else mask
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for sh in style["shadow"]:
            base.alpha_composite(self._shadow(shape, sh, s, op))
        if ow:
            base.alpha_composite(colorize(shape, o["color"], o["opacity"] * op))
        fill = self._fill(mask, style["color"], style["gradient"], op, row_boxes)
        layer = base.copy()
        layer.alpha_composite(fill)
        karaoke = None
        if kara is not None:
            hi = self._fill(mask, kara["highlight_color"], kara["highlight_gradient"], op, row_boxes)
            sung = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            if kara.get("glow"):
                sung.alpha_composite(self._shadow(mask, kara["glow"], s, op))
            sung.alpha_composite(hi)
            unsung = self._fill(mask, style["color"], style["gradient"], op * kara["inactive_opacity"], row_boxes)
            if kara["mode"] == "line":
                layer = base.copy()
                layer.alpha_composite(sung)
            else:
                karaoke = KaraokeLayers(base, unsung, sung, row_boxes, pad)
        placed = Placed("", 0, 0, layer, karaoke, rtl)
        res = (placed, bw, bh)
        with self._lock:
            self._cache[ck] = res
            while len(self._cache) > CACHE_LINES:
                self._cache.popitem(last=False)
        return res

    def _shadow(self, shape: Image.Image, sh: dict[str, Any], s: float, op: float) -> Image.Image:
        m = shape
        if sh["blur"] > 0:
            m = m.filter(ImageFilter.GaussianBlur(sh["blur"] * s / 2))
        dx, dy = int(round(sh["x"] * s)), int(round(sh["y"] * s))
        if dx or dy:
            m = _shift(m, dx, dy, m.size)
        return colorize(m, sh["color"], sh["opacity"] * op)

    def _fill(self, mask: Image.Image, color: str, grad: dict[str, Any] | None, op: float,
              rows: list[tuple[int, int, int, int, bool]]) -> Image.Image:
        if grad is None:
            return colorize(mask, color, op)
        if not rows:
            return colorize(mask, color, op)
        layer = Image.new("RGBA", mask.size, rgb(color) + (0,))
        # gradient across each line's own box
        for r in rows:
            g = gradient_image((max(1, r[2] - r[0]), max(1, r[3] - r[1])), grad)
            layer.paste(g, (r[0], r[1]))
        layer.putalpha(_scaled_alpha(mask, op))
        return layer

    # -- container
    def _box(self, t: dict[str, Any], w: int, h: int, s: float) -> tuple[Image.Image, int]:
        c = t["container"]
        ck = ("box", templates.style_hash(c), w, h, round(s, 3))
        with self._lock:
            hit = self._cache.get(ck)
            if hit is not None:
                return hit
        bg = c["background"]
        radius = c["corner_radius"] * s
        sh = c["shadow"]
        reach = 0
        if sh:
            reach = int(math.ceil((max(abs(sh["x"]), abs(sh["y"])) + sh["blur"] * 1.6) * s))
        m = reach + 2
        img = Image.new("RGBA", (w + 2 * m, h + 2 * m), (0, 0, 0, 0))
        shape = round_mask(w, h, radius)
        full = Image.new("L", img.size, 0)
        full.paste(shape, (m, m))
        if sh and sh["opacity"] > 0:
            img.alpha_composite(self._shadow(full, sh, s, 1.0))
        if bg["opacity"] > 0:
            if bg["gradient"]:
                g = gradient_image((w, h), bg["gradient"]).convert("RGBA")
                fill = Image.new("RGBA", img.size, (0, 0, 0, 0))
                fill.paste(g, (m, m))
                fill.putalpha(_scaled_alpha(full, bg["opacity"]))
            else:
                fill = colorize(full, bg["color"], bg["opacity"])
            img.alpha_composite(fill)
            blur = c["blur"]
            if blur["mode"] != "none":
                if blur["sheen"] > 0:
                    sheen = Image.linear_gradient("L").resize((1, 256)).transpose(Image.FLIP_TOP_BOTTOM)
                    sheen = sheen.resize((w, max(1, h // 2)))
                    sm = Image.new("L", img.size, 0)
                    sm.paste(sheen, (m, m))
                    sm = ImageChops.multiply(sm, full)
                    img.alpha_composite(colorize(sm, "#FFFFFF", blur["sheen"]))
                if blur["noise"] > 0:
                    noise = Image.effect_noise((w, h), 48).point(lambda v: min(255, abs(v - 128) * 2))
                    nm = Image.new("L", img.size, 0)
                    nm.paste(noise, (m, m))
                    nm = ImageChops.multiply(nm, full)
                    img.alpha_composite(colorize(nm, "#FFFFFF", min(1.0, blur["noise"] * 4)))
        b = c["border"]
        bw = b["width"] * s
        if bw > 0 and b["opacity"] > 0:
            inner = Image.new("L", img.size, 0)
            ib = max(1, int(round(bw)))
            inner.paste(round_mask(w - 2 * ib, h - 2 * ib, max(0, radius - ib)), (m + ib, m + ib))
            ring = ImageChops.subtract(full, inner)
            img.alpha_composite(colorize(ring, b["color"], b["opacity"]))
        res = (img, m)
        with self._lock:
            self._cache[ck] = res
            while len(self._cache) > CACHE_LINES:
                self._cache.popitem(last=False)
        return res

    # -- frame
    def render(self, t: dict[str, Any], lines: Lines, s: float, max_w: int, *, show: dict[str, bool] | None = None,
               karaoke: bool = True, hit: bool = False, fixed_w: int | None = None) -> Frame:
        """Draw a whole frame. ``show`` can switch lines off (menu "Show"); ``karaoke`` allows the karaoke
        highlight; ``hit`` gives the box area a minimum alpha so a text-only overlay still catches the mouse."""
        show = show or {}
        c = t["container"]
        L = t["lines"]
        pad_x, pad_y = int(round(c["padding"]["x"] * s)), int(round(c["padding"]["y"] * s))
        inner_max = max(40, (fixed_w or max_w) - 2 * pad_x)
        mirror = bool(t["layout"]["mirror_for_rtl"])
        align = c["align"]
        k = t["karaoke"]
        order: list[tuple[str, str, float]] = []   # (kind, text, gap before)
        original = lines.original if show.get("original", True) and L["original"]["visible"] else ""
        translation = lines.translation if show.get("translation", True) and L["translation"]["visible"] else ""
        if not original and not translation and not lines.status and L["original"]["visible"]:
            original = "♪"
        sp = L["speaker"]
        inline_speaker = ""
        if lines.speaker and sp["visible"] and original:
            label = sp["format"].replace("{name}", lines.speaker)
            if sp["placement"] == "above":
                order.append(("speaker", label, 0))
            else:
                inline_speaker = label
        pair = [("original", original), ("translation", translation)]
        if t["layout"]["translation_position"] == "above":
            pair.reverse()
        for kind, text in pair:
            if text:
                order.append((kind, text, c["line_gap"] * s if order else 0))
        nxt = lines.next if show.get("next", True) and L["next"]["visible"] else ""
        if nxt and order:
            order.append(("next", nxt, (c["line_gap"] + c["group_gap"]) * s))
        if lines.status:
            st = dict(L["next"], visible=True, max_lines=2)
            order.append(("status", lines.status, (c["line_gap"] * s) if order else 0))
        else:
            st = None
        blocks: list[tuple[Placed, int, int, float, str]] = []
        for kind, text, gap in order:
            style = st if kind == "status" else L[kind]
            kara = None
            if karaoke and k["enabled"] and kind in ("original", "translation") and k["applies_to"] in (kind, "both") \
                    and text != "♪":
                kara = k
            if kind == "original" and inline_speaker:
                sp_placed, sbw, sbh = self.block(sp, inline_speaker, s, inner_max, align, mirror)
                gap_x = int(round(sp["font"]["size"] * s * 0.35))
                placed, bw, bh = self.block(style, text, s, max(40, inner_max - sbw - gap_x), align, mirror, kara)
                if kara is None and sbw + gap_x + bw <= inner_max:
                    placed, bw, bh = _beside(sp_placed, sbw, sbh, placed, bw, bh, gap_x)
                else:   # no room (or a karaoke line): the label goes on its own line above
                    blocks.append((sp_placed, sbw, sbh, gap, "speaker"))
                    gap = c["line_gap"] * s
                    placed, bw, bh = self.block(style, text, s, inner_max, align, mirror, kara)  # type: ignore[arg-type]
            else:
                placed, bw, bh = self.block(style, text, s, inner_max, align, mirror, kara)  # type: ignore[arg-type]
            blocks.append((placed, bw, bh, gap, kind))
        content_w = max([b[1] for b in blocks] + [0])
        wcfg = c["width"]
        if fixed_w:
            box_w = fixed_w
        elif wcfg["fit_content"]:
            box_w = max(int(wcfg["min_px"] * s), content_w + 2 * pad_x)
            box_w = min(box_w, max_w)
        else:
            box_w = max_w
        box_w = max(box_w, 2 * pad_x + 8)
        content_h = sum(b[2] + int(b[3]) for b in blocks)
        box_h = max(content_h + 2 * pad_y, int(16 * s))
        box_img, bm = self._box(t, box_w, box_h, s)
        margin = max([bm] + [b[0].layer.size[0] // 2 - b[1] // 2 for b in blocks] + [0])
        margin = max(margin, max([(b[0].layer.size[1] - b[2]) // 2 for b in blocks] + [0]))
        W, H = box_w + 2 * margin, box_h + 2 * margin
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img.alpha_composite(box_img, (margin - bm, margin - bm))
        static = img.copy()
        kara_placed: list[Placed] = []
        y = margin + pad_y
        inner_x0, inner_w = margin + pad_x, box_w - 2 * pad_x
        for placed, bw, bh, gap, kind in blocks:
            y += int(gap)
            lay_pad = (placed.layer.size[0] - bw) // 2
            a = align
            if a in ("start", "end"):
                start_right = placed.rtl and mirror
                a = ("right" if start_right else "left") if a == "start" else ("left" if start_right else "right")
            if a == "left":
                x = inner_x0
            elif a == "right":
                x = inner_x0 + inner_w - bw
            else:
                x = inner_x0 + (inner_w - bw) // 2
            px, py = x - lay_pad, y - lay_pad
            if placed.karaoke is not None:
                static.alpha_composite(placed.karaoke.base, (px, py))
                img.alpha_composite(placed.layer, (px, py))
                kara_placed.append(Placed(kind, px, py, placed.layer, placed.karaoke))
            else:
                img.alpha_composite(placed.layer, (px, py))
                static.alpha_composite(placed.layer, (px, py))
            y += bh
        box = (margin, margin, margin + box_w, margin + box_h)
        if hit:
            for im in (img, static):
                a = im.getchannel("A")
                floor = Image.new("L", im.size, 0)
                ImageDraw.Draw(floor).rectangle((max(0, box[0] - GRIP), max(0, box[1] - GRIP),
                                                 min(W - 1, box[2] + GRIP), min(H - 1, box[3] + GRIP)), fill=1)
                im.putalpha(ImageChops.lighter(a, floor))
        return Frame(img, box, kara_placed, static if kara_placed else None)

    # -- unsynced lyrics: a scrolling list in the box
    def render_list(self, t: dict[str, Any], rows: list[tuple[str, str]], status: str, s: float, max_w: int,
                    view_h: int, scroll: int, *, show: dict[str, bool] | None = None, hit: bool = False,
                    fixed_w: int | None = None) -> tuple[Frame, int]:
        """Frame of a ``view_h`` px high box showing the list from ``scroll`` px. Returns (frame, total height)."""
        show = show or {}
        c = t["container"]
        L = t["lines"]
        pad_x, pad_y = int(round(c["padding"]["x"] * s)), int(round(c["padding"]["y"] * s))
        box_w = fixed_w or max_w
        inner = max(40, box_w - 2 * pad_x)
        mirror = bool(t["layout"]["mirror_for_rtl"])
        key = ("list", tuple(rows), status, templates.style_hash(t), round(s, 3), box_w,
               tuple(sorted(show.items())))
        with self._lock:
            content = self._cache.get(key)
        if content is None:
            parts: list[tuple[Image.Image, int, int, int]] = []
            y = 0
            if status:
                st = dict(L["next"], visible=True, max_lines=3)
                p, bw, bh = self.block(st, status, s, inner, c["align"], mirror)
                parts.append((p.layer, bw, bh, y))
                y += bh + int(c["group_gap"] * s)
            for orig, trans in rows:
                if not orig.strip():
                    y += int(12 * s)
                    continue
                pair = []
                if show.get("original", True) and L["original"]["visible"]:
                    pair.append((L["original"], orig))
                if trans and show.get("translation", True) and L["translation"]["visible"]:
                    pair.append((L["translation"], trans))
                if t["layout"]["translation_position"] == "above":
                    pair.reverse()
                for style, text in pair:
                    st2 = dict(style, max_lines=8)
                    p, bw, bh = self.block(st2, text, s, inner, c["align"], mirror)
                    parts.append((p.layer, bw, bh, y))
                    y += bh + int(c["line_gap"] * s)
                y += int(c["group_gap"] * s)
            total = max(1, y)
            content = Image.new("RGBA", (inner + 200, total + 200), (0, 0, 0, 0))
            for layer, bw, bh, yy in parts:
                lp = (layer.size[0] - bw) // 2
                a = c["align"]
                x = 100 + ({"left": 0, "right": inner - bw}.get(a, (inner - bw) // 2))
                content.alpha_composite(layer, (x - lp, 100 + yy - lp))
            content = (content, total)
            with self._lock:
                self._cache[key] = content
        img_all, total = content
        box_h = max(int(60 * s), view_h)
        box_img, bm = self._box(t, box_w, box_h, s)
        margin = bm
        W, H = box_w + 2 * margin, box_h + 2 * margin
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img.alpha_composite(box_img, (0, 0))
        scroll = max(0, min(scroll, max(0, total - (box_h - 2 * pad_y))))
        view = img_all.crop((100 - pad_x // 2, 100 + scroll, 100 + inner + pad_x // 2,
                             100 + scroll + box_h - 2 * pad_y))
        clip = round_mask(box_w, box_h, c["corner_radius"] * s)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.alpha_composite(view, (margin + pad_x - pad_x // 2, margin + pad_y))
        cm = Image.new("L", (W, H), 0)
        cm.paste(clip, (margin, margin))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), cm))
        img.alpha_composite(layer)
        box = (margin, margin, margin + box_w, margin + box_h)
        if hit:
            a = img.getchannel("A")
            floor = Image.new("L", img.size, 0)
            ImageDraw.Draw(floor).rectangle(box, fill=1)
            img.putalpha(ImageChops.lighter(a, floor))
        return Frame(img, box), total

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def _beside(a: Placed, aw: int, ah: int, b: Placed, bw: int, bh: int, gap: int) -> tuple[Placed, int, int]:
    """Speaker label ``a`` before the line ``b`` on one row (on the right for a right-to-left line)."""
    pa = (a.layer.size[0] - aw) // 2
    pb = (b.layer.size[0] - bw) // 2
    pad = max(pa, pb)
    W, H = aw + gap + bw, max(ah, bh)
    layer = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    ax, bx = (bw + gap, 0) if b.rtl else (0, aw + gap)
    layer.alpha_composite(a.layer, (pad + ax - pa, pad - pa))
    layer.alpha_composite(b.layer, (pad + bx - pb, pad - pb))
    return Placed("", 0, 0, layer, None, b.rtl), W, H


def _shift(img: Image.Image, dx: int, dy: int, size: tuple[int, int]) -> Image.Image:
    out = Image.new(img.mode, size, 0)
    out.paste(img, (dx, dy))
    return out


# ---------------------------------------------------------------- preview backdrops (no screenshots)
def backdrop(kind: str, size: tuple[int, int]) -> Image.Image:
    """Generated scenes for the preview: dark, bright, busy game, checker (transparency)."""
    w, h = size
    if kind == "bright":
        g = gradient_image((w, h), {"angle": 90, "stops": [{"offset": 0, "color": "#F5F7FA"},
                                                           {"offset": 1, "color": "#CFE3F5"}]})
        d = ImageDraw.Draw(g)
        for i in range(7):  # soft hills of snow
            cx = int(w * (i / 6))
            d.ellipse((cx - w // 4, int(h * 0.72), cx + w // 4, int(h * 1.25)), fill=(250, 252, 255))
        return g.convert("RGB")
    if kind == "game":
        import random

        rnd = random.Random(7)
        base = gradient_image((w, h), {"angle": 35, "stops": [{"offset": 0, "color": "#3B2A1A"},
                                                              {"offset": 0.5, "color": "#6B7F5A"},
                                                              {"offset": 1, "color": "#1E3A5F"}]})
        d = ImageDraw.Draw(base)
        for _ in range(int(w * h / 900)):
            x, y = rnd.randrange(w), rnd.randrange(h)
            r = rnd.choice((1, 1, 2, 3, 6, 10))
            col = rnd.choice(((255, 255, 255), (250, 200, 80), (220, 70, 40), (120, 200, 255), (30, 30, 30),
                              (200, 200, 200)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        noise = Image.effect_noise((w, h), 60).convert("RGB")
        return Image.blend(base, noise, 0.18)
    if kind == "checker":
        img = Image.new("RGB", (w, h), (200, 200, 200))
        d = ImageDraw.Draw(img)
        step = 12
        for yy in range(0, h, step):
            for xx in range(0, w, step):
                if (xx // step + yy // step) % 2:
                    d.rectangle((xx, yy, xx + step - 1, yy + step - 1), fill=(150, 150, 150))
        return img
    base = gradient_image((w, h), {"angle": 90, "stops": [{"offset": 0, "color": "#05070D"},
                                                          {"offset": 1, "color": "#1B2333"}]})
    noise = Image.effect_noise((w, h), 30).convert("RGB")
    return Image.blend(base, noise, 0.05)


def compose_on(back: Image.Image, frame_img: Image.Image, xy: tuple[int, int]) -> Image.Image:
    out = back.convert("RGBA")
    out.alpha_composite(frame_img, (max(0, xy[0]), max(0, xy[1])))
    return out.convert("RGB")


def text_contrast(t: dict[str, Any], kind: str, back_rgb: tuple[int, int, int]) -> float:
    """Contrast of a line against the box composited over a backdrop colour (or its outline, if better)."""
    style = t["lines"][kind]
    bg = t["container"]["background"]
    b = tuple(v / 255 for v in back_rgb)
    best = math.inf
    stops = [s["color"] for s in bg["gradient"]["stops"]] if bg["gradient"] else [bg["color"]]
    cols = [s["color"] for s in style["gradient"]["stops"]] if style["gradient"] else [style["color"]]
    for bc in stops:
        eff = templates.over(templates.hex_rgb(bc), bg["opacity"], b)  # type: ignore[arg-type]
        for c in cols:
            best = min(best, templates.ratio(templates.over(templates.hex_rgb(c), style["opacity"], eff), eff))
    o = style["outline"]
    if o["width"] >= 1 and o["opacity"] > 0:
        best = max(best, min(templates.ratio(templates.hex_rgb(c), templates.hex_rgb(o["color"])) for c in cols))
    return round(best, 1)
