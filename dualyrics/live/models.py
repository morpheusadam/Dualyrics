r"""Whisper models for Live mode: catalogue, location and first-use download.

Models are the CTranslate2 conversions published by Systran on Hugging Face (the ones faster-whisper
uses). They are downloaded with the app's own HTTP session, so the proxy setting applies, into
%APPDATA%\Dualyrics\models\faster-whisper-<name>. Downloads resume after a broken connection.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dualyrics import net, paths

log = logging.getLogger(__name__)

HF = "https://huggingface.co"
REPO = "Systran/faster-whisper-{name}"
NEEDED = ("config.json", "model.bin", "tokenizer.json")       # plus vocabulary.txt or vocabulary.json
OPTIONAL = ("preprocessor_config.json",)
CHUNK = 1 << 20
MIN_MODEL_BIN = 30_000_000       # the smallest model.bin (tiny) is ~75 MB; less is an unfinished file
_KNOWN = set(NEEDED) | set(OPTIONAL) | {"vocabulary.txt", "vocabulary.json"}


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size_mb: int
    english_only: bool

    @property
    def repo(self) -> str:
        return REPO.format(name=self.name)


MODELS: dict[str, ModelInfo] = {m.name: m for m in (
    ModelInfo("tiny", 75, False), ModelInfo("tiny.en", 75, True),
    ModelInfo("base", 145, False), ModelInfo("base.en", 145, True),
    ModelInfo("small", 484, False), ModelInfo("small.en", 484, True),
    ModelInfo("medium", 1530, False), ModelInfo("medium.en", 1530, True),
)}
SIZES = ("tiny", "base", "small", "medium")


class DownloadCancelled(Exception):
    pass


def model_name(size: str, english_only: bool) -> str:
    size = size if size in SIZES else "small"
    return f"{size}.en" if english_only else size


def models_dir() -> Path:
    p = paths.data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def model_path(name: str) -> Path:
    return models_dir() / f"faster-whisper-{name}"


def is_installed(name: str) -> bool:
    """The model finished downloading: the ``.complete`` marker (written last) exists, every needed file is
    there and, when the marker lists sizes (v2.2), every file still has its size. A killed download, a
    truncated file or an emptied folder never counts as installed (the idea of LiveTranslate's
    ``_hf_repo_complete``: files that do not resolve or are too small mean an unfinished cache)."""
    p = model_path(name)
    try:
        mark = json.loads((p / ".complete").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not all((p / f).is_file() for f in NEEDED) or not (
            (p / "vocabulary.txt").is_file() or (p / "vocabulary.json").is_file()):
        return False
    sizes = mark.get("sizes") if isinstance(mark, dict) else None
    try:
        if isinstance(sizes, dict):
            return all((p / f).stat().st_size == int(n) for f, n in sizes.items()
                       if f in _KNOWN)
        return (p / "model.bin").stat().st_size >= MIN_MODEL_BIN          # v2.1 marker: no sizes
    except (OSError, ValueError, TypeError):
        return False


def installed_models() -> list[str]:
    return [n for n in MODELS if is_installed(n)]


@dataclass(frozen=True)
class RemoteFile:
    name: str
    size: int = 0          # bytes (0 = unknown)
    sha256: str = ""       # LFS files (model.bin): sha256 of the content, from the Hub
    git_oid: str = ""      # small files: git blob id = sha1("blob <size>\0" + content)


def _file_list(session: Any, info: ModelInfo) -> list[RemoteFile]:
    """The files to fetch with their sizes and checksums, from the Hub API; a fixed list when the API fails."""
    try:
        r = session.get(f"{HF}/api/models/{info.repo}/tree/main", timeout=net.TIMEOUT_FAST)
        r.raise_for_status()
        want = set(NEEDED) | set(OPTIONAL) | {"vocabulary.txt", "vocabulary.json"}
        out: list[RemoteFile] = []
        for it in r.json():
            if it.get("type") != "file" or it.get("path") not in want:
                continue   # only known names: a file name from the network never becomes a path otherwise
            lfs = it.get("lfs") if isinstance(it.get("lfs"), dict) else {}
            sha = str(lfs.get("oid") or lfs.get("sha256") or "").lower()
            oid = str(it.get("oid") or "").lower()
            out.append(RemoteFile(it["path"], int(lfs.get("size") or it.get("size") or 0),
                                  sha if _HEX64.match(sha) else "",
                                  oid if not lfs and _HEX40.match(oid) else ""))
        if all(any(f.name == n for f in out) for n in NEEDED):
            return out
    except Exception as e:
        if net.is_network_error(e):
            raise
        log.info("model file list unavailable (%s); using the default list", e)
    log.warning("model %s: no checksums from the Hub, files are checked by size only", info.name)
    return [RemoteFile(f) for f in NEEDED] + [RemoteFile("vocabulary.txt")]


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _verify(path: Path, f: RemoteFile) -> str:
    """'' when ``path`` matches the size and checksum the Hub gave, else what is wrong."""
    size = path.stat().st_size
    if f.size and size != f.size:
        return f"{f.name}: got {size} of {f.size} bytes"
    if not (f.sha256 or f.git_oid):
        return ""
    h = hashlib.sha256() if f.sha256 else hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    if h.hexdigest() != (f.sha256 or f.git_oid):
        return f"{f.name}: checksum mismatch (damaged or changed download)"
    return ""


RETRIES = 6                 # attempts per file; a broken transfer resumes where it stopped (HTTP Range)
RETRY_PAUSE, RETRY_PAUSE_MAX = 2.0, 30.0
_sleep: Callable[[float], None] = time.sleep


def _wait(seconds: float, cancelled: Callable[[], bool]) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise DownloadCancelled()
        _sleep(min(0.25, max(0.0, end - time.monotonic())))


def _fetch_once(session: Any, url: str, part: Path, f: RemoteFile, done: int, total: int,
                progress: Callable[[int, int], None], cancelled: Callable[[], bool]) -> None:
    """One request for one file, appended to ``part`` (continued with a Range request)."""
    have = part.stat().st_size if part.exists() else 0
    if f.size and have > f.size:          # cannot be resumed: start this file again
        part.unlink()
        have = 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with session.get(url, headers=headers, stream=True, timeout=net.TIMEOUT_SLOW, allow_redirects=True) as r:
        if r.status_code == 416:          # already complete (checked by the caller)
            if not part.exists():
                raise OSError(f"{f.name}: nothing was downloaded")
            return
        r.raise_for_status()
        if have and r.status_code != 206:  # server ignored the range: start over
            have = 0
        with open(part, "ab" if have else "wb") as fh:
            done_file = have
            progress(done + done_file, total)
            for chunk in r.iter_content(CHUNK):
                if cancelled():
                    raise DownloadCancelled()
                if chunk:
                    fh.write(chunk)
                    done_file += len(chunk)
                    progress(done + done_file, total)
    if not part.exists():
        raise OSError(f"{f.name}: nothing was downloaded")


def download(name: str, session: Any, progress: Callable[[int, int], None] = lambda done, total: None,
             cancelled: Callable[[], bool] = lambda: False) -> Path:
    """Download model ``name`` (resumable, checked against the Hub's sizes and checksums). Calls
    ``progress(done_bytes, total_bytes)``. Returns its folder. Only one download per model runs at a time
    (Live mode and the Settings button share the same ``.part`` files)."""
    info = MODELS[name]
    with _locks_guard:
        lock = _locks.setdefault(name, threading.Lock())
    while not lock.acquire(timeout=0.5):
        if cancelled():
            raise DownloadCancelled()
    try:
        return _download_locked(info, session, progress, cancelled)
    finally:
        lock.release()


def _download_locked(info: ModelInfo, session: Any, progress: Callable[[int, int], None],
                     cancelled: Callable[[], bool]) -> Path:
    name = info.name
    dest = model_path(name)
    if is_installed(name):
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    pause = RETRY_PAUSE
    for attempt in range(RETRIES):
        try:
            files = _file_list(session, info)
            break
        except Exception as e:  # noqa: BLE001 - only network errors leave _file_list
            if attempt == RETRIES - 1:
                raise
            log.info("model %s: file list: %s (attempt %d)", name, e, attempt + 1)
            _wait(pause, cancelled)
            pause = min(RETRY_PAUSE_MAX, pause * 2)
    total = sum(f.size for f in files) or info.size_mb * 1024 * 1024
    done = 0
    for f in files:
        fname = f.name
        target = dest / fname
        if target.exists():
            if not _verify(target, f):
                done += target.stat().st_size
                progress(done, total)
                continue
            target.unlink()
        part = target.with_name(target.name + ".part")
        url = f"{HF}/{info.repo}/resolve/main/{fname}"
        pause = RETRY_PAUSE
        for attempt in range(RETRIES):
            try:
                _fetch_once(session, url, part, f, done, total, progress, cancelled)
                problem = _verify(part, f)
            except DownloadCancelled:
                raise
            except Exception as e:  # noqa: BLE001
                if not net.is_network_error(e) or attempt == RETRIES - 1:
                    raise
                log.info("model %s: %s: %s (attempt %d, resuming)", name, fname, e, attempt + 1)
            else:
                if not problem:
                    break
                short = bool(f.size) and part.exists() and part.stat().st_size < f.size
                if not short:
                    part.unlink(missing_ok=True)   # complete but wrong: never resume onto it
                    raise OSError(problem)
                if attempt == RETRIES - 1:
                    raise OSError(problem)        # a short file resumes next time
                log.info("model %s: %s (attempt %d, resuming)", name, problem, attempt + 1)
            _wait(pause, cancelled)               # a flaky connection or proxy: resume after a pause
            pause = min(RETRY_PAUSE_MAX, pause * 2)
        os.replace(part, target)
        done += target.stat().st_size
    sizes = {f.name: (dest / f.name).stat().st_size for f in files}
    (dest / ".complete").write_text(json.dumps({"repo": info.repo, "files": list(sizes), "sizes": sizes}),
                                    encoding="utf-8")
    log.info("model %s downloaded (%d MB)", name, done // (1024 * 1024))
    return dest

