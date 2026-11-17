"""Regression tests for the v2 pre-release review: model download integrity, profile overrides vs. quick
toggles, user edits during a translation, look-ahead after a seek, overlay sizes per profile, Live back-off,
settings safety (model names, undecryptable keys, v1 migration) and template file names."""

import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from dualyrics import profiles as P
from dualyrics import settings as S
from dualyrics import templates as T
from dualyrics.app import App, SongState
from dualyrics.lyrics import LyricsResult
from dualyrics.live import capture as capture_mod
from dualyrics.live import engine as engine_mod
from dualyrics.live import models
from dualyrics.overlay import Overlay
from dualyrics.winutil import Monitor


# ---------------------------------------------------------------- model download integrity
class _Resp:
    def __init__(self, status: int, body: bytes = b"", payload=None) -> None:
        self.status_code, self._body, self._payload = status, body, payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    def iter_content(self, n):
        for i in range(0, len(self._body), 7):
            yield self._body[i:i + 7]


def _git_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class _Hub:
    """A fake Hugging Face: a tree listing with checksums and file downloads (optionally damaged)."""

    def __init__(self, files: dict[str, bytes], damage: set[str] = frozenset(), extra=()) -> None:
        self.files, self.damage, self.extra, self.gets = files, set(damage), list(extra), []

    def get(self, url, headers=None, stream=False, timeout=None, allow_redirects=True):
        self.gets.append(url)
        if "/api/models/" in url:
            tree = []
            for name, data in self.files.items():
                item = {"type": "file", "path": name, "size": len(data), "oid": _git_oid(b"pointer")}
                if name == "model.bin":
                    item["lfs"] = {"oid": hashlib.sha256(data).hexdigest(), "size": len(data)}
                else:
                    item["oid"] = _git_oid(data)
                tree.append(item)
            return _Resp(200, payload=tree + self.extra)
        name = url.rsplit("/", 1)[1]
        data = self.files[name]
        if name in self.damage:
            data = data[:-1] + bytes([data[-1] ^ 0xFF])
        return _Resp(200, data)


MODEL_FILES = {"config.json": b'{"a": 1}', "model.bin": b"\x00\x01weights" * 50, "tokenizer.json": b"{}",
               "vocabulary.txt": b"a\nb\n"}


def test_model_download_checks_checksums(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    hub = _Hub(MODEL_FILES, extra=[{"type": "file", "path": "../../evil.exe", "size": 3, "oid": "0" * 40}])
    models.download("tiny", hub)
    assert models.is_installed("tiny")
    assert (tmp_path / "faster-whisper-tiny" / "model.bin").read_bytes() == MODEL_FILES["model.bin"]
    assert not list(tmp_path.rglob("evil.exe")) and not any("evil" in u for u in hub.gets)


def test_damaged_model_file_is_rejected_and_not_resumed(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    with pytest.raises(OSError, match="checksum"):
        models.download("tiny", _Hub(MODEL_FILES, damage={"model.bin"}))
    folder = tmp_path / "faster-whisper-tiny"
    assert not models.is_installed("tiny")
    assert not (folder / "model.bin").exists() and not (folder / "model.bin.part").exists()
    with pytest.raises(OSError, match="checksum"):
        models.download("tiny", _Hub(MODEL_FILES, damage={"tokenizer.json"}))   # git blob id check
    models.download("tiny", _Hub(MODEL_FILES))   # a clean retry works and reuses the good files
    assert models.is_installed("tiny")


def test_model_download_one_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "models_dir", lambda: tmp_path)
    inside, peak, lock = [0], [0], threading.Lock()
    real = models._download_locked

    def slow(*a, **k):
        with lock:
            inside[0] += 1
            peak[0] = max(peak[0], inside[0])
        time.sleep(0.2)
        try:
            return real(*a, **k)
        finally:
            with lock:
                inside[0] -= 1

    monkeypatch.setattr(models, "_download_locked", slow)
    ts = [threading.Thread(target=models.download, args=("tiny", _Hub(MODEL_FILES))) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1 and models.is_installed("tiny")


def test_waiting_download_can_be_cancelled(monkeypatch):
    lock = threading.Lock()
    monkeypatch.setitem(models._locks, "base", lock)
    lock.acquire()
    try:
        with pytest.raises(models.DownloadCancelled):
            models.download("base", _Hub(MODEL_FILES), cancelled=lambda: True)
    finally:
        lock.release()


# ---------------------------------------------------------------- settings safety
def test_gemini_model_names_cannot_change_the_url():
    v = S.validate({"gemini_models": ["gemini-2.5-flash", "../../v1/files?x=1", "a/b", "models/x", "ok.model_1"]})
    assert v["gemini_models"] == ["gemini-2.5-flash", "ok.model_1"]
    assert S.validate({"gemini_models": ["../x"]})["gemini_models"] == S.DEFAULT_MODELS


def test_undecryptable_keys_are_kept_until_replaced(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"gemini_keys_protected": "dpapi:bm90LWZvci10aGlzLXVzZXI=", "size_original": 30}),
                 encoding="utf-8")
    st = S.Settings(p)
    assert st["gemini_keys"] == []
    st.update(size_original=31)
    assert json.loads(p.read_text(encoding="utf-8"))["gemini_keys_protected"] == "dpapi:bm90LWZvci10aGlzLXVzZXI="
    st.update(gemini_keys=["AIzaNEWKEY000000000000"])
    raw = p.read_text(encoding="utf-8")
    assert "AIzaNEWKEY" not in raw and "bm90LWZvci10aGlzLXVzZXI=" not in raw
    assert S.Settings(p)["gemini_keys"] == ["AIzaNEWKEY000000000000"]


