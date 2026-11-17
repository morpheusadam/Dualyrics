# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Dualyrics: one windowed file, dist\Dualyrics.exe.
# Build with tools\build.ps1 (or: python -m PyInstaller --noconfirm --clean Dualyrics.spec).
#
# PyWinRT (winrt-runtime) ships its own PyInstaller hook that collects every projected namespace
# and its _table.pywinrt data file; the hidden imports below only make the needed ones explicit.

import re
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

datas = [(str(ROOT / "dualyrics" / "assets"), "dualyrics/assets")]
datas += collect_data_files("customtkinter")  # themes (json) and fonts

hiddenimports = [
    "pystray._win32",
    "PIL._tkinter_finder",
    "winrt.windows.foundation",
    "winrt.windows.foundation.collections",
    "winrt.windows.media.control",
    "winrt.windows.storage.streams",
    "socks",
]

a = Analysis(
    [str(ROOT / "run.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "pluggy", "iniconfig", "pygments"],
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
