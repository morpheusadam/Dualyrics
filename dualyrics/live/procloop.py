r"""Per-process audio capture: WASAPI process loopback (Windows 10 build 20348+ / Windows 11).

``ActivateAudioInterfaceAsync("VAD\Process_Loopback", IID_IAudioClient, PROPVARIANT(VT_BLOB, activation
params))`` gives an ``IAudioClient`` that records only what one process tree plays (``include``), or
everything except one process tree (``exclude``, used with our own PID so a dubbed voice is never captured
and transcribed again). The client is initialised in shared loopback mode with ``AUTOCONVERTPCM``, so the
audio engine delivers 16-bit PCM at the rate we ask for (16 kHz mono first, 48 kHz stereo when that is
refused), and packets are drained with ``IAudioCaptureClient``.

Ported from fr0styJS/dota-live-translator ``backend/procloop.py`` (MIT, Copyright (c) 2026 Fr0styDev), which
mirrors microsoft/Windows-classic-samples ``Samples/ApplicationLoopback`` (MIT). Changed: exclude mode, the
16 kHz request with a 48 kHz fallback, a stateful resampler (no drift or clicks at packet edges), a bigger
buffer for Python's polling, the process-exit check without psutil, COM objects released in order, and every
failure raised as ``ProcessLoopbackError`` so the caller can fall back to the system loopback.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
MIN_BUILD = 20348                     # documented minimum; older builds fail activation and fall back

VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE = 1
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2
VT_BLOB = 65
BUFFER_HNS = 2_000_000                # 200 ms: room for the GIL and a busy Whisper thread
ACTIVATE_TIMEOUT = 5.0
SYNCHRONIZE = 0x00100000
FORMATS = ((16000, 1), (48000, 2))   # (rate, channels) asked for, in order
COINIT_MULTITHREADED = 0


class ProcessLoopbackError(OSError):
    """Process loopback cannot be used (old Windows, activation or format refused, COM missing)."""


def windows_build() -> int:
    try:
        return int(sys.getwindowsversion().build)  # type: ignore[attr-defined]
    except Exception:
        return 0


def supported() -> bool:
    return IS_WINDOWS and windows_build() >= MIN_BUILD


# ---------------------------------------------------------------- resampling (pure numpy, stateful)
class Resampler:
    """Streaming linear-interpolation resampler to 16 kHz mono float32. It keeps its phase and the last
    sample between calls, so chunk boundaries neither drift nor click. Rates over 2x16 kHz get a short
    moving-average low-pass first (enough for speech recognition)."""

    def __init__(self, np: Any, rate: int, out_rate: int = 16000) -> None:
        self.np, self.rate, self.out = np, int(rate), int(out_rate)
        self.step = self.rate / self.out
        self.pos = 0.0                    # position of the next output sample, relative to self.prev
        self.prev: Any = None             # last input sample of the previous chunk
        self.taps = max(1, int(round(self.step))) if self.step > 2 else 1
        self.tail: Any = None             # last taps-1 raw samples, for the filter

    def __call__(self, x: Any) -> Any:
        np = self.np
        x = np.asarray(x, dtype=np.float32)
        if self.rate == self.out or not len(x):
            return x
        if self.taps > 1:
            k = self.taps
            head = self.tail if self.tail is not None else np.repeat(x[:1], k - 1)
            ext = np.concatenate([head, x])
            c = np.cumsum(np.concatenate([[0.0], ext]), dtype=np.float64)
            self.tail = ext[-(k - 1):]
            x = ((c[k:] - c[:-k]) / k).astype(np.float32)
        src = x if self.prev is None else np.concatenate([[self.prev], x])
        last = len(src) - 1
        if last < 1:
            self.prev = src[-1]
            return np.zeros(0, dtype=np.float32)
        n = int((last - self.pos) // self.step) + 1 if last >= self.pos else 0
        idx = self.pos + self.step * np.arange(n)
        out = np.interp(idx, np.arange(len(src)), src).astype(np.float32)
        self.pos = self.pos + self.step * n - last      # relative to the new prev (src[last])
        self.prev = src[last]
        return out


# ---------------------------------------------------------------- COM declarations (built on first use)
_com: dict[str, Any] = {}
_com_lock = threading.Lock()


def _declare() -> dict[str, Any]:
    """COM interfaces and the activation function; comtypes is imported only when capture starts."""
    with _com_lock:
        if _com:
            return _com
        if not IS_WINDOWS:
            raise ProcessLoopbackError("process loopback needs Windows")
        first = "comtypes" not in sys.modules
        had_flags = hasattr(sys, "coinit_flags")
        old_flags = getattr(sys, "coinit_flags", None)
        try:
            if first:          # importing comtypes initialises COM on this thread: make it the MTA we need
                sys.coinit_flags = COINIT_MULTITHREADED  # type: ignore[attr-defined]
            import comtypes
            from comtypes import COMMETHOD, COMObject, GUID, HRESULT, IUnknown
        except Exception as e:  # noqa: BLE001
            raise ProcessLoopbackError(f"comtypes unavailable: {e}") from e
        finally:
            if first:
                if had_flags:
                    sys.coinit_flags = old_flags  # type: ignore[attr-defined]
                else:
                    try:
                        del sys.coinit_flags  # type: ignore[attr-defined]
                    except AttributeError:
                        pass

        class WAVEFORMATEX(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort),
                        ("nSamplesPerSec", ctypes.c_uint32), ("nAvgBytesPerSec", ctypes.c_uint32),
                        ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                        ("cbSize", ctypes.c_ushort)]

        class BLOB(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("pBlobData", ctypes.POINTER(ctypes.c_ubyte))]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [("vt", ctypes.c_ushort), ("wReserved1", ctypes.c_ushort), ("wReserved2", ctypes.c_ushort),
                        ("wReserved3", ctypes.c_ushort), ("blob", BLOB)]

        class LOOPBACK_PARAMS(ctypes.Structure):
            _fields_ = [("TargetProcessId", ctypes.c_uint32), ("ProcessLoopbackMode", ctypes.c_int)]

        class ACTIVATION_PARAMS(ctypes.Structure):
            _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", LOOPBACK_PARAMS)]

        class IActivateAudioInterfaceAsyncOperation(IUnknown):
            _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
            _methods_ = [COMMETHOD([], HRESULT, "GetActivateResult",
                                   (["out"], ctypes.POINTER(HRESULT), "activateResult"),
                                   (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "activatedInterface"))]

        class IActivateAudioInterfaceCompletionHandler(IUnknown):
            _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
            _methods_ = [COMMETHOD([], HRESULT, "ActivateCompleted",
                                   (["in"], ctypes.POINTER(IActivateAudioInterfaceAsyncOperation), "op"))]

        class IAgileObject(IUnknown):
            # the completion arrives on an MTA worker: a non-agile handler is refused (E_ILLEGAL_METHOD_CALL)
            _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
            _methods_: list[Any] = []

        class IAudioClient(IUnknown):
            _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
            _methods_ = [
                COMMETHOD([], HRESULT, "Initialize", (["in"], ctypes.c_int, "ShareMode"),
                          (["in"], ctypes.c_uint32, "StreamFlags"), (["in"], ctypes.c_longlong, "hnsBufferDuration"),
                          (["in"], ctypes.c_longlong, "hnsPeriodicity"), (["in"], ctypes.POINTER(WAVEFORMATEX), "fmt"),
                          (["in"], ctypes.POINTER(GUID), "AudioSessionGuid")),
                COMMETHOD([], HRESULT, "GetBufferSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "frames")),
                COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], ctypes.POINTER(ctypes.c_longlong), "lat")),
                COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], ctypes.POINTER(ctypes.c_uint32), "pad")),
                COMMETHOD([], HRESULT, "IsFormatSupported", (["in"], ctypes.c_int, "ShareMode"),
                          (["in"], ctypes.POINTER(WAVEFORMATEX), "fmt"),
                          (["out"], ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)), "closest")),
                COMMETHOD([], HRESULT, "GetMixFormat", (["out"], ctypes.POINTER(ctypes.POINTER(WAVEFORMATEX)), "f")),
                COMMETHOD([], HRESULT, "GetDevicePeriod", (["out"], ctypes.POINTER(ctypes.c_longlong), "dflt"),
                          (["out"], ctypes.POINTER(ctypes.c_longlong), "minimum")),
                COMMETHOD([], HRESULT, "Start"),
                COMMETHOD([], HRESULT, "Stop"),
                COMMETHOD([], HRESULT, "Reset"),
                COMMETHOD([], HRESULT, "SetEventHandle", (["in"], ctypes.c_void_p, "h")),
                COMMETHOD([], HRESULT, "GetService", (["in"], ctypes.POINTER(GUID), "riid"),
                          (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppv")),
            ]

        class IAudioCaptureClient(IUnknown):
            _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
            _methods_ = [
                COMMETHOD([], HRESULT, "GetBuffer", (["out"], ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)), "data"),
                          (["out"], ctypes.POINTER(ctypes.c_uint32), "frames"),
                          (["out"], ctypes.POINTER(ctypes.c_uint32), "flags"),
                          (["out"], ctypes.POINTER(ctypes.c_uint64), "devpos"),
                          (["out"], ctypes.POINTER(ctypes.c_uint64), "qpcpos")),
                COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], ctypes.c_uint32, "frames")),
                COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "frames")),
            ]

        class Handler(COMObject):
            _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

            def __init__(self) -> None:
                super().__init__()
                self.done = threading.Event()
                self.hr: Any = None
                self.client: Any = None

            def ActivateCompleted(self, op: Any) -> int:  # noqa: N802 - COM name
                try:
                    hr, punk = op.GetActivateResult()
                    self.hr = int(hr) if hr is not None else -1
                    if self.hr == 0 and punk:
                        self.client = punk.QueryInterface(IAudioClient)
                except BaseException as e:  # noqa: BLE001 - never raise into COM
                    self.hr = e
                finally:
                    self.done.set()
                return 0

        try:
            mmdevapi = ctypes.WinDLL("mmdevapi.dll")
            activate = ctypes.WINFUNCTYPE(
                HRESULT, ctypes.c_wchar_p, ctypes.POINTER(GUID), ctypes.POINTER(PROPVARIANT), ctypes.POINTER(IUnknown),
                ctypes.POINTER(ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)),
            )(("ActivateAudioInterfaceAsync", mmdevapi))
        except (OSError, AttributeError) as e:
            raise ProcessLoopbackError(f"ActivateAudioInterfaceAsync unavailable: {e}") from e
        _com.update(comtypes=comtypes, WAVEFORMATEX=WAVEFORMATEX, PROPVARIANT=PROPVARIANT,
                    LOOPBACK_PARAMS=LOOPBACK_PARAMS, ACTIVATION_PARAMS=ACTIVATION_PARAMS, Handler=Handler,
                    IAudioClient=IAudioClient, IAudioCaptureClient=IAudioCaptureClient,
                    IActivateAudioInterfaceAsyncOperation=IActivateAudioInterfaceAsyncOperation, activate=activate)
        return _com


def init_thread() -> dict[str, Any]:
    """Load the declarations and join the multithreaded apartment on this thread (the activation completes on
    an MTA worker; an STA thread that already exists, e.g. Tk's, still works because the handler is agile)."""
    c = _declare()
    try:
        ctypes.windll.ole32.CoInitializeEx(None, COINIT_MULTITHREADED)   # S_FALSE / RPC_E_CHANGED_MODE: fine
    except OSError:
        pass
    return c


def _hex(hr: int) -> str:
    return f"0x{int(hr) & 0xFFFFFFFF:08X}"


def _activate(c: dict[str, Any], pid: int, include: bool) -> Any:
    mode = PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE if include else \
        PROCESS_LOOPBACK_MODE_EXCLUDE_TARGET_PROCESS_TREE
    params = c["ACTIVATION_PARAMS"](AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
                                    c["LOOPBACK_PARAMS"](ctypes.c_uint32(pid), mode))
    pv = c["PROPVARIANT"]()
    pv.vt = VT_BLOB
    pv.blob.cbSize = ctypes.sizeof(params)
    pv.blob.pBlobData = ctypes.cast(ctypes.pointer(params), ctypes.POINTER(ctypes.c_ubyte))
    handler = c["Handler"]()
    op = ctypes.POINTER(c["IActivateAudioInterfaceAsyncOperation"])()
    try:
        hr = c["activate"](VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, ctypes.byref(c["IAudioClient"]._iid_),
                           ctypes.byref(pv), handler, ctypes.byref(op))
    except OSError as e:          # comtypes raises COMError/OSError for a failed HRESULT
        raise ProcessLoopbackError(f"activation refused: {e}") from e
    if hr:
        raise ProcessLoopbackError(f"activation refused ({_hex(hr)})")
    # params / pv / handler stay referenced until here: the call reads them until it completes
    if not handler.done.wait(ACTIVATE_TIMEOUT):
        raise ProcessLoopbackError("activation timed out")
    del op
    if isinstance(handler.hr, BaseException):
        raise ProcessLoopbackError(f"activation failed: {handler.hr}")
    if handler.hr != 0 or handler.client is None:
        raise ProcessLoopbackError(f"activation failed ({_hex(handler.hr or 0)})")
    return handler.client


class ProcessLoopback:
    """One open process-loopback stream. ``read()`` returns new 16 kHz mono float32 audio (possibly empty)
    and whether any packet arrived; use it from the thread that opened it."""

    def __init__(self, pid: int, include: bool, np: Any) -> None:
        self.pid, self.include, self.np = int(pid), include, np
        self.rate = self.channels = 0
        self._client: Any = None
        self._cap: Any = None
        self._resample: Any = None
        self._proc = None
        c = init_thread()
        last: Exception | None = None
        for rate, ch in FORMATS:
            client = _activate(c, self.pid, include)
            fmt = c["WAVEFORMATEX"](1, ch, rate, rate * ch * 2, ch * 2, 16, 0)
            flags = (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                     | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY)
            try:
                client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, BUFFER_HNS, 0, ctypes.byref(fmt), None)
                cap = client.GetService(ctypes.byref(c["IAudioCaptureClient"]._iid_))
                self._cap = cap.QueryInterface(c["IAudioCaptureClient"])
                client.Start()
            except Exception as e:  # noqa: BLE001 - try the next format
                last = e
                self._cap = cap = client = None     # released before the next activation
                continue
            self._client, self.rate, self.channels = client, rate, ch
            break
        if self._client is None:
            raise ProcessLoopbackError(f"no capture format accepted: {last}")
        self._resample = Resampler(np, self.rate)
        if include and IS_WINDOWS:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self._k32 = k32
            self._proc = k32.OpenProcess(SYNCHRONIZE, False, self.pid) or None

    def target_alive(self) -> bool:
        if self._proc is None:
            return True
        return self._k32.WaitForSingleObject(self._proc, 0) != 0     # WAIT_OBJECT_0: the process ended

    def read(self) -> tuple[Any, bool]:
        np = self.np
        cap = self._cap
        out = []
        got = False
        size = cap.GetNextPacketSize()
        while size:
            data, frames, flags, _dev, _qpc = cap.GetBuffer()
            try:
                if frames:
                    got = True
                    if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                        mono = np.zeros(frames, dtype=np.float32)
                    else:
                        raw = ctypes.string_at(data, frames * self.channels * 2)
                        pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                        mono = pcm.reshape(-1, self.channels).mean(axis=1) if self.channels > 1 else pcm
                    out.append(self._resample(mono))
            finally:
                cap.ReleaseBuffer(frames)
            size = cap.GetNextPacketSize()
        if not out:
            return np.zeros(0, dtype=np.float32), got
        return (out[0] if len(out) == 1 else np.concatenate(out)), got

    def close(self) -> None:
        client, self._client = self._client, None
        self._cap = None
        if client is not None:
            try:
                client.Stop()
            except Exception:
                pass
        if self._proc is not None:
            try:
                self._k32.CloseHandle(self._proc)
            except Exception:
                pass
            self._proc = None
