"""Review of v2.1 (sources, dubbing, usage, MCP / API): crash-safe ducking, the dubber's quit, Gemini Live
socket handling, screen reader ownership and privacy, strict browser lookups, and every UI string."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import types
from pathlib import Path

import pytest
import requests

from dualyrics import i18n
from dualyrics.dub import ducking
from dualyrics.dub.audio import Clip
from dualyrics.dub.controller import Dubber
from dualyrics.dub.gemini_live import LiveDub

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- ducking: journal and crash recovery
class Sess:
    def __init__(self, key, pid, vol, group=""):
        self.key, self.pid, self.vol, self.group = key, pid, vol, group

    def get_volume(self):
        return self.vol

    def set_volume(self, v):
        self.vol = v


class Backend:
    def __init__(self, sessions):
        self.list = sessions

    def sessions(self):
        return list(self.list)


def test_ducking_keeps_a_journal_only_while_apps_are_ducked(tmp_path):
    j = tmp_path / "ducked.json"
    spotify = Sess("s1", 100, 0.8, "spotify.exe")
    d = ducking.Ducker(Backend([spotify, Sess("own", 1, 1.0)]), own_pids={1}, journal=j)
    assert d.duck(0.5) == 1 and spotify.vol == pytest.approx(0.4)
    data = json.loads(j.read_text(encoding="utf-8"))
    assert data["sessions"] == [{"key": "s1", "group": "spotify.exe", "orig": 0.8, "target": 0.4}]
    d.restore()
    assert spotify.vol == pytest.approx(0.8) and not j.exists()


def test_recover_gives_the_volume_back_after_a_crash_also_to_a_restarted_app(tmp_path):
    j = tmp_path / "ducked.json"
    old = Sess("s1", 100, 0.8, "spotify.exe")
    game = Sess("g1", 200, 1.0, "game.exe")
    ducking.Ducker(Backend([old, game]), own_pids={1}, journal=j).duck(0.6)     # ... and the process dies here
    restarted = Sess("s2", 101, old.vol, "spotify.exe")        # Windows remembered Spotify's lowered volume
    game.vol = 0.25                                             # the user changed the game's volume meanwhile
    assert ducking.recover(Backend([restarted, game]), j) == 1
    assert restarted.vol == pytest.approx(0.8) and game.vol == 0.25 and not j.exists()


def test_recover_keeps_the_journal_when_the_audio_sessions_cannot_be_read(tmp_path):
    j = tmp_path / "ducked.json"
    ducking.Ducker(Backend([Sess("s1", 100, 0.8)]), own_pids={1}, journal=j).duck(0.5)

    class Broken:
        def sessions(self):
            raise OSError("audio service not running")

    assert ducking.recover(Broken(), j) == 0 and j.exists()
    j.write_text("not json", encoding="utf-8")
    assert ducking.recover(Backend([]), j) == 0 and not j.exists()
    assert ducking.recover(Backend([]), j) == 0          # no journal: nothing to do


# ---------------------------------------------------------------- the dubber's quit restores the volume
class FakePlayer:
    def __init__(self):
        self.played = []
        self.closed = False

    def play(self, clip, volume):
        self.played.append(clip.text)
        return True

    def stream(self, pcm, rate, volume):
        return 0.0

    def stream_pending(self, rate):
        return 0.0

    def stop(self):
        pass

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class FakeDucker:
    def __init__(self):
        self.ducked = False
        self.restored = 0

    def duck(self, amount):
        self.ducked = True
        return 1

    def restore(self):
        self.ducked = False
        self.restored += 1
        return 1


def test_shutdown_waits_for_the_player_so_ducked_apps_get_their_volume_back():
    cfg = {"dub_enabled": True, "dub_duck": True, "dub_duck_amount": 60, "dub_volume": 90}
    player, ducker = FakePlayer(), FakeDucker()
    dub = Dubber(lambda: cfg, lambda: None, None, player_factory=lambda: player, ducker_factory=lambda: ducker,
                 engines={})
    dub._enqueue(Clip("hello", pcm=b"\0\0" * 10, seconds=0.0), cfg, "fa")
    deadline = time.monotonic() + 3
    while not player.played and time.monotonic() < deadline:
        time.sleep(0.01)
    assert player.played == ["hello"] and ducker.ducked
    dub.shutdown()            # returns only after the player thread restored the volumes (os._exit follows)
    assert ducker.restored >= 1 and not ducker.ducked and player.closed
    assert not dub._thread.is_alive()


# ---------------------------------------------------------------- Gemini Live: sockets and failures
class FakeGemini:
    def __init__(self):
        self.cool = []
        self.records = []

    def cooling(self, model, key):
        return False

    def _cooldown(self, model, key, seconds):
        self.cool.append((model, key, seconds))

    def record(self, *a):
        self.records.append(a)


def _fake_websocket(monkeypatch, sockets):
    mod = types.ModuleType("websocket")

    class Closed(Exception):
        pass

    class Timeout(Exception):
        pass

    class BadStatus(Exception):
        pass

    class WS:
        close_reason = "invalid argument"

        def __init__(self):
            self.n = 0
            self.closed = False

        def send(self, data):
            pass

        def recv(self):
            self.n += 1
            if self.n == 1:
                return json.dumps({"setupComplete": {}})
            raise Closed("closed")

        def settimeout(self, t):
            pass

        def close(self):
            self.closed = True

    def create_connection(url, timeout=0, **kw):
        ws = WS()
        sockets.append(ws)
        return ws

    mod.create_connection = create_connection
    mod.WebSocketConnectionClosedException = Closed
    mod.WebSocketTimeoutException = Timeout
    mod.WebSocketBadStatusException = BadStatus
    monkeypatch.setitem(sys.modules, "websocket", mod)


def test_a_session_the_server_closes_at_once_is_an_error_not_an_endless_reconnect(monkeypatch):
    sockets = []
    _fake_websocket(monkeypatch, sockets)
    gem = FakeGemini()
    live = LiveDub(requests.Session, gem, lambda pcm, rate: None)
    done = threading.Event()
    th = threading.Thread(target=lambda: (live._run(["m1", "m2"], ["k1"], "Kore", "fa", ""), done.set()),
                          daemon=True)
    th.start()
    assert done.wait(5), "LiveDub kept reconnecting"
    assert live.error is not None and not live.error.rate_limited
    assert len(sockets) == 2 and all(ws.closed for ws in sockets)     # one try per model, every socket closed
    assert [c[0] for c in gem.cool] == ["m1", "m2"]
    assert "k1" not in str(live.error)


def test_a_live_run_stopped_before_its_queued_start_never_opens_a_socket(monkeypatch):
    sockets = []
    _fake_websocket(monkeypatch, sockets)
    live = LiveDub(lambda: None, FakeGemini(), lambda pcm, rate: None)
    live.stop()                     # the dubber switched mode while start() waited in the pool
    live.start(["m1"], ["k1"], "Kore", "fa")
    time.sleep(0.1)
    assert not live.running and sockets == []


# ---------------------------------------------------------------- screen reader: readers and privacy
def test_an_ending_worker_never_closes_the_readers_a_new_worker_took_over():
    from dualyrics.screen.engine import Readers, ScreenReader, Target

    class Ocr:
        def __init__(self):
            self.closed = False

        def recognize_lines(self, img):
            assert not self.closed, "used after close"
            return []

        def close(self):
            self.closed = True

    made = []

    def make_ocr(lang):
        made.append(Ocr())
        return made[-1]

    from PIL import Image

    readers = Readers(uia=lambda: None, ocr=make_ocr, client_box=lambda hwnd: (0, 0, 400, 200),
                      grab=lambda box: Image.new("RGB", (40, 20)))
    r = ScreenReader(lambda: {"ocr_fps": 6, "screen_ocr": True, "screen_uia": False}, None, readers,
                     translate_async=False)
    target = Target(hwnd=1, key="game", kind="game")
    r.start(target)
    time.sleep(0.3)
    first = r._ocr
    assert first is not None
    r.stop()
    r.start(target)                  # at once: the old worker is still finishing its sleep
    time.sleep(0.4)
    assert r._ocr is first and not first.closed        # the new worker still uses it
    r.stop()
    time.sleep(0.4)
    assert first.closed and r._ocr is None             # closed by its owner once nobody reads any more


def test_the_screen_is_grabbed_only_while_the_target_window_is_in_front(monkeypatch):
    from dualyrics.screen import capture, engine

    assert capture.front_client_box(0) is None
    calls = []
    monkeypatch.setattr(capture, "is_front", lambda hwnd: False)
    monkeypatch.setattr(capture, "client_box", lambda hwnd: calls.append(hwnd) or (0, 0, 100, 100))
    assert engine.Readers().client_box(1234) is None and calls == []   # covered by another window: no grab
    monkeypatch.setattr(capture, "is_front", lambda hwnd: True)
    assert engine.Readers().client_box(1234) == (0, 0, 100, 100)


# ---------------------------------------------------------------- strict browser lookup without a length
def test_a_browser_track_without_its_length_yet_is_not_looked_up_or_cached(monkeypatch):
    from dualyrics import app as app_mod, settings as S
    from dualyrics.media import Track

    class Cache:
        def __init__(self):
            self.put = []

        def get_lyrics(self, key):
            return None

        def put_lyrics(self, key, value):
            self.put.append(key)

    class Snap:
        def snapshot(self):
            return S.defaults()

    def no_lookup(*a, **k):
        raise AssertionError("looked up without a length")

    monkeypatch.setattr(app_mod, "LyricsClient", no_lookup)
    fake = types.SimpleNamespace(cache=Cache(), settings=Snap(), song=None, _current=lambda gen: True,
                                 _translate=lambda *a: None, session=lambda: None)
    st = app_mod.SongState(gen=1)
    fake.song = st
    track = Track("Song|Artist", "Song", "Artist", "", 0.0, "Chrome")
    app_mod.App._load_inner(fake, track, 1, st)
    assert fake.cache.put == [] and st.wait_length and st.lyrics is not None and not st.lyrics.found


# ---------------------------------------------------------------- every UI string exists in both languages
def test_every_string_the_code_asks_for_exists_in_english_and_persian():
    used: dict[str, str] = {}
    for p in (ROOT / "dualyrics").rglob("*.py"):
        if p.name.startswith("i18n"):
            continue
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"""\bt\(\s*["']([a-z][a-z0-9_]*[a-z0-9])["']""", src):
            used.setdefault(m.group(1), p.name)
    from dualyrics import tray
    from dualyrics.settings_window import SECTIONS

    for key, _icon in SECTIONS:
        used.setdefault(f"sec_{key}", "settings_window.py")
        used.setdefault(f"sec_{key}_caption", "settings_window.py")
    for name in re.findall(r'_item\("([a-z_]+)"', (ROOT / "dualyrics" / "tray.py").read_text(encoding="utf-8")):
        used.setdefault(name, "tray.py")
    from dualyrics.app import NOTICES

    for key in NOTICES.values():
        used.setdefault(key, "app.py")
    for p in ("today", "week", "month"):
        used.setdefault(f"us_{p}", "usage_page.py")
    assert tray is not None
    missing = {k: f for k, f in used.items() if not (i18n.STRINGS.get(k, {}).get("en")
                                                     and i18n.STRINGS.get(k, {}).get("fa"))}
    assert not missing


def test_the_dubbing_status_keys_and_settings_pages_exist():
    from dualyrics.ui import dubbing_page, usage_page

    assert callable(dubbing_page.build) and callable(usage_page.build)
    for key in ("dub_st_no_engine", "dub_st_failed", "dub_st_live_limit", "dub_st_live_failed"):
        assert key in i18n.STRINGS
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for pkg in ("dualyrics.dub", "dualyrics.screen", "dualyrics.live", "dualyrics.ui", "dualyrics.templates"):
        assert f'"{pkg}"' in toml, pkg


# ---------------------------------------------------------------- MCP / API input validation
def test_profile_fields_of_the_wrong_type_are_refused_not_a_crash():
    from dualyrics import control

    ctrl = control.offline_controller()
    for bad in ({"name": 123, "exe": "x.exe"}, {"name": "A", "exes": "abc.exe"}, {"exe": ["x.exe"]},
                {"name": "B", "exe": "b.exe", "media_ids": [1]}):
        with pytest.raises(control.OpError):
            ctrl.call("upsert_profile", {"profile": bad})
    assert not any(p["id"] in ("a", "b", "x") for p in ctrl.s["profiles"])
