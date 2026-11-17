"""Playback of dubbed speech: PCM through ``waveOut`` (Gemini, Piper, Gemini Live streams), MP3 through MCI
(Edge voices). Both play in our own process, so ducking never lowers them. Use one ``Player`` from one thread.
"""

from __future__ import annotations

import ctypes
import io
import logging
import os
import sys
import tempfile
import threading
import time
import wave
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


@dataclass
class Clip:
    """One spoken line: raw 16-bit mono PCM (``pcm`` at ``rate``) or MP3 bytes (``mp3``)."""
    text: str
    pcm: bytes = b""
    rate: int = 24000
    mp3: bytes = b""
    seconds: float = 0.0
    engine: str = ""

    @property
    def empty(self) -> bool:
        return not self.pcm and not self.mp3


def pcm_seconds(pcm: bytes, rate: int) -> float:
    return len(pcm) / 2 / max(1, rate)


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """(16-bit mono PCM, sample rate) of a WAV file (Piper). Stereo is mixed down."""
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        import array

        a = array.array("h", frames)
        frames = array.array("h", (sum(a[i:i + ch]) // ch for i in range(0, len(a), ch))).tobytes()
    return frames, rate


def scale_pcm(pcm: bytes, gain: float) -> bytes:
    """16-bit PCM times ``gain`` (0..1), for the voice volume of waveOut clips."""
    if gain >= 0.999:
        return pcm
    import array

    a = array.array("h", pcm)
    g = max(0.0, gain)
    return array.array("h", (int(v * g) for v in a)).tobytes()


# ---------------------------------------------------------------- waveOut (PCM, also streamed)
if IS_WINDOWS:
    class WAVEFORMATEX(ctypes.Structure):
        _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                    ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                    ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]

    class WAVEHDR(ctypes.Structure):
        pass

    WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                        ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                        ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                        ("lpNext", ctypes.POINTER(WAVEHDR)), ("reserved", ctypes.c_size_t)]
    _winmm = ctypes.WinDLL("winmm")
    _winmm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, ctypes.POINTER(WAVEFORMATEX),
                                   ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
    _winmm.waveOutPrepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutUnprepareHeader.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutWrite.argtypes = [ctypes.c_void_p, ctypes.POINTER(WAVEHDR), wintypes.UINT]
    _winmm.waveOutReset.argtypes = [ctypes.c_void_p]
    _winmm.waveOutClose.argtypes = [ctypes.c_void_p]
    _winmm.mciSendStringW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE]
    _winmm.mciSendStringW.restype = wintypes.DWORD

WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 0x1


class WaveOut:
    """A waveOut device for one sample rate. ``write`` queues PCM and returns at once."""

    def __init__(self, rate: int) -> None:
        if not IS_WINDOWS:
            raise OSError("waveOut needs Windows")
        self.rate = rate
        fmt = WAVEFORMATEX(1, 1, rate, rate * 2, 2, 16, 0)
        self._h = ctypes.c_void_p()
        res = _winmm.waveOutOpen(ctypes.byref(self._h), WAVE_MAPPER, ctypes.byref(fmt), 0, 0, 0)
        if res != 0:
            raise OSError(f"waveOutOpen failed ({res})")
        self._queue: list[tuple[WAVEHDR, ctypes.Array]] = []

    def write(self, pcm: bytes) -> None:
        if not pcm:
            return
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        hdr = WAVEHDR()
        hdr.lpData = ctypes.cast(buf, ctypes.c_void_p)
        hdr.dwBufferLength = len(pcm)
        _winmm.waveOutPrepareHeader(self._h, ctypes.byref(hdr), ctypes.sizeof(hdr))
        _winmm.waveOutWrite(self._h, ctypes.byref(hdr), ctypes.sizeof(hdr))
        self._queue.append((hdr, buf))

    def pending(self) -> float:
        """Seconds of audio still queued (frees finished buffers)."""
        left = 0
        keep = []
        for hdr, buf in self._queue:
            if hdr.dwFlags & WHDR_DONE:
                _winmm.waveOutUnprepareHeader(self._h, ctypes.byref(hdr), ctypes.sizeof(hdr))
            else:
                keep.append((hdr, buf))
                left += hdr.dwBufferLength
        self._queue = keep
        return left / 2 / self.rate

    def stop(self) -> None:
        _winmm.waveOutReset(self._h)
        self.pending()

    def close(self) -> None:
        try:
            self.stop()
        finally:
            _winmm.waveOutClose(self._h)


