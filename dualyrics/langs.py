"""Supported translation languages, Windows UI language lookup and script-based language guessing."""

from __future__ import annotations

import locale
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str        # code sent to translation providers (Google style)
    name: str        # English name
    native: str      # name in the language itself
    rtl: bool = False


LANGUAGES: list[Language] = [
    Language("en", "English", "English"),
    Language("fa", "Persian", "فارسی", True),
    Language("ar", "Arabic", "العربية", True),
    Language("tr", "Turkish", "Türkçe"),
    Language("de", "German", "Deutsch"),
    Language("fr", "French", "Français"),
    Language("es", "Spanish", "Español"),
    Language("it", "Italian", "Italiano"),
    Language("pt", "Portuguese", "Português"),
    Language("ru", "Russian", "Русский"),
    Language("uk", "Ukrainian", "Українська"),
    Language("pl", "Polish", "Polski"),
    Language("nl", "Dutch", "Nederlands"),
    Language("sv", "Swedish", "Svenska"),
    Language("no", "Norwegian", "Norsk"),
    Language("da", "Danish", "Dansk"),
    Language("fi", "Finnish", "Suomi"),
    Language("cs", "Czech", "Čeština"),
    Language("sk", "Slovak", "Slovenčina"),
    Language("hu", "Hungarian", "Magyar"),
    Language("ro", "Romanian", "Română"),
    Language("bg", "Bulgarian", "Български"),
    Language("sr", "Serbian", "Српски"),
    Language("hr", "Croatian", "Hrvatski"),
    Language("el", "Greek", "Ελληνικά"),
    Language("he", "Hebrew", "עברית", True),
    Language("hi", "Hindi", "हिन्दी"),
    Language("ur", "Urdu", "اردو", True),
    Language("bn", "Bengali", "বাংলা"),
    Language("ta", "Tamil", "தமிழ்"),
    Language("id", "Indonesian", "Bahasa Indonesia"),
    Language("ms", "Malay", "Bahasa Melayu"),
    Language("tl", "Filipino", "Filipino"),
    Language("vi", "Vietnamese", "Tiếng Việt"),
    Language("th", "Thai", "ไทย"),
    Language("zh-CN", "Chinese (Simplified)", "简体中文"),
    Language("zh-TW", "Chinese (Traditional)", "繁體中文"),
    Language("ja", "Japanese", "日本語"),
    Language("ko", "Korean", "한국어"),
    Language("az", "Azerbaijani", "Azərbaycanca"),
    Language("ka", "Georgian", "ქართული"),
    Language("hy", "Armenian", "Հայերեն"),
    Language("ckb", "Kurdish (Sorani)", "کوردی", True),
    Language("ku", "Kurdish (Kurmanji)", "Kurmancî"),
    Language("ps", "Pashto", "پښتو", True),
    Language("sw", "Swahili", "Kiswahili"),
]

BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}


def base_code(code: str) -> str:
    """'zh-CN' -> 'zh', 'pt_BR' -> 'pt', 'iw' -> 'he' (normalises provider answers)."""
    c = (code or "").strip().lower().replace("_", "-").split("-")[0]
    return {"iw": "he", "fil": "tl", "nb": "no", "nn": "no", "pes": "fa", "prs": "fa"}.get(c, c)


def same_language(a: str, b: str) -> bool:
    return bool(a) and bool(b) and base_code(a) == base_code(b)


def is_rtl_language(code: str) -> bool:
    lang = BY_CODE.get(code)
    return bool(lang and lang.rtl)


def windows_ui_language() -> str:
    """Best supported language code for the Windows display language (falls back to English)."""
    tag = ""
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        tag = locale.windows_locale.get(lcid, "")
    except Exception:
        pass
    if not tag:
        try:
            tag = locale.getlocale()[0] or ""
        except Exception:
            tag = ""
    tag = tag.replace("_", "-")
    if tag.lower().startswith("zh"):
        return "zh-TW" if any(x in tag.upper() for x in ("TW", "HK", "MO", "HANT")) else "zh-CN"
    code = base_code(tag)
    return code if code in BY_CODE else "en"


_RTL_CHARS = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
_LETTERS = re.compile(r"[^\W\d_]")
_PERSIAN_ONLY = re.compile(r"[پچژگکی]")
_ARABIC_ONLY = re.compile(r"[ةيكىأإؤ]")
_SCRIPTS: list[tuple[str, re.Pattern[str]]] = [
    ("he", re.compile(r"[֐-׿]")),
    ("arabic", re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")),
    ("el", re.compile(r"[Ͱ-Ͽ]")),
    ("cyrillic", re.compile(r"[Ѐ-ӿ]")),
    ("hi", re.compile(r"[ऀ-ॿ]")),
    ("bn", re.compile(r"[ঀ-৿]")),
    ("ta", re.compile(r"[஀-௿]")),
    ("th", re.compile(r"[฀-๿]")),
    ("ka", re.compile(r"[Ⴀ-ჿ]")),
    ("hy", re.compile(r"[԰-֏]")),
    ("ko", re.compile(r"[가-힯ᄀ-ᇿ]")),
    ("kana", re.compile(r"[぀-ヿ]")),
    ("han", re.compile(r"[一-鿿]")),
    ("latin", re.compile(r"[A-Za-zÀ-ɏ]")),
]


def is_rtl_text(text: str) -> bool:
    """True when right-to-left letters dominate the text (Persian, Arabic, Hebrew...)."""
    letters = _LETTERS.findall(text or "")
    if not letters:
        return False
    rtl = sum(1 for ch in letters if _RTL_CHARS.match(ch))
    return rtl * 2 >= len(letters)


def guess_language(text: str) -> str | None:
    """Guess the language from its writing system.

    Returns a code only when the script identifies the language with good confidence
    (e.g. Persian, Korean, Greek). Latin and Cyrillic text return ``None`` because the
    script alone cannot tell English from Spanish; the translation provider decides then.
    """
    counts = {name: len(rx.findall(text or "")) for name, rx in _SCRIPTS}
    total = sum(counts.values())
    if total < 8:
        return None
    script, n = max(counts.items(), key=lambda kv: kv[1])
    if n / total < 0.7:
        return None  # mixed-language lyrics: let the provider translate
    if script == "arabic":
        fa, ar = len(_PERSIAN_ONLY.findall(text)), len(_ARABIC_ONLY.findall(text))
        if fa > ar:
            return "fa"
        if ar > fa:
            return "ar"
        return None
    if script == "kana":
        return "ja"
    if script == "han":
        return "ja" if counts["kana"] else None  # zh-CN vs zh-TW is ambiguous
    if script in ("latin", "cyrillic"):
        return None
    return script
