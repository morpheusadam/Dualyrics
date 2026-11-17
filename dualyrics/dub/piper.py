r"""Piper: offline neural voices (optional download, about 20 MB for the program and 20-60 MB per voice).

Downloaded on request into ``%APPDATA%\Dualyrics\piper`` (``piper\piper.exe`` and ``voices\<name>.onnx``),
through the app's proxy setting. Text goes to ``piper.exe`` as UTF-8 on stdin and a WAV comes back.
"""

from __future__ import annotations

import logging
import re
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from dualyrics import paths

log = logging.getLogger(__name__)

PIPER_ZIP = "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_windows_amd64.zip"
VOICE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{path}{ext}"
CREATE_NO_WINDOW = 0x08000000
RUN_TIMEOUT = 60
_lock = threading.Lock()


class PiperError(Exception):
    pass


def root() -> Path:
    return paths.data_dir() / "piper"


def exe() -> Path:
    return root() / "piper" / "piper.exe"


def voice_file(path: str) -> Path:
    return root() / "voices" / (path.rsplit("/", 1)[-1] + ".onnx")


def _unpacking_marker() -> Path:
    return root() / ".unpacking"


def installed() -> bool:
    """piper.exe is there and its archive was unpacked to the end (a stopped unpack leaves the marker)."""
    return exe().is_file() and not _unpacking_marker().exists() and (exe().parent / "espeak-ng-data").is_dir()


def voice_installed(path: str) -> bool:
    f = voice_file(path)
    return f.is_file() and f.with_suffix(".onnx.json").is_file()


def _fetch(session: Any, url: str, dest: Path, progress: Callable[[int, int], None],
           cancelled: Callable[[], bool]) -> None:
    """Resumable download (dualyrics/download.py): a broken connection continues where it stopped."""
    from dualyrics import download

    try:
        download.fetch(session, url, dest, progress, cancelled)
    except download.DownloadCancelled:
        raise PiperError("cancelled") from None


def _safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    base = target.resolve()
    for m in zf.infolist():
        out = (target / m.filename).resolve()
        if base not in out.parents and out != base:
            raise PiperError(f"unsafe path in the Piper archive: {m.filename}")
    zf.extractall(target)


def install(session: Any, voice_path: str, progress: Callable[[int, int], None] = lambda d, t: None,
            cancelled: Callable[[], bool] = lambda: False) -> None:
    """Download Piper (once) and one voice. Blocking; call it from a worker thread."""
    with _lock:
        if not installed():
            z = root() / "piper_windows_amd64.zip"
            _fetch(session, PIPER_ZIP, z, progress, cancelled)
            mark = _unpacking_marker()
            mark.write_text("unpacking", encoding="utf-8")
            try:
                if not zipfile.is_zipfile(z):
                    raise PiperError("the Piper download is damaged")
                with zipfile.ZipFile(z) as zf:
                    _safe_extract(zf, root())
                mark.unlink(missing_ok=True)
            finally:
                z.unlink(missing_ok=True)
            if not installed():
                raise PiperError("piper.exe missing after the download")
        if not voice_installed(voice_path):
            f = voice_file(voice_path)
            _fetch(session, VOICE_URL.format(path=voice_path, ext=".onnx.json"), f.with_suffix(".onnx.json"),
                   lambda d, t: None, cancelled)
            _fetch(session, VOICE_URL.format(path=voice_path, ext=".onnx"), f, progress, cancelled)


_SHADDA = re.compile("([ء-يپچژکگی])ّ")


def prepare(text: str, lang: str) -> str:
    """Text as Piper reads it best. Persian: Piper's voice rushes letters with a shadda (ّ), so the doubled
    letter is written out (letter + sukun + letter), which it pronounces correctly."""
    t = " ".join((text or "").split())
    if lang.split("-")[0] in ("fa", "ar", "ur"):
        t = _SHADDA.sub(lambda m: m.group(1) + "ْ" + m.group(1), t)
    return t


def synthesize(text: str, voice_path: str, lang: str, rate: float = 1.0) -> bytes:
    """WAV bytes of ``text``. Raises PiperError."""
    if not installed() or not voice_installed(voice_path):
        raise PiperError("Piper or its voice is not downloaded")
    out = root() / "tmp" / f"say-{threading.get_ident()}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(exe()), "--model", str(voice_file(voice_path)), "--output_file", str(out),
           "--length_scale", f"{1.0 / max(0.5, min(2.0, rate)):.3f}", "--sentence_silence", "0.15"]
    try:
        p = subprocess.run(cmd, input=prepare(text, lang).encode("utf-8"), capture_output=True, timeout=RUN_TIMEOUT,
                           creationflags=CREATE_NO_WINDOW, cwd=str(exe().parent))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PiperError(str(e)) from e
    if p.returncode != 0 or not out.is_file():
        raise PiperError((p.stderr or b"").decode("utf-8", "replace")[-300:] or f"exit {p.returncode}")
    try:
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
