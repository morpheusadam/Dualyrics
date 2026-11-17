"""Speaker capture: WASAPI loopback of the default output device (what you hear), via PyAudioWPatch.

Frames are mono float32 numpy arrays at the device rate. The stream re-opens by itself when the
device fails or disappears, and the default device is read again every time Live starts.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from dualyrics.live import deps

log = logging.getLogger(__name__)

FRAME_SEC = 0.03
REOPEN_DELAY = 3.0


class LoopbackCapture:
    def __init__(self, on_frame: Callable[[Any, float], None], frame_sec: float = FRAME_SEC) -> None:
        """``on_frame(mono_float32_frame, rms)`` is called from the capture thread."""
        self.on_frame = on_frame
        self.frame_sec = frame_sec
        self.rate = 0
        self.device = ""
        self.error = ""
        self.paused = False
        self.fatal = ""          # capture cannot work at all (audio packages fail to load); not retried
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-capture", daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Ask the capture thread to close the device and end (returns at once)."""
        self._stop.set()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread = None

    @staticmethod
    def _loopback_device(pa: Any, p: Any) -> dict[str, Any]:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if spk.get("isLoopbackDevice"):
            return spk
        for lb in p.get_loopback_device_info_generator():
            if spk["name"] in lb["name"]:
                return lb
        raise OSError(f"no loopback device for {spk['name']}")

    def _run(self) -> None:
        try:
            np = deps.import_module("numpy")
            pa = deps.import_module("pyaudiowpatch")
        except Exception as e:  # noqa: BLE001 - a broken or partial install must not kill the thread silently
            log.error("live capture unavailable: %s", e)
            self.fatal = f"{type(e).__name__}: {e}"[:300]
            return
        while not self._stop.is_set():
            p = stream = None
            try:
                p = pa.PyAudio()
                dev = self._loopback_device(pa, p)
                rate = int(dev["defaultSampleRate"])
                ch = max(1, int(dev["maxInputChannels"]))
                frame = int(rate * self.frame_sec)
                stream = p.open(format=pa.paFloat32, channels=ch, rate=rate, input=True,
                                input_device_index=dev["index"], frames_per_buffer=frame)
                self.rate, self.device, self.error = rate, dev["name"], ""
                log.info("live capture: %s @ %d Hz x%d", dev["name"], rate, ch)
                # WASAPI loopback delivers no data while nothing plays. Never block in read() (stop would
                # hang) and feed silent frames in real time, so the segmenter sees the pause after speech.
                silent = np.zeros(frame, dtype=np.float32)
                last = time.monotonic()
                while not self._stop.is_set():
                    if stream.get_read_available() < frame:
                        if time.monotonic() - last >= self.frame_sec * 1.5:
                            last += self.frame_sec
                            if not self.paused:
                                self.on_frame(silent, 0.0)
                        time.sleep(self.frame_sec / 3)
                        continue
                    raw = stream.read(frame, exception_on_overflow=False)
                    last = time.monotonic()
                    if self.paused:
                        continue
                    a = np.frombuffer(raw, dtype=np.float32)
                    if ch > 1:
                        a = a.reshape(-1, ch).mean(axis=1)
                    rms = float(np.sqrt(np.mean(a * a)) + 1e-9)
                    self.on_frame(a, rms)
            except Exception as e:
                if str(e) != self.error:
                    log.warning("live capture: %s", e)
                self.error = str(e) or type(e).__name__
                self._stop.wait(REOPEN_DELAY)
            finally:
                for closer in (lambda: stream and stream.stop_stream(), lambda: stream and stream.close(),
                               lambda: p and p.terminate()):
                    try:
                        closer()
                    except Exception:
                        pass
