"""Pure text logic of Live mode: hallucination filter, dedupe, fragment merging, reading time, context.

Nothing here needs audio, Whisper or a network, so it is fully unit-tested.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass, field

# Whisper "hears" these in silence, music or applause (they come from subtitle credits in its training data)
JUNK = {
    "thanks for watching", "thank you for watching", "thanks for watching and see you next time",
    "thank you for watching and see you next time", "please subscribe", "subscribe", "like and subscribe",
    "please like and subscribe", "dont forget to like and subscribe", "see you in the next video",
    "music", "applause", "laughter", "silence", "sigh", "subtitles by the amara org community",
    "transcription by castingwords", "www mooji org", "uh", "um", "hmm", "mm", "mhm", "ah", "eh",
    "زیرنویس", "زیرنویس فارسی", "اشتركوا في القناة", "ご視聴ありがとうございました", "谢谢观看", "謝謝觀看",
    "продолжение следует",
}
# short phrases Whisper invents in silence that are also real speech: dropped unless the model was sure
SUSPECT = {
    "you", "thank you", "thanks", "thank you very much", "thank you so much", "bye", "bye bye", "goodbye",
    "see you next time", "so", "oh", "okay", "ok", "yeah", "the end", "copyright", "translated by",
    "ممنون", "مرسی", "خداحافظ", "با تشکر", "شکرا", "شكرا", "شكرا لكم", "vielen dank", "danke", "merci",
    "gracias", "obrigado", "grazie", "감사합니다", "спасибо",
}
SURE_LOGPROB, SURE_NO_SPEECH = -0.5, 0.3
_JUNK_PARTS = ("amara org", "subscribe to", "thanks for watching", "thank you for watching", "subtitles by",
               "captions by", "transcribed by", "зарегистрируйтесь", "dimatorzok")
SENTENCE_END = ".!?…。！？؟;"
_BRACKETED = re.compile(r"^[\s\[\(（【♪♫🎵🎶*~-]*(music|applause|laughter|laughs|silence|noise|sighs?|inaudible|"
                        r"background music|upbeat music|dramatic music|soft music|موسیقی|موزیک)[\s\]\)）】♪♫🎵🎶*~.-]*$",
                        re.I)
_ALL_BRACKETS = re.compile(r"^\s*([\[\(（【][^\]\)）】]*[\]\)）】]\s*)+$")

# confidence limits (faster-whisper segment fields)
MIN_AVG_LOGPROB = -1.0      # below this the words are guesses...
MAX_NO_SPEECH = 0.6         # ...and when Whisper also thinks there is no speech, drop it
VERY_LOW_LOGPROB = -1.6     # drop regardless of no_speech_prob
MAX_COMPRESSION = 2.4       # gzip ratio above this = repetition loop ("the the the the")


@dataclass
class Recognition:
    """One transcription result for one audio segment."""
    text: str
    start: float                 # wall-clock seconds when the audio segment started
    end: float                   # ... and ended
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    compression_ratio: float = 1.0
    language: str = ""           # spoken language (detected or set)
    pivot: bool = False          # ``text`` is the English pivot of speech in ``language`` (Whisper translate)
    alt: str = ""                # the original-language line, when it was written down too


@dataclass
class Caption:
    id: int
    text: str
    start: float
    end: float
    shown_at: float              # when it (last) changed on screen: reading time counts from here
    language: str = ""
    translation: str = ""
    trans_state: str = "pending"  # pending | done | skipped | failed
    version: int = 0             # bumps on every merge/update, so stale translations are ignored
    final: bool = False
    written: bool = False
    pivot: bool = False          # ``text`` is English (pivot); ``alt`` holds the original-language line
    alt: str = ""


def normalize(text: str) -> str:
    """Lower case, no punctuation or symbols, single spaces (for comparing recognitions)."""
    t = unicodedata.normalize("NFKC", text or "").lower().replace("’", "'").replace("'", "")
    t = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in t)
    return " ".join(t.split())


def clean(text: str) -> str:
    """Single spaces, no leading dash, no music notes (sung words are kept, the notes are noise)."""
    t = re.sub(r"[♪♫🎵🎶]+", " ", text or "")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[-–—\s]+", "", t)
    return t


def ends_sentence(text: str) -> bool:
    t = (text or "").rstrip(" \"'”»)]")
    return bool(t) and t[-1] in SENTENCE_END


def _repetitive(norm: str) -> bool:
    words = norm.split()
    if len(words) >= 6:
        uniq = len(set(words))
        if uniq <= 2 or uniq / len(words) < 0.3:
            return True
    return bool(re.search(r"(\b\w+\b)(?:\s+\1\b){4,}", norm))


def hallucination_reason(r: Recognition) -> str | None:
    """Why this recognition should be dropped, or None to keep it."""
    text = clean(r.text)
    norm = normalize(text)
    if not norm:
        return "symbols_only"           # "♪♪", "...", "*"
    if _BRACKETED.match(text) or _ALL_BRACKETS.match(text):
        return "sound_tag"              # [Music], (applause)
    if norm in JUNK:
        return "junk_phrase"
    if norm in SUSPECT and not (r.avg_logprob > SURE_LOGPROB and r.no_speech_prob < SURE_NO_SPEECH):
        return "junk_phrase"
    if any(p in norm for p in _JUNK_PARTS):
        return "junk_phrase"
    if r.avg_logprob < VERY_LOW_LOGPROB:
        return "low_confidence"
    if r.avg_logprob < MIN_AVG_LOGPROB and r.no_speech_prob > MAX_NO_SPEECH:
        return "low_confidence"
    if r.compression_ratio > MAX_COMPRESSION or _repetitive(norm):
        return "repetition"
    return None


def strip_overlap(prev: str, new: str, min_words: int = 2) -> str:
    """Remove the start of ``new`` that repeats the end of ``prev`` (the segmenter keeps a short tail of
    audio when it cuts long speech, so the next piece can start with the last words again)."""
    pw, nw = prev.split(), new.split()
    pn, nn = [normalize(w) for w in pw], [normalize(w) for w in nw]
    best = 0
    for k in range(min(len(pn), len(nn) - 1), min_words - 1, -1):
        if pn[-k:] == nn[:k]:
            best = k
            break
    return " ".join(nw[best:]) if best else new


class CaptionStream:
    """Turns recognitions into captions: filter, dedupe, merge fragments that end mid-sentence.

    A fragment is merged into the previous caption when that one has no sentence-ending punctuation, the
    pause between them is short, and the result stays readable (``max_chars``, ``max_span``), so a speaker
    who never pauses (or a model that leaves out punctuation) still gets new captions every few seconds.

    ``push`` returns ``("new", caption)``, ``("update", caption)`` or ``(None, reason)``.
    """

    def __init__(self, merge_gap: float = 1.2, max_chars: int = 140, dedupe_window: float = 8.0,
                 history: int = 50, max_span: float = 7.0) -> None:
        self.merge_gap = merge_gap
        self.max_chars = max_chars
        self.max_span = max_span          # a caption never grows beyond this many seconds of speech
        self.dedupe_window = dedupe_window
        self.captions: deque[Caption] = deque(maxlen=history)
        self._next_id = 1
        self.dropped: dict[str, int] = {}

    @property
    def last(self) -> Caption | None:
        return self.captions[-1] if self.captions else None

    def _drop(self, reason: str) -> tuple[None, str]:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1
        return None, reason

    def push(self, r: Recognition, now: float | None = None) -> tuple[str | None, Caption | str]:
        now = r.end if now is None else now
        why = hallucination_reason(r)
        if why:
            return self._drop(why)
        text = clean(r.text)
        last = self.last
        if last is not None and r.start - last.end <= self.dedupe_window:
            ln, nn = normalize(last.text), normalize(text)
            if nn == ln or (len(nn) >= 4 and nn in ln):
                last.end = max(last.end, r.end)
                return self._drop("duplicate")
            if ln and nn.startswith(ln) and not last.final:
                # Whisper heard the same words again plus more: the longer version replaces the shorter one
                cap = self._replace(last, text, r, now)
                cap.alt = r.alt or cap.alt
                return "update", cap
            trimmed = strip_overlap(last.text, text)
            if trimmed != text:
                text = trimmed
                if not normalize(text):
                    return self._drop("duplicate")
        if (last is not None and not last.final and not ends_sentence(last.text)
                and r.start - last.end <= self.merge_gap
                and r.end - last.start <= self.max_span
                and len(last.text) + 1 + len(text) <= self.max_chars):
            cap = self._replace(last, f"{last.text} {text}", r, now)
            cap.alt = f"{cap.alt} {r.alt}".strip() if r.alt else cap.alt
            return "update", cap
        if last is not None:
            last.final = True
        cap = Caption(self._next_id, text, r.start, r.end, now, language=r.language, pivot=r.pivot, alt=r.alt)
        self._next_id += 1
        self.captions.append(cap)
        return "new", cap

    def revise(self, cap: Caption, r: Recognition, now: float | None = None) -> Caption | None:
        """The sentence of ``cap`` grew or was corrected (Windows Live Captions updates text in place).
        Returns the caption, or None when the words did not change."""
        text = clean(r.text)
        if not normalize(text):
            return None
        if normalize(text) == normalize(cap.text):
            cap.text = text                # punctuation only: shown, not translated again
            cap.end = max(cap.end, r.end)
            return None
        return self._replace(cap, text, r, r.end if now is None else now)

    @staticmethod
    def _replace(cap: Caption, text: str, r: Recognition, now: float) -> Caption:
        cap.text = text
        cap.end = max(cap.end, r.end)
        cap.shown_at = now
        cap.translation, cap.trans_state = "", "pending"
        cap.version += 1
        cap.language = cap.language or r.language
        cap.pivot = cap.pivot or r.pivot
        return cap

    def finalize_all(self) -> list[Caption]:
        for c in self.captions:
            c.final = True
        return list(self.captions)


# ---------------------------------------------------------------- reading time
def display_duration(original: str, translation: str = "", cps: float = 17.0, min_s: float = 1.5,
                     max_s: float = 8.0, translation_only: bool = False) -> float:
    """How long a caption stays readable on screen (seconds).

    Subtitling guidelines give about 17 characters per second for adults. With two lines on screen the
    reader reads one fully and skims the other, so the longer line counts in full and the shorter one at
    half weight. ``translation_only`` counts the translation alone (when there is one).
    """
    a, b = len((original or "").strip()), len((translation or "").strip())
    if translation_only and b:
        chars = float(b)
    else:
        chars = max(a, b) + 0.5 * min(a, b)
    secs = chars / max(1.0, cps)
    return max(min_s, min(max_s, secs))


# ---------------------------------------------------------------- context for translation
class ContextWindow:
    """The last N (original, translation) pairs: sent with each new line so names and terms stay consistent.

    ``key`` identifies a caption: a caption that grew by merging replaces its earlier, shorter entry.
    """

    def __init__(self, size: int = 4) -> None:
        self.items: deque[tuple[object, str, str]] = deque(maxlen=max(0, size))

    def resize(self, size: int) -> None:
        self.items = deque(self.items, maxlen=max(0, size))

    def add(self, original: str, translation: str, key: object = None) -> None:
        if not self.items.maxlen or not original.strip():
            return
        if self.items and ((key is not None and self.items[-1][0] == key) or self.items[-1][1] == original):
            self.items[-1] = (key, original, translation)
        else:
            self.items.append((key, original, translation))

    def pairs(self) -> list[tuple[str, str]]:
        return [(o, t) for _k, o, t in self.items]

    def clear(self) -> None:
        self.items.clear()


@dataclass
class LiveView:
    """What the overlay should show for Live mode right now."""
    original: str = ""
    translation: str = ""
    extra: str = ""              # small third line (the English pivot under an original-language line)
    status: str = ""             # i18n key of a status message, or ''
    status_kw: dict = field(default_factory=dict)
    caption_id: int = 0
    visible: bool = False
    preview: str = ""            # dim line: the unconfirmed end of long speech (interim commits)
