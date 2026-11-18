"""Fixes found by the end-to-end run of v2.2.1 on a real PC (Spotify, YouTube in Chrome, dubbing, settings)."""

import types

from dualyrics import settings as S, sources
from dualyrics.live import engine as engine_mod
from dualyrics.live.text import Recognition
from dualyrics.screen.engine import Readers, ScreenReader, Target


# ---------------------------------------------------------------- Live: every caption stays readable
def _engine():
    cfg = {**S.defaults(), "show_translation": False, "live_save_transcripts": False, "live_engine": "whisper"}
    return engine_mod.LiveEngine(lambda: cfg, lambda: None, types.SimpleNamespace(gemini=None, free=None))


def _say(eng, text, t):
    kind, _cap = eng.stream.push(Recognition(text, t - 1.0, t, language="en"), now=t)
    assert kind == "new"


def _timeline(eng, start, end, step=0.1):
    shown, t = [], start
    while t <= end:
        v = eng.view(t)
        if v.visible and (not shown or shown[-1][1] != v.original):
            shown.append((round(t, 2), v.original))
        t += step
    return shown


def test_captions_that_come_together_each_stay_on_screen():
    eng = _engine()
    t0 = 1_000_000.0
    # one Whisper segment with three sentences (seen live: lines on screen for 0.1-0.4 s, unreadable)
    for k, text in enumerate(["Anyway the three boys came in.", "They put these boxes down.",
                              "And the first boy said, I bring you gold."]):
        _say(eng, text, t0 + k * 0.05)
    shown = _timeline(eng, t0 + 0.1, t0 + 6)
    assert [s[1] for s in shown] == ["Anyway the three boys came in.", "They put these boxes down.",
                                     "And the first boy said, I bring you gold."]
    times = [s[0] for s in shown]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= engine_mod.MIN_HOLD - 0.05 for g in gaps)      # each one readable
    assert times[-1] - t0 <= 2.6                                   # and the overlay caught up quickly


def test_a_caption_counts_its_time_on_screen_from_when_it_came():
    eng = _engine()
    t0 = 3_000_000.0
    _say(eng, "There isn't an education system on the planet.", t0)
    assert eng.view(t0 + 0.05).original.startswith("There isn't")
    _say(eng, "The teachers dance every day.", t0 + 2.4)     # after the first one's turn, before it expired
    assert eng.view(t0 + 2.45).original == "The teachers dance every day."
    _say(eng, "Why not?", t0 + 2.9)                          # seen live: replaced the line above after 0.55 s
    assert eng.view(t0 + 3.5).original == "The teachers dance every day."
    assert eng.view(t0 + 4.0).original == "Why not?"


def test_a_lone_new_caption_shows_at_once_and_stale_ones_are_skipped():
    eng = _engine()
    t0 = 2_000_000.0
    _say(eng, "First line here.", t0)
    assert eng.view(t0 + 0.1).original == "First line here."
    # the overlay showed another source meanwhile: lines heard long ago are not replayed
    _say(eng, "Heard while the overlay was busy.", t0 + 2)
    _say(eng, "Said just now.", t0 + 20)
    assert eng.view(t0 + 20.1).original == "Said just now."
    # a single newer caption after a pause replaces the old one immediately
    _say(eng, "Another sentence.", t0 + 40)
    assert eng.view(t0 + 40.05).original == "Another sentence."


# ---------------------------------------------------------------- a browser window is not read with OCR
def test_ocr_reads_a_browser_only_fullscreen_or_with_its_own_area():
    browser = {"id": "browsers", "kind": "browser"}
    assert not sources.ocr_allowed(browser, fullscreen=False)
    assert sources.ocr_allowed(browser, fullscreen=True)
    assert sources.ocr_allowed({**browser, "ocr_region": [0.1, 0.7, 0.9, 0.95]}, fullscreen=False)
    for kind in ("video", "game", "other"):
        assert sources.ocr_allowed({"kind": kind}, fullscreen=False)


class _Ocr:
    def __init__(self):
        self.calls = 0

    def recognize_lines(self, img):
        self.calls += 1
        return ["See similar thumbnails", "Newest All changes"]   # a YouTube side panel, not subtitles

    def close(self):
        pass


def test_windowed_browser_page_text_never_becomes_subtitles():
    ocr = _Ocr()
    cfg = {**S.defaults(), "target_language": "fa"}
    readers = Readers(uia=lambda: None, ocr=lambda lang: ocr, client_box=lambda hwnd: (0, 0, 1000, 500),
                      grab=lambda box: object())
    r = ScreenReader(lambda: cfg, None, readers, translate_async=False)
    r._target, r._want = Target(hwnd=1, key="browsers", kind="browser", ocr=False), True
    for i in range(20):
        r.step(i * 0.3)
    assert ocr.calls == 0 and not r.has_text(6.0)
    # nothing copies the screen, so the overlay need not be hidden from screen recordings
    assert not r.grabs_screen
    r._target = Target(hwnd=1, key="browsers", kind="browser", ocr=True)
    assert r.grabs_screen
    cfg["screen_ocr"] = False
    assert not r.grabs_screen


