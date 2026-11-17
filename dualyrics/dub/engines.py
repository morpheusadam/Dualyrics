"""Text-to-speech engines for dubbing and the fallback chain between them.

- ``gemini_tts``: Gemini speech generation (``*-tts`` models, found with ListModels) with the user's keys;
  every request is counted in the usage meter (kind ``tts``, with seconds of audio). Rate limits (429) move to
  the next key, then the next model, then the next engine.
- ``edge``: Microsoft Edge neural voices (free, online, optional ``edge-tts`` package).
- ``piper``: Piper (offline, optional download).

``speak_chain`` gives the engines to try, best first, for a setting and what is installed.
"""

from __future__ import annotations

import base64
import logging
import re
import threading
import time
from typing import Any, Callable

from dualyrics import langs, net
from dualyrics.dub import edge, piper, voices
from dualyrics.dub.audio import Clip, pcm_seconds, wav_to_pcm

log = logging.getLogger(__name__)

MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
DISCOVER_EVERY = 6 * 3600.0
FALLBACK_TTS = ("gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")
FALLBACK_LIVE = ("gemini-2.5-flash-native-audio-preview-09-2025", "gemini-live-2.5-flash-preview",
                 "gemini-2.0-flash-live-001")
_RATE = re.compile(r"rate=(\d+)")


class TTSError(Exception):
    def __init__(self, msg: str, rate_limited: bool = False) -> None:
        super().__init__(msg)
        self.rate_limited = rate_limited


# ---------------------------------------------------------------- model discovery (ListModels)
class Discovery:
    """The TTS and Live model ids this key can use, asked once every few hours (ListModels is free)."""

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self.session_factory = session_factory
        self._at = 0.0
        self._key = ""
        self.tts: list[str] = []
        self.live: list[str] = []
        self._lock = threading.Lock()

    def refresh(self, key: str, force: bool = False) -> None:
        with self._lock:
            if not force and key == self._key and time.time() - self._at < DISCOVER_EVERY:
                return
            self._at, self._key = time.time(), key
        tts, live = [], []
        token = ""
        try:
            for _ in range(5):
                params: dict[str, Any] = {"pageSize": 1000}
                if token:
                    params["pageToken"] = token
                r = self.session_factory().get(MODELS_URL, headers={"x-goog-api-key": key}, params=params,
                                               timeout=net.TIMEOUT_FAST)
                if not r.ok:
                    log.info("ListModels -> HTTP %s", r.status_code)
                    break
                j = r.json()
                for m in j.get("models") or []:
                    name = str(m.get("name", "")).removeprefix("models/")
                    methods = m.get("supportedGenerationMethods") or []
                    if "bidiGenerateContent" in methods:
                        live.append(name)
                    elif "tts" in name and "generateContent" in methods:
                        tts.append(name)
                token = j.get("nextPageToken") or ""
                if not token:
                    break
        except Exception as e:  # noqa: BLE001
            log.info("ListModels failed: %s", e)
        with self._lock:
            self.tts = sort_tts(tts)
            self.live = sort_live(live)
        if tts or live:
            log.info("Gemini speech models: tts=%s live=%s", self.tts[:3], self.live[:3])

    def tts_models(self, chosen: str = "auto") -> list[str]:
        if chosen and chosen != "auto":
            return [chosen]
        return list(self.tts) or list(FALLBACK_TTS)

    def live_models(self, chosen: str = "auto") -> list[str]:
        if chosen and chosen != "auto":
            return [chosen]
        return list(self.live) or list(FALLBACK_LIVE)


def sort_tts(names: list[str]) -> list[str]:
    """Flash before Pro (cheaper, faster, bigger free tier), newest first."""
    return sorted(set(names), key=lambda n: (0 if "flash" in n else 1, "preview" in n, [-ord(c) for c in n]))


def sort_live(names: list[str]) -> list[str]:
    """Native-audio models first (they translate speech to speech best), then the other Live models."""
    return sorted(set(names), key=lambda n: (0 if "native-audio" in n else 1, 0 if "flash" in n else 1,
                                             [-ord(c) for c in n]))


# ---------------------------------------------------------------- Gemini TTS
def tts_prompt(text: str, lang: str, rate: float) -> str:
    lg = langs.BY_CODE.get(lang)
    pace = "" if 0.9 <= rate <= 1.1 else (" quickly" if rate > 1.1 else " slowly")
    who = f" in {lg.name}" if lg else ""
    return f"Say{pace}{who}, naturally, like a film dubbing actor: {text}"


def tts_audio(data: dict[str, Any]) -> tuple[bytes, int]:
    """(PCM, sample rate) from a Gemini speech answer."""
    for cand in data.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                m = _RATE.search(inline.get("mimeType", "") or "")
                return base64.b64decode(inline["data"]), int(m.group(1)) if m else 24000
    return b"", 24000


