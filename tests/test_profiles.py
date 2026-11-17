"""Per-app profiles: validation, adding apps and matching the app in use."""

from dualyrics import profiles as P
from dualyrics import settings as S
from dualyrics.profiles import AppRef, match_profile
from dualyrics.sync import current_index, lookahead_window

SPOTIFY = AppRef("spotify.exe", r"C:\Users\x\AppData\Roaming\Spotify\Spotify.exe")
CHROME = AppRef("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
GAME = AppRef("gow.exe", r"D:\Games\God of War Ragnarok\GoWR.exe")
WORD = AppRef("winword.exe")


def profs(*extra):
    out = P.builtin_profiles()
    for e in extra:
        out = P.add_profile(out, e)
    return P.validate_profiles(out)


def ids(lst):
    return [p["id"] for p in lst]


# ---------------------------------------------------------------- validation
def test_builtins_always_present_and_catch_alls_last():
    v = P.validate_profiles(None)
    assert ids(v) == ids(P.builtin_profiles())
    assert ids(v)[-3:] == [P.MEDIA_ANY_ID, P.FULLSCREEN_ID, P.DEFAULT_ID]
    assert next(p for p in v if p["id"] == P.DEFAULT_ID)["enabled"] is False   # unknown apps: off by default
    assert next(p for p in v if p["id"] == P.FULLSCREEN_ID)["mode"] == "live"


def test_settings_roundtrip_with_profiles():
    d = S.defaults()
    assert S.validate(d) == d
    assert S.validate({"profiles": "junk"})["profiles"] == d["profiles"]


def test_builtin_keeps_user_behaviour_but_code_identity():
    raw = [{"id": "spotify", "enabled": False, "mode": "off", "exes": ["evil.exe"], "width": 50, "name": "X"}]
    sp = next(p for p in P.validate_profiles(raw) if p["id"] == "spotify")
    assert sp["enabled"] is False and sp["mode"] == "off"
    assert sp["exes"] == ["spotify.exe"] and sp["name"] == "Spotify"
    assert sp["width"] == 300                                                  # clamped


def test_user_profiles_are_cleaned():
    raw = [{"id": "g", "name": "Game", "exes": ["D:\\Games\\x\\Game.EXE", "notes.txt"], "mode": "fly",
            "click_through": "yes", "target_language": "xx"},
           {"id": "g", "name": "Dup", "exes": ["other.exe"]},
           {"id": "nothing", "exes": []},
           "garbage"]
    v = P.validate_profiles(raw, lang_codes=["fa", "en"])
    user = [p for p in v if not p["builtin"]]
    assert [p["exes"] for p in user] == [["game.exe"], ["other.exe"]]
    assert user[0]["mode"] == "auto" and user[0]["click_through"] is None and user[0]["target_language"] == ""
    assert user[1]["id"] != user[0]["id"]


def test_new_profile_from_games_preset_and_add_once():
    g = P.new_profile("God of War Ragnarök", GAME.path, "games")
    assert g["exes"] == ["gowr.exe"] and g["mode"] == "live" and g["template"] == "game_hud"
    assert g["click_through"] is True and g["activation"] == "foreground" and g["kind"] == "game"
    lst = P.add_profile(P.builtin_profiles(), g)
    assert ids(lst)[-4] == g["id"] and ids(lst)[-3:] == list(P.SPECIAL_IDS)
    assert len(P.add_profile(lst, dict(g))) == len(lst)                       # same exe and path: not twice


# ---------------------------------------------------------------- matching
def test_foreground_app_wins():
    m = match_profile(profs(), foreground=SPOTIFY, running=[CHROME])
    assert (m.profile["id"], m.via) == ("spotify", "foreground")


def test_media_session_owner_rules_when_foreground_is_unknown():
    m = match_profile(profs(), foreground=WORD, running=[WORD, CHROME, SPOTIFY], media_app="spotify.exe",
                      media_playing=True)
    assert (m.profile["id"], m.via) == ("spotify", "media")
    m = match_profile(profs(), foreground=WORD, media_app="chrome", media_playing=True)
    assert m.profile["id"] == "browsers"


def test_unknown_player_uses_media_any_so_v1_players_keep_working():
    m = match_profile(profs(), foreground=WORD, media_app="plexamp.exe", media_playing=True)
    assert (m.profile["id"], m.via) == (P.MEDIA_ANY_ID, "media")


def test_fullscreen_unknown_app_gets_the_games_profile():
    m = match_profile(profs(), foreground=GAME, fullscreen=True)
    assert (m.profile["id"], m.via) == (P.FULLSCREEN_ID, "fullscreen")
    assert P.is_live_context(m.profile, m.via)
    m = match_profile(profs(), foreground=CHROME, fullscreen=True)          # F11 browser keeps its own profile
    assert m.profile["id"] == "browsers" and not P.is_live_context(m.profile, m.via)


def test_disabled_profile_falls_through():
    lst = profs()
    next(p for p in lst if p["id"] == "browsers")["enabled"] = False
    m = match_profile(lst, foreground=CHROME, fullscreen=True, media_app="spotify.exe", media_playing=True)
    assert m.profile["id"] == "spotify"      # no fullscreen takeover for a known (disabled) app


def test_foreground_only_profile_is_not_used_in_background():
    g = P.new_profile("GoW", GAME.path, "games")
    lst = profs(g)
    m = match_profile(lst, foreground=WORD, running=[GAME])
    assert m.profile["id"] == P.DEFAULT_ID and m.via == "default"
    m = match_profile(lst, foreground=GAME, running=[GAME])
    assert m.profile["id"] == g["id"] and P.is_live_context(m.profile, m.via)


def test_running_profiles_prefer_explicit_modes():
    iptv = P.new_profile("IPTV", r"C:\IPTV\iptv.exe", "custom")
    iptv.update(mode="live", activation="running")
    lst = profs(iptv)
    m = match_profile(lst, foreground=WORD, running=[CHROME, AppRef("iptv.exe", r"C:\IPTV\iptv.exe")])
    assert (m.profile["id"], m.via) == (iptv["id"], "running")


def test_path_specific_profile_matches_only_that_file():
    g = P.new_profile("GoW", GAME.path, "games")
    lst = profs(g)
    other = AppRef("gowr.exe", r"E:\Other\GoWR.exe")
    assert match_profile(lst, foreground=other).profile["id"] != g["id"]
    assert match_profile(lst, foreground=AppRef.of(GAME.path)).profile["id"] == g["id"]


def test_overrides_and_effective_mode():
    g = P.new_profile("GoW", GAME.path, "games")
    o = P.overrides(g)
    assert o == {"position_preset": "top", "click_through": True, "template": "game_hud"}
    assert P.effective_mode(g, "lyrics") == "live"
    assert P.effective_mode({"mode": "auto"}, "lyrics") == "lyrics"


# ---------------------------------------------------------------- lyrics look-ahead window
def test_lookahead_window():
    times = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert current_index(times, -1) == -1 and current_index(times, 4.5) == 2
    assert lookahead_window(times, 4.5, n=3) == [2, 3, 4]
    assert lookahead_window(times, 4.5, n=3, done={3}) == [2, 4]
    assert lookahead_window(times, 11, n=3) == [5]
    assert lookahead_window([], 0) == []
