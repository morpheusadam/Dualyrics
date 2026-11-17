"""Gemini usage meter: every Gemini request (translation text, speech, Live audio) and every cache hit, in
``%APPDATA%\\Dualyrics\\usage.sqlite``.

Each row holds the model, which key it used (its position in Settings, never the key itself), the kind of
request (``text`` / ``tts`` / ``live``, or ``cache`` for a translation served from the cache at no cost), the
token counts Google reports in ``usageMetadata`` (prompt, output, total), seconds of audio for speech and Live,
whether it worked, the HTTP status (429 = rate limit or quota) and the song or video it was for.

Free-tier limits are requests per day per model and per key (project). Google resets them at midnight Pacific
time, so the "quota day" of a request is its date in Pacific time; totals and the chart use local dates. The
default limits are estimates of the published free tier and can be edited in Settings > Usage, because Google
changes them from time to time.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)

KINDS = ("text", "tts", "live", "cache")
WARN_AT = 0.8
KEEP_DAYS = 400
# Free-tier requests per day, by model id pattern (first match wins). Estimates, see the module docstring and
# https://ai.google.dev/gemini-api/docs/rate-limits ; editable per model in Settings > Usage.
DEFAULT_LIMITS: tuple[tuple[str, int], ...] = (
    ("tts", 15),
    ("native-audio", 1000),
    ("live", 1000),
    ("flash-lite", 1000),
    ("2.0-flash", 200),
    ("pro", 100),
    ("flash", 250),
    ("", 100),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    day TEXT NOT NULL,
    qday TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    key_idx INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    audio_sec REAL NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL DEFAULT 1,
    status INTEGER NOT NULL DEFAULT 200,
    media TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS calls_day ON calls(day);
CREATE INDEX IF NOT EXISTS calls_qday ON calls(qday, model, key_idx);
"""


# ---------------------------------------------------------------- dates
def _nth_sunday(year: int, month: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    first = d + dt.timedelta(days=(6 - d.weekday()) % 7)
    return first + dt.timedelta(weeks=n - 1)


def pacific_offset(ts: float) -> dt.timedelta:
    """UTC offset of US Pacific time at ``ts`` (PDT from the second Sunday of March 2:00 to the first Sunday of
    November 2:00, else PST). No tz database needed (Windows Python has none without ``tzdata``)."""
    utc = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
    y = utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(10))   # 2:00 PST = 10:00 UTC
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(9))     # 2:00 PDT = 09:00 UTC
    return dt.timedelta(hours=-7 if start <= utc < end else -8)


def quota_day(ts: float) -> str:
    """The free-tier day of ``ts`` (Google resets the daily limits at midnight Pacific time)."""
    return (dt.datetime.fromtimestamp(ts, dt.timezone.utc) + pacific_offset(ts)).strftime("%Y-%m-%d")


