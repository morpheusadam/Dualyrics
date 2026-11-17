"""Smart source priority: source order, strict lyrics match for browsers, decision table, settings."""

from dualyrics import profiles as P, settings as S, sources as SR
from dualyrics.automode import (HIDE, LIVE, LYR_FOUND, LYR_NOT_FOUND, LYR_SEARCHING, LYRICS, SCREEN, Inputs,
                                decide)
from dualyrics.lyrics import LyricsClient, LyricsResult

PLAYING = dict(media_playing=True)


def mode(**kw):
    return decide(Inputs(**kw)).mode


# ---------------------------------------------------------------- strict lyric match
def test_strict_match_accepts_the_same_song_from_a_video_title():
    assert SR.strict_match("Rick Astley - Never Gonna Give You Up (Official Music Video)", "Rick Astley", 213,
                           "Never Gonna Give You Up", "Rick Astley", 212, 4)
    assert SR.strict_match("Never Gonna Give You Up", "Rick Astley - Topic", 213, "Never Gonna Give You Up",
                           "Rick Astley", 214, 4)


def test_strict_match_accepts_featured_and_co_artists():
    assert SR.strict_match("Señorita", "Shawn Mendes", 191, "Senorita", "Shawn Mendes & Camila Cabello", 191, 4)


def test_strict_match_rejects_an_ordinary_video_with_a_similar_name():
    assert not SR.strict_match("How to cook pasta", "Chef John", 600, "Pasta", "Some Band", 598, 4)
    assert not SR.strict_match("Hello", "My Vlog Channel", 241, "Hello", "Adele", 240, 4)   # artist differs


def test_strict_match_needs_the_duration_within_tolerance():
    args = ("Adele - Hello", "AdeleVEVO")
    assert SR.strict_match(*args, 295, "Hello", "Adele", 295, 4)
    assert not SR.strict_match(*args, 367, "Hello", "Adele", 295, 4)   # music video with a long intro
    assert not SR.strict_match(*args, 0, "Hello", "Adele", 295, 4)     # unknown length: never a strict match
    assert SR.strict_match(*args, 301, "Hello", "Adele", 295, 8)       # the tolerance is a setting


class _Resp:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _Session:
    """LRCLIB fake: /get finds nothing, /search returns a wrong song first and the right one second."""

    def get(self, url, params=None, timeout=None):
        if url.endswith("/get"):
            return _Resp(404, None)
        return _Resp(200, [
            {"trackName": "Pasta Song", "artistName": "Kids Band", "duration": 600, "syncedLyrics": "[00:01.00]la"},
            {"trackName": "Hello", "artistName": "Adele", "duration": 295, "syncedLyrics": "[00:01.00]Hello, it's me"},
        ])


def test_fetch_with_strict_accept_takes_only_the_exact_track():
    c = LyricsClient(_Session())
    res = c.fetch("Adele - Hello", "AdeleVEVO", "", 296, accept=SR.strict_accept("Adele - Hello", "AdeleVEVO", 296))
    assert res.found and res.synced[0].text == "Hello, it's me"
    assert (res.match_title, res.match_artist, res.match_duration) == ("Hello", "Adele", 295.0)
    none = c.fetch("How to cook pasta", "Chef John", "", 600,
                   accept=SR.strict_accept("How to cook pasta", "Chef John", 600))
    assert not none.found and not none.network_error


def test_lyrics_result_keeps_the_match_in_the_cache():
    r = LyricsResult(found=True, plain=["x"], match_title="Hello", match_artist="Adele", match_duration=295)
    back = LyricsResult.from_dict(r.to_dict())
    assert back.has_match_info and back.match_artist == "Adele" and back.match_duration == 295
    assert not LyricsResult.from_dict({"found": True, "plain": ["x"]}).has_match_info   # v2.0 cache entry


def test_browser_media_is_recognised_from_the_profiles():
    profs = P.builtin_profiles()
    assert SR.is_browser_media(profs, "Chrome")
    assert SR.is_browser_media(profs, "308046B0AF4A39CB")        # Firefox's media session id
    assert not SR.is_browser_media(profs, "Spotify.exe")


# ---------------------------------------------------------------- order, switches, regions
def test_order_validation_keeps_user_order_and_adds_missing_sources():
    assert SR.validate_order(["audio", "lyrics", "bogus", "audio"]) == ["audio", "lyrics", "screen"]
    assert SR.validate_order(None) == list(SR.DEFAULT_ORDER)
    assert SR.move(["lyrics", "screen", "audio"], "audio", -1) == ["lyrics", "audio", "screen"]
    assert SR.move(["lyrics", "screen", "audio"], "lyrics", -1) == ["lyrics", "screen", "audio"]


def test_effective_sources_global_or_per_profile():
    cfg = {"source_order": ["screen", "lyrics", "audio"], "sources_off": ["audio"]}
    assert SR.effective_sources(cfg) == ("screen", "lyrics")
    prof = {"sources_custom": True, "source_order": ["audio", "lyrics", "screen"], "sources_off": ["lyrics"]}
    assert SR.effective_sources(cfg, prof) == ("audio", "screen")
    assert SR.effective_sources(cfg, {**prof, "sources_custom": False}) == ("screen", "lyrics")


def test_region_validation_and_box():
    assert SR.validate_region([0.9, 0.95, 0.1, 0.6]) == [0.1, 0.6, 0.9, 0.95]   # corners in any order
    assert SR.validate_region([0.5, 0.5, 0.505, 0.9]) == list(SR.DEFAULT_OCR_REGION)   # too thin
    assert SR.validate_region("nope", ()) == []
    assert SR.region_box((100, 50, 1100, 550), [0.1, 0.5, 0.9, 1.0]) == (200, 300, 1000, 550)


