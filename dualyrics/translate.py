"""Whole-song translation: Gemini (API keys, best quality) or free Google Translate (no key).

The song is translated at once (so the model understands the whole story), repeated lines are sent
once, and the answer is repaired when the model returns the wrong number of lines.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from dualyrics import langs, net

log = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FREE_URL = "https://translate.googleapis.com/translate_a/single"
CHUNK = 25            # lines per chunk when the whole-song answer cannot be repaired
FREE_CHARS = 1800     # characters per free-translate request


class TranslationError(Exception):
    """Raised when no provider could translate (quota, keys, network)."""


@dataclass
class Translation:
    lines: list[str]                 # one entry per input line ('' for blank lines)
    provider: str                    # provider that produced it: 'gemini' | 'free' | 'none'
    skipped: bool = False            # lyrics already in the target language
    source_lang: str = ""
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- line-count repair
_NUM = re.compile(r"^\s*(\d{1,4})\s*[.)\-:]\s+")


def _text(item: Any) -> str:
    if isinstance(item, dict):
        for k in ("t", "text", "translation", "line"):
            if k in item:
                return str(item[k])
        return ""
    return "" if item is None else str(item)


def fit_lines(n: int, items: Any, force: bool = False) -> list[str] | None:
    """Make a model answer exactly ``n`` lines long, or return ``None`` if it cannot be trusted.

    Accepts plain strings, ``{"n": 3, "t": "..."}`` objects and numbered strings ("3. ...").
    Numbering is removed only when it matches the line position, so lyrics that start with a
    number stay intact. With ``force=True`` the answer is padded/truncated as a last resort.
    """
    if n == 0:
        return []
    if isinstance(items, str):
        items = items.split("\n")
    if not isinstance(items, list):
        return [""] * n if force else None

    # numbered objects: place by number
    if items and all(isinstance(x, dict) and isinstance(x.get("n"), int) for x in items):
        out = [""] * n
        for x in items:
            if 1 <= x["n"] <= n:
                out[x["n"] - 1] = _text(x).strip()
        filled = sum(1 for x in out if x)
        return out if filled >= max(1, int(n * 0.9)) or force else None

    texts = [_text(x) for x in items]

    def strip_own_number(i: int, s: str) -> str:
        m = _NUM.match(s)
        return s[m.end():] if m and int(m.group(1)) == i + 1 else s

    if len(texts) == n:
        return [strip_own_number(i, s).strip() for i, s in enumerate(texts)]

    # numbered strings in the wrong count: place by their number
    numbered = [(int(m.group(1)), s[m.end():].strip()) for s in texts if (m := _NUM.match(s))]
    if len(numbered) >= len(texts) * 0.8 and len(numbered) >= n * 0.9:
        out = [""] * n
        for k, s in numbered:
            if 1 <= k <= n and not out[k - 1]:
                out[k - 1] = s
        if sum(1 for x in out if x) >= max(1, int(n * 0.9)):
            return out

    non_empty = [s.strip() for s in texts if s.strip()]
    if len(non_empty) == n:
        return non_empty
    if force:
        fixed = [strip_own_number(i, s).strip() for i, s in enumerate(texts[:n])]
        return fixed + [""] * (n - len(fixed))
    return None


def _unique(lines: list[str]) -> tuple[list[str], list[int]]:
    """Unique non-empty lines (in order) and, for every input line, its index in that list (-1 = blank)."""
    uniq: list[str] = []
    pos: dict[str, int] = {}
    mapping: list[int] = []
    for ln in lines:
        key = ln.strip()
        if not key or key in ("♪", "...", "…"):
            mapping.append(-1)
            continue
        if key not in pos:
            pos[key] = len(uniq)
            uniq.append(key)
        mapping.append(pos[key])
    return uniq, mapping


# ---------------------------------------------------------------- Gemini
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')
NOTICE_EVERY = 60.0   # seconds between two "switched model/key" notices for the same pair


def rate_limit_wait(msg: str, raw: str = "", now: float | None = None) -> float:
    """How long a model x key stays skipped after HTTP 429: until the daily reset (midnight Pacific) when the
    daily quota is used up, else the server's ``retryDelay``, else 90 s."""
    text = f"{msg} {raw}".lower()
    if "perday" in text.replace(" ", "").replace("_", "") or "per day" in text or "requests_per_day" in text:
        from dualyrics import usage

        return usage.quota_reset_in(time.time() if now is None else now)
    m = _RETRY_DELAY.search(raw)
    if m:
        return max(5.0, min(3600.0, float(m.group(1)) + 1))
    return 90.0