def local_day(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def quota_reset_in(ts: float) -> float:
    """Seconds from ``ts`` until the daily limits reset (next midnight Pacific time)."""
    off = pacific_offset(ts)
    pac = dt.datetime.fromtimestamp(ts, dt.timezone.utc) + off
    midnight = (pac + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60.0, (midnight - pac).total_seconds())


def quota_reset_local(ts: float) -> str:
    """Local wall-clock time (HH:MM) of the next reset, for the Usage page."""
    return dt.datetime.fromtimestamp(ts + quota_reset_in(ts)).strftime("%H:%M")


def period_start(period: str, ts: float) -> str:
    """First local day of ``today`` / ``week`` (last 7 days) / ``month`` (this calendar month)."""
    d = dt.datetime.fromtimestamp(ts).date()
    if period == "week":
        d -= dt.timedelta(days=6)
    elif period == "month":
        d = d.replace(day=1)
    return d.strftime("%Y-%m-%d")


# ---------------------------------------------------------------- limits
def default_limit(model: str) -> int:
    m = (model or "").lower()
    for pattern, n in DEFAULT_LIMITS:
        if pattern in m:
            return n
    return DEFAULT_LIMITS[-1][1]


def limit_for(model: str, limits: dict[str, int] | None) -> int:
    """Requests per day for ``model``: the user's value, else the estimate. 0 = no limit known (no bar)."""
    if limits and model in limits:
        return max(0, int(limits[model]))
    return default_limit(model)


def validate_limits(raw: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in list(raw.items())[:60]:
            if isinstance(k, str) and 0 < len(k) <= 80 and isinstance(v, (int, float)) and not isinstance(v, bool):
                if v == v:
                    out[k] = int(max(0, min(1_000_000, v)))
    return out


def parse_usage(meta: Any) -> tuple[int, int, int]:
    """(prompt, output, total) tokens from a Gemini ``usageMetadata`` object (missing parts = 0). Live sends
    ``responseTokenCount`` instead of ``candidatesTokenCount``."""
    if not isinstance(meta, dict):
        return 0, 0, 0

    def num(*names: str) -> int:
        for n in names:
            v = meta.get(n)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return 0

    p = num("promptTokenCount")
    o = num("candidatesTokenCount", "responseTokenCount") + num("thoughtsTokenCount")
    t = num("totalTokenCount") or p + o
    return p, o, t


# ---------------------------------------------------------------- the meter
class UsageMeter:
    """Thread-safe; every method can be called from any thread. A broken database never breaks the app: the
    meter logs once and keeps counting in memory (``ok`` is False)."""

    def __init__(self, path: Path | None = None, clock: Callable[[], float] = time.time) -> None:
        if path is None:
            from dualyrics import paths

            path = paths.data_dir() / "usage.sqlite"
        self.path = path
        self.clock = clock
        self.media_fn: Callable[[], str] = lambda: ""
        self.limits_fn: Callable[[], dict[str, int]] = lambda: {}
        self.on_warning: Callable[[str, int, int, int], None] | None = None   # (model, key #, used, limit)
        self.rev = 0
        self._lock = threading.Lock()
        self._warned: set[tuple[str, str, int]] = set()
        self.ok = True
        try:
            self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error as e:
            log.error("usage database unavailable (%s); counting in memory only", e)
            self.ok = False
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
            self._db.executescript(_SCHEMA)

    # -- writing
    def record(self, model: str, key_idx: int, kind: str, usage: Any = None, ok: bool = True, status: int = 200,
               audio_sec: float = 0.0, media: str | None = None) -> None:
        """One Gemini request. ``usage`` is the response's ``usageMetadata`` (or a (prompt, output, total)
        tuple); ``key_idx`` is the key's position in Settings (1 = first)."""
        if kind not in KINDS:
            kind = "text"
        p, o, t = usage if isinstance(usage, tuple) else parse_usage(usage)
        ts = self.clock()
        if media is None:
            try:
                media = self.media_fn() or ""
            except Exception:
                media = ""
        row = (ts, local_day(ts), quota_day(ts), (model or "")[:80], int(key_idx), kind, int(p), int(o), int(t),
               round(float(audio_sec), 3), 1 if ok else 0, int(status), (media or "")[:200])
        with self._lock:
            try:
                self._db.execute("INSERT INTO calls (ts, day, qday, model, key_idx, kind, prompt_tokens, "
                                 "output_tokens, total_tokens, audio_sec, ok, status, media) "
                                 "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                self._db.commit()
            except sqlite3.Error as e:
                log.warning("usage not recorded: %s", e)
            self.rev += 1
        if ok and kind != "cache" and model:
            self._check(model, int(key_idx))

    def cache_hit(self, kind: str = "text", media: str | None = None) -> None:
        """A translation (or a spoken line) served from the cache: counted, at no cost."""
        self.record("", 0, "cache", (0, 0, 0), True, 200, 0.0, media)

    def _check(self, model: str, key_idx: int) -> None:
        limit = limit_for(model, self._limits())
        if limit <= 0 or self.on_warning is None:
            return
        used = self.used_today(model, key_idx)
        qd = quota_day(self.clock())
        mark = (qd, model, key_idx)
        if used >= WARN_AT * limit and mark not in self._warned:
            self._warned.add(mark)
            try:
                self.on_warning(model, key_idx, used, limit)
            except Exception:
                log.debug("usage warning callback failed", exc_info=True)

    def _limits(self) -> dict[str, int]:
        try:
            return self.limits_fn() or {}
        except Exception:
            return {}

    def reset(self) -> None:
        with self._lock:
            try:
                self._db.execute("DELETE FROM calls")
                self._db.commit()
            except sqlite3.Error as e:
                log.warning("usage reset failed: %s", e)
            self._warned.clear()
            self.rev += 1

    def prune(self, keep_days: int = KEEP_DAYS) -> None:
        cut = self.clock() - keep_days * 86400
        with self._lock:
            try:
                self._db.execute("DELETE FROM calls WHERE ts < ?", (cut,))
                self._db.commit()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass

    # -- reading
    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[tuple[Any, ...]]:
        with self._lock:
            try:
                return list(self._db.execute(sql, tuple(args)))
            except sqlite3.Error as e:
                log.warning("usage query failed: %s", e)
                return []

    def used_today(self, model: str, key_idx: int) -> int:
        """Requests that worked today (quota day) for one model and key: what counts against the limit."""
        qd = quota_day(self.clock())
        r = self._rows("SELECT COUNT(*) FROM calls WHERE qday=? AND model=? AND key_idx=? AND ok=1 AND kind!='cache'",
                       (qd, model, key_idx))
        return int(r[0][0]) if r else 0

    def quota_today(self) -> list[dict[str, Any]]:
        """Per (model, key) for today's quota day: requests used, 429s, the limit and the share used."""
        qd = quota_day(self.clock())
        limits = self._limits()
        out = []
        for model, key_idx, used, limited in self._rows(
                "SELECT model, key_idx, SUM(ok), SUM(CASE WHEN status=429 THEN 1 ELSE 0 END) FROM calls "
                "WHERE qday=? AND kind!='cache' AND model!='' GROUP BY model, key_idx ORDER BY model, key_idx", (qd,)):
            limit = limit_for(model, limits)
            out.append({"model": model, "key": int(key_idx), "used": int(used or 0), "limited": int(limited or 0),
                        "limit": limit, "share": (int(used or 0) / limit) if limit else 0.0})
        return out

    def totals(self, period: str = "today") -> dict[str, Any]:
        since = period_start(period, self.clock())
        r = self._rows(
            "SELECT SUM(kind!='cache'), SUM(ok=1 AND kind!='cache'), SUM(ok=0 AND kind!='cache'), "
            "SUM(status=429), SUM(prompt_tokens), SUM(output_tokens), SUM(total_tokens), SUM(audio_sec), "
            "SUM(kind='cache') FROM calls WHERE day>=?", (since,))
        vals = [v or 0 for v in (r[0] if r else ())] or [0] * 9
        keys = ("requests", "ok", "errors", "rate_limited", "prompt", "output", "total", "audio_sec", "cache_hits")
        out = {k: (round(float(v), 1) if k == "audio_sec" else int(v)) for k, v in zip(keys, vals)}
        return out

    def _grouped(self, column: str, period: str) -> list[dict[str, Any]]:
        since = period_start(period, self.clock())
        rows = self._rows(
            f"SELECT {column}, SUM(kind!='cache'), SUM(total_tokens), SUM(audio_sec), SUM(status=429), "
            f"SUM(kind='cache') FROM calls WHERE day>=? GROUP BY {column} ORDER BY SUM(total_tokens) DESC, "
            f"SUM(kind!='cache') DESC", (since,))
        return [{"name": name, "requests": int(req or 0), "tokens": int(tok or 0), "audio_sec": round(float(a or 0), 1),
                 "rate_limited": int(lim or 0), "cache_hits": int(c or 0)} for name, req, tok, a, lim, c in rows]

    def by_model(self, period: str = "today") -> list[dict[str, Any]]:
        return [r for r in self._grouped("model", period) if r["name"]]

    def by_key(self, period: str = "today") -> list[dict[str, Any]]:
        return [r for r in self._grouped("key_idx", period) if r["name"]]

    def by_media(self, period: str = "month", limit: int = 15) -> list[dict[str, Any]]:
        return [r for r in self._grouped("media", period) if r["name"]][:limit]

    def daily(self, days: int = 14) -> list[dict[str, Any]]:
        """One entry per local day (oldest first, zeros included): requests, tokens, audio seconds."""
        now = self.clock()
        today = dt.datetime.fromtimestamp(now).date()
        first = today - dt.timedelta(days=days - 1)
        got = {d: (int(r or 0), int(t or 0), float(a or 0)) for d, r, t, a in self._rows(
            "SELECT day, SUM(kind!='cache'), SUM(total_tokens), SUM(audio_sec) FROM calls WHERE day>=? GROUP BY day",
            (first.strftime("%Y-%m-%d"),))}
        out = []
        for i in range(days):
            d = (first + dt.timedelta(days=i)).strftime("%Y-%m-%d")
            r, t, a = got.get(d, (0, 0, 0.0))
            out.append({"day": d, "requests": r, "tokens": t, "audio_sec": round(a, 1)})
        return out

    def export_csv(self, path: Path) -> int:
        """All rows as CSV (UTF-8 with BOM, so Excel shows Persian titles). Returns the number of rows."""
        rows = self._rows("SELECT ts, day, qday, model, key_idx, kind, prompt_tokens, output_tokens, total_tokens, "
                          "audio_sec, ok, status, media FROM calls ORDER BY ts")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["time", "local_day", "quota_day_pacific", "model", "key", "kind", "prompt_tokens",
                        "output_tokens", "total_tokens", "audio_seconds", "ok", "http_status", "song_or_video"])
            for ts, *rest in rows:
                w.writerow([dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds"), *rest])
        return len(rows)
