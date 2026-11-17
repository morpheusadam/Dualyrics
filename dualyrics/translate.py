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

from dualyrics import keypool, langs, net

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
NOTICE_EVERY = 60.0   # seconds between two "switched model/key" notices for the same pair


class KeysResting(TranslationError):
    """No Gemini key x model can be asked now (all rate limited, out of daily quota, rejected or switched off):
    the caller goes straight to the next translator instead of retrying line by line."""


def rate_limit_wait(msg: str, raw: str = "", now: float | None = None) -> float:
    """How long a model x key stays skipped after HTTP 429: until the daily reset (midnight Pacific) when the
    daily quota is used up, else the server's ``retryDelay``, else 90 s."""
    if keypool.is_daily(msg, raw):
        from dualyrics import usage

        return usage.quota_reset_in(time.time() if now is None else now)
    return keypool.retry_delay(raw) or 90.0


class Gemini:
    def __init__(self, session_factory: Callable[[], requests.Session], pool: keypool.KeyPool | None = None) -> None:
        self.session_factory = session_factory
        self.pool = pool or keypool.KeyPool()
        self._lock = threading.Lock()
        # v2.1 usage meter (dualyrics/usage.py) and fallback notices, wired by the app
        self.meter: Any = None
        self.key_index: Callable[[str], int] = lambda key: 0
        self.on_notice: Callable[[str, dict[str, Any]], None] | None = None
        self._noticed: dict[tuple[str, str], float] = {}
        self._resting = False            # every key was resting at the last request (one notice per episode)

    # -- the key pool (dualyrics/keypool.py); the old names stay for the dubbing engines
    def _cooldown(self, model: str, key: str, seconds: float) -> None:
        self.pool.cooldown(model, key, seconds)

    def _cooling(self, model: str, key: str) -> bool:
        return not self.pool.available(model, key)

    def cooling(self, model: str, key: str) -> bool:
        return self._cooling(model, key)

    def reset_cooldowns(self) -> None:
        self.pool.reset(keep_exhausted=True)

    def mark_limited(self, model: str, key: str, msg: str = "", raw: str = "") -> float:
        """HTTP 429 seen outside ``ask`` (dubbing): per-minute limit or daily quota, in the key pool."""
        return self.pool.mark_limited(model, key, msg, raw)

    def can_ask(self, keys: list[str], models: list[str]) -> bool:
        """Some enabled key x model can be asked now."""
        return bool(keys) and self.pool.any_available(keys, models)

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
        """One answer from the key pool: the models in the user's order, the enabled keys in round robin, pairs
        that rest (rate limited, out of daily quota, rejected, switched off) skipped. A 429 moves on to the next
        key, then the next model, with a notice. Raises KeysResting when nothing can be asked (at once, without
        a request), else TranslationError."""
        if not keys:
            raise TranslationError("no Gemini API key")
        plan = self.pool.order(keys, models)
        if not plan:
            self._all_resting(keys, models)
            raise KeysResting("every Gemini key is resting (rate limit, daily quota, rejected or switched off)")
        last = "all models are cooling down"
        limited: tuple[str, str] | None = None     # the first model x key that answered 429 in this request
        for model, key in plan:
            if not self.pool.available(model, key):
                continue                            # marked meanwhile (a key of the same project ran out)
            try:
                data, status, msg, raw = self._call(model, key, prompt, timeout=timeout)
            except json.JSONDecodeError as e:
                self.pool.cooldown(model, key, 20, "bad JSON")
                last = f"{model}: bad JSON ({e})"
                continue
            except Exception as e:
                last = f"{model}: {e}"
                log.info("gemini %s failed: %s", model, e)
                if net.is_network_error(e):
                    self.pool.cooldown(model, key, 30, "network")
                    raise TranslationError(f"network error: {e}") from e
                self.pool.cooldown(model, key, 60, "error")
                continue
            if status == 200 and data is not None:
                self.pool.mark_ok(model, key)
                self._resting = False
                if limited is not None:
                    self.notice("fallback_model", frm=limited[0], to=model, key=self._key_no(key))
                return data
            if status == 429:
                self.pool.mark_limited(model, key, msg, raw)
                if keypool.is_daily(msg, raw):
                    self.pool.evidence_daily(model, keys)
                if limited is None:
                    limited = (model, key)
            elif keypool.is_bad_key(status, msg):
                self.pool.mark_invalid(key, msg)
            elif status == 400:
                self.pool.cooldown(model, key, 120, "bad request")   # bad request for this model, not a bad key
            elif status in (403, 404):
                self.pool.cooldown(model, key, 86400, f"HTTP {status}")  # model not available to this key
            else:
                self.pool.cooldown(model, key, 30, f"HTTP {status}")
            last = f"{model}: HTTP {status} {msg}"
            log.info("gemini %s -> %s %s", model, status, msg)
        if not self.pool.any_available(keys, models):
            self._all_resting(keys, models)
            raise KeysResting(f"rate limited: {last}" if limited else last)
        if limited is not None:
            last = f"rate limited: {last}"
        raise TranslationError(last)

    def _all_resting(self, keys: list[str], models: list[str]) -> None:
        """One notice when the pool runs dry (until a key works again)."""
        if self._resting:
            return
        self._resting = True
        at = self.pool.next_free_at(keys, models)
        if at == float("inf"):
            return                      # no key is switched on: nothing to wait for
        when = time.strftime("%H:%M", time.localtime(at))
        log.info("gemini: every key is resting until %s", when)
        self.notice("keys_resting", at=when)


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
FREE_REST = (15.0, 120.0)   # seconds the free endpoint is skipped after a failure (doubling up to the maximum)


