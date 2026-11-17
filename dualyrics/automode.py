"""Smart Auto: decide, moment by moment, whether the overlay shows synced lyrics, live captions or nothing.

The decision is a pure function of a few observations (see ``Inputs``) so it can be tested without
Windows, audio or network. ``AutoSwitch`` adds hysteresis on top so the overlay does not flicker
when an observation changes for a split second (a track change, an alt-tab, a lyrics lookup).
The full rule table is documented in docs/ALGORITHM.md.
"""

from __future__ import annotations

from dataclasses import dataclass

MODES = ("auto", "lyrics", "live", "off")    # user-facing choices (global setting and per-app profiles)
LYRICS, LIVE, HIDE = "lyrics", "live", "hide"  # what the overlay does right now
SCREEN = "screen"                              # ... or: the subtitles the app itself shows (UIA / OCR)

# sources, in the order the user gave them (see dualyrics/sources.py)
SRC_LYRICS, SRC_SCREEN, SRC_AUDIO = "lyrics", "screen", "audio"
ALL_SOURCES = (SRC_LYRICS, SRC_SCREEN, SRC_AUDIO)

# lyrics lookup states as seen by the decision
LYR_NONE = "none"               # nothing looked up yet (no track)
LYR_SEARCHING = "searching"
LYR_FOUND = "found"             # synced or plain lyrics, or an instrumental marker (strictly matched in browsers)
LYR_NOT_FOUND = "not_found"
LYR_NETWORK = "network"         # lookup failed on the network; retried later

# seconds a new decision must stay stable before the overlay switches to it
HOLD = {LYRICS: 0.4, SCREEN: 0.4, LIVE: 1.5, HIDE: 2.0}


@dataclass(frozen=True)
class Inputs:
    mode: str = "auto"                 # effective mode: the profile's mode, or the global one when it says auto
    profile_enabled: bool = True       # the matched profile is on (the default profile is off out of the box)
    media_playing: bool = False        # a Windows media session reports "playing"
    media_recent: bool = False         # it was playing a moment ago (paused, between tracks)
    lyrics: str = LYR_NONE             # lookup state of the current track
    live_ready: bool = False           # Live mode is installed and switched on
    live_when_no_lyrics: bool = True   # allowed to transcribe a song or video that has no lyrics
    live_context: bool = False         # foreground is a game, a fullscreen app or a video player (profile kind)
    sources: tuple[str, ...] = ALL_SOURCES   # enabled sources in priority order
    screen_ready: bool = False         # the window in front may be read for subtitles (UIA or OCR works)
    screen_text: bool = False          # ... and it shows subtitles right now (or did a moment ago)
    browser_video: bool = False        # the playing media session is a web browser (a video, most of the time)


@dataclass(frozen=True)
class Decision:
    mode: str      # LYRICS | SCREEN | LIVE | HIDE
    reason: str    # short machine-readable reason, logged and shown in the settings window
    probe_screen: bool = False   # the screen reader should run (it is the next source to try)


def _lyrics_wait_reason(state: str) -> str:
    return {LYR_NETWORK: "network", LYR_NOT_FOUND: "no_lyrics"}.get(state, "searching")


def decide(i: Inputs) -> Decision:
    """Smart source priority (docs/ALGORITHM.md, section 2): walk the enabled sources in the user's order
    and take the first one that has something to show."""
    media = i.media_playing or i.media_recent
    if not i.profile_enabled or i.mode == "off":
        return Decision(HIDE, "profile_off")
    if i.mode == "lyrics":
        return Decision(LYRICS, "forced_lyrics") if media else Decision(HIDE, "idle")
    forced_live = i.mode == "live"
    order = [s for s in i.sources if s in ALL_SOURCES]
    if forced_live:   # "Live" = captions: what the screen shows, else what is heard
        order = [s for s in order if s != SRC_LYRICS] or [SRC_AUDIO]
    context = i.live_context or i.browser_video     # games, films and browser videos do not wait for lyrics
    lyrics_settled = SRC_LYRICS not in order or i.lyrics == LYR_NOT_FOUND
    # the user put audio before lyrics: captions of what is heard even for songs that have lyrics
    audio_first = SRC_AUDIO in order and SRC_LYRICS in order and order.index(SRC_AUDIO) < order.index(SRC_LYRICS)
    probe = False
    for src in order:
        if src == SRC_LYRICS:
            if not i.media_playing:
                continue
            if i.lyrics == LYR_FOUND:
                return Decision(LYRICS, "lyrics_found")
            if i.lyrics != LYR_NOT_FOUND and not context:
                # music waits for the lookup (or shows its network error); the next sources come after
                return Decision(LYRICS, _lyrics_wait_reason(i.lyrics))
        elif src == SRC_SCREEN:
            if i.screen_ready and (context or forced_live or i.media_playing):
                if i.screen_text:
                    return Decision(SCREEN, "screen_text", True)
                probe = True
        elif src == SRC_AUDIO and i.live_ready:
            if forced_live:
                return Decision(LIVE, "forced_live", probe)
            if context:
                return Decision(LIVE, "live_context", probe)
            if i.media_playing and audio_first:
                return Decision(LIVE, "audio_first", probe)
            if i.media_playing and i.live_when_no_lyrics and lyrics_settled:
                return Decision(LIVE, "no_lyrics", probe)
    # no source has anything to show
    if forced_live:
        if not i.live_ready and SRC_AUDIO in order:
            return Decision(LYRICS, "live_unavailable", probe) if media else Decision(HIDE, "live_unavailable", probe)
        return Decision(HIDE, "no_subtitles", probe)
    if i.media_playing:
        if SRC_LYRICS in order:
            return Decision(LYRICS, _lyrics_wait_reason(i.lyrics), probe)
        return Decision(HIDE, "no_source", probe)
    if i.live_context:
        return Decision(HIDE, "live_unavailable" if not i.live_ready else "no_subtitles", probe)
    if i.media_recent and SRC_LYRICS in order:
        return Decision(LYRICS, "paused")
    return Decision(HIDE, "idle", probe)


class AutoSwitch:
    """Hysteresis: a new decision must win for ``HOLD[mode]`` seconds before it replaces the current one.

    The very first decision applies at once, and a profile that says Off applies at once too. Switching
    to LYRICS is the quickest (0.4 s), so synced lyrics appear as soon as they exist.
    """

    def __init__(self, hold: dict[str, float] | None = None) -> None:
        self.hold = dict(HOLD if hold is None else hold)
        self.current: Decision | None = None
        self._pending: Decision | None = None
        self._since = 0.0

    def update(self, d: Decision, now: float) -> Decision:
        if self.current is None:
            self.current = d
            return d
        if d.mode == self.current.mode:
            self.current = d          # keep the newest reason
            self._pending = None
            return d
        if self._pending is None or self._pending.mode != d.mode:
            self._pending, self._since = d, now
        if now - self._since >= self.hold.get(d.mode, 1.0) or d.reason == "profile_off":
            self.current, self._pending = d, None
        return self.current

    def reset(self) -> None:
        self.current = self._pending = None
