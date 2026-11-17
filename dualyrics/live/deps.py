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
import logging
import os
import shutil
import subprocess
import sys
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dualyrics import paths

log = logging.getLogger(__name__)

# import name -> pip requirement
REQUIRED = {"numpy": "numpy>=1.24", "faster_whisper": "faster-whisper>=1.0", "pyaudiowpatch": "PyAudioWPatch>=0.2.12"}
GPU = {"nvidia.cublas": "nvidia-cublas-cu12", "nvidia.cudnn": "nvidia-cudnn-cu12>=9,<10"}
CREATE_NO_WINDOW = 0x08000000

_dll_dirs_added = False
_lock = threading.Lock()


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
    """An optional package (Live mode, the Edge voices of dubbing) is installed."""
    activate_runtime()
    return _has(module)


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
    missing = tuple(req for mod, req in REQUIRED.items() if not _has(mod))
    return Status(missing, all(_has(m) for m in GPU), frozen())


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


def install_command(gpu: bool, proxy: str | None, packages: list[str] | None = None) -> list[str] | None:
    """The pip command line, or None when no suitable Python exists (packaged exe without Python).
    ``packages`` replaces Live mode's requirements (the dubbing voices use the same helper)."""
    reqs = list(packages) if packages else list(REQUIRED.values()) + (list(GPU.values()) if gpu else [])
    if frozen():
        py = _find_python()
        if py is None:
            return None
        cmd = py + ["-m", "pip", "install", "--upgrade", "--target", str(runtime_dir())]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    cmd += ["--disable-pip-version-check", "--no-input", "--progress-bar", "off"]
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd + reqs


def install(gpu: bool, proxy_mode: str, proxy_url: str, on_line: Callable[[str], None] = lambda s: None,
            cancelled: Callable[[], bool] = lambda: False, packages: dict[str, str] | None = None,
            ) -> tuple[bool, str]:
    """Run the installer (blocking; call it from a worker thread). Returns (ok, last message). ``packages``
    ({import name: pip requirement}) installs those instead of Live mode."""
    proxy = pip_proxy(proxy_mode, proxy_url)
    cmd = install_command(gpu, proxy, list(packages.values()) if packages else None)
    if cmd is None:
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        what = "these voices" if packages else "Live mode"
        return False, f"Python {v} not found (python.org); it is needed once to install {what}."
    env = dict(os.environ)
    if proxy == "":
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
    shown = [c if not (proxy and c == proxy) else "<proxy>" for c in cmd]
    log.info("installing Live mode: %s", " ".join(shown))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
    except OSError as e:
        return False, str(e)
    last = ""
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            last = line
            on_line(line[:300])
        if cancelled():
            proc.kill()
            return False, "cancelled"
    code = proc.wait()
    importlib.invalidate_caches()
    if code != 0:
        log.warning("install failed (exit %s): %s", code, last)
        return False, last or f"pip exit code {code}"
    if packages:
        missing = [req for mod, req in packages.items() if not has(mod)]
        return (False, "installed, but still missing: " + ", ".join(missing)) if missing else (True, last)
    st = status()
    if not st.ready:
        return False, "installed, but still missing: " + ", ".join(st.missing)
    return True, last