class GeminiTTS:
    name = "gemini_tts"

    def __init__(self, gemini: Any, discovery: Discovery) -> None:
        self.gemini = gemini            # dualyrics.translate.Gemini (shared cooldowns and usage meter)
        self.discovery = discovery

    def ready(self, cfg: dict[str, Any]) -> bool:
        return bool(cfg.get("gemini_keys"))

    def speak(self, text: str, lang: str, voice: str, rate: float, cfg: dict[str, Any]) -> Clip:
        keys = cfg.get("gemini_keys") or []
        if not keys:
            raise TTSError("no Gemini key")
        self.discovery.refresh(keys[0])
        body = {"contents": [{"role": "user", "parts": [{"text": tts_prompt(text, lang, rate)}]}],
                "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voices.gemini_voice(voice, voices.gender_for(cfg, lang))}}}}}
        limited, last = False, "no speech model"
        for model in self.discovery.tts_models(cfg.get("dub_gemini_tts_model", "auto")):
            for key in keys:
                if self.gemini.cooling(model, key):
                    limited = True
                    continue
                try:
                    data, status, msg, raw = self.gemini.post(
                        model, key, body, timeout=net.TIMEOUT_SLOW, kind="tts",
                        audio_sec=lambda d: pcm_seconds(*tts_audio(d)))
                except Exception as e:  # noqa: BLE001
                    self.gemini._cooldown(model, key, 30)
                    raise TTSError(f"{model}: {e}") from e
                if data is not None:
                    pcm, sr = tts_audio(data)
                    if pcm:
                        return Clip(text, pcm=pcm, rate=sr, seconds=pcm_seconds(pcm, sr), engine=self.name)
                    last = f"{model}: no audio in the answer"
                    self.gemini._cooldown(model, key, 60)
                    continue
                from dualyrics.translate import rate_limit_wait

                if status == 429:
                    limited = True
                    mark = getattr(self.gemini, "mark_limited", None)   # the key pool tells minute from day limits
                    if callable(mark):
                        mark(model, key, msg, raw)
                    else:
                        self.gemini._cooldown(model, key, rate_limit_wait(msg, raw))
                else:
                    self.gemini._cooldown(model, key, 3600 if status in (400, 404) else 120)
                last = f"{model}: HTTP {status} {msg}"
        raise TTSError(last, rate_limited=limited)


# ---------------------------------------------------------------- Edge
class EdgeTTS:
    name = "edge"

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self.session_factory = session_factory

    def ready(self, cfg: dict[str, Any]) -> bool:
        return edge.available()

    def speak(self, text: str, lang: str, voice: str, rate: float, cfg: dict[str, Any]) -> Clip:
        v = voices.edge_voice(voice, lang, voices.gender_for(cfg, lang))
        if v is None:
            raise TTSError(f"no Edge voice for {lang}")
        try:
            mp3 = edge.synthesize(text, v, rate, net.proxy_of(self.session_factory()))
        except edge.EdgeError as e:
            raise TTSError(str(e)) from e
        return Clip(text, mp3=mp3, seconds=edge.mp3_seconds(mp3), engine=self.name)


# ---------------------------------------------------------------- Piper
class PiperTTS:
    name = "piper"

    def ready(self, cfg: dict[str, Any]) -> bool:
        return piper.installed()

    def speak(self, text: str, lang: str, voice: str, rate: float, cfg: dict[str, Any]) -> Clip:
        # the voice of the chosen gender, else the language's other downloaded voice
        cands = [voices.piper_voice(voice, lang, voices.gender_for(cfg, lang)), voices.piper_voice(voice, lang),
                 *voices.PIPER_BY_GENDER.get(voices.base_lang(lang), {}).values()]
        path = next((p for p in cands if p and piper.voice_installed(p)), None)
        if path is None:
            raise TTSError(f"no Piper voice downloaded for {lang}")
        try:
            pcm, sr = wav_to_pcm(piper.synthesize(text, path, lang, rate))
        except (piper.PiperError, ValueError) as e:
            raise TTSError(str(e)) from e
        return Clip(text, pcm=pcm, rate=sr, seconds=pcm_seconds(pcm, sr), engine=self.name)


def speak_chain(engine: str, has_keys: bool) -> list[str]:
    """Text engines to try in order for the ``dub_engine`` setting (Gemini Live itself is no text engine:
    when it cannot run, its captions are spoken by this chain)."""
    if engine == "edge":
        return ["edge", "piper", "gemini_tts"] if has_keys else ["edge", "piper"]
    if engine == "piper":
        return ["piper", "edge", "gemini_tts"] if has_keys else ["piper", "edge"]
    # auto, gemini_tts, gemini_live
    return ["gemini_tts", "edge", "piper"] if has_keys else ["edge", "piper"]
