"""Pure logic of the on-screen subtitle source: change detection, text cleaning, debounce and dedupe.

Nothing here touches Windows, OCR or UI Automation, so it is unit-tested with fake frames and texts.

- ``ChangeDetector`` decides whether a new frame of the subtitle region is worth an OCR pass: subtitles are
  bright (white or yellow) text, so frames are compared through a small mask of their bright pixels. A moving
  film behind the subtitle barely changes that mask; a new subtitle line changes it a lot.
- ``SubtitleTracker`` turns the stream of recognised texts into subtitle events: a text must be read the same
  way on consecutive frames before it shows (OCR of a line that is fading in is noisy), it disappears only
  after it is gone on consecutive frames, a text that stays for a very long time is a label of the app (not a
  subtitle) and is ignored, and the same line read again with small OCR differences is not a new caption.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------- change detection
MASK_SIZE = (160, 40)      # the region is reduced to this before comparing
BRIGHT = 185               # luminance of "subtitle" pixels (white 255, yellow ~226, light grey ~200)
CHANGE = 0.006             # fraction of mask pixels that must differ to count as a change
MAX_INTERVAL = 1.5         # OCR at least this often even when nothing seems to change (seconds)


def signature(img: Any, size: tuple[int, int] = MASK_SIZE, bright: int = BRIGHT) -> bytes:
    """Bright-pixel mask of a PIL image (one byte per pixel: 0 or 1)."""
    g = img.convert("L").resize(size)
    return bytes(1 if v >= bright else 0 for v in g.tobytes())


def mask_diff(a: bytes, b: bytes) -> float:
    """Fraction of differing pixels between two masks of the same size (1.0 when sizes differ)."""
    if len(a) != len(b) or not a:
        return 1.0
    return sum(x != y for x, y in zip(a, b)) / len(a)


class ChangeDetector:
    def __init__(self, threshold: float = CHANGE, max_interval: float = MAX_INTERVAL,
                 size: tuple[int, int] = MASK_SIZE, bright: int = BRIGHT) -> None:
        self.threshold = threshold
        self.max_interval = max_interval
        self.size, self.bright = size, bright
        self._last: bytes | None = None
        self._at = -1e9

    def check(self, img: Any, now: float) -> bool:
        """True when this frame should be read (first frame, a real change, or the safety interval)."""
        sig = signature(img, self.size, self.bright)
        return self.check_signature(sig, now)

    def check_signature(self, sig: bytes, now: float) -> bool:
        if self._last is None or mask_diff(sig, self._last) > self.threshold or now - self._at >= self.max_interval:
            self._last, self._at = sig, now
            return True
        return False

    def reset(self) -> None:
        self._last, self._at = None, -1e9


# ---------------------------------------------------------------- text
_TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
_WS = re.compile(r"\s+")
_EDGE_JUNK = re.compile(r"^[\s|\\/_~`'\".,:;=+*#<>^-]+|[\s|\\/_~`=+*#<>^-]+$")


def letters(text: str) -> int:
    return sum(1 for ch in text if ch.isalpha())


def clean_text(text: str) -> str:
    """One line, single spaces, OCR edge noise removed; '' when it does not look like a subtitle (fewer than
    two letters, or player chrome such as '1:23 / 4:56')."""
    t = unicodedata.normalize("NFC", text or "")
    t = _WS.sub(" ", t).strip()
    t = _EDGE_JUNK.sub("", t).strip()
    if letters(t) < 2:
        return ""
    rest = _TIME.sub("", t)
    if letters(rest) < 2:
        return ""
    return t


def norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in t)
    return " ".join(t.split())


def similar(a: str, b: str, ratio: float = 0.82) -> bool:
    """Same subtitle line read twice (OCR may change a letter or two)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return na == nb
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb, autojunk=False).ratio() >= ratio


def grows(old: str, new: str, share: float = 0.7) -> bool:
    """``new`` is a longer reading of the same subtitle (it faded in, or a second line appeared): most words of
    ``old`` are in ``new``."""
    ow, nw = norm(old).split(), set(norm(new).split())
    return bool(ow) and len(norm(new)) > len(norm(old)) and sum(w in nw for w in ow) >= share * len(ow)


def ui_label(line: str) -> bool:
    """A line that looks like part of the app's interface, not dialogue: short ALL-CAPS button legends and
    titles ('ACTIVE MARKERS', 'MOVE  ZOOM', 'THE PATH'), or mostly symbols ('@ (X) [B]')."""
    t = clean_text(line)
    if not t:
        return True
    alpha = [ch for ch in t if ch.isalpha()]
    if len(alpha) < 0.5 * len(t.replace(" ", "")):
        return True
    cased = [ch for ch in alpha if ch.isupper() or ch.islower()]
    words = [w for w in t.split() if any(ch.isalpha() for ch in w)]
    return bool(cased) and all(ch.isupper() for ch in cased) and len(words) <= 4


_SHORT_OK = {"a", "i", "o", "y", "e", "u", "à", "è", "é", "ó", "и", "в", "с", "у", "о", "я", "а", "к"}


def garbage(line: str) -> bool:
    """OCR of a script the recogniser cannot read (or of a busy picture) gives tokens like 'æ-j9i:l' or
    '14-i91': a line where fewer than 60 % of the words look like words is noise."""
    tokens = [w for w in (line or "").split() if any(ch.isalnum() for ch in w)]
    if not tokens:
        return True
    good = 0
    for w in tokens:
        # punctuation and invisible joiners (Persian ZWNJ) aside, a word is letters only
        core = "".join(ch for ch in w if not unicodedata.category(ch).startswith(("P", "Cf")))
        if core.isalpha() and (len(core) > 1 or core.lower() in _SHORT_OK):
            good += 1
    return good < 0.6 * len(tokens) or sum(ch.isalpha() for ch in line) < 3


