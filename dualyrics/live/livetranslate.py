"""Line-by-line translation for Live mode, with context so names and terms stay consistent.

Gemini (when keys are set) gets the last N lines with their translations, the app or game name and the
media title, and translates only the new line. The free Google endpoint has no context, so it is used
as the fallback (or as the provider when the user chose it), and the offline translator after it. Repeated
lines come from a small memory cache and from the line cache on disk (``dualyrics/linecache.py``), so a
replayed video costs nothing. A line a lower tier translated is asked from Gemini again when Gemini can be
asked (a better translation replaces it); while every Gemini key rests, the cached line is used as it is.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Iterator

from dualyrics import langs
from dualyrics.linecache import PIVOT_TARGET
from dualyrics.live.text import normalize
from dualyrics.translate import (LIVE_DEADLINE, LIVE_TIMEOUT, ContentBlocked, KeysResting, TranslationError,
                                 fallback_chain, is_looping, offline_ready)

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


def build_english_prompt(text: str) -> str:
    return "\n".join([
        "Translate this on-screen subtitle line into natural English and name its language.",
        'Return JSON: {"language": "<ISO 639-1 code of the original>", "english": "<the line in English>"}',
        f"Line:\n{text}",
    ])


def _live_args() -> dict[str, Any]:
    """``Gemini.ask`` options for one live line: no thinking, a short timeout and a deadline for all pairs."""
    return {"timeout": LIVE_TIMEOUT, "live": True, "deadline": time.monotonic() + LIVE_DEADLINE}


def _gemini_on(cfg: dict[str, Any]) -> bool:
    return cfg["provider"] == "gemini" and bool(cfg["gemini_keys"])


class LiveTranslator:
    def __init__(self, translator: Any, store: Any = None) -> None:
        """``translator`` is the app's ``dualyrics.translate.Translator`` (its Gemini, free and offline clients);
        ``store`` the line cache on disk (``dualyrics.linecache.LineStore``) or None (memory only)."""
        self.translator = translator
        self.store = store
        self._cache: OrderedDict[tuple[str, str], tuple[str, str]] = OrderedDict()   # -> (translation, provider)
        self._english: OrderedDict[str, tuple[str, str]] = OrderedDict()   # pivot: (English, language)
        self._lock = threading.Lock()   # Live mode and the screen source translate on several threads
        self._inflight = 0              # Gemini requests on their way (partials go to a cheaper tier meanwhile)

    def _remember(self, key: tuple[str, str], value: str, provider: str = "", store: bool = True) -> None:
        with self._lock:
            self._cache[key] = (value, provider)
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        if store and self.store is not None:
            self.store.put(key[0], key[1], value, provider)

    def _gemini_can_ask(self, cfg: dict[str, Any]) -> bool:
        gem = getattr(self.translator, "gemini", None)
        can = getattr(gem, "can_ask", None)
        if not callable(can):
            return True
        try:
            return bool(can(cfg["gemini_keys"], cfg["gemini_models"]))
        except Exception:  # noqa: BLE001
            return True

    def _lookup(self, key: tuple[str, str], cfg: dict[str, Any]) -> tuple[str, str] | None:
        """A cached translation that is good enough now: Gemini's always; a lower tier's when Gemini is not used
        or cannot be asked right now."""
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
        if hit is None and self.store is not None:
            got = self.store.get(key[0], key[1])
            if got is not None:
                hit = (got[0], got[1])
                with self._lock:
                    self._cache[key] = hit
                    while len(self._cache) > CACHE_SIZE:
                        self._cache.popitem(last=False)
        if hit is None:
            return None
        if hit[1] != "gemini" and _gemini_on(cfg) and self._gemini_can_ask(cfg):
            return None                       # Gemini can do better now
        return hit

    def _fallback(self, text: str, target: str, cfg: dict[str, Any], source: str = "",
                  gemini_failed: bool = False) -> tuple[str, str, str]:
        """(translation, detected language, provider) from the tiers after Gemini. Raises TranslationError."""
        allow_free = not _gemini_on(cfg) or cfg["fallback_free"]
        lines, detected, provider = fallback_chain(self.translator, [text], target, cfg, source=source,
                                                   allow_free=allow_free)
        out = (lines[0] if lines else "").strip()
        if gemini_failed and out:
            notice = getattr(getattr(self.translator, "gemini", None), "notice", None)
            if callable(notice):
                notice("fallback_free" if provider == "free" else "fallback_offline")
        return out, detected, provider

    def to_english(self, text: str, cfg: dict[str, Any]) -> tuple[str, str]:
        """(English line, detected language) for the English pivot of on-screen text. Free Google when it is
        the provider or the allowed fallback, else Gemini, else the offline translator. Raises TranslationError."""
        key = normalize(text)
        with self._lock:
            if key in self._english:
                self._english.move_to_end(key)
                return self._english[key]
        if self.store is not None:
            got = self.store.get(key, PIVOT_TARGET)
            if got is not None:
                with self._lock:
                    self._english[key] = (got[0], got[2])
                return got[0], got[2]
        english, detected, provider = "", "", ""
        if _gemini_on(cfg) and not cfg["fallback_free"]:
            try:
                data = self.translator.gemini.ask(cfg["gemini_keys"], cfg["gemini_models"],
                                                  build_english_prompt(text), **_live_args())
                if isinstance(data, dict):
                    english = str(data.get("english") or "").strip()
                    if is_looping(english):
                        english = ""
                    detected = str(data.get("language") or "").strip().lower()[:8]
                    provider = "gemini"
            except TranslationError:
                if not offline_ready(self.translator, cfg, "en"):
                    raise
        if not english:
            allow_free = not (_gemini_on(cfg) and not cfg["fallback_free"])
            lines, detected, provider = fallback_chain(self.translator, [text], "en", cfg, allow_free=allow_free)
            english = (lines[0] if lines else "").strip()
        if english:
            with self._lock:
                self._english[key] = (english, detected or "")
                while len(self._english) > CACHE_SIZE:
                    self._english.popitem(last=False)
            if self.store is not None:
                self.store.put(key, PIVOT_TARGET, english, provider, detected or "")
        return english, detected or ""

    def translate(self, text: str, cfg: dict[str, Any], context: list[tuple[str, str]], source: str = "",
                  where: str = "", title: str = "", partial: bool = False) -> tuple[str, str]:
        """Returns (translation, state) with state done | skipped. Raises TranslationError.

        ``partial``: an unfinished Live Captions sentence. While another Gemini request is still on its way it
        goes to the free / offline tier (when one is allowed) and is kept in memory only; the finished
        sentence always goes to Gemini and replaces it."""
        target = cfg["target_language"]
        if source and cfg.get("skip_same_language", True) and langs.same_language(source, target):
            return "", "skipped"
        guess = langs.guess_language(text)
        if guess and cfg.get("skip_same_language", True) and langs.same_language(guess, target):
            return "", "skipped"
        key = (normalize(text), target)
        hit = self._lookup(key, cfg)
        if hit is not None:
            meter = getattr(getattr(self.translator, "gemini", None), "meter", None)
            if meter is not None and hit[1] == "gemini" and _gemini_on(cfg):
                meter.cache_hit()          # a Gemini request saved
            return hit[0], "done"
        out, provider = "", ""
        gemini_failed = False
        cheap = partial and self.busy() and (cfg["fallback_free"] or offline_ready(self.translator, cfg, target,
                                                                                    source))
        if _gemini_on(cfg) and not cheap:
            prompt = build_live_prompt(text, context, target, source, where, title)
            try:
                with self._asking():
                    data = self.translator.gemini.ask(cfg["gemini_keys"], cfg["gemini_models"], prompt,
                                                      **_live_args())
                out = (data.get("translation") if isinstance(data, dict) else str(data or "")) or ""
                out = str(out).strip().strip('"«»')
                provider = "gemini"
                if is_looping(out):
                    log.info("live gemini answer repeats itself; using the next tier")
                    out, gemini_failed = "", True
            except TranslationError as e:
                if not isinstance(e, (KeysResting, ContentBlocked)):
                    log.info("live gemini failed: %s", e)
                if not cfg["fallback_free"] and not offline_ready(self.translator, cfg, target, source):
                    raise
                gemini_failed = True
        if not out:
            out, _det, provider = self._fallback(text, target, cfg, source, gemini_failed)
        if out:
            self._remember(key, out, provider, store=not cheap)
        return out, "done"

    def busy(self) -> bool:
        """A Gemini request of this translator is on its way now."""
        with self._lock:
            return self._inflight > 0

    @contextlib.contextmanager
    def _asking(self) -> Iterator[None]:
        with self._lock:
            self._inflight += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight -= 1
