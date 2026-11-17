"""Live mode engine: speakers -> pauses -> Whisper -> clean captions -> translation, on worker threads.

The Tk thread only calls ``start`` / ``stop`` / ``set_paused`` / ``view``; everything slow (model download,
model load, capture, transcription, translation) runs on threads owned here.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from dualyrics import paths
from dualyrics.live import deps, models
from dualyrics.live.livetranslate import LiveTranslator
from dualyrics.live.text import Caption, CaptionStream, ContextWindow, LiveView, display_duration
from dualyrics.live.vad import Segmenter
from dualyrics.translate import TranslationError

log = logging.getLogger(__name__)

# states
OFF, MISSING, DOWNLOADING, LOADING, LISTENING, ERROR = "off", "missing", "downloading", "loading", "listening", "error"
QUEUE_MAX = 3            # segments waiting for Whisper; older ones are dropped so captions stay live
LINGER = 2.0             # extra seconds a caption stays while speech goes on (no flicker between lines)
HELLO = 3.0              # seconds the "Listening…" notice shows after start
ERROR_RETRY = 30.0       # seconds before a failed start (download, model, device) is tried again ...
ERROR_RETRY_MAX = 600.0  # ... doubling after each failure in a row, up to this


def live_config(cfg: dict[str, Any]) -> tuple[str, str]:
    """(model name, device preference) from settings."""
    return models.model_name(cfg["live_model"], cfg["live_english_only"]), cfg["live_device"]


class LiveEngine:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]], session_factory: Callable[[], Any],
                 translator: Any) -> None:
        self.get_cfg = get_cfg
        self.session_factory = session_factory
        self.live_tr = LiveTranslator(translator)
        self.state = OFF
        self.detail = ""                  # error text or device name
        self.progress = 0.0               # model download 0..1
        self.device = ""                  # cpu / cuda once loaded
        self.paused = False
        self.started_at = 0.0
        self.where = ""                   # app or game name (profile), for translation context
        self.title = ""                   # media title, if any
        self._want = False
        self._error_at = 0.0
        self._errors = 0                  # failed starts in a row (for the retry back-off)
        self._lock = threading.RLock()
        self._prep_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._transcriber: Any = None
        self._loaded_cfg: tuple[str, str] | None = None
        self._capture: Any = None
        self._segq: queue.Queue[tuple[Any, float, float]] = queue.Queue()
        self._seg: Segmenter | None = None
        self._seg_started = 0.0
        self._rate = 0
        self.stream = CaptionStream()
        self.context = ContextWindow(4)
        self._tx = ThreadPoolExecutor(max_workers=2, thread_name_prefix="live-tr")
        self._gen = 0

    # ------------------------------------------------------------------ control (Tk thread)
    @property
    def running(self) -> bool:
        return self._want

    @property
    def loaded(self) -> bool:
        return self._transcriber is not None

    def start(self, where: str = "", title: str = "") -> None:
        """Start listening (called every tick while Live is wanted; cheap when already running)."""
        self.where, self.title = where, title
        if self._want:
            return
        now = time.time()
        # a start that keeps failing (broken install, no audio device) waits longer each time: 30 s .. 10 min
        if self.state == ERROR and now - self._error_at < min(ERROR_RETRY_MAX, ERROR_RETRY * 2 ** max(
                0, self._errors - 1)):
            return
        if not deps.available():
            if self.state != MISSING:
                self.state, self.detail, self.started_at = MISSING, "", now
            return
        if self.state == MISSING:
            self.state = OFF
        self._want = True
        self.started_at = now
        self._gen += 1
        self._worker = threading.Thread(target=self._run, args=(self._gen,), name="live", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        """Stop listening (the model stays loaded, so the next start is quick)."""
        if self._want:
            self._gen += 1          # the worker of this run exits at its next check
        self._want = False
        if self.state == LISTENING:
            self.state = OFF
        cap = self._capture
        if cap is not None:
            cap.request_stop()   # never wait on the Tk thread: the worker closes the device when it exits
        self._flush_transcript()

    def unload(self) -> None:
        self.stop()
        with self._lock:
            self._transcriber, self._loaded_cfg = None, None
        if self.state != MISSING:
            self.state = OFF

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self._capture is not None:
            self._capture.paused = paused
        if paused and self._seg is not None:
            self._seg.reset()

    def clear_error(self) -> None:
        """Something the user changed may fix a failed start (settings, an install): try again at once."""
        self._errors = 0
        if self.state == ERROR:
            self.state = OFF

    def reload_if_changed(self) -> None:
        """Settings changed: apply segmenter values now; a new model or device needs a restart."""
        self.clear_error()
        cfg = self.get_cfg()
        if self._seg is not None:
            self._seg.set_sensitivity(cfg["live_vad_sensitivity"])
            self._seg.max_segment = float(cfg["live_max_segment"])
        self.context.resize(cfg["live_context_lines"])
        if self._loaded_cfg is not None and live_config(cfg) != self._loaded_cfg:
            was = self._want
            self.unload()
            if was:
                self.start(self.where, self.title)

    def shutdown(self) -> None:
        self.stop()
        self._tx.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ worker
    def _run(self, gen: int) -> None:
        try:
            self._prepare(gen)
            if not self._want or gen != self._gen:
                if self.state in (DOWNLOADING, LOADING) and not self._want:
                    self.state = OFF
                return
            self._listen(gen)
        except models.DownloadCancelled:
            log.info("model download cancelled")
            if gen == self._gen or not self._want:   # a newer run may already show its own progress
                self.state = OFF
        except Exception as e:  # noqa: BLE001
            log.exception("live mode failed")
            if gen == self._gen:
                self.state, self.detail = ERROR, str(e)[:300] or type(e).__name__
                self._error_at = time.time()
                self._errors += 1
                self._want = False

    def _prepare(self, gen: int) -> None:
        with self._prep_lock:  # a quick stop/start must not load the same model twice
            self._prepare_locked(gen)

    def _prepare_locked(self, gen: int) -> None:
        cfg = self.get_cfg()
        want = live_config(cfg)
        with self._lock:
            if self._transcriber is not None and self._loaded_cfg == want:
                return
        name, device_pref = want
        if not models.is_installed(name):
            self.state, self.progress, self.detail = DOWNLOADING, 0.0, name

            def prog(done: int, total: int) -> None:
                self.progress = min(1.0, done / total) if total else 0.0

            models.download(name, self.session_factory(), prog,
                            cancelled=lambda: not self._want or gen != self._gen)
        if not self._want or gen != self._gen:
            return
        self.state, self.detail = LOADING, name
        from dualyrics.live.stt import Transcriber

        tr = Transcriber(models.model_path(name), device_pref)
        self.device = tr.load()
        with self._lock:
            self._transcriber, self._loaded_cfg = tr, want

    def _listen(self, gen: int) -> None:
        from dualyrics.live.capture import FRAME_SEC, LoopbackCapture

        np = deps.import_module("numpy")
        cfg = self.get_cfg()
        self.context.resize(cfg["live_context_lines"])
        self._seg = Segmenter(FRAME_SEC, cfg["live_vad_sensitivity"], float(cfg["live_max_segment"]))
        self._seg_started = 0.0
        while not self._segq.empty():
            self._segq.get_nowait()
        # frames of a capture from an older run (still closing) must not reach this run's segmenter
        cap = LoopbackCapture(lambda frame, rms: self._on_frame(frame, rms) if gen == self._gen else None,
                              FRAME_SEC)
        cap.paused = self.paused
        self._capture = cap
        cap.start()
        self.state, self.detail = LISTENING, ""
        try:
            while self._want and gen == self._gen:
                if cap.fatal:          # e.g. PyAudioWPatch cannot load: an error with back-off, not silence
                    raise RuntimeError(cap.fatal)
                if cap.device:
                    self._errors = 0   # audio flows: the next failure starts the back-off again
                self.detail = cap.device or cap.error
                try:
                    frames, start, end = self._segq.get(timeout=0.3)
                except queue.Empty:
                    continue
                if self.paused or not self._want:
                    continue
                rate = cap.rate or 48000
                audio = np.concatenate(frames)
                n16 = int(len(audio) * 16000 / rate)
                if n16 < 1600:
                    continue
                a16 = np.interp(np.linspace(0, len(audio) - 1, n16), np.arange(len(audio)), audio).astype(np.float32)
                self._recognize(a16, start, end, self.get_cfg())
        finally:
            cap.stop()
            if self._capture is cap:
                self._capture = None
            if self.state == LISTENING and gen == self._gen:
                self.state = OFF

    def _on_frame(self, frame: Any, rms: float) -> None:
        seg = self._seg
        if seg is None:
            return
        now = time.time()
        if not seg.speaking:
            self._seg_started = now
        for frames in seg.feed(frame, rms):
            if self._segq.qsize() >= QUEUE_MAX:  # Whisper is behind: drop the oldest piece, stay live
                try:
                    self._segq.get_nowait()
                except queue.Empty:
                    pass
            self._segq.put((frames, self._seg_started, now))
            self._seg_started = now

    def _recognize(self, a16: Any, start: float, end: float, cfg: dict[str, Any]) -> None:
        tr = self._transcriber
        if tr is None:
            return
        name, _dev = live_config(cfg)
        src = cfg["live_source_language"]
        language = "en" if models.MODELS[name].english_only else (None if src == "auto" else src)
        last = self.stream.last
        # names in the prompt help Whisper spell them; a punctuated prompt keeps punctuation in the output
        prompt = " ".join(x.rstrip(".") + "." for x in (self.where, last.text if last else "") if x)
        try:
            recs = tr.transcribe(a16, start, end, language, prompt)
        except Exception as e:  # noqa: BLE001
            log.warning("transcribe failed: %s", e)
            return
        for r in recs:
            with self._lock:
                kind, cap = self.stream.push(r, now=time.time())
                if kind is None:
                    log.debug("live: dropped (%s): %s", cap, r.text)
                    continue
                prev = [c for c in self.stream.captions if c.final and not c.written]
            for p in prev:
                self._write_transcript(p)
            if cfg["show_translation"]:
                self._translate(cap, cfg)
            else:
                cap.trans_state = "skipped"

    # ------------------------------------------------------------------ translation
    def _translate(self, cap: Caption, cfg: dict[str, Any]) -> None:
        cid, version, text = cap.id, cap.version, cap.text
        ctx = self.context.pairs()
        source = cap.language if cfg["live_source_language"] == "auto" else cfg["live_source_language"]

        def work() -> None:
            try:
                out, state = self.live_tr.translate(text, cfg, ctx, source=source, where=self.where,
                                                    title=self.title)
            except TranslationError as e:
                log.info("live translation failed: %s", e)
                out, state = "", "failed"
            except Exception:
                log.exception("live translation crashed")
                out, state = "", "failed"
            with self._lock:
                if cap.version != version or cap.id != cid:
                    return  # merged with a newer fragment meanwhile: that version is being translated
                cap.translation, cap.trans_state = out, state
                if out:
                    cap.shown_at = max(cap.shown_at, time.time() - 0.5)  # give the reader time for it
                    self.context.add(text, out, key=cid)
                elif state == "skipped":
                    self.context.add(text, "", key=cid)
                done = cap.final and not cap.written
            if done:
                self._write_transcript(cap)

        try:
            self._tx.submit(work)
        except RuntimeError:  # shutting down
            pass

    # ------------------------------------------------------------------ transcripts
    def _write_transcript(self, cap: Caption) -> None:
        cfg = self.get_cfg()
        if cap.written or not cfg["live_save_transcripts"]:
            return
        if cap.trans_state == "pending" and time.time() - cap.end < 30:
            return  # written when the translation arrives
        cap.written = True
        try:
            folder = paths.data_dir() / "transcripts"
            folder.mkdir(parents=True, exist_ok=True)
            day = dt.datetime.fromtimestamp(cap.start)
            where = f" ({self.where})" if self.where else ""
            line = f"[{day:%H:%M:%S}]{where} {cap.text}\n"
            if cap.translation:
                line += f"    {cap.translation}\n"
            with open(folder / f"{day:%Y-%m-%d}.txt", "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            log.warning("could not save transcript: %s", e)

    def save_transcript(self) -> Any:
        """Write every caption still in memory to a new file now (menu "Save transcript"), even when automatic
        transcripts are off. Returns the file path, or None when there is nothing to save."""
        with self._lock:
            caps = [c for c in self.stream.captions if c.text.strip()]
        if not caps:
            return None
        folder = paths.data_dir() / "transcripts"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"saved-{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.txt"
            with open(path, "w", encoding="utf-8") as fh:
                if self.where:
                    fh.write(f"{self.where}\n\n")
                for c in caps:
                    fh.write(f"[{dt.datetime.fromtimestamp(c.start):%H:%M:%S}] {c.text}\n")
                    if c.translation:
                        fh.write(f"    {c.translation}\n")
            return path
        except (OSError, ValueError) as e:
            log.warning("could not save transcript: %s", e)
            return None

    def _flush_transcript(self) -> None:
        with self._lock:
            caps = [c for c in self.stream.finalize_all() if not c.written]
        for c in caps:
            if c.trans_state == "pending":
                c.trans_state = "failed"
            self._write_transcript(c)

    # ------------------------------------------------------------------ view (Tk thread)
    def view(self, now: float, translation_only: bool = False) -> LiveView:
        cfg = self.get_cfg()
        if self.state == MISSING:
            return LiveView(status="lv_missing", visible=now - self.started_at < 8)
        if self.state == DOWNLOADING:
            return LiveView(status="lv_downloading", status_kw={"model": self.detail,
                                                                 "pct": int(self.progress * 100)}, visible=True)
        if self.state == LOADING:
            return LiveView(status="lv_loading", status_kw={"model": self.detail}, visible=True)
        if self.state == ERROR:
            # shown after the first two failures in a row only, so a broken setup does not flash over a game
            return LiveView(status="lv_error", status_kw={"err": self.detail[:160]},
                            visible=now - self._error_at < 12 and self._errors <= 2)
        if self.paused:
            return LiveView(status="lv_paused", visible=False)
        with self._lock:
            cap = self.stream.last
            if cap is not None:
                show_tr = cfg["show_translation"]
                shown_tr = cap.translation if show_tr else ""
                dur = display_duration(cap.text, shown_tr, cfg["live_reading_cps"], cfg["live_min_display"],
                                       translation_only=translation_only)
                expire = cap.shown_at + dur
                if (self._seg is not None and self._seg.speaking) or not self._segq.empty():
                    expire += LINGER
                if now < expire:
                    pending = show_tr and cap.trans_state == "pending"
                    original = cap.text
                    if translation_only and (shown_tr or pending):
                        original = ""   # the original only stays when there is no translation at all
                    return LiveView(original=original, translation=shown_tr or ("…" if pending else ""),
                                    caption_id=cap.id, visible=True)
        if self.state == LISTENING and now - self.started_at < HELLO:
            return LiveView(status="lv_listening", visible=True)
        return LiveView(visible=False)
