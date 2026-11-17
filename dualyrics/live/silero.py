"""Silero VAD: the probability of speech in each 32 ms window of 16 kHz audio.

The model is snakers4/silero-vad (MIT). faster-whisper ships it as ``assets/silero_vad_v6.onnx`` and depends on
onnxruntime, so Live mode needs no extra download; without onnxruntime the energy (RMS) segmenter is used.
The model is run one window at a time, carrying its recurrent state and the last 64 samples of context from
window to window, as the streaming wrapper in silero-vad does.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from dualyrics.live import deps

log = logging.getLogger(__name__)

RATE = 16000
WINDOW = 512            # samples per window at 16 kHz (32 ms)
CONTEXT = 64            # samples of the previous window the model also sees
WINDOW_SEC = WINDOW / RATE
MODEL_FILES = ("silero_vad_v6.onnx", "silero_vad.onnx")


def _find(name: str) -> Any:
    try:
        return importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None


def model_path() -> Path | None:
    """The Silero ONNX file inside the installed faster-whisper package, or None."""
    deps.activate_runtime()
    spec = _find("faster_whisper")
    if spec is None or not spec.origin:
        return None
    assets = Path(spec.origin).parent / "assets"
    for name in MODEL_FILES:
        if (assets / name).is_file():
            return assets / name
    return None


def available() -> bool:
    """onnxruntime and the model file are installed (nothing is imported)."""
    return _find("onnxruntime") is not None and model_path() is not None


class SileroVad:
    """Call with one window of ``WINDOW`` float32 samples at 16 kHz; returns the speech probability 0..1."""

    def __init__(self, path: Path | None = None) -> None:
        self.np = deps.import_module("numpy")
        ort = deps.import_module("onnxruntime")
        path = path or model_path()
        if path is None:
            raise RuntimeError("the Silero VAD model is not installed")
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.enable_cpu_mem_arena = False
        opts.log_severity_level = 4
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=opts)
        names = {i.name for i in self.session.get_inputs()}
        self._state_input = "state" in names       # silero-vad v5 file; faster-whisper's v6 file has h and c
        self.reset()

    def reset(self) -> None:
        np = self.np
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, window: Any) -> float:
        np = self.np
        w = np.asarray(window, dtype=np.float32)
        if len(w) < WINDOW:
            w = np.pad(w, (0, WINDOW - len(w)))
        w = w[:WINDOW]
        x = np.concatenate([self._context, w])[None, :]
        self._context = w[-CONTEXT:].copy()
        if self._state_input:
            out, self._state = self.session.run(None, {"input": x, "state": self._state,
                                                       "sr": np.array(RATE, dtype=np.int64)})
        else:
            out, self._h, self._c = self.session.run(None, {"input": x, "h": self._h, "c": self._c})
        return float(np.asarray(out).reshape(-1)[0])


class To16k:
    """Streaming linear resampler from the device rate to 16 kHz that keeps its phase across frames (no clicks
    or lost samples at frame edges). 48 kHz, the usual loopback rate, is a plain 3:1 average."""

    def __init__(self, np: Any) -> None:
        self.np = np
        self._rate = 0
        self._buf = np.zeros(0, dtype=np.float32)
        self._pos = 0.0

    def __call__(self, frame: Any, rate: int) -> Any:
        np = self.np
        frame = np.asarray(frame, dtype=np.float32)
        if rate == RATE:
            return frame
        if rate != self._rate:
            self._rate, self._buf, self._pos = rate, np.zeros(0, dtype=np.float32), 0.0
        buf = np.concatenate([self._buf, frame])
        if rate == 48000:
            n = len(buf) // 3 * 3
            self._buf = buf[n:].copy()
            return buf[:n].reshape(-1, 3).mean(axis=1)
        step = rate / RATE
        if len(buf) < 2:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        count = int((len(buf) - 1 - self._pos) // step) + 1
        if count <= 0:
            self._buf = buf
            return np.zeros(0, dtype=np.float32)
        pts = self._pos + step * np.arange(count)
        out = np.interp(pts, np.arange(len(buf)), buf).astype(np.float32)
        nxt = self._pos + step * count
        drop = int(nxt)
        self._buf = buf[drop:].copy()
        self._pos = nxt - drop
        return out