class Gemini:
    def __init__(self, session_factory: Callable[[], requests.Session]) -> None:
        self.session_factory = session_factory
        self._cool: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()
        # v2.1 usage meter (dualyrics/usage.py) and fallback notices, wired by the app
        self.meter: Any = None
        self.key_index: Callable[[str], int] = lambda key: 0
        self.on_notice: Callable[[str, dict[str, Any]], None] | None = None
        self._noticed: dict[tuple[str, str], float] = {}

    def _cooldown(self, model: str, key: str, seconds: float) -> None:
        with self._lock:
            self._cool[(model, key)] = time.time() + seconds

    def _cooling(self, model: str, key: str) -> bool:
        with self._lock:
            return self._cool.get((model, key), 0) > time.time()

    def cooling(self, model: str, key: str) -> bool:
        return self._cooling(model, key)

    def reset_cooldowns(self) -> None:
        with self._lock:
            self._cool.clear()

    def notice(self, what: str, **kw: Any) -> None:
        """Tell the user that a model, a key or the provider changed because of a limit (rate limited)."""
        if self.on_notice is None:
            return
        mark = (what, str(sorted(kw.items())))
        now = time.time()
        with self._lock:
            if now - self._noticed.get(mark, 0.0) < NOTICE_EVERY:
                return
            self._noticed[mark] = now
        try:
            self.on_notice(what, kw)
        except Exception:
            log.debug("notice callback failed", exc_info=True)

    def _key_no(self, key: str) -> int:
        try:
            return int(self.key_index(key))
        except Exception:
            return 0

    def record(self, model: str, key: str, kind: str, usage: Any, ok: bool, status: int,
               audio_sec: float = 0.0) -> None:
        if self.meter is None:
            return
        try:
            self.meter.record(model, self._key_no(key), kind, usage, ok, status, audio_sec)
        except Exception:
            log.debug("usage record failed", exc_info=True)

    def post(self, model: str, key: str, body: dict[str, Any], timeout: Any = net.TIMEOUT_SLOW,
             kind: str = "text", audio_sec: Callable[[dict[str, Any]], float] | None = None,
             ) -> tuple[dict[str, Any] | None, int, str, str]:
        """One ``generateContent`` request, counted in the usage meter. Returns (json, status, message, raw
        error body). Network errors are raised (and counted as failed requests)."""
        try:
            r = self.session_factory().post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key},
                                            json=body, timeout=timeout)
        except Exception:
            self.record(model, key, kind, None, False, 0)
            raise
        if not r.ok:
            raw = r.text[:4000]
            try:
                msg = r.json().get("error", {}).get("message", "")[:200]
            except Exception:
                msg = raw[:200]
            self.record(model, key, kind, None, False, r.status_code)
            return None, r.status_code, msg, raw
        try:
            data = r.json()
        except ValueError:
            self.record(model, key, kind, None, False, r.status_code)
            raise
        secs = 0.0
        if audio_sec is not None:
            try:
                secs = float(audio_sec(data))
            except Exception:
                secs = 0.0
        self.record(model, key, kind, data.get("usageMetadata"), True, r.status_code, secs)
        return data, r.status_code, "", ""

    def call_once(self, model: str, key: str, prompt: str, timeout: Any = net.TIMEOUT_SLOW,
                  json_mode: bool = True) -> tuple[Any, int, str]:
        """One request. Returns (parsed_json_or_text, http_status, error_message)."""
        data, status, msg, _raw = self._call(model, key, prompt, timeout, json_mode)
        return data, status, msg

    def _call(self, model: str, key: str, prompt: str, timeout: Any = net.TIMEOUT_SLOW,
              json_mode: bool = True) -> tuple[Any, int, str, str]:
        body: dict[str, Any] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": 0.4}}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        j, status, msg, raw = self.post(model, key, body, timeout)
        if j is None:
            return None, status, msg, raw
        cands = j.get("candidates") or []
        if not cands:
            return None, 200, "empty answer (blocked?)", ""
        parts = cands[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not json_mode:
            return text, 200, "", ""
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        return json.loads(text), 200, "", ""

    def ask(self, keys: list[str], models: list[str], prompt: str, timeout: Any = net.TIMEOUT_SLOW) -> Any:
        """Try every model x key (skipping ones cooling down after errors): when one is rate limited (429) the
        next key, then the next model, is used, with a notice. Raises TranslationError."""
        if not keys:
            raise TranslationError("no Gemini API key")
        last = "all models are cooling down"
        limited: tuple[str, str] | None = None     # the first model x key that answered 429 in this request
        for model in models:
            for key in keys:
                if self._cooling(model, key):
                    continue
                try:
                    data, status, msg, raw = self._call(model, key, prompt, timeout=timeout)
                except json.JSONDecodeError as e:
                    self._cooldown(model, key, 20)
                    last = f"{model}: bad JSON ({e})"
                    continue
                except Exception as e:
                    self._cooldown(model, key, 30 if net.is_network_error(e) else 60)
                    last = f"{model}: {e}"
                    log.info("gemini %s failed: %s", model, e)
                    if net.is_network_error(e):
                        raise TranslationError(f"network error: {e}") from e
                    continue
                if status == 200 and data is not None:
                    if limited is not None:
                        self.notice("fallback_model", frm=limited[0], to=model, key=self._key_no(key))
                    return data
                wait = {400: 3600, 401: 86400, 403: 86400, 404: 86400}.get(status, 30)
                if status == 429:
                    wait = rate_limit_wait(msg, raw)
                    if limited is None:
                        limited = (model, key)
                elif status == 400 and "api key" not in msg.lower():
                    wait = 120  # bad request for this model, not a bad key
                self._cooldown(model, key, wait)
                last = f"{model}: HTTP {status} {msg}"
                log.info("gemini %s -> %s %s", model, status, msg)
        if limited is not None:
            last = f"rate limited: {last}"
        raise TranslationError(last)


def build_prompt(lines: list[str], title: str, artist: str, target: str, style: str, retry_note: str = "") -> str:
    lang = langs.BY_CODE.get(target)
    lang_name = f"{lang.name} ({lang.native})" if lang else target
    numbered = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))
    return (
        f"You translate song lyrics into {lang_name}.\n"
        f"Style: {style}\n"
        f"Song: \"{title}\" by {artist}. Read all lines first so the translation follows the whole song.\n"
        f"Rules: exactly one translated line per input line, same order, no numbering, no notes. "
        f"Repeated lines get the same translation. Keep names as they are.\n"
        f"{retry_note}"
        f"Return JSON: {{\"source_language\": \"<ISO 639-1 code of the lyrics>\", \"lines\": [...]}} "
        f"where \"lines\" has exactly {len(lines)} strings.\n"
        f"Lines:\n{numbered}"
    )


