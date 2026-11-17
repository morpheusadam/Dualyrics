"""The dubber: speaks the translation of what the overlay shows (docs/ALGORITHM.md section 9).

The app calls ``update(now, feed)`` every tick with what is on screen: timed lyric lines (``lyrics``), the
current caption of Live mode or of on-screen subtitles (``caption``), or the captured audio itself for the
Gemini Live engine (``audio``). Clips are made on worker threads, played on one player thread (which also
ducks the other apps), so the Tk thread never waits.

Engines: Gemini Live (speech to speech) for the audio source; for text Gemini TTS, Edge or Piper with the
fallback chain of ``engines.speak_chain``: an engine that fails (rate limit, network, not installed) hands
the line to the next one, with a notice.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from dualyrics import langs
from dualyrics.dub import engines as eng, scheduler as sch, voices
from dualyrics.dub.audio import Clip

log = logging.getLogger(__name__)

CLIP_CACHE = 60            # clips kept (a song's chorus is made once)
DUCK_RELEASE = 0.8         # s of silence before other apps get their volume back
SELF_TAIL = 0.6            # s after our voice ends that captured speech can still be our own echo
LIVE_RETRY = 600.0         # s before Gemini Live is tried again after it failed (captions are spoken meanwhile)
OWN_HISTORY = 30.0         # s of playback intervals kept for the self-voice check


@dataclass
class Feed:
    mode: str = "off"                 # off | lyrics | caption | audio
    lang: str = ""                    # target language (what is spoken)
    song: str = ""                    # song or source key: a new one forgets the clips of the old one
    lines: list[sch.Line] = field(default_factory=list)     # lyrics: timed translations
    pos: float = 0.0                  # lyrics: position in the song (s)
    caption_key: str = ""             # caption: id of the line on screen
    caption_text: str = ""            # caption: its translation
    where: str = ""                   # app or game (context for Gemini Live)


class Dubber:
    def __init__(self, get_cfg: Callable[[], dict[str, Any]], session_factory: Callable[[], Any], gemini: Any,
                 player_factory: Callable[[], Any] | None = None,
                 ducker_factory: Callable[[], Any] | None = None,
                 engines: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 start_threads: bool = True) -> None:
        self.get_cfg = get_cfg
        self.session_factory = session_factory
        self.gemini = gemini
        self.clock, self.wall = clock, wall
        self.discovery = eng.Discovery(session_factory)
        self.engines = engines if engines is not None else {
            "gemini_tts": eng.GeminiTTS(gemini, self.discovery), "edge": eng.EdgeTTS(session_factory),
            "piper": eng.PiperTTS()}
        self._player_factory = player_factory
        self._ducker_factory = ducker_factory
        self.on_notice: Callable[[str, dict[str, Any]], None] = lambda what, kw: None
        self.status = ""                      # i18n key of the last problem ('' = fine)
        self.last_engine = ""
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dub-synth")
        self._pq: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._song = ""
        self._ready: dict[str, Clip] = {}     # line key -> clip (lyrics)
        self._busy: set[str] = set()
        self._played: set[str] = set()
        self._cache: OrderedDict[str, Clip] = OrderedDict()
        self.cq = sch.CaptionQueue()
        self._playing_until = 0.0             # clock time the current clip (or stream) ends
        self._own: deque[tuple[float, float, str]] = deque()   # (wall start, wall end, language) of our speech
        self._live: Any = None                # gemini_live.LiveDub while it runs
        self._live_failed_at = -LIVE_RETRY
        self._mode = "off"
        self._gen = 0
        self._player: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start_threads:
            self._thread = threading.Thread(target=self._player_loop, name="dub-play", daemon=True)
            self._thread.start()

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------ settings
    def cfg(self) -> dict[str, Any]:
        return self.get_cfg()

    def enabled(self, cfg: dict[str, Any] | None = None) -> bool:
        return bool((cfg or self.cfg()).get("dub_enabled"))

    # ------------------------------------------------------------------ tick (Tk thread)
    def update(self, feed: Feed) -> None:
        cfg = self.cfg()
        mode = feed.mode if self.enabled(cfg) else "off"
        if mode == "audio" and not self._live_wanted(cfg):
            mode = "caption"                      # Gemini Live cannot run: speak the captions instead
        if mode != self._mode or feed.song != self._song:
            self._switch(mode, feed.song)
        if mode == "off":
            return
        if mode == "lyrics":
            self._lyrics(feed, cfg)
        elif mode == "caption":
            self._caption(feed, cfg)
        elif mode == "audio":
            self._audio(feed, cfg)

    def _switch(self, mode: str, song: str) -> None:
        if self._mode != mode:
            log.info("dubbing: %s", mode)
        self._mode, self._song = mode, song
        self._gen += 1
        with self._lock:
            self._ready.clear()
            self._busy.clear()
            self._played.clear()
        self.cq.clear()
        self.stop_speaking()
        if mode != "audio":
            self._stop_live()

    def stop(self) -> None:
        """Dubbing switched off: silence now (the volume of other apps comes back after a moment)."""
        self._switch("off", "")

    def shutdown(self, timeout: float = 2.0) -> None:
        """Quit: silence, then wait (briefly) for the player thread to give other apps their volume back;
        the app ends with os._exit right after, which would kill the thread before it restores them."""
        self.stop()
        self._stop.set()
        self._pq.put(("quit",))
        self._pool.shutdown(wait=False, cancel_futures=True)
        th = self._thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout)

    def test(self, text: str, lang: str, cfg: dict[str, Any]) -> bool:
        """Settings > Dubbing > Test: make one clip now and play it (blocking; call from a worker thread)."""
        clip = self.synthesize(text, lang, str(cfg.get("dub_voice", "auto")), self._speed(cfg), cfg)
        if clip is None:
            return False
        self._enqueue(clip, cfg, lang)
        return True

    def stop_speaking(self) -> None:
        while True:
            try:
                self._pq.get_nowait()
            except queue.Empty:
                break
        p = self._player
        if p is not None:
            p.stop()
            try:
                p.stop_stream()
            except Exception:
                pass
        self._playing_until = 0.0

    # -- lyrics: made ahead, started on time
    def _lyrics(self, feed: Feed, cfg: dict[str, Any]) -> None:
        lines = feed.lines
        with self._lock:
            have, busy = set(self._ready), set(self._busy)
        for ln in sch.prefetch(lines, feed.pos, have, busy):
            rate = sch.fit_rate(ln.text, feed.lang, ln.slot, self._speed(cfg))
            self._make(ln.key, ln.text, feed.lang, rate, cfg, self._gen, on_done=self._lyric_ready)
        d = sch.due(lines, feed.pos, self._played)
        if d is not None:
            with self._lock:
                clip = self._ready.get(d.key)
            if clip is not None:
                remaining = self.remaining()
                if remaining > 0 and sch.on_overlap(remaining) == "wait":
                    pass                                   # the previous clip ends in a moment
                else:
                    if remaining > 0:
                        self.stop_speaking()
                    self._played.add(d.key)
                    self._enqueue(clip, cfg, feed.lang)
        drop = sch.stale(lines, feed.pos, list(self._ready))
        if drop:
            with self._lock:
                for k in drop:
                    self._ready.pop(k, None)

    def _lyric_ready(self, key: str, clip: Clip | None, gen: int) -> None:
        with self._lock:
            self._busy.discard(key)
            if clip is not None and gen == self._gen:
                self._ready[key] = clip

    # -- captions: spoken once they stop changing
    def _caption(self, feed: Feed, cfg: dict[str, Any]) -> None:
        now = self.clock()
        if feed.caption_key and feed.caption_text:
            self.cq.push(feed.caption_key, feed.caption_text, now)
        c = self.cq.ready(now)
        if c is not None:
            queued = c.queued_at
            self._make(c.key, c.text, feed.lang, self._speed(cfg), cfg, self._gen,
                       on_done=lambda key, clip, gen: self._caption_ready(clip, gen, queued, feed.lang))

    def _caption_ready(self, clip: Clip | None, gen: int, queued_at: float, lang: str) -> None:
        if clip is None or gen != self._gen:
            return
        if not self.cq.fresh(queued_at, self.clock()):
            log.debug("dubbing: dropped a late caption clip")
            return
        self._enqueue(clip, self.cfg(), lang)

    # -- audio: Gemini Live
    def _live_wanted(self, cfg: dict[str, Any]) -> bool:
        if cfg.get("dub_engine") not in ("auto", "gemini_live") or not cfg.get("gemini_keys"):
            return False
        if self.clock() - self._live_failed_at < LIVE_RETRY:
            return False
        import importlib.util

        try:
            return importlib.util.find_spec("websocket") is not None
        except (ImportError, ValueError):
            return False

    def _audio(self, feed: Feed, cfg: dict[str, Any]) -> None:
        live = self._live
        if live is not None and live.error is not None:
            err = live.error
            log.info("gemini live dubbing stopped: %s", err)
            self._live = None
            self._live_failed_at = self.clock()
            self.status = "dub_st_live_limit" if err.rate_limited else "dub_st_live_failed"
            self.on_notice("dub_fallback", {"frm": "Gemini Live", "to": self._engine_label(
                eng.speak_chain(cfg.get("dub_engine", "auto"), bool(cfg.get("gemini_keys")))[0])})
            return
        if live is None:
            from dualyrics.dub.gemini_live import LiveDub

            keys = list(cfg.get("gemini_keys") or [])
            self._live = live = LiveDub(self.session_factory, self.gemini, self._live_audio)
            models = self.discovery.live_models(cfg.get("dub_gemini_live_model", "auto"))

            def start() -> None:
                if keys:
                    self.discovery.refresh(keys[0])
                live.start(self.discovery.live_models(cfg.get("dub_gemini_live_model", "auto")) or models, keys,
                           voices.gemini_voice(str(cfg.get("dub_voice", "auto")), voices.gender_for(cfg, feed.lang)),
                           feed.lang, feed.where)

            self._pool.submit(start)
            self.last_engine = "gemini_live"

    def feed_audio(self, frame: Any, rate: int) -> None:
        """Captured audio (capture thread) for Gemini Live."""
        live = self._live
        if live is not None and self._mode == "audio":
            live.feed(frame, rate)

    def _live_audio(self, pcm: bytes, rate: int) -> None:
        self._pq.put(("stream", pcm, rate, self._volume(self.cfg()), self._target_lang()))

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _target_lang(self) -> str:
        return str(self.cfg().get("target_language", ""))

    # ------------------------------------------------------------------ making clips
    def _speed(self, cfg: dict[str, Any]) -> float:
        return float(cfg.get("dub_speed", 1.0) or 1.0)

    def _volume(self, cfg: dict[str, Any]) -> float:
        return max(0.0, min(1.0, float(cfg.get("dub_volume", 90)) / 100.0))

    def _engine_label(self, name: str) -> str:
        return {"gemini_tts": "Gemini", "edge": "Edge", "piper": "Piper", "gemini_live": "Gemini Live"}.get(name,
                                                                                                          name)

    def _make(self, key: str, text: str, lang: str, rate: float, cfg: dict[str, Any], gen: int,
              on_done: Callable[[str, Clip | None, int], None]) -> None:
        with self._lock:
            if key in self._busy:
                return
            self._busy.add(key)
        voice = str(cfg.get("dub_voice", "auto"))
        ck = f"{lang}|{voice}|{voices.gender_for(cfg, lang)}|{rate:.2f}|{text}"
        hit = self._cache.get(ck)
        if hit is not None:
            self._cache.move_to_end(ck)
            if hit.engine == "gemini_tts" and getattr(self.gemini, "meter", None) is not None:
                self.gemini.meter.cache_hit("tts")
            on_done(key, hit, gen)
            with self._lock:
                self._busy.discard(key)
            return

        def work() -> None:
            clip = None
            try:
                if gen == self._gen:
                    clip = self.synthesize(text, lang, voice, rate, cfg)
                    if clip is not None:
                        self._cache[ck] = clip
                        while len(self._cache) > CLIP_CACHE:
                            self._cache.popitem(last=False)
            except Exception:
                log.exception("dubbing: making a clip failed")
            finally:
                on_done(key, clip, gen)
                with self._lock:
                    self._busy.discard(key)

        try:
            self._pool.submit(work)
        except RuntimeError:          # shutting down
            with self._lock:
                self._busy.discard(key)

    def synthesize(self, text: str, lang: str, voice: str, rate: float, cfg: dict[str, Any]) -> Clip | None:
        """A clip from the first engine of the chain that works (blocking)."""
        chain = eng.speak_chain(str(cfg.get("dub_engine", "auto")), bool(cfg.get("gemini_keys")))
        first_err = ""
        preferred = ""
        for name in chain:
            engine = self.engines.get(name)
            if engine is None or not engine.ready(cfg):
                continue
            preferred = preferred or name
            try:
                clip = engine.speak(text, lang, voice, rate, cfg)
            except eng.TTSError as e:
                first_err = first_err or f"{name}: {e}"
                log.info("dubbing: %s failed: %s", name, e)
                continue
            except Exception as e:  # noqa: BLE001
                first_err = first_err or f"{name}: {e}"
                log.warning("dubbing: %s failed", name, exc_info=True)
                continue
            if clip is None or clip.empty:
                continue
            if name != preferred and self.last_engine != name:
                self.on_notice("dub_fallback", {"frm": self._engine_label(preferred), "to": self._engine_label(name)})
            self.last_engine = name
            self.status = ""
            return clip
        self.status = "dub_st_no_engine" if not first_err else "dub_st_failed"
        if first_err:
            log.info("dubbing: no engine could speak (%s)", first_err)
        return None

    # ------------------------------------------------------------------ playing (player thread)
    def _enqueue(self, clip: Clip, cfg: dict[str, Any], lang: str) -> None:
        if self._pq.qsize() >= 2:            # never fall far behind: keep the newest clips
            try:
                self._pq.get_nowait()
            except queue.Empty:
                pass
        self._pq.put(("clip", clip, self._volume(cfg), lang))

    def remaining(self) -> float:
        return max(0.0, self._playing_until - self.clock())

    def speaking(self) -> bool:
        return self.remaining() > 0 or not self._pq.empty()

    def own_voice(self, language: str, start: float, end: float) -> bool:
        """Captured speech from ``start`` to ``end`` (wall time) in ``language`` is our dubbed voice."""
        if not language:
            return False
        for s, e, lang in list(self._own):
            if langs.same_language(language, lang) and start <= e + SELF_TAIL and end >= s:
                return True
        return False

    def _mark_own(self, seconds: float, lang: str) -> None:
        now = self.wall()
        self._own.append((now, now + seconds, lang))
        while self._own and self._own[0][1] < now - OWN_HISTORY:
            self._own.popleft()

    def _player_loop(self) -> None:
        from dualyrics.dub import ducking

        ducking.com_init()
        if self._ducker_factory is None:
            journal = ducking.journal_path()
            if journal.exists():          # the last run ended while other apps were ducked
                try:
                    ducking.recover(ducking.windows_backend(), journal)
                except Exception:
                    log.debug("ducking recovery failed", exc_info=True)
        player = self._player_factory() if self._player_factory else None
        if player is None:
            from dualyrics.dub.audio import Player

            try:
                player = Player()
            except Exception:
                log.exception("dubbing: no audio output")
                return
        self._player = player
        ducker = None
        idle_since = duck_at = self.clock()
        while not self._stop.is_set():
            try:
                item = self._pq.get(timeout=0.1)
            except queue.Empty:
                item = None
            cfg = self.cfg()
            if item is None:
                pending = 0.0
                try:
                    pending = max((player.stream_pending(r) for r in (24000,)), default=0.0)
                except Exception:
                    pass
                if pending > 0:
                    idle_since = self.clock()
                if ducker is not None and ducker.ducked and self.clock() - idle_since > DUCK_RELEASE:
                    ducker.restore()
                continue
            if item[0] == "quit":
                break
            if cfg.get("dub_duck", True) and float(cfg.get("dub_duck_amount", 60)) > 0:
                if ducker is None:
                    ducker = self._ducker_factory() if self._ducker_factory else ducking.Ducker(
                        ducking.windows_backend(), journal=ducking.journal_path())
                # streamed Live audio comes in many small pieces: look for new apps at most every 2 s
                if not ducker.ducked or item[0] == "clip" or self.clock() - duck_at > 2.0:
                    ducker.duck(float(cfg.get("dub_duck_amount", 60)) / 100.0)
                    duck_at = self.clock()
            elif ducker is not None and ducker.ducked:
                ducker.restore()
            if item[0] == "clip":
                _kind, clip, volume, lang = item
                self._playing_until = self.clock() + clip.seconds
                self._mark_own(clip.seconds, lang)
                try:
                    player.play(clip, volume)
                except Exception:
                    log.exception("dubbing: playback failed")
                self._playing_until = 0.0
            elif item[0] == "stream":
                _kind, pcm, rate, volume, lang = item
                try:
                    left = player.stream(pcm, rate, volume)
                except Exception:
                    log.exception("dubbing: stream playback failed")
                    left = 0.0
                self._playing_until = self.clock() + left
                self._mark_own(left, lang)
            idle_since = self.clock()
        if ducker is not None:
            ducker.restore()
        try:
            player.close()
        except Exception:
            pass
