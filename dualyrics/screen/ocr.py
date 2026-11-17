"""Offline OCR with Windows.Media.Ocr (built into Windows 10/11, free, no network).

The recogniser languages are the OCR packs Windows has (Settings > Time & language > Language: each
language's "Optical character recognition" feature). "auto" uses the user's profile languages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

MIN_TEXT_HEIGHT = 90      # small regions are scaled up to about this height (OCR misses tiny letters)
MAX_SIDE = 2400


def _mods() -> tuple[Any, Any, Any, Any]:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    return OcrEngine, Language, (SoftwareBitmap, BitmapPixelFormat), DataWriter


def available() -> bool:
    try:
        OcrEngine, _l, _b, _w = _mods()
        return OcrEngine.try_create_from_user_profile_languages() is not None
    except Exception as e:  # winrt OCR projection missing, or no OCR language at all
        log.info("Windows OCR unavailable: %s", e)
        return False


def languages() -> list[tuple[str, str]]:
    """(BCP-47 tag, display name) of the OCR languages installed in Windows."""
    try:
        OcrEngine, _l, _b, _w = _mods()
        return [(lg.language_tag, lg.display_name) for lg in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def prepare(img: Any) -> Any:
    """Scale a region so letters are big enough for OCR, and never beyond the engine's limit."""
    w, h = img.size
    scale = MIN_TEXT_HEIGHT / max(1, h) if h < MIN_TEXT_HEIGHT else 1.0
    scale = min(scale, MAX_SIDE / max(1, w), MAX_SIDE / max(1, h))
    if abs(scale - 1.0) > 0.05:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))
    return img


class WinOcr:
    """One recogniser; use it from one thread (it owns an asyncio loop for the WinRT calls)."""

    def __init__(self, language: str = "auto") -> None:
        OcrEngine, Language, _b, _w = _mods()
        engine = None
        if language and language != "auto":
            try:
                lang = Language(language)
                if OcrEngine.is_language_supported(lang):
                    engine = OcrEngine.try_create_from_language(lang)
            except Exception as e:
                log.info("OCR language %s unavailable: %s", language, e)
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("no Windows OCR language is installed")
        self.engine = engine
        self.language = engine.recognizer_language.language_tag
        self._loop = asyncio.new_event_loop()

    def recognize(self, img: Any) -> str:
        """Text of a PIL image: OCR lines joined with spaces (a two-line subtitle becomes one line)."""
        return " ".join(self.recognize_lines(img)).strip()

    def recognize_lines(self, img: Any) -> list[str]:
        """The OCR lines of a PIL image, top to bottom."""
        _e, _l, (SoftwareBitmap, BitmapPixelFormat), DataWriter = _mods()
        img = prepare(img.convert("RGB"))
        raw = img.convert("RGBA").tobytes("raw", "BGRA")
        writer = DataWriter()
        writer.write_bytes(raw)
        bmp = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8,
                                                     img.width, img.height)
        result = self._loop.run_until_complete(self.engine.recognize_async(bmp))
        return [line.text.strip() for line in result.lines if line.text.strip()]

    def close(self) -> None:
        try:
            self._loop.close()
        except Exception:
            pass
