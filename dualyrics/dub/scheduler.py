"""When dubbed lines are made and played. Pure logic (no audio, no threads), so it is unit tested.

Lyrics and other timed lines (a song with synced lyrics): the clips of the next lines are made ahead of time
(``prefetch``), so a line's speech starts when the line starts (``due``). A clip that would not fit before the
next line is spoken faster, up to ``MAX_BOOST`` (``fit_rate``). A line whose time passed more than ``LATE_OK``
ago is skipped rather than spoken late. When a line is due while the previous clip still plays, the clip
may run on for ``OVERRUN_OK`` seconds; after that it is cut, so speech stays in sync with the song.

Captions (Live mode, on-screen subtitles): lines come as they are recognised or read. Live captions grow
while fragments of a sentence are merged, so a caption is spoken once it has stayed the same for
``STABLE_SEC`` or a newer caption came (``CaptionQueue``). Clips older than ``MAX_LAG`` are dropped and at
most ``BACKLOG`` wait, so speech never falls far behind the picture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

LATE_OK = 1.2         # s: a timed line may start this late, else it is skipped
OVERRUN_OK = 0.6      # s: the previous clip may run this far into the next line before it is cut
MAX_BOOST = 1.35      # speak at most 35 % faster to fit a short line
AHEAD_LINES = 4       # clips made ahead (timed lines)
AHEAD_SEC = 25.0      # ... but not further ahead than this
STABLE_SEC = 0.7      # a caption is spoken after it stopped changing for this long
MAX_LAG = 6.0         # s: a caption clip older than this is dropped
BACKLOG = 2           # captions waiting to be spoken at most

# rough speaking rates (characters per second at normal speed), to fit clips into lines before they exist
_CPS = {"fa": 13.0, "ar": 13.0, "ur": 13.0, "he": 13.0, "zh": 5.5, "ja": 7.0, "ko": 7.5, "th": 10.0, "hi": 13.0}
DEFAULT_CPS = 15.0


def chars_per_sec(lang: str) -> float:
    return _CPS.get((lang or "").split("-")[0].lower(), DEFAULT_CPS)


def estimate_sec(text: str, lang: str = "", rate: float = 1.0) -> float:
    """Rough length of the spoken ``text`` (s) at ``rate`` (1 = normal)."""
    return len((text or "").strip()) / chars_per_sec(lang) / max(0.25, rate) + 0.25


def fit_rate(text: str, lang: str, slot: float, base: float = 1.0, max_boost: float = MAX_BOOST) -> float:
    """Speaking rate for a line that has ``slot`` seconds until the next one: the user's ``base`` rate, faster
    (at most ``base * max_boost``) when the clip would not fit."""
    if slot <= 0:
        return base
    est = estimate_sec(text, lang, base)
    if est <= slot:
        return base
    return round(min(base * max_boost, base * est / slot), 2)


@dataclass(frozen=True)
class Line:
    key: str            # unique per song and line (and target language)
    text: str           # what to say (the translation)
    start: float        # media time (s)
    end: float          # start of the next line (or start + a few seconds)

    @property
    def slot(self) -> float:
        return max(0.0, self.end - self.start)


def prefetch(lines: Iterable[Line], pos: float, have: set[str], busy: set[str], ahead_lines: int = AHEAD_LINES,
             ahead_sec: float = AHEAD_SEC, late_ok: float = LATE_OK) -> list[Line]:
    """Lines whose clip should be made now, soonest first: the current line while it can still start in time
    and the next ``ahead_lines`` lines within ``ahead_sec``, minus clips that exist or are being made."""
    out: list[Line] = []
    upcoming = 0
    for ln in lines:
        if not ln.text.strip():
            continue
        if ln.start < pos:
            if pos - ln.start > late_ok or ln.end <= pos:
                continue        # too late to start it
        elif ln.start - pos > ahead_sec or upcoming >= ahead_lines:
            break
        else:
            upcoming += 1
        if ln.key not in have and ln.key not in busy:
            out.append(ln)
    return out


def due(lines: Iterable[Line], pos: float, played: set[str], late_ok: float = LATE_OK) -> Line | None:
    """The line to start speaking at ``pos``: the line playing now, if it was not spoken yet and is at most
    ``late_ok`` seconds late."""
    current: Line | None = None
    for ln in lines:
        if ln.start <= pos < max(ln.end, ln.start + 0.01):
            current = ln
    if current is None or current.key in played or not current.text.strip():
        return None
    return current if pos - current.start <= late_ok else None


def on_overlap(remaining: float, overrun_ok: float = OVERRUN_OK) -> str:
    """A line is due while the previous clip still plays ``remaining`` seconds: ``wait`` or ``cut`` it."""
    return "wait" if remaining <= overrun_ok else "cut"


def stale(lines: Iterable[Line], pos: float, keep: Iterable[str], late_ok: float = LATE_OK) -> set[str]:
    """Clip keys that can be forgotten: lines already over (after a seek forwards too)."""
    alive = {ln.key for ln in lines if ln.end > pos - late_ok}
    return {k for k in keep if k not in alive}


# ---------------------------------------------------------------- captions
@dataclass
class Caption:
    key: str
    text: str
    changed_at: float
    queued_at: float
    superseded: bool = False


@dataclass
class CaptionQueue:
    """Captions to speak, in order. ``push`` the current caption every tick; ``ready`` gives the next one to
    make a clip for; ``take`` returns a made clip's caption when it is still worth playing."""
    stable_sec: float = STABLE_SEC
    max_lag: float = MAX_LAG
    backlog: int = BACKLOG
    items: list[Caption] = field(default_factory=list)
    spoken: dict[str, str] = field(default_factory=dict)     # key -> text that was sent to the voice

    def push(self, key: str, text: str, now: float) -> None:
        text = (text or "").strip()
        if not key or not text:
            return
        if self.spoken.get(key) is not None:
            return                       # already spoken (a later revision is not spoken again)
        for c in self.items:
            if c.key == key:
                if c.text != text:
                    c.text, c.changed_at = text, now
                return
        for c in self.items:
            c.superseded = True          # a newer caption came: the older ones will not change any more
        self.items.append(Caption(key, text, now, now))
        self._trim(now)

    def _trim(self, now: float) -> None:
        self.items = [c for c in self.items if now - c.queued_at <= self.max_lag]
        while len(self.items) > self.backlog:
            dropped = self.items.pop(0)
            self.spoken[dropped.key] = ""    # dropped: never spoken later
        if len(self.spoken) > 500:
            for k in list(self.spoken)[:250]:
                del self.spoken[k]

    def ready(self, now: float) -> Caption | None:
        """The oldest caption that stopped changing (or was followed by a newer one): make its clip now. It
        leaves the queue and counts as spoken."""
        self._trim(now)
        for c in self.items:
            if c.superseded or now - c.changed_at >= self.stable_sec:
                self.items.remove(c)
                self.spoken[c.key] = c.text
                return c
        return None

    def fresh(self, queued_at: float, now: float) -> bool:
        """A clip made for a caption queued at ``queued_at`` is still worth playing at ``now``."""
        return now - queued_at <= self.max_lag

    def clear(self) -> None:
        self.items.clear()
