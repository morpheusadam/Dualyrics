"""Now-playing information from Windows media controls (SMTC) with a smooth playback clock.

Players report their position rarely (some only on play/pause/seek), so we keep our own clock,
anchored to the last real report, and re-anchor only on new information (seek, pause, resume,
track change). A sticky session rule stops flip-flopping between players.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

PLAYING = 4          # GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
POLL = 0.25          # seconds between polls
SEEK_THRESHOLD = 0.6 # seconds of disagreement that count as a seek
SWITCH_POLLS = 3     # a different session must win this many polls in a row before we switch
RESTART_DELAY = 5.0  # seconds before re-creating the SMTC manager after a failure


@dataclass(frozen=True)
class Track:
    key: str
    title: str
    artist: str
    album: str
    duration: float
    app: str


class PlaybackClock:
    """Position = anchor + elapsed (while playing). Monotonic, so wall-clock changes do not matter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pos = 0.0
        self._at = time.monotonic()
        self.playing = False
        self.duration = 0.0

    def anchor(self, pos: float, playing: bool) -> None:
        with self._lock:
            self._pos, self._at, self.playing = max(0.0, pos), time.monotonic(), playing

    def position(self) -> float:
        with self._lock:
            p = self._pos + (time.monotonic() - self._at if self.playing else 0.0)
        return min(p, self.duration) if self.duration > 0 else p


def _wall(dt: Any) -> float | None:
    try:
        ts = dt.timestamp()
        return ts if ts > 1e9 else None
    except Exception:
        return None


def pick_session(cands: list[dict[str, Any]], sticky: str | None, preferred: list[str]) -> dict[str, Any] | None:
    """Score sessions: playing first, then the one we already follow, then the preferred-player order."""
    best, best_score = None, -1.0
    for c in cands:
        app = c["app"]
        pref = next((len(preferred) - i for i, p in enumerate(preferred) if p and p in app), 0)
        score = (100 if c["playing"] else 0) + (50 if app == sticky else 0) + pref
        if score > best_score:
            best, best_score = c, score
    return best


class MediaWatcher:
    """Background thread polling SMTC. Read ``track``, ``clock``, ``playing``, ``last_playing_at``."""

    def __init__(self, preferred: Callable[[], list[str]]) -> None:
        self.preferred = preferred
        self.track: Track | None = None
        self.clock = PlaybackClock()
        self.has_session = False
        self.last_playing_at = 0.0
        self.error: str = ""
        self._sticky: str | None = None
        self._pending: tuple[str, int] = ("", 0)
        self._last_report: tuple[float, float | None] | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="media", daemon=True)

    @property
    def playing(self) -> bool:
        return self.clock.playing

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        # SMTC can be unavailable for a while (e.g. right after sign-in): keep retrying instead of dying.
        last = ""
        while not self._stop.is_set():
            try:
                asyncio.run(self._loop())
                return
            except Exception as e:  # winrt missing or SMTC unavailable
                self.error = str(e) or type(e).__name__
                if self.error != last:
                    log.exception("media watcher failed; retrying")
                    last = self.error
            self._stop.wait(RESTART_DELAY)

    async def _loop(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        mgr = await Manager.request_async()
        while not self._stop.is_set():
            try:
                await self._poll(mgr)
                self.error = ""
            except Exception as e:
                if str(e) != self.error:
                    log.warning("media poll: %s", e)
                self.error = str(e)
            await asyncio.sleep(POLL)

    async def _poll(self, mgr: Any) -> None:
        cands = []
        for s in mgr.get_sessions():
            info = s.get_playback_info()
            cands.append({"s": s, "app": (s.source_app_user_model_id or "").lower(),
                          "playing": bool(info and info.playback_status == PLAYING)})
        if not cands:
            self.has_session = False
            self.clock.anchor(self.clock.position(), False)
            return
        prefs = [p.lower() for p in self.preferred()]
        best = pick_session(cands, self._sticky, prefs)
        if best is None:
            return
        # hysteresis: switch sessions only after a stable win (stops tab flicker in browsers)
        current = next((c for c in cands if c["app"] == self._sticky), None)
        # compare app ids, not objects: several sessions can share one app id, and comparing objects
        # made the hysteresis fall back to the first (paused) one every few polls
        if current is not None and best["app"] != self._sticky:
            app, n = self._pending
            n = n + 1 if app == best["app"] else 1
            if n < SWITCH_POLLS:
                self._pending = (best["app"], n)
                best = current
            else:
                self._pending = ("", 0)
        else:
            self._pending = ("", 0)
        self._sticky = best["app"]
        self.has_session = True

        s = best["s"]
        props = await s.try_get_media_properties_async()
        title = (props.title or "").strip() if props else ""
        artist = (props.artist or "").strip() if props else ""
        album = (props.album_title or "").strip() if props else ""
        tl = s.get_timeline_properties()
        playing = best["playing"]
        if playing:
            self.last_playing_at = time.time()
        if not title:
            self.clock.anchor(self.clock.position(), False)
            return

        rep_pos = tl.position.total_seconds() if tl else 0.0
        duration = max(0.0, tl.end_time.total_seconds()) if tl else 0.0
        updated = _wall(tl.last_updated_time) if tl else None
        has_timeline = duration > 0 or rep_pos > 0
        est = rep_pos + (max(0.0, time.time() - updated) if (playing and updated) else 0.0)
        report = (rep_pos, updated)
        new_info = report != self._last_report
        self._last_report = report

        key = f"{title}|{artist}"
        if self.track is None or key != self.track.key:
            self.track = Track(key, title, artist, album, duration, best["app"])
            self.clock.duration = duration
            self.clock.anchor(est if has_timeline else 0.0, playing)
            log.info("track: %s — %s [%s] %.0fs", artist, title, best["app"], duration)
            return
        if duration and abs(duration - self.clock.duration) > 1:
            self.clock.duration = duration
            self.track = Track(key, title, artist, album, duration, best["app"])
        ours = self.clock.position()
        if playing != self.clock.playing:
            # pause/resume: trust a fresh report, otherwise freeze/continue our own clock
            self.clock.anchor(est if (new_info and has_timeline) else ours, playing)
        elif new_info and has_timeline and abs(est - ours) > SEEK_THRESHOLD:
            self.clock.anchor(est, playing)  # seek, restart (repeat) or drift correction