class FreeGoogle:
    """Unofficial, keyless Google Translate endpoint (good enough as a fallback). After a failure (429, network)
    it rests for 15 s, doubling up to 2 min while it keeps failing, so when the offline translator is there the
    next lines go to it at once instead of each waiting for a timeout."""

    def __init__(self, session_factory: Callable[[], requests.Session]) -> None:
        self.session_factory = session_factory
        self._rest_until = 0.0
        self._rest = FREE_REST[0]

    def resting(self) -> bool:
        return time.time() < self._rest_until

    def _failed(self) -> None:
        self._rest_until = time.time() + self._rest
        self._rest = min(FREE_REST[1], self._rest * 2)

    def _one(self, text: str, target: str) -> tuple[str, str]:
        r = self.session_factory().get(FREE_URL, params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t",
                                                          "q": text}, timeout=net.TIMEOUT_FAST)
        r.raise_for_status()
        j = r.json()
        out = "".join(seg[0] for seg in (j[0] or []) if seg and seg[0])
        detected = j[2] if len(j) > 2 and isinstance(j[2], str) else ""
        return out, detected

    def translate(self, lines: list[str], target: str) -> tuple[list[str], str]:
        try:
            out = self._translate(lines, target)
        except Exception:
            self._failed()
            raise
        self._rest_until, self._rest = 0.0, FREE_REST[0]
        return out

    def _translate(self, lines: list[str], target: str) -> tuple[list[str], str]:
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


# ---------------------------------------------------------------- the chain after Gemini
def offline_ready(translator: Any, cfg: dict[str, Any], target: str, source: str = "") -> bool:
    """The offline translator (dualyrics/offline.py) is switched on and has a package route to ``target``."""
    off = getattr(translator, "offline", None)
    if off is None or not cfg.get("offline_enabled", True):
        return False
    try:
        return bool(off.ready(source, target))
    except Exception:  # noqa: BLE001
        return False


def fallback_chain(translator: Any, lines: list[str], target: str, cfg: dict[str, Any], source: str = "",
                   allow_free: bool = True) -> tuple[list[str], str, str]:
    """The tiers after Gemini: free Google (when allowed), then the offline translator. Returns (lines,
    detected source language, provider ``free`` | ``offline``). Raises TranslationError when both fail."""
    errors: list[str] = []
    offline = offline_ready(translator, cfg, target, source)
    free = getattr(translator, "free", None)
    if allow_free and free is not None:
        resting = getattr(free, "resting", None)
        if not (offline and callable(resting) and resting()):
            try:
                out, detected = free.translate(lines, target)
                return out, detected, "free"
            except Exception as e:  # noqa: BLE001
                errors.append(f"free translate: {e}")
                log.info("free translate failed: %s", e)
        else:
            errors.append("free translate: resting after a failure")
    if offline:
        try:
            out, used = translator.offline.translate(lines, target, source)
            return out, used, "offline"
        except Exception as e:  # noqa: BLE001
            errors.append(f"offline: {e}")
            log.info("offline translate failed: %s", e)
    raise TranslationError("; ".join(errors) or "no translator is available")


# ---------------------------------------------------------------- facade
class Translator:
    def __init__(self, session_factory: Callable[[], requests.Session], pool: keypool.KeyPool | None = None,
                 offline: Any = None) -> None:
        self.gemini = Gemini(session_factory, pool)
        self.free = FreeGoogle(session_factory)
        self.offline = offline           # dualyrics.offline.OfflineTranslator (the last tier), wired by the app

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
                if not cfg["fallback_free"] and not offline_ready(self, cfg, target):
                    raise
        if out is None and not cancelled():
            allow_free = cfg["provider"] != "gemini" or cfg["fallback_free"]
            if not allow_free and not offline_ready(self, cfg, target):
                raise TranslationError(notes[-1] if notes else "no Gemini API key")
            out, source, provider = fallback_chain(self, uniq, target, cfg, allow_free=allow_free)
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

    def test_key(self, key: str, models: list[str]) -> list[tuple[str, bool, str]]:
        """Ask every model once with a tiny request through ``key``; the answers update the key pool (a working
        key is no longer rejected, a 429 marks its limit). Returns (model, ok, message)."""
        out = []
        pool = self.gemini.pool
        for model in models:
            try:
                data, status, msg, raw = self.gemini._call(model, key, "Reply with the single word OK.",
                                                           timeout=(8, 30), json_mode=False)
            except Exception as e:  # noqa: BLE001
                out.append((model, False, str(e)[:160]))
                continue
            if status == 200:
                pool.mark_ok(model, key)
            elif status == 429:
                pool.mark_limited(model, key, msg, raw)
            elif keypool.is_bad_key(status, msg):
                pool.mark_invalid(key, msg)
            out.append((model, status == 200, "OK" if status == 200 else f"HTTP {status} {msg}"))
        return out

    def test_gemini(self, keys: list[str], models: list[str]) -> list[tuple[str, str, bool, str]]:
        """Check every key x model with a tiny request. Returns (masked_key, model, ok, message)."""
        results = []
        for key in keys:
            masked = mask_key(key)
            results += [(masked, model, ok, msg) for model, ok, msg in self.test_key(key, models)]
        return results


def mask_key(key: str) -> str:
    return key[:4] + "…" + key[-4:] if len(key) > 10 else "…"