# ---------------------------------------------------------------- MCP: a portable data folder is passed on
def test_mcp_client_config_passes_a_portable_data_folder(monkeypatch, tmp_path):
    import json

    from dualyrics import mcp

    monkeypatch.setenv("DUALYRICS_HOME", str(tmp_path))
    entry = json.loads(mcp.claude_desktop_config())["mcpServers"]["dualyrics"]
    assert entry["env"]["DUALYRICS_HOME"] == str(tmp_path)
    assert "--env DUALYRICS_HOME=" in mcp.claude_code_command()
    monkeypatch.delenv("DUALYRICS_HOME")
    entry = json.loads(mcp.claude_desktop_config())["mcpServers"]["dualyrics"]
    assert "DUALYRICS_HOME" not in entry.get("env", {})


# ---------------------------------------------------------------- a template's question never stops the ticks
def test_template_hint_question_is_asked_from_the_event_loop_not_inside_the_tick(monkeypatch):
    import tkinter.messagebox as mb

    from dualyrics import app as app_mod

    class Store:
        def exists(self, tid):
            return True

        def resolve(self, tid):
            return {"hints": {"placement": "top", "click_through": True}}

    later, asked, presets = [], [], []
    settings = {"template": "karaoke", "position_preset": "bottom", "click_through": False, "locked": False}

    class Settings(dict):
        def update(self, **kw):
            dict.update(self, **kw)

    s = Settings(settings)
    fake = types.SimpleNamespace(
        templates=Store(), match=types.SimpleNamespace(profile={}), settings=s, _stopping=False,
        root=types.SimpleNamespace(after=lambda ms, fn: later.append(fn)),
        effective_cfg=lambda: s, _set_preset=presets.append)
    fake._offer_hints = lambda tid, want, parent=None: app_mod.App._offer_hints(fake, tid, want, parent)
    fake._apply_hints = lambda want: app_mod.App._apply_hints(fake, want)
    monkeypatch.setattr(mb, "askyesno", lambda *a, **k: asked.append(a) or True)
    app_mod.App.apply_template(fake, "neon")          # runs inside the tick (overlay menu command)
    assert s["template"] == "neon" and not asked and len(later) == 1   # nothing modal yet
    later.pop()()                                     # the event loop asks
    assert asked and presets == ["top"] and s["click_through"] is True
    app_mod.App.apply_template(fake, "cinema")
    s["template"] = "karaoke"                         # another template chosen before the question came
    later.pop()()
    assert len(asked) == 1
    # templates clicked through while the question is open: no second dialog on top of the first
    s.update(template="neon", position_preset="bottom")

    def nested(*a, **k):
        asked.append(a)
        fake._offer_hints("neon", {"position_preset": "top"})
        return False
    monkeypatch.setattr(mb, "askyesno", nested)
    fake._offer_hints("neon", {"position_preset": "top"})
    assert len(asked) == 2


# ---------------------------------------------------------------- get_status after the player closed
def test_status_shows_nothing_playing_after_the_player_closed(tmp_path):
    from dualyrics import automode, control
    from dualyrics.settings import Settings
    from dualyrics.templates import TemplateStore

    track = types.SimpleNamespace(title="Blinding Lights", artist="The Weeknd", album="After Hours",
                                  app="spotify.exe", duration=200.0, key="k")
    media = types.SimpleNamespace(track=track, playing=False, has_session=True,
                                  clock=types.SimpleNamespace(position=lambda: 143.0))
    cfg = S.defaults()

    class App:
        def call_soon(self, fn):
            fn()

        def effective_cfg(self):
            return cfg

    app = App()
    app.decision = automode.Decision(automode.HIDE, "profile_off")
    app.match = types.SimpleNamespace(profile={"id": "default"}, via="default")
    app.media = media
    app.song = types.SimpleNamespace(lyrics=None, trans_status="", key="")
    app.overlay = types.SimpleNamespace(visible=False, _content=None)
    app.live = types.SimpleNamespace(paused=False)
    ctrl = control.Controller(Settings(tmp_path / "s.json"), TemplateStore(tmp_path / "t"), app=app)
    assert ctrl.call("get_status")["now_playing"]["title"] == "Blinding Lights"
    media.has_session = False                  # Spotify was closed: its session is gone
    st = ctrl.call("get_status")
    assert st["now_playing"] is None and st["lyrics"] == {"status": "none"}
