"""Ducking: other apps get quieter while the dubbed voice speaks, and get their volume back afterwards.

Works on the Windows audio sessions (the per-app volumes of the volume mixer) of every active output device,
through ``IAudioSessionManager2`` / ``ISimpleAudioVolume`` (comtypes). Our own process is never ducked, so the
voice stays at its volume. A session the user changed while it was ducked keeps the user's new volume.

``Ducker`` holds the logic and takes any backend with ``sessions()``, so it is unit tested with fakes; the
Windows backend is ``WindowsSessions``. Call both from one worker thread (COM).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterable, Protocol

log = logging.getLogger(__name__)

TOLERANCE = 0.02        # a volume this close to what we set counts as untouched by the user
JOURNAL = "ducked.json"  # volumes to give back, kept while apps are ducked (crash recovery)


class Session(Protocol):
    key: str            # stable id of the session (pid + instance id)
    pid: int

    def get_volume(self) -> float: ...

    def set_volume(self, v: float) -> None: ...


class Backend(Protocol):
    def sessions(self) -> Iterable[Session]: ...


def journal_path() -> Path:
    """Where the volumes to give back are kept while apps are ducked (``%APPDATA%\\Dualyrics``)."""
    from dualyrics import paths

    return paths.data_dir() / JOURNAL


def _group(s: Any) -> str:
    return str(getattr(s, "group", "") or "")


class Ducker:
    """``journal``: a file that holds the volumes to give back while apps are ducked, so a crash or a killed
    process never leaves other apps quiet: :func:`recover` restores them on the next start."""

    def __init__(self, backend: Backend, own_pids: Iterable[int] | None = None, journal: Path | None = None) -> None:
        self.backend = backend
        self.own = set(own_pids) if own_pids is not None else {os.getpid()}
        self._saved: dict[str, tuple[float, float]] = {}    # key -> (volume before, volume we set)
        self._groups: dict[str, str] = {}                   # key -> session group (the app, across restarts)
        self.amount = 0.0
        self.journal = journal

    def _write_journal(self) -> None:
        if self.journal is None:
            return
        try:
            if not self._saved:
                self.journal.unlink(missing_ok=True)
                return
            data = [{"key": k, "group": self._groups.get(k, ""), "orig": o, "target": tg}
                    for k, (o, tg) in self._saved.items()]
            tmp = self.journal.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pid": os.getpid(), "sessions": data}), encoding="utf-8")
            os.replace(tmp, self.journal)
        except OSError:
            log.debug("could not write the ducking journal", exc_info=True)

    @property
    def ducked(self) -> bool:
        return bool(self._saved)

    def duck(self, amount: float) -> int:
        """Lower every other app by ``amount`` (0..1 of its own volume). Called again while ducked, it ducks
        apps that started since. Returns how many sessions it lowered now."""
        amount = max(0.0, min(1.0, amount))
        self.amount = amount
        n = 0
        if amount <= 0:
            return 0
        for s in self._sessions():
            if s.pid in self.own or s.pid <= 0 or s.key in self._saved:
                continue                 # our own voice, Windows system sounds, already ducked
            try:
                vol = float(s.get_volume())
                target = round(vol * (1.0 - amount), 4)
                s.set_volume(target)
            except Exception:
                log.debug("could not duck session %s", s.key, exc_info=True)
                continue
            self._saved[s.key] = (vol, target)
            self._groups[s.key] = _group(s)
            n += 1
        if n:
            self._write_journal()
        return n

    def restore(self) -> int:
        """Give every ducked app its volume back, unless the user changed it meanwhile."""
        if not self._saved:
            return 0
        by_key = {s.key: s for s in self._sessions()}
        n = 0
        for key, (orig, target) in list(self._saved.items()):
            s = by_key.get(key)
            if s is None:
                continue                   # the app closed
            try:
                if abs(float(s.get_volume()) - target) <= TOLERANCE:
                    s.set_volume(orig)
                    n += 1
            except Exception:
                log.debug("could not restore session %s", key, exc_info=True)
        self._saved.clear()
        self._groups.clear()
        self._write_journal()
        return n

    def _sessions(self) -> list[Session]:
        try:
            return list(self.backend.sessions())
        except Exception:
            log.debug("audio sessions unavailable", exc_info=True)
            return []


def recover(backend: Backend, journal: Path) -> int:
    """After a crash: give the apps a previous run ducked their volume back (unless the user changed it), then
    forget the journal. A session is found by its key, or by its group when the app was restarted meanwhile
    (Windows remembers the lowered volume per app). Returns how many were restored."""
    try:
        data = json.loads(journal.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError):
        data = {}
    n = 0
    keep = False
    try:
        entries = [e for e in (data.get("sessions") or []) if isinstance(e, dict)] if isinstance(data, dict) else []
        if entries:
            try:
                live = list(backend.sessions())
            except Exception:
                log.debug("audio sessions unavailable", exc_info=True)
                keep = True       # try again on the next start
                return 0
            by_key = {s.key: s for s in live}
            by_group: dict[str, list[Any]] = {}
            for s in live:
                if _group(s):
                    by_group.setdefault(_group(s), []).append(s)
            done: set[str] = set()
            for e in entries:
                try:
                    orig, target = float(e["orig"]), float(e["target"])
                except (KeyError, TypeError, ValueError):
                    continue
                cands = [by_key[e["key"]]] if e.get("key") in by_key else by_group.get(str(e.get("group") or ""), [])
                for s in cands:
                    if s.key in done:
                        continue
                    try:
                        if abs(float(s.get_volume()) - target) <= TOLERANCE:
                            s.set_volume(max(0.0, min(1.0, orig)))
                            n += 1
                    except Exception:
                        log.debug("could not restore session %s", s.key, exc_info=True)
                    done.add(s.key)
        if n:
            log.info("ducking: gave %d app(s) their volume back after an unclean exit", n)
    finally:
        if not keep:
            try:
                journal.unlink(missing_ok=True)
            except OSError:
                pass
    return n


class NoSessions:
    """Backend used where the Windows one cannot load: ducking does nothing."""

    def sessions(self) -> list[Session]:
        return []


# ---------------------------------------------------------------- Windows audio sessions (comtypes)
_api: Any = None


def _define() -> Any:
    """The COM interfaces, defined on first use (comtypes is imported only then)."""
    global _api
    if _api is not None:
        return _api
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0      # type: ignore[attr-defined]  # multithreaded apartment for this thread
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown
    from ctypes import POINTER, c_float, c_int, c_uint, c_void_p, c_wchar_p
    from ctypes.wintypes import BOOL, DWORD

    class ISimpleAudioVolume(IUnknown):
        _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], c_float, "level"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], POINTER(c_float), "level")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "mute"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "mute")),
        ]

    class IAudioSessionControl(IUnknown):
        _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(c_int), "state")),
            COMMETHOD([], HRESULT, "GetDisplayName", (["out"], POINTER(c_void_p), "name")),
            COMMETHOD([], HRESULT, "SetDisplayName", (["in"], c_wchar_p, "name"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetIconPath", (["out"], POINTER(c_void_p), "path")),
            COMMETHOD([], HRESULT, "SetIconPath", (["in"], c_wchar_p, "path"), (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], POINTER(GUID), "param")),
            COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], POINTER(GUID), "param"),
                      (["in"], POINTER(GUID), "ctx")),
            COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], c_void_p, "client")),
            COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], c_void_p, "client")),
        ]

    class IAudioSessionControl2(IAudioSessionControl):
        _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], POINTER(c_void_p), "id")),
            COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier", (["out"], POINTER(c_void_p), "id")),
            COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(DWORD), "pid")),
            COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
            COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], BOOL, "optout")),
        ]

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "count")),
            COMMETHOD([], HRESULT, "GetSession", (["in"], c_int, "index"),
                      (["out"], POINTER(POINTER(IAudioSessionControl)), "session")),
        ]

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], POINTER(GUID), "group"),
                      (["in"], DWORD, "flags"), (["out"], POINTER(c_void_p), "control")),
            COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], POINTER(GUID), "group"),
                      (["in"], DWORD, "flags"), (["out"], POINTER(c_void_p), "volume")),
            COMMETHOD([], HRESULT, "GetSessionEnumerator",
                      (["out"], POINTER(POINTER(IAudioSessionEnumerator)), "enum")),
            COMMETHOD([], HRESULT, "RegisterSessionNotification", (["in"], c_void_p, "client")),
            COMMETHOD([], HRESULT, "UnregisterSessionNotification", (["in"], c_void_p, "client")),
            COMMETHOD([], HRESULT, "RegisterDuckNotification", (["in"], c_wchar_p, "id"), (["in"], c_void_p, "c")),
            COMMETHOD([], HRESULT, "UnregisterDuckNotification", (["in"], c_void_p, "client")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], DWORD, "ctx"),
                      (["in"], c_void_p, "params"), (["out"], POINTER(c_void_p), "out")),
            COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], DWORD, "access"), (["out"], POINTER(c_void_p), "s")),
            COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p), "id")),
            COMMETHOD([], HRESULT, "GetState", (["out"], POINTER(DWORD), "state")),
        ]

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "count")),
            COMMETHOD([], HRESULT, "Item", (["in"], c_uint, "index"), (["out"], POINTER(POINTER(IMMDevice)), "dev")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_int, "flow"), (["in"], DWORD, "mask"),
                      (["out"], POINTER(POINTER(IMMDeviceCollection)), "devices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_int, "flow"), (["in"], c_int, "role"),
                      (["out"], POINTER(POINTER(IMMDevice)), "dev")),
        ]

    class Api:
        pass

    api = Api()
    api.comtypes = comtypes
    api.GUID = GUID
    api.ISimpleAudioVolume = ISimpleAudioVolume
    api.IAudioSessionControl2 = IAudioSessionControl2
    api.IAudioSessionManager2 = IAudioSessionManager2
    api.IMMDeviceEnumerator = IMMDeviceEnumerator
    api.CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _api = api
    return api


def com_init() -> None:
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


def _take_string(p: Any) -> str:
    """A string the callee allocated with CoTaskMemAlloc."""
    if not p:
        return ""
    try:
        return ctypes.wstring_at(p)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(p))


class _WinSession:
    def __init__(self, key: str, pid: int, volume: Any, group: str = "") -> None:
        self.key, self.pid, self._vol = key, pid, volume
        self.group = group        # session identifier: the same app keeps it across restarts

    def get_volume(self) -> float:
        return float(self._vol.GetMasterVolume())

    def set_volume(self, v: float) -> None:
        self._vol.SetMasterVolume(max(0.0, min(1.0, float(v))), None)


class WindowsSessions:
    """The audio sessions of every active output device (one worker thread only)."""

    def __init__(self) -> None:
        com_init()
        api = _define()
        self.api = api
        self._enum = api.comtypes.CoCreateInstance(api.CLSID_MMDeviceEnumerator, interface=api.IMMDeviceEnumerator,
                                                   clsctx=api.comtypes.CLSCTX_ALL)

    def sessions(self) -> list[_WinSession]:
        api = self.api
        out: list[_WinSession] = []
        devices = self._enum.EnumAudioEndpoints(0, 1)          # eRender, DEVICE_STATE_ACTIVE
        for i in range(devices.GetCount()):
            dev = devices.Item(i)
            try:
                ptr = dev.Activate(ctypes.byref(api.IAudioSessionManager2._iid_), api.comtypes.CLSCTX_ALL, None)
                mgr = ctypes.cast(ptr, ctypes.POINTER(api.IAudioSessionManager2))
                sessions = mgr.GetSessionEnumerator()
            except Exception:
                log.debug("audio device without sessions", exc_info=True)
                continue
            for j in range(sessions.GetCount()):
                try:
                    ctl = sessions.GetSession(j).QueryInterface(api.IAudioSessionControl2)
                    if ctl.GetState() == 2:          # AudioSessionStateExpired
                        continue
                    pid = int(ctl.GetProcessId())
                    inst = _take_string(ctl.GetSessionInstanceIdentifier())
                    try:
                        group = _take_string(ctl.GetSessionIdentifier())
                    except Exception:
                        group = ""
                    vol = ctl.QueryInterface(api.ISimpleAudioVolume)
                except Exception:
                    continue
                out.append(_WinSession(inst or f"{pid}:{i}:{j}", pid, vol, group))
        return out


def windows_backend() -> Backend:
    try:
        return WindowsSessions()
    except Exception as e:  # noqa: BLE001
        log.info("ducking unavailable: %s", e)
        return NoSessions()
