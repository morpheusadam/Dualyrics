r"""Translations of single lines on disk (Live mode and on-screen subtitles), so a replayed video, a rewatched
episode or a game's repeated lines cost no request at all, also after a restart.

``%APPDATA%\Dualyrics\cache\lines.sqlite``: one row per (normalized line, target language) with the translation,
the translator that made it (``gemini`` > ``free`` > ``offline``; a better one replaces a worse one, never the
other way round) and when it was last used. The English pivot of on-screen text is kept the same way under the
target ``>en`` with the detected language. At most ``MAX_ROWS`` rows: the ones used longest ago go first.

Whole songs keep their own cache (``cache.py``, one file per song, target language and translator).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ROWS = 60000
RANK = {"gemini": 3, "free": 2, "offline": 1}
PIVOT_TARGET = ">en"
TOUCH_EVERY = 3600.0      # s: a cache hit updates the row's "last used" time at most this often
_SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
    k TEXT NOT NULL,
    target TEXT NOT NULL,
    tr TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    PRIMARY KEY (k, target)
);
CREATE INDEX IF NOT EXISTS lines_ts ON lines(ts);
"""


class LineStore:
    """Thread-safe; a broken database never breaks translation (it then works like an empty cache)."""

    def __init__(self, path: Path | None = None, max_rows: int = MAX_ROWS, clock=time.time) -> None:  # noqa: ANN001
        if path is None:
            from dualyrics import paths

            path = paths.cache_dir() / "lines.sqlite"
        self.path = path
        self.max_rows = max_rows
        self.clock = clock
        self._lock = threading.Lock()
        self._writes = 0
        try:
            self._db: sqlite3.Connection | None = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
            self._db.executescript(_SCHEMA)
            self._db.commit()
        except sqlite3.Error as e:
            log.warning("line cache unavailable: %s", e)
            self._db = None

    def get(self, key: str, target: str) -> tuple[str, str, str] | None:
        """(translation, provider, extra) or None."""
        if self._db is None or not key:
            return None
        with self._lock:
            try:
                row = self._db.execute("SELECT tr, provider, extra, ts FROM lines WHERE k=? AND target=?",
                                       (key, target)).fetchone()
                now = self.clock()
                if row is not None and now - float(row[3]) > TOUCH_EVERY:
                    # "last used" only orders the pruning: a disk write per hit is not needed for that
                    self._db.execute("UPDATE lines SET ts=? WHERE k=? AND target=?", (now, key, target))
                    self._db.commit()
            except sqlite3.Error as e:
                log.debug("line cache read failed: %s", e)
                return None
        return (str(row[0]), str(row[1]), str(row[2])) if row else None

    def put(self, key: str, target: str, translation: str, provider: str, extra: str = "") -> None:
        if self._db is None or not key or not translation:
            return
        with self._lock:
            try:
                row = self._db.execute("SELECT provider FROM lines WHERE k=? AND target=?", (key, target)).fetchone()
                if row is not None and RANK.get(str(row[0]), 0) > RANK.get(provider, 0):
                    return                     # a better translation is kept
                self._db.execute("INSERT OR REPLACE INTO lines (k, target, tr, provider, extra, ts) "
                                 "VALUES (?,?,?,?,?,?)", (key, target, translation, provider, extra, self.clock()))
                self._db.commit()
                self._writes += 1
                if self._writes % 500 == 0:
                    self._prune()
            except sqlite3.Error as e:
                log.debug("line cache write failed: %s", e)

    def _prune(self) -> None:
        assert self._db is not None
        n = self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0]
        if n > self.max_rows:
            self._db.execute("DELETE FROM lines WHERE rowid IN (SELECT rowid FROM lines ORDER BY ts LIMIT ?)",
                             (n - self.max_rows,))
            self._db.commit()

    def prune(self) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                self._prune()
            except sqlite3.Error:
                pass

    def count(self) -> int:
        if self._db is None:
            return 0
        with self._lock:
            try:
                return int(self._db.execute("SELECT COUNT(*) FROM lines").fetchone()[0])
            except sqlite3.Error:
                return 0

    def clear(self) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                self._db.execute("DELETE FROM lines")
                self._db.commit()
            except sqlite3.Error as e:
                log.warning("line cache not cleared: %s", e)

    def close(self) -> None:
        if self._db is None:
            return
        with self._lock:
            try:
                self._db.close()
            except sqlite3.Error:
                pass
            self._db = None
