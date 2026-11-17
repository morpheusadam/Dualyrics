r"""Filesystem locations. All user data (settings, keys, cache, logs) lives in %APPDATA%\Dualyrics.

Set the environment variable ``DUALYRICS_HOME`` to use another folder (portable mode, tests).
"""

from __future__ import annotations

import os
from pathlib import Path

from dualyrics import APP_NAME

PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"
ICON_PNG = ASSETS_DIR / "icon.png"
ICON_ICO = ASSETS_DIR / "icon.ico"


def data_dir() -> Path:
    """Return (and create) the per-user data folder."""
    override = os.environ.get("DUALYRICS_HOME")
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        base = Path(appdata) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def settings_file() -> Path:
    return data_dir() / "settings.json"


def cache_dir() -> Path:
    p = data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
