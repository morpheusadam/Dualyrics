"""Resumable downloads through the app's HTTP session (so the proxy setting applies).

``fetch`` writes to ``<dest>.part`` and continues it with an HTTP ``Range`` request after a broken connection,
retrying a few times with a growing pause before it gives up (the ``.part`` file stays, so the next attempt
continues too). The finished file is checked (size, sha256 when known) before it is renamed to ``dest``, so a
half-finished download never looks complete.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from dualyrics import net

log = logging.getLogger(__name__)

CHUNK = 1 << 20
ATTEMPTS = 6
PAUSE = 2.0                 # s before the first retry, doubled each time (at most 30 s)


class DownloadCancelled(Exception):
    pass


class DownloadError(OSError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _once(session: Any, url: str, part: Path, progress: Callable[[int, int], None],
          cancelled: Callable[[], bool]) -> int:
    """One request, appended to ``part``. Returns the total size the server reported (0 = unknown)."""
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with session.get(url, headers=headers, stream=True, timeout=net.TIMEOUT_SLOW, allow_redirects=True) as r:
        if r.status_code == 416:                 # nothing left to fetch: complete (checked by the caller)
            return have
        r.raise_for_status()
        if have and r.status_code != 206:        # the server ignored the range: start over
            have = 0
        size = int(r.headers.get("Content-Length") or 0)
        total = have + size if size else 0
        done = have
        with open(part, "ab" if have else "wb") as fh:
            progress(done, total)
            for chunk in r.iter_content(CHUNK):
                if cancelled():
                    raise DownloadCancelled()
                if chunk:
                    fh.write(chunk)
                    done += len(chunk)
                    progress(done, total)
        if total and done < total:
            raise DownloadError(f"connection closed early ({done} of {total} bytes)")
        return total


def fetch(session: Any, url: str, dest: Path, progress: Callable[[int, int], None] = lambda d, t: None,
          cancelled: Callable[[], bool] = lambda: False, size: int = 0, sha256: str = "",
          attempts: int = ATTEMPTS, sleep: Callable[[float], None] = time.sleep) -> Path:
    """Download ``url`` to ``dest`` (blocking), resuming and retrying broken transfers. ``size`` and ``sha256``
    are checked when given. Returns ``dest``."""
    dest = Path(dest)
    if dest.is_file() and (not size or dest.stat().st_size == size) and (
            not sha256 or sha256_of(dest) == sha256.lower()):
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    pause = PAUSE
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        if cancelled():
            raise DownloadCancelled()
        if size and part.exists() and part.stat().st_size > size:
            part.unlink()                         # longer than the file can be: not resumable
        try:
            total = _once(session, url, part, progress, cancelled)
        except DownloadCancelled:
            raise
        except Exception as e:  # noqa: BLE001 - network errors are retried, others after one more try
            last = e
            if not net.is_network_error(e) and not isinstance(e, DownloadError) and attempt:
                break
            log.info("download %s: %s (attempt %d, resuming)", dest.name, e, attempt + 1)
            sleep(pause)
            pause = min(30.0, pause * 2)
            continue
        got = part.stat().st_size if part.exists() else 0
        want = size or total
        if want and got < want:
            last = DownloadError(f"{dest.name}: got {got} of {want} bytes")
            sleep(pause)
            pause = min(30.0, pause * 2)
            continue
        if (want and got != want) or (sha256 and sha256_of(part) != sha256.lower()):
            part.unlink(missing_ok=True)          # complete but wrong: never resume onto it
            raise DownloadError(f"{dest.name}: damaged download (size or checksum mismatch)")
        os.replace(part, dest)
        return dest
    raise DownloadError(f"{dest.name}: download failed ({last})")
