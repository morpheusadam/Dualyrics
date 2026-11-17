"""The screen source: reads the subtitles the window in front already shows, and translates them.

One worker thread samples the target window a few times a second (setting ``ocr_fps``):

1. for browsers, caption elements through UI Automation (exact text, no OCR errors);
2. else OCR of the subtitle region (Windows.Media.Ocr), only when the region really changed;
3. the readings go through ``SubtitleTracker`` (stable-text debounce, dedupe, app labels ignored);
4. a line that repeats a recent reading (``screen/dedupe.py``, Translumo's rules: Jaro/Dice similarity, 3 s
   cache) gets the translation it had, without a request; a new translation that differs from the shown one in
   a character or two does not replace it (no flicker);
5. every new line is translated (English pivot for other languages, see ``live/pivot.py``).

The Tk thread calls ``start(target)`` / ``stop()`` / ``has_text()`` / ``view()``. ``step()`` is one sample and
takes its readers from factories, so the whole loop is unit-tested with fake frames.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from dualyrics import sources as sources_mod
from dualyrics.live import pivot
from dualyrics.live.text import ContextWindow, LiveView
from dualyrics.screen import dedupe
from dualyrics.screen.logic import ChangeDetector, StaticLines, SubtitleTracker, similar
from dualyrics.translate import TranslationError

log = logging.getLogger(__name__)

LINGER = 0.4          # a line stays this long after it left the screen ...
UIA_TRUST = 15.0      # ... caption elements that gave text this recently are trusted even when empty (no OCR)
MEMO = 20             # recent lines and their translations (reused for a repeated reading)
MEMO_KEYS = ("target_language", "pivot_english", "pivot_show_original", "provider", "style", "skip_same_language")


def memo_key(cfg: dict[str, Any]) -> tuple[Any, ...]:
    """The settings a reused translation must have been made with."""
    return tuple(str(cfg.get(k)) for k in MEMO_KEYS)


@dataclass(frozen=True)
class Target:
    hwnd: int
    key: str = ""                  # profile id (a new key resets the tracker)
    kind: str = "other"            # profile kind: browsers are read through UI Automation first
    region: tuple[float, ...] = sources_mod.DEFAULT_OCR_REGION
    where: str = ""                # app or game name, for translation context
    title: str = ""


@dataclass
class ScreenCaption:
    id: int
    text: str
    shown_at: float
    gone_at: float | None = None
    top: str = ""
    translation: str = ""
    extra: str = ""
    trans_state: str = "pending"   # pending | done | skipped | failed
    via: str = "ocr"               # ocr | uia
    version: int = 0               # bumps when a more complete reading replaces the text (stale translations)


@dataclass
class Readers:
    """Factories for what touches Windows (replaced by fakes in tests)."""
    uia: Callable[[], Any] = field(default=lambda: _real_uia())
    ocr: Callable[[str], Any] = field(default=lambda lang: _real_ocr(lang))
    # only while the target is the window in front: a grab copies whatever covers the box
    client_box: Callable[[int], Any] = field(default=lambda hwnd: _capture().front_client_box(hwnd))
    grab: Callable[[Any], Any] = field(default=lambda box: _capture().grab(box))


def _capture() -> Any:
    from dualyrics.screen import capture

    return capture


def _real_uia() -> Any:
    from dualyrics.screen.uia import UiaReader

    return UiaReader()


def _real_ocr(lang: str) -> Any:
    from dualyrics.screen.ocr import WinOcr

    return WinOcr(lang)


class ScreenReader:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]], live_tr: Any, readers: Readers | None = None,
                 translate_async: bool = True) -> None:
        self.get_cfg = get_cfg
        self.live_tr = live_tr                 # LiveTranslator (shared cache with Live mode)
        self.readers = readers or Readers()
        self.tracker = SubtitleTracker()
        self.detector = ChangeDetector()
        self.context = ContextWindow(4)
        self.error = ""
        self.last_via = ""
        self._target: Target | None = None
        self._last_key: tuple[int, str] | None = None
        self._want = False
        self._gen = 0
        self._owner = 0                        # generation of the worker that owns the OCR / UIA readers
        self._reader_lock = threading.Lock()
        self._step_lock = threading.Lock()
        self._lock = threading.RLock()
        self._cap: ScreenCaption | None = None
        self._next_id = 1
        self._uia: Any = None
        self._uia_failed = False
        self._uia_text_at = -1e9
        self._ocr: Any = None
        self._ocr_lang = ""
        self._ocr_failed_at = -1e9
        self._ocr_lines: list[str] = []
        self.static = StaticLines()
        self._now = 0.0
        self.dedupe = dedupe.TextResultCache(clock=lambda: self._now)
        self._memo: deque[tuple[str, str, str, str, str]] = deque(maxlen=MEMO)  # text, top, translation, extra, state
        self._memo_for: tuple[Any, ...] = ()   # the settings the memo's translations were made with
        self.reused = 0                       # translations reused for repeated readings (no request)
        self._async = translate_async
        self._tx = ThreadPoolExecutor(max_workers=2, thread_name_prefix="screen-tr") if translate_async else None

    # ------------------------------------------------------------------ control (Tk thread)
    @property
    def running(self) -> bool:
        return self._want

    def target_where(self) -> str:
        """The app or game being read, or its media title (for the usage meter)."""
        t = self._target
        return (t.where or t.title) if t is not None else ""

    def start(self, target: Target) -> None:
        self._target = target
        if self._want:
            return
        with self._reader_lock:
            self._want = True
            self._gen += 1
            self._owner = self._gen      # this worker owns the readers now (an old one still ending leaves them)
        threading.Thread(target=self._run, args=(self._gen,), name="screen", daemon=True).start()

    def stop(self) -> None:
        if self._want:
            self._gen += 1
        self._want = False
        with self._lock:
            self._cap = None
        self.tracker.reset()
        self._last_key = None

    def shutdown(self) -> None:
        self.stop()
        if self._tx is not None:
            self._tx.shutdown(wait=False, cancel_futures=True)

    def has_text(self, now: float | None = None) -> bool:
        return self._want and self.tracker.has_text(time.monotonic() if now is None else now)

    # ------------------------------------------------------------------ worker
    def _run(self, gen: int) -> None:
        try:
            from dualyrics.screen.uia import com_init

            com_init()
        except Exception:
            pass
        while self._want and gen == self._gen:
            t0 = time.monotonic()
            try:
                with self._step_lock:            # never two samples at once (a quick stop / start)
                    if self._want and gen == self._gen:
                        self.step(t0)
            except Exception as e:  # noqa: BLE001 - a broken reader must never end the loop silently
                if str(e) != self.error:
                    log.warning("screen reader: %s", e)
                self.error = str(e)
            fps = float(self.get_cfg().get("ocr_fps", 3.5))
            wait = max(0.05, 1.0 / max(0.5, fps) - (time.monotonic() - t0))
            end = time.monotonic() + wait
            while self._want and gen == self._gen and time.monotonic() < end:
                time.sleep(min(0.05, max(0.0, end - time.monotonic())))
        with self._step_lock, self._reader_lock:
            if self._owner != gen:
                return                  # a newer worker took over the readers: they are its to close
            r, self._ocr, self._ocr_lang = self._ocr, None, ""
            self._uia = None            # made in this thread's COM apartment: the next worker makes its own
        try:
            if r is not None:
                r.close()
        except Exception:
            pass

    def _uia_reader(self) -> Any:
        if self._uia is None and not self._uia_failed:
            try:
                self._uia = self.readers.uia()
            except Exception as e:
                self._uia_failed = True
                log.info("UI Automation reader unavailable: %s", e)
        return self._uia

    def _ocr_reader(self, lang: str, now: float) -> Any:
        if self._ocr is not None and self._ocr_lang == lang:
            return self._ocr
        if now - self._ocr_failed_at < 30:
            return None
        try:
            self._ocr, self._ocr_lang = self.readers.ocr(lang), lang
        except Exception as e:
            self._ocr, self._ocr_failed_at = None, now
            self.error = f"OCR: {e}"
            log.info("OCR unavailable: %s", e)
        return self._ocr

    def read_once(self, target: Target, cfg: dict[str, Any], now: float) -> tuple[str | None, bool]:
        """One reading of the target: (text or None when nothing could be read, trusted)."""
        if cfg.get("screen_uia", True) and target.kind == "browser":
            uia = self._uia_reader()
            if uia is not None:
                text = uia.read(target.hwnd, cfg.get("screen_uia_classes") or sources_mod.DEFAULT_UIA_CLASSES, now)
                if text:
                    self._uia_text_at = now
                if now - self._uia_text_at < UIA_TRUST:   # this page gives its captions: no OCR needed
                    self.last_via = "uia"
                    return text or "", True
        if not cfg.get("screen_ocr", True):
            return None, False
        ocr = self._ocr_reader(cfg.get("ocr_language", "auto"), now)
        if ocr is None:
            return None, False
        client = self.readers.client_box(target.hwnd)
        if client is None:
            return None, False
        box = sources_mod.region_box(client, target.region)
        img = self.readers.grab(box)
        if self.detector.check(img, now):
            self._ocr_lines = ocr.recognize_lines(img)
        self.last_via = "ocr"
        # lines that never change (HUD legends, logos, menu titles) are not subtitles
        return " ".join(self.static.feed(self._ocr_lines, now)), False

    def _memo_lookup(self, text: str) -> tuple[str, str, str, str, str] | None:
        with self._lock:
            best = None
            for m in self._memo:
                sim = 1.0 if m[0] == text else dedupe.similarity(m[0], text)
                if sim > dedupe.UPPER_SIMILARITY_THRESHOLD and (best is None or sim > best[0]):
                    best = (sim, m)
        return best[1] if best else None

    def step(self, now: float) -> None:
        self._now = now
        target = self._target
        if target is None or not target.hwnd:
            return
        key = (target.hwnd, target.key)
        if key != self._last_key:
            self._last_key = key
            self.tracker.reset()
            self.detector.reset()
            self._ocr_lines = []
            self.static.reset()
            self._uia_text_at = -1e9
            self.dedupe.reset()
            with self._lock:
                self._cap = None
                self._memo.clear()
        cfg = self.get_cfg()
        mk = memo_key(cfg)
        with self._lock:
            if mk != self._memo_for:          # another target language or pivot: earlier translations do not fit
                self._memo_for = mk
                self._memo.clear()
        text, trusted = self.read_once(target, cfg, now)
        if text is None:
            return
        self.error = ""
        with self._lock:
            cap = self._cap
            if cap is not None and text and cap.translation and similar(text, cap.translation):
                text = ""   # our own overlay read back (capture exclusion not supported): not a subtitle
        ev = self.tracker.feed(text, now, trusted)
        if ev is None:
            return
        reuse = None
        if ev.kind in ("show", "update") and ev.text:
            repeated, _it = self.dedupe.is_cached(ev.text, dedupe.TRUSTED if trusted else dedupe.letters(ev.text))
            self.dedupe.end_iteration()
            if repeated:
                reuse = self._memo_lookup(ev.text)
        with self._lock:
            if ev.kind == "clear":
                if self._cap is not None and self._cap.gone_at is None:
                    self._cap.gone_at = now
                return
            if ev.kind == "resume" and self._cap is not None and similar(ev.text, self._cap.text):
                self._cap.gone_at = None
                return
            if ev.kind == "update" and self._cap is not None and self._cap.gone_at is None:
                cap = self._cap                  # same caption, more complete text: translate it again
                cap.text, cap.top, cap.trans_state = ev.text, ev.text, "pending"
                cap.version += 1                 # the old translation stays on screen until the new one comes
                ev = None
            if ev is not None:
                cap = ScreenCaption(self._next_id, ev.text, now, top=ev.text, via="uia" if trusted else "ocr")
                self._next_id += 1
                self._cap = cap
            if reuse is not None and reuse[4] in ("done", "skipped"):
                # the same line read again (a flicker, a repeated subtitle): its translation, no request
                _t, cap.top, cap.translation, cap.extra, cap.trans_state = reuse
                self.reused += 1
                return
        if cfg.get("show_translation", True):
            self._translate(cap, cfg, target)
        else:
            cap.trans_state = "skipped"

    # ------------------------------------------------------------------ translation
    def _translate(self, cap: ScreenCaption, cfg: dict[str, Any], target: Target) -> None:
        self.context.resize(cfg.get("live_context_lines", 4))
        ctx = self.context.pairs()
        version, text = cap.version, cap.text
        mk = memo_key(cfg)

        def work() -> None:
            try:
                res = pivot.pivot_text(text, self.live_tr, cfg, ctx, where=target.where, title=target.title)
            except TranslationError as e:
                log.info("screen translation failed: %s", e)
                res = None
            except Exception:
                log.exception("screen translation crashed")
                res = None
            with self._lock:
                if cap.version != version:
                    return                       # a more complete reading is being translated meanwhile
                if res is None:
                    cap.trans_state = "failed"
                    return
                shown = cap.translation
                keep = dedupe.same_translation(shown, res.translation)   # only a character or two changed
                cap.top, cap.extra, cap.trans_state = res.top, res.extra, res.state
                cap.translation = shown if keep else res.translation
                self.context.add(res.english or text, cap.translation, key=cap.id)
                if res.state in ("done", "skipped") and mk == self._memo_for:
                    self._memo.append((text, cap.top, cap.translation, cap.extra, res.state))

        if self._tx is None:
            work()
            return
        try:
            self._tx.submit(work)
        except RuntimeError:  # shutting down
            pass

    # ------------------------------------------------------------------ view (Tk thread)
    def view(self, translation_only: bool = False, now: float | None = None) -> LiveView:
        now = time.monotonic() if now is None else now
        cfg = self.get_cfg()
        with self._lock:
            cap = self._cap
            if cap is None or not self.tracker.active(now):
                return LiveView(visible=False)
            if cap.gone_at is not None:
                keep = max(cap.gone_at + LINGER, cap.shown_at + float(cfg.get("live_min_display", 1.5)))
                if now >= keep:
                    return LiveView(visible=False)
            show_tr = bool(cfg.get("show_translation", True))
            pending = show_tr and cap.trans_state == "pending"
            top, translation, extra = cap.top or cap.text, cap.translation if show_tr else "", cap.extra
            if not show_tr:
                top, extra = cap.text, ""
            if translation_only and (translation or pending):
                top, extra = "", ""
            return LiveView(original=top, translation=translation or ("…" if pending else ""), extra=extra,
                            caption_id=cap.id, visible=True)


def probe_window(hwnd: int, region: Any, cfg: dict[str, Any], kind: str = "browser") -> dict[str, Any]:
    """Settings > Sources > Test: what UI Automation and OCR read in that window right now (blocking)."""
    out: dict[str, Any] = {"uia": None, "ocr": None, "error": "", "ocr_language": ""}
    readers = Readers()
    try:
        from dualyrics.screen.uia import com_init

        com_init()
        if cfg.get("screen_uia", True):
            try:
                out["uia"] = readers.uia().read(hwnd, cfg.get("screen_uia_classes") or
                                                sources_mod.DEFAULT_UIA_CLASSES, now=1e9)
            except Exception as e:
                out["error"] = f"UIA: {e}"
        if cfg.get("screen_ocr", True):
            client = _capture().client_box(hwnd)      # the user asked for this test: Settings is in front
            if client is None:
                out["error"] = "window not found"
            else:
                ocr = readers.ocr(cfg.get("ocr_language", "auto"))
                out["ocr_language"] = getattr(ocr, "language", "")
                out["ocr"] = ocr.recognize(readers.grab(sources_mod.region_box(client, region)))
                ocr.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:300]
    return out
