"""Any-language captions through an English pivot.

Audio: multilingual Whisper with ``task="translate"`` turns speech in any language into English; the English
line is then translated into the target language (Gemini with context, or free Google). With "Also show the
original-language line" a second Whisper pass writes down what was said in its own language.

On-screen text (OCR / UI Automation) in another language goes the same way through the translator:
original -> English -> target.

The functions here only plan and compose; they take the transcriber and translator as arguments, so the
pipeline is unit-tested with fakes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from dualyrics import langs

log = logging.getLogger(__name__)

TRANSCRIBE, TRANSLATE = "transcribe", "translate"
EN = "en"


def _is(code: str, other: str) -> bool:
    return langs.same_language(code or "", other or "")


# ---------------------------------------------------------------- audio (Whisper)
def whisper_tasks(pivot: bool, show_original: bool, english_model: bool, source: str, target: str) -> tuple[str, ...]:
    """Which Whisper passes to run for one piece of audio.

    No pivot for English-only models, when the pivot is off, or when the spoken language is set and is
    English or already the target language. With the pivot the English pass comes first (it also detects the
    language); the original-language pass follows only when the user wants to see that line too.
    """
    if english_model or not pivot:
        return (TRANSCRIBE,)
    if source and source != "auto" and (_is(source, EN) or _is(source, target)):
        return (TRANSCRIBE,)
    return (TRANSLATE, TRANSCRIBE) if show_original else (TRANSLATE,)


def recognize(transcriber: Any, audio: Any, start: float, end: float, language: str | None, prompt: str,
              tasks: tuple[str, ...], target: str) -> list[Any]:
    """Run the planned passes. Returns recognitions whose ``text`` is the line to translate: English with
    ``pivot=True`` (and the original in ``alt`` when it was written down), or the original language."""
    first = transcriber.transcribe(audio, start, end, language, prompt, task=tasks[0])
    if tasks[0] == TRANSCRIBE or not first:
        return first
    detected = first[0].language or (language or "")
    if detected and _is(detected, EN):
        return first                       # it was English already: nothing to pivot
    same_as_target = bool(detected) and _is(detected, target)
    if TRANSCRIBE in tasks or same_as_target:
        orig = transcriber.transcribe(audio, start, end, detected or language, "", task=TRANSCRIBE)
        if same_as_target:
            return orig                    # spoken in the target language: show it as it is
        alt = " ".join(r.text for r in orig).strip()
        return [replace(r, pivot=True, alt=alt if i == 0 else "") for i, r in enumerate(first)]
    return [replace(r, pivot=True) for r in first]


def display_lines(text: str, alt: str, pivot: bool, translation: str, target: str,
                  show_original: bool) -> tuple[str, str, str]:
    """(top line, translation line, small third line) for a caption.

    Without a pivot: the line as heard and its translation. With the English pivot: English and the target
    translation; with "also show the original" the original-language line goes on top, then the
    translation, then the English in the small third line. When the target is English the English line is
    the translation.
    """
    if not pivot:
        return text, translation, ""
    original = alt if (show_original and alt) else ""
    if _is(target, EN):
        return (original, text, "") if original else (text, "", "")
    if original:
        return original, translation, text
    return text, translation, ""


# ---------------------------------------------------------------- on-screen text (translator)
@dataclass
class TextResult:
    top: str                 # line shown on top
    translation: str         # line under it
    extra: str = ""          # small third line (English, when the original is on top)
    source: str = ""         # detected language of the original text
    english: str = ""        # the English pivot ('' when none was needed)
    state: str = "done"      # done | skipped


def pivot_text(text: str, live_tr: Any, cfg: dict[str, Any], context: list[tuple[str, str]], where: str = "",
               title: str = "") -> TextResult:
    """Translate one on-screen line. ``live_tr`` is a ``LiveTranslator`` (or a fake with the same methods).

    Pivot on: original -> English (``live_tr.to_english``, which also detects the language) -> target.
    Pivot off, or the text is English already: straight to the target.
    Raises ``TranslationError`` like ``LiveTranslator.translate``.
    """
    target = cfg["target_language"]
    show_original = bool(cfg.get("pivot_show_original"))
    english, source = "", langs.guess_language(text) or ""
    if cfg.get("pivot_english", True) and not (source and (_is(source, EN) or _is(source, target))):
        english, detected = live_tr.to_english(text, cfg)
        source = detected or source
        if source and _is(source, target):
            return TextResult(text, "", source=source, state="skipped")
        if source and _is(source, EN):
            english = ""                   # it was English: translate the text itself
    if english.strip() and not norm_eq(english, text):
        out, state = live_tr.translate(english, cfg, context, source=EN, where=where, title=title)
        top, tr, extra = display_lines(english, text, True, out, target, show_original)
        return TextResult(top, tr, extra, source=source, english=english, state=state)
    out, state = live_tr.translate(text, cfg, context, source=source, where=where, title=title)
    return TextResult(text, out, source=source, state=state)


def norm_eq(a: str, b: str) -> bool:
    return " ".join((a or "").lower().split()) == " ".join((b or "").lower().split())