class StaticLines:
    """Removes OCR lines that stay on screen for a long time (HUD legends, channel logos, menu titles).

    Each line is followed while it stays visible; after ``static_after`` seconds it counts as part of the
    app and is dropped until it has been gone for ``forget_after`` seconds. Subtitle lines change every few
    seconds and are never affected.
    """

    def __init__(self, static_after: float = 10.0, forget_after: float = 30.0, drop_labels: bool = True) -> None:
        self.static_after = static_after
        self.forget_after = forget_after
        self.drop_labels = drop_labels
        self._seen: dict[str, list[float]] = {}     # norm(line) -> [first seen, last seen]

    def reset(self) -> None:
        self._seen.clear()

    def feed(self, lines: list[str], now: float) -> list[str]:
        out = []
        present = set()
        for line in lines:
            if self.drop_labels and (ui_label(line) or garbage(line)):
                continue
            key = next((k for k in self._seen if similar(k, line)), None) or norm(line)
            if not key:
                continue
            present.add(key)
            entry = self._seen.get(key)
            if entry is None:
                entry = self._seen[key] = [now, now]
            else:
                static = entry[1] - entry[0] >= self.static_after
                if now - entry[1] > (self.forget_after if static else 1.5):
                    entry[0] = now          # gone long enough: follow it again from now
            entry[1] = now
            if now - entry[0] < self.static_after:
                out.append(clean_text(line))
        for k in [k for k, (_f, last) in self._seen.items() if k not in present and now - last > self.forget_after]:
            del self._seen[k]
        return out


# ---------------------------------------------------------------- debounce, dedupe, activity
@dataclass(frozen=True)
class Event:
    kind: str          # "show" (a new line), "update" (a more complete reading of the line on screen),
    #                    "resume" (the previous line again after a flicker), "clear"
    text: str = ""


class SubtitleTracker:
    """Feed every reading of the region (``feed(text, now)``); get subtitle events back.

    ``active`` tells whether the app really shows subtitles: two different lines within ``activate_window``
    seconds (a single text could be a title or a label), or one line from a trusted reader (UI Automation
    caption elements). Once active, it stays active for ``rearm`` seconds, so a pause in the dialogue does
    not lose the next first line.
    """

    def __init__(self, stable: int = 2, clear: int = 2, static_after: float = 15.0, dedupe_window: float = 10.0,
                 activate_window: float = 25.0, hold: float = 8.0, rearm: float = 90.0) -> None:
        self.stable = max(1, stable)
        self.clear_frames = max(1, clear)
        self.static_after = static_after
        self.dedupe_window = dedupe_window
        self.activate_window = activate_window
        self.hold = hold                 # "has text" this long after the last line (gaps between lines)
        self.rearm = rearm
        self.current = ""                # line on screen now ('' = none)
        self.current_since = 0.0
        self._cand = ""
        self._cand_n = 0
        self._static: deque[str] = deque(maxlen=20)
        self._recent: deque[tuple[float, str]] = deque(maxlen=30)   # (time, text) of lines shown
        self._last_text_at = -1e9
        self._active_at = -1e9
        self._last_shown = ("", -1e9)    # (text, time it was cleared) for "resume"

    # -- state
    def active(self, now: float) -> bool:
        return now - self._active_at <= self.rearm

    def has_text(self, now: float) -> bool:
        """For the decision: subtitles are on screen now, or were a moment ago (between two lines)."""
        return self.active(now) and (bool(self.current) or now - self._last_text_at <= self.hold)

    def reset(self) -> None:
        self.current, self._cand, self._cand_n = "", "", 0
        self._recent.clear()
        self._static.clear()
        self._last_text_at = self._active_at = -1e9
        self._last_shown = ("", -1e9)

    def _is_static(self, text: str) -> bool:
        return any(similar(text, s) for s in self._static)

    # -- input
    def feed(self, raw: str, now: float, trusted: bool = False) -> Event | None:
        text = clean_text(raw)
        if text and self._is_static(text):
            text = ""
        if similar(text, self._cand) if (text and self._cand) else text == self._cand:
            self._cand_n += 1
            if len(text) > len(self._cand):
                self._cand = text           # keep the most complete reading
        else:
            self._cand, self._cand_n = text, 1
        need = self.stable if text else self.clear_frames
        if trusted:
            need = 1
        if self._cand_n < need:
            return None
        cand = self._cand
        if not cand:
            if not self.current:
                return None
            self._last_shown = (self.current, now)
            self.current = ""
            return Event("clear")
        if self.current and similar(cand, self.current):
            if len(cand) > len(self.current):
                self.current = cand
            self._last_text_at = now
            if now - self.current_since >= self.static_after and not trusted:
                # the same text for a very long time: a label of the app, not a subtitle
                self._static.append(self.current)
                self.current = ""
                return Event("clear")
            return None
        if self.current and grows(self.current, cand):
            self.current, self._last_text_at = cand, now
            return Event("update", cand)
        # a new line
        self.current, self.current_since, self._last_text_at = cand, now, now
        prev, cleared_at = self._last_shown
        if prev and similar(cand, prev) and now - cleared_at <= 1.5:
            return Event("resume", cand)    # the same line flickered off and on
        if not any(similar(cand, t) for at, t in self._recent if now - at <= self.dedupe_window):
            self._recent.append((now, cand))
        distinct = {norm(t) for at, t in self._recent if now - at <= self.activate_window}
        if trusted or len(distinct) >= 2 or self.active(now):
            self._active_at = now
        return Event("show", cand)
