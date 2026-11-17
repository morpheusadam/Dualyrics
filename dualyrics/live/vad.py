"""Energy-based voice activity segmentation: cut the speaker audio at natural pauses.

Works on frames of any type (the caller passes each frame with its RMS level), so the logic has no
numpy dependency and is unit-tested. A segment ends after a short pause, or is force-cut at the
maximum length; after a forced cut a short tail is kept so the next piece starts with context.
"""

from __future__ import annotations

from typing import Any


def threshold_params(sensitivity: int) -> tuple[float, float]:
    """(absolute floor, noise multiplier) for sensitivity 1 (only loud speech) .. 10 (whispers)."""
    s = max(1, min(10, int(sensitivity)))
    return 0.016 * (0.8 ** (s - 1)), 4.0 - 0.25 * (s - 1)


class Segmenter:
    def __init__(self, frame_sec: float = 0.03, sensitivity: int = 5, max_segment: float = 6.0,
                 end_silence: float = 0.32, min_speech: float = 0.25, pre_roll: float = 0.2) -> None:
        self.frame_sec = frame_sec
        self.max_segment = max_segment
        self.end_silence = end_silence
        self.min_speech = min_speech
        self.pre_frames = max(1, int(round(pre_roll / frame_sec)))
        self.floor, self.mult = threshold_params(sensitivity)
        self.noise = 0.003
        self.level = 0.0                  # last RMS (for a level meter)
        self.speaking = False
        self._pre: list[Any] = []
        self._seg: list[Any] = []
        self._silence = 0.0
        self._len = 0.0
        self._voiced = 0.0
        self.forced = False               # the last emitted segment was force-cut (speech continues)

    def set_sensitivity(self, sensitivity: int) -> None:
        self.floor, self.mult = threshold_params(sensitivity)

    def reset(self) -> None:
        self.speaking = False
        self._pre, self._seg = [], []
        self._silence = self._len = self._voiced = 0.0

    def is_voice(self, rms: float) -> bool:
        return rms > max(self.floor, self.noise * self.mult)

    def feed(self, frame: Any, rms: float) -> list[list[Any]]:
        """Add one frame. Returns finished segments (lists of frames), usually none or one."""
        self.level = rms
        voice = self.is_voice(rms)
        out: list[list[Any]] = []
        if not self.speaking:
            if not voice:  # track the noise floor only while nobody speaks
                self.noise = 0.97 * self.noise + 0.03 * rms
            self._pre.append(frame)
            self._pre = self._pre[-self.pre_frames:]
            if voice:
                self.speaking = True
                self._seg = list(self._pre)
                self._len = len(self._seg) * self.frame_sec
                self._silence = self._voiced = 0.0
                self._pre = []
            return out
        self._seg.append(frame)
        self._len += self.frame_sec
        if voice:
            self._silence = 0.0
            self._voiced += self.frame_sec
        else:
            self._silence += self.frame_sec
        paused = self._silence >= self.end_silence
        if paused or self._len >= self.max_segment:
            if self._voiced >= self.min_speech:
                out.append(self._seg)
                self.forced = not paused
            # long speech without a pause: keep the tail as context for the next piece
            self.speaking = not paused
            self._seg = self._seg[-self.pre_frames:] if self.speaking else []
            self._len = len(self._seg) * self.frame_sec
            self._silence = self._voiced = 0.0
        return out