def test_screen_is_read_only_for_the_app_in_front():
    video = {"kind": "video"}
    assert SR.screen_allowed(video, "foreground")
    assert SR.screen_allowed({"kind": "game"}, "fullscreen")
    assert not SR.screen_allowed(video, "media")            # a player in the background
    assert not SR.screen_allowed({"kind": "music"}, "foreground")
    assert SR.screen_allowed({"kind": "music", "ocr_region": [0, 0.5, 1, 1]}, "foreground")


def test_settings_and_profiles_validate_the_new_keys():
    v = S.validate({"source_order": ["audio"], "sources_off": ["screen", "x"], "ocr_region": [2, 2, 3, 3],
                    "ocr_language": "fa-IR", "ocr_fps": 99, "lyrics_strict_tolerance": "bad",
                    "screen_uia_classes": "ytp-caption-segment, <script>"})
    assert v["source_order"] == ["audio", "lyrics", "screen"] and v["sources_off"] == ["screen"]
    assert v["ocr_region"] == list(SR.DEFAULT_OCR_REGION) and v["ocr_language"] == "fa-IR" and v["ocr_fps"] == 6
    assert v["lyrics_strict_tolerance"] == 4.0 and v["screen_uia_classes"] == ["ytp-caption-segment"]
    assert S.validate({"ocr_language": "../x"})["ocr_language"] == "auto"
    d = S.defaults()
    assert d["pivot_english"] is True and d["pivot_show_original"] is False and d["lyrics_strict_browsers"] is True
    profs = P.validate_profiles([{"id": "vlc", "sources_custom": True, "source_order": ["audio"],
                                  "ocr_region": [0.1, 0.1, 0.9, 0.3]}])
    vlc = next(p for p in profs if p["id"] == "vlc")
    assert vlc["sources_custom"] and vlc["source_order"] == ["audio", "lyrics", "screen"]
    assert vlc["ocr_region"] == [0.1, 0.1, 0.9, 0.3]
    spot = next(p for p in profs if p["id"] == "spotify")
    assert spot["sources_custom"] is False and spot["source_order"] == [] and spot["ocr_region"] == []


# ---------------------------------------------------------------- decision: source selection order
def test_default_order_lyrics_then_screen_then_audio():
    base = dict(live_ready=True, screen_ready=True, live_context=True, **PLAYING)
    assert mode(lyrics=LYR_FOUND, screen_text=True, **base) == LYRICS          # lyrics first
    assert mode(lyrics=LYR_NOT_FOUND, screen_text=True, **base) == SCREEN      # then what the app shows
    assert mode(lyrics=LYR_NOT_FOUND, screen_text=False, **base) == LIVE       # then the audio


def test_screen_is_probed_whenever_it_is_the_next_source():
    d = decide(Inputs(lyrics=LYR_NOT_FOUND, live_ready=True, screen_ready=True, live_context=True, **PLAYING))
    assert d.mode == LIVE and d.probe_screen
    assert not decide(Inputs(lyrics=LYR_FOUND, screen_ready=True, live_context=True, **PLAYING)).probe_screen
    assert not decide(Inputs(screen_ready=True)).probe_screen       # nothing in front plays: nothing to read


def test_user_order_is_respected():
    base = dict(live_ready=True, screen_ready=True, screen_text=True, lyrics=LYR_FOUND, **PLAYING)
    assert mode(sources=("screen", "lyrics", "audio"), **base) == SCREEN
    assert mode(sources=("audio", "lyrics", "screen"), **base) == LIVE           # audio before lyrics
    assert mode(sources=("lyrics", "screen", "audio"), **base) == LYRICS


def test_switched_off_sources_are_never_used():
    base = dict(live_ready=True, screen_ready=True, screen_text=True, live_context=True, **PLAYING)
    assert mode(sources=("lyrics", "audio"), lyrics=LYR_NOT_FOUND, **base) == LIVE
    assert mode(sources=("lyrics",), lyrics=LYR_NOT_FOUND, **base) == LYRICS      # "No lyrics" notice
    assert decide(Inputs(sources=("screen",), live_ready=True, **PLAYING)).mode == HIDE


def test_browser_video_without_matching_lyrics_goes_to_screen_then_audio():
    base = dict(browser_video=True, live_ready=True, live_when_no_lyrics=False, **PLAYING)
    assert mode(lyrics=LYR_NOT_FOUND, screen_ready=True, screen_text=True, **base) == SCREEN
    assert mode(lyrics=LYR_NOT_FOUND, **base) == LIVE                 # even with "caption songs" off
    assert mode(lyrics=LYR_SEARCHING, **base) == LIVE                 # a video does not wait for the lookup
    assert mode(lyrics=LYR_FOUND, **base) == LYRICS                   # a strictly matched song


def test_forced_live_uses_screen_subtitles_first():
    base = dict(mode="live", live_ready=True, lyrics=LYR_FOUND, **PLAYING)
    assert mode(screen_ready=True, screen_text=True, **base) == SCREEN
    assert mode(**base) == LIVE
    assert decide(Inputs(mode="live", sources=("lyrics", "screen"), live_ready=True)).mode == HIDE


def test_music_still_waits_for_the_lookup_and_shows_no_lyrics():
    assert mode(lyrics=LYR_SEARCHING, live_ready=True, screen_ready=True, **PLAYING) == LYRICS
    assert decide(Inputs(lyrics=LYR_NOT_FOUND, live_when_no_lyrics=False, live_ready=True, **PLAYING)).reason \
        == "no_lyrics"
