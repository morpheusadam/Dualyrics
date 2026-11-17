"""Near-duplicate readings of OCR and UI Automation text, so the same subtitle read again with small differences
neither flickers nor costs a second translation.

Port of the rules of ``TextResultCacheService`` and ``StringExtensions`` (Jaro and Sørensen-Dice similarity) of
Translumo (https://github.com/ramjke/Translumo, Apache-2.0, ``src/Translumo.Processing/TextProcessing/
TextResultCacheService.cs`` and ``src/Translumo.Utils/Extensions/StringExtensions.cs``):

- the similarity of two readings is the mean of their Dice coefficient and their Jaro similarity (Dice twice when
  Jaro is 0);
- a reading is *cached* when the same text (ignoring case) was read in the last ``lifetime`` seconds (the cache
  empties itself when nothing was read for that long), or when a similar reading (similarity above 0.65, 0.55 for
  Chinese, Japanese, Korean and Thai) with a score at least as good is in the cache; a reading from a trusted
  reader (UI Automation, score ``TRUSTED``) only covers an OCR reading when they are very similar (above 0.9);
- readings of one pass are added at its end (``end_iteration``), keeping the best score of each text; a reading
  that is like nothing in the cache starts a new *iteration* (a new subtitle);
- a translation is a repeat when a translation of the same iteration is more than 0.955 Jaro-similar to it.

Scores: Translumo scores OCR results with a model; here an OCR reading scores its number of letters (a more
complete reading of a line wins) and a UI Automation reading scores ``TRUSTED``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Callable

SIMILARITY_THRESHOLD = 0.65
LOWER_SIMILARITY_THRESHOLD = 0.55       # Asian scripts: OCR differences weigh more in short strings
UPPER_SIMILARITY_THRESHOLD = 0.9
TRANSLATED_SIMILARITY_THRESHOLD = 0.955
LIFETIME = 3.0
CAPACITY = 20
TRANSLATED_CAPACITY = 6
TRUSTED = float("inf")
_MARK = "ʔ"                         # 'ʔ': marks a matched character (as upstream)


def _common(first: str, second: str, sep: int) -> list[str]:
    out: list[str] = []
    other = list(second)
    for i, ch in enumerate(first):
        c = ch.lower()
        for j in range(max(0, i - sep), min(i + sep, len(second))):
            if other[j].lower() == c:
                out.append(c)
                other[j] = _MARK
                break
    return out


def jaro(a: str, b: str) -> float:
    """Jaro similarity 0..1 (case-insensitive), as Translumo computes it."""
    if not b or not a:
        return 0.0
    sep = min(len(a), len(b)) // 2 + 1
    c1 = _common(a, b, sep)
    n = len(c1)
    if n == 0:
        return 0.0
    c2 = _common(b, a, sep)
    if n != len(c2):
        return 0.0
    transpositions = sum(1 for x, y in zip(c1, c2) if x != y) // 2
    return n / (3.0 * len(a)) + n / (3.0 * len(b)) + (n - transpositions) / (3.0 * n)


def dice(a: str, b: str) -> float:
    """Sørensen-Dice coefficient of the character bigrams (sets), 0..1."""
    if not a or not b:
        return 0.0
    b1 = {a[i:i + 2] for i in range(len(a) - 1)}
    b2 = {b[i:i + 2] for i in range(len(b) - 1)}
    total = len(b1) + len(b2)
    if total == 0:
        return 0.0
    return 2 * sum(1 for x in b2 if x in b1) / total


def similarity(a: str, b: str) -> float:
    j, d = jaro(a, b), dice(a, b)
    return ((d if j == 0 else j) + d) / 2.0


def same_translation(old: str, new: str) -> bool:
    """A new translation that only differs from the shown one in a character or two (keep the shown one)."""
    return bool(old) and bool(new) and jaro(old, new) > TRANSLATED_SIMILARITY_THRESHOLD


def letters(text: str) -> float:
    return float(sum(1 for ch in text if ch.isalnum()))


_ASIAN = ((0x3040, 0x30FF), (0x3400, 0x9FFF), (0xAC00, 0xD7AF), (0x0E00, 0x0E7F))


def is_asian(text: str) -> bool:
    n = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _ASIAN))
    return n * 2 >= max(1, sum(1 for ch in text if ch.isalpha()))


class TextResultCache:
    """Thread-safe. ``clock`` in seconds (monotonic)."""

    def __init__(self, lifetime: float = LIFETIME, capacity: int = CAPACITY,
                 translated_capacity: int = TRANSLATED_CAPACITY, clock: Callable[[], float] = time.monotonic) -> None:
        self.lifetime = lifetime
        self.capacity = capacity
        self.clock = clock
        self._lock = threading.Lock()
        self._texts: OrderedDict[str, tuple[str, float]] = OrderedDict()   # lower-case -> (text, best score)
        self._current: list[tuple[str, float]] = []
        self._translated: deque[tuple[str, int]] = deque(maxlen=translated_capacity)
        self._cached_at: float | None = None
        self._iteration = 0

    @property
    def iteration(self) -> int:
        return self._iteration

    def is_cached(self, text: str, score: float, sequential: bool = False,
                  asian: bool | None = None) -> tuple[bool, int]:
        """(this reading repeats one in the cache, its iteration). ``sequential`` readings (a text that is
        known to change step by step) are never covered by a similar one."""
        if asian is None:
            asian = is_asian(text)
        with self._lock:
            if self._cached_internal(text):
                return True, self._iteration
            self._current.append((text, score))
            better = self._has_better_similar(text, score, asian)
            return better and not sequential, self._iteration

    def is_translated_cached(self, text: str, iteration: int) -> bool:
        with self._lock:
            has = any(it == iteration and jaro(cached, text) > TRANSLATED_SIMILARITY_THRESHOLD
                      for cached, it in self._translated)
            if not has:
                self._translated.append((text, iteration))
            return has

    def end_iteration(self) -> None:
        with self._lock:
            for text, score in self._current:
                k = text.lower()
                old = self._texts.get(k)
                if old is None or score > old[1]:
                    self._texts[k] = (text, score)
                    if old is None:
                        while len(self._texts) > self.capacity:
                            self._texts.popitem(last=False)
            self._current.clear()

    def reset(self) -> None:
        with self._lock:
            self._current.clear()
            self._texts.clear()
            self._translated.clear()
            self._iteration += 1

    def _cached_internal(self, text: str) -> bool:
        if not text:
            return False
        now = self.clock()
        try:
            if self._cached_at is not None and self._cached_at < now - self.lifetime:
                self._texts.clear()
            return text.lower() in self._texts
        finally:
            self._cached_at = now

    def _has_better_similar(self, text: str, score: float, asian: bool) -> bool:
        has_similar = False
        threshold = LOWER_SIMILARITY_THRESHOLD if asian else SIMILARITY_THRESHOLD
        for cached, cached_score in self._texts.values():
            sim = similarity(cached, text)
            if sim > threshold:
                if cached_score == TRUSTED:
                    if score != TRUSTED and sim > UPPER_SIMILARITY_THRESHOLD:
                        return True
                elif cached_score >= score:
                    return True
                has_similar = True
        if not has_similar:
            self._iteration += 1
        return False
