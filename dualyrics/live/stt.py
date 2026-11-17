"""Offline speech recognition with faster-whisper: GPU (CUDA float16) when available, else CPU int8."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from dualyrics.live import deps
from dualyrics.live.text import Recognition

log = logging.getLogger(__name__)


def plan_devices(pref: str, gpu_ok: bool) -> list[tuple[str, str]]:
    """(device, compute type) to try in order for the preference auto / cpu / gpu.

    The GPU is tried only when ``gpu_ok`` (a CUDA device and the cuBLAS/cuDNN DLLs exist); CPU int8 is
    always the last resort, so asking for the GPU on a PC without one still gives captions.
    """
    cpu = ("cpu", "int8")
    gpu = [("cuda", "float16"), ("cuda", "int8_float16")]
    if pref == "cpu" or not gpu_ok:
        return [cpu]
    return gpu + [cpu]


class Transcriber:
    def __init__(self, model_dir: Path, device_pref: str = "auto") -> None:
        self.model_dir = model_dir
        self.device_pref = device_pref
        self.model: Any = None
        self.device = ""
        self.compute = ""
        self.note = ""          # e.g. why the GPU was not used

    def load(self) -> str:
        """Load the model and run a warm-up pass (that is where a missing CUDA DLL shows up)."""
        np = deps.import_module("numpy")
        fw = deps.import_module("faster_whisper")
        errors = []
        gpu_ok = self.device_pref != "cpu" and deps.gpu_usable()
        if self.device_pref == "gpu" and not gpu_ok:
            self.note = "no usable NVIDIA GPU or CUDA libraries; using the CPU"
            log.warning("whisper: %s", self.note)
        _quiet_dll_errors()
        for device, compute in plan_devices(self.device_pref, gpu_ok):
            try:
                threads = max(2, min(8, (os.cpu_count() or 4) - 1)) if device == "cpu" else 0
                model = fw.WhisperModel(str(self.model_dir), device=device, compute_type=compute,
                                        cpu_threads=threads)
                segs, _info = model.transcribe(np.zeros(16000, dtype=np.float32), beam_size=1, language="en",
                                               without_timestamps=True)
                list(segs)
                self.model, self.device, self.compute = model, device, compute
                log.info("whisper %s loaded on %s (%s)", self.model_dir.name, device, compute)
                return device
            except Exception as e:
                errors.append(f"{device}/{compute}: {e}")
                log.warning("whisper load on %s/%s failed: %s", device, compute, e)
        raise RuntimeError("; ".join(errors) or "no device")

    def transcribe(self, audio16k: Any, start: float, end: float, language: str | None = None,
                   prompt: str = "", task: str = "transcribe") -> list[Recognition]:
        """One audio segment -> recognitions (usually one; Whisper may split long speech).

        ``task="translate"`` (multilingual models) writes English for speech in any language; the detected
        spoken language is still reported in ``Recognition.language``."""
        if self.model is None:
            raise RuntimeError("model not loaded")
        segs, info = self.model.transcribe(
            audio16k, language=language or None, task=task if task in ("transcribe", "translate") else "transcribe",
            beam_size=1 if self.device == "cpu" else 3,
            vad_filter=False, condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=prompt[-200:] or None, temperature=0.0)
        lang = getattr(info, "language", "") or (language or "")
        out = []
        for s in segs:
            text = re.sub(r"\s+", " ", s.text or "").strip()
            if text:
                out.append(Recognition(text, start, end, float(s.avg_logprob), float(s.no_speech_prob),
                                       float(s.compression_ratio), lang))
        return _join(out)


def _quiet_dll_errors() -> None:
    """No "DLL not found" message boxes from Windows if a GPU library is broken: fail with an error instead."""
    if os.name == "nt":
        try:
            import ctypes

            k32 = ctypes.windll.kernel32
            k32.SetErrorMode(k32.GetErrorMode() | 0x0001 | 0x8000)  # SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX
        except Exception:
            pass


def _join(recs: list[Recognition]) -> list[Recognition]:
    """Whisper may split one segment into sentences: keep one recognition per audio segment, with the
    average confidence of its parts weighted by length (so the hallucination filter judges the whole)."""
    if len(recs) <= 1:
        return recs
    first = recs[0]
    w = [max(1, len(r.text)) for r in recs]
    avg = lambda vals: sum(v * k for v, k in zip(vals, w)) / sum(w)  # noqa: E731
    return [Recognition(" ".join(r.text for r in recs), first.start, first.end,
                        avg([r.avg_logprob for r in recs]), avg([r.no_speech_prob for r in recs]),
                        max(r.compression_ratio for r in recs), first.language)]
