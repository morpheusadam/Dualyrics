"""Line-by-line translation for Live mode, with context so names and terms stay consistent.

Gemini (when keys are set) gets the last N lines with their translations, the app or game name and the
media title, and translates only the new line. The free Google endpoint has no context, so it is used
as the fallback (or as the provider when the user chose it). Repeated lines come from a small cache.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

from dualyrics import langs, net
from dualyrics.live.text import normalize
from dualyrics.translate import TranslationError

log = logging.getLogger(__name__)

CACHE_SIZE = 500


def build_live_prompt(text: str, context: list[tuple[str, str]], target: str, source: str = "",
                      where: str = "", title: str = "", style: str = "") -> str:
    lang = langs.BY_CODE.get(target)
    lang_name = f"{lang.name} ({lang.native})" if lang else target
    src = langs.BY_CODE.get(source)
    lines = [
        f"You write live subtitles in {lang_name} for speech heard on a computer"
        + (f" (from {src.name})" if src else "") + ".",
        "Translate ONLY the new line into natural, fluent, conversational subtitles: short, idiomatic, not "
        "word for word, same tone and emotion. Use the earlier lines only as context, and keep names of "
        "people, places and things exactly as you translated them before (or as they are when there is no "
        "usual form). The new line may be a fragment of a sentence; translate it as a fragment.",
    ]
    if style:
        lines.append(f"Style: {style}")
    if where:
        lines.append(f"Heard in: {where}" + (f" — {title}" if title and title != where else ""))
    elif title:
        lines.append(f"Heard in: {title}")
    if context:
        lines.append("Earlier lines (original => your translation):")
        lines += [f"- {o} => {t}" if t else f"- {o}" for o, t in context]
    lines.append('Return JSON: {"translation": "<the new line in the target language>"}')
    lines.append(f"New line:\n{text}")
    return "\n".join(lines)


class LiveTranslator:
    def __init__(self, translator: Any) -> None:
        """``translator`` is the app's ``dualyrics.translate.Translator`` (its Gemini and free clients)."""
        self.translator = translator
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()

    def _remember(self, key: tuple[str, str], value: str) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def translate(self, text: str, cfg: dict[str, Any], context: list[tuple[str, str]], source: str = "",
                  where: str = "", title: str = "") -> tuple[str, str]:
        """Returns (translation, state) with state done | skipped. Raises TranslationError."""
        target = cfg["target_language"]
        if source and cfg.get("skip_same_language", True) and langs.same_language(source, target):
            return "", "skipped"
        guess = langs.guess_language(text)
        if guess and cfg.get("skip_same_language", True) and langs.same_language(guess, target):
            return "", "skipped"
        key = (normalize(text), target)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key], "done"
        out = ""
        if cfg["provider"] == "gemini" and cfg["gemini_keys"]:
            prompt = build_live_prompt(text, context, target, source, where, title)
            try:
                data = self.translator.gemini.ask(cfg["gemini_keys"], cfg["gemini_models"], prompt,
                                                  timeout=net.TIMEOUT_FAST)
                out = (data.get("translation") if isinstance(data, dict) else str(data or "")) or ""
                out = str(out).strip().strip('"«»')
            except TranslationError as e:
                log.info("live gemini failed: %s", e)
                if not cfg["fallback_free"]:
                    raise
        if not out:
            try:
                lines, _det = self.translator.free.translate([text], target)
                out = (lines[0] if lines else "").strip()
            except Exception as e:
                raise TranslationError(f"free translate: {e}") from e
        if out:
            self._remember(key, out)
        return out, "done"