# ---------------------------------------------------------------- free Google Translate
class FreeGoogle:
    """Unofficial, keyless Google Translate endpoint (good enough as a fallback)."""

    def __init__(self, session_factory: Callable[[], requests.Session]) -> None:
        self.session_factory = session_factory

    def _one(self, text: str, target: str) -> tuple[str, str]:
        r = self.session_factory().get(FREE_URL, params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t",
                                                          "q": text}, timeout=net.TIMEOUT_FAST)
        r.raise_for_status()
        j = r.json()
        out = "".join(seg[0] for seg in (j[0] or []) if seg and seg[0])
        detected = j[2] if len(j) > 2 and isinstance(j[2], str) else ""
        return out, detected

    def translate(self, lines: list[str], target: str) -> tuple[list[str], str]:
        chunks: list[list[str]] = [[]]
        size = 0
        for ln in lines:
            if chunks[-1] and size + len(ln) + 1 > FREE_CHARS:
                chunks.append([])
                size = 0
            chunks[-1].append(ln)
            size += len(ln) + 1
        result: list[str] = []
        detected = ""
        for ch in chunks:
            if not ch:
                continue
            text, det = self._one("\n".join(ch), target)
            detected = detected or det
            parts = [p.strip() for p in text.split("\n")]
            if len(parts) != len(ch):  # newline lost: fall back to one request per line
                with ThreadPoolExecutor(4) as ex:
                    parts = [p.strip() for p, _ in ex.map(lambda s: self._one(s, target), ch)]
            result.extend(parts)
        return result, detected