def test_settings_file_with_bom_is_read(tmp_path):
    p = tmp_path / "settings.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"size_original": 41, "template": "neon"}).encode("utf-8"))
    st = S.Settings(p)
    assert st["size_original"] == 41 and st["template"] == "neon" and not (tmp_path / "settings.broken.json").exists()


def test_settings_revision_grows_only_on_change(tmp_path):
    st = S.Settings(tmp_path / "s.json")
    r = st.rev
    st.update(size_original=st["size_original"])
    assert st.rev == r
    st.update(size_original=33)
    assert st.rev == r + 1


def test_template_ids_never_name_a_windows_device(tmp_path):
    store = T.TemplateStore(tmp_path)
    doc = T.builtin("minimal")
    doc.update(id="con", builtin=False, name={"en": "Con"})
    saved = store.save(doc)
    assert saved["id"] == "con-t" and (tmp_path / "con-t.json").exists()
    assert T.slug("NUL") == "nul-t"


# ---------------------------------------------------------------- app logic without Tk
def _bare_app(tmp_path, profile=None, **settings):
    app = App.__new__(App)
    app.settings = S.Settings(tmp_path / "settings.json")
    if settings:
        app.settings.update(**settings)
    profs = app.settings["profiles"]
    if profile is not None:
        for p in profs:
            if p["id"] == profile["id"]:
                p.update(profile)
        app.settings.update(profiles=profs)
    prof = next((p for p in app.settings["profiles"] if profile and p["id"] == profile["id"]), {})
    app.match = P.Match(prof, "fullscreen")
    app._overrides = P.overrides(prof) if prof else {}
    app._cfg_cache = None
    return app


def _refresh_match(app):
    prof = next(p for p in app.settings["profiles"] if p["id"] == app.match.profile["id"])
    app.match = P.Match(prof, app.match.via)
    app._overrides = P.overrides(prof)


def test_click_through_hotkey_works_over_a_profile_override(tmp_path):
    app = _bare_app(tmp_path, {"id": P.FULLSCREEN_ID, "click_through": True})
    assert app.effective_cfg()["click_through"] is True
    app._handle("toggle_click_through")
    _refresh_match(app)
    assert app.effective_cfg()["click_through"] is False     # the overlay can be reached again
    app._handle("toggle_click_through")
    _refresh_match(app)
    assert app.effective_cfg()["click_through"] is True


def test_menu_choice_clears_a_false_profile_override(tmp_path):
    app = _bare_app(tmp_path, {"id": "spotify", "translation_only": False})
    app._handle_v2("show:translation")
    _refresh_match(app)
    assert app.effective_cfg()["translation_only"] is True


