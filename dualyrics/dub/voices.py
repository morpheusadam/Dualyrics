"""Voices of the dubbing engines and the automatic choice per target language.

A voice setting is ``auto`` or one name. The name's shape tells the engine it belongs to: Edge voices look
like ``fa-IR-DilaraNeural``, Piper voices like ``fa_IR-amir-medium``, Gemini voices are single words
(``Kore``). An engine that gets a name of another engine uses its own automatic voice instead.

The automatic voice follows the gender chosen for the target language (``dub_gender``, female by default):
for Persian Edge speaks with Dilara (female) or Farid (male), Gemini with Kore or Charon.
"""

from __future__ import annotations

import re

ENGINES = ("auto", "gemini_live", "gemini_tts", "edge", "piper")

# Gemini prebuilt voices (multilingual). The first is the automatic choice.
GEMINI_VOICES = ("Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr", "Callirrhoe", "Autonoe",
                 "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi",
                 "Laomedeia", "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird",
                 "Zubenelgenubi", "Vindemiatrix", "Sadachbia", "Sadaltager", "Sulafat")

GENDERS = ("female", "male")
DEFAULT_GENDER = "female"

# gender of every Gemini prebuilt voice (Google's voice list), and the automatic voice per gender
GEMINI_MALE = frozenset({"Puck", "Charon", "Fenrir", "Orus", "Enceladus", "Iapetus", "Umbriel", "Algieba",
                         "Algenib", "Rasalgethi", "Alnilam", "Schedar", "Achird", "Zubenelgenubi", "Sadachbia",
                         "Sadaltager"})
GEMINI_BY_GENDER = {"female": "Kore", "male": "Charon"}

