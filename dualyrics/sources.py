"""Smart source priority: which source gives the lines for what is in front of the user.

Three sources, tried in the user's order (Settings > Sources, globally or per app profile):

- ``lyrics``: synced lyrics for the media session (LRCLIB). For browsers the match must be strict (title,
  artist and duration), so an ordinary video never gets the lyrics of a song with a similar name.
- ``screen``: subtitles the app already shows: caption elements of a web page read through UI Automation
  (YouTube and other web players), else OCR of a subtitle region of the foreground window.
- ``audio``: Live mode (speech recognition of what the speakers play).

Everything here is pure (no Windows, no network) and unit-tested; ``automode.decide`` walks the order.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

LYRICS, SCREEN, AUDIO = "lyrics", "screen", "audio"
SOURCE_IDS = (LYRICS, SCREEN, AUDIO)
DEFAULT_ORDER = (LYRICS, SCREEN, AUDIO)
SCREEN_KINDS = ("video", "browser", "game")   # profile kinds whose foreground window may be read for subtitles

DEFAULT_OCR_REGION = (0.08, 0.66, 0.92, 0.97)  # x0, y0, x1, y1 as fractions of the window's client area
MIN_REGION = 0.02                              # smallest width / height of a region (fraction)

# class names (substrings) of caption elements in web players, read through UI Automation
DEFAULT_UIA_CLASSES = (
    "ytp-caption-segment", "caption-window",          # YouTube
    "player-timedtext",                               # Netflix
    "vjs-text-track",                                 # video.js players
    "shaka-text-container",                           # Shaka player
    "jw-text-track", "jw-captions",                   # JW Player
    "plyr__caption",                                  # Plyr
    "atvwebplayersdk-captions",                       # Prime Video
    "bmpui-ui-subtitle",                              # Bitmovin
    "dss-subtitle-renderer",                          # Disney+
    "vp-captions", "captions-text",                   # Vimeo and generic players
)


# ---------------------------------------------------------------- order and switches
def validate_order(v: Any, default: Iterable[str] = DEFAULT_ORDER) -> list[str]:
    """Known ids, each once, in the given order; missing ones are added at the end (in default order)."""
    out: list[str] = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, str) and x in SOURCE_IDS and x not in out:
            out.append(x)
    for x in default:
        if x not in out:
            out.append(x)
    return out


def validate_off(v: Any) -> list[str]:
    out: list[str] = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, str) and x in SOURCE_IDS and x not in out:
            out.append(x)
    return out


def enabled_sources(order: Iterable[str], off: Iterable[str]) -> tuple[str, ...]:
    off_set = set(off)
    return tuple(s for s in validate_order(list(order)) if s not in off_set)


def effective_sources(cfg: dict[str, Any], profile: dict[str, Any] | None = None) -> tuple[str, ...]:
    """The enabled sources in priority order: the profile's own list when it has one, else the global one."""
    if profile and profile.get("sources_custom"):
        return enabled_sources(profile.get("source_order") or DEFAULT_ORDER, profile.get("sources_off") or ())
    return enabled_sources(cfg.get("source_order") or DEFAULT_ORDER, cfg.get("sources_off") or ())


def move(order: list[str], sid: str, delta: int) -> list[str]:
    """``order`` with ``sid`` moved up (-1) or down (+1); unchanged at the ends."""
    out = validate_order(order)
    if sid not in out:
        return out
    i = out.index(sid)
    j = max(0, min(len(out) - 1, i + delta))
    out.insert(j, out.pop(i))
    return out


# ---------------------------------------------------------------- OCR region
def validate_region(v: Any, default: Any = DEFAULT_OCR_REGION) -> list[float]:
    """[x0, y0, x1, y1] fractions in 0..1, ordered, at least MIN_REGION wide and high; else ``default``
    (``default`` may be ``()`` to mean "no region": then an empty list comes back)."""
    try:
        vals = [float(x) for x in v]  # type: ignore[union-attr]
        if len(vals) != 4 or any(x != x for x in vals):
            raise ValueError
    except (TypeError, ValueError):
        return [float(x) for x in default]
    x0, y0, x1, y1 = (min(1.0, max(0.0, x)) for x in vals)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if x1 - x0 < MIN_REGION or y1 - y0 < MIN_REGION:
        return [float(x) for x in default]
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def region_box(client: tuple[int, int, int, int], region: Iterable[float]) -> tuple[int, int, int, int]:
    """Screen box (left, top, right, bottom) of ``region`` inside the client rectangle ``client``."""
    left, top, right, bottom = client
    w, h = max(1, right - left), max(1, bottom - top)
    x0, y0, x1, y1 = validate_region(list(region))
    return (left + int(round(x0 * w)), top + int(round(y0 * h)),
            left + max(1, int(round(x1 * w))), top + max(1, int(round(y1 * h))))


