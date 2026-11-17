# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Dualyrics: one windowed file, dist\Dualyrics.exe.
# Build with tools\build.ps1 (or: python -m PyInstaller --noconfirm --clean Dualyrics.spec).
#
# PyWinRT (winrt-runtime) ships its own PyInstaller hook that collects every projected namespace
# and its _table.pywinrt data file; the hidden imports below only make the needed ones explicit.

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "dualyrics" / "__init__.py").read_text("utf-8")).group(1)
NUMS = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=NUMS, prodvers=NUMS, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                      subtype=0x0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Dualyrics contributors"),
            StringStruct("FileDescription", "Dualyrics - synced lyrics with live translation"),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", "Dualyrics"),
            StringStruct("LegalCopyright", "MIT License"),
            StringStruct("OriginalFilename", "Dualyrics.exe"),
            StringStruct("ProductName", "Dualyrics"),
            StringStruct("ProductVersion", VERSION),
        ])]),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

datas = [(str(ROOT / "dualyrics" / "assets"), "dualyrics/assets"),   # icon, Vazirmatn (OFL), ctk theme
         (str(ROOT / "dualyrics" / "templates" / "builtin"), "dualyrics/templates/builtin"),
         (str(ROOT / "dualyrics" / "templates" / "schema.json"), "dualyrics/templates"),
         # third-party notices travel with the exe itself (ported MIT / Apache-2.0 code, bundled libraries)
         (str(ROOT / "LICENSE"), "."), (str(ROOT / "NOTICE"), "."), (str(ROOT / "LICENSES"), "LICENSES")]
datas += collect_data_files("customtkinter")  # themes (json) and fonts

hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.media.control",
    "winrt.windows.storage.streams",
    # v2.1 screen source: Windows OCR and UI Automation (comtypes wrapper generated at build time)
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.globalization",
    "socks",
    # v2.2 dubbing: MP3 decoding without Media Foundation (dr_mp3 through pyminiaudio, a cffi extension)
    "miniaudio",
    "_cffi_backend",
]
try:
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")   # writes comtypes.gen.UIAutomationClient into the venv
except Exception as e:  # noqa: BLE001
    print("warning: UI Automation wrapper not generated:", e)
from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

hiddenimports += [m for m in collect_submodules("comtypes") if not m.startswith("comtypes.test")]

# Live mode's packages (numpy, faster-whisper, CTranslate2, PyAudioWPatch: ~250 MB) are NOT bundled; the app
# installs them on demand into %APPDATA%\Dualyrics\live-runtime (dualyrics/live/deps.py). They import
# standard-library modules the rest of the app never uses, so the whole standard library goes in (a few MB),
# minus tests, IDLE and other developer tools.
STDLIB_SKIP = {"idlelib", "test", "lib2to3", "ensurepip", "venv", "turtledemo", "pydoc_data", "distutils",
               "turtle", "antigravity", "this", "__phello__", "_pyrepl", "tkinter.test", "unittest.test"}


def _stdlib_modules() -> list[str]:
    import importlib.util
    import pkgutil

    out = []
    for name in sorted(sys.stdlib_module_names):
        if name in STDLIB_SKIP or name.startswith("_test"):
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue                       # POSIX-only module
        out.append(name)
        if spec.submodule_search_locations:
            for m in pkgutil.walk_packages(spec.submodule_search_locations, name + ".", onerror=lambda _: None):
                parts = m.name.split(".")
                if not any(p in ("test", "tests", "idle_test") for p in parts) and m.name not in STDLIB_SKIP:
                    out.append(m.name)
    return out


hiddenimports += _stdlib_modules()

# Most of those packages are abi3 extensions linked against python3.dll (av, tokenizers, ...), which nothing
# in the app itself needs, so PyInstaller would leave it out.
binaries = [(str(p), ".") for p in (Path(sys.base_prefix) / "python3.dll", Path(sys.base_prefix) / "vcruntime140_1.dll")
            if p.exists()]

a = Analysis(
    [str(ROOT / "run.pyw")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Live mode's heavy packages are optional and installed by the app itself (dualyrics/live/deps.py)
    excludes=["pytest", "_pytest", "pluggy", "iniconfig", "pygments", "numpy", "faster_whisper", "ctranslate2",
              "pyaudiowpatch", "av", "onnxruntime", "tokenizers", "huggingface_hub", "hf_xet"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Dualyrics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "dualyrics" / "assets" / "icon.ico"),
    version=version_info,
)
