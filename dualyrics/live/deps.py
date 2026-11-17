r"""Optional dependencies of Live mode and the "Install Live mode" helper.

Live mode needs numpy, faster-whisper (CTranslate2) and PyAudioWPatch (WASAPI loopback). They are
large, so they are not part of the base install; the app runs fine without them.

- Running from source: the helper runs ``pip install`` for the current interpreter.
- Packaged exe: the helper installs the packages into %APPDATA%\Dualyrics\live-runtime with a local
  Python of the same version (found with the ``py`` launcher or on PATH); the exe adds that folder to
  ``sys.path`` at start-up.
- GPU (optional): the NVIDIA cuBLAS and cuDNN wheels; their DLL folders are registered before Whisper
  loads, so CUDA works without a system-wide CUDA install.

Nothing here imports the heavy packages; ``importlib.util.find_spec`` only looks them up.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from dualyrics import paths

log = logging.getLogger(__name__)

# import name -> pip requirement
REQUIRED = {"numpy": "numpy>=1.24", "faster_whisper": "faster-whisper>=1.0", "pyaudiowpatch": "PyAudioWPatch>=0.2.12"}
GPU = {"nvidia.cublas": "nvidia-cublas-cu12", "nvidia.cudnn": "nvidia-cudnn-cu12>=9,<10"}
CREATE_NO_WINDOW = 0x08000000

_dll_dirs_added = False
_lock = threading.Lock()
_install_lock = threading.Lock()


def runtime_dir() -> Path:
    return paths.data_dir() / "live-runtime"


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def activate_runtime() -> None:
    """Make packages installed by the helper importable (packaged exe) and register CUDA DLL folders."""
    global _dll_dirs_added
    rd = runtime_dir()
    if rd.is_dir() and str(rd) not in sys.path:
        sys.path.append(str(rd))
        importlib.invalidate_caches()
    with _lock:
        if _dll_dirs_added or os.name != "nt":
            return
        _dll_dirs_added = True
        for base in _site_dirs():
            nv = base / "nvidia"
            if not nv.is_dir():
                continue
            for sub in nv.iterdir():
                b = sub / "bin"
                if b.is_dir():
                    try:
                        os.add_dll_directory(str(b))
                    except OSError:
                        continue
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def _site_dirs() -> list[Path]:
    out = []
    for p in sys.path:
        try:
            pp = Path(p)
        except TypeError:
            continue
        if pp.is_dir() and (pp.name in ("site-packages", "live-runtime")):
            out.append(pp)
    return out


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def has(module: str) -> bool:
    """An optional package (Live mode, the Edge voices of dubbing) is installed, and its last install finished."""
    activate_runtime()
    return module not in pending() and _has(module)


@dataclass(frozen=True)
class Status:
    missing: tuple[str, ...]        # pip names of missing required packages
    gpu_libs: bool                  # NVIDIA runtime wheels present
    frozen: bool

    @property
    def ready(self) -> bool:
        return not self.missing


def status() -> Status:
    activate_runtime()
    pend = pending()
    missing = tuple(req for mod, req in REQUIRED.items() if mod in pend or not _has(mod))
    return Status(missing, all(m not in pend and _has(m) for m in GPU), frozen())


def available() -> bool:
    return status().ready


def import_module(name: str):  # noqa: ANN201
    """Import an optional heavy module by name (kept out of PyInstaller's static analysis)."""
    activate_runtime()
    return importlib.import_module(name)


CUBLAS = ("cublas64_12", "cublas64_13", "cublas64_11")
CUDNN = ("cudnn64_9", "cudnn64_8")


def cuda_libs() -> bool:
    """cuBLAS and cuDNN DLLs can be found (NVIDIA wheels, a CUDA install or PATH). Without them CTranslate2
    on CUDA can hang or show a "DLL not found" dialog, so the GPU is only tried when they exist."""
    import ctypes.util

    activate_runtime()
    return any(ctypes.util.find_library(n) for n in CUBLAS) and any(ctypes.util.find_library(n) for n in CUDNN)


def gpu_usable() -> bool:
    return cuda_libs() and cuda_devices() > 0


def cuda_devices() -> int:
    """Number of CUDA devices CTranslate2 can see (0 when no GPU, no driver or no ctranslate2)."""
    try:
        return int(import_module("ctranslate2").get_cuda_device_count())
    except Exception:
        return 0


# ---------------------------------------------------------------- installer
def pip_proxy(proxy_mode: str, proxy_url: str) -> str | None:
    """The proxy pip should use for the app's network setting ('' = explicitly none, None = pip default)."""
    if proxy_mode == "custom" and proxy_url:
        return proxy_url
    if proxy_mode == "none":
        return ""
    found = urllib.request.getproxies()   # environment, then the Windows Internet Options proxy
    return found.get("https") or found.get("http") or None


def _find_python() -> list[str] | None:
    """A Python matching this exe's version, to install wheels the packaged app can load."""
    want = f"{sys.version_info.major}.{sys.version_info.minor}"
    cands: list[list[str]] = []
    if shutil.which("py"):
        cands.append(["py", f"-{want}"])
    for exe in ("python", "python3"):
        p = shutil.which(exe)
        if p and "windowsapps" not in p.lower():
            cands.append([p])
    for c in cands:
        try:
            out = subprocess.run(c + ["-c", "import sys;print('%d.%d'%sys.version_info[:2])"], capture_output=True,
                                 text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
            if out.returncode == 0 and out.stdout.strip() == want:
                return c
        except Exception:
            continue
    return None


PIP_RETRIES = 10                 # pip's own retries per request (a flaky proxy drops connections)
PIP_TIMEOUT = 60                 # s without data before pip retries
PIP_RESUME_RETRIES = 20          # pip >= 25.1: continue a broken download instead of starting it again
VERIFY_TIMEOUT = 300
_WHEEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}\.(?:whl|tar\.gz|zip)$")


def _python() -> list[str] | None:
    """The interpreter whose pip installs the packages (None: packaged exe without a matching Python)."""
    return _find_python() if frozen() else [sys.executable]


def pip_version(py: list[str]) -> tuple[int, ...]:
    """Version of that interpreter's pip ((0,) when unknown)."""
    try:
        out = subprocess.run(py + ["-m", "pip", "--version"], capture_output=True, text=True, timeout=30,
                             creationflags=CREATE_NO_WINDOW)
        m = re.match(r"pip (\d+)\.(\d+)(?:\.(\d+))?", out.stdout.strip())
        if m:
            return tuple(int(x) for x in m.groups() if x is not None)
    except Exception:
        log.debug("pip --version failed", exc_info=True)
    return (0,)


def robust_flags(version: tuple[int, ...]) -> list[str]:
    """pip options that survive a flaky connection: more retries, a longer timeout, and resumed downloads
    when this pip has ``--resume-retries`` (25.1 and later)."""
    flags = ["--retries", str(PIP_RETRIES), "--timeout", str(PIP_TIMEOUT)]
    if version >= (25, 1):
        flags += ["--resume-retries", str(PIP_RESUME_RETRIES)]
    return flags


def _requirements(gpu: bool, packages: list[str] | None) -> list[str]:
    return list(packages) if packages else list(REQUIRED.values()) + (list(GPU.values()) if gpu else [])


def install_command(gpu: bool, proxy: str | None, packages: list[str] | None = None,
                    pip_ver: tuple[int, ...] = (0,), py: list[str] | None = None) -> list[str] | None:
    """The pip command line (requirements last), or None when no suitable Python exists (packaged exe
    without Python). ``packages`` replaces Live mode's requirements (the dubbing voices use the same helper)."""
    reqs = _requirements(gpu, packages)
    py = py or _python()
    if py is None:
        return None
    cmd = py + ["-m", "pip", "install", "--upgrade"]
    if frozen():
        cmd += ["--target", str(runtime_dir())]
    cmd += ["--disable-pip-version-check", "--no-input", "--progress-bar", "off"] + robust_flags(pip_ver)
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd + reqs


# ---------------------------------------------------------------- a half-finished install never counts
def _pending_file() -> Path:
    return paths.data_dir() / "install-pending.json"


def pending() -> set[str]:
    """Import names of packages whose last install did not finish (they count as missing until one does)."""
    try:
        data = json.loads(_pending_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    mods = data.get("modules", []) if isinstance(data, dict) else []
    return {m for m in mods if isinstance(m, str)} if isinstance(mods, list) else set()


def _set_pending(modules: set[str]) -> None:
    f = _pending_file()
    try:
        if modules:
            f.write_text(json.dumps({"modules": sorted(modules)}), encoding="utf-8")
        else:
            f.unlink(missing_ok=True)
    except OSError:
        log.debug("install marker not written", exc_info=True)


def wheels_dir() -> Path:
    return paths.data_dir() / "downloads" / "wheels"


# ---------------------------------------------------------------- the steps (separate so tests can fake them)
def _run_pip(cmd: list[str], env: dict[str, str], on_line: Callable[[str], None],
             cancelled: Callable[[], bool]) -> tuple[int, str]:
    """Run pip, forwarding its output lines. Returns (exit code, last line); -1 when cancelled."""
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return 1, str(e)
    last = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            last = line
            on_line(line[:300])
        if cancelled():
            proc.kill()
            proc.wait()
            return -1, "cancelled"
    return proc.wait(), last


def _verify_imports(py: list[str], modules: list[str]) -> str:
    """'' when every module imports in a fresh interpreter (the way the app will load them), else the error."""
    if not modules:
        return ""
    code = ("import sys, importlib\n"
            + (f"sys.path.insert(0, {str(runtime_dir())!r})\n" if frozen() else "")
            + f"for m in {modules!r}:\n    importlib.import_module(m)\n")
    try:
        out = subprocess.run(py + ["-c", code], capture_output=True, text=True, timeout=VERIFY_TIMEOUT,
                             encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except Exception as e:  # noqa: BLE001
        return str(e)
    if out.returncode == 0:
        return ""
    lines = [ln for ln in (out.stderr or out.stdout or "").splitlines() if ln.strip()]
    return lines[-1][:300] if lines else f"exit code {out.returncode}"


def _report_files(report: Any) -> list[tuple[str, str, str]] | None:
    """(file name, https URL, sha256) of everything a ``pip install --dry-run --report`` would download, or
    None when something cannot be fetched by the app (a local path, a VCS link, an odd file name)."""
    from urllib.parse import unquote, urlsplit

    out = []
    items = report.get("install", []) if isinstance(report, dict) else []
    for it in items if isinstance(items, list) else []:
        di = (it.get("download_info") or {}) if isinstance(it, dict) else {}
        url = str(di.get("url") or "")
        u = urlsplit(url)
        name = unquote(u.path.rsplit("/", 1)[-1])
        if u.scheme != "https" or not _WHEEL_NAME.match(name):
            return None
        ai = di.get("archive_info") or {}
        sha = str((ai.get("hashes") or {}).get("sha256") or "")
        if not sha and str(ai.get("hash") or "").startswith("sha256="):
            sha = str(ai["hash"])[7:]
        out.append((name, url, sha.lower() if re.match(r"^[0-9a-fA-F]{64}$", sha) else ""))
    return out


def _prefetch(cmd: list[str], n_reqs: int, env: dict[str, str], proxy_mode: str, proxy_url: str,
              on_line: Callable[[str], None], cancelled: Callable[[], bool]) -> list[Path] | None:
    """Resolve with pip (``--dry-run --report``, metadata only) and download every file with the app's
    resumable downloader into ``wheels_dir()``. Returns the files, or None when this cannot be used (old pip,
    resolution failed, a file the app cannot fetch); the caller then lets pip download as usual."""
    from dualyrics import download, net

    folder = wheels_dir()
    folder.mkdir(parents=True, exist_ok=True)
    report_path = folder / "report.json"
    report_path.unlink(missing_ok=True)
    dry = cmd[:-n_reqs] + ["--dry-run", "--quiet", "--report", str(report_path)] + cmd[-n_reqs:]
    on_line("Resolving packages…")
    code, last = _run_pip(dry, env, lambda line: None, cancelled)
    if code == -1:
        raise download.DownloadCancelled()
    if code != 0 or not report_path.is_file():
        log.info("pip could not resolve for the prefetch (exit %s): %s", code, last)
        return None
    try:
        files = _report_files(json.loads(report_path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        files = None
    if files is None:
        return None
    session = net.make_session(proxy_mode, proxy_url)
    out: list[Path] = []
    state = {"at": 0.0}
    for i, (name, url, sha) in enumerate(files, 1):
        def progress(done: int, total: int, name: str = name, i: int = i) -> None:
            now = time.monotonic()
            if now - state["at"] >= 0.5 or (total and done >= total):
                state["at"] = now
                size = f"{done / 1e6:.1f} / {total / 1e6:.1f} MB" if total else f"{done / 1e6:.1f} MB"
                on_line(f"Downloading {name} ({i}/{len(files)}): {size}")

        out.append(download.fetch(session, url, folder / name, progress, cancelled, sha256=sha))
    return out


def install(gpu: bool, proxy_mode: str, proxy_url: str, on_line: Callable[[str], None] = lambda s: None,
            cancelled: Callable[[], bool] = lambda: False, packages: dict[str, str] | None = None,
            ) -> tuple[bool, str]:
    """Run the installer (blocking; call it from a worker thread). Returns (ok, last message). ``packages``
    ({import name: pip requirement}) installs those instead of Live mode.

    1. pip resolves the packages (metadata only) and the app downloads every file itself, resuming broken
       transfers (``_prefetch``); pip then installs from those local files with ``--no-index``.
    2. When that cannot be used or fails, pip downloads by itself with more retries, a longer timeout and
       ``--resume-retries`` where available.
    3. The packages are imported in a fresh interpreter. Until an install gets this far they are marked
       pending and count as missing, so a half-finished install never looks installed.

    One install runs at a time (they share the download folder); a second one waits."""
    with _install_lock:
        return _install(gpu, proxy_mode, proxy_url, on_line, cancelled, packages)


def _install(gpu: bool, proxy_mode: str, proxy_url: str, on_line: Callable[[str], None],
             cancelled: Callable[[], bool], packages: dict[str, str] | None) -> tuple[bool, str]:
    from dualyrics import download

    proxy = pip_proxy(proxy_mode, proxy_url)
    py = _python()
    what = "these voices" if packages else "Live mode"
    if py is None:
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        return False, f"Python {v} not found (python.org); it is needed once to install {what}."
    mods = dict(packages) if packages else {**REQUIRED, **(GPU if gpu else {})}
    reqs = _requirements(gpu, list(packages.values()) if packages else None)
    cmd = install_command(gpu, proxy, list(packages.values()) if packages else None, pip_version(py), py)
    assert cmd is not None
    env = dict(os.environ)
    if proxy == "":
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
    shown = [c if not (proxy and c == proxy) else "<proxy>" for c in cmd]
    log.info("installing %s: %s", what, " ".join(shown))
    files: list[Path] | None = None
    try:
        # resolving and downloading change nothing installed: the pending mark comes only before pip installs
        files = _prefetch(cmd, len(reqs), env, proxy_mode, proxy_url, on_line, cancelled)
    except download.DownloadCancelled:
        return False, "cancelled"
    except Exception as e:  # noqa: BLE001 - pip can still download by itself
        log.warning("prefetch failed, pip downloads by itself: %s", e)
        on_line(f"Download failed ({str(e)[:160]}); pip tries by itself…")
    if cancelled():
        return False, "cancelled"
    _set_pending(pending() | set(mods))
    code, last = 1, ""
    if files is not None:
        local = cmd[:-len(reqs)] + ["--no-index", "--find-links", str(wheels_dir())] + reqs
        code, last = _run_pip(local, env, on_line, cancelled)
        if code not in (0, -1):
            log.info("install from the downloaded files failed (%s); pip downloads by itself", last)
    if code not in (0, -1):
        code, last = _run_pip(cmd, env, on_line, cancelled)
    importlib.invalidate_caches()
    if code != 0:
        # a stopped or failed pip often changed nothing (or a reinstall over a working copy): what still
        # loads in a fresh interpreter keeps counting as installed, so Live mode does not turn "missing"
        if not _verify_imports(py, list(mods)):
            _set_pending(pending() - set(mods))
        if code == -1:
            return False, "cancelled"
        log.warning("install failed (exit %s): %s", code, last)
        return False, last or f"pip exit code {code}"
    problem = _verify_imports(py, list(mods))
    if problem:
        log.warning("installed, but the packages do not load: %s", problem)
        return False, f"installed, but it does not load: {problem}"
    _set_pending(pending() - set(mods))
    for f in [*(files or []), wheels_dir() / "report.json"]:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
    missing = [req for mod, req in mods.items() if not has(mod)]
    if missing:
        return False, "installed, but still missing: " + ", ".join(missing)
    return True, last
