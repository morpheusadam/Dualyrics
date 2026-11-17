r"""Start with Windows: a value under HKCU\Software\Microsoft\Windows\CurrentVersion\Run (no admin needed)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dualyrics import APP_NAME

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command_line() -> str:
    """Command that starts this copy of Dualyrics without a console window."""
    if getattr(sys, "frozen", False):  # packaged .exe
        return f'"{sys.executable}" --startup'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    script = Path(__file__).resolve().parents[1] / "run.pyw"
    if script.exists():
        return f'"{exe}" "{script}" --startup'
    return f'"{exe}" -m dualyrics --startup'


def is_enabled() -> bool:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Add or remove the Run entry. Returns True on success."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, command_line())
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as e:
        log.error("could not change startup entry: %s", e)
        return False


def refresh() -> None:
    """If enabled, rewrite the entry so it points at the current install location."""
    if is_enabled():
        set_enabled(True)