# ---------------------------------------------------------------- facade
class Translator:
    def __init__(self, session_factory: Callable[[], requests.Session]) -> None:
        self.gemini = Gemini(session_factory)
        self.free = FreeGoogle(session_factory)

    def translate(self, lines: list[str], title: str, artist: str, cfg: dict[str, Any],
                  cancelled: Callable[[], bool] = lambda: False) -> Translation:
        """Translate ``lines`` according to the settings snapshot ``cfg``."""
        target = cfg["target_language"]
        uniq, mapping = _unique(lines)
        if not uniq:
            return Translation([""] * len(lines), "none", skipped=True)
        if cfg["skip_same_language"]:
            guess = langs.guess_language("\n".join(uniq))
            if guess and langs.same_language(guess, target):
                return Translation([""] * len(lines), "none", skipped=True, source_lang=guess)

        out: list[str] | None = None
        provider, source = "none", ""
        notes: list[str] = []
        if cfg["provider"] == "gemini" and cfg["gemini_keys"]:
            try:
                out, source = self._gemini_song(uniq, title, artist, cfg, cancelled)
                provider = "gemini"
            except TranslationError as e:
                notes.append(f"gemini: {e}")
                log.warning("gemini translation failed: %s", e)
                if not cfg["fallback_free"]:
                    raise
        if out is None and not cancelled():
            if cfg["provider"] == "gemini" and not cfg["gemini_keys"] and not cfg["fallback_free"]:
                raise TranslationError("no Gemini API key")
            try:
                out, source = self.free.translate(uniq, target)
                provider = "free"
            except Exception as e:
                raise TranslationError(f"free translate: {e}") from e
        if out is None:
            raise TranslationError("cancelled")
        if cfg["skip_same_language"] and source and langs.same_language(source, target):
            return Translation([""] * len(lines), provider, skipped=True, source_lang=source, notes=notes)
        full = [out[i] if i >= 0 and i < len(out) else "" for i in mapping]
        return Translation(full, provider, source_lang=source, notes=notes)

    def _gemini_song(self, uniq: list[str], title: str, artist: str, cfg: dict[str, Any],
                     cancelled: Callable[[], bool]) -> tuple[list[str], str]:
        keys, models = cfg["gemini_keys"], cfg["gemini_models"]
        target, style = cfg["target_language"], cfg["style"]

        def attempt(part: list[str], note: str = "") -> tuple[list[str] | None, str, int]:
            data = self.gemini.ask(keys, models, build_prompt(part, title, artist, target, style, note))
            items = data.get("lines") if isinstance(data, dict) else data
            if isinstance(data, dict) and items is None:  # model used another key name
                items = next((v for v in data.values() if isinstance(v, list)), None)
            src = str(data.get("source_language", "")) if isinstance(data, dict) else ""
            got = len(items) if isinstance(items, list) else -1
            return fit_lines(len(part), items), src, got

        if len(uniq) <= 150:
            res, src, got = attempt(uniq)
            if res is not None:
                return res, src
            log.info("gemini returned %s lines instead of %s; retrying", got, len(uniq))
            if cancelled():
                raise TranslationError("cancelled")
            res, src, got = attempt(uniq, f"IMPORTANT: your previous answer had {got} lines; it must have "
                                          f"exactly {len(uniq)}.\n")
            if res is not None:
                return res, src
            log.info("gemini line count still wrong; translating in chunks of %s", CHUNK)
        out: list[str] = []
        src = ""
        for start in range(0, len(uniq), CHUNK):
            if cancelled():
                raise TranslationError("cancelled")
            part = uniq[start:start + CHUNK]
            res, s, _ = attempt(part)
            if res is None:
                data = self.gemini.ask(keys, models, build_prompt(part, title, artist, target, style))
                items = data.get("lines") if isinstance(data, dict) else data
                res = fit_lines(len(part), items, force=True) or [""] * len(part)
            src = src or s
            out.extend(res)
        return out, src

    def test_gemini(self, keys: list[str], models: list[str]) -> list[tuple[str, str, bool, str]]:
        """Check every key x model with a tiny request. Returns (masked_key, model, ok, message)."""
        results = []
        for key in keys:
            masked = key[:4] + "…" + key[-4:] if len(key) > 10 else "…"
            for model in models:
                try:
                    data, status, msg = self.gemini.call_once(model, key, "Reply with the single word OK.",
                                                              timeout=(8, 30), json_mode=False)
                    ok = status == 200
                    results.append((masked, model, ok, "OK" if ok else f"HTTP {status} {msg}"))
                except Exception as e:
                    results.append((masked, model, False, str(e)[:160]))
        if any(ok for *_, ok, _ in results):
            self.gemini.reset_cooldowns()
        return results
