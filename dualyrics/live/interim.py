"""Interim commits: captions for long speech before the speaker pauses.

While someone keeps talking, the piece buffered so far is transcribed every ~2 s (once at least 1.5 s are
buffered). Every complete sentence but the last is committed (captioned and translated at once) and its audio
is trimmed from the front of the buffer; the last, unfinished sentence is shown as a dim preview line until
it is confirmed. When the piece finally ends, its rest is transcribed as usual and whatever repeats the end of
the committed text is removed.

The rules follow ``_do_interim_asr`` and ``_strip_committed_overlap`` in TheDeathDragon/LiveTranslate (MIT);
see docs/ATTRIBUTION_NOTES.md. Everything here is pure text and arithmetic, so it is unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

INTERVAL = 2.0          # seconds of new audio between interim passes
MIN_BUFFER = 1.5        # an interim pass needs at least this much audio
COOLDOWN = 1.0          # wall seconds between interim passes at least
MARGIN = 0.3            # trim a little more than the committed share, so its last word is not heard again
KEEP = 0.5              # never trim below this much audio (the unfinished sentence)
TAIL_CHARS = 50         # the end of the committed text kept for removing echo
SHORT_ALNUM = 8         # a "sentence" with at most this many letters/digits is merged into the next one

TERMINATORS = ".!?…。！？؟।۔"
_NO_SPACE = "。！？"     # CJK terminators need no space after them
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "jr", "sr", "prof", "mt", "no", "etc", "eg", "ie", "approx"}


@dataclass
class Plan:
    complete: list[str]          # sentences to commit now
    rest: str                    # the unfinished last sentence (preview)
    committed: str               # the committed text as one string (for the echo tail and the trim)
    full: str                    # the whole (overlap-stripped) transcription


def _alnum(text: str) -> int:
    return sum(1 for c in text if c.isalnum())


def _is_abbrev(text: str, dot: int) -> bool:
    """The '.' at ``dot`` belongs to an abbreviation, an initial or a number (Mr., U.S., 3.5)."""
    if text[dot] != ".":
        return False
    nxt = text[dot + 1:dot + 2]
    if nxt.isdigit():
        return True
    m = re.search(r"(\w+)$", text[:dot])
    if not m:
        return False
    word = m.group(1)
    return word.lower() in _ABBREV or (len(word) == 1 and word.isalpha())


def split_sentences(text: str, merge_short: bool = True) -> list[str]:
    """Sentences of ``text`` (terminators kept). A very short sentence ("Oh." "Yes!") is merged into the next
    unless ``merge_short`` is off."""
    text = " ".join((text or "").split())
    parts: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in TERMINATORS:
            j = i
            while j + 1 < n and (text[j + 1] in TERMINATORS or text[j + 1] in "\"'”»)]"):
                j += 1
            end_ok = j + 1 >= n or text[j + 1] == " " or ch in _NO_SPACE
            if end_ok and not _is_abbrev(text, i):
                piece = text[start:j + 1].strip()
                if piece:
                    parts.append(piece)
                start = j + 1
            i = j + 1
            continue
        i += 1
    rest = text[start:].strip()
    if rest:
        parts.append(rest)
    if not merge_short:
        return parts
    merged: list[str] = []
    carry = ""
    for i, p in enumerate(parts):
        p = f"{carry} {p}".strip() if carry else p
        if _alnum(p) <= SHORT_ALNUM and i < len(parts) - 1:
            carry = p
            continue
        carry = ""
        merged.append(p)
    if carry:
        merged.append(carry)
    return merged


def strip_committed_overlap(text: str, tail: str) -> str:
    """Remove the start of ``text`` that repeats the end of the committed ``tail`` (case-insensitive)."""
    if not tail:
        return text
    t = tail.lower().rstrip()
    low = text.lower()
    for k in range(min(len(t), len(low)), 2, -1):
        if low[:k] == t[-k:]:
            return text[k:].strip()
    return text


def plan(text: str, tail: str = "") -> Plan | None:
    """What to commit from one interim transcription, or None when there is no complete sentence yet."""
    full = strip_committed_overlap(" ".join((text or "").split()), tail)
    if not full or not any(c.isalnum() for c in full):
        return None
    sents = split_sentences(full)
    if len(sents) <= 1:
        return Plan([], full, "", full)
    complete = sents[:-1]
    return Plan(complete, sents[-1], " ".join(complete), full)


def trim_seconds(committed_chars: int, total_chars: int, duration: float, margin: float = MARGIN,
                 keep: float = KEEP) -> float:
    """Audio to drop from the front: the committed share of the text, plus a margin, keeping ``keep`` seconds."""
    if committed_chars <= 0 or duration <= 0:
        return 0.0
    trim = committed_chars / max(total_chars, 1) * duration + margin
    trim = min(trim, max(duration - keep, 0.0))
    if 0 < trim < margin:
        trim = min(margin, duration / 2)
    return max(0.0, trim)


def due(buffered: float, at_last: float, now: float, last_check: float, interval: float = INTERVAL,
        min_buffer: float = MIN_BUFFER, cooldown: float = COOLDOWN) -> bool:
    """An interim pass is due: enough audio, ``interval`` seconds of it new since the last pass, not too soon."""
    return (buffered >= min_buffer and buffered - at_last >= interval and now - last_check >= cooldown)


def new_tail(committed: str) -> str:
    return committed[-TAIL_CHARS:]
