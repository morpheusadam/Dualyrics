"""On-disk cache: lyrics per track, translations per (track, target language, provider), per-song offsets."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from dualyrics import paths

log = logging.getLogger(__name__)

NOT_FOUND_TTL = 24 * 3600   # retry songs without lyrics after a day
MAX_FILES = 4000


def track_key(title: str, artist: str) -> str:
    return f"{(artist or '').strip().lower()}|{(title or '').strip().lower()}"


def _h(*parts: str) -> str:
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


class Cache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or paths.cache_dir()
        self.lyrics_dir = self.root / "lyrics"
        self.trans_dir = self.root / "translations"
        for d in (self.lyrics_dir, self.trans_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._offsets_file = self.root / "offsets.json"
        self._lock = threading.Lock()
        self._offsets: dict[str, int] = self._read(self._offsets_file) or {}

    # -- low level
    @staticmethod
    def _read(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except Exception as e:
            log.warning("bad cache file %s: %s", path.name, e)
            try:
                path.unlink()
            except OSError:
                pass
            return None

    @staticmethod
    def _write(path: Path, data: Any) -> None:
        # unique temp name: two workers can write the same entry at once (reload while a load still runs)
        tmp = path.with_name(f"{path.stem}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as e:
            log.warning("cache write failed %s: %s", path.name, e)
            try:
                tmp.unlink()
            except OSError:
                pass

    # -- lyrics
    def get_lyrics(self, key: str) -> dict[str, Any] | None:
        d = self._read(self.lyrics_dir / f"{_h(key)}.json")
        if not isinstance(d, dict):
            return None
        if not d.get("found") and time.time() - d.get("at", 0) > NOT_FOUND_TTL:
            return None  # negative entry expired
        return d

    def put_lyrics(self, key: str, data: dict[str, Any]) -> None:
        self._write(self.lyrics_dir / f"{_h(key)}.json", {**data, "key": key, "at": time.time()})

    # -- translations (key includes target language and provider)
    def _tpath(self, key: str, target: str, provider: str) -> Path:
        return self.trans_dir / f"{_h(key, target, provider)}.json"

    def get_translation(self, key: str, target: str, provider: str) -> dict[str, Any] | None:
        d = self._read(self._tpath(key, target, provider))
        return d if isinstance(d, dict) and isinstance(d.get("lines"), list) else None

    def put_translation(self, key: str, target: str, provider: str, data: dict[str, Any]) -> None:
        self._write(self._tpath(key, target, provider), {**data, "key": key, "target": target,
                                                         "provider": provider, "at": time.time()})

    def drop_translation(self, key: str, target: str) -> None:
        for provider in ("gemini", "free", "none"):
            try:
                self._tpath(key, target, provider).unlink()
            except OSError:
                pass

    # -- per-song sync offsets (ms)
    def get_offset(self, key: str) -> int:
        with self._lock:
            return int(self._offsets.get(key, 0))

    def set_offset(self, key: str, ms: int) -> None:
        with self._lock:
            if ms:
                self._offsets[key] = int(ms)
            else:
                self._offsets.pop(key, None)
            if len(self._offsets) > 2000:
                for k in list(self._offsets)[: len(self._offsets) - 2000]:
                    del self._offsets[k]
            data = dict(self._offsets)
        self._write(self._offsets_file, data)

    # -- maintenance
    def clear(self) -> None:
        """Remove cached lyrics and translations (offsets are kept)."""
        for d in (self.lyrics_dir, self.trans_dir):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)

    def prune(self) -> None:
        """Keep the cache bounded: delete the oldest files beyond MAX_FILES per folder."""
        for d in (self.lyrics_dir, self.trans_dir):
            try:
                files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
                for p in files[: max(0, len(files) - MAX_FILES)]:
                    p.unlink()
            except OSError:
                pass