def test_menu_mode_wins_over_the_profile_mode(tmp_path):
    app = _bare_app(tmp_path, {"id": P.FULLSCREEN_ID})       # built-in Games preset: mode live
    assert app.match.profile["mode"] == "live"
    app._handle_v2("mode:lyrics")
    _refresh_match(app)
    assert P.effective_mode(app.match.profile, app.settings["mode"]) == "lyrics"


def test_effective_cfg_cache_follows_changes(tmp_path):
    app = _bare_app(tmp_path)
    a = app.effective_cfg()
    a["size_original"] = 999                       # callers get copies
    assert app.effective_cfg()["size_original"] != 999
    app.settings.update(size_original=40)
    assert app.effective_cfg()["size_original"] == 40
    app._overrides = {"target_language": "de"}
    assert app.effective_cfg()["target_language"] == "de"


def _song_app(tmp_path, lines, status="translating"):
    app = _bare_app(tmp_path)
    puts = []
    app.cache = SimpleNamespace(put_translation=lambda *a: puts.append(a), get_offset=lambda k: 0,
                                get_translation=lambda *a: None)
    app._notice = ("", 0)
    res = LyricsResult(found=True, synced=[SimpleNamespace(time=float(i), text=t) for i, t in enumerate(lines)])
    app.song = SongState(key="a|b", lyrics=res, trans_status=status)
    return app, puts


def test_edit_while_translating_is_kept_and_merged(tmp_path):
    app, puts = _song_app(tmp_path, ["one", "two", "three"])
    app.edit_translation(1, "DOS")
    assert puts == [] and app.song.preview[1] == "DOS" and app.song.trans_status == "translating"
    target = app.effective_cfg()["target_language"]
    app._store_translation(app.song, "a|b", target, "gemini", ["uno", "dos", "tres"], False, "en",
                           dict(app.song.edits))
    assert app.song.translation == ["uno", "DOS", "tres"] and app.song.trans_status == "done"
    assert puts[-1][3]["lines"] == ["uno", "DOS", "tres"] and puts[-1][3]["edited"] is True


def test_edit_after_failure_is_not_cached_as_a_finished_translation(tmp_path):
    app, puts = _song_app(tmp_path, ["one", "two"], status="failed")
    app.edit_translation(0, "UNO")
    assert puts == [] and app.song.trans_status == "failed"   # the retry still happens


def test_edit_of_finished_translation_is_cached(tmp_path):
    app, puts = _song_app(tmp_path, ["one", "two"], status="done")
    app.song.translation = ["uno", "dos"]
    app.edit_translation(1, "DOS!")
    assert app.song.translation == ["uno", "DOS!"] and puts and puts[-1][3]["lines"] == ["uno", "DOS!"]


def test_lookahead_covers_lines_after_a_seek_back(tmp_path):
    lines = [f"line {i}" for i in range(60)]
    app, _ = _song_app(tmp_path, lines)
    app.settings.update(provider="gemini", gemini_keys=["AIzaTEST0000000000000"], fallback_free=True,
                        skip_same_language=False)
    sent = []
    app.translator = SimpleNamespace(free=SimpleNamespace(
        translate=lambda ls, tgt: (sent.append(list(ls)) or [x.upper() for x in ls], "en")))
    app.executor = SimpleNamespace(submit=lambda fn: fn())
    app._stopping = False
    pos = [40.5]
    app.position = lambda: pos[0]
    app.song.times = [float(i) for i in range(60)]
    app._lookahead(app.song)
    assert app.song.preview[40] == "LINE 40"
    pos[0] = 10.5                                   # seek back
    app._lookahead(app.song)
    assert app.song.preview[10] == "LINE 10"
    n = len(sent)
    app._lookahead(app.song)                        # nothing new to send
    assert len(sent) == n


def test_v1_migration_skipped_for_unreadable_settings(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{broken", encoding="utf-8")
    app = App.__new__(App)
    app.settings = S.Settings(p)
    saved = []
    app.templates = SimpleNamespace(exists=lambda i: False, save=lambda d: saved.append(d))
    app._migrate_v1()
    assert saved == [] and app.settings["template"] == "cinema"


def test_v1_migration_moves_left_edge_to_center(tmp_path, monkeypatch):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"version": 1, "width": 800, "position_preset": "custom",
                             "positions": {r"\\.\DISPLAY1": {"x": 100, "y": 900, "anchor": "bottom"}}}),
                 encoding="utf-8")
    from dualyrics import app as app_mod
    monkeypatch.setattr(app_mod.winutil, "primary_monitor", lambda: Monitor("m", 0, 0, 2560, 1400, True, dpi=144))
    app = App.__new__(App)
    app.settings = S.Settings(p)
    store = T.TemplateStore(tmp_path / "templates")
    app.templates = store
    app._migrate_v1()
    assert app.settings["template"] == T.MIGRATED_ID and store.exists(T.MIGRATED_ID)
    assert app.settings["positions"][r"\\.\DISPLAY1"]["x"] == 100 + 600   # 800 px at 150 % -> half is 600


