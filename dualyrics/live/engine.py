"""Live mode engine: speech -> clean captions -> translation, on worker threads.

Two speech engines (setting ``live_engine``; docs/ALGORITHM.md section 4):

- Windows 11 Live Captions (``wincaptions``): started hidden, its caption box read every 25 ms;
- faster-whisper: speakers -> voice activity segmenter (Silero VAD, or energy without onnxruntime) -> Whisper,
  with interim commits of complete sentences while long speech goes on (``interim``).

``auto`` prefers Live Captions when it is available and fits the settings, and falls back to Whisper by itself
when Live Captions fails. The Tk thread only calls ``start`` / ``stop`` / ``set_paused`` / ``view``; everything
slow (model download, model load, capture, transcription, translation) runs on threads owned here.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import Any, Callable

from dualyrics import langs, paths
from dualyrics.live import deps, interim, models, pivot, wincaptions
from dualyrics.live.livetranslate import LiveTranslator
from dualyrics.live.text import (Caption, CaptionStream, ContextWindow, LiveView, Recognition, display_duration,
                                 hallucination_reason, normalize)
from dualyrics.live.vad import make_segmenter
from dualyrics.screen import dedupe
from dualyrics.translate import TranslationError

log = logging.getLogger(__name__)

# states
OFF, MISSING, DOWNLOADING, LOADING, LISTENING, ERROR = "off", "missing", "downloading", "loading", "listening", "error"
QUEUE_MAX = 3            # segments waiting for Whisper; older ones are dropped so captions stay live
LINGER = 2.0             # extra seconds a caption stays while speech goes on (no flicker between lines)
HELLO = 3.0              # seconds the "Listening…" notice shows after start
ERROR_RETRY = 30.0       # seconds before a failed start (download, model, device) is tried again ...
ERROR_RETRY_MAX = 600.0  # ... doubling after each failure in a row, up to this
LIVE_CAPTIONS, WHISPER = wincaptions.LIVE_CAPTIONS, wincaptions.WHISPER
PREVIEW_MAX_AGE = 6.0    # seconds an interim preview line stays without a newer pass
MIN_AUDIO = 1600         # 0.1 s at 16 kHz: shorter pieces are not transcribed


def live_config(cfg: dict[str, Any]) -> tuple[str, str]:
    """(model name, device preference) from settings."""
    return models.model_name(cfg["live_model"], cfg["live_english_only"]), cfg["live_device"]


@dataclass
class _Interim:
    """What interim passes committed from the piece of generation ``gen``."""
    gen: int = -1
    tail: str = ""               # end of the committed text (echo removal)
    late_trim: float = 0.0       # seconds to cut from the piece's audio (it ended before the trim applied)


class LiveEngine:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]], session_factory: Callable[[], Any],
                 translator: Any, line_store: Any = None) -> None:
        self.get_cfg = get_cfg
        self.session_factory = session_factory
        self.live_tr = LiveTranslator(translator, line_store)
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
        self._segq: queue.Queue[tuple[Any, float, float, int, int]] = queue.Queue()   # frames, start, end, gen, rate
        self._seg: Any = None             # vad.Segmenter or vad.SileroSegmenter
        self._seg_started = 0.0
        self._rate = 0
        self.stream = CaptionStream()
        self.context = ContextWindow(4)
        self._tx = ThreadPoolExecutor(max_workers=2, thread_name_prefix="live-tr")
        self._gen = 0
        # v2.1 dubbing: the captured audio also goes to Gemini Live (taps get (frame, rate)), and speech that
        # is our own dubbed voice is not captioned (self_voice(language, start, end) -> True drops it)
        self.frame_taps: list[Callable[[Any, int], None]] = []
        self.self_voice: Callable[[str, float, float], bool] = lambda language, start, end: False
        # v2.2 per-process capture (live_capture = app): PID of the active profile's app, set by the app (0 = none)
        self.capture_target = 0
        self.capture_mode = ""            # what the running capture records (capture.SYSTEM / APP / ALL_EXCEPT_OWN)
        self.capture_note = ""            # why a process capture fell back to the system loopback
        # v2.2: speech engine choice, Silero VAD, interim commits
        self.engine = ""                  # live_captions / whisper while running
        self.engine_why = ""              # why that engine (wincaptions.choose)
        self.lc_note = ""                 # why Live Captions last failed
        self._lc_failed_at = 0.0
        self._lc_app: Any = None          # wincaptions.LiveCaptionsApp, kept between runs (no window flashes)
        self._runner: Any = None          # wincaptions.CaptionsRunner of the running Live Captions engine
        self._lc_lang = ""
        self._lc_keys: dict[int, int] = {}   # caption id -> Live Captions sentence key
        self._seg_lock = threading.Lock()    # the segmenter is fed by the capture thread, trimmed by the worker
        self._vad_pref = ""
        self._interim = _Interim()
        self._ipace = [-1, 0.0, 0.0]      # interim pacing: [piece gen, buffered s after the last pass, monotonic]
        self._interim_due = False
        self._interim_on = False
        self._preview: tuple[str, float] = ("", 0.0)
        self._tr_jobs: dict[int, list[tuple[int, Any]]] = {}   # caption id -> [(version, future)]

    # ------------------------------------------------------------------ control (Tk thread)
    @property
    def running(self) -> bool:
        return self._want

    @property
    def loaded(self) -> bool:
        """Something to free after a while without Live: the Whisper model, or a Live Captions we started."""
        app = self._lc_app
        return self._transcriber is not None or (app is not None and app.ours)

    def choose_engine(self, cfg: dict[str, Any], whisper_ok: bool = True) -> tuple[str, str]:
        """(engine, why) for these settings (wincaptions.choose)."""
        pref = cfg.get("live_engine", "auto")
        if pref == WHISPER:
            return WHISPER, "chosen"
        src = cfg.get("live_source_language", "auto")
        name = models.model_name(cfg.get("live_model", "small"), cfg.get("live_english_only", False))
        tasks = pivot.whisper_tasks(cfg.get("pivot_english", True), cfg.get("pivot_show_original", False),
                                    models.MODELS[name].english_only, src, cfg.get("target_language", "en"))
        # dubbing through Gemini Live (dub/controller.py: auto or gemini_live with keys) needs the captured audio
        needs_audio = bool(cfg.get("dub_enabled")) and cfg.get("dub_engine") in ("auto", "gemini_live") and bool(
            cfg.get("gemini_keys"))
        failed = bool(self._lc_failed_at) and time.time() - self._lc_failed_at < wincaptions.RETRY_AFTER
        return wincaptions.choose(pref, wincaptions.availability(), src, pivot.TRANSLATE in tasks, needs_audio,
                                  failed, whisper_ok)

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
        if not deps.available() and self.choose_engine(self.get_cfg(), False)[0] != LIVE_CAPTIONS:
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
        self._preview = ("", 0.0)
        self._flush_transcript()

    def unload(self) -> None:
        self.stop()
        with self._lock:
            self._transcriber, self._loaded_cfg = None, None
        self._close_live_captions()
        if self.state != MISSING:
            self.state = OFF

    def _close_live_captions(self) -> None:
        """Close a Live Captions this app started (its window style and place are given back first)."""
        with self._lock:
            app, self._lc_app = self._lc_app, None
        if app is not None:
            app.closed = True              # a poll still running never starts it again (it would be orphaned)
            if app.ours:
                threading.Thread(target=app.close, name="live-captions-close", daemon=True).start()

    def adopt_orphan(self) -> bool:
        """A Live Captions that an earlier run hid and never closed (the app crashed or was killed): take it
        over, so it is closed after a while without Live mode like one started now. Call it off the Tk thread."""
        try:
            if self._lc_app is not None or not wincaptions.availability().ok:
                return False
            app = wincaptions.LiveCaptionsApp()
            if not app.adopt():
                return False
        except Exception:  # noqa: BLE001 - no user32 / UIA: nothing to adopt
            log.debug("live captions orphan check failed", exc_info=True)
            return False
        with self._lock:
            if self._lc_app is not None:
                return False
            self._lc_app = app
        log.info("live: took over a hidden Live Captions left by an earlier run")
        return True

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if self._capture is not None:
            self._capture.paused = paused
        if paused:
            self._preview = ("", 0.0)
            with self._seg_lock:
                if self._seg is not None:
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
        with self._seg_lock:
            if self._seg is not None:
                self._seg.set_sensitivity(cfg["live_vad_sensitivity"])
                self._seg.max_segment = float(cfg["live_max_segment"])
        self.context.resize(cfg["live_context_lines"])
        if self._want and self.engine:
            engine, _why = self.choose_engine(cfg, deps.available())
            vad_changed = self.engine == WHISPER and cfg.get("live_vad", "auto") != self._vad_pref
            cap = self._capture
            cap_changed = cap is not None and getattr(cap, "want", "system") != cfg.get("live_capture", "system")
            if engine != self.engine or vad_changed or cap_changed:   # another engine, segmenter or capture
                self.stop()
                self.start(self.where, self.title)
                return
        if self._loaded_cfg is not None and live_config(cfg) != self._loaded_cfg:
            was = self._want
            self.unload()
            if was:
                self.start(self.where, self.title)

    def shutdown(self) -> None:
        self.stop()
        with self._lock:
            app, self._lc_app = self._lc_app, None
        if app is not None:
            app.closed = True
            if app.ours:
                try:
                    app.close(wait=False)
                except Exception:
                    log.debug("closing Live Captions failed", exc_info=True)
        self._tx.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ worker
    def _run(self, gen: int) -> None:
        try:
            whisper_ok = deps.available()
            engine, self.engine_why = self.choose_engine(self.get_cfg(), whisper_ok)
            if engine == LIVE_CAPTIONS:
                self.engine = LIVE_CAPTIONS
                try:
                    self._listen_captions(gen)
                    return
                except wincaptions.Unavailable as e:
                    if not self._want or gen != self._gen:
                        return             # stopped meanwhile (e.g. closed by unload): not a failure
                    log.warning("Windows Live Captions cannot be used, falling back to Whisper: %s", e)
                    self._lc_failed_at, self.lc_note = time.time(), str(e)[:200]
                    # a Live Captions we started is of no use now: never leave it running (it could show its
                    # window later, e.g. after a slow start, while nothing keeps it hidden any more)
                    self._close_live_captions()
                    if not whisper_ok:
                        raise RuntimeError(f"Windows Live Captions: {e}") from e
                    self.engine_why = "failed"
            self.engine = WHISPER
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

    # ------------------------------------------------------------------ engine: Windows Live Captions
    def _listen_captions(self, gen: int) -> None:
        """Read Windows Live Captions until stopped. Raises ``wincaptions.Unavailable`` when it cannot work."""
        cfg = self.get_cfg()
        avail = wincaptions.availability()
        self._lc_lang = wincaptions.caption_language(avail, cfg.get("live_source_language", "auto"))
        try:
            reader = wincaptions.CaptionBoxReader()
            with self._lock:
                if self._lc_app is None:
                    self._lc_app = wincaptions.LiveCaptionsApp()
                app = self._lc_app
        except Exception as e:  # noqa: BLE001 - comtypes or user32 missing: use Whisper
            raise wincaptions.Unavailable(f"UI Automation: {e}") from e
        runner = wincaptions.CaptionsRunner(app, reader, lambda e: self._on_caption(e, gen))
        self.state, self.detail = LOADING, "Windows Live Captions"
        was_paused = False
        try:
            runner.start(time.time())
            self._runner = runner
            self.state, self.detail = LISTENING, "Windows Live Captions"
            while self._want and gen == self._gen:
                if self.paused:
                    was_paused = True
                    time.sleep(0.2)
                    continue
                if was_paused:             # what was said during the pause is not captioned afterwards
                    runner.tracker.reset()
                    was_paused = False
                runner.step(time.time())
                if runner.working_since:
                    self._errors = 0
                time.sleep(wincaptions.POLL)
        except wincaptions.Unavailable:
            raise
        except Exception as e:  # noqa: BLE001 - any Windows/UIA failure: fall back to Whisper, not an error
            raise wincaptions.Unavailable(f"{type(e).__name__}: {e}") from e
        finally:
            if self._runner is runner:
                self._runner = None
            if self.state == LISTENING and gen == self._gen:
                self.state = OFF

    def _on_caption(self, e: wincaptions.Emit, gen: int) -> None:
        """A sentence event of Live Captions: a new caption, or the current one grown or finished."""
        if gen != self._gen or self.paused or not self._want:
            return
        cfg = self.get_cfg()
        now = time.time()
        r = Recognition(e.text, e.start, now, language=self._lc_lang)
        if self._own_voice(r):
            return
        with self._lock:
            last = self.stream.last
            if last is not None and self._lc_keys.get(last.id) == e.key:
                cap = self.stream.revise(last, r, now)
                if cap is None:            # same words again (only the end of the sentence changed)
                    last.final = last.final or e.done
                    return
            else:
                kind, got = self.stream.push(r, now=now)
                if kind is None:
                    return
                cap = got  # type: ignore[assignment]
                self._lc_keys[cap.id] = e.key
                for old in [k for k in self._lc_keys if k < cap.id - 20]:
                    del self._lc_keys[old]
            if e.done:
                cap.final = True
        self._after_push(cap, cfg)

    # ------------------------------------------------------------------ engine: faster-whisper
    def _listen(self, gen: int) -> None:
        from dualyrics.live.capture import FRAME_SEC, make_capture

        np = deps.import_module("numpy")
        cfg = self.get_cfg()
        self.context.resize(cfg["live_context_lines"])
        self._vad_pref = cfg.get("live_vad", "auto")
        seg = make_segmenter(self._vad_pref, FRAME_SEC, cfg["live_vad_sensitivity"], float(cfg["live_max_segment"]),
                             rate=lambda: self._capture.rate if self._capture is not None else 0)
        log.info("live: %s voice activity segmenter", seg.kind)
        with self._seg_lock:
            self._seg = seg
            self._seg_started = 0.0
            self._interim, self._ipace, self._interim_due = _Interim(), [-1, 0.0, 0.0], False
        self._preview = ("", 0.0)
        while not self._segq.empty():
            self._segq.get_nowait()
        # frames of a capture from an older run (still closing) must not reach this run's segmenter
        cap = make_capture(str(cfg.get("live_capture", "system")),
                           lambda frame, rms: self._on_frame(frame, rms) if gen == self._gen else None,
                           FRAME_SEC, lambda: self.capture_target)
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
                self.capture_mode, self.capture_note = cap.mode, getattr(cap, "fallback", "")
                cfg = self.get_cfg()
                self._interim_on = bool(cfg.get("live_interim", True))
                try:
                    frames, start, end, sgen, srate = self._segq.get(timeout=0.1)
                except queue.Empty:
                    if self._interim_due:
                        self._interim_due = False
                        if not self.paused and self._interim_on:
                            self._do_interim(np, cap.rate, cfg)
                    continue
                if self.paused or not self._want:
                    continue
                a16 = self._to16(np, frames, srate or cap.rate or 48000)
                self._final_piece(a16, start, end, sgen, cfg)
        finally:
            cap.stop()
            if self._capture is cap:
                self._capture = None
            if self.state == LISTENING and gen == self._gen:
                self.state = OFF

    @staticmethod
    def _to16(np: Any, frames: list[Any], rate: int) -> Any:
        """Frames -> one float32 array at 16 kHz, or None when shorter than 0.1 s."""
        if not frames:
            return None
        audio = np.concatenate(frames).astype(np.float32)
        if rate == 16000:
            return audio if len(audio) >= MIN_AUDIO else None
        n16 = int(len(audio) * 16000 / rate)
        if n16 < MIN_AUDIO:
            return None
        return np.interp(np.linspace(0, len(audio) - 1, n16), np.arange(len(audio)), audio).astype(np.float32)

    def _on_frame(self, frame: Any, rms: float) -> None:
        cap = self._capture
        for tap in list(self.frame_taps):
            try:
                tap(frame, (cap.rate if cap is not None else 0) or 48000)
            except Exception:
                log.debug("audio tap failed", exc_info=True)
        seg = self._seg
        if seg is None:
            return
        now = time.time()
        with self._seg_lock:
            if seg is not self._seg:
                return
            if not seg.pending:
                self._seg_started = now
            gen = seg.gen
            for frames in seg.feed(frame, rms):
                if self._segq.qsize() >= QUEUE_MAX:  # Whisper is behind: drop the oldest piece, stay live
                    try:
                        self._segq.get_nowait()
                    except queue.Empty:
                        pass
                self._segq.put((frames, self._seg_started, now, gen, seg.rate))
                self._seg_started = now
                gen = seg.gen
            if self._interim_on and seg.speaking:
                pace = self._ipace
                at = pace[1] if pace[0] == seg.gen else 0.0
                mono = time.monotonic()
                buffered = seg.buffered()
                if interim.due(buffered, at, mono, pace[2]):
                    # the pass counts from now even when it commits nothing (no sentence end, a hallucination,
                    # a failure): the next one waits for INTERVAL seconds of new audio, not only the cooldown
                    self._ipace = [seg.gen, buffered, mono]
                    self._interim_due = True

    def _whisper_args(self, cfg: dict[str, Any]) -> tuple[str | None, str, tuple[str, ...]]:
        """(language, prompt, Whisper tasks) for the next piece."""
        name, _dev = live_config(cfg)
        src = cfg["live_source_language"]
        english_model = models.MODELS[name].english_only
        language = "en" if english_model else (None if src == "auto" else src)
        last = self.stream.last
        # names in the prompt help Whisper spell them; a punctuated prompt keeps punctuation in the output
        prompt = " ".join(x.rstrip(".") + "." for x in (self.where, last.text if last else "") if x)
        # any language: Whisper translates to English first, then English goes to the target (live/pivot.py)
        tasks = pivot.whisper_tasks(cfg.get("pivot_english", True), cfg.get("pivot_show_original", False),
                                    english_model, src, cfg["target_language"])
        return language, prompt, tasks

    def _final_piece(self, a16: Any, start: float, end: float, sgen: int, cfg: dict[str, Any]) -> None:
        """A piece ended at a pause (or its maximum length): transcribe what interim passes left of it."""
        it = self._interim
        tail = ""
        if it.gen == sgen:
            tail = it.tail
            if it.late_trim > 0 and a16 is not None:
                cut = int(it.late_trim * 16000)
                a16 = a16[cut:] if len(a16) - cut >= MIN_AUDIO else None
                start += it.late_trim
            self._interim = _Interim()
        self._preview = ("", 0.0)
        if a16 is not None:
            self._recognize(a16, start, end, cfg, tail)

    def _do_interim(self, np: Any, rate: int, cfg: dict[str, Any]) -> None:
        """Long speech without a pause: commit its complete sentences now (see live/interim.py)."""
        tr = self._transcriber
        if tr is None:
            return
        language, prompt, tasks = self._whisper_args(cfg)
        if len(tasks) > 1:
            return                         # two passes per piece (original line too): no interim passes
        with self._seg_lock:
            seg = self._seg
            peek = seg.peek() if seg is not None else None
            started = self._seg_started
        if peek is None or seg is None:
            return
        frames, g = peek
        a16 = self._to16(np, frames, seg.rate or rate or 48000)
        if a16 is None:
            return
        dur = len(a16) / 16000
        if dur < interim.MIN_BUFFER:
            return
        try:
            recs = pivot.recognize(tr, a16, started, started + dur, language, prompt, tasks, cfg["target_language"])
        except Exception as e:  # noqa: BLE001
            log.warning("interim transcribe failed: %s", e)
            return
        if not recs or hallucination_reason(recs[0]):
            return
        r = recs[0]
        it = self._interim
        p = interim.plan(r.text, it.tail if it.gen == g else "")
        mono = time.monotonic()
        if p is None:
            return
        now = time.time()
        if not p.complete:
            self._preview = (p.rest, now)
            with self._seg_lock:
                if seg is self._seg and seg.gen == g:
                    self._ipace = [g, seg.buffered(), mono]
            return
        total = max(1, len(p.full))
        t = started
        for sent in p.complete:
            d = dur * len(sent) / total
            rec = replace(r, text=sent, start=t, end=t + d)
            t += d
            if self._own_voice(rec):       # committed (trimmed) all the same, so it is not heard again
                continue
            self._emit(rec, cfg)
        trim = interim.trim_seconds(len(p.committed), len(p.full), dur)
        with self._seg_lock:
            ok = seg is self._seg and seg.trim_front(trim, g)
            if ok:
                self._seg_started = started + trim
                self._ipace = [g, seg.buffered(), mono]
            self._interim = _Interim(g, interim.new_tail(p.committed), 0.0 if ok else trim)
        self._preview = (p.rest, now)
        log.debug("live: interim committed %d sentence(s), trimmed %.2f s", len(p.complete), trim)

    def _recognize(self, a16: Any, start: float, end: float, cfg: dict[str, Any], tail: str = "") -> None:
        tr = self._transcriber
        if tr is None:
            return
        language, prompt, tasks = self._whisper_args(cfg)
        try:
            recs = pivot.recognize(tr, a16, start, end, language, prompt, tasks, cfg["target_language"])
        except Exception as e:  # noqa: BLE001
            log.warning("transcribe failed: %s", e)
            return
        for r in recs:
            if tail:                       # interim passes committed the start of this piece already
                rest = interim.strip_committed_overlap(r.text, tail)
                if not normalize(rest):
                    continue
                r = replace(r, text=rest)
            if self._own_voice(r, start, end):
                continue
            self._emit(r, cfg)

    def _own_voice(self, r: Recognition, start: float | None = None, end: float | None = None) -> bool:
        """The speech is our own dubbed voice (dubbing heard through a system-wide capture): not captioned."""
        try:
            own = bool(self.self_voice(r.language, r.start if start is None else start,
                                       r.end if end is None else end))
        except Exception:
            own = False
        if own:
            log.debug("live: dropped our own dubbed voice: %s", r.text)
        return own

    def _emit(self, r: Recognition, cfg: dict[str, Any]) -> None:
        with self._lock:
            kind, cap = self.stream.push(r, now=time.time())
            if kind is None:
                log.debug("live: dropped (%s): %s", cap, r.text)
                return
        self._after_push(cap, cfg)  # type: ignore[arg-type]

    def _after_push(self, cap: Caption, cfg: dict[str, Any]) -> None:
        """Write finished captions to the transcript and translate the new or changed one."""
        with self._lock:
            prev = [c for c in self.stream.captions if c.final and not c.written and c is not cap]
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
        if cap.pivot:
            source = "en"          # the caption is Whisper's English; the original language is only shown

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
                if not dedupe.same_translation(cap.translation, out):   # a character or two: keep (no flicker)
                    cap.translation = out
                cap.trans_state = state
                if out:
                    cap.shown_at = max(cap.shown_at, time.time() - 0.5)  # give the reader time for it
                    self.context.add(text, cap.translation, key=cid)
                elif state == "skipped":
                    self.context.add(text, "", key=cid)
                done = cap.final and not cap.written
            if done:
                self._write_transcript(cap)
            self._cancel_stale(cid)

        with self._lock:   # an older version of this caption that has not started yet is stale
            for _v, fut in self._tr_jobs.pop(cid, []):
                fut.cancel()
        try:
            fut = self._tx.submit(work)
        except RuntimeError:  # shutting down
            return
        with self._lock:
            self._tr_jobs.setdefault(cid, []).append((version, fut))
            for old in [k for k in self._tr_jobs if k < cid - 20]:
                del self._tr_jobs[old]

    def _cancel_stale(self, cid: int) -> None:
        """A translation finished: drop its queued older versions and, with Live Captions (which sends many
        quick updates), queued translations of older captions that are no longer on screen."""
        stale: list[Caption] = []
        with self._lock:
            jobs = self._tr_jobs.get(cid, [])
            self._tr_jobs[cid] = [(v, f) for v, f in jobs if not f.done()]
            if self.engine == LIVE_CAPTIONS:
                for other in [k for k in self._tr_jobs if k < cid]:
                    left = []
                    for v, f in self._tr_jobs[other]:
                        if f.cancel():
                            stale.extend(c for c in self.stream.captions if c.id == other and c.version == v)
                        elif not f.done():
                            left.append((v, f))
                    self._tr_jobs[other] = left
            for c in stale:
                if c.trans_state == "pending":
                    c.trans_state = "failed"
        for c in stale:
            if c.final and not c.written:
                self._write_transcript(c)

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
            if cap.alt:
                line += f"    {cap.alt}\n"
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
            if self.engine == LIVE_CAPTIONS:
                return LiveView(status="lv_lc_starting", visible=True)
            return LiveView(status="lv_loading", status_kw={"model": self.detail}, visible=True)
        if self.state == ERROR:
            # shown after the first two failures in a row only, so a broken setup does not flash over a game
            return LiveView(status="lv_error", status_kw={"err": self.detail[:160]},
                            visible=now - self._error_at < 12 and self._errors <= 2)
        if self.paused:
            return LiveView(status="lv_paused", visible=False)
        preview = self._preview_text(now, cfg, translation_only)
        with self._lock:
            cap = self.stream.last
            if cap is not None:
                show_tr = cfg["show_translation"]
                shown_tr = cap.translation if show_tr else ""
                dur = display_duration(cap.text, shown_tr, cfg["live_reading_cps"], cfg["live_min_display"],
                                       translation_only=translation_only)
                expire = cap.shown_at + dur
                if self._speaking():
                    expire += LINGER
                if now < expire:
                    pending = show_tr and cap.trans_state == "pending"
                    original, translation, extra = pivot.display_lines(
                        cap.text, cap.alt, cap.pivot, shown_tr, cfg["target_language"],
                        bool(cfg.get("pivot_show_original")))
                    if cap.pivot and langs.same_language(cfg["target_language"], "en"):
                        pending = False   # target English: Whisper's English line is the translation
                    if translation_only and (translation or pending):
                        original, extra = "", ""   # the original only stays when there is no translation at all
                    return LiveView(original=original, translation=translation or ("…" if pending else ""),
                                    extra=extra, caption_id=cap.id, visible=True, preview=preview)
        if preview:
            return LiveView(preview=preview, visible=True)
        if self.state == LISTENING and now - self.started_at < HELLO:
            return LiveView(status="lv_listening", visible=True)
        return LiveView(visible=False)

    def _speaking(self) -> bool:
        """Speech goes on (a caption then stays a little longer, so the overlay does not flicker)."""
        if self.engine == LIVE_CAPTIONS:
            runner = self._runner
            return runner is not None and runner.tracker.active
        seg = self._seg
        return (seg is not None and seg.speaking) or not self._segq.empty()

    def _preview_text(self, now: float, cfg: dict[str, Any], translation_only: bool) -> str:
        """The unconfirmed end of long speech (interim passes), shown as a dim line until it is committed."""
        text, at = self._preview
        if not text or now - at > PREVIEW_MAX_AGE or translation_only:
            return ""
        if not cfg.get("live_interim", True) or not cfg.get("live_interim_preview", True):
            return ""
        return text
