"""Smart Auto decision table and its hysteresis."""

from dualyrics.automode import (HIDE, LIVE, LYR_FOUND, LYR_NETWORK, LYR_NONE, LYR_NOT_FOUND, LYR_SEARCHING, LYRICS,
                                AutoSwitch, Decision, Inputs, decide)

PLAYING = dict(media_playing=True)


def mode(**kw):
    return decide(Inputs(**kw)).mode


def test_song_with_lyrics_shows_lyrics():
    assert mode(lyrics=LYR_FOUND, live_ready=True, **PLAYING) == LYRICS


def test_song_without_lyrics_goes_live_when_allowed():
    assert mode(lyrics=LYR_NOT_FOUND, live_ready=True, **PLAYING) == LIVE
    assert decide(Inputs(lyrics=LYR_NOT_FOUND, live_ready=True, **PLAYING)).reason == "no_lyrics"


def test_song_without_lyrics_stays_on_lyrics_notice_when_live_not_possible():
    assert mode(lyrics=LYR_NOT_FOUND, live_ready=False, **PLAYING) == LYRICS
    assert mode(lyrics=LYR_NOT_FOUND, live_ready=True, live_when_no_lyrics=False, **PLAYING) == LYRICS


def test_video_player_or_game_without_lyrics_goes_live_even_if_songs_are_not_allowed():
    assert mode(lyrics=LYR_NOT_FOUND, live_ready=True, live_when_no_lyrics=False, live_context=True,
                **PLAYING) == LIVE


def test_searching_waits_for_music_but_not_for_games():
    assert mode(lyrics=LYR_SEARCHING, live_ready=True, **PLAYING) == LYRICS
    assert mode(lyrics=LYR_NONE, live_ready=True, **PLAYING) == LYRICS
    assert mode(lyrics=LYR_SEARCHING, live_ready=True, live_context=True, **PLAYING) == LIVE


def test_network_error_keeps_lyrics_status_unless_live_context():
    assert mode(lyrics=LYR_NETWORK, live_ready=True, **PLAYING) == LYRICS
    assert mode(lyrics=LYR_NETWORK, live_ready=True, live_context=True, **PLAYING) == LIVE


def test_found_lyrics_win_over_game_context():
    # Spotify playing a song with lyrics while a fullscreen game is in front: the lyrics are more exact
    assert mode(lyrics=LYR_FOUND, live_ready=True, live_context=True, **PLAYING) == LYRICS


def test_game_without_media_goes_live_or_hides():
    assert mode(live_context=True, live_ready=True) == LIVE
    assert mode(live_context=True, live_ready=False) == HIDE


def test_nothing_playing_hides_and_a_short_pause_keeps_lyrics():
    assert decide(Inputs()) == Decision(HIDE, "idle")
    assert mode(media_recent=True, lyrics=LYR_FOUND) == LYRICS


def test_profile_off_or_disabled_hides_everything():
    assert decide(Inputs(mode="off", lyrics=LYR_FOUND, **PLAYING)) == Decision(HIDE, "profile_off")
    assert mode(profile_enabled=False, lyrics=LYR_FOUND, live_ready=True, **PLAYING) == HIDE


def test_forced_modes():
    assert mode(mode="lyrics", lyrics=LYR_NOT_FOUND, live_ready=True, **PLAYING) == LYRICS
    assert mode(mode="lyrics") == HIDE
    assert mode(mode="live", live_ready=True) == LIVE
    assert mode(mode="live", live_ready=False, **PLAYING) == LYRICS   # not installed: fall back to lyrics
    assert mode(mode="live", live_ready=False) == HIDE


# ---------------------------------------------------------------- hysteresis
def test_first_decision_applies_at_once():
    sw = AutoSwitch()
    assert sw.update(Decision(LIVE, "x"), 0.0).mode == LIVE


def test_short_flip_is_ignored_and_a_stable_one_applies():
    sw = AutoSwitch({LYRICS: 0.4, LIVE: 1.5, HIDE: 2.0})
    sw.update(Decision(LYRICS, "lyrics_found"), 0.0)
    assert sw.update(Decision(HIDE, "idle"), 1.0).mode == LYRICS      # pending
    assert sw.update(Decision(LYRICS, "lyrics_found"), 1.5).mode == LYRICS
    assert sw.update(Decision(HIDE, "idle"), 2.0).mode == LYRICS      # pending again, timer restarted
    assert sw.update(Decision(HIDE, "idle"), 3.9).mode == LYRICS
    assert sw.update(Decision(HIDE, "idle"), 4.0).mode == HIDE


def test_lyrics_come_back_quickly_and_profile_off_is_immediate():
    sw = AutoSwitch()
    sw.update(Decision(LIVE, "no_lyrics"), 0.0)
    sw.update(Decision(LYRICS, "lyrics_found"), 10.0)
    assert sw.update(Decision(LYRICS, "lyrics_found"), 10.4).mode == LYRICS
    assert sw.update(Decision(HIDE, "profile_off"), 11.0).mode == HIDE
