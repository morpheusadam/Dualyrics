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

# lyrics lookup states as seen by the decision
LYR_NONE = "none"               # nothing looked up yet (no track)
LYR_SEARCHING = "searching"
LYR_FOUND = "found"             # synced or plain lyrics, or an instrumental marker
LYR_NOT_FOUND = "not_found"
LYR_NETWORK = "network"         # lookup failed on the network; retried later

# seconds a new decision must stay stable before the overlay switches to it
HOLD = {LYRICS: 0.4, LIVE: 1.5, HIDE: 2.0}


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


@dataclass(frozen=True)
class Decision:
    mode: str      # LYRICS | LIVE | HIDE
    reason: str    # short machine-readable reason, logged and shown in the settings window


def decide(i: Inputs) -> Decision:
    """The Smart Auto rule table (first matching rule wins)."""
    media = i.media_playing or i.media_recent
    if not i.profile_enabled or i.mode == "off":
        return Decision(HIDE, "profile_off")
    if i.mode == "lyrics":
        return Decision(LYRICS, "forced_lyrics") if media else Decision(HIDE, "idle")
    if i.mode == "live":
        if i.live_ready:
            return Decision(LIVE, "forced_live")
        return Decision(LYRICS, "live_unavailable") if media else Decision(HIDE, "live_unavailable")

    # ---- auto
    if i.media_playing:
        if i.lyrics == LYR_FOUND:
            return Decision(LYRICS, "lyrics_found")
        if i.lyrics in (LYR_NONE, LYR_SEARCHING):
            # a game or film keeps its captions while a lookup runs; music waits for the result
            if i.live_context and i.live_ready:
                return Decision(LIVE, "live_context")
            return Decision(LYRICS, "searching")
        if i.lyrics == LYR_NETWORK:
            if i.live_context and i.live_ready:
                return Decision(LIVE, "live_context")
            return Decision(LYRICS, "network")
        # not found
        if i.live_ready and (i.live_when_no_lyrics or i.live_context):
            return Decision(LIVE, "no_lyrics")
        return Decision(LYRICS, "no_lyrics")
    if i.live_context:
        return Decision(LIVE, "live_context") if i.live_ready else Decision(HIDE, "live_unavailable")
    if i.media_recent:
        return Decision(LYRICS, "paused")
    return Decision(HIDE, "idle")


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