# ---------------------------------------------------------------- overlay sizes per profile (no window)
MON = Monitor(r"\\.\DISPLAY1", 0, 0, 1920, 1040, True, (0, 0, 1920, 1080), 96)


def _bare_overlay(tmp_path, positions, profile_id="spotify"):
    ov = Overlay.__new__(Overlay)
    ov.settings = S.Settings(tmp_path / "settings.json")
    ov.settings.update(positions=positions, position_preset="custom")
    ov.cfg = ov.settings.snapshot()
    ov.profile_id = profile_id
    ov.overrides = {}
    ov.monitor = lambda: MON
    return ov


def test_profile_size_entry_keeps_the_monitor_place(tmp_path):
    ov = _bare_overlay(tmp_path, {MON.name: {"x": 300, "y": 700, "anchor": "bottom", "scale": 1.5}})
    assert ov.user_scale(MON) == 1.5
    ov.change_scale(1.1)
    ov.cfg = ov.settings.snapshot()
    pos = ov.settings["positions"]
    assert pos[f"spotify@{MON.name}"] == {"scale": 1.65}          # from what was on screen, not 1.0
    scope, e = ov._entry(MON)
    assert scope == "global" and e["x"] == 300 and e["scale"] == 1.65   # the dragged place still applies
    ov.change_scale(None)
    ov.cfg = ov.settings.snapshot()
    assert ov.user_scale(MON) == 1.0 and ov._entry(MON)[1].get("x") == 300


# ---------------------------------------------------------------- Live: capture and back-off
def test_capture_reports_a_broken_audio_install(monkeypatch):
    def boom(name):
        raise ImportError("DLL load failed while importing _portaudiowpatch")

    monkeypatch.setattr(capture_mod.deps, "import_module", boom)
    cap = capture_mod.LoopbackCapture(lambda f, r: None)
    cap._run()
    assert "DLL load failed" in cap.fatal


def test_live_start_backs_off_after_repeated_failures(monkeypatch):
    monkeypatch.setattr(engine_mod.deps, "available", lambda: True)
    starts = []
    monkeypatch.setattr(engine_mod.LiveEngine, "_run", lambda self, gen: starts.append(gen))
    eng = engine_mod.LiveEngine(lambda: {}, lambda: None, SimpleNamespace(gemini=None, free=None))
    eng.state, eng._errors, eng._error_at = engine_mod.ERROR, 3, time.time() - 100   # 3rd failure: wait 120 s
    eng.start()
    assert not eng.running
    eng._error_at = time.time() - 130
    eng.start()
    assert eng.running
    eng.stop()
    eng.state, eng._errors, eng._error_at = engine_mod.ERROR, 5, time.time()
    eng.clear_error()                                # e.g. after "Install Live mode" or a proxy change
    eng.start()
    assert eng.running
    eng.shutdown()


# ---------------------------------------------------------------- GDI font cache does not leak
def test_font_cache_flush_releases_every_font():
    import ctypes
    import sys

    from dualyrics import render

    if sys.platform != "win32":
        pytest.skip("GDI only")
    eng = render.TextEngine()
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    u32.GetGuiResources.argtypes = [ctypes.c_void_p, ctypes.c_uint]

    def gdi() -> int:
        return u32.GetGuiResources(k32.GetCurrentProcess(), 0)

    for px in range(8, 120):                    # warm up: one flush happens here
        eng.metrics(render.FontKey("Segoe UI", px, 400, False))
    before = gdi()
    for _ in range(3):
        for px in range(8, 120):                # > 96 fonts each round: several flushes
            eng.metrics(render.FontKey("Segoe UI", px, 700, False))
    assert gdi() - before < 100