def region_of(cfg: dict[str, Any], profile: dict[str, Any] | None = None) -> list[float]:
    if profile and profile.get("ocr_region"):
        return validate_region(profile["ocr_region"], cfg.get("ocr_region") or DEFAULT_OCR_REGION)
    return validate_region(cfg.get("ocr_region") or DEFAULT_OCR_REGION)


def ocr_allowed(profile: dict[str, Any], fullscreen: bool) -> bool:
    """May OCR read this window? A browser in a window shows a web page: the bottom of its window holds page
    text (comments, side panels, titles), not a video, and OCR of it gave "subtitles" that pushed Live aside.
    So a browser is read with OCR only while it fills the screen (a fullscreen video) or when the user drew a
    subtitle area for it; otherwise only its caption elements (UI Automation) count. Other kinds always."""
    if profile.get("kind") != "browser":
        return True
    return bool(fullscreen or profile.get("ocr_region"))


def screen_allowed(profile: dict[str, Any], via: str) -> bool:
    """May the foreground window be read for subtitles? Only when the matched profile is the app in front
    (never a background player while another app is in front), and only for video players, browsers,
    games and fullscreen apps, or an app whose profile names its own sources or subtitle region."""
    if via not in ("foreground", "fullscreen"):
        return False
    if via == "fullscreen" or profile.get("kind") in SCREEN_KINDS:
        return True
    return bool(profile.get("ocr_region") or profile.get("sources_custom"))


# ---------------------------------------------------------------- strict lyrics match (browsers)
_PUNCT = re.compile(r"[^\w\s]+", re.U)


def norm(text: str) -> str:
    """Lower case, accents and punctuation removed, '&' as 'and', single spaces."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower().replace("&", " and ")
    t = t.replace("’", "'").replace("'", "")
    return " ".join(_PUNCT.sub(" ", t).split())


def _tokens(text: str) -> set[str]:
    return set(norm(text).split())


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def title_matches(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or _jaccard(set(na.split()), set(nb.split())) >= 0.8


def artist_matches(a: str, b: str) -> bool:
    """The candidate artist is one of the result's artists (LRCLIB may list 'A & B' or 'A, B')."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb or re.search(rf"(^| ){re.escape(na)}( |$)", nb) or re.search(rf"(^| ){re.escape(nb)}( |$)", na):
        return True
    return _jaccard(set(na.split()), set(nb.split())) >= 0.6


def strict_match(title: str, artist: str, duration: float, res_title: str, res_artist: str,
                 res_duration: float, tolerance: float = 4.0) -> bool:
    """Title, artist and duration all agree. A video title like 'Artist - Song (Official Video)' with the
    channel as artist is tried as (Song, Artist) too. Without a known duration on both sides: no match."""
    if not (duration and duration > 0 and res_duration and res_duration > 0):
        return False
    if abs(float(duration) - float(res_duration)) > tolerance:
        return False
    from dualyrics.lyrics import candidates  # local import: lyrics imports requests

    pairs = [(title, artist)] + candidates(title, artist)
    return any(title_matches(t, res_title) and artist_matches(a, res_artist) for t, a in pairs)


def strict_accept(title: str, artist: str, duration: float, tolerance: float = 4.0):  # noqa: ANN201
    """A filter for ``LyricsClient.fetch(accept=...)``: LRCLIB answers that strictly match this track."""
    def accept(j: dict[str, Any]) -> bool:
        try:
            d = float(j.get("duration") or 0)
        except (TypeError, ValueError):
            d = 0.0
        return strict_match(title, artist, duration, str(j.get("trackName") or j.get("name") or ""),
                            str(j.get("artistName") or ""), d, tolerance)
    return accept


def is_browser_media(profiles: Iterable[dict[str, Any]], media_app: str) -> bool:
    """The media session belongs to a web browser (a profile of kind 'browser' claims its app id)."""
    m = (media_app or "").lower()
    if not m:
        return False
    return any(p.get("kind") == "browser" and any(tok and tok in m for tok in p.get("media_ids", ()))
               for p in profiles)
