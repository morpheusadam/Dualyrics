"""Speaker capture for Live mode (``live_capture``, docs/ALGORITHM.md section 3.1).

- ``system``: WASAPI loopback of the default output device (what you hear), via PyAudioWPatch. Frames are
  mono float32 numpy arrays at the device rate. The stream re-opens by itself when the device fails or
  disappears, and the default device is read again every time Live starts.
- ``app``: WASAPI process loopback of the active profile's app and its child processes (``procloop.py``),
  16 kHz mono; without a known app it records everything except Dualyrics.
- ``all_except_dualyrics``: process loopback of everything except Dualyrics' own process tree, so the dubbed
  voice is never captured and transcribed again.

The two process modes fall back to the system loopback, with one log line, on Windows builds without process
loopback or when activation fails.
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
SYSTEM, APP, ALL_EXCEPT_OWN = "system", "app", "all_except_dualyrics"
MODES = (SYSTEM, APP, ALL_EXCEPT_OWN)
RETARGET_EVERY = 1.0              # s between checks of the app to record (app mode)


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
        self.want = SYSTEM       # the live_capture setting this capture was made for
        self.mode = SYSTEM       # what is really recorded: system / app / all_except_dualyrics
        self.fallback = ""       # why a process mode fell back to the system loopback
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


class ProcessCapture(LoopbackCapture):
    """Process loopback (``app`` or ``all_except_dualyrics``) with the same interface as ``LoopbackCapture``.
    ``target()`` returns the PID of the app to record (0 = none known: everything except Dualyrics);
    ``own_pid`` is the process tree that is never recorded."""

    def __init__(self, on_frame: Callable[[Any, float], None], frame_sec: float = FRAME_SEC, want: str = APP,
                 target: Callable[[], int] = lambda: 0, own_pid: int = 0,
                 opener: Callable[[int, bool, Any], Any] | None = None) -> None:
        super().__init__(on_frame, frame_sec)
        import os

        self.want = want
        self.target = target
        self.own_pid = own_pid or os.getpid()
        self._opener = opener
        self._bad_pid = 0        # an app whose stream could not be opened (e.g. protected): not retried

    def _open(self, np: Any, pid: int, include: bool) -> Any:
        if self._opener is not None:
            return self._opener(pid, include, np)
        from dualyrics.live import procloop

        if not procloop.supported():
            raise procloop.ProcessLoopbackError(f"Windows build {procloop.windows_build()} has no process loopback")
        return procloop.ProcessLoopback(pid, include, np)

    def _wanted(self) -> tuple[int, bool]:
        """(pid, include) to record now."""
        if self.want == APP:
            try:
                pid = int(self.target() or 0)
            except Exception:
                pid = 0
            if pid and pid != self.own_pid and pid != self._bad_pid:
                return pid, True
        return self.own_pid, False

    def _run(self) -> None:
        try:
            np = deps.import_module("numpy")
        except Exception as e:  # noqa: BLE001
            log.error("live capture unavailable: %s", e)
            self.fatal = f"{type(e).__name__}: {e}"[:300]
            return
        try:
            self._run_process(np)
        except Exception as e:  # noqa: BLE001 - old Windows, COM refused: the system loopback still works
            if self._stop.is_set():
                return
            self.fallback = str(e)[:200] or type(e).__name__
            log.warning("live capture: process loopback unavailable (%s); using the system loopback", self.fallback)
            self.mode, self.rate = SYSTEM, 0
            super()._run()

    def _run_process(self, np: Any) -> None:
        frame = int(16000 * self.frame_sec)
        silent = np.zeros(frame, dtype=np.float32)
        opened_once = False
        while not self._stop.is_set():
            pid, include = self._wanted()
            try:
                stream = self._open(np, pid, include)
            except Exception as e:
                if include:              # this app cannot be recorded: everything except Dualyrics instead
                    log.info("live capture: app pid %d cannot be recorded (%s)", pid, e)
                    self._bad_pid = pid
                    continue
                if not opened_once:
                    raise                        # never worked: let _run fall back to the system loopback
                if str(e) != self.error:
                    log.warning("live capture: %s", e)
                self.error = str(e) or type(e).__name__
                self._stop.wait(REOPEN_DELAY)
                continue
            opened_once = True
            self.rate, self.error = 16000, ""
            self.mode = APP if include else ALL_EXCEPT_OWN
            self.device = f"pid {pid}" if include else "all apps except Dualyrics"
            log.info("live capture: process loopback, %s", "app pid %d and its children" % pid if include
                     else "everything except Dualyrics")
            buf = np.zeros(0, dtype=np.float32)
            last = time.monotonic()
            checked = last
            try:
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - checked >= RETARGET_EVERY:
                        checked = now
                        if self._wanted() != (pid, include) or not stream.target_alive():
                            break                # the app changed or closed: open the stream again
                    a, got = stream.read()
                    if got:
                        last = now
                    if len(a):
                        buf = np.concatenate([buf, a]) if len(buf) else a
                    while len(buf) >= frame:
                        f, buf = buf[:frame], buf[frame:]
                        if not self.paused:
                            self.on_frame(f, float(np.sqrt(np.mean(f * f)) + 1e-9))
                    if not got and now - last >= self.frame_sec * 1.5:
                        last += self.frame_sec     # nothing rendered: silent frames in real time
                        if not self.paused:
                            self.on_frame(silent, 0.0)
                    self._stop.wait(self.frame_sec / 3)
            except Exception as e:
                if str(e) != self.error:
                    log.warning("live capture: %s", e)
                self.error = str(e) or type(e).__name__
                self._stop.wait(REOPEN_DELAY / 3)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass


def make_capture(mode: str, on_frame: Callable[[Any, float], None], frame_sec: float = FRAME_SEC,
                 target: Callable[[], int] = lambda: 0) -> LoopbackCapture:
    """The capture for the ``live_capture`` setting (an unknown value means the system loopback)."""
    if mode in (APP, ALL_EXCEPT_OWN):
        return ProcessCapture(on_frame, frame_sec, mode, target)
    return LoopbackCapture(on_frame, frame_sec)
