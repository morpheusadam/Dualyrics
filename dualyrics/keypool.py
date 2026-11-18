r"""Gemini key pool: which key and model the next request uses, and what every key x model is doing now.

- Round robin: request n starts at enabled key n mod N for the first model (the models stay in the user's order:
  the best one first), so the load is spread over all keys instead of draining key 1 first. Keys and models
  that are resting are skipped.
- Pacing: a pair that answered a per-minute 429 is from then on given at most its free-tier requests per minute
  (``DEFAULT_RPM``, a 60 s window in memory), so bursts are spread over the keys instead of collecting 429s.
- State of every model x key: ``ok``, ``rate_limited`` (a per-minute limit; until the server's ``retryDelay``),
  ``exhausted`` (the free-tier daily quota is used up; until the next reset at midnight US Pacific, stored as an
  absolute time so a restart does not ask again), ``cooling`` (a bad answer or a server error), and per key
  ``invalid`` (Google rejected the key) or ``off`` (switched off in Settings).
- Shared projects: Google counts the free quota per Cloud *project*, not per key. Two keys of one project hit
  429 together: when one key gets 429 and another one also gets 429 for the same model within a few seconds,
  the pair gains a point; when the other key answers fine right after, it loses one. From two points on the
  pair is reported as sharing a project (Settings shows a warning), and a daily-quota 429 of one key marks its
  partners exhausted too, so they are not asked in vain.

Only fingerprints of the keys (12 hex digits of a SHA-256) are stored, in
``%APPDATA%\Dualyrics\gemini_keys_state.json``; never the keys.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

OK, LIMITED, EXHAUSTED, COOLING, INVALID, OFF = "ok", "rate_limited", "exhausted", "cooling", "invalid", "off"
SHARED_WINDOW = 5.0        # seconds: two 429s of different keys this close = evidence of one project
SHARED_SCORE = 2           # points from which a pair is reported as sharing a project
INVALID_FOR = 6 * 3600.0   # a rejected key is not asked again for this long (or until a Test passes)
PACE_WINDOW = 60.0         # seconds of the requests-per-minute window
# free-tier requests per minute (first match wins); used only for a pair that already hit a per-minute 429, so a
# paid key (far higher limits) is never slowed down by these numbers
DEFAULT_RPM: tuple[tuple[str, int], ...] = (
    ("flash-lite", 15),
    ("2.0-flash", 15),
    ("gemma", 30),
    ("pro", 5),
    ("flash", 10),
    ("", 10),
)
_RETRY_DELAY = re.compile(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"')


def fingerprint(key: str) -> str:
    return hashlib.sha256((key or "").strip().encode("utf-8")).hexdigest()[:12]


def is_daily(msg: str, raw: str = "") -> bool:
    """The 429 says the daily quota is used up (``...PerDay...`` quota id), not a per-minute limit."""
    text = f"{msg} {raw}".lower()
    return "perday" in text.replace(" ", "").replace("_", "") or "per day" in text or "requests_per_day" in text


def retry_delay(raw: str) -> float | None:
    m = _RETRY_DELAY.search(raw or "")
    return max(5.0, min(3600.0, float(m.group(1)) + 1)) if m else None


def rpm_for(model: str) -> int:
    m = (model or "").lower()
    for pattern, n in DEFAULT_RPM:
        if pattern in m:
            return n
    return DEFAULT_RPM[-1][1]


def is_bad_key(status: int, msg: str) -> bool:
    """Google rejected the key itself (not the model or the request)."""
    low = (msg or "").lower()
    if status == 401:
        return True
    return status in (400, 403) and ("api key" in low or "api_key" in low) and (
        "not valid" in low or "invalid" in low or "expired" in low or "leaked" in low or "suspended" in low)


class KeyPool:
    """Thread-safe. ``path`` None keeps the state in memory only (tests)."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self.clock = clock
        self.disabled_fn: Callable[[], Iterable[str]] = lambda: ()    # fingerprints switched off in Settings
        self.used_fn: Callable[[str, str], int] | None = None         # (model, key) -> requests that worked today
        self.limit_fn: Callable[[str], int] | None = None             # model -> requests per day (0 = unknown)
        self.reset_fn: Callable[[float], float] | None = None         # now -> seconds until the daily reset
        self._lock = threading.RLock()
        self._slots: dict[tuple[str, str], tuple[str, float, str]] = {}   # (model, fp) -> (state, until, why)
        self._invalid: dict[str, tuple[float, str]] = {}                  # fp -> (until, why)
        self._pairs: dict[str, int] = {}                                  # "fpA|fpB" (sorted) -> score
        self._last429: dict[tuple[str, str], float] = {}
        self._sent: dict[tuple[str, str], deque[float]] = {}            # (model, fp) -> request times (memory)
        self._paced: set[tuple[str, str]] = set()                        # pairs that hit a per-minute 429
        self._turn = 0
        self.rev = 0
        self._load()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:  # noqa: BLE001 - a damaged file only loses the remembered states
            log.warning("key state file unreadable (%s); starting fresh", e)
            return
        now = self.clock()
        if not isinstance(d, dict):
            return
        for slot, until in (d.get("exhausted") or {}).items():
            if isinstance(slot, str) and "|" in slot and isinstance(until, (int, float)) and until > now:
                model, fp = slot.rsplit("|", 1)
                self._slots[(model, fp)] = (EXHAUSTED, float(until), "daily quota")
        for fp, until in (d.get("invalid") or {}).items():
            if isinstance(fp, str) and isinstance(until, (int, float)) and until > now:
                self._invalid[fp] = (float(until), "rejected by Google")
        for pair, score in (d.get("pairs") or {}).items():
            if isinstance(pair, str) and isinstance(score, int) and not isinstance(score, bool):
                self._pairs[pair] = max(-3, min(5, score))

    def _save(self) -> None:
        if self.path is None:
            return
        with self._lock:
            now = self.clock()
            data = {
                "exhausted": {f"{m}|{fp}": until for (m, fp), (st, until, _w) in self._slots.items()
                              if st == EXHAUSTED and until > now},
                "invalid": {fp: until for fp, (until, _w) in self._invalid.items() if until > now},
                "pairs": dict(self._pairs),
            }
            # written under the lock too: two threads saving at once must not leave the older state on disk
            tmp = self.path.with_name(f"{self.path.stem}.{threading.get_ident()}.tmp")
            try:
                tmp.write_text(json.dumps(data), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as e:
                log.debug("key state not saved: %s", e)
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ state
    def _disabled(self) -> set[str]:
        try:
            return {str(x) for x in self.disabled_fn() or ()}
        except Exception:  # noqa: BLE001
            return set()

    def enabled(self, keys: list[str]) -> list[str]:
        off = self._disabled()
        return [k for k in keys if k and fingerprint(k) not in off]

    def slot(self, model: str, key: str) -> tuple[str, float, str]:
        """(state, until, why) of one model x key now."""
        fp = fingerprint(key)
        now = self.clock()
        if fp in self._disabled():
            return OFF, 0.0, ""
        with self._lock:
            inv = self._invalid.get(fp)
            if inv is not None and inv[0] > now:
                return INVALID, inv[0], inv[1]
            st = self._slots.get((model, fp))
            if (st is None or st[1] <= now) and (model, fp) in self._paced:
                sent = self._sent.get((model, fp))
                while sent and now - sent[0] >= PACE_WINDOW:
                    sent.popleft()
                if sent and len(sent) >= rpm_for(model):    # its minute is full: rest until a request leaves it
                    return LIMITED, sent[0] + PACE_WINDOW, "requests per minute"
        if st is not None and st[1] > now:
            return st
        return OK, 0.0, ""

    def note_sent(self, model: str, key: str) -> None:
        """A request goes out through this model x key (for the requests-per-minute pacing)."""
        with self._lock:
            q = self._sent.setdefault((model, fingerprint(key)), deque(maxlen=128))
            q.append(self.clock())

    def available(self, model: str, key: str) -> bool:
        return self.slot(model, key)[0] == OK

    def order(self, keys: list[str], models: list[str]) -> list[tuple[str, str]]:
        """The model x key pairs to try for the next request: models in the user's order, the enabled keys of
        each rotated by one place per request, resting pairs left out."""
        keys = self.enabled(keys)
        if not keys:
            return []
        with self._lock:
            start = self._turn % len(keys)
            self._turn += 1
        turn = keys[start:] + keys[:start]
        return [(m, k) for m in models for k in turn if self.available(m, k)]

    def any_available(self, keys: list[str], models: list[str]) -> bool:
        keys = self.enabled(keys)
        return any(self.available(m, k) for m in models for k in keys)

    def next_free_at(self, keys: list[str], models: list[str]) -> float:
        """When the first resting pair can be asked again (0 = one can be asked now; inf = never: no keys)."""
        keys = self.enabled(keys)
        if not keys or not models:
            return float("inf")
        best = float("inf")
        for m in models:
            for k in keys:
                st, until, _w = self.slot(m, k)
                if st == OK:
                    return 0.0
                best = min(best, until)
        return best

    # ------------------------------------------------------------------ events
    def _set(self, model: str, key: str, state: str, seconds: float, why: str = "") -> None:
        with self._lock:
            self._slots[(model, fingerprint(key))] = (state, self.clock() + max(0.0, seconds), why)
            self.rev += 1

    def mark_ok(self, model: str, key: str) -> None:
        fp = fingerprint(key)
        now = self.clock()
        changed = False
        with self._lock:
            if self._slots.pop((model, fp), None) is not None:
                self.rev += 1
            if self._invalid.pop(fp, None) is not None:
                self.rev += 1
                changed = True
            # another key got 429 for this model a moment ago but this one works: separate quotas
            for (m, other), at in list(self._last429.items()):
                if m == model and other != fp and now - at <= SHARED_WINDOW:
                    changed = self._score(fp, other, -1) or changed
        if changed:
            self._save()

    def cooldown(self, model: str, key: str, seconds: float, why: str = "") -> None:
        self._set(model, key, COOLING, seconds, why)

    def mark_invalid(self, key: str, why: str = "") -> None:
        with self._lock:
            self._invalid[fingerprint(key)] = (self.clock() + INVALID_FOR, why[:160])
            self.rev += 1
        self._save()

    def mark_limited(self, model: str, key: str, msg: str = "", raw: str = "") -> float:
        """HTTP 429. Returns how long this model x key now rests."""
        fp = fingerprint(key)
        now = self.clock()
        daily = is_daily(msg, raw)
        if daily:
            wait = self._reset_in(now)
            state, why = EXHAUSTED, "daily quota"
        else:
            wait = retry_delay(raw) or 90.0
            state, why = LIMITED, "rate limit"
        partners: list[str] = []
        scored = False
        with self._lock:
            if not daily:
                self._paced.add((model, fp))      # a free-tier minute limit: pace this pair from now on
            self._slots[(model, fp)] = (state, now + wait, why)
            for (m, other), at in list(self._last429.items()):
                if m == model and other != fp and now - at <= SHARED_WINDOW:
                    scored = self._score(fp, other, +1) or scored
                if now - at > 600:
                    del self._last429[(m, other)]
            self._last429[(model, fp)] = now
            if daily:
                partners = self.partners(fp)
                for p in partners:     # one project: its other keys have no quota left either
                    cur = self._slots.get((model, p))
                    if cur is None or cur[0] != EXHAUSTED or cur[1] < now + wait:
                        self._slots[(model, p)] = (EXHAUSTED, now + wait, "daily quota (shared project)")
            self.rev += 1
        if daily or scored:
            self._save()
        return wait

    def _reset_in(self, now: float) -> float:
        if self.reset_fn is not None:
            try:
                return float(self.reset_fn(now))
            except Exception:  # noqa: BLE001
                pass
        from dualyrics import usage

        return usage.quota_reset_in(now)

    def reset(self, keep_exhausted: bool = True) -> None:
        """Forget rate limits, cool-downs and rejected keys (a Test passed, the user asked to translate
        again). Daily quotas stay until their reset unless ``keep_exhausted`` is False."""
        with self._lock:
            self._slots = {s: v for s, v in self._slots.items() if keep_exhausted and v[0] == EXHAUSTED}
            self._invalid.clear()
            self.rev += 1
        self._save()

    # ------------------------------------------------------------------ shared projects
    @staticmethod
    def _pair(a: str, b: str) -> str:
        return "|".join(sorted((a, b)))

    def _score(self, a: str, b: str, delta: int) -> bool:
        p = self._pair(a, b)
        old = self._pairs.get(p, 0)
        new = max(-3, min(5, old + delta))
        if new == old:
            return False
        self._pairs[p] = new
        self.rev += 1
        if (old >= SHARED_SCORE) != (new >= SHARED_SCORE):
            log.info("keys %s: %s", p, "share one project" if new >= SHARED_SCORE else "separate projects")
        return True

    def partners(self, fp: str) -> list[str]:
        with self._lock:
            out = []
            for p, score in self._pairs.items():
                if score >= SHARED_SCORE:
                    a, b = p.split("|")
                    if fp in (a, b):
                        out.append(b if a == fp else a)
            return out

    def shared_groups(self, keys: list[str]) -> list[list[int]]:
        """Groups (1-based positions in ``keys``) of keys that seem to share one Google project."""
        fps = [fingerprint(k) for k in keys]
        parent = list(range(len(fps)))

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        with self._lock:
            for i in range(len(fps)):
                for j in range(i + 1, len(fps)):
                    if fps[i] != fps[j] and self._pairs.get(self._pair(fps[i], fps[j]), 0) >= SHARED_SCORE:
                        parent[root(j)] = root(i)
                    elif fps[i] == fps[j]:
                        parent[root(j)] = root(i)        # the same key entered twice
        groups: dict[int, list[int]] = {}
        for i in range(len(fps)):
            groups.setdefault(root(i), []).append(i + 1)
        return [g for g in groups.values() if len(g) > 1]

    def forget_pairs(self) -> None:
        with self._lock:
            self._pairs.clear()
            self.rev += 1
        self._save()

    def evidence_daily(self, model: str, keys: list[str]) -> None:
        """Usage-based check when two keys ran out of the daily quota of ``model``: their requests today add up
        to about one key's limit when they share a project, and to about two limits when they do not."""
        if self.used_fn is None or self.limit_fn is None:
            return
        try:
            limit = int(self.limit_fn(model))
        except Exception:  # noqa: BLE001
            return
        if limit <= 0:
            return
        done = [k for k in keys if self.slot(model, k)[0] == EXHAUSTED]
        changed = False
        for i, a in enumerate(done):
            for b in done[i + 1:]:
                try:
                    total = int(self.used_fn(model, a)) + int(self.used_fn(model, b))
                except Exception:  # noqa: BLE001
                    continue
                with self._lock:
                    p = self._pair(fingerprint(a), fingerprint(b))
                    if total <= 1.4 * limit and self._pairs.get(p, 0) < SHARED_SCORE:
                        self._pairs[p] = SHARED_SCORE
                        changed = True
                    elif total >= 1.8 * limit and self._pairs.get(p, 0) > 0:
                        self._pairs[p] = 0
                        changed = True
        if changed:
            with self._lock:
                self.rev += 1
            self._save()

    # ------------------------------------------------------------------ for Settings
    def summary(self, keys: list[str], models: list[str]) -> list[dict[str, Any]]:
        """One entry per key: position, fingerprint, enabled, overall state (ok if any model can be asked, else
        rate_limited, exhausted, cooling, invalid or off), when it can be asked again, per-model states and the
        positions of keys it seems to share a project with."""
        groups = self.shared_groups(keys)
        out = []
        for i, key in enumerate(keys):
            fp = fingerprint(key)
            states = {m: self.slot(m, key) for m in models}
            kinds = [s[0] for s in states.values()]
            if not kinds:
                overall, until = OK, 0.0
            elif OFF in kinds:
                overall, until = OFF, 0.0
            elif INVALID in kinds:
                overall, until = INVALID, max(s[1] for s in states.values())
            elif OK in kinds:
                overall, until = OK, 0.0
            else:
                for kind in (LIMITED, COOLING, EXHAUSTED):
                    if kind in kinds:
                        overall = kind
                        break
                until = min(s[1] for s in states.values())
            shared = next((g for g in groups if i + 1 in g), [])
            out.append({"index": i + 1, "fp": fp, "enabled": overall != OFF, "state": overall, "until": until,
                        "models": {m: s[0] for m, s in states.items()},
                        "shared_with": [x for x in shared if x != i + 1]})
        return out