def _decode_mp3(data: bytes, rate: int = 24000) -> tuple[bytes, int] | None:
    """MP3 -> 16-bit mono PCM with PyAV; None when PyAV is not available or decoding fails."""
    try:
        import io
        import av  # type: ignore
        pcm = bytearray()
        with av.open(io.BytesIO(data)) as container:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            for frame in container.decode(audio=0):
                for out in resampler.resample(frame):
                    pcm += out.to_ndarray().tobytes()
            for out in resampler.resample(None):
                pcm += out.to_ndarray().tobytes()
        return (bytes(pcm), rate) if pcm else None
    except Exception:  # noqa: BLE001 - optional path, MCI is the fallback
        return None


# ---------------------------------------------------------------- MCI (MP3 files)
def _mci(cmd: str) -> str:
    buf = ctypes.create_unicode_buffer(256)
    err = _winmm.mciSendStringW(cmd, buf, 255, None)
    if err:
        raise OSError(f"MCI error {err}: {cmd.split(' ')[0]}")
    return buf.value


class Player:
    """Plays one clip at a time (call from the player thread). ``stop()`` may be called from any thread."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._outs: dict[int, WaveOut] = {}
        self._n = 0
        self.tmp = Path(tempfile.gettempdir()) / "dualyrics-dub"
        self.started_at = 0.0
        self.length = 0.0

    def remaining(self, now: float | None = None) -> float:
        if not self.length:
            return 0.0
        return max(0.0, self.started_at + self.length - (time.monotonic() if now is None else now))

    def stop(self) -> None:
        self._stop.set()

    def play(self, clip: Clip, volume: float = 1.0) -> bool:
        """Block until ``clip`` finished (True) or ``stop()`` was called (False)."""
        self._stop.clear()
        self.started_at, self.length = time.monotonic(), clip.seconds
        try:
            if clip.pcm:
                return self._play_pcm(clip.pcm, clip.rate, volume)
            if clip.mp3:
                return self._play_mp3(clip.mp3, volume)
            return True
        finally:
            self.length = 0.0

    def _out(self, rate: int) -> WaveOut:
        out = self._outs.get(rate)
        if out is None:
            out = self._outs[rate] = WaveOut(rate)
        return out

    def _play_pcm(self, pcm: bytes, rate: int, volume: float) -> bool:
        out = self._out(rate)
        out.write(scale_pcm(pcm, volume))
        while out.pending() > 0:
            if self._stop.wait(0.02):
                out.stop()
                return False
        return True

    def stream(self, pcm: bytes, rate: int, volume: float = 1.0) -> float:
        """Queue a chunk of a live stream (Gemini Live); returns seconds still queued."""
        out = self._out(rate)
        out.write(scale_pcm(pcm, volume))
        return out.pending()

    def stream_pending(self, rate: int) -> float:
        out = self._outs.get(rate)
        return out.pending() if out is not None else 0.0

    def stop_stream(self) -> None:
        for out in self._outs.values():
            out.stop()

    def _play_mp3(self, data: bytes, volume: float) -> bool:
        # Windows "N" editions ship without the MP3 (mpegvideo) MCI driver -> MCI error 266. Decode with PyAV
        # (installed with Live mode) and play the PCM through waveOut, which every edition has; MCI stays the fallback.
        pcm = _decode_mp3(data)
        if pcm:
            return self._play_pcm(pcm[0], pcm[1], volume)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._n += 1
        path = self.tmp / f"clip{os.getpid()}-{self._n % 8}.mp3"
        path.write_bytes(data)
        alias = f"dub{self._n}"
        _mci(f'open "{path}" type mpegvideo alias {alias}')
        try:
            _mci(f"setaudio {alias} volume to {int(max(0.0, min(1.0, volume)) * 1000)}")
            _mci(f"play {alias}")
            time.sleep(0.05)
            while _mci(f"status {alias} mode") == "playing":
                if self._stop.wait(0.02):
                    _mci(f"stop {alias}")
                    return False
            return True
        finally:
            try:
                _mci(f"close {alias}")
            except OSError:
                pass

    def close(self) -> None:
        for out in self._outs.values():
            try:
                out.close()
            except Exception:
                pass
        self._outs.clear()
