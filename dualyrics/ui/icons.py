"""Small line icons drawn with Pillow (no emoji, no icon font): sidebar, menus and buttons.

Each icon is drawn on a 24-unit grid at 4x and scaled down, so strokes stay crisp at 16 and 20 px.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Callable

from PIL import Image, ImageDraw

SS = 4          # supersampling
GRID = 24.0

Draw = Callable[[ImageDraw.ImageDraw, Callable[[float], float], float], None]
_ICONS: dict[str, Draw] = {}


def icon(name: str) -> Callable[[Draw], Draw]:
    def deco(fn: Draw) -> Draw:
        _ICONS[name] = fn
        return fn
    return deco


def names() -> list[str]:
    return sorted(_ICONS)


@lru_cache(maxsize=512)
def render(name: str, size: int = 20, color: str = "#E6EDF7", stroke: float = 1.8) -> Image.Image:
    """RGBA icon image (transparent background)."""
    px = size * SS
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = px / GRID

    def u(v: float) -> float:
        return v * k

    fn = _ICONS.get(name, _ICONS["dot"])
    d._dl_color = color  # type: ignore[attr-defined]
    d._dl_w = max(1, int(round(stroke * k)))  # type: ignore[attr-defined]
    fn(d, u, k)
    return img.resize((size, size), Image.LANCZOS)


# -- drawing helpers (every helper reads the colour and stroke stored on the draw object)
def _line(d: ImageDraw.ImageDraw, u: Callable[[float], float], *pts: float) -> None:
    d.line([u(p) for p in pts], fill=d._dl_color, width=d._dl_w, joint="curve")  # type: ignore[attr-defined]
    r = d._dl_w / 2  # type: ignore[attr-defined]
    for x, y in ((pts[0], pts[1]), (pts[-2], pts[-1])):
        d.ellipse((u(x) - r, u(y) - r, u(x) + r, u(y) + r), fill=d._dl_color)  # type: ignore[attr-defined]


def _circle(d: ImageDraw.ImageDraw, u: Callable[[float], float], cx: float, cy: float, r: float,
            fill: bool = False) -> None:
    box = (u(cx - r), u(cy - r), u(cx + r), u(cy + r))
    if fill:
        d.ellipse(box, fill=d._dl_color)  # type: ignore[attr-defined]
    else:
        d.ellipse(box, outline=d._dl_color, width=d._dl_w)  # type: ignore[attr-defined]


def _rect(d: ImageDraw.ImageDraw, u: Callable[[float], float], x0: float, y0: float, x1: float, y1: float,
          r: float = 2, fill: bool = False) -> None:
    box = (u(x0), u(y0), u(x1), u(y1))
    if fill:
        d.rounded_rectangle(box, radius=u(r), fill=d._dl_color)  # type: ignore[attr-defined]
    else:
        d.rounded_rectangle(box, radius=u(r), outline=d._dl_color, width=d._dl_w)  # type: ignore[attr-defined]


def _arc(d: ImageDraw.ImageDraw, u: Callable[[float], float], cx: float, cy: float, r: float, a0: float,
         a1: float) -> None:
    d.arc((u(cx - r), u(cy - r), u(cx + r), u(cy + r)), a0, a1, fill=d._dl_color,  # type: ignore[attr-defined]
          width=d._dl_w)  # type: ignore[attr-defined]


@icon("dot")
def _dot(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 3, fill=True)


@icon("general")
def _general(d, u, k):  # noqa: ANN001
    for y, x in ((6, 15), (12, 8), (18, 13)):
        _line(d, u, 4, y, 20, y)
        _circle(d, u, x, y, 2.3, fill=True)


@icon("profiles")
def _profiles(d, u, k):  # noqa: ANN001
    _rect(d, u, 4, 4, 10.5, 10.5, 2)
    _rect(d, u, 13.5, 4, 20, 10.5, 2)
    _rect(d, u, 4, 13.5, 10.5, 20, 2)
    _line(d, u, 16.75, 13.5, 16.75, 20)
    _line(d, u, 13.5, 16.75, 20, 16.75)


@icon("templates")
def _templates(d, u, k):  # noqa: ANN001
    _rect(d, u, 3.5, 6, 20.5, 18, 3)
    _line(d, u, 7, 10.5, 17, 10.5)
    _line(d, u, 8.5, 14, 15.5, 14)


@icon("lyrics")
def _lyrics(d, u, k):  # noqa: ANN001
    _line(d, u, 10, 17, 10, 5, 19, 3.5, 19, 15)
    _circle(d, u, 7.5, 17.5, 2.6)
    _circle(d, u, 16.5, 15.5, 2.6)


@icon("live")
def _live(d, u, k):  # noqa: ANN001
    _rect(d, u, 9, 3.5, 15, 14, 3)
    _arc(d, u, 12, 10.5, 6, 0, 180)
    _line(d, u, 12, 16.5, 12, 20.5)
    _line(d, u, 8.5, 20.5, 15.5, 20.5)


@icon("translation")
def _translation(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    d.ellipse((u(8), u(3.5), u(16), u(20.5)), outline=d._dl_color, width=d._dl_w)
    _line(d, u, 3.5, 12, 20.5, 12)


@icon("monitors")
def _monitors(d, u, k):  # noqa: ANN001
    _rect(d, u, 3, 4.5, 21, 16, 2)
    _line(d, u, 12, 16, 12, 19.5)
    _line(d, u, 8, 19.5, 16, 19.5)


@icon("sync")
def _sync(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    _line(d, u, 12, 7, 12, 12, 15.5, 14)


@icon("network")
def _network(d, u, k):  # noqa: ANN001
    _arc(d, u, 12, 19, 14, 225, 315)
    _arc(d, u, 12, 19, 9.5, 225, 315)
    _arc(d, u, 12, 19, 5, 225, 315)
    _circle(d, u, 12, 18.5, 1.6, fill=True)


@icon("about")
def _about(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    _line(d, u, 12, 11, 12, 16.5)
    _circle(d, u, 12, 7.8, 1.2, fill=True)


@icon("mode")
def _mode(d, u, k):  # noqa: ANN001
    _arc(d, u, 12, 12, 7.5, 200, 340)
    _arc(d, u, 12, 12, 7.5, 20, 160)
    _line(d, u, 19.5, 6.5, 19.2, 10.3, 15.5, 9.8)
    _line(d, u, 4.5, 17.5, 4.8, 13.7, 8.5, 14.2)


@icon("eye")
def _eye(d, u, k):  # noqa: ANN001
    _arc(d, u, 12, 19, 10, 215, 325)
    _arc(d, u, 12, 5, 10, 35, 145)
    _circle(d, u, 12, 12, 2.8)


@icon("eye_off")
def _eye_off(d, u, k):  # noqa: ANN001
    _eye(d, u, k)
    _line(d, u, 4, 4, 20, 20)


@icon("text_size")
def _text_size(d, u, k):  # noqa: ANN001
    _line(d, u, 3, 19, 8, 6, 13, 19)
    _line(d, u, 5, 14.5, 11, 14.5)
    _line(d, u, 17.5, 7, 17.5, 15)
    _line(d, u, 14.5, 10, 17.5, 7, 20.5, 10)


@icon("plus")
def _plus(d, u, k):  # noqa: ANN001
    _line(d, u, 12, 5, 12, 19)
    _line(d, u, 5, 12, 19, 12)


@icon("minus")
def _minus(d, u, k):  # noqa: ANN001
    _line(d, u, 5, 12, 19, 12)


@icon("opacity")
def _opacity(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8)
    d.pieslice((u(4), u(4), u(20), u(20)), 90, 270, fill=d._dl_color)


@icon("position")
def _position(d, u, k):  # noqa: ANN001
    _rect(d, u, 3, 4, 21, 18, 2)
    _rect(d, u, 7, 13, 17, 16, 1, fill=True)


@icon("language")
def _language(d, u, k):  # noqa: ANN001
    _line(d, u, 3, 6, 12, 6)
    _line(d, u, 7.5, 4, 7.5, 6)
    _line(d, u, 10.5, 6, 9, 10, 4, 14)
    _line(d, u, 6, 9, 11, 13)
    _line(d, u, 12, 20, 16, 10, 20, 20)
    _line(d, u, 13.5, 16.5, 18.5, 16.5)


@icon("copy")
def _copy(d, u, k):  # noqa: ANN001
    _rect(d, u, 8, 8, 20, 20, 2)
    _line(d, u, 16, 5, 16, 4.5, 4.5, 4.5, 4.5, 16, 5, 16)


@icon("edit")
def _edit(d, u, k):  # noqa: ANN001
    _line(d, u, 4.5, 19.5, 5.5, 15, 15.5, 5, 19, 8.5, 9, 18.5, 4.5, 19.5)
    _line(d, u, 13, 7.5, 16.5, 11)


@icon("retranslate")
def _retranslate(d, u, k):  # noqa: ANN001
    _arc(d, u, 12, 12, 7.5, 300, 240)
    _line(d, u, 6.5, 3.5, 7.8, 7.6, 3.8, 8.8)


@icon("lock")
def _lock(d, u, k):  # noqa: ANN001
    _rect(d, u, 5, 10.5, 19, 20, 2)
    _arc(d, u, 12, 9, 4.5, 180, 360)
    _line(d, u, 7.5, 9, 7.5, 10.5)
    _line(d, u, 16.5, 9, 16.5, 10.5)


@icon("click")
def _click(d, u, k):  # noqa: ANN001
    _line(d, u, 6, 4, 6, 18, 9.5, 14.5, 12.5, 20, 14.5, 19, 11.5, 13.5, 16.5, 13.5, 6, 4)


@icon("pause")
def _pause(d, u, k):  # noqa: ANN001
    _rect(d, u, 6.5, 5, 10, 19, 1, fill=True)
    _rect(d, u, 14, 5, 17.5, 19, 1, fill=True)


@icon("play")
def _play(d, u, k):  # noqa: ANN001
    d.polygon([(u(7), u(5)), (u(19), u(12)), (u(7), u(19))], fill=d._dl_color)


@icon("save")
def _save(d, u, k):  # noqa: ANN001
    _line(d, u, 12, 4, 12, 15)
    _line(d, u, 7.5, 10.5, 12, 15, 16.5, 10.5)
    _line(d, u, 4.5, 16, 4.5, 20, 19.5, 20, 19.5, 16)


@icon("settings")
def _settings(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 3)
    for i in range(8):
        a = math.radians(i * 45)
        _line(d, u, 12 + math.cos(a) * 6.2, 12 + math.sin(a) * 6.2, 12 + math.cos(a) * 8.8, 12 + math.sin(a) * 8.8)
    _circle(d, u, 12, 12, 6.2)


@icon("quit")
def _quit(d, u, k):  # noqa: ANN001
    _arc(d, u, 12, 13, 7.5, 300, 240)
    _line(d, u, 12, 3.5, 12, 11)


@icon("check")
def _check(d, u, k):  # noqa: ANN001
    _line(d, u, 5, 12.5, 10, 17.5, 19, 7)


@icon("add")
def _add(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    _line(d, u, 12, 8, 12, 16)
    _line(d, u, 8, 12, 16, 12)


@icon("trash")
def _trash(d, u, k):  # noqa: ANN001
    _line(d, u, 4, 6.5, 20, 6.5)
    _line(d, u, 9.5, 6.5, 9.5, 4, 14.5, 4, 14.5, 6.5)
    _line(d, u, 6, 6.5, 7, 20, 17, 20, 18, 6.5)


@icon("folder")
def _folder(d, u, k):  # noqa: ANN001
    _line(d, u, 3.5, 18.5, 3.5, 5.5, 9.5, 5.5, 11.5, 8, 20.5, 8, 20.5, 18.5, 3.5, 18.5)


@icon("target")
def _target(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 7.5)
    _line(d, u, 12, 2.5, 12, 7)
    _line(d, u, 12, 17, 12, 21.5)
    _line(d, u, 2.5, 12, 7, 12)
    _line(d, u, 17, 12, 21.5, 12)


@icon("import")
def _import(d, u, k):  # noqa: ANN001
    _line(d, u, 12, 3.5, 12, 14)
    _line(d, u, 7.5, 9.5, 12, 14, 16.5, 9.5)
    _line(d, u, 4.5, 15, 4.5, 20, 19.5, 20, 19.5, 15)


@icon("export")
def _export(d, u, k):  # noqa: ANN001
    _line(d, u, 12, 14, 12, 3.5)
    _line(d, u, 7.5, 8, 12, 3.5, 16.5, 8)
    _line(d, u, 4.5, 15, 4.5, 20, 19.5, 20, 19.5, 15)


@icon("chevron_left")
def _chev_l(d, u, k):  # noqa: ANN001
    _line(d, u, 15, 5, 8, 12, 15, 19)


@icon("chevron_right")
def _chev_r(d, u, k):  # noqa: ANN001
    _line(d, u, 9, 5, 16, 12, 9, 19)


@icon("warning")
def _warning(d, u, k):  # noqa: ANN001
    _line(d, u, 12, 3.5, 21, 19.5, 3, 19.5, 12, 3.5)
    _line(d, u, 12, 9.5, 12, 14)
    _circle(d, u, 12, 16.8, 1.1, fill=True)


@icon("ok")
def _ok(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    _line(d, u, 8, 12.5, 11, 15.5, 16.5, 9)


@icon("error")
def _error(d, u, k):  # noqa: ANN001
    _circle(d, u, 12, 12, 8.5)
    _line(d, u, 9, 9, 15, 15)
    _line(d, u, 15, 9, 9, 15)


@icon("dubbing")
def _dubbing(d, u, k):  # noqa: ANN001
    _line(d, u, 3.5, 9.5, 7.5, 9.5, 12.5, 5, 12.5, 19, 7.5, 14.5, 3.5, 14.5, 3.5, 9.5)
    _arc(d, u, 12.5, 12, 4, -55, 55)
    _arc(d, u, 12.5, 12, 8, -55, 55)


@icon("usage")
def _usage(d, u, k):  # noqa: ANN001
    _line(d, u, 3.5, 20.5, 20.5, 20.5)
    _rect(d, u, 5, 13, 8.5, 20.5, 1)
    _rect(d, u, 10.25, 8, 13.75, 20.5, 1)
    _rect(d, u, 15.5, 3.5, 19, 20.5, 1)


@icon("ai")
def _ai(d, u, k):  # noqa: ANN001
    # a large four-point sparkle and a small one (AI assistants)
    _line(d, u, 10, 4, 11.6, 10.4, 18, 12, 11.6, 13.6, 10, 20, 8.4, 13.6, 2, 12, 8.4, 10.4, 10, 4)
    _line(d, u, 18.5, 2.5, 19.2, 4.8, 21.5, 5.5, 19.2, 6.2, 18.5, 8.5, 17.8, 6.2, 15.5, 5.5, 17.8, 4.8, 18.5, 2.5)


@icon("star")
def _star(d, u, k):  # noqa: ANN001
    import math

    pts = []
    for i in range(10):
        r = 8.5 if i % 2 == 0 else 3.8
        a = math.radians(-90 + i * 36)
        pts += [12 + r * math.cos(a), 12.6 + r * math.sin(a)]
    _line(d, u, *pts, pts[0], pts[1])