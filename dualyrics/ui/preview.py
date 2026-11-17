"""Live preview of a template (same renderer as the overlay) over generated backdrops, preset thumbnails and
contrast badges (docs/DESIGN.md 1.7)."""

from __future__ import annotations

from typing import Any

from PIL import Image

from dualyrics import langs, render, templates

SAMPLE_ORIGINAL = "I still hear your voice when the night is quiet"
SAMPLE_NEXT = "And every word is calling me home"
SAMPLE_SPEAKER = "NARRATOR"
# the sample line in a few target languages (other targets show the Persian sample)
SAMPLE_TRANSLATIONS = {
    "fa": "هنوز وقتی شب آرام است صدایت را می‌شنوم",
    "ar": "ما زلت أسمع صوتك حين يهدأ الليل",
    "he": "אני עדיין שומע את קולך כשהלילה שקט",
    "ur": "رات خاموش ہو تو اب بھی تمہاری آواز سنتا ہوں",
    "en": "I still hear your voice when the night is quiet",
    "es": "Todavía oigo tu voz cuando la noche está en calma",
    "fr": "J'entends encore ta voix quand la nuit est calme",
    "de": "Ich höre noch deine Stimme, wenn die Nacht still ist",
    "it": "Sento ancora la tua voce quando la notte tace",
    "pt": "Ainda ouço a tua voz quando a noite está calma",
    "tr": "Gece sessizken hâlâ sesini duyuyorum",
    "ru": "Я всё ещё слышу твой голос, когда ночь тиха",
    "zh-CN": "夜深人静时，我仍能听见你的声音",
    "ja": "夜が静かになると、まだ君の声が聞こえる",
    "ko": "밤이 고요해지면 아직도 너의 목소리가 들려",
    "hi": "रात जब शांत होती है, मुझे अब भी तुम्हारी आवाज़ सुनाई देती है",
}
BACKDROP_MEAN = {"dark": (14, 18, 28), "bright": (232, 240, 248), "checker": (175, 175, 175)}
PREVIEW_SIZE = (348, 196)
THUMB_SIZE = (160, 90)
_backdrops: dict[tuple[str, tuple[int, int]], Image.Image] = {}


def sample_lines(target: str, current: render.Lines | None = None, speaker: bool = False) -> render.Lines:
    if current is not None and (current.original or current.translation):
        return render.Lines(current.original, current.translation, current.next, "", "")
    tr = SAMPLE_TRANSLATIONS.get(target) or SAMPLE_TRANSLATIONS.get(langs.base_code(target), "") \
        or SAMPLE_TRANSLATIONS["fa"]
    if tr == SAMPLE_ORIGINAL:
        tr = SAMPLE_TRANSLATIONS["fa"]
    return render.Lines(SAMPLE_ORIGINAL, tr, SAMPLE_NEXT, SAMPLE_SPEAKER if speaker else "", "")


def backdrop(kind: str, size: tuple[int, int]) -> Image.Image:
    key = (kind, size)
    img = _backdrops.get(key)
    if img is None:
        img = render.backdrop(kind, size)
        _backdrops[key] = img
    return img


def backdrop_mean(kind: str) -> tuple[int, int, int]:
    if kind in BACKDROP_MEAN:
        return BACKDROP_MEAN[kind]
    img = backdrop(kind, (64, 36)).resize((1, 1), Image.BOX)
    return img.getpixel((0, 0))[:3]  # type: ignore[return-value]


def frame_for(renderer: render.Renderer, t: dict[str, Any], lines: render.Lines, size: tuple[int, int],
              s: float) -> render.Frame:
    """Render ``t`` for a preview of ``size``: as if the preview showed the middle 80 % of a screen."""
    w, _h = size
    work = int(w * 1.25)
    max_w = min(w - 8, renderer.max_width(t, work, s))
    return renderer.render(t, lines, s, max_w)


def compose(renderer: render.Renderer, t: dict[str, Any], lines: render.Lines, kind: str,
            size: tuple[int, int] = PREVIEW_SIZE, s: float = 0.42, progress: float = 0.45,
            alpha: float = 1.0, dy: int = 0, frame: render.Frame | None = None) -> Image.Image:
    """Backdrop + rendered frame (bottom centred), RGB."""
    w, h = size
    f = frame or frame_for(renderer, t, lines, size, s)
    img = f.at(progress) if f.karaoke else f.image
    bx0, by0, bx1, by1 = f.box
    if bx1 - bx0 > w - 4:  # never wider than the preview
        k = (w - 4) / (bx1 - bx0)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
        bx0, by0, bx1, by1 = (int(v * k) for v in f.box)
    if alpha < 0.999:
        a = img.getchannel("A").point([int(v * alpha) for v in range(256)])
        img = img.copy()
        img.putalpha(a)
    x = (w - (bx1 - bx0)) // 2 - bx0
    margin = max(6, int(h * 0.07))
    y = h - margin - (by1 - by0) - by0 + dy
    if (by1 - by0) > h - 2 * margin:
        y = (h - (by1 - by0)) // 2 - by0 + dy
    out = backdrop(kind, size).convert("RGBA")
    out.alpha_composite(img, (max(0, x), max(0, y)), (max(0, -x), max(0, -y)))
    return out.convert("RGB")


def thumbnail(renderer: render.Renderer, t: dict[str, Any], target: str, kind: str = "dark",
              size: tuple[int, int] = THUMB_SIZE) -> Image.Image:
    lines = sample_lines(target)
    lines.next = ""
    return compose(renderer, t, lines, kind, size, s=0.2, progress=0.5)


def contrast(t: dict[str, Any], kind: str, visible: list[str]) -> list[tuple[str, float, float, bool]]:
    """(line, ratio, needed, ok) for each visible line against the box over the backdrop."""
    back = backdrop_mean(kind)
    out = []
    for line in visible:
        style = t["lines"][line]
        r = render.text_contrast(t, line, back)
        need = 3.0 if templates.is_large(style) else 4.5
        out.append((line, r, need, r >= need))
    return out