# Edge neural voices per language: (female, male), then extra voices; the first is the automatic choice
EDGE_VOICES: dict[str, tuple[str, ...]] = {
    "fa": ("fa-IR-DilaraNeural", "fa-IR-FaridNeural"),
    "en": ("en-US-AriaNeural", "en-US-GuyNeural", "en-US-EmmaMultilingualNeural", "en-GB-SoniaNeural"),
    "ar": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    "tr": ("tr-TR-EmelNeural", "tr-TR-AhmetNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "es": ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    "uk": ("uk-UA-PolinaNeural", "uk-UA-OstapNeural"),
    "zh": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    "hi": ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    "ur": ("ur-PK-UzmaNeural", "ur-PK-AsadNeural"),
    "nl": ("nl-NL-ColetteNeural", "nl-NL-MaartenNeural"),
    "pl": ("pl-PL-ZofiaNeural", "pl-PL-MarekNeural"),
    "sv": ("sv-SE-SofieNeural", "sv-SE-MattiasNeural"),
    "id": ("id-ID-GadisNeural", "id-ID-ArdiNeural"),
    "vi": ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"),
    "th": ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural"),
    "he": ("he-IL-HilaNeural", "he-IL-AvriNeural"),
    "el": ("el-GR-AthinaNeural", "el-GR-NestorasNeural"),
    "ro": ("ro-RO-AlinaNeural", "ro-RO-EmilNeural"),
    "cs": ("cs-CZ-VlastaNeural", "cs-CZ-AntoninNeural"),
    "hu": ("hu-HU-NoemiNeural", "hu-HU-TamasNeural"),
    "az": ("az-AZ-BanuNeural", "az-AZ-BabekNeural"),
    "ps": ("ps-AF-LatifaNeural", "ps-AF-GulNawazNeural"),
}

# Piper voices (rhasspy/piper-voices v1.0.0): path inside the repository, per language
PIPER_VOICES: dict[str, str] = {
    "fa": "fa/fa_IR/amir/medium/fa_IR-amir-medium",
    "en": "en/en_US/lessac/medium/en_US-lessac-medium",
    "de": "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
    "es": "es/es_ES/davefx/medium/es_ES-davefx-medium",
    "fr": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium",
    "it": "it/it_IT/riccardo/x_low/it_IT-riccardo-x_low",
    "pt": "pt/pt_BR/faber/medium/pt_BR-faber-medium",
    "ru": "ru/ru_RU/irina/medium/ru_RU-irina-medium",
    "uk": "uk/uk_UA/ukrainian_tts/medium/uk_UA-ukrainian_tts-medium",
    "tr": "tr/tr_TR/dfki/medium/tr_TR-dfki-medium",
    "ar": "ar/ar_JO/kareem/medium/ar_JO-kareem-medium",
    "zh": "zh/zh_CN/huayan/medium/zh_CN-huayan-medium",
    "nl": "nl/nl_NL/mls/medium/nl_NL-mls-medium",
    "pl": "pl/pl_PL/gosia/medium/pl_PL-gosia-medium",
    "sv": "sv/sv_SE/nst/medium/sv_SE-nst-medium",
    "vi": "vi/vi_VN/vais1000/medium/vi_VN-vais1000-medium",
    "el": "el/el_GR/rapunzelina/low/el_GR-rapunzelina-low",
    "ro": "ro/ro_RO/mihai/medium/ro_RO-mihai-medium",
    "cs": "cs/cs_CZ/jirka/medium/cs_CZ-jirka-medium",
    "hu": "hu/hu_HU/anna/medium/hu_HU-anna-medium",
}
# Piper voices by gender where both exist (the Persian voices are all male: the one above is used)
PIPER_BY_GENDER: dict[str, dict[str, str]] = {
    "en": {"female": PIPER_VOICES["en"], "male": "en/en_US/ryan/medium/en_US-ryan-medium"},
    "de": {"female": "de/de_DE/kerstin/low/de_DE-kerstin-low", "male": PIPER_VOICES["de"]},
    "fr": {"female": PIPER_VOICES["fr"], "male": "fr/fr_FR/tom/medium/fr_FR-tom-medium"},
    "it": {"female": "it/it_IT/paola/medium/it_IT-paola-medium", "male": PIPER_VOICES["it"]},
    "ru": {"female": PIPER_VOICES["ru"], "male": "ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium"},
    "pl": {"female": PIPER_VOICES["pl"], "male": "pl/pl_PL/darkman/medium/pl_PL-darkman-medium"},
    "hu": {"female": PIPER_VOICES["hu"], "male": "hu/hu_HU/imre/medium/hu_HU-imre-medium"},
}

_EDGE = re.compile(r"^[a-z]{2,3}-[A-Z]{2}(?:-[A-Za-z]+)*-[A-Za-z]+Neural$")
_PIPER = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(?:x_low|low|medium|high)$")
_GEMINI = re.compile(r"^[A-Z][a-z]+$")
VOICE_RE = re.compile(r"^[A-Za-z0-9_-]{2,80}$")


def base_lang(code: str) -> str:
    return (code or "").split("-")[0].lower()


def engine_of(voice: str) -> str:
    """Which engine a voice name belongs to ('' for ``auto`` or an unknown name)."""
    if _EDGE.match(voice or ""):
        return "edge"
    if _PIPER.match(voice or ""):
        return "piper"
    if _GEMINI.match(voice or "") and voice in GEMINI_VOICES:
        return "gemini"
    return ""


def gender_for(cfg: dict, lang: str) -> str:
    """The voice gender chosen for target language ``lang`` (``dub_gender``), else the default."""
    g = (cfg.get("dub_gender") or {}).get(base_lang(lang)) if isinstance(cfg.get("dub_gender"), dict) else None
    return g if g in GENDERS else DEFAULT_GENDER


def edge_voice(voice: str, lang: str, gender: str = "") -> str | None:
    """The Edge voice to use for ``lang`` (None when Edge has none for that language). A chosen voice of that
    language wins; otherwise the first voice is female and the second male."""
    if engine_of(voice) == "edge" and base_lang(voice) == base_lang(lang):
        return voice
    choices = EDGE_VOICES.get(base_lang(lang))
    if not choices:
        return None
    return choices[1] if gender == "male" and len(choices) > 1 else choices[0]


def gemini_voice(voice: str, gender: str = "") -> str:
    if engine_of(voice) == "gemini":
        return voice
    return GEMINI_BY_GENDER.get(gender, GEMINI_VOICES[0])


def gemini_gender(voice: str) -> str:
    return "male" if voice in GEMINI_MALE else "female"


def _piper_paths() -> list[str]:
    return [*PIPER_VOICES.values(), *(p for d in PIPER_BY_GENDER.values() for p in d.values())]


def piper_voice(voice: str, lang: str, gender: str = "") -> str | None:
    """Repository path of the Piper voice for ``lang`` (None when there is none)."""
    if engine_of(voice) == "piper" and base_lang(voice.split("_")[0]) == base_lang(lang):
        for path in _piper_paths():
            if path.endswith("/" + voice):
                return path
    by = PIPER_BY_GENDER.get(base_lang(lang), {})
    return by.get(gender) or PIPER_VOICES.get(base_lang(lang))


def auto_voice(engine: str, lang: str, gender: str) -> str:
    """The name the automatic voice has for an engine, language and gender ('' when there is none), for the
    labels of Settings > Dubbing."""
    if engine == "edge":
        return edge_voice("auto", lang, gender) or ""
    if engine == "piper":
        p = piper_voice("auto", lang, gender)
        return p.rsplit("/", 1)[1] if p else ""
    if engine in ("gemini_tts", "gemini_live"):
        return gemini_voice("auto", gender)
    return ""


def short_name(voice: str) -> str:
    """``fa-IR-FaridNeural`` -> ``Farid``, ``en_US-ryan-medium`` -> ``ryan``, ``Kore`` -> ``Kore``."""
    if _EDGE.match(voice or ""):
        return voice.rsplit("-", 1)[1][:-len("Neural")]
    if _PIPER.match(voice or ""):
        return voice.split("-")[1]
    return voice or ""


def choices(engine: str, lang: str) -> list[str]:
    """Voices to offer in Settings for an engine and target language (``auto`` first)."""
    if engine == "edge":
        return ["auto", *EDGE_VOICES.get(base_lang(lang), ())]
    if engine == "piper":
        lg = base_lang(lang)
        paths = list(dict.fromkeys([*PIPER_BY_GENDER.get(lg, {}).values(), *(
            [PIPER_VOICES[lg]] if lg in PIPER_VOICES else [])]))
        return ["auto"] + [p.rsplit("/", 1)[1] for p in paths]
    if engine in ("gemini_tts", "gemini_live"):
        return ["auto", *GEMINI_VOICES]
    return ["auto"]
