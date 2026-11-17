"""Voice activity segmentation: cut the speaker audio at natural pauses.

Two segmenters with the same interface (``feed``, ``speaking``, ``forced``, ``peek``, ``trim_front``, ...):

- ``SileroSegmenter`` (default when onnxruntime is installed): the Silero VAD speech probability of each 32 ms
  window, ~96 ms of pre-roll, an end-of-speech pause that adapts to how the speaker pauses and gets shorter
  the longer the piece is, a cut at the quietest point when the piece reaches its maximum length, short
  pieces merged with the next one, and pieces that are mostly not speech dropped. The rules follow
  ``vad_processor.py`` of TheDeathDragon/LiveTranslate (MIT); see docs/ATTRIBUTION_NOTES.md.
- ``Segmenter``: the RMS energy of each frame against a tracked noise floor (the fallback without onnxruntime).

Both work on frames of any type: the probability or RMS is passed in, so the logic has no numpy dependency and
is unit-tested. ``gen`` changes whenever the buffered piece is emitted or dropped, so a trim requested for a
piece (interim commits) never cuts the next one.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable

ENERGY, SILERO = "energy", "silero"


def threshold_params(sensitivity: int) -> tuple[float, float]:
    """(absolute floor, noise multiplier) for sensitivity 1 (only loud speech) .. 10 (whispers)."""
    s = max(1, min(10, int(sensitivity)))
    return 0.016 * (0.8 ** (s - 1)), 4.0 - 0.25 * (s - 1)


def silero_threshold(sensitivity: int) -> float:
    """Speech probability threshold for sensitivity 1 (0.7, only clear speech) .. 10 (0.25)."""
    s = max(1, min(10, int(sensitivity)))
    return round(0.5 - (s - 5) * 0.05, 3)


class Segmenter:
    """Energy (RMS) segmenter."""

    rate = 0                              # frames stay at the device rate

    def __init__(self, frame_sec: float = 0.03, sensitivity: int = 5, max_segment: float = 6.0,
                 end_silence: float = 0.32, min_speech: float = 0.25, pre_roll: float = 0.2) -> None:
        self.kind = ENERGY
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
        self.trimmed = False              # the front of this piece was already committed (interim)
        self.gen = 0

    def set_sensitivity(self, sensitivity: int) -> None:
        self.floor, self.mult = threshold_params(sensitivity)

    def reset(self) -> None:
        self.speaking = False
        self._pre, self._seg = [], []
        self._silence = self._len = self._voiced = 0.0
        self.trimmed = False
        self.gen += 1

    def is_voice(self, rms: float) -> bool:
        return rms > max(self.floor, self.noise * self.mult)

    @property
    def pending(self) -> bool:
        """A piece is being collected."""
        return self.speaking

    def buffered(self) -> float:
        """Seconds of the piece being spoken now (0 while nobody speaks)."""
        return self._len if self.speaking else 0.0

    def peek(self) -> tuple[list[Any], int] | None:
        """(frames of the piece so far, its generation), or None while nobody speaks."""
        return (list(self._seg), self.gen) if self.speaking and self._seg else None

    def trim_front(self, seconds: float, gen: int) -> bool:
        """Drop the first ``seconds`` of the piece (already committed). False when the piece is gone."""
        if gen != self.gen or not self.speaking:
            return False
        n = min(len(self._seg), int(seconds / self.frame_sec))
        del self._seg[:n]
        self._len = len(self._seg) * self.frame_sec
        self._voiced = min(self._voiced, self._len)
        self.trimmed = True
        return True

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
            if self._voiced >= self.min_speech or (self.trimmed and self._voiced > 0):
                out.append(self._seg)
                self.forced = not paused
            # long speech without a pause: keep the tail as context for the next piece
            self.speaking = not paused
            self._seg = self._seg[-self.pre_frames:] if self.speaking else []
            self._len = len(self._seg) * self.frame_sec
            self._silence = self._voiced = 0.0
            self.trimmed = False
            self.gen += 1
        return out


class SileroSegmenter:
    """Segmenter on Silero VAD probabilities of 32 ms windows (16 kHz).

    ``feed(frame, rms)`` takes capture frames at any rate (``resample`` turns them into 16 kHz samples and
    ``prob`` scores each full window); ``feed_window(window, prob)`` is the pure logic, used by the tests.
    Emitted segments are lists of 16 kHz windows (``rate`` = 16000).
    """

    rate = 16000
    TIERS = ((3.0, 0.75), (6.0, 0.5), (10.0, 0.25))   # (piece longer than s, end pause x factor)

    def __init__(self, prob: Callable[[Any], float] | None = None, sensitivity: int = 5, max_segment: float = 6.0,
                 end_silence: float = 0.5, min_speech: float = 0.3, pre_roll: float = 0.096,
                 window_sec: float = 0.032, merge_wait: float = 0.6, min_voiced: float = 0.12,
                 resample: Callable[[Any], Any] | None = None, np: Any = None) -> None:
        self.kind = SILERO
        self.prob = prob
        self.resample = resample
        self.np = np
        self.frame_sec = window_sec
        self.max_segment = max_segment
        self.end_silence = end_silence     # adapted from the speaker's pauses (0.3 .. 2.0 s)
        self.min_speech = min_speech       # seconds of speech windows; shorter pieces wait to merge
        self.merge_wait = merge_wait       # how long a short piece waits for more speech
        self.min_voiced = min_voiced       # a short piece that waited is still kept with this much speech
        self.pre_n = max(1, int(round(pre_roll / window_sec)))
        self.threshold = silero_threshold(sensitivity)
        self.pauses: deque[float] = deque(maxlen=50)
        self.level = 0.0
        self.last_prob = 0.0
        self.speaking = False
        self.forced = False
        self.trimmed = False
        self.gen = 0
        self._pre: deque[Any] = deque(maxlen=self.pre_n)
        self._buf: list[Any] = []
        self._conf: list[float] = []
        self._silence = 0                  # windows of silence since the last speech window
        self._held = 0                     # windows a short piece has waited
        self._pending: Any = None          # 16 kHz samples not yet a full window

    # -- settings
    def set_sensitivity(self, sensitivity: int) -> None:
        self.threshold = silero_threshold(sensitivity)

    def reset(self) -> None:
        self.speaking = False
        self._pre.clear()
        self._buf, self._conf = [], []
        self._silence = self._held = 0
        self.trimmed = False
        self._pending = None
        self.gen += 1

    # -- interim support
    @property
    def pending(self) -> bool:
        """A piece is being collected (also a short piece that waits for more speech)."""
        return self.speaking or bool(self._buf)

    def buffered(self) -> float:
        return len(self._buf) * self.frame_sec if self.speaking else 0.0

    def peek(self) -> tuple[list[Any], int] | None:
        return (list(self._buf), self.gen) if self.speaking and self._buf else None

    def trim_front(self, seconds: float, gen: int) -> bool:
        if gen != self.gen or not self._buf:
            return False
        n = min(len(self._buf), int(seconds / self.frame_sec))
        del self._buf[:n]
        del self._conf[:n]
        self.trimmed = True
        return True

    # -- end-of-speech pause
    def _adapt(self) -> None:
        """End pause = 75th percentile of the speaker's recent pauses x 1.2, within 0.3 .. 2.0 s."""
        if len(self.pauses) < 3:
            return
        p = sorted(self.pauses)
        p75 = p[min(int(len(p) * 0.75), len(p) - 1)]
        self.end_silence = max(0.3, min(2.0, p75 * 1.2))

    def silence_limit(self) -> int:
        """Windows of silence that end the piece now: shorter the longer the piece already is."""
        secs = len(self._buf) * self.frame_sec
        mult = 1.0
        for tier, m in self.TIERS:
            if secs >= tier:
                mult = m
        return max(3, int(round(self.end_silence * mult / self.frame_sec)))

    # -- frames
    def feed(self, frame: Any, rms: float) -> list[list[Any]]:
        self.level = rms
        np = self.np
        samples = self.resample(frame) if self.resample is not None else frame
        if np is None:                     # frames are windows already (tests)
            return self.feed_window(samples, self.prob(samples) if self.prob else 0.0)
        buf = samples if self._pending is None else np.concatenate([self._pending, samples])
        out: list[list[Any]] = []
        n = 512
        i = 0
        while len(buf) - i >= n:
            w = buf[i:i + n]
            i += n
            out.extend(self.feed_window(w, self.prob(w) if self.prob else 0.0))
        self._pending = buf[i:] if i < len(buf) else None
        return out

    def feed_window(self, w: Any, prob: float) -> list[list[Any]]:
        self.last_prob = prob
        voice = prob >= self.threshold
        out: list[list[Any]] = []
        if voice:
            if self.speaking and self._silence > 0:
                pause = self._silence * self.frame_sec
                if pause >= 0.1:
                    self.pauses.append(pause)
                    self._adapt()
            if not self.speaking:          # onset (or more speech for a short piece that waits)
                for p in self._pre:
                    self._buf.append(p)
                    self._conf.append(self.threshold)   # pre-roll must not look like a quiet split point
                self._pre.clear()
                self.speaking = True
                self._held = 0
            self._silence = 0
            self._buf.append(w)
            self._conf.append(prob)
        elif self.speaking:
            self._silence += 1
            self._buf.append(w)
            self._conf.append(prob)
        else:
            self._pre.append(w)
            if self._buf:                  # a short piece waits for more speech
                self._held += 1
                if self._held * self.frame_sec >= self.merge_wait:
                    seg = self._flush(self.min_voiced)
                    if seg:
                        self.forced = False
                        out.append(seg)
            return out
        if len(self._buf) * self.frame_sec >= self.max_segment:
            seg = self._split()
            if seg:
                out.append(seg)
            return out
        if self.speaking and self._silence >= self.silence_limit():
            if self._voiced_sec() >= self.min_speech or self.trimmed:
                seg = self._flush(0.0)
                if seg:
                    self.forced = False
                    out.append(seg)
            else:                          # too short: keep it and merge it with the next speech
                self.speaking = False
                self._silence = self._held = 0
        return out

    def _voiced_sec(self) -> float:
        return sum(1 for c in self._conf if c >= self.threshold) * self.frame_sec

    def _flush(self, min_voiced: float) -> list[Any] | None:
        buf, conf = self._buf, self._conf
        voiced = sum(1 for c in conf if c >= self.threshold)
        self._buf, self._conf = [], []
        self.speaking = False
        self._silence = self._held = 0
        self.trimmed = False
        self.gen += 1
        if not buf:
            return None
        if len(conf) >= 4 and voiced / len(conf) < 0.25:
            return None                    # mostly not speech (music, noise): dropped
        if voiced * self.frame_sec < min_voiced:
            return None
        return buf

    def best_split(self) -> int:
        """Index of the quietest point (confidence smoothed over ~160 ms) in the last 70 % of the piece, or -1."""
        conf = self._conf
        n = len(conf)
        if n < 4:
            return -1
        half = min(5, n // 2) // 2
        smooth = []
        for i in range(n):
            lo, hi = max(0, i - half), min(n, i + half + 1)
            smooth.append(sum(conf[lo:hi]) / (hi - lo))
        start = max(1, n * 3 // 10)
        idx, low = -1, float("inf")
        for i in range(start, n):
            if smooth[i] <= low:
                idx, low = i, smooth[i]
        if idx <= 0:
            return -1
        avg = sum(smooth[start:]) / max(1, n - start)
        if low < self.threshold or low / max(avg, 1e-6) < 0.8 or low < avg:
            return idx
        return -1

    def _split(self) -> list[Any] | None:
        """Maximum length reached: emit up to the quietest point and keep the rest as the next piece."""
        idx = self.best_split()
        if idx <= 0:
            seg = self._flush(0.0)
            if seg:
                self.forced = True
            return seg
        first = self._buf[:idx]
        self._buf, self._conf = self._buf[idx:], self._conf[idx:]
        self._silence = 0
        self.trimmed = False
        self.gen += 1
        self.forced = True
        return first


def make_segmenter(kind: str, frame_sec: float, sensitivity: int, max_segment: float,
                   rate: Callable[[], int] = lambda: 48000) -> Any:
    """The segmenter for the ``live_vad`` setting: auto / silero use Silero when it can load, else energy."""
    if kind in ("auto", SILERO):
        try:
            from dualyrics.live import deps, silero

            if silero.available():
                np = deps.import_module("numpy")
                model = silero.SileroVad()
                to16 = silero.To16k(np)
                return SileroSegmenter(model, sensitivity, max_segment,
                                       resample=lambda f: to16(f, rate() or 48000), np=np)
        except Exception as e:  # noqa: BLE001 - a broken onnxruntime must not stop Live mode
            import logging

            logging.getLogger(__name__).warning("Silero VAD unavailable, using the energy segmenter: %s", e)
    return Segmenter(frame_sec, sensitivity, max_segment)
