"""Pieces the Windows installer relies on: `Dualyrics.exe --quit`, the version and the Inno Setup script."""

import re
import sys
import threading
import time
from pathlib import Path

import pytest

from dualyrics import __version__, app, winutil

ROOT = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="named mutex and events are Windows only")


def _skip_if_real_copy_running() -> None:
    probe = winutil.SingleInstance()
    running = probe.already_running
    probe.release()
    if running:
        pytest.skip("a real Dualyrics is running on this machine; --quit would close it")


@windows_only
def test_quit_without_running_copy_returns_at_once(monkeypatch):
    _skip_if_real_copy_running()
    monkeypatch.setattr(app, "App", lambda *a, **k: pytest.fail("--quit must not start the app"))
    t0 = time.monotonic()
    app.main(["--quit"])
    assert time.monotonic() - t0 < 2


@windows_only
def test_quit_asks_running_copy_and_waits_until_it_exits():
    running = winutil.SingleInstance()
    if running.already_running:
        running.release()
        pytest.skip("a real Dualyrics is running on this machine")
    try:
        t = threading.Thread(target=app.main, args=(["--quit"],), daemon=True)
        t.start()
        end = time.monotonic() + 5
        while not running.quit_requested() and time.monotonic() < end:
            time.sleep(0.05)
        assert running.quit_requested()
        time.sleep(0.3)
        assert t.is_alive(), "--quit must wait while the running copy still holds the mutex"
    finally:
        running.release()
    t.join(5)
    assert not t.is_alive()


def test_versions_agree():
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M).group(1) == __version__


def test_installer_script_basics():
    iss = (ROOT / "installer" / "Dualyrics.iss").read_text("utf-8-sig")
    assert "PrivilegesRequired=lowest" in iss
    assert r"DefaultDirName={localappdata}\Programs\{#AppName}" in iss and '#define AppName "Dualyrics"' in iss
    assert "OutputBaseFilename={#AppName}-Setup-{#AppVersion}" in iss
    assert 'Name: "persian"' in iss and 'Name: "english"' in iss
    # the Run value must use the same name as dualyrics/startup.py so the in-app switch sees it
    assert 'ValueName: "Dualyrics"' in iss
    assert "--quit" in iss
    # every custom message exists in both installer languages
    names = set(re.findall(r"^english\.(\w+)=", iss, re.M))
    assert names and names == set(re.findall(r"^persian\.(\w+)=", iss, re.M))
